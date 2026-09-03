"""
test_factory.py —— provider 工厂与容错单元测试 / Provider factory and resilience tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/llm/test_factory.py -v

覆盖 / Covers:
    1. build_provider() 支持 deepseek / openrouter / vllm / ollama / openvino
    2. 云端 provider 缺 key 或缺 model → ConfigError（附带修复指引）
    3. 未知 provider 名 → ConfigError 且列出可选值
    4. is_configured() 判定是否具备可用配置
    5. **RetryPolicy 指数退避**：1s → 2s → 4s，且被 max_delay 截断
    6. **可重试错误会退避重试**，成功后返回，attempts 计数正确
    7. **不可重试错误（鉴权）不浪费重试**，直接切备用 provider
    8. 主 provider 重试用尽 → 切备用 provider
    9. 主备都失败 → 抛出最后一个异常
   10. get_llm()：备用与主相同时不挂备用；备用未配置时不挂备用
   11. ResilientProvider 本身是 LLMProvider，chat_json() 等基类能力可用
   12. **退避期间不真的 sleep**（注入的 sleep 被调用且时长正确）

为什么重点测降级 / Why fallback gets this much attention:
    OpenRouter 免费层限流是已经踩过的坑（doc_analyzer issue 002）。一次日报要跑
    十几次 LLM 调用，中途被限流会让整期作废。这层逻辑一旦有 bug，表现是「偶尔
    整期生成失败」，极难复现。
    OpenRouter's free tier rate-limiting has already bitten this user once. A single
    issue makes a dozen LLM calls, so being cut off halfway wastes the whole run.
    A bug here surfaces as "the whole issue occasionally fails" and barely reproduces.

预期 / Expected:
    23 passed；耗时 < 2s（退避通过注入的假 sleep 完成，不真的等待）
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from dna.core.config import Settings
from dna.core.errors import (
    AuthError,
    ConfigError,
    ProviderError,
    RateLimitError,
)
from dna.llm.base import LLMProvider, user
from dna.llm.factory import (
    ResilientProvider,
    RetryPolicy,
    build_provider,
    get_llm,
    is_configured,
)
from dna.llm.openvino_provider import OpenVINOProvider

from tests.llm.fakes import ScriptedProvider


@pytest.fixture
def cfg(tmp_path) -> Settings:  # noqa: ANN001
    """一份配置齐全的 Settings / A fully configured Settings instance."""
    return Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_fallback_provider="openrouter",
        deepseek_api_key="sk-ds-test",
        deepseek_model="deepseek-chat",
        openrouter_api_key="sk-or-test",
        openrouter_model="some/model:free",
        output_dir=tmp_path / "outputs",
        data_dir=tmp_path / "data",
    )


def recorder() -> tuple[list[float], object]:
    """返回 (记录列表, sleep 替身) / Return a delay log and a fake sleep."""
    delays: list[float] = []
    return delays, delays.append


# --- build_provider ----------------------------------------------------------


def test_build_deepseek(cfg: Settings) -> None:
    """构造 DeepSeek / Build the DeepSeek provider."""
    p = build_provider("deepseek", cfg)
    assert p.info.name == "deepseek"
    assert p.info.is_local is False


def test_build_openrouter(cfg: Settings) -> None:
    """构造 OpenRouter / Build the OpenRouter provider."""
    assert build_provider("openrouter", cfg).info.name == "openrouter"


def test_build_ollama_is_local(cfg: Settings) -> None:
    """Ollama 标记为本地 / Ollama is flagged local."""
    p = build_provider("ollama", cfg)
    assert p.info.is_local is True


def test_build_vllm_needs_no_key(cfg: Settings) -> None:
    """vLLM 是本地服务，不需要 API key / vLLM is local and needs no key."""
    p = build_provider("vllm", cfg)
    assert p.info.is_local is True


def test_build_openvino_placeholder(cfg: Settings) -> None:
    """
    OpenVINO 占位实现可以构造，但调用时给出明确指引而不是 KeyError。
    The OpenVINO placeholder constructs fine but explains itself when called.
    """
    p = build_provider("openvino", cfg)
    assert isinstance(p, OpenVINOProvider)
    with pytest.raises(ProviderError, match="P10"):
        p.chat([user("hi")])


def test_build_name_is_case_insensitive(cfg: Settings) -> None:
    """provider 名大小写不敏感 / Provider names are case-insensitive."""
    assert build_provider("DeepSeek", cfg).info.name == "deepseek"


def test_missing_api_key_raises_with_hint(cfg: Settings) -> None:
    """缺 key 时报错并说明改哪个变量 / A missing key names the variable to set."""
    broken = cfg.model_copy(update={"deepseek_api_key": ""})
    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        build_provider("deepseek", broken)


def test_missing_model_raises(cfg: Settings) -> None:
    """缺模型名时报错 / A missing model name raises."""
    broken = cfg.model_copy(update={"deepseek_model": ""})
    with pytest.raises(ConfigError, match="DEEPSEEK_MODEL"):
        build_provider("deepseek", broken)


def test_unknown_provider_lists_options(cfg: Settings) -> None:
    """未知 provider 应列出可选值 / An unknown provider lists the valid ones."""
    with pytest.raises(ConfigError, match="ollama"):
        build_provider("gpt-9", cfg)


def test_is_configured(cfg: Settings) -> None:
    """is_configured 反映配置完整性 / is_configured reflects completeness."""
    assert is_configured("deepseek", cfg) is True
    assert is_configured("ollama", cfg) is True
    assert is_configured("gpt-9", cfg) is False
    assert is_configured("deepseek", cfg.model_copy(update={"deepseek_api_key": ""})) is False


# --- 退避策略 / backoff policy ------------------------------------------------


def test_retry_policy_is_exponential() -> None:
    """退避时长指数增长 / Delays grow exponentially."""
    policy = RetryPolicy(base_delay=1.0, max_delay=100.0)
    assert [policy.delay_for(i) for i in range(4)] == [1.0, 2.0, 4.0, 8.0]


def test_retry_policy_respects_max_delay() -> None:
    """退避时长被 max_delay 截断 / Delays are capped by max_delay."""
    policy = RetryPolicy(base_delay=1.0, max_delay=5.0)
    assert policy.delay_for(10) == 5.0


# --- 重试 / retries -----------------------------------------------------------


def test_retries_then_succeeds() -> None:
    """限流两次后成功 / Succeeds after two rate-limit errors."""
    delays, sleep = recorder()
    primary = ScriptedProvider(
        "primary", [RateLimitError("429"), RateLimitError("429"), "终于成功"]
    )
    llm = ResilientProvider(primary, policy=RetryPolicy(max_attempts=3), sleep=sleep)

    result = llm.chat([user("hi")])

    assert result.text == "终于成功"
    assert primary.call_count == 3
    assert result.attempts == 3


def test_backoff_delays_are_exponential_and_not_slept_for_real() -> None:
    """
    退避确实发生且时长指数增长，但测试里不真的等待。
    Backoff happens with exponential delays, without the test actually waiting.
    """
    delays, sleep = recorder()
    primary = ScriptedProvider("p", [RateLimitError("1"), RateLimitError("2"), "ok"])
    llm = ResilientProvider(
        primary, policy=RetryPolicy(max_attempts=3, base_delay=1.0), sleep=sleep
    )
    llm.chat([user("hi")])

    assert delays == [1.0, 2.0]


def test_retries_exhausted_raises() -> None:
    """重试用尽后抛出最后一个异常 / Raises the last error once retries run out."""
    delays, sleep = recorder()
    primary = ScriptedProvider("p", [RateLimitError("boom")] * 5)
    llm = ResilientProvider(primary, policy=RetryPolicy(max_attempts=3), sleep=sleep)

    with pytest.raises(RateLimitError):
        llm.chat([user("hi")])
    assert primary.call_count == 3


def test_non_retryable_error_is_not_retried() -> None:
    """
    鉴权失败不应重试 —— 重试三次只是把失败时间拉长三倍。
    An auth failure must not be retried; three attempts only triple the time wasted.
    """
    delays, sleep = recorder()
    primary = ScriptedProvider("p", [AuthError("401"), "不该走到这里"])
    llm = ResilientProvider(primary, policy=RetryPolicy(max_attempts=3), sleep=sleep)

    with pytest.raises(AuthError):
        llm.chat([user("hi")])

    assert primary.call_count == 1
    assert delays == []  # 一次都没退避


# --- 降级 / fallback ----------------------------------------------------------


def test_falls_back_after_primary_exhausted() -> None:
    """主 provider 重试用尽后切备用 / Switches to the fallback once the primary is exhausted."""
    delays, sleep = recorder()
    primary = ScriptedProvider("primary", [RateLimitError("429")] * 5)
    fallback = ScriptedProvider("fallback", ["备用的回答"])
    llm = ResilientProvider(primary, fallback, policy=RetryPolicy(max_attempts=2), sleep=sleep)

    result = llm.chat([user("hi")])

    assert result.text == "备用的回答"
    assert result.info.name == "fallback"
    assert primary.call_count == 2
    assert fallback.call_count == 1


def test_auth_failure_switches_immediately() -> None:
    """
    鉴权失败应立刻切备用，不浪费退避时间 —— 这正是 OpenRouter 充值失效时的场景。
    An auth failure switches immediately without burning backoff time — exactly the
    situation when a key stops working.
    """
    delays, sleep = recorder()
    primary = ScriptedProvider("primary", [AuthError("invalid key")])
    fallback = ScriptedProvider("fallback", ["备用可用"])
    llm = ResilientProvider(primary, fallback, sleep=sleep)

    assert llm.chat([user("hi")]).text == "备用可用"
    assert primary.call_count == 1
    assert delays == []


def test_both_providers_failing_raises() -> None:
    """主备都失败时抛出异常 / Raises when both providers fail."""
    delays, sleep = recorder()
    primary = ScriptedProvider("p", [RateLimitError("a")] * 3)
    fallback = ScriptedProvider("f", [RateLimitError("b")] * 3)
    llm = ResilientProvider(primary, fallback, policy=RetryPolicy(max_attempts=2), sleep=sleep)

    with pytest.raises(ProviderError):
        llm.chat([user("hi")])
    assert primary.call_count == 2
    assert fallback.call_count == 2


def test_active_provider_is_reported() -> None:
    """info 应反映当前实际生效的 provider / info reflects the provider actually used."""
    delays, sleep = recorder()
    llm = ResilientProvider(
        ScriptedProvider("primary", [RateLimitError("x")] * 3),
        ScriptedProvider("fallback", ["ok"]),
        policy=RetryPolicy(max_attempts=1),
        sleep=sleep,
    )
    llm.chat([user("hi")])
    assert llm.info.name == "fallback"


# --- get_llm 组装 / assembly --------------------------------------------------


def test_get_llm_attaches_fallback(cfg: Settings) -> None:
    """主备不同且都配置好时应挂上备用 / Attaches the fallback when it differs and is configured."""
    llm = get_llm(cfg, cache=False)
    assert isinstance(llm, ResilientProvider)
    assert llm.primary.info.name == "deepseek"
    assert llm.fallback is not None
    assert llm.fallback.info.name == "openrouter"


def test_get_llm_skips_identical_fallback(cfg: Settings) -> None:
    """备用与主相同则不挂 / No fallback when it is the same as the primary."""
    same = cfg.model_copy(update={"llm_fallback_provider": "deepseek"})
    assert get_llm(same, cache=False).fallback is None  # type: ignore[union-attr]


def test_get_llm_skips_unconfigured_fallback(cfg: Settings) -> None:
    """
    备用未配置完整时不挂 —— 挂一个坏的备用比没有备用更糟，会再浪费一轮重试。
    An unconfigured fallback is skipped: a broken fallback is worse than none, as
    it wastes another round of retries after the primary already failed.
    """
    broken = cfg.model_copy(update={"openrouter_api_key": ""})
    assert get_llm(broken, cache=False).fallback is None  # type: ignore[union-attr]


def test_get_llm_provider_override(cfg: Settings) -> None:
    """可用参数覆盖 .env 中的 provider / The provider can be overridden per call."""
    llm = get_llm(cfg, provider="ollama", cache=False)
    assert llm.primary.info.name == "ollama"  # type: ignore[union-attr]


def test_get_llm_without_fallback(cfg: Settings) -> None:
    """可以显式关闭备用 / The fallback can be disabled explicitly."""
    assert get_llm(cfg, with_fallback=False, cache=False).fallback is None  # type: ignore[union-attr]


# --- 包装后仍是完整的 LLMProvider / the wrapper is still a full provider --------


class Tiny(BaseModel):
    value: int


def test_resilient_provider_supports_chat_json() -> None:
    """
    ResilientProvider 继承自 LLMProvider，因此 chat_json 等能力自动可用，
    且结构化输出的修复重试与降级可以叠加生效。
    Because ResilientProvider is itself an LLMProvider, chat_json works through it
    and JSON repair composes with provider fallback.
    """
    delays, sleep = recorder()
    primary = ScriptedProvider("primary", [RateLimitError("429")])
    fallback = ScriptedProvider("fallback", ['{"value": 42}'])
    llm = ResilientProvider(primary, fallback, policy=RetryPolicy(max_attempts=1), sleep=sleep)

    assert llm.chat_json([user("give me json")], Tiny).value == 42


def test_resilient_provider_is_llm_provider() -> None:
    """类型契约：包装后仍满足 LLMProvider / The wrapper still satisfies the interface."""
    llm = ResilientProvider(ScriptedProvider("p", ["ok"]))
    assert isinstance(llm, LLMProvider)
