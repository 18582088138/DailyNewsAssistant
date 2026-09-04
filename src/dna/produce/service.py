"""
单篇产物生成 / Per-article production service.

CLI 的 `dna produce` 与 GUI 的重做按钮**都调这里**——业务逻辑只有一份，
两个前端不会各写一套、各错一套。
Both `dna produce` and the workbench's redo buttons call into this, so the logic exists
once and the two front-ends cannot drift apart or break differently.

一次生成做的事 / What one run does:
    查前置 → 取文章 → 调生成器 → 写文件 → 记 productions 表

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
from dna.narration.script_builder import ScriptResult, build_narration, build_short_video
from dna.pipeline.source import load_candidates
from dna.produce.tasks import ProductionKind, TaskSpec, batch_kinds, json_sidecar, spec
from dna.store.ledger import ArticleRecord, Ledger, ProductionRecord

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
        parts.append(f"{self.calls} 次调用")
        return f"{label}：{'，'.join(parts)}"


def produce(
    article_id: str,
    kind: ProductionKind | str,
    *,
    variant: str | None = None,
    force: bool = False,
    settings: Settings | None = None,
    profile: Profile | None = None,
    llm: LLMProvider | None = None,
) -> ProduceResult:
    """
    为一篇文章生成一种产物 / Produce one kind for one article.

    参数 / Args:
        variant: 长文案的形式，`feature`（专题）或 `interview`（访谈）
        force:   已有产物时是否重做。**默认不重做，也就不花钱**
        profile: 时长区间与结尾引导语来自这里；不给则读 profile.yaml
        llm:     注入 provider；不给则按 .env 构造（测试一律注入假的）
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

    provider = llm or get_llm()

    # 前置缺失时先补上——报错让人手动跑一遍是没必要的摩擦
    if task.requires is not None:
        prerequisite = ledger.latest_production(article_id, str(task.requires))
        if prerequisite is None or not prerequisite.ok:
            logger.info("先补前置产物：%s", spec(task.requires).label)
            upstream = produce(
                article_id, task.requires, settings=s, profile=prof, llm=provider, force=force
            )
            if not upstream.ok:
                return ProduceResult(
                    kind=task.kind,
                    ok=False,
                    error=f"前置产物「{spec(task.requires).label}」未能生成：{upstream.error}",
                )

    started = time.perf_counter()
    try:
        text, seconds, calls, within, sidecar = _generate(
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
            llm_provider=provider.info.name,
            llm_model=provider.info.model,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return ProduceResult(kind=task.kind, ok=False, error=error)

    directory.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    if sidecar is not None:
        sidecar_name = json_sidecar(task)
        if sidecar_name:
            (directory / sidecar_name).write_text(
                json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    ledger.record_production(
        article_id,
        str(task.kind),
        status="ok",
        variant=variant,
        output_path=output.relative_to(s.output_path).as_posix(),
        chars=len(text),
        est_seconds=seconds,
        llm_provider=provider.info.name,
        llm_model=provider.info.model,
        calls=calls,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )

    return ProduceResult(
        kind=task.kind,
        ok=True,
        path=output,
        chars=len(text),
        seconds=seconds,
        calls=calls,
        within_target=within,
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
) -> tuple[str, float | None, int, bool, dict | None]:
    """
    分派到具体的生成器 / Dispatch to the concrete generator.

    返回 `(正文, 估算秒数, 调用次数, 是否达标, JSON 附件)`。

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
        body = _front_matter(article, "总结") + result.summary + "\n"
        return body, None, 1, True, None

    if task.kind is ProductionKind.SUMMARY_EN:
        from dna.pipeline.translate import translate_batch

        chinese = read_production(article_id, ProductionKind.SUMMARY_ZH, settings=settings)
        summary_zh = _strip_front_matter(chinese)
        translated = translate_batch([(article_id, article.title, summary_zh)], llm)
        if article_id not in translated:
            raise RuntimeError("翻译未返回该条目")
        title_en, summary_en = translated[article_id]
        body = f"# {title_en}\n\n> Source: {article.url}\n\n{summary_en}\n"
        return body, None, 1, True, None

    if task.kind is ProductionKind.SHORTVIDEO:
        low, high = profile.video_duration_seconds
        script = build_short_video(article, llm, low=low, high=high, cta=profile.cta_line)
        body = _front_matter(article, "短视频文案") + _script_block(script)
        return body, script.seconds, script.calls, script.within_target, None

    if task.kind is ProductionKind.NARRATION:
        low, high = profile.narration_duration_seconds
        script = build_narration(article, llm, low=low, high=high, cta=profile.cta_line)
        body = _front_matter(article, "口播文案") + _script_block(script)
        return body, script.seconds, script.calls, script.within_target, None

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
            _front_matter(article, f"长文案 · {label}（约 {result.seconds / 60:.0f} 分钟）")
            + f"<!-- 提纲\n{outline}\n-->\n\n"
            + result.text
            + "\n"
        )
        return body, result.seconds, result.calls, True, result.to_json_dict()

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


def _front_matter(article: Article, label: str) -> str:
    """
    产物文件的抬头 / The header every production file carries.

    带上原文标题与链接：这些文件会被单独拷去发布，脱离目录之后仍要能追溯来源——
    和图片旁边放 .json 是同一个道理。
    Carries the source title and link because these files get copied out for publishing
    and must remain traceable on their own — the same reasoning as the per-image sidecar.
    """
    return f"# {article.title}\n\n> {label}　·　来源：{article.url}\n\n"




def _script_block(script: ScriptResult) -> str:
    """
    短视频稿与口播稿的正文排版 / The body layout shared by both video scripts.

    两种稿子都带主副标题——发布时标题栏要填，写在产物里就不用再想一遍。
    Both carry a title pair, because the publishing form needs one and having it in the
    artefact saves composing it again.
    """
    parts = []
    if script.title:
        parts.append(f"**主标题：** {script.title}\n")
    if script.subtitle:
        parts.append(f"**副标题：** {script.subtitle}\n")
    parts.append(f"**口播（约 {script.seconds:.0f} 秒 · {script.chars} 字）：**\n")
    parts.append(f"{script.text}\n")
    return "\n".join(parts)


def _strip_front_matter(text: str) -> str:
    """去掉抬头，取正文 / Drop the header and return the body."""
    lines = [ln for ln in text.splitlines() if not ln.startswith(("# ", "> "))]
    return "\n".join(lines).strip()


__all__ = [
    "DEFAULT_NEW_WINDOW_HOURS",
    "ProduceResult",
    "is_new_article",
    "produce",
    "produce_all",
    "read_production",
]
