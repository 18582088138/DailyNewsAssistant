"""
单篇产物生成 / Per-article production service.

CLI 的 `dna produce` 与 GUI 的重做按钮**都调这里**——业务逻辑只有一份，
两个前端不会各写一套、各错一套。
Both `dna produce` and the workbench's redo buttons call into this, so the logic exists
once and the two front-ends cannot drift apart or break differently.

一次生成做的事 / What one run does:
    查前置 → 取文章 → 调生成器 → 写文件 → 记 productions 表

文本产物调 LLM（花钱），音频产物调 TTS（花时间）/ Two kinds of cost:
    两者走**同一条路**——同样的「已存在不重跑」、同样的失败入账、同样的逐格重做。
    差别只在生成器和护栏的理由：一个防账单，一个防「点一下机器就没法用了」。
    Both take the same path with the same guards; only the generator and the reason for
    the guard differ — one protects the bill, the other protects the machine.

三条保护 / Three guards:
    1. **已有产物且未指定 force 时直接返回**，不调 LLM。
       GUI 里按钮就在手边，误触一次就是一次计费；默认不重复花钱。
       An existing production is returned as-is unless forced: the buttons sit right
       there, and a mis-click must not cost money.
    2. **前置缺失时先自动补**（英文总结依赖中文总结），而不是报错让人手动跑一遍。
       A missing prerequisite is produced first rather than raising and making the user
       run it by hand.
    3. **失败也记一行**，状态为 failed 且带错误原因。不记的话表格里显示「未生成」，
       人会以为没跑过，于是再点一次、再失败一次。
       Failures are recorded too. Without a row the table shows "not generated", the
       user assumes it never ran, clicks again and fails again.

重做必须绕开 LLM 缓存 / A redo must bypass the response cache:
    响应缓存**故意不设过期**，键是「provider + 模型 + 完整提示词」——同样的输入
    永远给同样的输出，这正是它的价值。但「重做」的字面意思就是**要一个不一样的**，
    照常走缓存的话，模型确实被调了、文件确实被重写了，内容却一字未变——
    看起来就像「生成了但没保存」。所以 `force=True` 时 provider 不带缓存。
    The cache deliberately never expires and keys on the prompt, which is exactly what
    makes it valuable. But "redo" means "give me a different one": served from cache, the
    call happens, the file is rewritten, and not one character changes — which reads as
    "it generated but did not save". `force=True` therefore builds an uncached provider.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dna.core.config import Profile, Settings, get_settings, safe_profile
from dna.core.logging import get_logger
from dna.core.models import Article, Cluster, NewsItem
from dna.llm.base import LLMProvider
from dna.llm.factory import get_llm
from dna.narration.longform import LongformMode, build_longform, can_build_longform
from dna.narration.script_builder import build_narration, build_short_video
from dna.pipeline.source import load_candidates
from dna.produce.documents import (
    front_matter,
    longform_turns,
    script_block,
    spoken_text,
    strip_front_matter,
)
from dna.produce.tasks import (
    DEFAULT_LANGUAGE,
    ProductionKind,
    TaskSpec,
    batch_kinds,
    json_sidecar,
    normalize_lang,
    prerequisite,
    spec,
)
from dna.store.ledger import ArticleRecord, Ledger, ProductionRecord
from dna.tts.base import ProgressFn, SpeechSegment, TTSProvider, wav_seconds
from dna.tts.factory import get_tts, voice_for_role
from dna.tts.preprocess import prepare_for_speech
from dna.tts.segment import split_for_speech
from dna.tts.subtitle import write_srt

logger = get_logger("produce.service")

# 算「人工投递」的来源 / the sources that count as manually submitted
#
# 界面上粘的链接与飞书投进来的，都是**人特意挑出来要处理的**；RSS 抓来的是候选池。
# NEW 标识区分的正是这两者，见 `is_new_article`。
# A pasted or messaged link was deliberately chosen; the RSS feed is a candidate pool.
MANUAL_SOURCES = frozenset({"gui", "inbox"})


@dataclass
class ProduceResult:
    """
    一次生成的结果 / The outcome of one production run.

    `skipped` 与 `ok` 是两回事：跳过表示「已有产物，没花钱」，
    成功表示「刚生成，花了钱」。GUI 要能把这两种情况区分显示。
    `skipped` and `ok` are different: skipped means an existing production was reused at
    no cost, ok means it was just generated and billed. The workbench distinguishes them.
    """

    kind: ProductionKind
    ok: bool
    skipped: bool = False
    path: Path | None = None
    chars: int = 0
    seconds: float | None = None
    calls: int = 0
    error: str = ""
    within_target: bool = True

    def summary(self) -> str:
        """一行摘要 / A one-line summary."""
        label = spec(self.kind).label
        if self.skipped:
            return f"{label}：已存在，跳过（未调用 LLM）"
        if not self.ok:
            return f"{label}：失败 —— {self.error}"

        parts = [f"{self.chars} 字"]
        if self.seconds is not None:
            parts.append(f"约 {self.seconds:.0f} 秒")
            if not self.within_target:
                parts.append("⚠️ 超出目标区间")
        # 音频不调 LLM，报「0 次调用」只会让人以为出了问题
        # Audio makes no LLM calls; reporting zero of them would read as a fault.
        if self.calls:
            parts.append(f"{self.calls} 次调用")
        return f"{label}：{'，'.join(parts)}"


@dataclass
class Generated:
    """
    生成器的产出 / What a generator hands back.

    文本产物填 `text`，音频产物填 `audio`，**两者互斥**。
    用一个结构而不是一串元组：加一种载荷（将来的图片、字幕）时，
    改一个字段而不是改所有调用点的解包。
    Text kinds fill `text`, audio kinds fill `audio`. A record rather than a tuple, so a
    future payload kind adds a field instead of breaking every unpacking site.
    """

    text: str = ""
    audio: bytes | None = None
    chars: int = 0
    """
    记进台账的字数：**正文本身**，不含抬头与主副标题；音频产物是被朗读的字数。

    不能填 `len(text)`：抬头带着标题和整条 URL，一篇 250 字的短视频稿会显示成
    454 字，与 `seconds`（只量正文）和文件里那行「约 N 秒 · M 字」互相矛盾——
    三个数字说的不是同一件事，人只会以为是哪里算错了。
    Never the whole document: the header carries the title and a full URL, so a 250-word
    script reports as 454 and contradicts both `seconds` and the file's own count.
    """
    seconds: float | None = None
    """文本产物是估算时长，音频产物是**真实时长**——音频存在之后没必要再估。"""
    calls: int = 0
    within_target: bool = True
    sidecar: dict | None = None

    cues: list[tuple[float, float, str]] = field(default_factory=list)
    """音频产物的字幕时间轴 / the subtitle timeline of an audio production."""


def produce(
    article_id: str,
    kind: ProductionKind | str,
    *,
    lang: str = DEFAULT_LANGUAGE,
    variant: str | None = None,
    force: bool = False,
    instructions: str = "",
    settings: Settings | None = None,
    profile: Profile | None = None,
    llm: LLMProvider | None = None,
    tts: TTSProvider | None = None,
    on_progress: ProgressFn | None = None,
) -> ProduceResult:
    """
    为一篇文章生成一种产物 / Produce one kind for one article.

    参数 / Args:
        lang:        输出语言，`zh` 或 `en`。语言是产物的一个维度，不是另一种产物
        variant:     长文案的形式，`feature`（专题）或 `interview`（访谈）
        force:       已有产物时是否重做。**默认不重做，也就不花钱**
        instructions: 这一次的额外要求（「加长到 40 秒」「用词再专业一点」）。
                     它会接在提示词末尾并记进台账，下次重做能看到上次改了什么
        profile:     时长区间与结尾引导语来自这里；不给则读 profile.yaml
        llm:         注入 provider；不给则按 .env 构造（测试一律注入假的）
        tts:         音频产物的后端；不给则按 .env 构造
        on_progress: 音频合成的进度回调 `(已完成段, 总段, 已产出秒数)`。
                     长文案音频要跑半小时，没有进度就只能看着界面发呆
    """
    s = settings or get_settings()
    prof = profile or safe_profile()
    task = spec(kind)
    lang = normalize_lang(lang)
    ledger = Ledger(s.db_file)

    record = ledger.get(article_id)
    if record is None:
        return ProduceResult(kind=task.kind, ok=False, error=f"台账里没有这篇文章：{article_id}")
    if not record.store_dir:
        return ProduceResult(kind=task.kind, ok=False, error="这篇文章还没有落盘目录")

    directory = s.output_path / record.store_dir
    output = directory / task.filename_for(lang)

    existing = ledger.latest_production(article_id, str(task.kind), lang)
    if not force and existing is not None and existing.ok and output.exists():
        return ProduceResult(
            kind=task.kind,
            ok=True,
            skipped=True,
            path=output,
            chars=existing.chars,
            seconds=existing.est_seconds,
        )

    article = _load_article(article_id, s)
    if article is None:
        return ProduceResult(kind=task.kind, ok=False, error="读不到这篇文章的正文")

    if task.min_body_chars and len(article.text) < task.min_body_chars:
        return ProduceResult(
            kind=task.kind,
            ok=False,
            error=f"正文只有 {len(article.text)} 字，不足 {task.min_body_chars} 字，本篇不适合生成{task.label}",
        )

    # 音频产物不在这里构造 LLM / audio does not build the LLM here
    #
    # 无条件 `get_llm()` 的话，只想合成一段音频也会先要求 API key，而
    # `TTS_PREPROCESS=false` 的机器根本不需要 key。所以朗读友好化那一次调用由
    # `prepare_for_speech` 自己按需构造，失败就退回原文（见 `tts/preprocess.py`）。
    #
    # 注意边界：**TTS service 从不调用 LLM**（它是纯 TTS）；那一次调用属于本项目，
    # 是把文案交出去之前的准备。合成本身仍然记 `calls=0`。
    # The service never calls an LLM; that one call belongs to this project, as
    # preparation before handing the copy over. Synthesis itself still bills nothing.
    is_audio = task.audio_of is not None
    provider = None if is_audio else (llm or get_llm(cache=not force))
    speaker = (tts or get_tts(s)) if is_audio else None
    generator = speaker.info if is_audio else provider.info

    # 前置缺失时先补上——报错让人手动跑一遍是没必要的摩擦
    #
    # **前置一律不 force**：重做音频不该顺手把稿子也重新调一遍 LLM。
    # 下游重做是免费的，上游重做是要花钱的，让一个动作同时触发两者是危险的默认值。
    # Prerequisites are never forced: redoing the audio must not re-bill the script.
    # The downstream redo is free while the upstream one costs money, and letting one
    # click trigger both is a dangerous default.
    need = prerequisite(task.kind, lang)
    if need is not None:
        need_kind, need_lang = need
        upstream_record = ledger.latest_production(article_id, str(need_kind), need_lang)
        if upstream_record is None or not upstream_record.ok:
            logger.info("先补前置产物：%s / %s", spec(need_kind).label, need_lang)
            upstream = produce(
                article_id,
                need_kind,
                lang=need_lang,
                settings=s,
                profile=prof,
                llm=llm,
                force=False,
            )
            if not upstream.ok:
                return ProduceResult(
                    kind=task.kind,
                    ok=False,
                    error=f"前置产物「{spec(need_kind).label}」未能生成：{upstream.error}",
                )

    started = time.perf_counter()
    try:
        if is_audio:
            result = _generate_audio(
                task, speaker, directory, lang=lang, settings=s,
                on_progress=on_progress, llm=llm, article_id=article_id,
            )
        else:
            result = _generate(
                task,
                article,
                provider,
                directory,
                article_id,
                lang=lang,
                variant=variant,
                instructions=instructions,
                settings=s,
                profile=prof,
            )
    except Exception as exc:  # noqa: BLE001 - 失败要记进台账，不能只是抛出去
        error = " ".join(str(exc).split())[:300]
        logger.warning("生成失败 %s / %s：%s", article_id[:8], task.kind, error)
        ledger.record_production(
            article_id,
            str(task.kind),
            status="failed",
            lang=lang,
            variant=variant,
            instructions=instructions,
            error=error,
            llm_provider=generator.name,
            llm_model=generator.model,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return ProduceResult(kind=task.kind, ok=False, error=error)

    directory.mkdir(parents=True, exist_ok=True)
    if result.audio is not None:
        output.write_bytes(result.audio)
        # 字幕和音频**同名同目录**（`narration_audio.srt`）：
        # 这样拖进剪辑软件时两个文件是一眼配对的，不必再想哪个配哪个。
        # Same stem, same folder: the pairing is obvious in the editor.
        if s.tts_subtitles and result.cues:
            written = write_srt(output.with_suffix(".srt"), result.cues)
            if written is not None:
                logger.info("字幕已导出：%s（%d 条）", written.name, len(result.cues))
    else:
        output.write_text(result.text, encoding="utf-8")

    if result.sidecar is not None:
        sidecar_name = json_sidecar(task, lang)
        if sidecar_name:
            (directory / sidecar_name).write_text(
                json.dumps(result.sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    ledger.record_production(
        article_id,
        str(task.kind),
        status="ok",
        lang=lang,
        variant=variant,
        instructions=instructions,
        output_path=output.relative_to(s.output_path).as_posix(),
        chars=result.chars,
        est_seconds=result.seconds,
        llm_provider=generator.name,
        llm_model=generator.model,
        calls=result.calls,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )

    return ProduceResult(
        kind=task.kind,
        ok=True,
        path=output,
        chars=result.chars,
        seconds=result.seconds,
        calls=result.calls,
        within_target=result.within_target,
    )


def produce_all(
    article_id: str,
    *,
    lang: str = DEFAULT_LANGUAGE,
    force: bool = False,
    settings: Settings | None = None,
    profile: Profile | None = None,
    llm: LLMProvider | None = None,
) -> list[ProduceResult]:
    """
    批量生成 / Produce the batch kinds.

    **不含长文案**——它是最贵的产物，必须显式指定（见 `tasks.py`）。
    Excludes the long-form script, which must be requested explicitly.

    前一项失败不阻断后面的：四项彼此独立（除了英文总结依赖中文总结，
    而那条依赖由 `produce` 自己处理）。
    A failure does not stop the rest: the kinds are independent apart from the English
    summary's prerequisite, which `produce` handles itself.
    """
    provider = llm or get_llm()
    prof = profile or safe_profile()
    return [
        produce(
            article_id,
            kind,
            lang=lang,
            force=force,
            settings=settings,
            profile=prof,
            llm=provider,
        )
        for kind in batch_kinds()
    ]


def is_new_article(
    record: ArticleRecord,
    productions: dict,
) -> bool:
    """
    这篇是「手动导入、还没动过」的吗 / Is this a manually imported, untouched article?

    两个条件同时成立 / Both conditions must hold:
        1. **是人工投递进来的**（界面粘的链接或飞书投的），不是 RSS 抓来的
        2. **一条产物记录都没有**——包括失败的那种

    标识**一直挂着，直到对它调过一次 LLM 为止**。
    The badge stays until an LLM has been called for the article.

    为什么第 2 条算上失败的 / Why a failed attempt also clears the flag:
        失败的记录说明**已经调过 LLM 了**（钱已经花了），这篇不再是「没动过」。
        而且失败的格子本身就显示 ▲，和 NEW 摆在一起是自相矛盾的信号：
        一个说「还没开始」，一个说「试过而且出错了」。
        A failure record means the LLM was already called and the money already spent, so
        the article is no longer untouched. The cell also already shows a failure glyph,
        and pairing it with NEW would send contradicting signals.

    为什么改成看来源，而不是看时间 / Why the source replaced the time window:
        原先加了 24 小时的时间窗，因为实测 37 篇里有 32 篇没有任何产物——
        RSS 抓来的存量文章大多如此，只看「有没有产物」的话 NEW 会挂在 32 行上。
        但时间窗解决错了问题：它让**昨天粘进来、今天还没处理**的链接第二天就
        失去标识——而那恰恰是最需要标识的一条。实测 `fe3b7d3c` 导入 47 小时、
        零产物，正是这样丢掉的。
        真正要区分的是**来源**：人工粘进来的链接是「我特意要处理的」，
        RSS 抓来的是「候选池」。按来源过滤后实测只有 5 行挂 NEW，
        既解决了泛滥，又不会因为过了一夜就把待办清空。
        The window solved the wrong problem: it stripped the badge from a link pasted
        yesterday and still untouched today — precisely the row that most needs it. What
        actually distinguishes them is provenance: a pasted link was deliberately chosen,
        while the RSS backlog is a candidate pool. Filtering by source leaves five badged
        rows in the real ledger, without emptying the to-do list overnight.
    """
    if productions:
        return False
    return record.via in MANUAL_SOURCES




def read_production(
    article_id: str,
    kind: ProductionKind | str,
    *,
    lang: str = DEFAULT_LANGUAGE,
    settings: Settings | None = None,
) -> str:
    """读回已生成的产物内容 / Read back a production's text; empty when absent."""
    s = settings or get_settings()
    record = Ledger(s.db_file).get(article_id)
    if record is None or not record.store_dir:
        return ""

    path = s.output_path / record.store_dir / spec(kind).filename_for(lang)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def speech_segments_for(
    article_id: str,
    kind: ProductionKind | str,
    *,
    lang: str = DEFAULT_LANGUAGE,
    settings: Settings | None = None,
    llm: LLMProvider | None = None,
) -> list[SpeechSegment]:
    """
    这一格音频会念哪些分段 / The pieces this audio cell would speak.

    「高级配置」用它把稿子交给 TTS 图形界面 —— 界面里看到的分段与自动合成
    **完全一致**（同一份 `_build_segments`），否则在界面上调好的东西
    换成自动合成又不一样了。
    Used by the advanced hand-off so the workbench shows exactly what the pipeline would
    synthesise.
    """
    s = settings or get_settings()
    task = spec(kind)
    if task.audio_of is None:
        return []

    record = Ledger(s.db_file).get(article_id)
    if record is None or not record.store_dir:
        return []
    segments, _ = _build_segments(
        task, s.output_path / record.store_dir, lang=lang, settings=s, llm=llm
    )
    return segments


def import_audio(
    article_id: str,
    kind: ProductionKind | str,
    wav: Path,
    *,
    lang: str = DEFAULT_LANGUAGE,
    extras: Sequence[Path] = (),
    source: str = "tts_gui",
    settings: Settings | None = None,
) -> ProduceResult:
    """
    收下一份**在别处生成**的音频 / Adopt audio produced elsewhere.

    「高级配置」把稿子交给 TTS 图形界面精修之后，成品在 TTS 那一侧。这个函数把它
    收进文章目录并**记进台账** —— 不记的话工作台上那一格仍然是空的，
    人会以为刚才在界面里忙半天的成果丢了。
    After the workbench hands a script to the TTS GUI, the finished audio lives on the
    service side. This adopts it and records it, or the cell would still read empty and
    the work would look lost.

    参数 / Args:
        wav:    已经落到本地的整条音频
        extras: 一起收下的附件（逐段 wav、字幕），放进 `<文章目录>/tts/`。
                **一篇一个文件夹、同名覆盖**，不按来源或时间再分层
        source: 记进台账的来源标记，用来区分「界面精修的」与「流水线合成的」

    落盘位置与正常合成**完全一致**（`narration_audio.wav` 这些），
    这样下载按钮、播放器、视频合成都不必区分音频是从哪条路来的。
    The file lands exactly where a pipeline run would put it, so nothing downstream
    needs to know which route produced it.
    """
    s = settings or get_settings()
    task = spec(kind)
    ledger = Ledger(s.db_file)

    record = ledger.get(article_id)
    if record is None or not record.store_dir:
        return ProduceResult(kind=task.kind, ok=False, error=f"台账里没有这篇文章：{article_id}")
    if not wav.is_file():
        return ProduceResult(kind=task.kind, ok=False, error=f"音频文件不存在：{wav}")

    directory = s.output_path / record.store_dir
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / task.filename_for(lang)
    payload = wav.read_bytes()
    output.write_bytes(payload)

    seconds = wav_seconds(payload)

    # 字幕跟音频**同名同目录**，和流水线合成出来的完全一样（`narration_audio.srt`），
    # 这样下游（剪辑、发布）不必区分这条音频是哪条路来的。
    # The subtitle sits beside the audio exactly as a pipeline run would leave it.
    for extra in extras:
        if extra.is_file() and extra.suffix.lower() == ".srt":
            output.with_suffix(".srt").write_bytes(extra.read_bytes())
            break

    kept: list[Path] = []
    if extras:
        target = directory / (s.tts_artifact_dirname or "tts")
        target.mkdir(parents=True, exist_ok=True)
        for extra in extras:
            if extra.is_file() and extra.resolve() != wav.resolve():
                destination = target / extra.name
                if destination.resolve() != extra.resolve():
                    destination.write_bytes(extra.read_bytes())
                kept.append(destination)

    ledger.record_production(
        article_id,
        str(task.kind),
        status="ok",
        lang=lang,
        output_path=output.relative_to(s.output_path).as_posix(),
        est_seconds=seconds,
        llm_provider="tts_service",
        llm_model=source,
        calls=0,
    )
    logger.info("收下外部音频 %s：%.1f 秒，附件 %d 个", output.name, seconds, len(kept))

    return ProduceResult(kind=task.kind, ok=True, path=output, seconds=seconds)


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _generate(
    task: TaskSpec,
    article: Article,
    llm: LLMProvider,
    directory: Path,
    article_id: str,
    *,
    lang: str,
    variant: str | None,
    instructions: str,
    settings: Settings,
    profile: Profile,
) -> Generated:
    """
    分派到具体的文本生成器 / Dispatch to the concrete text generator.

    时长区间与结尾引导语都从 profile 取 / Windows and sign-off come from the profile:
        写死在构建器默认值里的话，`profile.yaml` 里那三行时长配置就是死配置——
        改了没反应，比没有这个配置更糟。
        Hard-coding them in the builders' defaults would leave the three duration lines in
        `profile.yaml` inert: editing them would silently do nothing, which is worse than
        not offering the setting at all.
    """
    if task.kind is ProductionKind.SUMMARY:
        if lang == "zh":
            from dna.pipeline.summarize import summarize_cluster

            cluster = _as_cluster(article, article_id)
            result = summarize_cluster(cluster, llm, instructions=instructions)
            if result.degraded:
                raise RuntimeError("摘要调用失败，已退回标题；请重试")
            body = front_matter(article, "总结") + result.summary + "\n"
            return Generated(text=body, chars=len(result.summary), calls=1)

        # 英文总结**翻译已写好的中文**，不重写一遍：便宜，而且中英两版保证说的是
        # 同一件事。见 `tasks.TaskSpec.translated_from`。
        from dna.pipeline.translate import translate_batch

        chinese = read_production(
            article_id, ProductionKind.SUMMARY, lang="zh", settings=settings
        )
        summary_zh = strip_front_matter(chinese)
        translated = translate_batch(
            [(article_id, article.title, summary_zh)], llm, instructions=instructions
        )
        if article_id not in translated:
            raise RuntimeError("翻译未返回该条目")
        title_en, summary_en = translated[article_id]
        body = f"# {title_en}\n\n> Source: {article.url}\n\n{summary_en}\n"
        return Generated(text=body, chars=len(summary_en), calls=1)

    if task.kind is ProductionKind.SHORTVIDEO:
        low, high = profile.video_duration_seconds
        script = build_short_video(
            article,
            llm,
            low=low,
            high=high,
            cta=profile.cta_line,
            lang=lang,
            instructions=instructions,
        )
        body = front_matter(article, f"短视频文案（{lang}）") + script_block(script)
        return Generated(
            text=body,
            chars=script.chars,
            seconds=script.seconds,
            calls=script.calls,
            within_target=script.within_target,
        )

    if task.kind is ProductionKind.NARRATION:
        low, high = profile.narration_duration_seconds
        script = build_narration(
            article,
            llm,
            low=low,
            high=high,
            cta=profile.cta_line,
            lang=lang,
            instructions=instructions,
        )
        body = front_matter(article, f"口播文案（{lang}）") + script_block(script)
        return Generated(
            text=body,
            chars=script.chars,
            seconds=script.seconds,
            calls=script.calls,
            within_target=script.within_target,
        )

    if task.kind is ProductionKind.LONGFORM:
        ok, reason = can_build_longform(article)
        if not ok:
            raise ValueError(reason)

        mode = LongformMode(variant or LongformMode.FEATURE)
        low, high = profile.longform_duration_seconds
        result = build_longform(
            article, llm, mode=mode, low=low, high=high, lang=lang, instructions=instructions
        )
        label = "专题" if mode is LongformMode.FEATURE else "访谈"
        outline = "\n".join(f"{i}. {s_.title}" for i, s_ in enumerate(result.sections, 1))
        body = (
            front_matter(
                article, f"长文案 · {label} · {lang}（约 {result.seconds / 60:.0f} 分钟）"
            )
            + f"<!-- 提纲\n{outline}\n-->\n\n"
            + result.text
            + "\n"
        )
        return Generated(
            text=body,
            chars=len(result.text),
            seconds=result.seconds,
            calls=result.calls,
            sidecar=result.to_json_dict(),
        )

    raise ValueError(f"未知的产物类型：{task.kind}")


def _as_cluster(article: Article, article_id: str) -> Cluster:
    """
    把单篇文章包成 Cluster / Wrap one article as a single-member cluster.

    复用 `pipeline/summarize` 而不是另写一份单篇摘要：提示词、降级处理、
    标签抽取全都是现成的，重写一遍只会多出一处要同步维护的地方。
    Reuses `pipeline/summarize` rather than duplicating a single-article summariser: the
    prompt, degradation handling and tag extraction already exist, and a second copy
    would only be another place to keep in sync.
    """
    item = NewsItem(
        id=article_id,
        source_id="",
        via="rss",  # type: ignore[arg-type] - NewsItem 会做枚举转换
        url=article.url,
        canonical_url=article.url,
        title=article.title,
        text=article.text,
        published_at=article.published_at,
        media=article.media,
    )
    return Cluster(id=article_id, members=[item], canonical_url=article.url)


def _load_article(article_id: str, settings: Settings) -> Article | None:
    """
    从落盘目录读回完整文章 / Read the full article back from disk.

    复用 `pipeline/source.load_candidates`：它已经处理好了「读 meta.json、
    目录缺失时降级、媒体项损坏时跳过」这些情况。
    Reuses `pipeline/source.load_candidates`, which already handles reading meta.json,
    degrading when the directory is gone, and skipping corrupt media entries.
    """
    items = load_candidates(settings=settings, article_ids=[article_id])
    if not items:
        return None

    item = items[0]
    return Article(
        url=item.url,
        title=item.title,
        text=item.text,
        published_at=item.published_at,
        media=item.media,
        extraction_ok=bool(item.text),
    )


def _generate_audio(
    task: TaskSpec,
    tts: TTSProvider,
    directory: Path,
    *,
    lang: str,
    settings: Settings,
    on_progress: ProgressFn | None,
    llm: LLMProvider | None = None,
    article_id: str = "",
) -> Generated:
    """
    把已有的稿子合成为音频 / Synthesise the existing script into audio.

    **输入是稿子文件，不是原文。**稿子已经是为朗读写的了，再回头找原文只会
    念出一篇没人打算念的东西。
    The input is the script file, not the article: the script is already written to be
    read aloud, whereas the article is not.

    两条取文路径 / Two routes into the text:
        长文案走 `.json` 附件——它已经按发言人切好 turns，访谈的两个角色就是
        两个音色；其余走 Markdown，解析出「口播」那一段。
        Long-form reads the JSON sidecar, already split into speaker turns; the others
        parse the spoken section out of the Markdown.

    三步，顺序不能反 / Three steps in this order:
        1. **朗读友好化**（一次 LLM 调用，见 `tts/preprocess.py`）：型号、公式、
           多音字、断句。必须在分段**之前**做 —— 它会调整断句与停顿标记，
           先切好再改写，切点就落在了改写前的位置上
        2. **分段**：切成一段段独立合成的长度，只在句子边界切
        3. **合成**：交给 TTS 服务，逐段回报进度
        Preprocessing precedes segmentation because it rewrites the very punctuation the
        segmentation depends on.
    """
    segments, spoken_chars = _build_segments(
        task, directory, lang=lang, settings=settings, llm=llm
    )
    if not segments:
        raise RuntimeError(f"{spec(task.audio_of).label}里没有可朗读的内容")

    logger.info("开始合成 %s：%d 段 · %d 字", task.label, len(segments), spoken_chars)
    # 服务端产物目录名**按「文章 + 产物 + 语言」固定**：重做覆盖同一个目录，
    # 而不是每次多留一份时间戳目录（那样「哪份是最新的」只能靠人比时间戳）。
    # A stable name per cell: a redo overwrites rather than accumulating.
    run = "-".join(filter(None, [article_id[:8] or "adhoc", str(task.kind), lang]))
    clip = tts.synthesize(segments, on_progress=on_progress, run=run)

    # 服务端留下的逐段 wav 与字幕拷进文章目录 —— 产物必须在**本项目的**输出里
    # 找得齐，否则想换一句话或拿字幕去剪辑时得翻到另一个仓库的 outputs/ 下。
    # The service's own files are copied next to the production so everything for one
    # article stays in one place.
    _copy_tts_artifacts(tts, clip, directory, settings)

    if not clip.complete:
        # 缺了几段的音频照样存下来——重跑要几十分钟，把能用的先留住，
        # 但**必须说出来缺了几段**，否则人会把残缺的音频当成成品发出去。
        # Incomplete audio is still saved because re-running costs half an hour, but the
        # gap is reported: otherwise a defective file gets published as finished.
        logger.warning(
            "%s 有 %d/%d 段合成失败，音频不完整",
            task.label,
            len(clip.failed_segments),
            clip.segments,
        )

    return Generated(
        audio=clip.wav,
        chars=spoken_chars,
        seconds=clip.seconds,
        calls=0,
        within_target=clip.complete,
        cues=list(getattr(clip, "cues", [])),
    )


def _build_segments(
    task: TaskSpec,
    directory: Path,
    *,
    lang: str,
    settings: Settings,
    llm: LLMProvider | None,
) -> tuple[list[SpeechSegment], int]:
    """
    稿子文件 → 一串待合成的分段 / The script file to a list of pieces.

    两条路径都在这里，**两个入口共用它**：流水线合成（`_generate_audio`）与
    交给 TTS 界面精修（`speech_segments_for`）必须切得一模一样，
    否则界面上看到的分段和自动合成出来的对不上。
    Shared by both entry points so the pieces a person sees in the TTS workbench are
    exactly the pieces the pipeline would have synthesised.
    """
    script_spec = spec(task.audio_of)
    script_path = directory / script_spec.filename_for(lang)
    try:
        markdown = script_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"读不到{script_spec.label}：{script_path.name}") from exc

    sidecar_name = json_sidecar(script_spec, lang)
    sidecar = directory / sidecar_name if sidecar_name else None
    turns = longform_turns(sidecar) if sidecar and sidecar.exists() else []

    if turns:
        pairs = [(role, text) for role, text in turns]
    else:
        pairs = [("narrator", spoken_text(markdown))]

    segments: list[SpeechSegment] = []
    spoken_chars = 0
    for role, text in pairs:
        prepared = prepare_for_speech(text, llm=llm, settings=settings)
        if prepared.reason:
            # 退回原文也要留一行日志：音质问题还在，只是没挡住音频
            logger.info("TTS 预处理未生效（%s）：%s", role, prepared.reason)
        elif prepared.notes:
            logger.info("TTS 预处理（%s）：%s", role, "；".join(prepared.notes[:5]))

        # 音色跟着语言走：英文稿用英文音色，否则模型会用中文的发音习惯念英文
        # The voice follows the language; otherwise English is read with Chinese phonetics.
        voice = voice_for_role(role, settings, lang=lang)
        for piece in split_for_speech(prepared.text):
            segments.append(SpeechSegment(text=piece, voice=voice, role=role))
            spoken_chars += len(piece)

    return segments, spoken_chars


def _copy_tts_artifacts(
    tts: TTSProvider, clip, directory: Path, settings: Settings
) -> list[Path]:
    """
    把 TTS 服务那边的产物取回来 / Pull the service's artifacts over.

    provider 不一定实现（协议里是可选的），所以用 `getattr` 探一下 ——
    将来换个不落盘的 provider 时，这里不该因此报错。
    Optional in the protocol, so its absence is not an error.

    取不回来只记一条警告：**主音频已经在手上了**，拿不到逐段文件不该让整次
    生成算失败。
    The main track is already in hand; a failed copy must not void the production.
    """
    fetch = getattr(tts, "fetch_artifacts", None)
    if fetch is None or not getattr(clip, "pieces", None):
        return []

    # 一篇文章一个文件夹，**不按 run 再分一层**：重做直接覆盖同名文件。
    # 分层的话「这篇的音频是哪一份」要靠人比时间戳，而人只会打开最上面那个。
    # One folder per article, overwritten on redo.
    target = directory / (settings.tts_artifact_dirname or "tts")
    try:
        saved = fetch(clip, target)
    except Exception as exc:  # noqa: BLE001 - 拷贝失败不影响成品
        logger.warning("TTS 产物拷贝失败：%s", exc)
        return []
    if saved:
        logger.info("已拷回 %d 个 TTS 产物 → %s", len(saved), target)
    return saved


__all__ = [
    "MANUAL_SOURCES",
    "Generated",
    "ProduceResult",
    "import_audio",
    "is_new_article",
    "produce",
    "produce_all",
    "read_production",
    "speech_segments_for",
]
