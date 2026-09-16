"""环境与配置相关的命令 / Environment and configuration commands。"""

from __future__ import annotations

import typer
from rich.table import Table

from dna import __version__
from dna.core.config import get_settings, load_profile, load_sources
from dna.core.config_edit import mask_secret
from dna.core.doctor import Status, has_failure, run_all, summarize
from dna.core.errors import DNAError
from frontends.cli.app import _STYLE, app, console


@app.command()
def version() -> None:
    """显示版本 / Show the version."""
    console.print(f"DailyNewsAssistant [bold cyan]{__version__}[/bold cyan]")


@app.command()
def doctor(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示每项的修复提示"),
) -> None:
    """
    环境自检 / Check the environment.

    存在 FAIL 项时以退出码 1 结束，便于在脚本或 CI 中做门禁。
    Exits with code 1 when any check fails, so it can gate scripts or CI.
    """
    # 上层的检查由前端交进去：`core` 不认识 `dna.tts`（架构铁律，见 CLAUDE.md）。
    # 从前这一条是靠 core/doctor.py 里一行函数内 import 偷偷反向依赖实现的。
    from dna.tts.doctor import check_tts_service

    results = run_all(extra=[check_tts_service])

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
        """打码复用 `config_edit.mask_secret`：两份实现早晚会漂开，而这是密钥。"""
        return mask_secret(value) if value else "[dim]未配置[/dim]"

    rows = [
        ("LLM provider", f"{s.llm_provider}（备用：{s.llm_fallback_provider or '无'}）"),
        ("DeepSeek key", mask(s.deepseek_api_key)),
        ("OpenRouter key", mask(s.openrouter_api_key)),
        ("Ollama", f"{s.ollama_base_url} / {s.ollama_model}"),
        ("Embedding", s.embedding_model),
        ("TTS 服务", f"{s.tts_service_url}（预处理 {'开' if s.tts_preprocess else '关'}）"),
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



@app.command()
def gui(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址"),
    port: int = typer.Option(8080, "--port", "-p", help="端口"),
    show: bool = typer.Option(True, "--show/--no-show", help="是否自动打开浏览器"),
) -> None:
    """
    启动台账工作台 / Launch the article workbench.

    一张大表：每篇文章一行，总结 / 短视频 / 口播 / 长文案各一列，
    每格都能单独重做。**打开界面本身不产生费用**，只有点生成按钮才会调用 LLM。
    """
    from frontends.nicegui_app.main import run

    console.print(f"[green]台账工作台启动中：[/green]http://{host}:{port}")
    console.print("[dim]打开界面不产生费用；点击生成按钮才会调用 LLM。Ctrl+C 退出。[/dim]")
    run(host=host, port=port, show=show)
