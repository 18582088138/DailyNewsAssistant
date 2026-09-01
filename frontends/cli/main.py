"""
命令行前端 / Command-line front-end.

前端层不含任何业务逻辑，只负责「收参数 → 调核心 → 渲染结果」。
The front-end holds no business logic: it parses arguments, calls the core, and
renders the result. All checks live in dna.core.doctor.

P0 提供 / Available at P0:
    dna doctor     环境自检
    dna version    版本信息
    dna config     显示当前生效配置（密钥自动脱敏）
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from dna import __version__
from dna.core.config import get_settings, load_profile, load_sources
from dna.core.doctor import Status, has_failure, run_all, summarize
from dna.core.errors import DNAError

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


@app.command()
def version() -> None:
    """显示版本 / Show the version."""
    console.print(f"DailyNewsAssistant [bold cyan]{__version__}[/bold cyan]")


@app.command()
def doctor(verbose: bool = typer.Option(False, "--verbose", "-v", help="显示每项的修复提示")) -> None:
    """
    环境自检 / Check the environment.

    存在 FAIL 项时以退出码 1 结束，便于在脚本或 CI 中做门禁。
    Exits with code 1 when any check fails, so it can gate scripts or CI.
    """
    results = run_all()

    table = Table(title="dna doctor", show_lines=False, header_style="bold")
    table.add_column("检查项", no_wrap=True)
    table.add_column("状态", justify="center", no_wrap=True)
    table.add_column("说明")

    for r in results:
        label, _ = _STYLE[r.status]
        detail = r.detail
        if r.hint and (verbose or r.status is Status.FAIL):
            detail = f"{detail}\n[dim]→ {r.hint}[/dim]"
        table.add_row(r.name, label, detail)

    console.print(table)

    counts = summarize(results)
    console.print(
        f"合计：[green]{counts[Status.OK]} OK[/green] · "
        f"[yellow]{counts[Status.WARN]} WARN[/yellow] · "
        f"[red]{counts[Status.FAIL]} FAIL[/red] · "
        f"[dim]{counts[Status.SKIP]} SKIP[/dim]"
    )

    if has_failure(results):
        console.print("[red]存在阻塞项，请按提示处理后重试。[/red]")
        raise typer.Exit(code=1)
    console.print("[green]环境就绪。[/green]")


@app.command(name="config")
def show_config() -> None:
    """显示当前生效配置 / Show the effective configuration (secrets are masked)."""
    try:
        s = get_settings()
    except DNAError as exc:
        console.print(f"[red]配置加载失败：{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title="生效配置 / effective settings", header_style="bold")
    table.add_column("项")
    table.add_column("值")

    def mask(value: str) -> str:
        """密钥脱敏，只保留前 7 位 / Mask a secret, keeping the first 7 characters."""
        return f"{value[:7]}…（{len(value)} 字符）" if value else "[dim]未配置[/dim]"

    rows = [
        ("LLM provider", f"{s.llm_provider}（备用：{s.llm_fallback_provider or '无'}）"),
        ("DeepSeek key", mask(s.deepseek_api_key)),
        ("OpenRouter key", mask(s.openrouter_api_key)),
        ("Ollama", f"{s.ollama_base_url} / {s.ollama_model}"),
        ("Embedding", s.embedding_model),
        ("TTS", f"{s.tts_provider} @ {s.tts_device}"),
        ("默认语言", str(s.default_language)),
        ("产物目录", str(s.output_path)),
        ("台账数据库", str(s.db_file)),
        ("投递接口", f"{s.inbox_provider}（enabled={s.inbox_enabled}）"),
        ("代理", s.https_proxy or "[dim]无[/dim]"),
    ]
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)


@app.command(name="sources")
def show_sources() -> None:
    """列出已启用的订阅源 / List the enabled news sources."""
    try:
        sources = load_sources()
        profile = load_profile()
    except DNAError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"订阅源（{len(sources)} 个启用）", header_style="bold")
    table.add_column("id", no_wrap=True)
    table.add_column("名称")
    table.add_column("类型", no_wrap=True)
    table.add_column("标签")
    for s in sources:
        table.add_row(s.id, s.name, str(s.kind), ", ".join(s.tags) or "-")
    console.print(table)
    console.print(f"关注关键词：{', '.join(profile.focus_keywords) or '（未设置）'}")


if __name__ == "__main__":  # pragma: no cover
    app()
