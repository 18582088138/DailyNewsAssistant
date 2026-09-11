"""
界面动作 / Workbench actions.

界面按钮与核心逻辑之间的唯一桥梁。**这里不写业务逻辑**——每个函数都只是
「调 dna.produce / dna.store，把结果整理成界面好显示的形状」。
The single bridge between the buttons and the core. No business logic lives here: each
function calls into `dna.produce` or `dna.store` and reshapes the result for display.

为什么必须走 io_bound / Why everything goes through io_bound:
    一次 LLM 调用要十几秒，长文案要一两分钟。直接在事件回调里同步调用会**冻住
    整个界面**——连滚动和点别的按钮都不行，看起来就像程序崩了。
    NiceGUI 的 `run.io_bound` 把它扔到线程池里，界面继续响应。
    A single LLM call takes ten-odd seconds and a long-form script one to two minutes.
    Calling synchronously inside an event handler freezes the whole page — no scrolling,
    no other buttons — and looks exactly like a crash. `run.io_bound` moves the work to a
    thread pool and keeps the UI alive.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from nicegui import app, run

from dna.core.config import get_settings
from dna.core.logging import get_logger
from dna.core.urls import extract_urls
from dna.llm.factory import get_llm
from dna.produce import (
    ProduceResult,
    ProductionKind,
    is_new_article,
    produce,
    read_production,
    spec,
)
from dna.produce.tasks import DEFAULT_LANGUAGE, audio_kind, json_sidecar
from dna.store import FetchStatus, IntakeResult, Ledger, ProductionRecord, intake_urls
from dna.store.ledger import ArticleRecord
from dna.tts.base import SpeechSegment, VoiceSpec
from dna.tts.factory import get_tts

logger = get_logger("gui.actions")


@dataclass
class RowView:
    """
    表格里的一行 / One row of the workbench table.

    把「文章记录 + 该文章的全部产物」打包在一起，界面层就不必再各自查库。
    Bundles the article record with all of its productions so the view layer never
    queries the database itself.
    """

    article: ArticleRecord
    productions: dict[tuple[str, str], ProductionRecord]
    """按 `(产物类型, 语言)` 索引 / keyed by kind and language."""

    is_new: bool = False
    """
    刚导入、还没调过 LLM / freshly imported and never sent to an LLM.

    判定在 `dna.produce.is_new_article`，不在这里——「有没有产物」是产物层的知识，
    界面只负责把它画成一个标识。
    The predicate lives in `dna.produce.is_new_article`: whether anything has been
    produced is the production layer's knowledge, and the view only draws the badge.
    """

    def production(
        self, kind: ProductionKind, lang: str = DEFAULT_LANGUAGE
    ) -> ProductionRecord | None:
        return self.productions.get((str(kind), lang))

    def has_language(self, kind: ProductionKind, lang: str) -> bool:
        """该语言版本是否已经生成 / Whether that language edition exists."""
        record = self.production(kind, lang)
        return bool(record and record.ok)

    @property
    def media_label(self) -> str:
        """配图与视频数量 / Image and video counts."""
        video = f"·{self.article.video_count}▶" if self.article.video_count else ""
        return f"{self.article.image_count}图{video}" if self.article.image_count or video else "—"

    @property
    def body_label(self) -> str:
        """正文字数 / Body length."""
        return f"{self.article.text_len} 字" if self.article.text_len else "—"


@dataclass
class PageView:
    """
    一页表格 / One page of the table.

    页数在这里算好，界面只管画：分页控件与「共 N 条」如果各算各的，
    两个数字迟早不一致，而人只会觉得「这表是坏的」。
    Paging is computed here so the control and the counter cannot disagree.
    """

    rows: list[RowView]
    total: int
    page: int
    page_size: int
    pages: int
    scanned_cap: bool = False


# 后置筛选一次最多扫多少行 / how many rows a post-filter may scan
#
# `only_new` / `only_gaps` 要看产物矩阵，SQL 层数不出来，只能取一批回来在
# Python 里筛。扫上限存在是为了不让「勾一下开关」变成全表扫描；**撞到上限
# 必须说出来**——一个看起来「筛完了」而其实只筛了一部分的列表，比明说
# 「只筛了最近 1000 条」危险得多。
# Post-filters need the production matrix, so they scan a capped batch. Hitting the cap
# is surfaced: a list that looks complete but is not is worse than an honest bound.
SCAN_CAP = 1000


def load_rows(
    *,
    status: str | None = None,
    source: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    only_gaps: bool = False,
    only_new: bool = False,
) -> PageView:
    """
    读取一页表格数据 / Load one page of the table.

    **产物状态一次查完**（`production_matrix`），不是每行每格查一次——
    一页 50 行 × 5 种产物就是 250 次查询，界面会肉眼可见地卡。
    Production status is fetched in a single query rather than per cell: fifty rows times
    five kinds would be 250 round trips and the lag would be visible.

    两条路 / Two paths:
        无后置筛选 —— SQL 直接 LIMIT/OFFSET，总数走 `ledger.count()`
        有后置筛选 —— 取 `SCAN_CAP` 行在 Python 里筛完再切页（见 `SCAN_CAP`）
    """
    settings = get_settings()
    ledger = Ledger(settings.db_file)

    page = max(1, int(page))
    page_size = max(1, int(page_size))
    status_filter = FetchStatus(status) if status else None
    post_filtered = only_new or only_gaps

    if post_filtered:
        records = ledger.list(
            status=status_filter, source_id=source, search=search, limit=SCAN_CAP
        )
        scanned_cap = len(records) >= SCAN_CAP
    else:
        records = ledger.list(
            status=status_filter, source_id=source, search=search,
            limit=page_size, offset=(page - 1) * page_size,
        )
        scanned_cap = False

    matrix = ledger.production_matrix([r.id for r in records])

    rows = [
        RowView(
            article=r,
            productions=(prods := matrix.get(r.id, {})),
            is_new=is_new_article(r, prods),
        )
        for r in records
    ]

    if only_new:
        rows = [row for row in rows if row.is_new]

    if only_gaps:
        # 「有缺口的」= 常规四项里至少有一项没生成。长文案不算——它本来就是按需的，
        # 把它计入的话所有文章都会显示有缺口，筛选就失去意义了。
        # "Has gaps" means at least one of the four routine kinds is missing. The
        # long-form script is excluded: it is on-demand by design, and counting it would
        # mark every article as having a gap, making the filter useless.
        from dna.produce.tasks import batch_kinds

        rows = [
            row
            for row in rows
            if any(
                (p := row.production(k)) is None or not p.ok for k in batch_kinds()
            )
        ]

    if post_filtered:
        total = len(rows)
        rows = rows[(page - 1) * page_size : page * page_size]
    else:
        total = ledger.count(status=status_filter, source_id=source, search=search)

    pages = max(1, -(-total // page_size))  # 向上取整 / ceiling division
    return PageView(
        rows=rows, total=total, page=min(page, pages), page_size=page_size,
        pages=pages, scanned_cap=scanned_cap,
    )


async def run_production(
    article_id: str,
    kind: ProductionKind | str,
    *,
    lang: str = DEFAULT_LANGUAGE,
    variant: str | None = None,
    force: bool = False,
    instructions: str = "",
    progress: dict | None = None,
    segments: list[SpeechSegment] | None = None,
    rendered: dict[int, bytes] | None = None,
) -> ProduceResult:
    """
    在后台线程里生成一种产物 / Produce one kind on a worker thread.

    provider 在线程里构造：`get_llm()` 会读 `.env` 并可能建立连接，
    放在事件循环里做同样会卡一下。
    The provider is built inside the thread: `get_llm()` reads `.env` and may open a
    connection, which would also stall the event loop.

    **音频产物不构造 LLM。**它一分钱都不花，却会因为 `get_llm()` 而要求 API key ——
    在没配 key 的机器上，合成语音本来是能跑的，却会在这一步先失败。
    Audio kinds do not build an LLM: they spend nothing, yet `get_llm()` would demand an
    API key and fail on a machine where synthesis would otherwise work fine.

    `segments` 只给音频产物用：TTS 操作台里逐段调好的分段从这里进来，
    走的仍是同一条落盘路径（台账、时长、字幕都一样）。
    `rendered` 是操作台里已经逐段生成好的波形，那些段不再重新合成。

    `progress` 是一个**共享字典**，工作线程往里写、界面定时读。
    跨线程直接改 NiceGUI 元素是不安全的；写字典再由 UI 侧轮询是最简单可靠的做法。
    A shared dict written by the worker and polled by the UI: mutating NiceGUI elements
    from another thread is unsafe, and polling a dict is the simplest safe alternative.
    """
    is_audio = spec(kind).audio_of is not None

    def _on_progress(done: int, total: int, seconds: float) -> None:
        if progress is not None:
            progress.update(done=done, total=total, seconds=seconds)

    def _work() -> ProduceResult:
        # provider 交给 `produce` 自己构造：**重做时它要一个不带缓存的**。
        # 在这里先建好再传进去，就会把「重做要绕开缓存」这条规则绕过去——
        # 于是重做调了 LLM、写了文件，内容却一字未变。
        # `produce` builds the provider itself because a redo needs an uncached one;
        # constructing it here would bypass that rule and make every redo a no-op.
        return produce(
            article_id,
            kind,
            lang=lang,
            variant=variant,
            force=force,
            instructions=instructions,
            on_progress=_on_progress if is_audio else None,
            segments=segments,
            rendered=rendered,
        )

    return await run.io_bound(_work)


def audio_estimate_seconds(
    record: ArticleRecord, kind: ProductionKind | str, lang: str = DEFAULT_LANGUAGE
) -> float:
    """
    合成这一格音频大约要等多久 / How long synthesising this cell will take.

    按**稿子的估算时长 × 实测 RTF** 算。长文案要等半小时以上，
    这个数字必须在按钮按下之前就摆出来——否则界面看起来就是卡死了。
    Computed from the script's projected duration times the measured real-time factor. A
    long-form script runs past half an hour, and without saying so up front the interface
    merely looks frozen.
    """
    from dna.produce.tasks import spec as _spec
    from dna.tts import estimate_synthesis_seconds

    script_kind = _spec(kind).audio_of
    if script_kind is None:
        return 0.0

    ledger = Ledger(get_settings().db_file)
    script = ledger.latest_production(record.id, str(script_kind), lang)
    return estimate_synthesis_seconds(script.est_seconds or 0.0) if script else 0.0


def last_instructions(
    record: ArticleRecord, kind: ProductionKind | str, lang: str = DEFAULT_LANGUAGE
) -> str:
    """
    上一版是带着什么额外要求生成的 / The extra requirements the last version used.

    用来预填「修改指令」输入框。**人调稿子是渐进的**——上次写了「用词再专业一点」，
    这次多半是在此基础上再加一条，而不是从零重想。每次都给一个空框，
    等于每次都要求人回忆上次改了什么。
    Used to pre-fill the instruction box. Tuning is incremental: the next round usually
    builds on the last one, and an empty box asks the user to remember what they wrote.
    """
    production = Ledger(get_settings().db_file).latest_production(
        record.id, str(kind), lang
    )
    return (production.instructions or "") if production else ""


def audio_for(kind: ProductionKind | str) -> ProductionKind | None:
    """这份稿子对应的音频产物 / The audio kind matching a script kind."""
    return audio_kind(kind)


def preview_links(text: str) -> list[str]:
    """
    从粘贴的文本里认出有哪些链接 / Find the links in whatever was pasted.

    纯函数，**先给人看清单再动手抓**。粘一整段聊天记录进来时，「认出了哪几条」
    是唯一能提前发现「少粘了一条」或「多认了一个图片地址」的机会——
    抓完再发现就已经在台账里留下垃圾行了。
    A pure function that lists what was found before anything is fetched. When a whole
    chat log is pasted this is the only chance to notice a missing link or a stray image
    URL; discovering it afterwards means junk rows are already in the ledger.
    """
    return extract_urls(text or "")


def _progress_writer(progress: dict | None):
    """
    把采集回调接到共享字典上 / Wire the intake callback to the shared dict.

    工作线程只写字典，UI 侧用 `ui.timer` 读——**不能在这里碰任何 NiceGUI 元素**。
    The worker only writes the dict; touching NiceGUI elements here is unsafe.
    """
    if progress is None:
        return None

    def _write(done: int, total: int, title: str) -> None:
        progress.update(done=done, total=total, title=title)

    return _write


async def import_links(
    text: str,
    *,
    download_images: bool = True,
    download_videos: bool = True,
    max_images: int | None = None,
    refetch: bool = False,
    progress: dict | None = None,
) -> str:
    """
    导入用户粘贴的链接 / Ingest the links the user pasted.

    **不调用 LLM，不产生费用**——只是抓正文和媒体。抓完是否要生成文案，
    仍然由表格里的格子逐个决定。
    Calls no LLM and costs nothing: it fetches bodies and media only. Whether to write any
    copy afterwards remains a per-cell decision in the table.

    走 `intake_urls` 而不是在界面里另写一套：链接提取、去重、按 canonical_url 判重、
    落盘、写台账全都在那里，重写一遍就会出现「命令行抓的和界面抓的不一样」。
    Delegates to `intake_urls` rather than reimplementing: extraction, de-duplication by
    canonical URL, storage and ledger writes all live there, and a second copy would mean
    the CLI and the workbench ingest differently.
    """

    def _work() -> str:
        result = intake_urls(
            text,
            download_images=download_images,
            max_images=max_images,
            download_videos=download_videos,
            refetch=refetch,
            on_progress=_progress_writer(progress),
        )
        if result.collected == 0:
            return "没有识别到任何链接"

        # 每一类都报出来，账要对得上：collected = 跳过 + 成功 + 降级 + 失败。
        # 只报「入了 N 条」的话，少掉的那几条去哪了没人知道。
        # Every bucket is reported so the counts reconcile; a bare "ingested N" leaves the
        # missing ones unexplained.
        parts = [f"识别 {result.collected} 条"]
        if result.fetched_ok:
            parts.append(f"成功 {result.fetched_ok}")
        if result.fetched_degraded:
            parts.append(f"降级 {result.fetched_degraded}（正文没抓到，可用 dna sync 手动补）")
        if result.skipped_existing:
            parts.append(f"已存在跳过 {result.skipped_existing}")
        if result.failed:
            parts.append(f"失败 {result.failed}")
        return "，".join(parts)

    return await run.io_bound(_work)




@dataclass
class TTSStatus:
    """
    TTS 服务此刻在不在 / Whether the TTS service is up right now.

    界面必须一直显示这个。合成那条路会**自动拉起**服务并等最多
    `tts_start_timeout` 秒（模型冷启动），期间画面上什么都没有 ——
    于是「点了没反应」和「正在冷启动」是同一个观感。
    The synthesis path auto-spawns the service and waits out a cold start with nothing
    on screen, making "nothing happened" indistinguishable from "still loading".
    """

    online: bool
    url: str
    detail: str


async def tts_voices() -> list[str]:
    """服务端的音色表 / The voice list，离线返回空表。**HTTP 调用，不能在事件循环里做。**"""
    from dna.tts.console import available_voices

    return await run.io_bound(available_voices)


async def speech_segments(
    article_id: str,
    kind: ProductionKind | str,
    *,
    lang: str = DEFAULT_LANGUAGE,
) -> list[SpeechSegment]:
    """
    这一格会念哪些分段 / The pieces this cell would speak.

    **会走一次朗读友好化**（`TTS_PREPROCESS=true` 时是一次 LLM 调用，很便宜），
    所以操作台把拿到的分段一直拿在手里，合成时原样传回 `run_production`——
    重新取一次不只是慢，还会再调一次 LLM，且切点可能与试听过的那批不同。
    Preprocessing happens here, so the console keeps the pieces it got and hands the very
    same list back at synthesis time.
    """
    from dna.produce.service import speech_segments_for

    def _work() -> list[SpeechSegment]:
        return speech_segments_for(article_id, kind, lang=lang)

    return await run.io_bound(_work)


async def preview_voice(text: str, voice: VoiceSpec, *, role: str = "narrator") -> bytes:
    """
    试听一段 / Audition one piece，返回 wav 字节，**不写台账**。

    参数拼装在 `dna.tts.console.preview_segment` 里（那里复用 provider 自己的
    那一份），界面不碰。
    """
    from dna.tts.console import preview_segment

    def _work() -> bytes:
        return preview_segment(text, voice, role=role)

    return await run.io_bound(_work)


async def save_script(
    article_id: str,
    kind: ProductionKind | str,
    text: str,
    *,
    lang: str = DEFAULT_LANGUAGE,
) -> int:
    """
    把操作台里校对过的文本写回稿子 / Write the console's proof-read text back.

    零 LLM、零费用；台账里多一行 `calls=0` 的「人工校对」。
    **先写稿子行、再写音频行** —— 顺序反了的话，音频那一格的前置稿子还是旧的。
    """
    from dna.produce.service import save_script_text

    def _work() -> int:
        return save_script_text(article_id, kind, text, lang=lang)

    return await run.io_bound(_work)


def script_is_editable(kind: ProductionKind | str, *, lang: str = DEFAULT_LANGUAGE) -> bool:
    """
    这一格的稿子能不能从操作台写回 / Whether this cell's script can be written back.

    长文案的稿子**按发言人分轮**存在 JSON 边车里，一段纯文本写不回去（`save_script_text`
    会直接拒绝）。面板要提前知道，好在打开时就说清「这里改的字不会写回稿子」——
    等到人校对完一整篇再报错，那份工就白做了。
    """
    task = spec(kind)
    if task.audio_of is not None:
        task = spec(task.audio_of)
    return not json_sidecar(task, lang)


def resplit_text(text: str, *, max_chars: int | None = None) -> list[str]:
    """
    重新拆条 / Re-split，纯函数、零费用，直接在事件循环里算（微秒级）。

    切法与自动合成同一份（`tts/segment.py`）—— 面板里看到的分段必须就是
    真会合成的那批。`max_chars` 由操作台上那个输入框给：默认值就是自动合成用的那个，
    调大调小只影响这一次拆分。
    """
    from dna.tts.console import resplit

    if max_chars:
        return resplit(text, max_chars=int(max_chars))
    return resplit(text)


async def upload_ref_audio(filename: str, data: bytes) -> str:
    """
    收下一份上传的参考音频 / Adopt an uploaded reference clip，返回相对路径。

    落盘是 I/O，交给工作线程；返回的相对路径可以直接进下拉框，也能存进 `.env`。
    """
    from dna.tts.console import save_ref_audio

    def _work() -> str:
        return save_ref_audio(filename, data)

    return await run.io_bound(_work)


async def tts_status() -> TTSStatus:
    """探一次 TTS 服务 / Probe the TTS service. 不拉起、不抛异常。"""

    def _work() -> TTSStatus:
        settings = get_settings()
        url = settings.tts_service_url
        client = get_tts(settings).client
        if client.health():
            return TTSStatus(online=True, url=url, detail=f"TTS 服务在线：{url}")
        hint = "点一下启动" if settings.tts_autostart else "自动启动已关闭（TTS_AUTOSTART）"
        return TTSStatus(online=False, url=url, detail=f"TTS 服务离线：{url}　·　{hint}")

    return await run.io_bound(_work)


async def tts_start() -> TTSStatus:
    """
    拉起 TTS 服务 / Bring the TTS service up.

    失败原因**原样上抛**：`ensure_service` 抛出的那句话里已经写了怎么排查
    （模块目录、Python 路径、端口占用），改写成「启动失败」就把它扔了。
    The reason is propagated verbatim; it already says where to look.
    """
    from dna.tts.supervisor import ensure_service

    def _work() -> TTSStatus:
        settings = get_settings()
        client = get_tts(settings).client
        ensure_service(settings, client=client)
        return TTSStatus(
            online=True, url=settings.tts_service_url,
            detail=f"TTS 服务已就绪：{settings.tts_service_url}",
        )

    return await run.io_bound(_work)


def production_text(
    article_id: str, kind: ProductionKind | str, lang: str = DEFAULT_LANGUAGE
) -> str:
    """读回产物内容供预览 / Read a production back for preview."""
    return read_production(article_id, kind, lang=lang)


def article_directory(record: ArticleRecord) -> str:
    """产物目录的绝对路径 / The absolute path of the article's directory."""
    if not record.store_dir:
        return ""
    return str(get_settings().output_path / record.store_dir)


def production_file(
    record: ArticleRecord, kind: ProductionKind | str, lang: str = DEFAULT_LANGUAGE
) -> Path | None:
    """
    某个产物的文件路径 / The file path of one production.

    给下载按钮用。**返回 None 表示文件不在**——按钮据此禁用，
    而不是让人点了之后拿到一个 404。
    Used by the download buttons. `None` means the file is missing, so the button is
    disabled rather than handing the user a 404 after the click.
    """
    if not record.store_dir:
        return None
    path = get_settings().output_path / record.store_dir / spec(kind).filename_for(lang)
    return path if path.exists() else None


def subtitle_file(
    record: ArticleRecord, kind: ProductionKind | str, lang: str = DEFAULT_LANGUAGE
) -> Path | None:
    """
    音频旁边的字幕 / The subtitle file next to one audio production.

    字幕是**音频那一格的产物**（`produce()` 写在音频同名 `.srt` 上，时间轴按每段
    波形的真实长度算），但界面上此前完全没提过它——于是「操作台按分段出字幕」
    这件事做了，人看不见，只能当成没做。
    Written all along, but nothing in the UI ever pointed at it.
    """
    audio = production_file(record, kind, lang)
    if audio is None:
        return None
    srt = audio.with_suffix(".srt")
    return srt if srt.exists() else None


def production_sidecar(
    record: ArticleRecord, kind: ProductionKind | str, lang: str = DEFAULT_LANGUAGE
) -> Path | None:
    """
    产物的 JSON 附件 / A production's JSON sidecar, when it has one.

    目前只有长文案有：按发言人切好的轮次，P6/P7 的 TTS 要用它分配音色。
    Only the long-form script has one today: the speaker turns the TTS stage needs.
    """
    name = json_sidecar(spec(kind), lang)
    if not name or not record.store_dir:
        return None
    path = get_settings().output_path / record.store_dir / name
    return path if path.exists() else None


def media_folders(record: ArticleRecord) -> dict[str, Path]:
    """
    素材子目录 / The media sub-directories that actually exist.

    只返回**真实存在且非空**的，界面才不会给出一个点开是空的按钮。
    Only directories that exist and hold something, so no button opens onto nothing.
    """
    directory = article_directory(record)
    if not directory:
        return {}

    result: dict[str, Path] = {}
    for label, name in (("配图", "images"), ("视频", "videos")):
        path = Path(directory) / name
        if path.is_dir() and any(path.iterdir()):
            result[label] = path
    return result


def body_file(record: ArticleRecord) -> Path | None:
    """
    正文文件 / The stored article body.

    **返回 None 表示没有可打开的东西**——抓取失败的文章目录里根本没有 `article.md`，
    界面据此不把那一格做成可点的。
    `None` means there is nothing to open: a failed fetch leaves no `article.md`, and the
    cell stays unclickable rather than opening onto an error.
    """
    directory = article_directory(record)
    if not directory:
        return None
    path = Path(directory) / "article.md"
    return path if path.is_file() else None


def media_target(record: ArticleRecord) -> Path | None:
    """
    媒体格该打开哪个目录 / Which directory the media cell opens.

    有图开 `images/`，否则有视频开 `videos/`，都没有返回 None。
    **配图优先**：绝大多数文章只有配图，视频是少数；而两者都有时人要看的
    通常是配图（它进图文版），视频还在展开面板里另有入口。
    Images win when both exist: they are what almost every article has and what the
    graphic edition uses, while videos keep their own entry in the detail panel.
    """
    folders = media_folders(record)
    return folders.get("配图") or folders.get("视频")


def over_target(
    record: ProductionRecord | None, kind: ProductionKind | str
) -> bool:
    """
    这一格的字数是否落在目标区间之外 / Whether this cell's length misses its window.

    **现算，不从库里读。** 台账里不存「是否合格」这个布尔值：改了 `profile.yaml`
    的字数窗口，历史产物应当跟着重新判定，而存下来的布尔值不会变——于是界面上
    显示的合格标准和下一次生成用的标准不是同一个。
    Computed rather than stored: editing the character window in `profile.yaml` must
    re-judge existing productions, whereas a stored boolean would not change and the
    table's notion of "acceptable" would drift from the generator's.

    窗口取自 `char_window()`，和生成时验收用的是同一个函数——否则会出现
    「生成时报合格、表格里标超长」。
    """
    from dna.core.config import safe_profile
    from dna.produce.tasks import char_window

    if record is None or not record.ok or not record.chars:
        return False
    window = char_window(kind, safe_profile())
    if window is None:
        return False
    low, high = window
    return not (low <= record.chars <= high)


def target_window(kind: ProductionKind | str) -> tuple[int, int] | None:
    """这类产物的字数区间 / The character window for one kind，供提示文案用。"""
    from dna.core.config import safe_profile
    from dna.produce.tasks import char_window

    return char_window(kind, safe_profile())


async def batch_refetch(
    article_ids: list[str],
    *,
    download_images: bool = True,
    download_videos: bool = True,
    on_step=None,
) -> str:
    """
    批量重新抓取 / Re-fetch a batch of articles.

    **逐篇过 `run.io_bound`，不是把整批丢进一个线程。** 一批十篇要跑好几分钟，
    整批一个线程的话进度条从头到尾不动，和卡死看起来一模一样；逐篇回来才能
    报「第 3/10 篇」。这也是 `audio_progress.py` 存在的同一个理由。
    One thread per article rather than one for the batch: a ten-article run takes minutes,
    and a progress indicator that never moves is indistinguishable from a hang.

    **单篇失败不中断其余。** 抓取失败的原因大多是这一个站点的问题
    （403、超时、改版），后面九篇没有理由跟着不抓。
    A single failure never aborts the rest: its cause is almost always specific to that
    one site.

    参数 / Args:
        on_step: `(已完成, 总数, 标题)` 回调，界面用它更新提示条

    返回 / Returns:
        一行汇总。媒体告警单独计数——它**不算失败**（正文已经入库）。
    """
    from dna.store import refetch_article

    ok = degraded = failed = 0
    warnings: list[tuple[str, str]] = []

    for index, article_id in enumerate(article_ids, start=1):
        detail = IntakeResult()

        def _work(aid: str = article_id, d: IntakeResult = detail) -> ArticleRecord | None:
            return refetch_article(
                aid,
                download_images=download_images,
                download_videos=download_videos,
                result=d,
            )

        try:
            updated = await run.io_bound(_work)
        except Exception as exc:  # noqa: BLE001 - 一篇的任何异常都不该中断整批
            failed += 1
            logger.warning("重抓失败 %s：%s", article_id[:8], exc)
            updated = None
        else:
            if updated is None:
                failed += 1
            elif str(updated.status) == str(FetchStatus.OK):
                ok += 1
            else:
                degraded += 1

        warnings.extend(detail.media_warnings)
        if on_step is not None:
            on_step(index, len(article_ids), (updated.title if updated else "") or article_id[:8])

    parts = [f"重抓 {len(article_ids)} 篇"]
    if ok:
        parts.append(f"成功 {ok}")
    if degraded:
        parts.append(f"降级 {degraded}（正文没抓到，可用 dna sync 手动补）")
    if failed:
        parts.append(f"失败 {failed}")
    if warnings:
        # **原因要带上第一条。** 只说「媒体未下载 2」等于没说：视频失败的原因
        # （地域限制、封禁下载、拿回来是错误页）决定了要不要人工去弄，
        # 而完整清单在文章目录的 `references.md` 里。
        parts.append(
            f"媒体未下载 {len(warnings)}（不影响正文）——{warnings[0][1]}"
            + ("；其余见文章目录的 references.md" if len(warnings) > 1 else "")
        )
    return "，".join(parts)


def plan_batch_delete(article_ids: list[str]):
    """
    算出这批会删掉什么 / Work out what a batch delete would remove.

    纯查询，给确认框用。**确认框上的数字必须来自真正要删的那批对象**，
    不能在界面里另数一遍——「我以为只选了一篇」是删除事故最常见的形态。
    Read-only, for the confirmation dialog. The numbers must come from the very objects
    about to go; counting them again in the view is how "I thought I only picked one"
    happens.
    """
    from dna.store import plan_delete

    return plan_delete(article_ids)


async def batch_delete(article_ids: list[str], *, remove_files: bool = True) -> str:
    """
    批量删除 / Delete a batch of articles.

    磁盘删除会等（几十个文件 + Windows 上的杀软扫描），所以照样走 io_bound。
    Disk removal blocks long enough to matter, so it goes off the event loop too.
    """
    from dna.store import delete_articles

    def _work():
        return delete_articles(article_ids, remove_files=remove_files)

    plan = await run.io_bound(_work)
    message = plan.result_summary()
    if plan.errors:
        # 失败原因原样带出来：Windows 上「目录被占用」要人去关掉资源管理器，
        # 概括成「删除失败」的话人不知道该做什么。
        message += "　·　" + "；".join(f"{i[:8]} {r}" for i, r in plan.errors[:3])
    return message


def open_in_file_manager(path: str | Path) -> str:
    """
    在系统文件管理器里打开文件或目录 / Reveal a file or directory in the OS file manager.

    返回空串表示成功，否则返回给人看的失败原因。
    Returns an empty string on success, or a human-readable reason.

    **接受文件，不只是目录。** Windows 下 `os.startfile` 对 `article.md` 会用默认
    编辑器打开它——「点正文栏打开对应的文件」要的正是这个行为。先前这里写着
    `is_dir()`，于是正文格只能开目录、开不了文件。
    Files are accepted, not only directories: `os.startfile` opens `article.md` in the
    default editor, which is exactly what clicking the body cell should do.

    **只在服务端与浏览器同机时有意义。** 工作台默认绑 127.0.0.1，两者本来就是同一台
    机器；一旦有人把它绑到 0.0.0.0 给别人访问，这个按钮会在**服务器**上弹出窗口，
    点的人什么也看不到。所以这里显式检查绑定地址，不是本机就拒绝并说明原因。
    Only meaningful when the server and browser are the same machine. The workbench binds
    to 127.0.0.1 by default, where they are; if someone rebinds it to 0.0.0.0 for others
    to reach, this would pop a window on the *server* and the clicker would see nothing.
    The bind address is therefore checked explicitly.
    """
    target = Path(path)
    if not target.exists():
        return f"路径不存在：{target}"

    if not _server_is_local():
        return "工作台没有绑定在本机，无法打开你这边的文件管理器"

    try:
        if sys.platform == "win32":
            _open_on_windows(target)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as exc:
        return f"打不开：{exc}"
    return ""


def _server_is_local() -> bool:
    """服务端是否绑在本机 / Whether the server is bound to loopback."""
    host = str(getattr(app, "config", None) and getattr(app.config, "host", "") or "")
    return host in {"", "127.0.0.1", "localhost", "::1"}


def _open_on_windows(target: Path) -> None:
    """
    Windows 下打开并尽量顶到最前 / Open on Windows and try to raise it to the front.

    为什么要额外做一步 / Why the extra step:
        抢到前台权的是浏览器，我们这个 Python 进程没有——Windows 于是拒绝激活
        资源管理器，只让它在任务栏闪一下，看起来就是「被浏览器盖住了」。
        `AllowSetForegroundWindow(ASFW_ANY)` 把这一次的前台权让出去，
        再由后面那个线程把窗口顶上来。
        The browser owns the foreground right, not this process, so Windows refuses to
        activate Explorer and only flashes it in the taskbar.

    **任何一步失败都退回原来的行为**：窗口照样开，只是可能在后面。
    提示「打不开」会是假的。
    """
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception as exc:  # noqa: BLE001 - 顶不上来不算失败
        logger.debug("放行前台权失败，窗口可能开在后面：%s", exc)

    if not target.is_dir():
        # 文件仍走 startfile：它按扩展名挑默认程序，
        # 「点正文格用编辑器打开 article.md」这条行为不能变成「打开所在目录」。
        # 路径来自本地台账，不是用户输入 / from the local ledger, not user input
        os.startfile(target)
        return

    subprocess.Popen(["explorer", os.path.normpath(str(target))])
    # 开窗要时间，不能同步等：这个函数跑在界面的事件循环上，等两秒就是卡两秒。
    threading.Thread(target=_raise_explorer, args=(target,), daemon=True).start()


def _raise_explorer(target: Path, *, timeout: float = 2.0) -> None:
    """把刚开的资源管理器窗口顶到最前 / Bring the new Explorer window to the front."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        wanted = {target.name.lower(), os.path.normpath(str(target)).lower()}
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _visit(hwnd, _lparam):  # pragma: no cover - 要真的有窗口才会进来
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value not in {"CabinetWClass", "ExploreWClass"}:
                return True
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            if title.value.lower() in wanted:
                found.append(hwnd)
                return False
            return True

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not found:
            user32.EnumWindows(_visit, 0)
            if found:
                break
            time.sleep(0.1)

        if not found:
            return
        hwnd = found[0]
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE，窗口可能是最小化状态
        if user32.SetForegroundWindow(hwnd):
            return
        # 还是不给：把自己的线程挂到前台线程的输入队列上再试一次，这是
        # Windows 唯一还认的一条路（前台窗口所属线程可以替别人做主）。
        target_tid = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        own_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if user32.AttachThreadInput(own_tid, target_tid, True):
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(own_tid, target_tid, False)
    except Exception:
        logger.debug("raise explorer failed", exc_info=True)


def longform_estimate(record: ArticleRecord) -> str:
    """
    长文案的时长预估 / The projected duration of a long-form script.

    走 `plan_target_seconds` 而不是在界面里另算一遍。先前这里写着
    `min(text_len * 1.2, 4000) / 4.5 / 60`——那是**已经废弃的按字符数推导**，
    确认框里报的分钟数和实际生成的对不上。
    Delegates to `plan_target_seconds` instead of recomputing. The dialog previously
    carried its own character-based formula, long since replaced in the core, so the
    minutes it quoted no longer matched what was produced.
    """
    from dna.core.config import safe_profile
    from dna.core.models import Article
    from dna.narration.longform import plan_target_seconds

    profile = safe_profile()
    low, high = profile.longform_duration_seconds
    stub = Article(url=record.url, title=record.title, text="字" * record.text_len)
    seconds = plan_target_seconds(stub, low=low, high=high)
    return f"{seconds / 60:.0f} 分钟"




# ---------------------------------------------------------------------------
# 订阅采集 / collecting from the configured sources
# ---------------------------------------------------------------------------


def source_options() -> dict[str, str]:
    """
    订阅源下拉选项 / The subscription dropdown options.

    从 `load_sources()` 来，**不是** `Ledger.count_by_source()`——后者是台账里出现过的源，
    里面有已经停用的死源；这里要的是配置里当前启用的源，包括**一次还没抓过的新源**。
    筛选栏那个下拉正好相反，它问的是「已经抓到的东西里挑哪些看」。
    From `load_sources()` rather than the ledger: the ledger lists sources that have
    appeared before, including disabled dead ones, while this needs the currently enabled
    ones — including a source added today that has never run. The filter bar's dropdown
    asks the opposite question.
    """
    from dna.core.config import load_sources

    try:
        sources = load_sources()
    except Exception as exc:  # noqa: BLE001 - sources.yaml 坏了不该让整个对话框打不开
        logger.warning("读取订阅源失败：%s", exc)
        return {}
    return {s.id: (s.name or s.id) for s in sources}


async def import_from_sources(
    source_ids: list[str] | None = None,
    *,
    max_age_days: int | None = 3,
    limit_per_source: int | None = 10,
    download_images: bool = True,
    download_videos: bool = True,
    refetch: bool = False,
    progress: dict | None = None,
) -> str:
    """
    从订阅源采集并入库 / Collect from the sources and ingest.

    **不调用 LLM，不产生费用。** 与 `dna fetch` 调的是同一个 `intake_sources`——
    命令行抓的和界面抓的必须是同一批东西，否则「昨天命令行抓到了、今天界面抓不到」
    这类问题永远查不清是配置差异还是代码差异。
    Calls no LLM. Delegates to the same `intake_sources` as `dna fetch`, so the two
    front-ends cannot collect differently.

    `progress` 与 `run_production` 是同一个做法：**共享字典由工作线程写、UI 定时读**。
    整趟采集在一个 `run.io_bound` 里，回调落在工作线程上，而跨线程改 NiceGUI
    元素不安全。
    The same shared-dict idiom as `run_production`, for the same cross-thread reason.
    """
    from dna.store import intake_sources

    def _work() -> str:
        result = intake_sources(
            source_ids=source_ids or None,
            limit_per_source=limit_per_source,
            download_images=download_images,
            download_videos=download_videos,
            refetch=refetch,
            max_age_days=max_age_days,
            on_progress=_progress_writer(progress),
        )
        message = result.summary()
        # 源级失败单独报：一个 feed 挂了而其余正常时，总数会显得「今天新闻很少」,
        # 不点出来的话人会以为是天数选窄了。
        # Reported separately: with one dead feed the totals just look like a slow news
        # day, and the user would blame the day-count instead.
        if result.source_failures:
            names = "、".join(sid for sid, _ in result.source_failures[:3])
            message += f"　·　{len(result.source_failures)} 个源失败（{names}）"
        if result.media_warnings:
            # 同上：带上第一条原因，否则「媒体未下载 N」等于没提示
            message += (
                f"　·　媒体未下载 {len(result.media_warnings)}（不影响正文）"
                f"——{result.media_warnings[0][1]}"
            )
        return message

    return await run.io_bound(_work)


# ---------------------------------------------------------------------------
# 设置面板 / the settings panel
# ---------------------------------------------------------------------------


@dataclass
class EnvField:
    """
    设置面板里的一个 `.env` 项 / One `.env` entry in the settings panel.

    界面画它、`config_edit` 写它，两边都不需要知道另一边。
    """

    key: str
    group: str
    label: str
    help: str = ""
    options: tuple[str, ...] = ()
    """非空时画下拉（仍可自填）/ non-empty renders a dropdown that still accepts free text."""

    boolean: bool = False
    secret: bool = False

    page: str = "运行设置"
    """哪个子页 / which settings tab；`group` 仍是页内的小标题。"""

    needs_mode: str = ""
    """
    只在 `TTS_MODE` 等于这个值时才生效 / only meaningful in this `TTS_MODE`.

    服务端把「同时给 ref_audio 和音色名」当成互相冲突的参数**直接报错**，
    所以界面按当前模式把无关的那几项灰掉，而不是让人配好了才发现两者不能共存。
    """

    @property
    def field(self) -> str:
        """对应的 `Settings` 字段名 / the matching `Settings` attribute."""
        return self.key.lower()


# 分组与文案。**顺序就是界面上的顺序**，最常改的放最前面。
# 这张表只描述「怎么画」，能不能写由 `ENV_ALLOWLIST` 说了算——
# 少写一项这里的测试会报，多写一项 `save_env` 会拒。
# This table says how to draw; whether a key may be written is `ENV_ALLOWLIST`'s call.
ENV_FIELDS: tuple[EnvField, ...] = (
    EnvField("LLM_PROVIDER", "LLM", "主用提供方", options=("deepseek", "openrouter")),
    EnvField(
        "DEEPSEEK_MODEL", "LLM", "DeepSeek 模型",
        # 上一轮排查「GUI 卡住」的根因就是这一行被换成了推理模型，而界面上完全看不出来。
        # 推理模型每次要先想几百到几千个 token 才开始输出，慢 30~50 倍——
        # 表现和死循环一模一样。
        # A past "the GUI is frozen" investigation ended here: a reasoning model had been
        # selected, which is 30-50x slower and indistinguishable from a hang.
        help="deepseek-chat 是会话模型（日常用这个）；带 reasoner/thinking 的是推理模型，慢 30~50 倍，界面会像卡死",
        options=("deepseek-chat", "deepseek-reasoner"),
    ),
    EnvField("DEEPSEEK_BASE_URL", "LLM", "DeepSeek 接口地址"),
    EnvField("LLM_FALLBACK_PROVIDER", "LLM", "备用提供方", options=("", "openrouter", "deepseek")),
    EnvField("OPENROUTER_MODEL", "LLM", "OpenRouter 模型"),
    EnvField(
        "LLM_CACHE_ENABLED", "LLM", "启用响应缓存", boolean=True,
        help="关掉之后每次生成都真花钱；重做同一篇也不再免费",
    ),
    EnvField(
        "DEEPSEEK_API_KEY", "密钥", "DeepSeek API Key", secret=True,
        help="留空表示不修改。只显示前 7 位与长度——界面会被截图",
    ),
    EnvField("OPENROUTER_API_KEY", "密钥", "OpenRouter API Key", secret=True, help="留空表示不修改"),
    # --- TTS 子页 / the TTS tab ---
    EnvField("TTS_SERVICE_URL", "服务", "TTS 服务地址", page="TTS"),
    EnvField(
        "TTS_MODULE_DIR", "服务", "TTS 模块目录", page="TTS",
        help="留空表示用内置默认位置；自动拉起服务时从这里启动",
    ),
    EnvField(
        "TTS_PYTHON", "服务", "TTS 用的 Python", page="TTS",
        help="留空表示用当前解释器。TTS 常装在自己的环境里，两边依赖不一样",
    ),
    EnvField(
        "TTS_AUTOSTART", "服务", "服务没起来时自动拉起", boolean=True, page="TTS",
        help="关掉之后合成会直接失败并提示，而不是等模型冷启动",
    ),
    EnvField(
        "TTS_START_TIMEOUT", "服务", "启动等待上限（秒）", page="TTS",
        help="模型冷启动要一两分钟；调太小会在快好的时候放弃",
    ),
    EnvField(
        "TTS_REQUEST_TIMEOUT", "服务", "单次请求上限（秒）", page="TTS",
        help="本地合成 RTF≈2.5，长文案一段就要几分钟——这个值宁大勿小",
    ),
    EnvField(
        "TTS_MODE", "音色", "合成方式", options=("voice_clone", "custom_voice"), page="TTS",
        help="voice_clone 用参考音频克隆；custom_voice 用服务端的音色名。**两者不能同时给**",
    ),
    EnvField(
        "TTS_VOICE_HOST", "音色", "主播音色名", page="TTS", needs_mode="custom_voice",
        help="服务在线时下拉里就是服务端的音色表；手打一个不存在的名字，"
             "要等几分钟的合成跑完才会报错",
    ),
    EnvField(
        "TTS_VOICE_GUEST", "音色", "嘉宾音色名（双人稿）", page="TTS", needs_mode="custom_voice",
        help="只有长文案的访谈体用得上",
    ),
    EnvField(
        "TTS_REF_AUDIO", "参考音频", "主播参考音频", page="TTS", needs_mode="voice_clone",
        help="相对路径按**数据目录**解析（不是仓库根）",
    ),
    EnvField(
        "TTS_REF_TEXT", "参考音频", "主播参考音频的原话", page="TTS", needs_mode="voice_clone",
        help="留空则走纯 x-vector；随便编一句会让克隆质量明显变差",
    ),
    EnvField(
        "TTS_REF_AUDIO_GUEST", "参考音频", "嘉宾参考音频", page="TTS", needs_mode="voice_clone",
    ),
    EnvField(
        "TTS_REF_TEXT_GUEST", "参考音频", "嘉宾参考音频的原话", page="TTS",
        needs_mode="voice_clone",
    ),
    EnvField("TTS_PREPROCESS", "产物", "合成前做文本预处理", boolean=True, page="TTS"),
    EnvField(
        "TTS_SUBTITLES", "产物", "同时导出字幕", boolean=True, page="TTS",
        help="按段落时间轴出 srt，剪辑时省一遍对轴",
    ),
    EnvField(
        "TTS_ARTIFACT_DIRNAME", "产物", "音频落盘子目录名", page="TTS",
        help="在文章目录下，默认 tts",
    ),
    EnvField("HTTP_PROXY", "网络", "HTTP 代理"),
    EnvField("HTTPS_PROXY", "网络", "HTTPS 代理"),
    EnvField(
        "NO_PROXY", "网络", "不走代理的地址",
        # 少了 localhost，本机的 TTS 服务与 RSSHub 会被路由到公司代理然后失败。
        help="**必须包含 localhost 与 127.0.0.1**，否则本机的 TTS 服务和 RSSHub 会被送进代理",
    ),
    EnvField("DEFAULT_LANGUAGE", "运行", "默认语言", options=("zh", "en")),
    EnvField("MAX_ITEMS_PER_SOURCE", "运行", "每个源最多取几条"),
    EnvField("LOG_LEVEL", "运行", "日志级别", options=("DEBUG", "INFO", "WARNING", "ERROR")),
)


def env_groups(page: str = "运行设置") -> list[tuple[str, list[EnvField]]]:
    """
    某个子页的字段，按小组归拢 / One tab's fields, grouped, in declaration order.

    分页之后**每个字段有且只有一个归属**：同一项画在两页上，人会在另一页看到
    自己刚改过的旧值，然后不知道哪一份才算数。
    """
    groups: dict[str, list[EnvField]] = {}
    for field in ENV_FIELDS:
        if field.page == page:
            groups.setdefault(field.group, []).append(field)
    return list(groups.items())


def env_display(field: EnvField) -> str:
    """
    这一项现在显示什么 / What this field shows right now.

    密钥走 `mask_secret`，其余读 `Settings` 的**生效值**而不是 `.env` 的文本——
    被系统环境变量盖住时，文件里写的那个值根本不是程序在用的那个。
    Secrets are masked; everything else shows the *effective* value from `Settings` rather
    than the file's text, because an OS environment variable may be overriding it.
    """
    from dna.core.config_edit import mask_secret

    value = getattr(get_settings(), field.field, "")
    if value is None:
        return ""
    text = str(getattr(value, "value", value))  # Language 这类枚举取 .value
    return mask_secret(text) if field.secret else text


def env_shadowed(field: EnvField) -> bool:
    """这一项是否被系统环境变量盖住 / Whether an OS variable overrides it."""
    from dna.core.config_edit import shadowed_by_env

    return shadowed_by_env(field.key)


def tts_voice_options() -> list[str]:
    """服务端的音色表 / The service's voice list（离线返回空表）。"""
    from dna.tts.console import available_voices

    return available_voices()


def ref_audio_options() -> list[str]:
    """可选的参考音频 / The reference-audio candidates，相对路径。"""
    from dna.tts.console import ref_audio_choices

    return ref_audio_choices()


def ref_audio_file(raw: str) -> Path | None:
    """某个参考音频设置解析到的文件 / The file a reference-audio setting resolves to。"""
    from dna.tts.console import resolve_ref_audio

    return resolve_ref_audio(raw)


def profile_values() -> dict:
    """当前的内容偏好 / The current content preferences, as a plain dict."""
    from dna.core.config import safe_profile

    return safe_profile().model_dump(mode="json")


def save_settings(
    profile_updates: dict | None = None,
    env_updates: dict[str, str] | None = None,
) -> str:
    """
    保存设置 / Persist the settings.

    抛出 / Raises:
        ConfigError: 校验失败或配置项不存在。**上层直接把消息显示出来**——
                     pydantic 的报错已经指明是哪个字段哪里不对，改写一遍只会变模糊。

    返回一句人话，写明改了几项、哪些**需要重启**才生效。
    Returns a sentence naming what changed and what needs a restart.
    """
    from dna.core.config_edit import save_env, save_profile

    parts: list[str] = []
    if profile_updates:
        save_profile(profile_updates)
        parts.append(f"内容偏好 {len(profile_updates)} 项")

    written: list[str] = []
    if env_updates:
        written = save_env(env_updates)
        if written:
            parts.append(f"运行设置 {len(written)} 项")

    if not parts:
        return "没有改动"

    message = "已保存：" + "、".join(parts)
    # 代理与日志级别在进程启动时就被读走了，改完这次会话不会变——
    # 不说清楚的话人会以为没保存成功，然后反复点保存。
    # Proxies and the log level are read at process start; without saying so the user
    # assumes the save failed and clicks again.
    restart = [k for k in written if k in {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "LOG_LEVEL"}]
    if restart:
        message += f"　·　{'、'.join(restart)} 要重启 dna gui 才生效"
    if profile_updates:
        message += "　·　字数窗口即时生效，表格里的超长标记会跟着重判"
    return message


def cache_status() -> str:
    """
    LLM 缓存命中情况 / The LLM cache hit rate.

    常驻在顶部：**费用要一直看得见**。看不见的成本最容易失控——
    界面上按钮很多，不显示的话没人知道这一小时点掉了多少钱。
    Pinned to the top because cost must stay visible. Invisible cost is the kind that
    runs away: there are many buttons here, and without this nobody would know what an
    hour of clicking added up to.
    """
    llm = get_llm()
    return llm.stats() if hasattr(llm, "stats") else "LLM 缓存：未启用"




__all__ = [
    "ENV_FIELDS",
    "EnvField",
    "RowView",
    "article_directory",
    "audio_estimate_seconds",
    "audio_for",
    "batch_delete",
    "batch_refetch",
    "body_file",
    "cache_status",
    "env_display",
    "env_groups",
    "env_shadowed",
    "import_from_sources",
    "import_links",
    "last_instructions",
    "load_rows",
    "longform_estimate",
    "media_folders",
    "media_target",
    "open_in_file_manager",
    "over_target",
    "plan_batch_delete",
    "preview_links",
    "production_file",
    "production_sidecar",
    "production_text",
    "profile_values",
    "run_production",
    "save_settings",
    "source_options",
    "target_window",
]
