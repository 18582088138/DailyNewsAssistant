"""
LLM 抽象层 / LLM abstraction layer.

这是「先用 API LLM 验证功能、后续迁移到 Local LLM」这条要求的落点：
业务代码只依赖 LLMProvider 接口，换 provider（DeepSeek / OpenRouter / Ollama /
vLLM / OpenVINO）不需要改动任何调用方。
This is where the "validate with a cloud LLM first, migrate to a local one later"
requirement lands: callers only ever depend on the LLMProvider interface, so
swapping providers requires no changes at the call sites.

子类只需实现 `_complete()` 与 `info`；`chat()` / `chat_json()` 的通用逻辑
（计时、用量统计、JSON 提取、结构校验与修复重试）都在基类里，避免每个
provider 各写一份、各错一份。
Subclasses implement only `_complete()` and `info`. The shared logic — timing,
usage accounting, JSON extraction, schema validation and repair retries — lives
in the base class so each provider does not reimplement (and re-break) it.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

from dna.core.errors import ProviderResponseError
from dna.core.logging import get_logger
from dna.llm.parsing import extract_json_block

logger = get_logger("llm")

Role = Literal["system", "user", "assistant"]
M = TypeVar("M", bound=BaseModel)

DEFAULT_TIMEOUT = 120.0
DEFAULT_TEMPERATURE = 0.3


# ---------------------------------------------------------------------------
# 消息与结果 / Messages and results
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """一条对话消息 / A single chat message."""

    role: Role
    content: str


def system(content: str) -> ChatMessage:
    """构造 system 消息 / Build a system message."""
    return ChatMessage(role="system", content=content)


def user(content: str) -> ChatMessage:
    """构造 user 消息 / Build a user message."""
    return ChatMessage(role="user", content=content)


def assistant(content: str) -> ChatMessage:
    """构造 assistant 消息 / Build an assistant message."""
    return ChatMessage(role="assistant", content=content)


@dataclass(frozen=True)
class ProviderInfo:
    """provider 的身份信息，用于日志与台账记账 / Provider identity, for logs and the ledger."""

    name: str
    model: str
    is_local: bool
    base_url: str | None = None

    def __str__(self) -> str:
        return f"{self.name}:{self.model}"


@dataclass(frozen=True)
class Usage:
    """
    token 用量 / Token usage.

    会写进台账 productions 表，用于统计每期日报的成本。
    Recorded in the ledger's productions table to track the cost of each issue.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        """累加多次调用的用量 / Accumulate usage across several calls."""
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass(frozen=True)
class ChatResult:
    """一次调用的完整结果 / The full result of one call."""

    text: str
    info: ProviderInfo
    usage: Usage = field(default_factory=Usage)
    duration_ms: int = 0
    attempts: int = 1
    reasoning: str | None = field(default=None, repr=False)
    """
    推理模型的思维链，与正文分开保存 / Chain-of-thought, kept separate from the answer.

    Ollama 的 OpenAI 兼容端点会把它放在独立的 `reasoning` 字段。留着它是为了排查
    「答案为空但 token 烧了一大堆」这类问题，正常业务逻辑不应该读它。
    Ollama's OpenAI-compatible endpoint returns it in a separate `reasoning` field.
    It is retained for diagnosing "empty answer but a lot of tokens burned"; normal
    business logic should never read it.
    """


# ---------------------------------------------------------------------------
# 抽象基类 / Abstract base class
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """
    所有 LLM 提供方的统一接口 / The single interface every LLM provider implements.
    """

    @property
    @abstractmethod
    def info(self) -> ProviderInfo:
        """provider 身份信息 / Identity of this provider."""

    @abstractmethod
    def _complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
        timeout: float,
        json_mode: bool = False,
    ) -> ChatResult:
        """
        真正发起一次调用 / Perform one actual completion call.

        子类只需实现这个方法；异常应转换成 dna.core.errors 中的 ProviderError 子类，
        以便上层的重试与降级逻辑能正确分类处理。
        Subclasses implement only this. Failures must be translated into the
        ProviderError subclasses so the retry and fallback logic can classify them.

        json_mode 为真时应请求服务端的 JSON 模式（若该后端支持）。
        When json_mode is set, request the backend's JSON mode if it supports one.
        """

    # -- 公开 API / public API ------------------------------------------------

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        json_mode: bool = False,
    ) -> ChatResult:
        """
        发起一次对话补全 / Run one chat completion.

        统一在这里计时与记日志，子类无需操心。
        Timing and logging happen here so subclasses do not have to care.
        """
        if not messages:
            raise ValueError("messages must not be empty / 消息列表不能为空")

        started = time.perf_counter()
        result = self._complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            json_mode=json_mode,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        # 子类若未填写耗时，则用这里测得的值
        if result.duration_ms == 0:
            result = replace(result, duration_ms=elapsed_ms)

        logger.debug(
            "%s 完成：%d tokens，%d ms",
            result.info,
            result.usage.total_tokens,
            result.duration_ms,
        )
        return result

    def chat_text(self, messages: list[ChatMessage], **kwargs: object) -> str:
        """只要文本结果的便捷方法 / Convenience wrapper returning just the text."""
        return self.chat(messages, **kwargs).text  # type: ignore[arg-type]

    def chat_json(
        self,
        messages: list[ChatMessage],
        schema: type[M],
        *,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_repair: int = 2,
    ) -> M:
        """
        要求模型返回符合 Pydantic 结构的 JSON，并在失败时带着错误信息重试。
        Ask the model for JSON matching a Pydantic schema, retrying with the error
        message fed back when validation fails.

        为什么在这一层做修复重试 / Why repair here:
            本地小模型（后续迁移目标）比云端模型更容易漏字段或多包一层说明文字。
            把「提取 JSON → 校验 → 带错误重试」放在基类，可以让同一份业务代码在
            云端与本地模型之间无缝切换，而不是每个调用点各写一遍容错。
            Local smaller models — the eventual migration target — are more prone to
            dropping fields or wrapping the JSON in prose. Handling extraction,
            validation and repair here lets the same business code run unchanged
            against both cloud and local models.

        参数 / Args:
            schema:     期望的 Pydantic 模型类 / the expected Pydantic model
            max_repair: 校验失败后的额外重试次数 / extra attempts after a failure

        抛出 / Raises:
            ProviderResponseError: 用尽重试仍无法得到合法结构
        """
        convo = [*messages, user(_json_instruction(schema))]
        last_error = ""

        for attempt in range(max_repair + 1):
            result = self.chat(
                convo,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                json_mode=True,
            )
            try:
                payload = json.loads(extract_json_block(result.text))
                if _looks_like_json_schema(payload):
                    # 小模型的典型错误：把 Schema 原样抄回来
                    raise _SchemaEchoError()
                return schema.model_validate(payload)
            except (ProviderResponseError, ValidationError, json.JSONDecodeError) as exc:
                last_error = _repair_hint(exc)
                logger.warning(
                    "%s 返回的 JSON 不合法（第 %d/%d 次）：%s",
                    self.info,
                    attempt + 1,
                    max_repair + 1,
                    last_error.splitlines()[0] if last_error else "",
                )
                if attempt == max_repair:
                    break
                # 把原样输出和错误一起回灌，让模型自己修
                convo = [
                    *convo,
                    assistant(result.text),
                    user(
                        f"上面的输出不可用，原因如下：\n{last_error}\n\n"
                        "请只输出修正后的 JSON 数据本身，不要任何解释文字、不要代码块标记。"
                    ),
                ]

        raise ProviderResponseError(
            f"{self.info} 在 {max_repair + 1} 次尝试后仍未返回合法 JSON / "
            f"no valid JSON after {max_repair + 1} attempts: {last_error}"
        )

    def health_check(self, timeout: float = 15.0) -> bool:
        """
        轻量连通性探测 / A cheap reachability probe.

        用于 GUI 里的 provider 选择与 `dna doctor` 的可选检查。
        Used by the provider picker in the GUI and by optional doctor checks.
        """
        try:
            self.chat([user("ping")], max_tokens=8, timeout=timeout)
            return True
        except Exception as exc:
            logger.info("%s 连通性探测失败：%s", self.info, exc)
            return False

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.info}>"


class _SchemaEchoError(ProviderResponseError):
    """模型把 Schema 原样抄了回来 / The model echoed the schema back verbatim."""

    def __init__(self) -> None:
        super().__init__("模型返回了 JSON Schema 本身，而不是符合该 Schema 的数据")


def _looks_like_json_schema(payload: object) -> bool:
    """
    判断返回的是不是 Schema 本身 / Detect whether the payload is the schema itself.

    实测发现的小模型典型失败 / A failure mode observed in practice:
        `qwen2.5:3b` 收到 Schema 后，直接把 Schema 原样返回。此时 Pydantic 的报错是
        「Field required」，完全没提示「你抄的是 Schema」，模型据此修不出正确结果，
        三次重试全部失败。显式识别这种情况，才能给出有效的纠正提示。
        Given a schema, `qwen2.5:3b` returns the schema verbatim. Pydantic then
        reports "Field required", which never hints that the schema was echoed, so
        the model cannot fix it and every repair attempt fails. Detecting this
        explicitly is what makes the correction actionable.
    """
    if not isinstance(payload, dict):
        return False
    schema_markers = {"properties", "$defs", "$schema", "definitions"}
    return bool(schema_markers & payload.keys())


def _repair_hint(exc: Exception) -> str:
    """
    把异常转成能让模型自我修正的提示 / Turn an exception into a hint the model can act on.

    通用的校验错误直接透传；Schema 回声则换成明确的纠正说明。
    Generic validation errors pass through; a schema echo gets an explicit correction.
    """
    if isinstance(exc, _SchemaEchoError):
        return (
            "你返回的是 JSON Schema 定义本身（含 properties / $defs 等字段）。"
            "我需要的是**符合该 Schema 的一条真实数据**，不是 Schema 定义。"
            "例如 Schema 里写 title 是字符串，你就应该返回真实的标题内容。"
        )
    return str(exc)


def _json_instruction(schema: type[BaseModel]) -> str:
    """
    生成要求模型输出 JSON 的指令 / Build the instruction asking for JSON output.

    直接把 Pydantic 生成的 JSON Schema 塞给模型，字段说明（description）也会一并
    带过去，因此提示词随数据模型自动更新，不会两处不同步。
    The Pydantic-generated JSON Schema is handed to the model verbatim, field
    descriptions included, so the prompt tracks the data model automatically
    instead of drifting out of sync with it.

    措辞上明确区分「Schema」与「数据」，因为小模型很容易把两者搞混（见
    _looks_like_json_schema 的说明）。
    The wording separates "schema" from "data" explicitly, because smaller models
    readily confuse the two — see _looks_like_json_schema.
    """
    return (
        "请按下面的 JSON Schema 生成**一条符合该结构的数据**。\n"
        "Produce one JSON object that conforms to this schema.\n\n"
        "--- Schema（仅用于说明字段要求，不要照抄）---\n"
        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)}\n"
        "--- Schema 结束 ---\n\n"
        "要求 / Requirements：\n"
        "1. 只输出 JSON 数据本身，不要解释文字，不要 Markdown 代码块标记\n"
        "2. **不要返回上面的 Schema 定义**，不要包含 properties / type / required 这些元字段\n"
        "3. 每个字段都要填真实内容，必填字段不能缺失"
    )


__all__ = [
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT",
    "ChatMessage",
    "ChatResult",
    "LLMProvider",
    "ProviderInfo",
    "Usage",
    "assistant",
    "system",
    "user",
]
