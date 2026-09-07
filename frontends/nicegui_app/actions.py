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
from dataclasses import dataclass
from pathlib import Path

from nicegui import app, run

from dna.core.config import get_settings
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
from dna.store import FetchStatus, Ledger, ProductionRecord, intake_urls
from dna.store.ledger import ArticleRecord
from dna.tts.base import TTSError
from dna.tts.factory import get_tts


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


def load_rows(
    *,
    status: str | None = None,
    source: str | None = None,
    search: str | None = None,
    limit: int = 50,
    only_gaps: bool = False,
    only_new: bool = False,
) -> list[RowView]:
    """
    读取表格数据 / Load the table data.

    **产物状态一次查完**（`production_matrix`），不是每行每格查一次——
    一页 50 行 × 5 种产物就是 250 次查询，界面会肉眼可见地卡。
    Production status is fetched in a single query rather than per cell: fifty rows times
    five kinds would be 250 round trips and the lag would be visible.
    """
    settings = get_settings()
    ledger = Ledger(settings.db_file)

    status_filter = FetchStatus(status) if status else None
    records = ledger.list(
        status=status_filter, source_id=source, search=search, limit=limit
    )
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

    return rows


async def run_production(
    article_id: str,
    kind: ProductionKind | str,
    *,
    lang: str = DEFAULT_LANGUAGE,
    variant: str | None = None,
    force: bool = False,
    instructions: str = "",
    progress: dict | None = None,
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


async def import_links(
    text: str,
    *,
    download_images: bool = True,
    download_videos: bool = True,
    max_images: int | None = None,
    refetch: bool = False,
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
class TTSHandoff:
    """
    一次「交给 TTS 界面精修」/ One hand-off to the TTS workbench.

    界面拿着它做两件事：打开那个 URL，然后**轮询**同一个 token 等产物。
    不用回调（TTS 那边可能在另一台机器上，也不该知道工作台的地址）。
    Polled rather than called back: the service may be on another machine and should not
    need to know this application's address.
    """

    token: str
    gui_url: str
    article_id: str
    kind: str
    lang: str
    segments: int = 0


async def open_tts_workbench(
    article_id: str,
    kind: ProductionKind | str,
    *,
    lang: str = DEFAULT_LANGUAGE,
    title: str = "",
    return_url: str = "",
) -> TTSHandoff:
    """
    把这一格的稿子交给 TTS 图形界面 / Hand this cell's script to the TTS workbench.

    做四件事（都在工作线程里，因为都可能等）/ Four steps, all off the event loop:
        1. 确认 TTS 服务在线（不在线自动拉起，三次失败报不可用）
        2. 确认 TTS 界面在线（它是另一个进程、另一个端口）
        3. 把稿子切成分段 —— **和自动合成切得一模一样**（同一份 `_build_segments`），
           所以在界面里调完的东西，和这边跑出来的是同一批分段
        4. POST 交接单（带上 `return_url`），拿回一个直接能打开的 URL

    `return_url` 是**本页面的地址**：TTS 界面生成完会自己跳回来（关不掉标签页时
    才退回跳转）。不带的话人得自己找回原来那个标签页。
    The caller's own URL, so the TTS workbench can come back when it is done.

    抛出 / Raises:
        TTSError: 服务或界面拉不起来；调用方原样显示（那句话里已经写了怎么排查）
    """
    from dna.produce.service import speech_segments_for
    from dna.tts.factory import voice_for_role
    from dna.tts.supervisor import ensure_gui, ensure_service

    def _work() -> TTSHandoff:
        settings = get_settings()
        provider = get_tts(settings)
        ensure_service(settings, client=provider.client)
        gui = ensure_gui(settings)

        pieces = [s.text for s in speech_segments_for(article_id, kind, lang=lang)]
        if not pieces:
            raise TTSError("这一格还没有可朗读的稿子")

        # 只在用内置音色时把音色名带过去 / carry the voice name only for built-ins
        #
        # 克隆模式下 `speaker` 是一个**本地文件路径**，不是音色名 —— 带过去 TTS 界面
        # 只会得到一个「没有这个音色」。参考音频要在那边的上传框里选，
        # 那个控件本来就在（这也正是「高级配置」存在的理由）。
        # In clone mode the speaker field holds a file path, not a name.
        host = voice_for_role("host", settings, lang=lang)
        body = provider.client.handoff(
            pieces,
            title=title,
            voice=host.speaker if host.mode == "custom_voice" else None,
            return_url=return_url,
            meta={"article_id": article_id, "kind": str(kind), "lang": lang},
        )
        # 交接单里的 gui_url 用的是**服务端**配置的界面地址；本项目自己的
        # TTS_GUI_URL 才是这台机器上打得开的那个，两者不一致时以后者为准。
        # The record's URL comes from the service's own config; this project's setting is
        # the one the browser here can actually reach.
        url = f"{gui.rstrip('/')}/?import={body['token']}"
        return TTSHandoff(token=body["token"], gui_url=url, article_id=article_id,
                          kind=str(kind), lang=lang, segments=len(pieces))

    return await run.io_bound(_work)


async def collect_tts_handoff(handoff: TTSHandoff) -> dict | None:
    """
    看一眼交接单 / Take one look at the hand-off record.

    **不判断完成与否**，原样把记录交出去：里面既有 `status`，也有 TTS 界面写回的
    `done` / `total` —— 等待动效要靠后两个数字动起来，在这里过滤掉就只剩一个
    「还在等」，和卡死看起来一样。
    Returned unfiltered: the progress counts drive the waiting animation.

    返回 `None` 只表示**这次没读到**（服务重启、交接单过期），不是「没完成」。
    """
    def _work() -> dict | None:
        return get_tts().client.handoff_state(handoff.token)

    try:
        return await run.io_bound(_work)
    except TTSError:
        return None


async def import_tts_handoff(handoff: TTSHandoff, record: dict) -> ProduceResult:
    """
    把 TTS 界面的产物收进本项目 / Adopt what the workbench produced.

    产物**全部**拷进文章目录（逐段 wav、合并音频、字幕），然后把整条音频
    记进台账 —— 记了那一格才会变成「已生成」，否则界面上刚忙完的活看起来像丢了。
    Everything is copied next to the article and the merged track is recorded, or the
    cell would still read empty.

    整条音频认哪一个 / Which file becomes the production:
        优先 `merged.wav`（多段合并的成品）；没有就取**唯一/最后一个** wav。
        猜错的代价很直接，所以规则写死，不做"最大文件"这类启发式。
    """
    from dna.produce.service import import_audio

    def _work() -> ProduceResult:
        settings = get_settings()
        ledger = Ledger(settings.db_file)
        article = ledger.get(handoff.article_id)
        if article is None or not article.store_dir:
            return ProduceResult(kind=spec(handoff.kind).kind, ok=False,
                                 error="台账里找不到这篇文章")

        # 一篇文章一个文件夹，**同名覆盖**：不按 run 或时间再分层，
        # 否则「这篇的音频是哪一份」要靠人比时间戳。
        # One folder per article, overwritten in place.
        target = (settings.output_path / article.store_dir
                  / (settings.tts_artifact_dirname or "tts"))
        client = get_tts(settings).client

        saved: list[Path] = []
        for relative in record.get("files", []):
            try:
                saved.append(client.download(relative, target / Path(relative).name))
            except TTSError:
                continue
        if not saved:
            return ProduceResult(kind=spec(handoff.kind).kind, ok=False,
                                 error="产物一个都没取回来（服务可能已重启）")

        wavs = [p for p in saved if p.suffix.lower() == ".wav"]
        merged = next((p for p in wavs if p.name == "merged.wav"), wavs[-1] if wavs else None)
        if merged is None:
            return ProduceResult(kind=spec(handoff.kind).kind, ok=False,
                                 error="产物里没有 wav")

        # 附件一起交给 import_audio：它会把 .srt 放到音频旁边（同名同目录），
        # 于是产物形状和流水线合成出来的完全一致。
        return import_audio(handoff.article_id, handoff.kind, merged,
                            lang=handoff.lang, extras=saved,
                            source="tts_gui", settings=settings)

    return await run.io_bound(_work)


def tts_service_label() -> str:
    """顶栏上的 TTS 服务状态 / The TTS service's state for the header."""
    settings = get_settings()
    online = get_tts(settings).client.health()
    return (f"TTS {'在线' if online else '未运行'}　{settings.tts_service_url}"
            + ("" if online else "（合成时自动拉起）"))


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


def open_in_file_manager(path: str | Path) -> str:
    """
    在系统文件管理器里打开目录 / Reveal a directory in the OS file manager.

    返回空串表示成功，否则返回给人看的失败原因。
    Returns an empty string on success, or a human-readable reason.

    **只在服务端与浏览器同机时有意义。** 工作台默认绑 127.0.0.1，两者本来就是同一台
    机器；一旦有人把它绑到 0.0.0.0 给别人访问，这个按钮会在**服务器**上弹出窗口，
    点的人什么也看不到。所以这里显式检查绑定地址，不是本机就拒绝并说明原因。
    Only meaningful when the server and browser are the same machine. The workbench binds
    to 127.0.0.1 by default, where they are; if someone rebinds it to 0.0.0.0 for others
    to reach, this would pop a window on the *server* and the clicker would see nothing.
    The bind address is therefore checked explicitly.
    """
    target = Path(path)
    if not target.is_dir():
        return f"目录不存在：{target}"

    if not _server_is_local():
        return "工作台没有绑定在本机，无法打开你这边的文件管理器"

    try:
        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606 - 路径来自本地台账，非用户输入
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])  # noqa: S603,S607
        else:
            subprocess.Popen(["xdg-open", str(target)])  # noqa: S603,S607
    except OSError as exc:
        return f"打不开：{exc}"
    return ""


def _server_is_local() -> bool:
    """服务端是否绑在本机 / Whether the server is bound to loopback."""
    host = str(getattr(app, "config", None) and getattr(app.config, "host", "") or "")
    return host in {"", "127.0.0.1", "localhost", "::1"}


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
    "RowView",
    "TTSHandoff",
    "collect_tts_handoff",
    "import_tts_handoff",
    "open_tts_workbench",
    "tts_service_label",
    "article_directory",
    "audio_estimate_seconds",
    "audio_for",
    "last_instructions",
    "cache_status",
    "import_links",
    "preview_links",
    "load_rows",
    "longform_estimate",
    "media_folders",
    "open_in_file_manager",
    "production_file",
    "production_sidecar",
    "production_text",
    "run_production",
]
