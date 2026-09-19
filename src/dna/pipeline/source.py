"""
台账 → 流水线的取数层 / Feeding the pipeline from the ledger.

从台账挑出要进入本期日报的文章，读回它们落盘的正文与媒体，转成 `NewsItem`。
Selects the articles for this issue from the ledger, reads back their persisted body and
media, and converts them into `NewsItem`s.

为什么正文从 `meta.json` 读而不是从数据库读 / Why the body comes from meta.json:
    台账只存正文长度，不存正文——几百篇文章的全文塞进 SQLite 会让这张「总表」
    变得又大又慢，而它的职责是**索引与状态**，不是内容仓库。
    内容在落盘目录里，`meta.json` 是可反序列化回 `Article` 的完整副本。
    The ledger stores the body's length, not the body: putting hundreds of full articles
    into SQLite would make the master table large and slow, while its job is indexing and
    status rather than content storage. The content lives in the article directory, where
    `meta.json` is a complete, deserialisable copy.

这也是「重做不用重抓」的落点：只要落盘目录还在，任何一期都能重新生成。
This is also what makes redoing an issue free of re-fetching: as long as the directory
survives, any issue can be regenerated.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from dna.core.config import Settings, get_settings
from dna.core.logging import get_logger
from dna.core.models import MediaAsset, NewsItem, SourceKind
from dna.pipeline.clean import to_news_item
from dna.store.ledger import ArticleRecord, FetchStatus, Ledger

logger = get_logger("pipeline.source")

# 默认回看几天 / how many days back to consider by default
# 日报是「今天的资讯」，但 feed 的发布时间常常滞后，只看当天会漏。
# The digest covers today's news, but feeds lag, so a one-day window would miss items.
DEFAULT_SINCE_DAYS = 2


def load_candidates(
    *,
    settings: Settings | None = None,
    since_days: int = DEFAULT_SINCE_DAYS,
    limit: int = 200,
    source_ids: list[str] | None = None,
    article_ids: list[str] | None = None,
) -> list[NewsItem]:
    """
    从台账取出候选条目 / Load candidate items from the ledger.

    参数 / Args:
        since_days:  回看天数；`article_ids` 指定时忽略
        limit:       最多取几条（打分排序之前的候选池上限）
        source_ids:  只取指定来源
        article_ids: 直接指定文章 id —— **人工挑选时走这条路**，
                     不受时间窗与来源过滤限制

    `article_ids` 是「在总表里勾选几篇做日报」这个需求的落点。
    `article_ids` is where "tick a few rows in the master table and build a digest from
    them" lands.
    """
    s = settings or get_settings()
    ledger = Ledger(s.db_file)

    if article_ids:
        records = [r for r in (ledger.get(i) for i in article_ids) if r is not None]
    else:
        records = ledger.list(
            since=datetime.now() - timedelta(days=since_days),
            limit=limit,
        )
        if source_ids:
            wanted = set(source_ids)
            records = [r for r in records if r.source_id in wanted]

    items: list[NewsItem] = []
    for record in records:
        # 抓取失败的没有任何可用内容，跳过；降级的只有标题，仍然要（见 clean 的说明）
        # Failed fetches carry nothing usable and are skipped; degraded ones keep their
        # title and are still wanted.
        if record.status is FetchStatus.FAILED or record.status is FetchStatus.PENDING:
            continue

        item = _to_item(record, s)
        if item is not None:
            items.append(item)

    logger.info("候选条目：台账 %d 条 → 可用 %d 条", len(records), len(items))
    return items


def _to_item(record: ArticleRecord, settings: Settings) -> NewsItem | None:
    """
    把一条台账记录转成 NewsItem / Turn one ledger row into a NewsItem.

    读不到落盘内容时**不放弃这条**，退回只用标题与链接——
    落盘目录可能被手工删过，但台账里的标题和链接仍然是有效资讯。
    A missing directory does not drop the row: it falls back to title and link, since the
    directory may have been deleted by hand while the ledger entry remains valid news.
    """
    body, media = _read_persisted(record, settings)

    return to_news_item(
        url=record.url,
        title=record.title,
        text=body,
        source_id=record.source_id,
        via=SourceKind(record.via) if record.via else SourceKind.RSS,
        published_at=record.published_at,
        media=media,
    )


def _read_persisted(
    record: ArticleRecord, settings: Settings
) -> tuple[str, list[MediaAsset]]:
    """
    读回落盘的正文与媒体 / Read the persisted body and media back.

    任何读取失败都退化成「空正文 + 无媒体」，不抛异常：内容读不到是遗憾，
    不是故障——标题和链接还在，这条仍然能进日报。
    Any read failure degrades to an empty body and no media rather than raising: missing
    content is a pity, not a fault, and the item still publishes on its title and link.
    """
    if not record.store_dir:
        return "", []

    meta_path = settings.output_path / record.store_dir / "meta.json"
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("读不到落盘内容，退回标题+链接：%s —— %s", record.id[:8], exc)
        return "", []

    body = data.get("text") or ""
    media: list[MediaAsset] = []
    for raw in data.get("media") or []:
        try:
            media.append(MediaAsset.model_validate(raw))
        except Exception as exc:
            # 单条媒体记录坏掉不该毁掉整篇正文；但要留个痕，否则「图少了」查不到原因
            logger.debug("跳过一条无法解析的媒体记录：%s", exc)
            continue

    return body, media


__all__ = ["DEFAULT_SINCE_DAYS", "load_candidates"]
