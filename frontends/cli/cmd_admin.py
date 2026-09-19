"""运维类命令：统计、布局迁移、探测订阅源 / Housekeeping commands。"""

from __future__ import annotations

import typer
from rich.table import Table

from dna.core.config import get_settings
from frontends.cli.app import app, console
from frontends.cli.render import (
    _slug_id,
    _status_label,
)


@app.command(name="migrate-layout")
def migrate_layout(
    dry_run: bool = typer.Option(False, "--dry-run", help="只看会移动什么，不动文件"),
) -> None:
    """
    把文章目录从 data/ 迁到 outputs/ / Move article directories into outputs/.

    文章目录里放的是产物——正文、配图、视频、各类文案，都是要打开、要拷走、
    要发布的东西，属于 outputs/。data/ 留给台账数据库与 LLM 缓存。

    **先移文件再改数据库**，中途失败时数据库未动，重跑即可从断点继续。
    """
    from dna.store import migrate, plan_migration

    plan = plan_migration() if dry_run else migrate()
    console.print(f"[bold]{plan.summary()}[/bold]\n")

    for source, _target in plan.moves[:15]:
        arrow = "将移动" if dry_run else "已移动"
        console.print(f"  {arrow}　{source.name}", markup=False, highlight=False)
    if len(plan.moves) > 15:
        console.print(f"  [dim]… 另有 {len(plan.moves) - 15} 个[/dim]")

    if plan.conflicts:
        console.print("\n[yellow]两边都存在，未处理（请人工确认哪份是新的）：[/yellow]")
        for source, _ in plan.conflicts:
            console.print(f"  {source}", markup=False, highlight=False)

    if plan.orphans:
        console.print(
            f"\n[yellow]旧位置还有 {len(plan.orphans)} 个目录没有任何台账行引用[/yellow]"
            "（多半是历史遗留的重名副本）。**未删除**，请自行确认后处理："
        )
        for path in plan.orphans[:10]:
            console.print(f"  {path}", markup=False, highlight=False)

    if plan.errors:
        console.print("\n[red]失败：[/red]")
        for key, reason in plan.errors:
            console.print(f"  {key}：{reason}", markup=False, highlight=False)

    if dry_run:
        console.print("\n[dim]这是 --dry-run，未改动任何文件。[/dim]")



@app.command()
def stats() -> None:
    """台账统计 / Ledger statistics."""
    from dna.store import Ledger

    ledger = Ledger(get_settings().db_file)
    total = ledger.total()

    if total == 0:
        console.print("[yellow]台账为空。[/yellow]先跑 dna fetch 或 dna add <链接>。")
        return

    console.print(f"\n[bold]共 {total} 篇文章[/bold]\n")

    by_status = Table(title="按状态", header_style="bold")
    by_status.add_column("状态", no_wrap=True)
    by_status.add_column("数量", justify="right")
    for name, count in sorted(ledger.count_by_status().items()):
        by_status.add_row(_status_label(name), str(count))
    console.print(by_status)

    by_source = Table(title="按来源", header_style="bold")
    by_source.add_column("来源", no_wrap=True)
    by_source.add_column("数量", justify="right")
    for name, count in ledger.count_by_source().items():
        by_source.add_row(name or "(未知)", str(count))
    console.print(by_source)



@app.command()
def probe(
    url: str = typer.Argument(..., help="网站首页或 feed 地址"),
    source_id: str = typer.Option("", "--id", help="生成 YAML 片段时用的源 id"),
    name: str = typer.Option("", "--name", help="源显示名"),
    lang: str = typer.Option("zh", "--lang", help="源语言：zh | en"),
    tags: str = typer.Option("", "--tags", help="标签，逗号分隔，如 ai,cn"),
) -> None:
    """
    探测网站的 RSS 地址 / Discover a site's RSS feed.

    传首页会自动尝试：页面声明的 feed → 常见路径（/feed、/rss…）。
    验证通过后直接给出可粘进 config/sources.yaml 的片段。
    **不调用 LLM，无费用。**
    """
    from dna.sources.discover import discover_feeds, suggest_yaml

    with console.status(f"探测 {url} …"):
        candidates = discover_feeds(url)

    if not candidates:
        console.print("[red]无法探测：地址不合法[/red]")
        raise typer.Exit(code=1)

    table = Table(title="探测结果", header_style="bold")
    table.add_column("状态", justify="center", no_wrap=True)
    table.add_column("地址")
    table.add_column("条目", justify="right", no_wrap=True)
    table.add_column("说明")

    for c in candidates:
        table.add_row(
            "[green]OK[/green]" if c.ok else "[dim]--[/dim]",
            c.url,
            str(c.entry_count) if c.ok else "-",
            c.title or c.reason,
        )
    console.print(table)

    usable = [c for c in candidates if c.ok and c.entry_count > 0]
    if not usable:
        console.print(
            "\n[yellow]没有找到可用的 feed。[/yellow]\n"
            "该站点可能已不提供 RSS（常见于 SPA 改版），可考虑：\n"
            "  1. 用自建 RSSHub 转换（P10 阶段接入，见 docs/02_development_plan.md）\n"
            "  2. 直接把文章链接发给应用（用户投递接口）"
        )
        raise typer.Exit(code=1)

    best = usable[0]
    console.print(f"\n[green]推荐：[/green]{best.url}")
    if best.latest_title:
        console.print(f"[dim]最新一条：{best.latest_title}[/dim]")

    console.print("\n[bold]粘进 config/sources.yaml 的 sources: 下面即可：[/bold]\n")
    snippet = suggest_yaml(
        best,
        source_id=source_id or _slug_id(best.url),
        name=name,
        lang=lang,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        max_items=15 if best.entry_count > 50 else None,
    )
    # markup=False：YAML 里的 [tech, cn] 会被 rich 当成标记语法吞掉
    # markup=False: rich would otherwise swallow YAML lists like [tech, cn] as markup
    console.print(snippet, markup=False, highlight=False)
    if best.entry_count > 50:
        console.print(
            f"\n[dim]该源单次返回 {best.entry_count} 条，已在片段里加上 max_items: 15 限量。[/dim]"
        )
