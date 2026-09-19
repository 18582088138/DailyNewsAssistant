"""文章总表：采集、导入、查看、重抓、删除 / The article ledger commands。"""

from __future__ import annotations

import typer
from rich.table import Table

from dna.core.config import get_settings
from frontends.cli.app import app, console
from frontends.cli.render import (
    _intake_status,
    _preview_collection,
    _render_intake,
    _resolve_article,
    _status_label,
)


@app.command()
def fetch(
    source: str | None = typer.Option(None, "--source", "-s", help="只抓指定 id 的源"),
    limit: int = typer.Option(5, "--limit", "-n", help="每个源最多抓几条"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只预览采集到什么，不抓正文不落盘"),
    no_images: bool = typer.Option(False, "--no-images", help="不下载配图，只存正文"),
    max_images: int | None = typer.Option(
        None, "--max-images", help="每篇最多存几张配图；不给则按配置（源级 > profile.yaml）"
    ),
    no_videos: bool = typer.Option(False, "--no-videos", help="不下载官方视频（视频较大较慢）"),
    refetch: bool = typer.Option(False, "--refetch", help="已抓过的文章也重抓"),
    days: int | None = typer.Option(
        None, "--days", "-d", help="只要最近几天的（覆盖 sources.yaml 里的按源设置）"
    ),
) -> None:
    """
    采集入库 / Collect from feeds and ingest.

    完整流程：采集 → 过滤 → 抓正文 → 落盘 → 记台账。
    **不调用 LLM，不产生费用**，可放心重复执行。

    加 --dry-run 只看采集到什么，不写任何文件。

    --days 只作用于这一次，不改 sources.yaml。**没有发布时间的条目不受它限制**，
    会照常入库——大量 feed 不给 pubDate，按「无日期即过期」处理会整源丢空。
    """
    from dna.store import intake_sources

    if dry_run:
        _preview_collection(source, limit)
        return

    with console.status("采集入库中（抓正文较慢，请稍候）…") as status:
        result = intake_sources(
            source_ids=[source] if source else None,
            limit_per_source=limit,
            download_images=not no_images,
            max_images=max_images,
            download_videos=not no_videos,
            refetch=refetch,
            max_age_days=days,
            on_progress=_intake_status(status, "采集"),
        )

    _render_intake(result)


@app.command()
def add(
    urls: list[str] = typer.Argument(..., help="一个或多个链接，也可以粘一整段带链接的文字"),
    no_images: bool = typer.Option(False, "--no-images", help="不下载配图"),
    max_images: int | None = typer.Option(
        None, "--max-images", help="每篇最多存几张配图；不给则按配置（源级 > profile.yaml）"
    ),
    no_videos: bool = typer.Option(False, "--no-videos", help="不下载官方视频"),
    refetch: bool = typer.Option(False, "--refetch", help="已抓过也重抓"),
) -> None:
    """
    直接抓取指定链接 / Fetch specific article URLs.

    支持一次多个链接，也支持直接粘贴一整段带链接的文字（会自动提取其中所有链接）。
    手动指定的链接**不经过订阅源的过滤规则**——你点名要的就是想要的。
    **不调用 LLM，不产生费用。**
    """
    from dna.store import intake_urls

    with console.status("抓取中…") as status:
        result = intake_urls(
            list(urls),
            download_images=not no_images,
            max_images=max_images,
            download_videos=not no_videos,
            refetch=refetch,
            on_progress=_intake_status(status, "抓取"),
        )

    if result.collected == 0:
        console.print("[yellow]没有识别到任何链接。[/yellow]")
        raise typer.Exit(code=1)

    _render_intake(result)
    for article_id in result.article_ids:
        console.print(f"[dim]  dna show {article_id[:8]}[/dim]")


@app.command(name="list")
def list_articles(
    status: str | None = typer.Option(None, "--status", help="pending | ok | degraded | failed"),
    source: str | None = typer.Option(None, "--source", "-s", help="只看指定来源"),
    search: str | None = typer.Option(None, "--search", "-q", help="标题或链接包含的关键词"),
    limit: int = typer.Option(30, "--limit", "-n", help="最多显示几条"),
) -> None:
    """
    文章总表 / The master article table.

    列出台账里的文章：标题、来源、抓取状态、正文长度、配图数。
    后续「选几篇做日报 / 选一篇做口播稿」都从这张表里挑。
    """
    from dna.store import FetchStatus, Ledger

    ledger = Ledger(get_settings().db_file)

    try:
        status_filter = FetchStatus(status) if status else None
    except ValueError:
        console.print(f"[red]未知状态：{status}[/red]（可选：pending / ok / degraded / failed）")
        raise typer.Exit(code=1) from None

    records = ledger.list(status=status_filter, source_id=source, search=search, limit=limit)

    if not records:
        console.print("[yellow]台账里还没有文章。[/yellow]先跑 dna fetch 或 dna add <链接>。")
        return

    title = f"文章总表（显示 {len(records)} / 共 {ledger.total()} 条）"
    table = Table(title=title, header_style="bold")
    table.add_column("id", no_wrap=True)
    table.add_column("状态", justify="center", no_wrap=True)
    table.add_column("来源", no_wrap=True)
    table.add_column("标题")
    table.add_column("正文", justify="right", no_wrap=True)
    table.add_column("图", justify="right", no_wrap=True)

    for r in records:
        table.add_row(
            r.id[:8],
            _status_label(r.status),
            r.source_id,
            r.title[:48] or "[dim](无标题)[/dim]",
            f"{r.text_len}" if r.text_len else "-",
            f"{r.image_count}" if r.image_count else "-",
        )
    console.print(table)
    console.print("[dim]查看详情：dna show <id>　重新抓取：dna refetch <id>[/dim]")


@app.command()
def show(article_id: str = typer.Argument(..., help="文章 id，前 8 位即可")) -> None:
    """
    查看一篇文章的抓取结果 / Inspect one article's fetch result.

    显示落盘位置、正文预览、配图与视频清单，用于核对抓取是否正确。
    """
    from dna.store import FetchStatus

    record = _resolve_article(article_id)
    settings = get_settings()

    console.print(f"\n[bold]{record.title or '(无标题)'}[/bold]")
    console.print(f"[dim]{record.url}[/dim]\n")

    info = Table(show_header=False, box=None)
    info.add_row("id", record.id)
    info.add_row("状态", _status_label(record.status))
    info.add_row("来源", f"{record.source_id}（{record.via}）")
    if record.author:
        info.add_row("作者", record.author)
    if record.published_at:
        info.add_row("发布时间", record.published_at.strftime("%Y-%m-%d %H:%M"))
    if record.fetched_at:
        fetched = f"{record.fetched_at:%Y-%m-%d %H:%M}（第 {record.fetch_count} 次）"
        info.add_row("抓取时间", fetched)
    info.add_row("正文长度", f"{record.text_len} 字")
    info.add_row("配图 / 视频", f"{record.image_count} / {record.video_count}")
    if record.error:
        info.add_row("错误", f"[red]{record.error}[/red]")
    console.print(info)

    if not record.store_dir:
        console.print("\n[yellow]尚未落盘。[/yellow]")
        return

    directory = settings.output_path / record.store_dir
    console.print(f"\n[bold]落盘位置：[/bold]{directory}")

    article_file = directory / "article.md"
    if article_file.exists():
        body = article_file.read_text(encoding="utf-8")
        preview = body.split("---", 2)[-1].strip()
        console.print(f"\n[dim]{preview[:500]}…[/dim]")

    images = sorted((directory / "images").glob("*")) if (directory / "images").exists() else []
    picture_files = [p for p in images if p.suffix != ".json"]
    if picture_files:
        console.print(f"\n[bold]配图 {len(picture_files)} 张：[/bold]")
        for path in picture_files:
            console.print(f"  {path.name}　[dim]{path.stat().st_size // 1024} KB[/dim]")

    videos = sorted((directory / "videos").glob("*")) if (directory / "videos").exists() else []
    video_files = [p for p in videos if p.suffix != ".json"]
    if video_files:
        console.print(f"\n[bold]视频 {len(video_files)} 个：[/bold]")
        for path in video_files:
            size_mb = path.stat().st_size / (1024 * 1024)
            console.print(f"  {path.name}　[dim]{size_mb:.1f} MB[/dim]")

    if record.status is FetchStatus.DEGRADED:
        console.print(
            f"\n[yellow]正文未抓到。[/yellow]把原文粘进 {article_file} 的「正文粘贴区」，"
            f"再执行 [bold]dna sync {record.id[:8]}[/bold] 回写台账。"
        )


@app.command()
def sync(article_id: str = typer.Argument(..., help="文章 id，前 8 位即可")) -> None:
    """
    回写人工补写的正文 / Sync a hand-written body back into the ledger.

    知乎、小红书这类站点有强反爬，抓不到正文。做法是打开落盘的 article.md，
    把原文粘到「正文粘贴区」下面，保存，再执行本命令。

    不回写的话台账里这篇永远是「0 字 / degraded」，后续挑文章做日报时会被当成
    空文章跳过——补的正文等于白补。
    **不调用 LLM，不产生费用。**
    """
    from dna.store import sync_manual_body

    record = _resolve_article(article_id)
    updated, length = sync_manual_body(record.id)

    if updated is None:
        console.print("[red]这篇文章还没有落盘目录，无法回写。[/red]")
        raise typer.Exit(code=1)

    if length == 0:
        directory = get_settings().output_path / (updated.store_dir or "")
        console.print(
            f"[yellow]没有读到正文。[/yellow]请把原文粘进 {directory / 'article.md'} "
            "的「正文粘贴区」下面再试。"
        )
        raise typer.Exit(code=1)

    console.print(
        f"{_status_label(updated.status)}　已回写正文 [bold]{length}[/bold] 字："
        f"{updated.title}"
    )


@app.command()
def refetch(
    article_id: str = typer.Argument(..., help="文章 id，前 8 位即可"),
    no_images: bool = typer.Option(False, "--no-images", help="不下载配图"),
    max_images: int | None = typer.Option(
        None, "--max-images", help="每篇最多存几张配图；不给则按配置（源级 > profile.yaml）"
    ),
    no_videos: bool = typer.Option(False, "--no-videos", help="不下载官方视频"),
) -> None:
    """重新抓取一篇文章 / Re-fetch one article."""
    from dna.store import IntakeResult, refetch_article

    record = _resolve_article(article_id)
    detail = IntakeResult()  # 收媒体告警，`refetch_article` 的返回值里带不出来
    with console.status(f"重新抓取 {record.url} …"):
        updated = refetch_article(
            record.id,
            download_images=not no_images,
            max_images=max_images,
            download_videos=not no_videos,
            result=detail,
        )

    if updated is None:
        console.print("[red]重抓失败：文章不在台账里[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"{_status_label(updated.status)}　正文 {updated.text_len} 字　"
        f"配图 {updated.image_count} 张，视频 {updated.video_count} 个"
        f"　（第 {updated.fetch_count} 次抓取）"
    )
    if updated.error:
        console.print(f"[red]{updated.error}[/red]")
    for _, reason in detail.media_warnings:
        console.print(f"[yellow]⚠[/yellow] [dim]{reason}[/dim]")


@app.command()
def delete(
    article_ids: list[str] = typer.Argument(..., help="一个或多个文章 id，前 8 位即可"),
    keep_files: bool = typer.Option(
        False, "--keep-files", help="只删台账行，磁盘上的正文与媒体留着"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认（脚本里用）"),
) -> None:
    """
    删除文章 / Delete articles.

    删台账行 + 它的所有产物行 + 磁盘目录。**先列出将删什么再确认**——
    删除是这套工具里唯一不可撤销的操作，重抓也拿不回已经下线的图和视频。

    --keep-files 只清台账：用于「这篇不想在总表里看见，但素材还想留着」。
    """
    from dna.store import delete_articles, plan_delete

    records = [_resolve_article(prefix) for prefix in article_ids]
    ids = list(dict.fromkeys(r.id for r in records))  # 同一篇被点两次只算一次

    plan = plan_delete(ids)
    if not plan.has_work:
        console.print("[yellow]没有可删的文章[/yellow]")
        raise typer.Exit(code=1)

    console.print(f"\n[bold]将删除 {plan.summary()}[/bold]")
    for title in plan.titles[:10]:
        console.print(f"  · {title}")
    if len(plan.titles) > 10:
        console.print(f"  [dim]…… 另有 {len(plan.titles) - 10} 篇[/dim]")

    if keep_files:
        console.print("\n[dim]--keep-files：磁盘文件保留，只删台账行[/dim]")
    else:
        for directory in plan.directories[:10]:
            console.print(f"[dim]  {directory}[/dim]")
    for article_id, reason in plan.unsafe_dirs:
        console.print(f"[yellow]⚠ {article_id[:8]} 的目录不会删：{reason}[/yellow]")

    if not yes and not typer.confirm("\n确认删除？此操作不可撤销", default=False):
        console.print("[dim]已取消[/dim]")
        return

    done = delete_articles(ids, remove_files=not keep_files)
    console.print(f"[green]✓[/green] {done.result_summary()}")
    for article_id, reason in done.errors:
        console.print(f"[red]✗ {article_id[:8]}[/red]：{reason}[dim]（台账行已保留）[/dim]")
