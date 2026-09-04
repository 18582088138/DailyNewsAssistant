"""
入库流程 / Intake pipeline.

把「采集 → 过滤 → 抓正文 → 落盘 → 记台账」串成一条可复用的流程。
Ties collection, filtering, body extraction, persistence and ledger recording into one
reusable flow.

CLI 与后续的 GUI 都调用这里，避免两边各写一份、各自漏掉一步。
Both the CLI and the later GUI call into this, so neither has to reimplement the flow
and miss a step.

设计要点 / Design notes:
    - **逐条隔离**：任何一篇文章抓取失败都只影响它自己，其余照常
    - **先查台账再抓**：已经抓过的文章不重复消耗网络与存储
    - **不涉及 LLM**：本流程只做采集与抽取，不产生任何 API 费用
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from dna.core.config import Profile, Settings, SourceConfig, get_settings, safe_profile
from dna.core.logging import get_logger
from dna.core.models import RawItem, SourceKind
from dna.core.urls import url_hash
from dna.extract.article import fetch_article
from dna.sources.filters import FilterOutcome, apply_filters, build_filter
from dna.sources.registry import CollectResult, build_adapters, collect
from dna.sources.user_link import items_from_text
from dna.store.article_store import (
    DEFAULT_MAX_IMAGES,
    read_body,
    read_title,
    save_article,
)
from dna.store.ledger import ArticleRecord, FetchStatus, Ledger
from dna.store.video_store import DEFAULT_MAX_VIDEOS

logger = get_logger("store.intake")


@dataclass
class IntakeResult:
    """
    一轮入库的结果 / The outcome of one intake run.

    每一类计数都要能对上账：collected = filtered + skipped_existing + processed。
    用户看到「今天只入了 6 条」时，应当能立刻知道其余的去哪了。
    The counts must reconcile: collected = filtered + skipped_existing + processed.
    When only six items are ingested the user should immediately see where the rest went.
    """

    collected: int = 0
    filtered_out: int = 0
    skipped_existing: int = 0
    fetched_ok: int = 0
    fetched_degraded: int = 0
    failed: int = 0
    article_ids: list[str] = field(default_factory=list)
    filter_reasons: dict[str, int] = field(default_factory=dict)
    source_failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def processed(self) -> int:
        """本轮实际抓取的条数 / Items actually fetched this run."""
        return self.fetched_ok + self.fetched_degraded + self.failed

    def summary(self) -> str:
        """一行摘要 / A one-line summary."""
        parts = [f"采集 {self.collected}"]
        if self.filtered_out:
            parts.append(f"过滤 {self.filtered_out}")
        if self.skipped_existing:
            parts.append(f"已存在跳过 {self.skipped_existing}")
        parts.append(f"新入库 {self.fetched_ok + self.fetched_degraded}")
        if self.fetched_degraded:
            parts.append(f"（其中降级 {self.fetched_degraded}）")
        if self.failed:
            parts.append(f"失败 {self.failed}")
        return "，".join(parts)


def intake_sources(
    *,
    settings: Settings | None = None,
    profile: Profile | None = None,
    source_ids: list[str] | None = None,
    limit_per_source: int | None = None,
    extract: bool = True,
    download_images: bool = True,
    max_images: int | None = None,
    download_videos: bool = True,
    refetch: bool = False,
) -> IntakeResult:
    """
    从订阅源采集并入库 / Collect from the configured sources and ingest.

    参数 / Args:
        source_ids:      只处理指定的源；None 表示全部启用的源
        extract:         是否抓正文；关掉只登记标题与链接（快速预览）
        download_images: 是否下载配图
        max_images:      每篇最多存几张配图；**None 表示按配置**
                         （源级 max_images > profile.max_images_per_article）
        download_videos: 是否下载官方视频（比图片慢得多）
        refetch:         已抓过的文章是否重抓
    """
    s = settings or get_settings()
    prof = profile or safe_profile()
    ledger = Ledger(s.db_file)

    configs = _select_configs(source_ids)
    adapters = build_adapters(configs, settings=s)
    collected: CollectResult = collect(adapters, settings=s, limit_per_source=limit_per_source)

    result = IntakeResult(collected=collected.total)
    result.source_failures = [(f.source_id, f.reason) for f in collected.failures]

    # 过滤按源分别应用：每个源可以有自己的规则
    by_source = {c.id: c for c in configs}
    grouped: dict[str, list[RawItem]] = {}
    for item in collected.items:
        grouped.setdefault(item.source_id, []).append(item)

    kept: list[RawItem] = []
    for source_id, items in grouped.items():
        config = by_source.get(source_id)
        rules = build_filter(config, prof) if config else build_filter(_dummy_config(source_id), prof)
        outcome: FilterOutcome = apply_filters(items, rules)

        kept.extend(outcome.kept)
        result.filtered_out += outcome.dropped_count
        for reason, count in outcome.reasons().items():
            result.filter_reasons[reason] = result.filter_reasons.get(reason, 0) + count

    _ingest_items(
        kept,
        ledger,
        s,
        result,
        extract=extract,
        download_images=download_images,
        max_images=max_images,
        download_videos=download_videos,
        refetch=refetch,
        by_source=by_source,
        profile=prof,
    )

    logger.info("入库完成：%s", result.summary())
    return result


def intake_urls(
    urls: list[str] | str,
    *,
    settings: Settings | None = None,
    via: SourceKind = SourceKind.GUI,
    source_id: str = "user",
    download_images: bool = True,
    max_images: int | None = None,
    download_videos: bool = True,
    refetch: bool = False,
) -> IntakeResult:
    """
    直接入库指定的链接 / Ingest specific URLs directly.

    支持传一整段文本：会从中提取所有链接。用户投递的内容往往是
    「这篇不错 https://… 」这种夹带形式。
    A whole block of text is accepted and every link inside it is extracted, since user
    submissions usually look like "this one is good <link>" rather than a bare URL.

    **不经过订阅源的过滤规则**——用户手动指定的链接就是明确想要的，
    再拿关键词去筛会很违反直觉。
    Source filtering is deliberately skipped: a link the user picked by hand is one they
    explicitly want, and screening it against keywords would be surprising.
    """
    s = settings or get_settings()
    ledger = Ledger(s.db_file)

    text = urls if isinstance(urls, str) else "\n".join(urls)
    items = items_from_text(text, source_id=source_id, via=via)

    result = IntakeResult(collected=len(items))
    _ingest_items(
        items,
        ledger,
        s,
        result,
        extract=True,
        download_images=download_images,
        max_images=max_images,
        download_videos=download_videos,
        refetch=refetch,
        profile=safe_profile(),
    )

    logger.info("链接入库完成：%s", result.summary())
    return result


def refetch_article(
    article_id: str,
    *,
    settings: Settings | None = None,
    download_images: bool = True,
    max_images: int | None = None,
    download_videos: bool = True,
) -> ArticleRecord | None:
    """
    重新抓取一篇已在台账里的文章 / Re-fetch an article already in the ledger.

    用于抓取失败后重试，或站点内容更新后刷新。
    For retrying after a failure, or refreshing when the site has updated the article.
    """
    s = settings or get_settings()
    ledger = Ledger(s.db_file)

    record = ledger.get(article_id)
    if record is None:
        return None

    # 用 feed_title 而不是 title 作为兜底：title 可能已被上一次降级抽取污染成
    # 站点通用名（见 issues/004），而 feed_title 始终是源发布时的原始标题。
    # feed_title, not title, is the fallback: a previous degraded extraction may have
    # overwritten title with the site's generic name (issues/004), whereas feed_title
    # always holds what the source originally published.
    item = RawItem(
        source_id=record.source_id,
        via=SourceKind(record.via) if record.via else SourceKind.GUI,
        url=record.url,
        title=record.feed_title or record.title,
    )
    result = IntakeResult()
    _ingest_items(
        [item],
        ledger,
        s,
        result,
        extract=True,
        download_images=download_images,
        max_images=max_images,
        download_videos=download_videos,
        refetch=True,
        by_source={c.id: c for c in _select_configs(None)},
        profile=safe_profile(),
    )
    return ledger.get(article_id)


def sync_manual_body(
    article_id: str,
    *,
    settings: Settings | None = None,
) -> tuple[ArticleRecord | None, int]:
    """
    把人工补写的正文回写进台账与 meta.json / Sync a hand-written body back into the store.

    知乎、小红书这类站点抓不到正文，正文由人工粘进 `article.md`。若不回写，
    台账里这篇永远是「0 字 / degraded」，后续挑文章做日报时会被当成没内容而跳过——
    人辛苦补的正文等于白补。
    Sites like Zhihu cannot be fetched, so the body is pasted into `article.md` by hand.
    Without syncing, the ledger keeps showing "0 chars / degraded" and every later step
    that picks articles for the digest skips it as empty, wasting the manual work.

    `meta.json` 也要更新：它才是后续流程实际读取的结构化数据。
    `meta.json` is updated too — it is the structured form later stages actually read.

    返回 / Returns:
        (更新后的台账记录, 正文字数)；文章不存在或未落盘时返回 (None, 0)
    """
    s = settings or get_settings()
    ledger = Ledger(s.db_file)

    record = ledger.get(article_id)
    if record is None or not record.store_dir:
        return None, 0

    directory = s.output_path / record.store_dir
    article_path = directory / "article.md"
    body = read_body(article_path)
    if not body:
        return record, 0

    # 标题也一并读回：抓取失败的文章标题是从 URL 推的，人工补正文时通常顺手改对了
    # The title is read back too: a failed fetch's title comes from the URL, and whoever
    # pasted the body has usually corrected it.
    title = read_title(article_path)

    # meta.json 是权威的结构化副本，正文必须同步进去
    meta_path = directory / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["text"] = body
        meta["extraction_ok"] = True
        if title:
            meta["title"] = title
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    ledger.set_body(article_id, body, title=title)
    logger.info("已回写人工正文：%s（%d 字）", article_id[:8], len(body))
    return ledger.get(article_id), len(body)


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _ingest_items(
    items: list[RawItem],
    ledger: Ledger,
    settings: Settings,
    result: IntakeResult,
    *,
    extract: bool,
    by_source: dict[str, SourceConfig] | None = None,
    profile: Profile | None = None,
    download_images: bool,
    max_images: int | None,
    download_videos: bool,
    refetch: bool,
) -> None:
    """
    逐条登记、抓取、落盘 / Register, fetch and persist each item in turn.

    **逐条隔离**：任何一篇失败都记进台账并继续，不影响其余文章。
    Per-item isolation: a failure is recorded in the ledger and the run continues.
    """
    for item in items:
        article_id, is_new = ledger.register(item)

        if not is_new and not refetch:
            existing = ledger.get(article_id)
            if existing is not None and existing.status is not FetchStatus.PENDING:
                result.skipped_existing += 1
                continue

        result.article_ids.append(article_id)

        if not extract:
            continue

        try:
            article = fetch_article(item.url, fallback_title=item.title)
        except Exception as exc:  # noqa: BLE001 - 单篇失败不能中断整轮
            logger.warning("抓取失败 %s：%s", item.url, exc)
            ledger.record_failure(article_id, str(exc))
            result.failed += 1
            continue

        saved = None
        try:
            saved = save_article(
                article,
                article_id,
                root=settings.output_path,
                download_images=download_images,
                max_images=_resolve_max_images(item, max_images, by_source, profile),
                download_videos_too=download_videos,
                max_videos=_resolve_max_videos(item, by_source, profile),
            )
        except OSError as exc:
            logger.warning("落盘失败 %s：%s", item.url, exc)
            ledger.record_failure(article_id, f"落盘失败：{exc}")
            result.failed += 1
            continue

        status = ledger.record_fetch(
            article_id,
            article,
            store_dir=saved.directory.relative_to(settings.output_path).as_posix(),
            image_count=saved.image_count,
            video_count=saved.video_count,
        )
        if status is FetchStatus.OK:
            result.fetched_ok += 1
        else:
            result.fetched_degraded += 1


def _resolve_max_images(
    item: RawItem,
    override: int | None,
    by_source: dict[str, SourceConfig] | None,
    profile: Profile | None,
) -> int:
    """
    定出这一条用多少张图的上限 / Work out this item's image cap.

    优先级：**命令行显式指定 > 源级配置 > profile 全局 > 代码默认**。
    命令行排最前是因为它是「就这一次，我知道自己在做什么」的表达；
    源级排在全局前面，是因为 arXiv 摘要页根本没有配图、而微信长文可能有二十几张，
    一个全局值伺候所有源要么浪费带宽要么漏素材。
    Precedence: an explicit command-line value, then the per-source setting, then the
    profile-wide one, then the code default. The command line wins because it expresses
    "just this once, deliberately"; per-source beats global because an arXiv abstract has
    no images while a long WeChat post may have twenty, and one number for both either
    wastes bandwidth or misses material.
    """
    if override is not None:
        return override

    config = (by_source or {}).get(item.source_id)
    if config is not None and config.max_images is not None:
        return config.max_images

    if profile is not None:
        return profile.max_images_per_article

    return DEFAULT_MAX_IMAGES


def _resolve_max_videos(
    item: RawItem,
    by_source: dict[str, SourceConfig] | None,
    profile: Profile | None,
) -> int:
    """定出这一条用多少个视频的上限 / Work out this item's video cap."""
    config = (by_source or {}).get(item.source_id)
    if config is not None and config.max_videos is not None:
        return config.max_videos
    if profile is not None:
        return profile.max_videos_per_article
    return DEFAULT_MAX_VIDEOS


def _select_configs(source_ids: list[str] | None) -> list[SourceConfig]:
    """选出要处理的源配置 / Pick the source configurations to process."""
    from dna.core.config import load_sources

    configs = load_sources()
    if source_ids:
        wanted = set(source_ids)
        configs = [c for c in configs if c.id in wanted]
    return configs


def _dummy_config(source_id: str) -> SourceConfig:
    """给未在配置里的源造一个占位配置 / A placeholder config for an unconfigured source."""
    return SourceConfig(id=source_id, name=source_id, url="")




__all__ = [
    "IntakeResult",
    "intake_sources",
    "intake_urls",
    "refetch_article",
    "sync_manual_body",
]
