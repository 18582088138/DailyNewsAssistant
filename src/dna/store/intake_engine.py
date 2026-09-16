"""
入库引擎 / The ingestion engine.

`intake.py` 四个公开入口共用的那一段：过滤 → 抓正文 → 落盘 → 记台账，
外加「这一条用多少张图/几个视频」的优先级解析。

**上限必须在抓取前就定下来**：抽取阶段会按它截候选图，之后再给大值也补不回来
（见 docs/issues/014）。优先级是 命令行 > 源级 > profile 全局 > 代码默认。
"""

from __future__ import annotations

from dna.core.config import Profile, Settings, SourceConfig
from dna.core.logging import get_logger
from dna.core.models import RawItem
from dna.extract.article import fetch_article
from dna.extract.media import DEFAULT_MAX_IMAGES
from dna.store.article_store import save_article
from dna.store.intake_models import IntakeProgressFn, IntakeResult
from dna.store.ledger import FetchStatus, Ledger
from dna.store.video_store import DEFAULT_MAX_VIDEOS

logger = get_logger("store.intake_engine")


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
    on_progress: IntakeProgressFn | None = None,
) -> None:
    """
    逐条登记、抓取、落盘 / Register, fetch and persist each item in turn.

    **逐条隔离**：任何一篇失败都记进台账并继续，不影响其余文章。
    Per-item isolation: a failure is recorded in the ledger and the run continues.
    """
    total = len(items)
    for index, item in enumerate(items, start=1):
        # 报的是**正在处理的那一篇**，不是刚做完的那一篇：抓一篇要好几秒，
        # 而人盯着进度条想知道的是「现在卡在谁身上」。
        # The item about to be fetched, not the one just finished: a fetch takes seconds
        # and the question being asked is "which one is it on now".
        if on_progress is not None:
            on_progress(index, total, item.title or item.url)

        article_id, is_new = ledger.register(item)

        if not is_new and not refetch:
            existing = ledger.get(article_id)
            if existing is not None and existing.status is not FetchStatus.PENDING:
                result.skipped_existing += 1
                continue

        result.article_ids.append(article_id)

        if not extract:
            continue

        # 上限在抓取时就要定下来：抽取阶段会按它截候选图，之后再给大值也补不回来
        image_cap = _resolve_max_images(item, max_images, by_source, profile)

        try:
            article = fetch_article(
                item.url, fallback_title=item.title, max_images=image_cap
            )
        except Exception as exc:
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
                max_images=image_cap,
                download_videos_too=download_videos,
                max_videos=_resolve_max_videos(item, by_source, profile),
            )
        except OSError as exc:
            logger.warning("落盘失败 %s：%s", item.url, exc)
            ledger.record_failure(article_id, f"落盘失败：{exc}")
            result.failed += 1
            continue

        # 视频抓不下来只提醒，不算失败——正文已经入库，这篇文章照样能用
        # A missing clip is a warning, not a failure: the article is in and usable.
        for url, reason in saved.skipped_videos:
            result.media_warnings.append((article_id, f"视频未下载（{url}）：{reason}"))

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


__all__ = [
    "_ingest_items",
    "_resolve_max_images",
    "_resolve_max_videos",
]
