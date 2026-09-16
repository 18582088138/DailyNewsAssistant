"""
fakes.py —— LLM 测试用的假客户端与假 provider / Fakes used by the LLM tests.

本模块不含测试用例，只提供测试替身；由 test_provider.py 与 test_factory.py 导入。
No test cases here — just test doubles, imported by test_provider.py and test_factory.py.

设计要点 / Design note:
    P1 的全部测试都必须离线跑通。真实的网络调用只在 @pytest.mark.live 的用例里出现，
    默认跳过。因此这里提供两层假对象：
      1. FakeOpenAIClient —— 冒充 OpenAI SDK，用来测 OpenAICompatProvider 的解析与错误翻译
      2. ScriptedProvider —— 冒充一个 LLMProvider，用来测重试与降级逻辑
    Every P1 test must run offline; real network calls live only in @pytest.mark.live
    cases, which are skipped by default. Two layers of fakes make that possible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dna.llm.base import ChatMessage, ChatResult, LLMProvider, ProviderInfo, Usage

# ---------------------------------------------------------------------------
# 假的 OpenAI SDK 响应 / Fake OpenAI SDK response objects
# ---------------------------------------------------------------------------


@dataclass
class FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 20
    total_tokens: int = 30


@dataclass
class FakeMessage:
    content: str | None
    reasoning: str | None = None


@dataclass
class FakeChoice:
    message: FakeMessage
    finish_reason: str | None = "stop"


@dataclass
class FakeResponse:
    choices: list[FakeChoice]
    usage: FakeUsage | None = None


def make_response(
    text: str | None,
    *,
    usage: FakeUsage | None = None,
    reasoning: str | None = None,
    finish_reason: str = "stop",
) -> FakeResponse:
    """
    构造一个假的补全响应 / Build a fake completion response.

    reasoning 与 finish_reason 用于复现推理模型的真实行为：思维链走独立字段，
    token 预算被耗尽时 finish_reason 为 "length" 且 content 为空。
    `reasoning` and `finish_reason` reproduce how reasoning models actually behave:
    the chain-of-thought arrives in its own field, and when the token budget runs
    out finish_reason is "length" with empty content.
    """
    return FakeResponse(
        choices=[FakeChoice(FakeMessage(text, reasoning), finish_reason)],
        usage=usage or FakeUsage(),
    )


class FakeCompletions:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0) if self._outcomes else make_response("默认回复")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeOpenAIClient:
    """
    冒充 openai.OpenAI 的最小结构 / A minimal stand-in for openai.OpenAI.

    outcomes 按顺序消费：FakeResponse 直接返回，Exception 则抛出，
    因此可以精确编排「第一次限流、第二次成功」这类场景。
    Outcomes are consumed in order — responses are returned, exceptions raised —
    which makes scenarios like "rate-limited once, then succeeds" easy to script.
    """

    def __init__(self, outcomes: list[Any] | None = None) -> None:
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(outcomes or [])

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.chat.completions.calls


# ---------------------------------------------------------------------------
# 假的 provider / Fake provider
# ---------------------------------------------------------------------------


class ScriptedProvider(LLMProvider):
    """
    按剧本行事的 provider，用于测试重试与降级 / A provider that follows a script.

    outcomes 中的每一项要么是要返回的文本，要么是要抛出的异常。
    Each entry is either the text to return or the exception to raise.
    """

    def __init__(self, name: str, outcomes: list[Any], *, is_local: bool = False) -> None:
        self._info = ProviderInfo(name=name, model="fake-model", is_local=is_local)
        self._outcomes = list(outcomes)
        self.call_count = 0
        self.json_mode_calls: list[bool] = []
        # 记下每次收到的消息：pipeline 的测试重点是「提示词构建得对不对」，
        # 断言模型说了什么既贵又不稳定，断言我们问了什么才是可靠的。
        # Every received message list is recorded: the pipeline tests target prompt
        # construction, since asserting what we asked is reliable while asserting what
        # the model answered is neither cheap nor stable.
        self.messages: list[list[ChatMessage]] = []

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
        self.call_count += 1
        self.json_mode_calls.append(json_mode)
        self.messages.append(list(messages))
        outcome = self._outcomes.pop(0) if self._outcomes else "默认回复"
        if isinstance(outcome, Exception):
            raise outcome
        return ChatResult(text=str(outcome), info=self._info, usage=Usage(1, 2, 3))
