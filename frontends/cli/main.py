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

    from dna.tts.supervisor import gui_url as _tts_gui_url

    rows = [
        ("LLM provider", f"{s.llm_provider}（备用：{s.llm_fallback_provider or '无'}）"),
        ("DeepSeek key", mask(s.deepseek_api_key)),
        ("OpenRouter key", mask(s.openrouter_api_key)),
        ("Ollama", f"{s.ollama_base_url} / {s.ollama_model}"),
        ("Embedding", s.embedding_model),
        ("TTS 服务", f"{s.tts_service_url}（预处理 {'开' if s.tts_preprocess else '关'}）"),
        ("TTS 界面", _tts_gui_url(s)),
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
def fetch(
    source: str | None = typer.Option(None, "--source", "-s", help="只抓指定 id 的源"),
    limit: int = typer.Option(5, "--limit", "-n", help="每个源最多抓几条"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只预览采集到什么，不抓正文不落盘"),
    no_images: bool = typer.Option(False, "--no-images", help="不下载配图，只存正文"),
    max_images: int = typer.Option(
        10, "--max-images", help="每篇最多存几张配图（默认 10，多存是为了攒素材）"
    ),
    no_videos: bool = typer.Option(False, "--no-videos", help="不下载官方视频（视频较大较慢）"),
    refetch: bool = typer.Option(False, "--refetch", help="已抓过的文章也重抓"),
) -> None:
    """
    采集入库 / Collect from feeds and ingest.

    完整流程：采集 → 过滤 → 抓正文 → 落盘 → 记台账。
    **不调用 LLM，不产生费用**，可放心重复执行。

    加 --dry-run 只看采集到什么，不写任何文件。
    """
    from dna.store import intake_sources

    if dry_run:
        _preview_collection(source, limit)
        return

    with console.status("采集入库中（抓正文较慢，请稍候）…"):
        result = intake_sources(
            source_ids=[source] if source else None,
            limit_per_source=limit,
            download_images=not no_images,
            max_images=max_images,
            download_videos=not no_videos,
            refetch=refetch,
        )

    _render_intake(result)


@app.command()
def add(
    urls: list[str] = typer.Argument(..., help="一个或多个文章链接，也可以直接粘一整段带链接的文字"),
    no_images: bool = typer.Option(False, "--no-images", help="不下载配图"),
    max_images: int = typer.Option(
        10, "--max-images", help="每篇最多存几张配图（默认 10，多存是为了攒素材）"
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

    with console.status("抓取中…"):
        result = intake_urls(
            list(urls),
            download_images=not no_images,
            max_images=max_images,
            download_videos=not no_videos,
            refetch=refetch,
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

    table = Table(title=f"文章总表（显示 {len(records)} / 共 {ledger.total()} 条）", header_style="bold")
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
        info.add_row("抓取时间", f"{record.fetched_at:%Y-%m-%d %H:%M}（第 {record.fetch_count} 次）")
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
    max_images: int = typer.Option(
        10, "--max-images", help="每篇最多存几张配图（默认 10，多存是为了攒素材）"
    ),
    no_videos: bool = typer.Option(False, "--no-videos", help="不下载官方视频"),
) -> None:
    """重新抓取一篇文章 / Re-fetch one article."""
    from dna.store import refetch_article

    record = _resolve_article(article_id)
    with console.status(f"重新抓取 {record.url} …"):
        updated = refetch_article(
            record.id,
            download_images=not no_images,
            max_images=max_images,
            download_videos=not no_videos,
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


@app.command()
def produce(
    article_id: str = typer.Argument(..., help="文章 id，前 8 位即可"),
    kind: str | None = typer.Option(
        None,
        "--kind",
        "-k",
        help=(
            "summary | shortvideo | narration | longform"
            " | shortvideo_audio | narration_audio | longform_audio"
        ),
    ),
    lang: str = typer.Option("zh", "--lang", "-l", help="输出语言：zh | en"),
    instructions: str = typer.Option(
        "", "--instructions", "-i", help="本次的额外要求，例如「加长到 40 秒」"
    ),
    all_kinds: bool = typer.Option(False, "--all", help="生成常规三项（不含长文案与音频）"),
    variant: str | None = typer.Option(
        None, "--variant", help="长文案形式：feature（专题，单角色）| interview（访谈，双角色）"
    ),
    force: bool = typer.Option(False, "--force", help="已有产物也重做（会重新计费）"),
) -> None:
    """
    为一篇文章生成产物 / Produce content for one article.

    ⚠️ **文案类产物会调用 LLM 并产生费用。** 已有的产物默认直接复用、不重复计费，
    要重做请加 --force。

    长文案（5~15 分钟）**不在 --all 里**，必须显式 --kind longform：
    它一篇要 5~9 次调用，是其余三项加起来的两倍多。

    `--lang en` 出英文版。**英文版单独计费**，而且不进 --all——
    多数文章不需要英文版，跟着批量跑等于每篇都翻倍。
    总结类的英文版翻译已写好的中文（便宜）；三种文稿的英文版**原生重写**，
    因为中文 30 秒的稿子翻成英文不是 30 秒的稿子，而时长是它们的验收标准。

    `--force` 时**不走 LLM 缓存**：缓存故意不设过期、键是完整提示词，
    照常走的话重做会拿回一模一样的旧答案。要引导出不同的结果，
    再配 `--instructions "加长到 40 秒"`。

    三种 `*_audio` 是**本地 TTS 合成，一分钱不花，但很花时间**（实测 RTF≈2.5：
    口播约 4 分钟，长文案约 37 分钟）。它们同样不在 --all 里，理由是时间不是钱。
    """
    from dna.produce import ProductionKind, produce_all, spec
    from dna.produce import produce as produce_one
    from dna.produce.tasks import batch_kinds, estimate_calls

    record = _resolve_article(article_id)

    if not all_kinds and kind is None:
        console.print("[yellow]请指定 --kind，或用 --all 生成常规四项。[/yellow]")
        console.print(f"[dim]可选：{' | '.join(str(k) for k in ProductionKind)}[/dim]")
        raise typer.Exit(code=1)

    if all_kinds:
        console.print(
            f"[dim]将生成 {len(batch_kinds())} 项，预估 "
            f"{estimate_calls(list(batch_kinds()))} 次 LLM 调用（已有产物会跳过）[/dim]"
        )
        with console.status(f"生成中：{record.title[:30]}…"):
            results = produce_all(record.id, lang=lang, force=force)
    else:
        task = spec(kind)
        if task.needs_variant and variant is None:
            variant = "feature"
            console.print("[dim]未指定 --variant，按专题（单角色）生成[/dim]")

        if task.audio_of is not None:
            console.print(
                "[dim]本地 TTS 合成，不产生费用；长稿子要跑几十分钟，进度见日志[/dim]"
            )
        else:
            console.print(f"[dim]预估 {task.approx_calls} 次 LLM 调用[/dim]")

        with console.status(f"生成中：{task.label}…"):
            results = [
                produce_one(
                    record.id,
                    kind,
                    lang=lang,
                    variant=variant,
                    force=force,
                    instructions=instructions,
                )
            ]

    console.print(f"\n[bold]{record.title}[/bold]")
    for result in results:
        style = "green" if result.ok else "red"
        marker = "[dim]○[/dim]" if result.skipped else f"[{style}]●[/{style}]"
        console.print(f"  {marker} {result.summary()}")

    if any(r.ok and not r.skipped for r in results):
        console.print(f"\n[dim]产物目录：{get_settings().output_path / record.store_dir}[/dim]")


@app.command()
def tts(
    say: str | None = typer.Option(None, "--say", help="合成一句话试听，写成 wav"),
    out: str = typer.Option("tts-check.wav", "--out", "-o", help="试听文件写到哪"),
    voice: str | None = typer.Option(None, "--voice", help="指定音色；默认按 .env"),
) -> None:
    """
    检查 TTS 服务 / Check the TTS service.

    **不产生任何费用**（加 --say 时会有一次很小的 LLM 预处理调用，可用
    TTS_PREPROCESS=false 关掉）。服务不在线时会**自动拉起**，三次失败才报不可用——
    这条命令同时也是在验「自动拉起这条路通不通」。

    加 --say 会真的合成一句话并写成 wav，用来确认「服务在、音色对、声音正常」。
    换部署环境（本机 → 4060 那台）后第一件事就该跑它：改 TTS_SERVICE_URL 再跑一次。
    """
    import time as _time
    from pathlib import Path

    from dna.tts import RTF_ESTIMATE, SpeechSegment, VoiceSpec, get_tts, voice_for_role
    from dna.tts.base import TTSError
    from dna.tts.supervisor import ensure_service

    settings = get_settings()
    provider = get_tts(settings)

    # 探活与自动拉起放在最前面：后面每一步都依赖服务在线，
    # 让它们各自失败一次只会给出三条互相矛盾的报错。
    try:
        with console.status(f"检查 TTS 服务 {settings.tts_service_url}…"):
            status = ensure_service(settings)
    except TTSError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"\n[green]{status.summary()}[/green]")
    console.print(f"[bold]引擎[/bold]　{provider.info}")

    try:
        speakers = provider.available_speakers()
    except TTSError as exc:
        console.print(f"[red]取音色清单失败：{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[dim]可用音色（{len(speakers)}）：{'、'.join(speakers)}[/dim]")
    def _describe(role: str) -> str:
        """一行说清这个角色怎么发声 / One line: how this role speaks."""
        spec_ = voice_for_role(role, settings)
        if spec_.ref_audio:
            kind = "x-vector 克隆" if spec_.x_vector_only else "ICL 克隆"
            return f"{kind} {Path(spec_.ref_audio).name}"
        return f"内置音色 {spec_.speaker}"

    console.print(f"[dim]主持人：{_describe('host')}　嘉宾：{_describe('guest')}[/dim]")

    if say is None:
        console.print("\n[green]服务就绪。[/green][dim]加 --say \"一句话\" 可实际合成试听。[/dim]")
        return

    # 默认拿 voice_for_role 给的那一份**整体**，不要只取 speaker：
    # 克隆模式的信息（ref_audio / x_vector_only）都在那个对象里，
    # 只抄一个字段就等于把「默认走克隆」这件事悄悄丢掉。
    # The whole spec is used: clone fields live on it, and copying one field would
    # silently drop cloning.
    spec_ = voice_for_role("host", settings)
    if voice:
        spec_ = VoiceSpec(speaker=voice, language=spec_.language)   # --voice 指定 = 用内置音色
    console.print(f"[dim]发声方式：{spec_.mode}"
                  + (f"　参考音频 {spec_.ref_audio}" if spec_.ref_audio else
                     f"　音色 {spec_.speaker}") + "[/dim]")
    started = _time.perf_counter()
    with console.status(f"合成中（约 {len(say) / 4.5 * RTF_ESTIMATE:.0f} 秒起）…"):
        try:
            clip = provider.synthesize([SpeechSegment(text=say, voice=spec_)])
        except TTSError as exc:
            console.print(f"[red]合成失败：{exc}[/red]")
            raise typer.Exit(code=1) from exc
    elapsed = _time.perf_counter() - started

    path = Path(out).resolve()
    path.write_bytes(clip.wav)
    # 耗时与音频时长**分开报**：只给一个数字会被读成"这次跑了多久"，
    # 而音频时长通常小一个数量级（CPU 上 RTF≈13）。
    console.print(
        f"\n[green]已写入：[/green]{path}\n"
        f"[dim]{len(say)} 字　音频 {clip.seconds:.1f}s　耗时 {elapsed:.1f}s"
        f"　RTF {elapsed / clip.seconds:.2f}[/dim]"
    )
    if clip.artifacts:
        console.print(f"[dim]服务端产物：{clip.run}/（{len(clip.artifacts)} 个文件）[/dim]")


@app.command()
def gui(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址"),
    port: int = typer.Option(8080, "--port", "-p", help="端口"),
    show: bool = typer.Option(True, "--show/--no-show", help="是否自动打开浏览器"),
) -> None:
    """
    启动台账工作台 / Launch the article workbench.

    一张大表：每篇文章一行，总结 / 英文总结 / 短视频 / 口播 / 长文案各一列，
    每格都能单独重做。**打开界面本身不产生费用**，只有点生成按钮才会调用 LLM。
    """
    from frontends.nicegui_app.main import run

    console.print(f"[green]台账工作台启动中：[/green]http://{host}:{port}")
    console.print("[dim]打开界面不产生费用；点击生成按钮才会调用 LLM。Ctrl+C 退出。[/dim]")
    run(host=host, port=port, show=show)


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

    for source, target in plan.moves[:15]:
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


# --- CLI 内部辅助 / CLI helpers ------------------------------------------------


_STATUS_STYLE = {
    "ok": "[green]ok[/green]",
    "degraded": "[yellow]degraded[/yellow]",
    "failed": "[red]failed[/red]",
    "pending": "[dim]pending[/dim]",
}


def _status_label(status: object) -> str:
    """状态着色 / Colourise a status value."""
    return _STATUS_STYLE.get(str(status), str(status))


def _resolve_article(prefix: str):  # noqa: ANN201 - 返回 ArticleRecord
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
        console.print(f"[red]✗ {failure.source_id}[/red]（{failure.source_name}）：{failure.reason}")
    console.print("\n[dim]去掉 --dry-run 即可抓正文并入库。[/dim]")


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


@app.command(name="prompt")
def prompt_lab_cmd(
    article_id: str | None = typer.Argument(
        None, help="文章 id，前 8 位即可；配 --list 时可省略"
    ),
    task: str | None = typer.Option(
        None, "--task", "-t", help="summary | shortvideo | narration | longform"
    ),
    lang: str = typer.Option("zh", "--lang", "-l", help="输出语言：zh | en"),
    variant: str | None = typer.Option(
        None, "--variant", help="长文案形式：feature（专题）| interview（访谈）"
    ),
    instructions: str = typer.Option(
        "", "--instructions", "-i", help="本次的额外要求，测试它接在提示词末尾的效果"
    ),
    run_live: bool = typer.Option(
        False, "--run", help="真机跑一次并打印产物（**会产生费用**）"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过 --run 的确认"),
    no_cache: bool = typer.Option(
        False, "--no-cache", help="配 --run：绕过 LLM 缓存，在同一份提示词上另抽一个样本"
    ),
    save_dir: bool = typer.Option(
        False, "--save", help="把提示词与产物存到 outputs/prompt_lab/，方便前后对比"
    ),
    list_tasks: bool = typer.Option(False, "--list", help="列出各任务读哪些提示词文件"),
    full: bool = typer.Option(False, "--full", help="打印完整提示词，不折叠长文案的中间几节"),
) -> None:
    """
    提示词调试台 / Tune the prompts —— 看提示词、试提示词。

    提示词正文都在 `config/prompts/`，一个任务一个文件。改完用这个命令验证：

    \b
      dna prompt --list                       # 各任务读哪些文件
      dna prompt a1b2c3d4 -t narration        # 看真正发出去的提示词（免费）
      dna prompt a1b2c3d4 -t narration --run  # 真机跑一次看产物（**计费**）

    **默认不花钱**：不加 --run 时一次 LLM 调用都不发，只把提示词渲染出来。
    长文案会打印「提纲 + 每节」十几条，因为每一节的提示词都不一样。

    看到的提示词与 `dna produce` / 工作台点「重做」发出去的**逐字节相同**——
    两条路走的是同一个 `dna.produce.service.generate_text()`，只是这里换了 provider。
    所以在这里调好的提示词，在应用里必然生效。

    ⚠️ `--run` 不写文件也不记台账：调试台跑十次不该在产物目录里留十份垃圾。
    要正式产出请用 `dna produce`。
    """
    from dna.produce import prompt_lab
    from dna.produce.tasks import spec

    if list_tasks:
        table = Table(title="提示词文件 / prompt files", header_style="bold")
        table.add_column("任务", style="bold")
        table.add_column("提示词文件（config/prompts/ 下）")
        for kind in prompt_lab.TASKS:
            table.add_row(
                f"{kind}\n[dim]{spec(kind).label}[/dim]",
                "\n".join(prompt_lab.prompt_files_for(kind)),
            )
        console.print(table)
        console.print(
            "\n[dim]占位符写 {{name}}；整行 `## @key` 起一个新块。"
            "改完运行 dna prompt <id> -t <task> 看效果。[/dim]"
        )
        return

    if article_id is None or task is None:
        console.print("[yellow]请给出文章 id 与 --task，或用 --list 看有哪些任务。[/yellow]")
        console.print(f"[dim]可选任务：{' | '.join(str(k) for k in prompt_lab.TASKS)}[/dim]")
        raise typer.Exit(code=1)

    record = _resolve_article(article_id)
    task_spec = spec(task)
    if task_spec.kind not in prompt_lab.TASKS:
        console.print(f"[red]{task} 不调用 LLM，没有提示词可调。[/red]")
        raise typer.Exit(code=1)
    if task_spec.needs_variant and variant is None:
        variant = "feature"
        console.print("[dim]未指定 --variant，按专题（单角色）[/dim]")

    if run_live and not yes:
        console.print(
            f"[yellow]--run 会真实调用 LLM，预估 {task_spec.approx_calls} 次，产生费用。[/yellow]"
        )
        if not typer.confirm("继续？"):
            raise typer.Exit(code=1)

    if run_live:
        with console.status(f"真机生成中：{task_spec.label}…"):
            result = prompt_lab.run(
                record.id,
                task_spec.kind,
                lang=lang,
                variant=variant,
                instructions=instructions,
                cache=not no_cache,
            )
    else:
        result = prompt_lab.render(
            record.id,
            task_spec.kind,
            lang=lang,
            variant=variant,
            instructions=instructions,
        )

    console.print(f"\n[bold]{result.article_title}[/bold]")
    console.print(
        f"[dim]{result.article_id[:8]} · 正文 {result.body_chars} 字 · "
        f"provider {result.provider}[/dim]"
    )
    console.print(f"[dim]提示词文件：{'、'.join(prompt_lab.prompt_files_for(task_spec.kind))}[/dim]")

    if not result.ok:
        console.print(f"\n[red]{result.error}[/red]")
        raise typer.Exit(code=1)

    # 长文案十几条提示词全打出来会淹掉终端；默认只展开首尾，中间给一行提要。
    # `--full` 全展开，`--save` 落盘之后随便看。
    total = len(result.prompts)
    for index, prompt in enumerate(result.prompts, 1):
        folded = not full and total > 4 and 2 <= index < total
        console.rule(f"[bold cyan]提示词 {index}/{total}[/bold cyan]")
        if folded:
            console.print(f"[dim]（{len(prompt)} 字，已折叠；--full 展开）[/dim]")
            continue
        console.print(prompt, markup=False, highlight=False)

    if result.output:
        console.rule("[bold green]产物[/bold green]")
        console.print(result.output, markup=False, highlight=False)

    console.print(f"\n{result.summary()}")

    if save_dir:
        from datetime import datetime

        from dna.core.naming import slugify

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        root = (
            get_settings().output_path
            / "prompt_lab"
            / f"{stamp}__{task_spec.kind}.{result.lang}__{slugify(result.article_title, 24)}"
        )
        prompt_lab.save(result, root)
        console.print(f"[dim]已存：{root}[/dim]")


def _slug_id(feed_url: str) -> str:
    """由 feed 地址猜一个源 id / Guess a source id from the feed URL."""
    from dna.core.urls import host_of

    host = host_of(feed_url)
    parts = [p for p in host.split(".") if p not in ("www", "com", "cn", "org", "net", "io")]
    return "-".join(parts) or "new-source"


if __name__ == "__main__":  # pragma: no cover
    app()
