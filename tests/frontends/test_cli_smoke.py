"""
CLI 冒烟测试：每个子命令都注册了、`--help` 都打得出来。

    $PY -m pytest tests/frontends/test_cli_smoke.py -q

**这是拆分 `cli/main.py` 的安全网。** 那个文件一千三百多行、二十个命令，
而此前只有 version / doctor / config / sources 四个被测过。裸拆的风险不是
「逻辑写错」，而是**注册掉了一个命令、或者某个模块 import 环了**——
这两种错都不会被现有测试发现，只会在人真去敲那条命令时才冒出来。

`--help` 是最便宜的探针：它走完了「模块导入 → typer 注册 → 参数声明」
整条路，而且**一次 LLM 都不调、一个文件都不写**。

不测什么：不测命令的实际行为（那是各层自己的单测的事），
也不测帮助文本的措辞（会随文案调整而碎）。
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from frontends.cli.main import app

runner = CliRunner()

# 全部子命令。**写死在这里是故意的**：从 app 自己身上取一遍再断言「都能 --help」
# 等于用被测对象证明自己完整——命令掉了一个，两边一起少一个，测试照样绿。
EXPECTED_COMMANDS: tuple[str, ...] = (
    "version",
    "doctor",
    "config",
    "sources",
    "fetch",
    "add",
    "list",
    "show",
    "sync",
    "refetch",
    "delete",
    "produce",
    "tts",
    "gui",
    "migrate-layout",
    "digest",
    "issue",
    "stats",
    "probe",
    "prompt",
)


def _registered() -> set[str]:
    """app 上实际注册了哪些命令名。"""
    return {
        command.name or (command.callback.__name__.replace("_", "-"))
        for command in app.registered_commands
    }


def test_一个命令都没少() -> None:
    """
    拆分 `cli/main.py` 最容易出的错就是漏注册一个命令，而漏了不会报错。
    """
    registered = _registered()
    missing = set(EXPECTED_COMMANDS) - registered
    extra = registered - set(EXPECTED_COMMANDS)

    assert not missing, f"这些命令没注册上：{sorted(missing)}"
    assert not extra, (
        f"多出这些命令：{sorted(extra)}。"
        "新增命令是好事，但要同时加进 EXPECTED_COMMANDS，否则这条护栏就形同虚设"
    )


@pytest.mark.parametrize("command", EXPECTED_COMMANDS)
def test_每个命令的_help_都打得出来(command: str) -> None:
    """
    `--help` 走完「导入 → 注册 → 参数声明」整条路，零费用零落盘。

    import 环、参数默认值写错（比如 `int | None` 配了个不兼容的默认）
    都会在这里当场暴露。
    """
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0, f"dna {command} --help 挂了：\n{result.output}"
    assert result.output.strip(), f"dna {command} --help 什么都没输出"


def test_顶层_help_列出全部命令() -> None:
    """`dna --help` 是人找命令的唯一入口，少一条就等于那条命令不存在。"""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in EXPECTED_COMMANDS:
        assert command in result.output, f"dna --help 里看不到 {command}"
