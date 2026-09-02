"""
test_provider.py —— LLM provider 单元测试 / LLM provider unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/llm/test_provider.py -v

联网验证（默认跳过）/ Live check (skipped by default):
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/llm/test_provider.py -m live -v

覆盖 / Covers:
    1. OpenAICompatProvider 正常返回：文本、token 用量、耗时
    2. 请求参数正确传递：model / temperature / max_tokens / messages
    3. **<think> 段自动剥离**（Qwen3 系列默认输出，留着会让 JSON 解析失败）
    4. 空内容 / content=None / 空 choices → ProviderResponseError
    5. **错误分类**：429→RateLimitError(可重试)、401→AuthError(不可重试)、
       timeout→ProviderTimeoutError(可重试)、其它→ProviderError(可重试)
    6. chat() 拒绝空消息列表
    7. chat_json() 正常解析、代码块解析、**校验失败后带错误重试并成功修复**
    8. chat_json() 重试用尽后抛 ProviderResponseError，且尝试次数符合 max_repair
    9. OllamaProvider：base_url 归一化、标记为本地、复用同一套解析逻辑
   10. health_check() 成功与失败路径

预期 / Expected:
    27 passed, 2 skipped（live 用例默认跳过）；耗时 < 3s；全程不联网
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field

from dna.core.errors import (
    AuthError,
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
    RateLimitError,
)
from dna.llm.base import user
from dna.llm.ollama_provider import OllamaProvider, normalize_base_url
from dna.llm.openai_compat import OpenAICompatProvider

from tests.llm.fakes import FakeOpenAIClient, FakeUsage, make_response


def build(outcomes: list[object], **kwargs: object) -> OpenAICompatProvider:
    """用假客户端构造 provider / Build a provider backed by the fake client."""
    client = FakeOpenAIClient(outcomes)
    provider = OpenAICompatProvider(
        name=kwargs.pop("name", "deepseek"),  # type: ignore[arg-type]
        model=kwargs.pop("model", "deepseek-chat"),  # type: ignore[arg-type]
        api_key="sk-test",
        base_url="https://api.example.com/v1",
        client=client,
        **kwargs,  # type: ignore[arg-type]
    )
    provider.fake_client = client  # type: ignore[attr-defined]
    return provider


# --- 正常路径 / happy path ----------------------------------------------------


def test_chat_returns_text_and_usage() -> None:
    """返回文本与 token 用量 / Returns the text and token usage."""
    p = build([make_response("这是摘要。", usage=FakeUsage(11, 22, 33))])
    result = p.chat([user("总结一下")])

    assert result.text == "这是摘要。"
    assert result.usage.prompt_tokens == 11
    assert result.usage.total_tokens == 33
    assert result.info.name == "deepseek"
    assert result.duration_ms >= 0


def test_request_parameters_are_forwarded() -> None:
    """请求参数应原样传给 SDK / Request parameters reach the SDK unchanged."""
    p = build([make_response("ok")])
    p.chat([user("hi")], temperature=0.7, max_tokens=128)

    call = p.fake_client.calls[0]  # type: ignore[attr-defined]
    assert call["model"] == "deepseek-chat"
    assert call["temperature"] == 0.7
    assert call["max_tokens"] == 128
    assert call["messages"] == [{"role": "user", "content": "hi"}]


def test_max_tokens_omitted_when_none() -> None:
    """未指定 max_tokens 时不应发送该字段 / max_tokens is omitted when unset."""
    p = build([make_response("ok")])
    p.chat([user("hi")])
    assert "max_tokens" not in p.fake_client.calls[0]  # type: ignore[attr-defined]


def test_think_tags_are_stripped() -> None:
    """
    推理模型的 <think> 段必须剥掉 / Reasoning <think> blocks must be stripped.

    Qwen3 系列默认输出思维链，不剥掉会让下游 JSON 解析直接失败。
    """
    p = build([make_response("<think>先分析一下</think>\n真正的答案")])
    assert p.chat([user("hi")]).text == "真正的答案"


def test_chat_text_shortcut() -> None:
    """chat_text 只返回文本 / chat_text returns the text alone."""
    p = build([make_response("纯文本")])
    assert p.chat_text([user("hi")]) == "纯文本"


# --- 异常响应 / malformed responses -------------------------------------------


def test_none_content_raises() -> None:
    """content 为 None → ProviderResponseError."""
    p = build([make_response(None)])
    with pytest.raises(ProviderResponseError, match="内容为空"):
        p.chat([user("hi")])


def test_empty_content_raises() -> None:
    """内容为空白 → ProviderResponseError."""
    p = build([make_response("   ")])
    with pytest.raises(ProviderResponseError):
        p.chat([user("hi")])


def test_only_inline_think_tags_raises() -> None:
    """只有内联思维链没有正文 → 报错而不是返回空串 / Inline reasoning only raises."""
    p = build([make_response("<think>想了半天</think>")])
    with pytest.raises(ProviderResponseError):
        p.chat([user("hi")])


# --- 推理模型的真实行为（实测发现）/ real reasoning-model behaviour --------------


def test_reasoning_field_is_captured_separately() -> None:
    """
    思维链走独立的 reasoning 字段时，正文应干净、思维链应被保留供排查。
    When the chain-of-thought arrives in its own field, the answer stays clean and
    the reasoning is retained for diagnostics.

    这是 Ollama 的 OpenAI 兼容端点的真实行为（实测 qwen3.5:9b）。
    """
    p = build([make_response("二", reasoning="Thinking: 1+1=2, in Chinese 二")])
    result = p.chat([user("1+1?")])

    assert result.text == "二"
    assert result.reasoning is not None
    assert "Thinking" in result.reasoning


def test_truncated_by_max_tokens_gives_actionable_error() -> None:
    """
    **实测踩到的坑**：qwen3.5:9b 回答一个字却花了 900 个 token 在思维链上，
    max_tokens 不够时 content 为空、finish_reason=length。

    报错必须指出「被 max_tokens 截断、预算被思维链吃光」，而不是笼统的「内容为空」
    ——后者会把排查方向完全带偏。
    The error must say the response was truncated and the budget went to reasoning,
    not merely "empty content", which sends debugging the wrong way.

    见 docs/issues/001-ollama-reasoning-token-budget.md
    """
    p = build(
        [
            make_response(
                "",
                reasoning="很长的思维链……",
                finish_reason="length",
                usage=FakeUsage(20, 900, 920),
            )
        ]
    )
    with pytest.raises(ProviderResponseError) as excinfo:
        p.chat([user("1+1?")])

    message = str(excinfo.value)
    assert "max_tokens" in message
    assert "900" in message  # 烧掉的 token 数要写出来
    assert "推理模型" in message  # 要点明根因


def test_truncation_error_is_not_retryable() -> None:
    """
    截断错误不应重试 —— 用同样的 max_tokens 重试必然再次截断，只是三倍的等待。
    A truncation error must not be retried: the same max_tokens will truncate again.
    """
    p = build(
        [make_response("", reasoning="…", finish_reason="length", usage=FakeUsage(20, 900, 920))]
    )
    with pytest.raises(ProviderResponseError) as excinfo:
        p.chat([user("hi")])
    assert excinfo.value.retryable is False


def test_truncation_without_reasoning_still_explains() -> None:
    """非推理模型被截断时也应说明是 max_tokens 的问题 / Truncation is explained either way."""
    p = build([make_response("", finish_reason="length", usage=FakeUsage(10, 64, 74))])
    with pytest.raises(ProviderResponseError, match="max_tokens"):
        p.chat([user("hi")])


def test_empty_choices_raises() -> None:
    """choices 为空 → ProviderResponseError."""
    from tests.llm.fakes import FakeResponse

    p = build([FakeResponse(choices=[])])
    with pytest.raises(ProviderResponseError, match="空的 choices"):
        p.chat([user("hi")])


def test_empty_messages_rejected() -> None:
    """空消息列表应在发请求前就被拒绝 / An empty message list is rejected up front."""
    p = build([make_response("ok")])
    with pytest.raises(ValueError, match="must not be empty"):
        p.chat([])


# --- 错误分类（决定重试策略）/ error classification drives the retry policy ------


def _error(message: str, status: int | None = None, name: str = "APIError") -> Exception:
    """构造一个带 status_code 的假 SDK 异常 / Fake SDK exception carrying a status code."""
    exc = type(name, (Exception,), {})(message)
    if status is not None:
        exc.status_code = status  # type: ignore[attr-defined]
    return exc


def test_rate_limit_is_retryable() -> None:
    """429 → RateLimitError 且可重试 / 429 maps to a retryable RateLimitError."""
    p = build([_error("Rate limit exceeded", status=429)])
    with pytest.raises(RateLimitError) as excinfo:
        p.chat([user("hi")])
    assert excinfo.value.retryable is True


def test_rate_limit_detected_from_message() -> None:
    """没有 status_code 时靠文案识别限流 / Detected from the message when no status code."""
    p = build([_error("Too Many Requests")])
    with pytest.raises(RateLimitError):
        p.chat([user("hi")])


def test_auth_error_is_not_retryable() -> None:
    """
    401 → AuthError 且不可重试 —— 重试再多次也不会变成有权限。
    401 maps to a non-retryable AuthError: retrying will never grant access.
    """
    p = build([_error("Invalid API key", status=401)])
    with pytest.raises(AuthError) as excinfo:
        p.chat([user("hi")])
    assert excinfo.value.retryable is False


def test_timeout_is_retryable() -> None:
    """超时 → ProviderTimeoutError 且可重试 / Timeouts are retryable."""
    p = build([_error("Request timed out")])
    with pytest.raises(ProviderTimeoutError) as excinfo:
        p.chat([user("hi")])
    assert excinfo.value.retryable is True


def test_unknown_error_defaults_to_retryable() -> None:
    """未知错误（网络抖动、5xx）默认可重试 / Unknown errors default to retryable."""
    p = build([_error("Internal server error", status=500)])
    with pytest.raises(ProviderError) as excinfo:
        p.chat([user("hi")])
    assert excinfo.value.retryable is True


# --- 结构化输出 / structured output -------------------------------------------


class Summary(BaseModel):
    """测试用的成稿结构 / Test schema."""

    title: str
    summary: str
    score: float = Field(ge=0, le=10)


def test_chat_json_parses_clean_output() -> None:
    """纯净 JSON 直接解析 / Clean JSON parses directly."""
    p = build([make_response('{"title": "标题", "summary": "摘要", "score": 8.5}')])
    result = p.chat_json([user("抽取")], Summary)

    assert isinstance(result, Summary)
    assert result.title == "标题"
    assert result.score == 8.5


def test_chat_json_parses_fenced_output() -> None:
    """带代码块的输出也能解析 / Fenced output parses too."""
    p = build([make_response('```json\n{"title": "T", "summary": "S", "score": 1}\n```')])
    assert p.chat_json([user("抽取")], Summary).title == "T"


def test_chat_json_repairs_after_validation_failure() -> None:
    """
    首次校验失败后，把错误回灌给模型并重试，第二次成功。
    After a validation failure the error is fed back to the model, and the retry
    succeeds — this is what makes smaller local models usable.
    """
    p = build(
        [
            make_response('{"title": "T"}'),  # 缺字段
            make_response('{"title": "T", "summary": "S", "score": 5}'),
        ]
    )
    result = p.chat_json([user("抽取")], Summary)

    assert result.summary == "S"
    assert len(p.fake_client.calls) == 2  # type: ignore[attr-defined]


def test_chat_json_repair_feeds_error_back() -> None:
    """重试时应把原样输出与错误一起发回 / The retry carries the bad output and the error."""
    p = build(
        [
            make_response("这不是 JSON"),
            make_response('{"title": "T", "summary": "S", "score": 5}'),
        ]
    )
    p.chat_json([user("抽取")], Summary)

    second_call_messages = p.fake_client.calls[1]["messages"]  # type: ignore[attr-defined]
    contents = " ".join(m["content"] for m in second_call_messages)
    assert "这不是 JSON" in contents  # 原样输出被回灌
    assert "输出不可用" in contents  # 纠正说明被回灌
    assert "找不到 JSON" in contents  # 具体原因被回灌


def test_chat_json_gives_up_after_max_repair() -> None:
    """重试用尽后抛错，且调用次数 = max_repair + 1 / Gives up after max_repair retries."""
    p = build([make_response("永远不是 JSON")] * 5)
    with pytest.raises(ProviderResponseError, match="仍未返回合法 JSON"):
        p.chat_json([user("抽取")], Summary, max_repair=2)
    assert len(p.fake_client.calls) == 3  # type: ignore[attr-defined]


def test_chat_json_includes_schema_in_prompt() -> None:
    """提示词里应带上 JSON Schema / The JSON Schema is included in the prompt."""
    p = build([make_response('{"title": "T", "summary": "S", "score": 1}')])
    p.chat_json([user("抽取")], Summary)

    contents = " ".join(m["content"] for m in p.fake_client.calls[0]["messages"])  # type: ignore[attr-defined]
    assert "summary" in contents and "score" in contents


def test_chat_json_prompt_forbids_echoing_schema() -> None:
    """提示词必须明确禁止照抄 Schema / The prompt must forbid echoing the schema."""
    p = build([make_response('{"title": "T", "summary": "S", "score": 1}')])
    p.chat_json([user("抽取")], Summary)

    contents = " ".join(m["content"] for m in p.fake_client.calls[0]["messages"])  # type: ignore[attr-defined]
    assert "不要照抄" in contents or "不要返回上面的 Schema" in contents


def test_chat_json_requests_server_side_json_mode() -> None:
    """chat_json 应请求服务端 JSON 模式 / chat_json requests the server-side JSON mode."""
    p = build([make_response('{"title": "T", "summary": "S", "score": 1}')])
    p.chat_json([user("抽取")], Summary)
    assert p.fake_client.calls[0]["response_format"] == {"type": "json_object"}  # type: ignore[attr-defined]


def test_plain_chat_does_not_request_json_mode() -> None:
    """普通 chat 不应带 response_format / A plain chat must not set response_format."""
    p = build([make_response("普通回复")])
    p.chat([user("hi")])
    assert "response_format" not in p.fake_client.calls[0]  # type: ignore[attr-defined]


def test_json_mode_falls_back_when_unsupported() -> None:
    """
    端点不认 response_format 时应退回普通模式，而不是让整条流水线失败。
    An endpoint that rejects response_format degrades gracefully instead of taking
    the pipeline down.
    """
    p = build(
        [
            _error("Unsupported parameter: response_format"),
            make_response('{"title": "T", "summary": "S", "score": 1}'),
        ]
    )
    assert p.chat_json([user("抽取")], Summary).title == "T"

    calls = p.fake_client.calls  # type: ignore[attr-defined]
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]  # 第二次已去掉


def test_schema_echo_is_detected_and_corrected() -> None:
    """
    **实测踩到的坑**：小模型会把 JSON Schema 原样抄回来。

    Pydantic 对此只会报「Field required」，完全不提示「你抄的是 Schema」，
    模型据此改不出正确结果。必须显式识别并给出针对性纠正，重试才有意义。
    Pydantic reports only "Field required", which never hints that the schema was
    echoed, so the model cannot self-correct. Detecting it explicitly is what makes
    the repair attempt actually work.

    见 docs/issues/002-small-model-echoes-json-schema.md
    """
    p = build(
        [
            make_response(json.dumps(Summary.model_json_schema())),  # 抄回 Schema
            make_response('{"title": "真标题", "summary": "真摘要", "score": 7}'),
        ]
    )
    result = p.chat_json([user("抽取")], Summary)

    assert result.title == "真标题"

    # 第二次调用必须带上「你返回的是 Schema 本身」这句纠正
    second = " ".join(m["content"] for m in p.fake_client.calls[1]["messages"])  # type: ignore[attr-defined]
    assert "Schema 定义本身" in second
    assert "真实数据" in second


@pytest.mark.parametrize("marker", ["properties", "$defs", "$schema", "definitions"])
def test_schema_markers_are_recognised(marker: str) -> None:
    """各种 Schema 特征字段都应被识别 / Every schema marker is recognised."""
    p = build(
        [
            make_response(json.dumps({marker: {"x": 1}})),
            make_response('{"title": "T", "summary": "S", "score": 1}'),
        ]
    )
    p.chat_json([user("抽取")], Summary)
    second = " ".join(m["content"] for m in p.fake_client.calls[1]["messages"])  # type: ignore[attr-defined]
    assert "Schema 定义本身" in second


# --- Ollama ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:11434", "http://localhost:11434/v1"),
        ("http://localhost:11434/", "http://localhost:11434/v1"),
        ("http://localhost:11434/v1", "http://localhost:11434/v1"),
        ("http://localhost:11434/v1/", "http://localhost:11434/v1"),
    ],
)
def test_ollama_base_url_normalisation(raw: str, expected: str) -> None:
    """容忍 .env 里各种写法 / Tolerates the various forms users write in .env."""
    assert normalize_base_url(raw) == expected


def test_ollama_is_marked_local() -> None:
    """Ollama 必须标记为本地 / Ollama must be flagged as local."""
    p = OllamaProvider(model="qwen3.5:9b", client=FakeOpenAIClient([make_response("ok")]))
    assert p.info.is_local is True
    assert p.info.name == "ollama"
    assert p.info.base_url.endswith("/v1")


def test_ollama_reuses_same_parsing() -> None:
    """
    Ollama 复用同一套解析逻辑，因此 <think> 剥离对本地模型同样生效。
    Ollama reuses the same parsing, so <think> stripping works for local models too
    — that is the whole point of subclassing rather than writing a second client.
    """
    p = OllamaProvider(client=FakeOpenAIClient([make_response("<think>x</think>本地答案")]))
    assert p.chat_text([user("hi")]) == "本地答案"


# --- 连通性探测 / health check ------------------------------------------------


def test_health_check_ok() -> None:
    """能正常调用即为健康 / A successful call means healthy."""
    assert build([make_response("pong")]).health_check() is True


def test_health_check_failure_returns_false() -> None:
    """失败时返回 False 而不是抛异常 / Failure returns False rather than raising."""
    assert build([_error("boom")]).health_check() is False


# --- 联网验证（默认跳过）/ live checks, skipped by default ---------------------


@pytest.mark.live
def test_live_deepseek() -> None:
    """
    真实调用 DeepSeek / Real call against DeepSeek.

    ⚠️ 会产生真实费用，**只在阶段验收时手动跑一次**，不要放进日常循环。
    Costs real money. Run once per phase sign-off, never in the routine loop.

    跑法：python -m pytest tests/llm/test_provider.py -m live -v
    """
    from dna.core.config import get_settings
    from dna.llm.factory import build_provider

    settings = get_settings()
    if not settings.deepseek_api_key:
        pytest.skip("未配置 DEEPSEEK_API_KEY")

    provider = build_provider("deepseek", settings)
    result = provider.chat([user("用一个词回答：中国的首都是？")], max_tokens=20)
    assert "北京" in result.text
    assert result.usage.total_tokens > 0


@pytest.mark.live
@pytest.mark.skip(
    reason="Local LLM 功能已冻结：接口保留，暂不做功能开发与测试。"
    "待本地方案成熟后（P10）再启用。/ Local LLM work is frozen; interface kept only."
)
def test_live_ollama() -> None:
    """
    真实调用本地 Ollama / Real call against local Ollama.

    **本用例已冻结**，不随 `-m live` 执行。
    P1 阶段已验证过 Ollama 路径可用（qwen2.5:3b 与 qwen3.5:9b 均通过，
    见 docs/issues/001、002），但本地方案当前不够成熟：qwen3.5:9b 回答一个字
    需 2244 tokens / 314 秒，尚不具备实用性。
    因此接口保留、代码保留、测试冻结，后续核实本地方案状态后再启动开发。

    The Ollama path was verified working in P1, but the local option is not yet
    mature enough to build on. The interface and code stay; this test is frozen.

    解冻时的跑法 / To unfreeze: 去掉上面的 skip 标记，先启动 `ollama serve`
    """
    from dna.core.config import get_settings
    from dna.llm.factory import build_provider

    provider = build_provider("ollama", get_settings())
    if not provider.health_check(timeout=10):
        pytest.skip("本机 Ollama 未运行")

    # 注意：本地推理模型不要设小的 max_tokens，会在推理阶段被截断（issue 001）
    result = provider.chat([user("用一个词回答：1+1=?")])
    assert result.text.strip()
