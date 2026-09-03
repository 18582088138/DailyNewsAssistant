"""
LLM 响应缓存 / LLM response cache.

把「相同请求」的结果存到磁盘，重复调用直接命中缓存，**不再计费**。
Stores results on disk so an identical request is served locally and never billed again.

为什么必须有 / Why this exists:
    P3 之后每个节点都调 LLM，而开发过程是「跑一遍 → 看输出不对 → 改提示词 → 再跑」。
    如果只改了 trend 的提示词，前面 summarize、translate 的几十次调用不该重新付一次钱。
    有了缓存，一轮调试的成本只等于**真正变化了的那部分**。
    Every node from P3 onward calls an LLM, and development means run, inspect, tweak the
    prompt, run again. Changing only the trend prompt must not re-bill the dozens of
    summarize and translate calls before it. With the cache, one debugging round costs
    only what actually changed.

缓存键 / Cache key:
    provider + model + 完整消息 + temperature + max_tokens + json_mode 的 sha256。
    **模型与提示词任何一处改动都会自然错开缓存**，不会拿旧模型的答案冒充新模型的。
    A sha256 over provider, model, the full messages, temperature, max_tokens and
    json_mode. Any change to the model or the prompt naturally misses the cache, so an
    old model's answer can never masquerade as a new one's.

刻意不做的事 / Deliberate omissions:
    - **不设过期时间**：同样的输入配同样的输出，正是我们想要的可复现性。
      需要新答案时用 `temperature` 或提示词的真实变化去错开，而不是靠缓存失效。
    - **不缓存失败**：报错重来一次是对的，把失败缓存下来只会让人困惑。
      No expiry: identical input yielding identical output is exactly the reproducibility
      we want. Failures are never cached — retrying an error is the correct behaviour.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dna.core.logging import get_logger
from dna.llm.base import ChatMessage, ChatResult, LLMProvider, ProviderInfo, Usage

logger = get_logger("llm.cache")


def cache_key(
    info: ProviderInfo,
    messages: list[ChatMessage],
    *,
    temperature: float,
    max_tokens: int | None,
    json_mode: bool,
) -> str:
    """
    计算缓存键 / Compute the cache key.

    纯函数，便于单独测试「什么情况下算同一个请求」。
    A pure function, so "what counts as the same request" can be tested on its own.
    """
    payload = {
        "provider": info.name,
        "model": info.model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "json_mode": json_mode,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class CachedProvider(LLMProvider):
    """
    给任意 provider 套一层磁盘缓存 / Wrap any provider in a disk cache.

    自身也是一个 `LLMProvider`，因此可以和 `ResilientProvider` 任意叠放，
    调用方完全无感。
    It is itself an `LLMProvider`, so it composes freely with `ResilientProvider` and is
    invisible to callers.

    叠放顺序有讲究 / The nesting order matters:
        `CachedProvider(ResilientProvider(real))` —— 缓存在最外层。
        命中缓存时连重试逻辑都不进，省下的是整条链路；反过来包的话，
        每次命中都要先穿过一遍重试封装，没有意义。
        Cache outermost: a hit skips the retry wrapper entirely. Nested the other way,
        every hit would still traverse the retry machinery for nothing.
    """

    def __init__(self, inner: LLMProvider, cache_dir: Path, *, enabled: bool = True) -> None:
        self._inner = inner
        self._dir = cache_dir
        self._enabled = enabled
        self.hits = 0
        self.misses = 0

    @property
    def info(self) -> ProviderInfo:
        """身份沿用被包装的 provider / Identity passes through from the wrapped provider."""
        return self._inner.info

    @property
    def inner(self) -> LLMProvider:
        """被包装的 provider / The wrapped provider, so the stack stays inspectable."""
        return self._inner

    def _complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
        timeout: float,
        json_mode: bool = False,
    ) -> ChatResult:
        """先查缓存，未命中才真正调用 / Serve from cache, calling through only on a miss."""
        if not self._enabled:
            return self._inner._complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                json_mode=json_mode,
            )

        key = cache_key(
            self._inner.info,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        path = self._path_for(key)

        cached = self._read(path)
        if cached is not None:
            self.hits += 1
            logger.debug("缓存命中：%s（已省一次调用）", key[:12])
            return cached

        self.misses += 1
        result = self._inner._complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            json_mode=json_mode,
        )
        self._write(path, result, messages)
        return result

    # -- 读写 / persistence ---------------------------------------------------

    def _path_for(self, key: str) -> Path:
        """
        缓存文件路径 / Path of one cache entry.

        用前两位做子目录：一期日报几十次调用不算多，但缓存会跨天累积，
        单目录塞几万个文件在 Windows 上会明显变慢。
        The first two characters form a subdirectory: one issue makes only dozens of
        calls, but entries accumulate across days and tens of thousands of files in a
        single directory is noticeably slow on Windows.
        """
        return self._dir / key[:2] / f"{key}.json"

    def _read(self, path: Path) -> ChatResult | None:
        """读取缓存；文件损坏时当作未命中 / Read an entry, treating corruption as a miss."""
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            usage = data.get("usage", {})
            return ChatResult(
                text=data["text"],
                info=self._inner.info,
                usage=Usage(
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                ),
                duration_ms=0,  # 命中缓存没有真实耗时，记 0 而不是沿用旧值
                attempts=data.get("attempts", 1),
            )
        except (OSError, ValueError, KeyError) as exc:
            # 缓存坏了就当没有——它只是加速手段，绝不能成为故障源
            # A broken entry is simply a miss: the cache is an optimisation and must
            # never become a source of failure.
            logger.warning("缓存文件损坏，忽略：%s —— %s", path.name, exc)
            return None

    def _write(self, path: Path, result: ChatResult, messages: list[ChatMessage]) -> None:
        """写入缓存；写失败不影响调用结果 / Persist an entry; a write failure is non-fatal."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "text": result.text,
                        "usage": {
                            "prompt_tokens": result.usage.prompt_tokens,
                            "completion_tokens": result.usage.completion_tokens,
                            "total_tokens": result.usage.total_tokens,
                        },
                        "attempts": result.attempts,
                        "provider": result.info.name,
                        "model": result.info.model,
                        # 存一份提示词摘要，便于人工翻查「这条缓存是哪次请求」
                        # A prompt excerpt makes it possible to tell by hand which
                        # request an entry belongs to.
                        "prompt_preview": _preview(messages),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("写缓存失败（不影响本次结果）：%s", exc)

    # -- 统计 / statistics ----------------------------------------------------

    def stats(self) -> str:
        """一行命中率摘要 / A one-line hit-rate summary."""
        total = self.hits + self.misses
        if total == 0:
            return "LLM 缓存：未发生调用"
        return f"LLM 缓存：命中 {self.hits} / {total}（省下 {self.hits} 次计费调用）"

    def __repr__(self) -> str:
        return f"CachedProvider({self._inner!r}, dir={self._dir})"


def _preview(messages: list[ChatMessage], limit: int = 200) -> str:
    """取最后一条用户消息的开头，作为人工翻查用的线索 / An excerpt for manual lookup."""
    for message in reversed(messages):
        if message.role == "user":
            text = " ".join(message.content.split())
            return text[:limit]
    return ""


__all__ = ["CachedProvider", "cache_key"]
