"""
test_doctor.py —— 环境自检单元测试 / Environment self-check unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/core/test_doctor.py -v

对应的人工验证 / Matching manual check:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m frontends.cli.main doctor -v

覆盖 / Covers:
    1. check_python()：当前环境必须通过（本项目要求 Python >= 3.12）
    2. check_packages()：P0 依赖必须 OK；未安装的后续阶段依赖只能是 WARN，不能 FAIL
    3. check_llm_config()：云端 provider 缺 key 报 FAIL；本地 provider 无需 key 报 OK
    4. check_proxy()：NO_PROXY 漏放行 localhost 必须报 FAIL（Ollama/RSSHub 会被代理拦截）
    5. check_inbox()：未启用报 SKIP（部署在私人电脑）；启用但缺凭证报 FAIL；
       **启用但白名单为空必须报 FAIL**（否则等于拒收全部却毫无提示）
    6. check_tts_service()：不在线且拉不起来 → WARN，且**说清缺哪一项配置**
    7. check_writable_dirs()：临时目录可写，且会自动创建目录
    8. run_all() / summarize() / has_failure() 汇总逻辑

预期 / Expected:
    18 passed；耗时 < 3s；不联网；只在 pytest 的 tmp_path 下建目录
"""

from __future__ import annotations

from pathlib import Path

from dna.core.config import Settings
from dna.core.doctor import (
    Status,
    check_inbox,
    check_llm_config,
    check_packages,
    check_proxy,
    check_python,
    check_tts_service,
    check_writable_dirs,
    has_failure,
    run_all,
    summarize,
)


# --- 基础检查 / basic checks -------------------------------------------------


def test_python_version_ok() -> None:
    """开发环境必须是 Python 3.12+ / The dev environment must be Python 3.12+."""
    assert check_python().status is Status.OK


def test_p0_packages_present_and_later_phases_only_warn() -> None:
    """
    P0 依赖必须齐备；后续阶段依赖未装只能 WARN。
    P0 dependencies must be installed; later-phase ones may only warn, since they
    are installed incrementally as each phase starts.
    """
    results = check_packages()
    by_name = {r.name: r for r in results}

    assert by_name["依赖 P0 core"].status is Status.OK
    for name, r in by_name.items():
        if not name.startswith("依赖 P0"):
            assert r.status in (Status.OK, Status.WARN), f"{name} 不应为 {r.status}"


# --- LLM 配置 / LLM configuration --------------------------------------------


def test_llm_config_missing_key_fails() -> None:
    """云端 provider 缺 key 必须 FAIL / A cloud provider without a key must fail."""
    s = Settings(_env_file=None, llm_provider="deepseek", deepseek_api_key="")
    r = check_llm_config(s)
    assert r.status is Status.FAIL
    assert "DEEPSEEK_API_KEY" in r.hint


def test_llm_config_with_key_ok() -> None:
    """有 key 则通过，且输出必须脱敏 / Passes with a key, and the key must be masked."""
    s = Settings(_env_file=None, llm_provider="deepseek", deepseek_api_key="sk-abcdefghijklmnop")
    r = check_llm_config(s)
    assert r.status is Status.OK
    assert "abcdefghijklmnop" not in r.detail  # 完整密钥不得出现在输出里


def test_llm_config_local_provider_needs_no_key() -> None:
    """本地 provider 无需 key / Local providers need no API key."""
    s = Settings(_env_file=None, llm_provider="ollama", deepseek_api_key="")
    assert check_llm_config(s).status is Status.OK


# --- 代理 / proxy ------------------------------------------------------------


def test_proxy_ok_when_localhost_bypassed() -> None:
    """NO_PROXY 放行本地回环时通过 / Passes when NO_PROXY bypasses loopback."""
    s = Settings(_env_file=None, https_proxy="http://child-prc.intel.com:913")
    assert check_proxy(s).status is Status.OK


def test_proxy_fails_when_localhost_not_bypassed() -> None:
    """
    漏放行 localhost 必须 FAIL —— 否则本地 Ollama / RSSHub 调用会被送进公司代理。
    Must fail when localhost is not bypassed, otherwise local Ollama and RSSHub
    calls are routed through the corporate proxy.
    """
    s = Settings(_env_file=None, https_proxy="http://child-prc.intel.com:913", no_proxy="10.0.0.0/8")
    r = check_proxy(s)
    assert r.status is Status.FAIL
    assert "localhost" in r.hint


def test_proxy_ok_without_proxy() -> None:
    """无代理时直接通过 / Passes when no proxy is configured."""
    s = Settings(_env_file=None, http_proxy="", https_proxy="")
    assert check_proxy(s).status is Status.OK


# --- 远程投递 / remote inbox --------------------------------------------------


def test_inbox_disabled_is_skip() -> None:
    """
    开发机上未启用应报 SKIP 而非 FAIL —— 该功能部署在用户私人电脑。
    On the dev machine a disabled inbox is SKIP, not FAIL: it runs on the user's
    personal computer.
    """
    s = Settings(_env_file=None, inbox_enabled=False)
    r = check_inbox(s)
    assert r.status is Status.SKIP
    assert "08_feishu_bot_deployment" in r.hint


def test_inbox_enabled_without_credentials_fails() -> None:
    """启用但缺凭证必须 FAIL / Enabled without credentials must fail."""
    s = Settings(_env_file=None, inbox_enabled=True, feishu_app_id="", feishu_app_secret="")
    assert check_inbox(s).status is Status.FAIL


def test_inbox_enabled_with_empty_whitelist_fails() -> None:
    """
    白名单为空等于拒收全部，必须明确报错而不是静默不工作。
    An empty whitelist rejects everyone; that must be reported loudly rather than
    failing silently at runtime.
    """
    s = Settings(
        _env_file=None,
        inbox_enabled=True,
        feishu_app_id="cli_x",
        feishu_app_secret="secret",
        feishu_allowed_users="",
    )
    r = check_inbox(s)
    assert r.status is Status.FAIL
    assert "白名单" in r.detail


def test_inbox_fully_configured_ok() -> None:
    """配置齐全时通过 / Passes when fully configured."""
    s = Settings(
        _env_file=None,
        inbox_enabled=True,
        feishu_app_id="cli_x",
        feishu_app_secret="secret",
        feishu_allowed_users="ou_a,ou_b",
    )
    r = check_inbox(s)
    assert r.status is Status.OK
    assert "2" in r.detail


# --- TTS 服务 / TTS service --------------------------------------------------
#
# 本项目已经没有模型文件了，所以这里只剩一个问题：那个地址上有没有活的服务，
# 以及**不在线时能不能自动拉起**。一律 WARN —— 采集/日报/文案都不需要它。


def _offline(monkeypatch) -> None:
    """把探活按成「不在线」/ Force the liveness probe to fail."""
    monkeypatch.setattr("dna.tts.client.TTSServiceClient.health",
                        lambda self, timeout=3.0: False)


def test_tts_service_online_is_ok(monkeypatch) -> None:
    monkeypatch.setattr("dna.tts.client.TTSServiceClient.health",
                        lambda self, timeout=3.0: True)
    r = check_tts_service(Settings(_env_file=None))
    assert r.status is Status.OK
    assert "在线" in r.detail


def test_tts_service_offline_but_startable_is_ok(monkeypatch, tmp_path: Path) -> None:
    """
    不在线**但能自动拉起**时报 OK：那是正常状态，报成问题会让人跑去手动开服务，
    而那一步本来是自动的。
    Reporting a problem here would send the user off to do what happens automatically.
    """
    _offline(monkeypatch)
    (tmp_path / "agentic_tts").mkdir()
    r = check_tts_service(Settings(_env_file=None, tts_module_dir=str(tmp_path)))
    assert r.status is Status.OK
    assert "自动拉起" in r.detail


def test_tts_service_without_module_dir_names_the_setting(monkeypatch) -> None:
    """缺配置时要说出配置项的名字，而不是只说「起不来」。"""
    _offline(monkeypatch)
    r = check_tts_service(Settings(_env_file=None, tts_module_dir=""))
    assert r.status is Status.WARN
    assert "TTS_MODULE_DIR" in r.detail


def test_tts_service_remote_says_it_cannot_be_started(monkeypatch) -> None:
    """远程地址拉不起来（那是别人机器上的进程），提示要给出去哪儿启动。"""
    _offline(monkeypatch)
    r = check_tts_service(Settings(_env_file=None,
                                   tts_service_url="http://10.0.0.9:8300"))
    assert r.status is Status.WARN
    assert "远程" in r.detail


def test_tts_service_wrong_module_dir_warns(monkeypatch, tmp_path: Path) -> None:
    """指到了一个不含 agentic_tts/ 的目录 —— 这是最常见的配错方式。"""
    _offline(monkeypatch)
    r = check_tts_service(Settings(_env_file=None, tts_module_dir=str(tmp_path)))
    assert r.status is Status.WARN
    assert "agentic_tts" in r.detail


# --- 目录可写 / writable directories -----------------------------------------


def test_writable_dirs_are_created(settings: Settings) -> None:
    """目录不存在时应自动创建并通过 / Directories are created on demand."""
    results = check_writable_dirs(settings)
    assert all(r.status is Status.OK for r in results)
    assert settings.output_path.exists()
    assert settings.data_path.exists()


# --- 汇总 / aggregation ------------------------------------------------------


def test_run_all_and_summary(settings: Settings) -> None:
    """run_all 应覆盖全部检查项并可汇总 / run_all returns every check and can be summarised."""
    results = run_all(settings)
    names = {r.name for r in results}

    assert "Python 版本" in names
    assert "LLM 配置" in names
    assert "代理配置" in names
    assert "远程投递（飞书）" in names

    counts = summarize(results)
    assert sum(counts.values()) == len(results)
    assert counts[Status.OK] > 0


def test_has_failure_on_clean_settings(settings: Settings, tmp_path: Path) -> None:
    """
    使用测试夹具的干净配置时不应有阻塞项。
    With the clean fixture settings there must be no blocking failure — this is the
    gate `dna doctor` uses to return exit code 0.

    `env_file` 必须注入临时文件 / The env file must be injected:
        `.env` 按约定不入库，**干净克隆里没有它**。不注入的话 `check_env_file`
        会去读开发机上那个未跟踪的文件——测试于是「在我机器上过、在干净克隆里挂」，
        而挂的原因和被测代码毫无关系。实测就是这么发现的。
        `.env` is deliberately untracked, so a fresh clone has none. Without injection the
        check reads an untracked file on the developer's machine and the test passes there
        while failing on a clean clone for a reason unrelated to the code under test.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=sk-test\n", encoding="utf-8")

    assert has_failure(run_all(settings, env_file=env_file)) is False


def test_missing_env_file_is_a_blocking_failure(settings: Settings, tmp_path: Path) -> None:
    """
    `.env` 不存在时必须是阻塞项，不能降级为警告。

    没有 `.env` 就没有 API key，所有花钱的节点都跑不了——这不是「提醒一下」
    的级别。`dna doctor` 靠它返回退出码 1，可以直接做脚本门禁。
    """
    assert has_failure(run_all(settings, env_file=tmp_path / "不存在.env")) is True
