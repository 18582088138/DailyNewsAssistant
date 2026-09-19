"""
CLI 的渲染与查找辅助 / Rendering and lookup helpers for the CLI.

**不含任何命令**，所以各 `cmd_*.py` 都能 import 它而不产生环。
这里的函数只做两件事：把核心层的返回值排成人能读的样子，
以及把「id 前 8 位」解析成一条台账记录。
"""

from __future__ import annotations

import typer
from rich.table import Table

from dna.core.config import get_settings, load_sources
from frontends.cli.app import console

_STATUS_STYLE = {
    "ok": "[green]ok[/green]",
    "degraded": "[yellow]degraded[/yellow]",
    "failed": "[red]failed[/red]",
    "pending": "[dim]pending[/dim]",
}


def _status_label(status: object) -> str:
    """状态着色 / Colourise a status value."""
    return _STATUS_STYLE.get(str(status), str(status))


def _resolve_article(prefix: str):
    """
    按 id 前缀查找文章 / Look up an article by id prefix.

    允许只输前 8 位：完整 id 是 16 位哈希，手敲全长很痛苦。
    An 8-character prefix is enough; the full id is a 16-character hash and typing it
    out is needlessly painful.
    """
    from dna.store import Ledger

    ledger = Ledger(get_settings().db_file)

    record = ledger.get(prefix)
    if record is not None:
        return record

    matches = [r for r in ledger.list(limit=1000) if r.id.startswith(prefix)]
    if not matches:
        console.print(f"[red]找不到文章：{prefix}[/red]　用 dna list 查看可用的 id")
        raise typer.Exit(code=1)
    if len(matches) > 1:
        console.print(f"[red]id 前缀 {prefix} 匹配到 {len(matches)} 篇，请提供更长的前缀[/red]")
        raise typer.Exit(code=1)
    return matches[0]


def _intake_status(status: object, verb: str):
    """
    把采集进度接到 rich 的转圈行上 / Feed intake progress into the rich spinner.

    转圈行原本从头到尾一句话不变，一批十几篇跑好几分钟，
    「在跑」和「卡住了」看起来一模一样。
    """

    def _write(done: int, total: int, title: str) -> None:
        # 标题只留前几个字：转圈行是单行的，整条标题会把进度数字挤走
        head = (title or "").strip().replace("\n", " ")
        if len(head) > 24:
            head = head[:24] + "…"
        status.update(f"{verb} {done}/{total} · {head}")  # type: ignore[attr-defined]

    return _write


def _render_intake(result: object) -> None:
    """渲染入库结果 / Render an intake result."""
    console.print(f"\n[bold]{result.summary()}[/bold]")  # type: ignore[attr-defined]

    if result.filter_reasons:  # type: ignore[attr-defined]
        console.print("\n[dim]过滤明细：[/dim]")
        for reason, count in result.filter_reasons.items():  # type: ignore[attr-defined]
            console.print(f"[dim]  {reason} × {count}[/dim]")

    for source_id, reason in result.source_failures:  # type: ignore[attr-defined]
        console.print(f"[red]✗ {source_id}[/red]：{reason}")

    if result.fetched_degraded:  # type: ignore[attr-defined]
        console.print(
            f"[yellow]{result.fetched_degraded} 篇正文抽取降级[/yellow]"  # type: ignore[attr-defined]
            "（仅标题+链接，站点可能需登录或有反爬）"
        )

    # 媒体没抓下来只提醒。视频失败的常态是地域限制、会员墙、平台禁下载，
    # 这些重试也没用；正文已经入库，这篇文章照样能发。
    warnings = getattr(result, "media_warnings", [])
    if warnings:
        console.print(
            f"\n[yellow]{len(warnings)} 个媒体文件未下载[/yellow][dim]（不影响正文）[/dim]"
        )
        for article_id, reason in warnings[:10]:
            console.print(f"[dim]  {article_id[:8]}　{reason}[/dim]")
        if len(warnings) > 10:
            console.print(f"[dim]  …… 另有 {len(warnings) - 10} 条[/dim]")

    console.print("\n[dim]查看总表：dna list[/dim]")


def _preview_collection(source: str | None, limit: int) -> None:
    """采集预览，不写任何文件 / Preview a collection round without writing anything."""
    from dna.sources import build_adapters, collect

    configs = [c for c in load_sources() if source is None or c.id == source]
    if not configs:
        console.print(f"[red]没有匹配的信息源：{source}[/red]")
        raise typer.Exit(code=1)

    with console.status("采集中…"):
        result = collect(build_adapters(configs), limit_per_source=limit)

    table = Table(title=f"采集预览 —— {result.summary()}", header_style="bold")
    table.add_column("源", no_wrap=True)
    table.add_column("时间", no_wrap=True)
    table.add_column("标题")

    for item in result.items:
        when = item.published_at.strftime("%m-%d %H:%M") if item.published_at else "-"
        table.add_row(item.source_id, when, item.title[:60])
    console.print(table)

    for failure in result.failures:
        console.print(
            f"[red]✗ {failure.source_id}[/red]（{failure.source_name}）：{failure.reason}"
        )
    console.print("\n[dim]去掉 --dry-run 即可抓正文并入库。[/dim]")


def _slug_id(feed_url: str) -> str:
    """由 feed 地址猜一个源 id / Guess a source id from the feed URL."""
    from dna.core.urls import host_of

    host = host_of(feed_url)
    parts = [p for p in host.split(".") if p not in ("www", "com", "cn", "org", "net", "io")]
    return "-".join(parts) or "new-source"

