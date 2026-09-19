"""
流水线编排 / Pipeline orchestration.

把六个节点串成一条可重放的流程，产出唯一的事实源 `DailyDigest`。
Chains the six nodes into one replayable flow producing the single source of truth,
`DailyDigest`.

    台账 → clean → dedup → score → summarize → translate → trend → DailyDigest
           ───────── 无 LLM，纯计算 ─────────   ──── 调用 LLM ────

分界线的意义 / Why the boundary matters:
    前三个节点不花钱，可以随便跑；后三个每跑一次都在计费。因此 `run_daily()`
    提供 `--dry-run`：只跑前半段，把「今天会选出哪些条目」先给人看，
    确认选得对了再花钱做摘要。**先看清单再付费**，而不是付完钱才发现选错了。
    The first three nodes are free and can be run freely; the last three bill on every
    run. `run_daily()` therefore offers a dry run that stops at the boundary and shows
    which entries would be picked, so the selection is confirmed before paying to
    summarise it — inspect the list first, rather than discovering a bad selection after
    paying for it.

DailyDigest 是三个发布应用（图文 / 视频 / 播客）的唯一输入。
它一旦生成，重做任何一种输出都不需要重新采集或重新调用 LLM。
`DailyDigest` is the only input the graphic, video and podcast apps consume. Once it
exists, redoing any output requires neither re-collection nor further LLM calls.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime

from dna.core.config import Profile, Settings, safe_profile
from dna.core.logging import get_logger
from dna.core.models import (
    Cluster,
    DailyDigest,
    DigestEntry,
    DigestStats,
    MediaKind,
    NewsItem,
)
from dna.llm.base import LLMProvider
from dna.pipeline import summarize, translate, trend
from dna.pipeline.dedup import DedupResult, dedup
from dna.pipeline.score import ScoreBreakdown, rank_clusters

logger = get_logger("pipeline.flow")


@dataclass
class PipelineReport:
    """
    一次流水线运行的过程记录 / A trace of one pipeline run.

    和 `DailyDigest` 分开：Digest 是**产物**，给发布应用用；
    Report 是**过程**，给人看「为什么是这些条目」。混在一起会让产物越长越杂。
    Kept separate from `DailyDigest`: the digest is the artefact consumed by the apps,
    while the report explains to a person why these entries were chosen. Merging them
    would bloat the artefact.
    """

    dedup_result: DedupResult | None = None
    ranked: list[tuple[Cluster, ScoreBreakdown, bool]] = field(default_factory=list)
    summaries_degraded: int = 0
    translated_count: int = 0
    llm_calls: int = 0
    cache_hits: int = 0
    duration_ms: int = 0

    def explain(self, limit: int = 10) -> list[str]:
        """逐条解释入选理由 / Explain why each entry was selected."""
        return [
            f"{index}. [{breakdown.total:.3f}] {cluster.canonical.title}"
            f"{'  📹' if need_video else ''}\n     {breakdown.explain()}"
            for index, (cluster, breakdown, need_video) in enumerate(self.ranked[:limit], 1)
        ]


def run_daily(
    items: list[NewsItem],
    *,
    llm: LLMProvider | None = None,
    settings: Settings | None = None,
    profile: Profile | None = None,
    when: Date | None = None,
    max_entries: int | None = None,
    bilingual: bool = False,
    dry_run: bool = False,
    use_embeddings: bool = False,
) -> tuple[DailyDigest, PipelineReport]:
    """
    跑一整期日报 / Run one full issue.

    参数 / Args:
        items:          已清洗的新闻条目（来自 `clean.clean_all`）
        llm:            LLM provider；`dry_run` 时可以为 None
        max_entries:    日报最多几条；None 表示按 profile.digest_max_entries
        bilingual:      是否产出英文版
        dry_run:        **只跑到打分为止，不调用 LLM、不产生费用**
        use_embeddings: 是否启用语义聚类（需要向量模型）

    返回 / Returns:
        (DailyDigest, PipelineReport)

    `dry_run=True` 时返回的 Digest 里，摘要位置暂时放的是标题——
    这样人能直接看到「入选的是哪些条目、排序对不对」，确认后再花钱。
    In a dry run the digest carries titles where summaries will go, so the selection and
    ordering can be inspected before any money is spent.
    """
    started = time.perf_counter()
    prof = profile or safe_profile()
    report = PipelineReport()

    limit = max_entries if max_entries is not None else prof.digest_max_entries

    # -- 无 LLM 的前半段 / the free half -------------------------------------
    report.dedup_result = dedup(
        items,
        threshold=prof.tuning.hamming_threshold,
        sample_chars=prof.tuning.simhash_sample_chars,
        use_embeddings=use_embeddings,
    )
    report.ranked = rank_clusters(report.dedup_result.clusters, prof, limit=limit)

    if dry_run:
        digest = _assemble(
            report.ranked,
            summaries=[c.canonical.title for c, _, _ in report.ranked],
            tag_lists=[[] for _ in report.ranked],
            when=when,
        )
        digest.stats = _build_stats(report, items, llm=None, started=started)
        report.duration_ms = digest.stats.duration_ms
        logger.info("dry-run 完成（未调用 LLM）：%d 条候选", len(digest.entries))
        return digest, report

    if llm is None:
        raise ValueError("非 dry-run 模式必须提供 llm / an LLM is required unless dry_run is set")

    # -- 调用 LLM 的后半段 / the billed half ---------------------------------
    clusters = [c for c, _, _ in report.ranked]
    summary_results = summarize.summarize_all(
        clusters, llm, chars=prof.summary_chars,
        max_rewrites=prof.summary_max_rewrites,
        body_chars=prof.summary_body_chars,
    )
    report.summaries_degraded = sum(1 for r in summary_results if r.degraded)

    digest = _assemble(
        report.ranked,
        summaries=[r.summary for r in summary_results],
        tag_lists=[r.tags for r in summary_results],
        when=when,
    )

    note, trend_keywords = trend.build_trend(
        [(e.title_zh, e.summary_zh) for e in digest.entries],
        llm,
        min_entries=prof.tuning.min_entries_for_trend,
    )
    digest.trend_note_zh = note

    if bilingual:
        _fill_english(digest, llm, report)

    if trend_keywords:
        logger.debug("当日关键词：%s", "、".join(trend_keywords))

    digest.stats = _build_stats(report, items, llm=llm, started=started)
    report.duration_ms = digest.stats.duration_ms
    report.cache_hits = getattr(llm, "hits", 0)

    logger.info(
        "日报生成完成：%d 条，耗时 %.1f 秒%s",
        len(digest.entries),
        digest.stats.duration_ms / 1000,
        f"，缓存命中 {report.cache_hits} 次" if report.cache_hits else "",
    )
    return digest, report


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _assemble(
    ranked: list[tuple[Cluster, ScoreBreakdown, bool]],
    *,
    summaries: list[str],
    tag_lists: list[list[str]],
    when: Date | None,
) -> DailyDigest:
    """
    组装 Digest / Assemble the digest.

    图片与视频从代表条目上取，但**来源链接取整个 cluster 的**——
    多源报道时，读者应该看到全部出处，而不只是被选中那一家。
    Images and video come from the representative item, but the reference links come from
    the whole cluster: with multi-source coverage the reader should see every origin, not
    only the one that happened to be picked.
    """
    entries: list[DigestEntry] = []

    for rank, ((cluster, breakdown, need_video), summary, tags) in enumerate(
        zip(ranked, summaries, tag_lists, strict=True), start=1
    ):
        item = cluster.canonical
        entries.append(
            DigestEntry(
                id=cluster.id,
                rank=rank,
                cluster_id=cluster.id,
                title_zh=item.title,
                summary_zh=summary,
                score=breakdown.total,
                tags=tags,
                need_video=need_video,
                images=[m for m in item.media if m.kind is MediaKind.IMAGE],
                videos=[m for m in item.media if m.kind is MediaKind.VIDEO],
                refs=cluster.refs,
            )
        )

    return DailyDigest(date=when or datetime.now().date(), entries=entries)


def _fill_english(digest: DailyDigest, llm: LLMProvider, report: PipelineReport) -> None:
    """
    回填英文版 / Back-fill the English edition.

    就地修改 Digest：英文是同一份产物的另一面，不是另一份产物。
    分成两份会让「重做英文版」变成「重跑整条流水线」。
    The digest is modified in place: English is another facet of the same artefact, not a
    second one. Splitting them would turn "redo the English edition" into "rerun the
    whole pipeline".
    """
    pairs = [(e.id, e.title_zh, e.summary_zh) for e in digest.entries]
    translated = translate.translate_all(pairs, llm)
    report.translated_count = len(translated)

    for entry in digest.entries:
        if entry.id in translated:
            entry.title_en, entry.summary_en = translated[entry.id]

    if digest.trend_note_zh:
        digest.trend_note_en = translate.translate_text(
            digest.trend_note_zh, llm, kind="trend note"
        )


def _build_stats(
    report: PipelineReport,
    items: list[NewsItem],
    *,
    llm: LLMProvider | None,
    started: float,
) -> DigestStats:
    """汇总统计 / Collect the run's statistics."""
    dedup_result = report.dedup_result
    return DigestStats(
        raw_count=len(items),
        after_dedup_count=len(dedup_result.clusters) if dedup_result else 0,
        cluster_count=len(dedup_result.clusters) if dedup_result else 0,
        entry_count=len(report.ranked),
        source_count=len({i.source_id for i in items if i.source_id}),
        llm_provider=llm.info.name if llm else None,
        llm_model=llm.info.model if llm else None,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )




__all__ = ["PipelineReport", "run_daily"]
