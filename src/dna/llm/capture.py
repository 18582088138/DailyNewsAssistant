"""
提示词捕获 provider / A provider that records the prompt instead of answering it.

给 `dna prompt`（提示词调试台，见 `produce/prompt_lab.py`）用：想看某个任务
**真正发出去的提示词**长什么样，但不想为此付钱。
Used by the prompt workbench: see the prompt a task actually sends, without paying.

为什么要在 provider 这一层拦 / Why the interception happens at the provider boundary:
    `LLMProvider.chat_json()` 会在业务代码给的 messages 后面再追加一条
    「请只输出 JSON」的指令（`base.py::_json_instruction`，内容含完整 JSON Schema）。
    也就是说**业务代码构造的 messages 不等于真正发出去的 messages**。
    在 `build_messages()` 那一层看提示词会漏掉这一条；只有在 `_complete()`
    这里才拿到与计费请求逐字节相同的东西——而「脚本里验证的提示词在应用里生效」
    正是这个工具唯一的价值。
    `chat_json()` appends a "JSON only" instruction carrying the full schema, so the
    messages business code builds are not the messages that get sent. Only `_complete()`
    sees byte-for-byte what a billed request would carry — which is the entire point of
    this tool.

它同时**返回预置回复让流程走完** / It also returns canned replies so the flow completes:
    长文案是「提纲 1 次 + 每节 1 次」十几次调用串起来的，其中每一节的提示词都依赖
    上一节的产出。抛异常中断的话只能看到第一条提示词；返回一个合法的假回复，
    整条链路会照常跑完，十几条提示词一次全拿到，仍然是零费用。
    A long-form script chains a dozen calls whose prompts depend on the previous
    section's output. Aborting would surface only the first prompt; returning a valid
    canned reply runs the whole chain and yields every prompt, still for free.
"""

from __future__ import annotations

from dna.core.errors import ProviderResponseError
from dna.llm.base import ChatMessage, ChatResult, LLMProvider, ProviderInfo, Usage


class RepliesExhausted(ProviderResponseError):
    """
    预置回复用完了 / The canned replies ran out.

    继承 `ProviderResponseError` 是刻意的：`chat_json()` 的修复重试会捕获它并
    重试两次，`summarize_cluster` 之类的节点也会把它降级掉——两种反应都对，
    因为该看的提示词此刻已经记下来了，流程怎么收尾都不影响结果。
    Deliberately a ProviderResponseError: the repair loop retries it and nodes like
    `summarize_cluster` degrade on it. Both are fine — the prompts are already recorded
    by the time it is raised.
    """


class CapturingProvider(LLMProvider):
    """
    记下每一次真实发出的 messages / Record every message list actually sent.

    参数 / Args:
        replies: 按顺序返回的假回复（通常是符合该任务 schema 的 JSON 字符串）。
            用完之后抛 `RepliesExhausted`。
        name:    记进 `info` 的名字，只用于日志显示
    """

    def __init__(self, replies: list[str] | None = None, *, name: str = "capture") -> None:
        self._info = ProviderInfo(name=name, model="capture", is_local=True)
        self._replies = list(replies or [])
        self.captured: list[list[ChatMessage]] = []

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
        self.captured.append(list(messages))
        if not self._replies:
            raise RepliesExhausted(
                f"提示词已捕获 {len(self.captured)} 条，预置回复用完 / "
                f"{len(self.captured)} prompts captured, no canned replies left"
            )
        return ChatResult(text=self._replies.pop(0), info=self._info, usage=Usage())


class RecordingProvider(LLMProvider):
    """
    在真 provider 外面记一份 messages / Record the messages around a real provider.

    真机跑一次也要能看到提示词——**看到的必须是这次计费请求带的那一份**，
    而不是「事后按同样参数再渲染一遍」的重现。重现会漏掉回炉重写那几轮
    （它们的 messages 里带着上一稿），也就漏掉了最需要检查的部分。
    A billed run must surface the prompt it actually sent, not a re-render: a re-render
    misses the rewrite rounds, whose messages carry the previous draft — precisely the
    part worth inspecting.

    委托而不是继承被包装的类 / Delegates rather than subclasses the wrapped provider:
        与 `llm/cache.py::CachedProvider` 一致，任何 provider 都能被套上，
        包括已经套了缓存的那一个。
    """

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.captured: list[list[ChatMessage]] = []

    @property
    def info(self) -> ProviderInfo:
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
        self.captured.append(list(messages))
        return self._inner._complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            json_mode=json_mode,
        )


def render_messages(messages: list[ChatMessage]) -> str:
    """
    把一次调用的 messages 拼成人读的全文 / Lay one call's messages out for reading.

    带上角色分隔线：system 与 user 的边界是调提示词时最常看的东西
    （「这句到底在 system 里还是被我写进 user 了」）。
    """
    parts = []
    for message in messages:
        parts.append(f"────── {message.role} ──────\n{message.content}")
    return "\n\n".join(parts)


__all__ = [
    "CapturingProvider",
    "RecordingProvider",
    "RepliesExhausted",
    "render_messages",
]
