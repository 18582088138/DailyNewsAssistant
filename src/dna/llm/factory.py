"""
Provider 工厂与容错包装 / Provider factory and resilience wrapper.

对外只暴露一个入口 `get_llm()`，返回的对象仍然是 LLMProvider，
因此调用方感知不到底下有几个 provider、重试了几次。
The single entry point is `get_llm()`, and what it returns is still an
LLMProvider — callers never learn how many providers sit underneath or how many
retries happened.

降级策略 / Fallback strategy:
    可重试错误（限流 / 超时 / 5xx）→ 指数退避重试
    不可重试错误（鉴权失败）      → 不浪费时间，直接切备用 provider
    主 provider 用尽             → 切备用 provider 再试一轮

这套策略是针对实际踩过的坑设计的：OpenRouter 免费层会限流（doc_analyzer issue
002），而批量摘要一次要跑十几条，中途被限流就前功尽弃。
The strategy addresses a problem already hit in practice: OpenRouter's free tier
rate-limits, and a batch summarisation run makes a dozen calls, so being cut off
halfway wastes the whole run.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from dna.core.config import Settings, get_settings
from dna.core.errors import ConfigError, ProviderError
from dna.core.logging import get_logger
from dna.llm.base import ChatMessage, ChatResult, LLMProvider, ProviderInfo
from dna.llm.cache import CachedProvider
from dna.llm.ollama_provider import OllamaProvider
from dna.llm.openai_compat import OpenAICompatProvider
from dna.llm.openvino_provider import OpenVINOProvider

logger = get_logger("llm.factory")

# 已知的 provider 名 / recognised provider names
KNOWN_PROVIDERS = ("deepseek", "openrouter", "vllm", "ollama", "openvino")
CLOUD_PROVIDERS = ("deepseek", "openrouter")


@dataclass(frozen=True)
class RetryPolicy:
    """
    退避重试策略 / Exponential backoff policy.

    delay = min(base_delay * 2**attempt, max_delay)
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 20.0

    def delay_for(self, attempt: int) -> float:
        """第 attempt 次失败后应等待的秒数（attempt 从 0 起）/ Delay after attempt N."""
        return min(self.base_delay * (2**attempt), self.max_delay)


# ---------------------------------------------------------------------------
# 构造单个 provider / Building a single provider
# ---------------------------------------------------------------------------


def build_provider(name: str, settings: Settings | None = None) -> LLMProvider:
    """
    按名字构造一个 provider / Build one provider by name.

    抛出 / Raises:
        ConfigError: provider 名未知，或云端 provider 缺少 API key
    """
    s = settings or get_settings()
    key = name.strip().lower()

    if key == "deepseek":
        return _cloud(s, "deepseek", s.deepseek_model, s.deepseek_api_key, s.deepseek_base_url)

    if key == "openrouter":
        return _cloud(
            s, "openrouter", s.openrouter_model, s.openrouter_api_key, s.openrouter_base_url
        )

    if key == "vllm":
        return OpenAICompatProvider(
            name="vllm",
            model=s.vllm_model,
            api_key="not-needed",
            base_url=s.vllm_base_url,
            is_local=True,
        )

    if key == "ollama":
        return OllamaProvider(base_url=s.ollama_base_url, model=s.ollama_model)

    if key == "openvino":
        return OpenVINOProvider(model_dir=s.openvino_llm_dir, device=s.openvino_llm_device)

    raise ConfigError(
        f"未知的 LLM provider：{name!r}。可选：{', '.join(KNOWN_PROVIDERS)}"
        f" / unknown LLM provider {name!r}"
    )


def _cloud(
    settings: Settings, name: str, model: str, api_key: str, base_url: str
) -> OpenAICompatProvider:
    """构造云端 provider，并在缺 key 时给出明确指引 / Build a cloud provider, key required."""
    if not api_key:
        raise ConfigError(
            f"{name} 缺少 API key，请在 .env 中设置 {name.upper()}_API_KEY"
            f" / missing API key for {name}"
        )
    if not model:
        raise ConfigError(
            f"{name} 缺少模型名，请在 .env 中设置 {name.upper()}_MODEL / missing model for {name}"
        )
    return OpenAICompatProvider(
        name=name, model=model, api_key=api_key, base_url=base_url, is_local=False
    )


def is_configured(name: str, settings: Settings | None = None) -> bool:
    """
    某 provider 是否具备可用配置 / Whether a provider is usable as configured.

    用于决定要不要挂备用 provider——没配好的备用比没有备用更糟，
    会在主 provider 失败后再浪费一轮重试。
    Used to decide whether to attach a fallback: a misconfigured fallback is worse
    than none, since it wastes another round of retries after the primary fails.
    """
    try:
        build_provider(name, settings)
        return True
    except ConfigError:
        return False


# ---------------------------------------------------------------------------
# 容错包装 / Resilience wrapper
# ---------------------------------------------------------------------------


class ResilientProvider(LLMProvider):
    """
    给 provider 套上重试与降级 / Wraps a provider with retries and a fallback.

    本身也是 LLMProvider，因此 chat_json()、health_check() 等基类能力自动可用。
    It is itself an LLMProvider, so chat_json() and health_check() come for free.
    """

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider | None = None,
        policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._policy = policy or RetryPolicy()
        self._sleep = sleep  # 注入以便测试不真的等待 / injected so tests need not wait
        self._active = primary

    @property
    def info(self) -> ProviderInfo:
        """当前实际生效的 provider / The provider currently in use."""
        return self._active.info

    @property
    def primary(self) -> LLMProvider:
        return self._primary

    @property
    def fallback(self) -> LLMProvider | None:
        return self._fallback

    def _complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
        timeout: float,
        json_mode: bool = False,
    ) -> ChatResult:
        chain: list[LLMProvider] = [self._primary]
        if self._fallback is not None:
            chain.append(self._fallback)

        last: ProviderError | None = None

        for position, provider in enumerate(chain):
            is_last_provider = position == len(chain) - 1
            try:
                result = self._attempt(
                    provider,
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    json_mode=json_mode,
                    allow_backoff=True,
                )
                self._active = provider
                return result
            except ProviderError as exc:
                last = exc
                if not is_last_provider:
                    logger.warning(
                        "%s 不可用，切换到备用 provider %s：%s",
                        provider.info,
                        chain[position + 1].info,
                        exc,
                    )

        raise last or ProviderError("没有可用的 LLM provider / no usable LLM provider")

    def _attempt(
        self,
        provider: LLMProvider,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
        timeout: float,
        json_mode: bool,
        allow_backoff: bool,
    ) -> ChatResult:
        """对单个 provider 做带退避的重试 / Retry one provider with backoff."""
        last: ProviderError | None = None

        for attempt in range(self._policy.max_attempts):
            try:
                result = provider.chat(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    json_mode=json_mode,
                )
                return replace(result, attempts=attempt + 1)
            except ProviderError as exc:
                last = exc
                if not exc.retryable:
                    # 鉴权之类的错误重试没有意义，立刻放弃这个 provider
                    logger.warning("%s 遇到不可重试的错误：%s", provider.info, exc)
                    raise
                if attempt == self._policy.max_attempts - 1:
                    break
                if allow_backoff:
                    delay = self._policy.delay_for(attempt)
                    logger.warning(
                        "%s 第 %d/%d 次失败，%.1fs 后重试：%s",
                        provider.info,
                        attempt + 1,
                        self._policy.max_attempts,
                        delay,
                        exc,
                    )
                    self._sleep(delay)

        raise last or ProviderError(f"{provider.info} 调用失败 / call failed")


# ---------------------------------------------------------------------------
# 对外入口 / Public entry point
# ---------------------------------------------------------------------------


def get_llm(
    settings: Settings | None = None,
    *,
    provider: str | None = None,
    with_fallback: bool = True,
    policy: RetryPolicy | None = None,
    cache: bool | None = None,
) -> LLMProvider:
    """
    取一个可直接使用的 LLM / Get a ready-to-use LLM.

    参数 / Args:
        provider:      覆盖 .env 中的 LLM_PROVIDER / overrides LLM_PROVIDER
        with_fallback: 是否挂备用 provider / whether to attach the fallback
        policy:        自定义重试策略 / custom retry policy
        cache:         是否启用磁盘响应缓存；None 表示按 .env 的 LLM_CACHE_ENABLED

    备用 provider 只在「已配置好」且「与主 provider 不同」时才挂上。
    The fallback is attached only when it is properly configured and differs from
    the primary.
    """
    s = settings or get_settings()
    primary_name = (provider or s.llm_provider).strip().lower()
    primary = build_provider(primary_name, s)

    fallback: LLMProvider | None = None
    if with_fallback and s.llm_fallback_provider:
        fallback_name = s.llm_fallback_provider.strip().lower()
        if fallback_name and fallback_name != primary_name:
            if is_configured(fallback_name, s):
                fallback = build_provider(fallback_name, s)
            else:
                logger.info("备用 provider %s 未配置完整，本次不启用", fallback_name)

    logger.info(
        "LLM: %s%s", primary.info, f"（备用 {fallback.info}）" if fallback else "（无备用）"
    )
    resilient = ResilientProvider(primary, fallback, policy=policy)

    # 缓存包在重试之外：命中时整条重试链路都不用进
    # The cache wraps the retry layer so a hit skips that machinery entirely.
    use_cache = s.llm_cache_enabled if cache is None else cache
    if not use_cache:
        return resilient
    return CachedProvider(resilient, s.llm_cache_path)


__all__ = [
    "CLOUD_PROVIDERS",
    "KNOWN_PROVIDERS",
    "CachedProvider",
    "ResilientProvider",
    "RetryPolicy",
    "build_provider",
    "get_llm",
    "is_configured",
]
