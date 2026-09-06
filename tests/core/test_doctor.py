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
    6. check_tts_model()：路径为空 / 不存在 / 无 IR 文件 → WARN；有 IR 文件 → OK
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
    check_tts_model,
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


# --- TTS 模型 / TTS model ----------------------------------------------------


def test_tts_model_unset_warns() -> None:
    """未配置 TTS 路径只是 WARN（P6 才用）/ An unset TTS path only warns."""
    s = Settings(_env_file=None, qwen3_tts_model_dir="")
    assert check_tts_model(s).status is Status.WARN


def test_tts_model_missing_dir_warns(tmp_path: Path) -> None:
    """路径不存在 → WARN / A missing directory warns."""
    s = Settings(_env_file=None, qwen3_tts_model_dir=str(tmp_path / "nope"))
    assert check_tts_model(s).status is Status.WARN


def test_tts_model_dir_without_ir_warns(tmp_path: Path) -> None:
    """目录存在但无 IR 文件 → WARN / A directory without IR files warns."""
    s = Settings(_env_file=None, qwen3_tts_model_dir=str(tmp_path))
    assert check_tts_model(s).status is Status.WARN


def test_tts_model_dir_with_ir_ok(tmp_path: Path) -> None:
    """存在 *.xml 即认为 OpenVINO 后端就位 / IR files mean the OpenVINO backend is ready."""
    (tmp_path / "openvino_talker_language_model.xml").write_text("<net/>", encoding="utf-8")
    s = Settings(_env_file=None, qwen3_tts_model_dir=str(tmp_path))
    r = check_tts_model(s)
    assert r.status is Status.OK
    assert "1 个OpenVINO IR" in r.detail


def test_tts_check_follows_the_configured_backend(tmp_path: Path) -> None:
    """
    检查的是**当前配的那个后端**，不是两个都要。

    Intel 机器上没有 CUDA 权重是完全正常的，把它报成问题只会让人学会忽略
    doctor 的输出——而 doctor 唯一的价值就是它说话时值得当真。
    Flagging the absence of the backend this machine does not use would train the user to
    ignore doctor, whose only value is being worth taking seriously.
    """
    (tmp_path / "openvino_talker_language_model.xml").write_text("<net/>", encoding="utf-8")

    s = Settings(
        _env_file=None,
        tts_provider="qwen3_torch",
        qwen3_tts_model_dir=str(tmp_path),  # OV 的目录在，但当前用的是 torch 后端
        qwen3_tts_torch_model_dir="",
    )
    r = check_tts_model(s)

    assert r.status is Status.WARN
    assert "QWEN3_TTS_TORCH_MODEL_DIR" in r.detail


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
