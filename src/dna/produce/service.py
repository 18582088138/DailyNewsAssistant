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
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
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
from dna.produce.tasks import ProductionKind, TaskSpec, batch_kinds, json_sidecar, spec
from dna.store.ledger import ArticleRecord, Ledger, ProductionRecord
from dna.tts.base import ProgressFn, SpeechSegment, TTSProvider
from dna.tts.factory import get_tts, voice_for_role
from dna.tts.segment import split_for_speech

logger = get_logger("produce.service")

# 「新导入」标识默认的时间窗（小时）/ default window for the "new import" flag
# 24 小时：日报是按天做的，早上粘的链接当天就该处理完。实际取值来自
# `Profile.new_badge_hours`，这里只是不传参时的兜底。
# One day, matching the daily cadence: links pasted in the morning are meant to be worked
# through the same day. The real value comes from the profile.
DEFAULT_NEW_WINDOW_HOURS = 24


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
    """记进台账的字数：文本产物是文件长度，音频产物是**被朗读的字数**。"""
    seconds: float | None = None
    """文本产物是估算时长，音频产物是**真实时长**——音频存在之后没必要再估。"""
    calls: int = 0
    within_target: bool = True
    sidecar: dict | None = None


def produce(
    article_id: str,
    kind: ProductionKind | str,
    *,
    variant: str | None = None,
    force: bool = False,
    settings: Settings | None = None,
    profile: Profile | None = None,
    llm: LLMProvider | None = None,
    tts: TTSProvider | None = None,
    on_progress: ProgressFn | None = None,
) -> ProduceResult:
    """
    为一篇文章生成一种产物 / Produce one kind for one article.

    参数 / Args:
        variant:     长文案的形式，`feature`（专题）或 `interview`（访谈）
        force:       已有产物时是否重做。**默认不重做，也就不花钱**
        profile:     时长区间与结尾引导语来自这里；不给则读 profile.yaml
        llm:         注入 provider；不给则按 .env 构造（测试一律注入假的）
        tts:         音频产物的后端；不给则按 .env 构造
        on_progress: 音频合成的进度回调 `(已完成段, 总段, 已产出秒数)`。
                     长文案音频要跑半小时，没有进度就只能看着界面发呆
    """
    s = settings or get_settings()
    prof = profile or safe_profile()
    task = spec(kind)
    ledger = Ledger(s.db_file)

    record = ledger.get(article_id)
    if record is None:
        return ProduceResult(kind=task.kind, ok=False, error=f"台账里没有这篇文章：{article_id}")
    if not record.store_dir:
        return ProduceResult(kind=task.kind, ok=False, error="这篇文章还没有落盘目录")

    directory = s.output_path / record.store_dir
    output = directory / task.filename

    existing = ledger.latest_production(article_id, str(task.kind))
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

    # 音频产物不碰 LLM / audio never touches the LLM
    #
    # 无条件 `get_llm()` 的话，只想合成一段音频也会先要求 API key ——
    # 而这一步一分钱都不该花，也不该依赖网络。
    # Constructing the LLM unconditionally would demand an API key to synthesise audio,
    # which spends nothing and needs no network.
    is_audio = task.audio_of is not None
    provider = None if is_audio else (llm or get_llm())
    speaker = (tts or get_tts(s)) if is_audio else None
    generator = speaker.info if is_audio else provider.info

    # 前置缺失时先补上——报错让人手动跑一遍是没必要的摩擦
    #
    # **前置一律不 force**：重做音频不该顺手把稿子也重新调一遍 LLM。
    # 下游重做是免费的，上游重做是要花钱的，让一个动作同时触发两者是危险的默认值。
    # Prerequisites are never forced: redoing the audio must not re-bill the script.
    # The downstream redo is free while the upstream one costs money, and letting one
    # click trigger both is a dangerous default.
    if task.requires is not None:
        prerequisite = ledger.latest_production(article_id, str(task.requires))
        if prerequisite is None or not prerequisite.ok:
            logger.info("先补前置产物：%s", spec(task.requires).label)
            upstream = produce(
                article_id, task.requires, settings=s, profile=prof, llm=llm, force=False
            )
            if not upstream.ok:
                return ProduceResult(
                    kind=task.kind,
                    ok=False,
                    error=f"前置产物「{spec(task.requires).label}」未能生成：{upstream.error}",
                )

    started = time.perf_counter()
    try:
        if is_audio:
            result = _generate_audio(
                task, speaker, directory, settings=s, on_progress=on_progress
            )
        else:
            result = _generate(
                task,
                article,
                provider,
                directory,
                article_id,
                variant=variant,
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
            variant=variant,
            error=error,
            llm_provider=generator.name,
            llm_model=generator.model,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return ProduceResult(kind=task.kind, ok=False, error=error)

    directory.mkdir(parents=True, exist_ok=True)
    if result.audio is not None:
        output.write_bytes(result.audio)
    else:
        output.write_text(result.text, encoding="utf-8")

    if result.sidecar is not None:
        sidecar_name = json_sidecar(task)
        if sidecar_name:
            (directory / sidecar_name).write_text(
                json.dumps(result.sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    ledger.record_production(
        article_id,
        str(task.kind),
        status="ok",
        variant=variant,
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
        produce(article_id, kind, force=force, settings=settings, profile=prof, llm=provider)
        for kind in batch_kinds()
    ]


def is_new_article(
    record: ArticleRecord,
    productions: dict[str, ProductionRecord],
    *,
    now: datetime | None = None,
    within_hours: int = DEFAULT_NEW_WINDOW_HOURS,
) -> bool:
    """
    这篇是「刚导入、还没动过」的吗 / Is this a fresh, untouched import?

    两个条件同时成立 / Both conditions must hold:
        1. **一条产物记录都没有**——包括失败的那种
        2. 首次入库在最近 `within_hours` 小时内

    为什么第 1 条算上失败的 / Why a failed attempt also clears the flag:
        失败的记录说明**已经调过 LLM 了**（钱已经花了），这篇不再是「没动过」。
        而且失败的格子本身就显示 ▲，和 NEW 摆在一起是自相矛盾的信号：
        一个说「还没开始」，一个说「试过而且出错了」。
        A failure record means the LLM was already called and the money already spent, so
        the article is no longer untouched. The cell also already shows a failure glyph,
        and pairing it with NEW would send contradicting signals.

    为什么必须有时间窗 / Why the time window is not optional:
        实测台账里 37 篇有 32 篇没有任何产物——RSS 抓进来的存量文章大多如此。
        只看「有没有产物」的话，NEW 会挂在 32 行上，标识就完全失去意义了。
        标识存在的目的是**从几十行里找出刚粘进去的那几条**。
        Measured against the real ledger, 32 of 37 articles have no productions at all —
        most of the RSS backlog never gets any. Without the window the badge would sit on
        32 rows and mean nothing, when its entire purpose is to pick the few just pasted
        out of dozens.

    参数 / Args:
        within_hours: 时间窗；**≤ 0 表示不设时间窗**（只看有没有产物）
    """
    if productions:
        return False
    if within_hours <= 0:
        return True
    reference = now or datetime.now()
    return (reference - record.first_seen_at) <= timedelta(hours=within_hours)




def read_production(
    article_id: str, kind: ProductionKind | str, *, settings: Settings | None = None
) -> str:
    """读回已生成的产物内容 / Read back a production's text; empty when absent."""
    s = settings or get_settings()
    record = Ledger(s.db_file).get(article_id)
    if record is None or not record.store_dir:
        return ""

    path = s.output_path / record.store_dir / spec(kind).filename
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


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
    variant: str | None,
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
    if task.kind is ProductionKind.SUMMARY_ZH:
        from dna.pipeline.summarize import summarize_cluster

        cluster = _as_cluster(article, article_id)
        result = summarize_cluster(cluster, llm)
        if result.degraded:
            raise RuntimeError("摘要调用失败，已退回标题；请重试")
        body = front_matter(article, "总结") + result.summary + "\n"
        return Generated(text=body, chars=len(body), calls=1)

    if task.kind is ProductionKind.SUMMARY_EN:
        from dna.pipeline.translate import translate_batch

        chinese = read_production(article_id, ProductionKind.SUMMARY_ZH, settings=settings)
        summary_zh = strip_front_matter(chinese)
        translated = translate_batch([(article_id, article.title, summary_zh)], llm)
        if article_id not in translated:
            raise RuntimeError("翻译未返回该条目")
        title_en, summary_en = translated[article_id]
        body = f"# {title_en}\n\n> Source: {article.url}\n\n{summary_en}\n"
        return Generated(text=body, chars=len(body), calls=1)

    if task.kind is ProductionKind.SHORTVIDEO:
        low, high = profile.video_duration_seconds
        script = build_short_video(article, llm, low=low, high=high, cta=profile.cta_line)
        body = front_matter(article, "短视频文案") + script_block(script)
        return Generated(
            text=body,
            chars=len(body),
            seconds=script.seconds,
            calls=script.calls,
            within_target=script.within_target,
        )

    if task.kind is ProductionKind.NARRATION:
        low, high = profile.narration_duration_seconds
        script = build_narration(article, llm, low=low, high=high, cta=profile.cta_line)
        body = front_matter(article, "口播文案") + script_block(script)
        return Generated(
            text=body,
            chars=len(body),
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
        result = build_longform(article, llm, mode=mode, low=low, high=high)
        label = "专题" if mode is LongformMode.FEATURE else "访谈"
        outline = "\n".join(f"{i}. {s_.title}" for i, s_ in enumerate(result.sections, 1))
        body = (
            front_matter(article, f"长文案 · {label}（约 {result.seconds / 60:.0f} 分钟）")
            + f"<!-- 提纲\n{outline}\n-->\n\n"
            + result.text
            + "\n"
        )
        return Generated(
            text=body,
            chars=len(body),
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
    settings: Settings,
    on_progress: ProgressFn | None,
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
    """
    script_spec = spec(task.audio_of)
    script_path = directory / script_spec.filename
    try:
        markdown = script_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"读不到{script_spec.label}：{script_path.name}") from exc

    sidecar_name = json_sidecar(script_spec)
    sidecar = directory / sidecar_name if sidecar_name else None
    turns = longform_turns(sidecar) if sidecar and sidecar.exists() else []

    if turns:
        pairs = [(role, text) for role, text in turns]
    else:
        pairs = [("narrator", spoken_text(markdown))]

    segments: list[SpeechSegment] = []
    spoken_chars = 0
    for role, text in pairs:
        voice = voice_for_role(role, settings)
        for piece in split_for_speech(text):
            segments.append(SpeechSegment(text=piece, voice=voice, role=role))
            spoken_chars += len(piece)

    if not segments:
        raise RuntimeError(f"{script_spec.label}里没有可朗读的内容")

    logger.info("开始合成 %s：%d 段 · %d 字", task.label, len(segments), spoken_chars)
    clip = tts.synthesize(segments, on_progress=on_progress)

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
    )


__all__ = [
    "DEFAULT_NEW_WINDOW_HOURS",
    "Generated",
    "ProduceResult",
    "is_new_article",
    "produce",
    "produce_all",
    "read_production",
]
