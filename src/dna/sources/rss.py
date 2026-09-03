"""
RSS / Atom 信息源 / RSS and Atom news source.

用 feedparser 解析，它对残缺的 feed 容忍度很高——真实世界的 RSS 经常缺日期、
缺链接、日期格式五花八门，严格解析器会直接罢工。
Parsing uses feedparser, which is deliberately lenient: real-world feeds routinely
omit dates or links and use a zoo of date formats that a strict parser would reject.

抓取与解析分离：`parse_feed()` 是纯函数，可以用本地样例离线测试。
Fetching and parsing are separate; `parse_feed()` is pure and testable offline.
"""

from __future__ import annotations

from datetime import datetime
from time import struct_time
from typing import Any

import feedparser

from dna.core.config import SourceConfig
from dna.core.errors import SourceError
from dna.core.logging import get_logger
from dna.core.models import RawItem, SourceKind
from dna.sources.base import SourceAdapter
from dna.sources.http import fetch_text, is_local_url

logger = get_logger("sources.rss")


class RSSAdapter(SourceAdapter):
    """标准 RSS / Atom 订阅源 / A standard RSS or Atom feed."""

    def fetch(self, limit: int | None = None) -> list[RawItem]:
        """抓取并解析该 feed / Fetch and parse the feed."""
        url = self.feed_url()
        logger.debug("抓取 RSS：%s", url)
        text = fetch_text(url, local=is_local_url(url))
        return parse_feed(text, self.config, limit=self.effective_limit(limit, 30))

    def feed_url(self) -> str:
        """feed 的完整地址 / The full feed URL."""
        return self.config.url


def parse_feed(
    content: str, config: SourceConfig, *, limit: int = 30, via: SourceKind | None = None
) -> list[RawItem]:
    """
    解析 feed 文本为 RawItem 列表 / Parse feed text into RawItem objects.

    纯函数：不发网络请求，因此可以直接喂本地样例做测试。
    A pure function — no network access — so it can be fed local samples in tests.

    容错策略 / Tolerance:
        - feedparser 标记 bozo（格式有瑕疵）但仍解析出条目时，**继续使用**并记一条日志。
          真实的 RSS 十有八九带瑕疵，因此报错就等于放弃大半信息源。
          A bozo flag with usable entries is accepted and logged: most real feeds are
          slightly malformed, so treating that as fatal would discard most sources.
        - 缺标题或缺链接的条目直接跳过——没有链接就无法抽正文，留着只会污染后续环节。
          Entries without a title or link are skipped: without a link the body cannot
          be extracted, so keeping them only pollutes later stages.
        - 合法但当前没有新条目的 feed 返回空列表，**不算错误**。
          A valid feed that currently has no items returns an empty list, not an error.

    抛出 / Raises:
        SourceError: 内容根本不是 feed（如站点返回了 HTML 错误页）
    """
    parsed = feedparser.parse(content)

    # 判别依据是 version 而不是 bozo：feedparser 认出 feed 格式时会给出
    # 'rss20' / 'atom10' 之类的值，认不出时是 '' 或 None。
    # **bozo 在这里不可靠**——实测站点返回 HTTP 200 + HTML 错误页时 bozo 为 False，
    # 若依赖 bozo，这种源会被静默记成「成功，0 条」，日报悄悄变短却没有任何失败记录。
    # The discriminator is `version`, not `bozo`: feedparser reports 'rss20' / 'atom10'
    # when it recognises a feed and '' or None when it does not. `bozo` is unreliable
    # here — a site returning HTTP 200 with an HTML error page yields bozo=False, so
    # relying on it would silently record the source as "succeeded with 0 items" and
    # quietly shrink the digest with nothing logged.
    if not parsed.get("version"):
        reason = parsed.get("bozo_exception") or "内容不是 RSS/Atom（可能是 HTML 错误页）"
        raise SourceError(f"feed 无法解析：{config.id} —— {reason} / unparseable feed {config.id}")

    if parsed.bozo:
        logger.debug("feed %s 格式有瑕疵但可用：%s", config.id, parsed.get("bozo_exception"))

    items: list[RawItem] = []
    skipped = 0

    for entry in parsed.entries[: max(limit, 0)]:
        link = _first_str(entry, "link", "id")
        title = _clean(_first_str(entry, "title"))

        if not link or not title:
            skipped += 1
            continue

        items.append(
            RawItem(
                source_id=config.id,
                via=via or config.kind,
                url=link,
                title=title,
                summary_raw=_clean(_first_str(entry, "summary", "description")),
                published_at=_parse_published(entry),
            )
        )

    if skipped:
        logger.debug("feed %s 跳过 %d 条缺标题或缺链接的条目", config.id, skipped)

    return items


def _first_str(entry: Any, *keys: str) -> str:
    """按顺序取第一个非空字符串字段 / First non-empty string field among the keys."""
    for key in keys:
        value = entry.get(key) if hasattr(entry, "get") else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _clean(text: str) -> str:
    """
    压平空白 / Collapse whitespace.

    feed 里的标题常带换行和连续空格，直接用会让标题 slug 和日报排版都变难看。
    Feed titles often carry newlines and runs of spaces, which would otherwise show
    up in slugs and in the rendered digest.
    """
    return " ".join(text.split()) if text else ""


def _parse_published(entry: Any) -> datetime | None:
    """
    取发布时间 / Extract the publication time.

    feedparser 已经把各种日期格式统一成 struct_time，这里只做转换。
    取不到时返回 None，而不是回填「现在」——回填会让排序与跨日去重失真。
    feedparser already normalises the many date formats into struct_time. When no
    date is available this returns None rather than substituting "now", which would
    distort ordering and cross-day de-duplication.
    """
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key) if hasattr(entry, "get") else None
        if isinstance(value, struct_time):
            try:
                return datetime(*value[:6])
            except ValueError:
                continue
    return None


__all__ = ["RSSAdapter", "parse_feed"]
