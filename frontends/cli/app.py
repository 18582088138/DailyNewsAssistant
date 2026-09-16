"""
CLI 的装配根 / The CLI's assembly root.

只有三样东西：`typer.Typer` 实例、共用的 `Console`、跑每条命令之前的 callback。

**为什么单独一个文件**：各 `cmd_*.py` 都要 `@app.command()` 装饰自己的命令，
所以都得 import `app`；而 `main.py` 又要 import 全部 `cmd_*` 才能完成注册。
`app` 留在 `main.py` 里就成环了。
The command modules need `app` and `main` needs them; keeping `app` here breaks
what would otherwise be an import cycle.
"""

from __future__ import annotations

import typer
from rich.console import Console

from dna.core.config import apply_proxy_env, get_settings
from dna.core.doctor import Status
from dna.core.logging import setup_logging

app = typer.Typer(
    name="dna",
    help="DailyNewsAssistant —— 每日 AI 资讯助手 / daily AI news assistant",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

# 状态 → 显示样式 / status to display style
_STYLE: dict[Status, tuple[str, str]] = {
    Status.OK: ("[green]OK[/green]", "green"),
    Status.WARN: ("[yellow]WARN[/yellow]", "yellow"),
    Status.FAIL: ("[red]FAIL[/red]", "red"),
    Status.SKIP: ("[dim]SKIP[/dim]", "dim"),
}


@app.callback()
def _before_any_command() -> None:
    # 每条子命令跑之前把日志接到文件上。放 callback 而不是模块顶层：
    # 顶层会在 `--help` 和被测试导入时也建目录写文件。
    #
    # 级别是 WARNING 而不是 INFO：命令行的输出是给人读的（表格、进度、颜色），
    # 掺进 INFO 会把它冲烂。而真正需要事后排查的东西——比如逐段合成失败的原因
    # ——本来就是 WARNING，一条不少。
    # WARNING, not INFO: the CLI's own output is meant to be read, and the lines that
    # matter after the fact are warnings anyway.
    #
    # `LOG_LEVEL` 填了就听它——这里以前写死 WARNING，于是配置项和界面上那个下拉
    # 都是摆设（issues/014）。
    s = get_settings()
    apply_proxy_env(s)
    setup_logging(level=s.log_level or "WARNING", log_dir=s.data_path / "logs",
                  log_file="dna.log")

__all__ = ["app", "console"]
