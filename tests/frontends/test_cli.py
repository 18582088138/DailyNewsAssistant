"""
test_cli.py —— CLI 前端单元测试 / CLI front-end unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/frontends/test_cli.py -v

覆盖 / Covers:
    1. `dna version` 输出版本号
    2. `dna doctor` 可运行；无阻塞项时退出码 0，有阻塞项时退出码 1
    3. `dna config` **不得泄露完整密钥**（只显示前 7 位）
    4. `dna sources` 能列出仓库自带的订阅源
    5. 无参数时打印帮助而不是报错

预期 / Expected:
    6 passed；耗时 < 5s；不联网
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from dna import __version__
from dna.core.doctor import CheckResult, Status
from frontends.cli.main import app

runner = CliRunner()


def test_version() -> None:
    """版本命令输出当前版本号 / The version command prints the version."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_doctor_runs() -> None:
    """doctor 可运行并输出表格 / doctor runs and renders its table."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code in (0, 1)  # 取决于本机环境是否有 FAIL 项
    assert "Python" in result.stdout


def test_doctor_exit_code_1_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    存在 FAIL 项时必须以退出码 1 结束，便于脚本/CI 做门禁。
    A failing check must exit with code 1 so scripts and CI can gate on it.
    """
    monkeypatch.setattr(
        "frontends.cli.main.run_all",
        lambda: [CheckResult("假检查", Status.FAIL, "故意失败", hint="修它")],
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "存在阻塞项" in result.stdout


def test_doctor_exit_code_0_when_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """全部通过时退出码 0 / Exits 0 when everything passes."""
    monkeypatch.setattr(
        "frontends.cli.main.run_all",
        lambda: [CheckResult("假检查", Status.OK, "通过")],
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "环境就绪" in result.stdout


def test_config_masks_secrets() -> None:
    """
    config 命令绝不能打印完整密钥 —— 用户可能会截图分享终端输出。
    The config command must never print a full secret; users screenshot terminals.
    """
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0

    from dna.core.config import get_settings

    key = get_settings().deepseek_api_key
    if key:
        assert key not in result.stdout  # 完整密钥不得出现
        assert key[:7] in result.stdout  # 但前缀应可见，便于确认配了哪把


def test_sources_lists_enabled() -> None:
    """sources 命令列出启用的订阅源 / The sources command lists enabled sources."""
    result = runner.invoke(app, ["sources"])
    assert result.exit_code == 0
    assert "订阅源" in result.stdout


def test_no_args_shows_help() -> None:
    """无参数时打印帮助 / Invoking with no arguments prints help."""
    result = runner.invoke(app, [])
    assert "doctor" in result.stdout
