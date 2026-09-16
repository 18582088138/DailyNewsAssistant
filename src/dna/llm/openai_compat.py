"""
OpenAI 兼容接口的 provider / Provider for OpenAI-compatible endpoints.

一份实现覆盖四个后端 / One implementation covers four backends:
    DeepSeek（默认云端）· OpenRouter（备用云端）· vLLM（本地 Linux）· Ollama（本地）

它们的 HTTP 协议完全一致，只有 base_url / model / api_key 不同，所以没有必要
写四份客户端代码——写四份就意味着四份各自的重试、超时和错误分类逻辑。
They speak the identical HTTP protocol and differ only in base_url, model and
api_key, so four separate clients would mean four separate (and separately buggy)
retry, timeout and error-classification paths.
"""

from __future__ import annotations

from typing import Any

from dna.core.errors import (
    AuthError,
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
    RateLimitError,
)
from dna.core.logging import get_logger
from dna.llm.base import ChatMessage, ChatResult, LLMProvider, ProviderInfo, Usage
from dna.llm.parsing import strip_think_tags

logger = get_logger("llm.openai_compat")


class OpenAICompatProvider(LLMProvider):
    """
    通过 OpenAI 兼容协议访问的 LLM / An LLM reached over the OpenAI-compatible API.

    参数 / Args:
        name:     provider 标识，用于日志与台账 / identifier used in logs and the ledger
        model:    模型名 / model name
        api_key:  云端必填；本地服务传占位串即可 / required for cloud, placeholder locally
        base_url: 接口根地址 / API root
        is_local: 是否本地服务；本地服务会绕过系统代理 / local services bypass the proxy
        client:   注入的客户端，仅供测试 / injected client, for tests only
    """

    def __init__(
        self,
        *,
        name: str,
        model: str,
        api_key: str,
        base_url: str,
        is_local: bool = False,
        timeout: float = 120.0,
        client: Any | None = None,
    ) -> None:
        self._info = ProviderInfo(name=name, model=model, is_local=is_local, base_url=base_url)
        self._api_key = api_key
        self._timeout = timeout
        self._client = client or self._build_client(api_key, base_url, is_local, timeout)

    @staticmethod
    def _build_client(api_key: str, base_url: str, is_local: bool, timeout: float) -> Any:
        """
        构造 OpenAI SDK 客户端 / Build the OpenAI SDK client.

        本地服务显式关闭 trust_env，绕开系统代理设置。否则在公司网络下，
        对 localhost:11434 的请求会被送去公司代理并失败——即使 NO_PROXY 配了
        localhost，也不是所有 HTTP 栈都会正确解析 CIDR 形式的条目。
        For local services trust_env is disabled so the system proxy is bypassed.
        Otherwise a request to localhost:11434 gets routed through the corporate
        proxy and fails; NO_PROXY helps, but not every HTTP stack parses its CIDR
        entries correctly.
        """
        from openai import OpenAI

        kwargs: dict[str, Any] = {
            "api_key": api_key or "not-needed",
            "base_url": base_url,
            "timeout": timeout,
            "max_retries": 0,  # 重试由我们自己的 ResilientProvider 统一负责
        }
        if is_local:
            import httpx

            kwargs["http_client"] = httpx.Client(trust_env=False, timeout=timeout)
        return OpenAI(**kwargs)

    @property
    def info(self) -> ProviderInfo:
        return self._info

    def _complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
        timeout: float,
        json_mode: bool = False,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self._info.model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
            "timeout": timeout,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if json_mode:
            # 服务端 JSON 模式，DeepSeek / Ollama / vLLM 都支持
            payload["response_format"] = {"type": "json_object"}

        try:
            response = self._client.chat.completions.create(**payload)
        except Exception as exc:
            translated = _translate_error(exc, self._info)
            # 少数端点不认 response_format，退回普通模式重试一次而不是直接失败
            # A few endpoints reject response_format; fall back once instead of failing.
            if json_mode and _is_unsupported_parameter(exc):
                logger.info("%s 不支持 response_format，退回普通模式", self._info)
                payload.pop("response_format")
                try:
                    response = self._client.chat.completions.create(**payload)
                except Exception as retry_exc:
                    raise _translate_error(retry_exc, self._info) from retry_exc
            else:
                raise translated from exc

        usage = _extract_usage(response)
        text, reasoning = _extract_text(response, self._info, usage)
        return ChatResult(text=text, info=self._info, usage=usage, reasoning=reasoning)


def _extract_text(response: Any, info: ProviderInfo, usage: Usage) -> tuple[str, str | None]:
    """
    从响应中取出正文与思维链 / Pull the answer and the chain-of-thought out of a response.

    推理模型有两种输出思维链的方式，两种都要处理：
    Reasoning models emit their chain-of-thought in one of two ways, and both must
    be handled:
      1. 独立的 `reasoning` 字段（Ollama 的 OpenAI 兼容端点走这条路）
         A separate `reasoning` field — this is what Ollama's endpoint does.
      2. 内联在正文里的 <think>…</think>（部分自建部署走这条路）
         Inline <think>…</think> inside the content, as some self-hosted setups do.

    返回 / Returns:
        (正文, 思维链或 None)
    """
    try:
        choices = response.choices
        if not choices:
            raise ProviderResponseError(f"{info} 返回了空的 choices / empty choices")
        choice = choices[0]
        content = choice.message.content
    except AttributeError as exc:
        raise ProviderResponseError(
            f"{info} 返回结构异常 / unexpected response shape: {exc}"
        ) from exc

    reasoning = getattr(choice.message, "reasoning", None) or getattr(
        choice.message, "reasoning_content", None
    )
    finish_reason = getattr(choice, "finish_reason", None)

    text = strip_think_tags(content) if content else ""

    if not text.strip():
        raise _empty_content_error(info, finish_reason, reasoning, usage)

    return text, reasoning


def _empty_content_error(
    info: ProviderInfo, finish_reason: str | None, reasoning: str | None, usage: Usage
) -> ProviderResponseError:
    """
    正文为空时给出**能指到根因**的报错 / Build an actionable error for an empty answer.

    实测踩到的坑 / The failure actually hit in practice:
        `qwen3.5:9b` 回答「二」一个字，却在思维链上花掉 900 个 completion tokens。
        当 max_tokens 小于这个开销时，预算全被推理吃光，`content` 为空、
        `finish_reason` 为 "length"。若只报「内容为空」，排查方向会完全跑偏。
        `qwen3.5:9b` spent 900 completion tokens reasoning before answering with a
        single character. When max_tokens is below that, the budget is consumed by
        reasoning, leaving empty content and finish_reason="length". Reporting only
        "empty content" would send debugging off in the wrong direction.
    """
    if finish_reason == "length":
        detail = (
            f"{info} 正文为空且响应被 max_tokens 截断（finish_reason=length，"
            f"已用 {usage.completion_tokens} completion tokens）。"
        )
        if reasoning:
            detail += (
                "该模型是推理模型，token 预算被思维链耗尽——请调大 max_tokens，"
                "或改用非推理模型。"
            )
        else:
            detail += "请调大 max_tokens。"
        detail += (
            f" / Empty answer, truncated by max_tokens after "
            f"{usage.completion_tokens} completion tokens; raise max_tokens."
        )
        error = ProviderResponseError(detail)
        # 用同样的 max_tokens 重试必然再次截断，重试没有意义
        error.retryable = False
        return error

    if reasoning:
        return ProviderResponseError(
            f"{info} 只返回了思维链、没有正文（finish_reason={finish_reason}）"
            f" / the model returned reasoning but no answer"
        )

    return ProviderResponseError(
        f"{info} 返回内容为空（finish_reason={finish_reason}）/ empty response content"
    )


def _extract_usage(response: Any) -> Usage:
    """
    读取 token 用量；部分本地服务不返回该字段，缺失时按 0 计。
    Read token usage. Some local services omit it, in which case it counts as zero.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return Usage()
    return Usage(
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
    )


def _is_unsupported_parameter(exc: Exception) -> bool:
    """
    判断错误是否为「不认识某个请求参数」/ Whether the error means an unsupported parameter.

    用于 response_format 的优雅降级：老版本或精简实现的 OpenAI 兼容端点可能不支持
    JSON 模式，此时应退回普通模式，而不是让整条流水线失败。
    Used for graceful degradation of response_format: older or minimal
    OpenAI-compatible endpoints may not support JSON mode, and that should not take
    the whole pipeline down.
    """
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "response_format", "unsupported parameter", "unknown field", "invalid_request",
        )
    )


def _translate_error(exc: Exception, info: ProviderInfo) -> ProviderError:
    """
    把 SDK / 网络异常翻译成本项目的异常类型 / Translate SDK and network errors.

    分类的意义在于上层的降级策略：限流和超时值得退避重试，鉴权失败重试多少次
    都一样，应该直接切到备用 provider。
    The classification drives the fallback strategy: rate limits and timeouts are
    worth backing off for, whereas an auth failure will never succeed on retry and
    should switch providers immediately.
    """
    name = type(exc).__name__
    text = str(exc)
    lowered = text.lower()
    status = getattr(exc, "status_code", None)

    if status == 429 or "rate limit" in lowered or "too many requests" in lowered:
        return RateLimitError(f"{info} 触发限流 / rate limited: {text}")

    if status in (401, 403) or "authentication" in lowered or "invalid api key" in lowered:
        return AuthError(f"{info} 鉴权失败 / authentication failed: {text}")

    if "timeout" in lowered or "timed out" in lowered or name.endswith("TimeoutError"):
        return ProviderTimeoutError(f"{info} 调用超时 / timed out: {text}")

    if isinstance(exc, ProviderError):
        return exc

    # 其余（网络抖动、5xx）默认可重试 / everything else defaults to retryable
    err = ProviderError(f"{info} 调用失败 / call failed [{name}]: {text}")
    err.retryable = True
    return err


__all__ = ["OpenAICompatProvider"]
