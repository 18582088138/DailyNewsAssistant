"""日报与期次 / The daily digest and its issues。"""

from __future__ import annotations

import typer
from rich.table import Table

from dna.core.config import get_settings
from frontends.cli.app import app, console


@app.command()
def digest(
    limit: int | None = typer.Option(None, "--limit", "-n", help="日报最多几条；默认按 profile"),
    since_days: int = typer.Option(2, "--since-days", help="回看几天的文章"),
    source: str | None = typer.Option(None, "--source", "-s", help="只用指定来源的文章"),
    article: list[str] = typer.Option(
        None, "--article", "-a", help="直接指定文章 id（可重复），忽略时间窗与来源"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="**不调用 LLM、不产生费用**，只看会选出哪些条目"
    ),
    bilingual: bool = typer.Option(False, "--bilingual", help="同时产出英文版（额外计费）"),
    no_cache: bool = typer.Option(False, "--no-cache", help="不使用响应缓存，强制重新调用"),
) -> None:
    """
    生成一期日报 / Build one daily digest.

    流程：台账取数 → 清洗 → 去重聚类 → 打分 → 摘要 → 翻译 → 主线提炼 → _digest.json

    ⚠️ **本命令会调用 LLM 并产生费用。**先用 --dry-run 确认选出来的条目对不对，
    确认了再去掉这个参数——它只跑到打分为止，一次调用都不发。

    相同的请求会命中本地缓存，反复调试同一批文章不会重复计费。
    """
    from dna.llm.factory import get_llm
    from dna.pipeline import load_candidates, run_daily
    from dna.store import save_issue

    settings = get_settings()

    with console.status("从台账取数…"):
        items = load_candidates(
            settings=settings,
            since_days=since_days,
            source_ids=[source] if source else None,
            article_ids=list(article) if article else None,
        )

    if not items:
        console.print("[yellow]台账里没有可用的文章。先跑 dna fetch 或 dna add。[/yellow]")
        raise typer.Exit(code=1)

    llm = None
    if not dry_run:
        llm = get_llm(cache=not no_cache)
        console.print(f"[dim]LLM：{llm.info}　（相同请求会命中缓存，不重复计费）[/dim]")

    status_text = "打分排序中（不调用 LLM）…" if dry_run else "生成中（调用 LLM，请稍候）…"
    with console.status(status_text):
        result, report = run_daily(
            items,
            llm=llm,
            settings=settings,
            max_entries=limit,
            bilingual=bilingual,
            dry_run=dry_run,
        )

    console.print(f"\n[bold]{report.dedup_result.summary()}[/bold]\n")
    for line in report.explain(limit=len(result.entries)):
        console.print(line, markup=False, highlight=False)

    if dry_run:
        console.print(
            "\n[yellow]dry-run：未调用 LLM，未产生费用，未写文件。[/yellow]\n"
            "确认选题无误后，去掉 --dry-run 即可生成。"
        )
        return

    # 期次落盘 —— _digest.json（事实源）+ _references.md（来源汇总）
    saved = save_issue(result, settings=settings)

    if result.trend_note_zh:
        console.print(f"\n[bold]今日主线[/bold]\n{result.trend_note_zh}")

    console.print(f"\n[green]已生成：[/green]{saved.paths.root}")
    console.print(f"[dim]├ {saved.paths.digest.name}　三个发布应用的唯一输入[/dim]")
    console.print(f"[dim]└ {saved.paths.references.name}　全期来源与图片出处[/dim]")
    if saved.unresolved:
        console.print(
            f"[yellow]{len(saved.unresolved)} 条未能定位条目目录[/yellow]"
            "（详见 _references.md 末尾；重抓后可用 dna issue --refresh 修复）"
        )
    console.print(
        f"[dim]{len(result.entries)} 条"
        f"{f'，其中 {report.summaries_degraded} 条摘要降级' if report.summaries_degraded else ''}"
        f"{f'，英文版 {report.translated_count} 条' if bilingual else ''}"
        f"，耗时 {report.duration_ms / 1000:.1f} 秒[/dim]"
    )
    if hasattr(llm, "stats"):
        console.print(f"[dim]{llm.stats()}[/dim]")


@app.command()
def issue(
    date: str | None = typer.Option(None, "--date", "-d", help="期次日期 YYYYMMDD；默认最新一期"),
    list_all: bool = typer.Option(False, "--list", "-l", help="列出已生成的全部期次"),
    refresh: bool = typer.Option(
        False, "--refresh", help="重建 _references.md（纯计算，不调用 LLM，不花钱）"
    ),
) -> None:
    """
    查看已生成的期次 / Inspect a built issue.

    期次目录里只有整期产物；每条的正文、配图与文案都在 `outputs/articles/` 下，
    这里按 id 引用过去。

    `--refresh` 用于重抓改名之后修复 `_references.md` 里指错的链接——
    它只重算引用，**不碰 `_digest.json`，一次 LLM 都不调**。
    """
    from datetime import datetime as _dt

    from dna.store import (
        issue_paths,
        list_issues,
        load_issue,
        refresh_references,
        resolve_links,
    )

    settings = get_settings()

    if list_all:
        days = list_issues(settings=settings)
        if not days:
            console.print("[yellow]还没有生成过任何一期。[/yellow]先跑 dna digest。")
            return
        table = Table(title=f"已生成 {len(days)} 期", header_style="bold")
        table.add_column("期次", no_wrap=True)
        table.add_column("条目", justify="right")
        table.add_column("来源汇总", no_wrap=True)
        for day in days:
            digest_obj = load_issue(day, settings=settings)
            paths = issue_paths(day, settings=settings)
            table.add_row(
                paths.root.name,
                str(len(digest_obj.entries)) if digest_obj else "?",
                "✅" if paths.references.exists() else "[yellow]缺失[/yellow]",
            )
        console.print(table)
        return

    if date:
        try:
            day = _dt.strptime(date, "%Y%m%d").date()
        except ValueError:
            console.print("[red]日期格式应为 YYYYMMDD，例如 20260902。[/red]")
            raise typer.Exit(code=1) from None
    else:
        days = list_issues(settings=settings)
        if not days:
            console.print("[yellow]还没有生成过任何一期。[/yellow]先跑 dna digest。")
            raise typer.Exit(code=1)
        day = days[0]

    digest_obj = load_issue(day, settings=settings)
    if digest_obj is None:
        console.print(f"[red]读不到这一期：[/red]{issue_paths(day, settings=settings).digest}")
        raise typer.Exit(code=1)

    paths = issue_paths(day, settings=settings)
    console.print(f"\n[bold]{paths.root.name}[/bold]　{len(digest_obj.entries)} 条")
    console.print(f"[dim]{paths.root}[/dim]\n")

    if refresh:
        saved = refresh_references(day, settings=settings)
        console.print(f"[green]已重建 _references.md：[/green]{saved.summary()}\n")
        links = saved.links
    else:
        from dna.store import resolve_links

        links = resolve_links(digest_obj, settings=settings)

    by_id = {link.entry_id: link for link in links}

    table = Table(header_style="bold")
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("标题")
    table.add_column("评分", justify="right", no_wrap=True)
    table.add_column("图·视", justify="right", no_wrap=True)
    table.add_column("条目目录", no_wrap=True)
    for entry in digest_obj.entries:
        link = by_id.get(entry.id)
        table.add_row(
            str(entry.rank),
            (entry.title_zh[:42] + "…") if len(entry.title_zh) > 42 else entry.title_zh,
            f"{entry.score:.3f}",
            f"{len(entry.images)}·{len(entry.videos)}",
            "✅" if link and link.store_dir else "[yellow]未定位[/yellow]",
        )
    console.print(table)

    if digest_obj.trend_note_zh:
        console.print(f"\n[bold]今日主线[/bold]\n{digest_obj.trend_note_zh}")

    console.print("\n[bold]整期产物[/bold]")
    console.print(f"  {'✅' if paths.digest.exists() else '⬜'} _digest.json")
    console.print(f"  {'✅' if paths.references.exists() else '⬜'} _references.md")
    console.print(f"  {'✅' if paths.graphic.is_dir() else '⬜'} graphic/　图文版（P5）")
    console.print(f"  {'✅' if paths.podcast.is_dir() else '⬜'} podcast/　播客版（P7）")
