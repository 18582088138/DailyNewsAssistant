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

from dna.core.config import Profile, Settings, SourceConfig, get_settings, safe_profile
from dna.core.logging import get_logger
from dna.core.models import RawItem, SourceKind
from dna.sources.filters import FilterOutcome, apply_filters, build_filter
from dna.sources.registry import CollectResult, build_adapters, collect
from dna.sources.user_link import items_from_text
from dna.store.article_render import read_body, read_title
from dna.store.intake_engine import _ingest_items
from dna.store.intake_models import IntakeProgressFn, IntakeResult
from dna.store.ledger import ArticleRecord, Ledger

logger = get_logger("store.intake")


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
    max_age_days: int | None = None,
    on_progress: IntakeProgressFn | None = None,
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
        max_age_days:    只要最近这几天的条目，**覆盖每个源在 `sources.yaml` 里的设置**。
                         覆盖而不是改配置文件：YAML 里那一份是长期偏好，
                         这个参数是「这一次我只想要最近三天」的临时意图。
                         注意没有发布时间的条目**不受此限制**（见 `filters.should_keep`），
                         大量 feed 不给 pubDate，按「未知即旧」丢弃会误杀整个源。
                         Overrides each source's own setting for this run only. Items with
                         no published date are unaffected: many feeds omit it, and
                         treating unknown as old would silently drop whole sources.
        on_progress:     采集进度回调 `(第几篇, 共几篇, 标题)`，在每篇抓取**之前**触发
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
        rules = build_filter(config or _dummy_config(source_id), prof)
        if max_age_days is not None:
            rules = rules.model_copy(update={"max_age_days": max_age_days})
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
        on_progress=on_progress,
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
    on_progress: IntakeProgressFn | None = None,
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

    参数 / Args:
        on_progress: 采集进度回调 `(第几篇, 共几篇, 标题)`，在每篇抓取**之前**触发
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
        on_progress=on_progress,
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
    result: IntakeResult | None = None,
) -> ArticleRecord | None:
    """
    重新抓取一篇已在台账里的文章 / Re-fetch an article already in the ledger.

    用于抓取失败后重试，或站点内容更新后刷新。
    For retrying after a failure, or refreshing when the site has updated the article.

    参数 / Args:
        result: 传一个 `IntakeResult` 进来就能拿到这一篇的明细（媒体告警、成功/降级）。
            返回值仍是台账记录，绝大多数调用方只关心这个。
            Pass one in to receive the per-item detail (media warnings, ok/degraded);
            the return value stays the ledger record, which is all most callers want.
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
    outcome = result if result is not None else IntakeResult()
    outcome.collected += 1
    _ingest_items(
        [item],
        ledger,
        s,
        outcome,
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
