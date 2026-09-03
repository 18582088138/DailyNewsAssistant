"""
文章台账 / Article ledger.

这是「总表」：记录每一篇被采集到的文章、它的抓取状态、落盘位置。
This is the master table: every collected article, its fetch status and where it landed.

它承担三件事 / It serves three purposes:
    1. **可验证**——抓了什么、成功没有、正文多长、几张图，一览无余
       Verifiability: what was fetched, whether it worked, how long the body is, how
       many images — all visible at a glance.
    2. **跨日去重**——同一篇文章第二天再出现在 feed 里时不再重复处理
       Cross-day de-duplication: an article reappearing in a feed tomorrow is not
       processed twice.
    3. **后续功能的选取依据**——从表里挑几篇做日报、挑一篇做口播稿或视频、
       或指定重抓。后续阶段的产出记录（productions 表）会挂在这张表上。
       The selection basis for later features: pick entries for a digest, pick one for
       a voice-over or video, or force a re-fetch. The productions table added later
       hangs off this one.

状态 / Status values:
    pending   已采集到，尚未抓正文
    ok        抓取成功
    degraded  抓取降级（仅标题 + 链接，正文抽不出来）
    failed    抓取失败（网络错误等）
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from dna.core.logging import get_logger
from dna.core.models import Article, MediaKind, RawItem
from dna.core.urls import canonicalize_url, url_hash
from dna.store.db import open_db

logger = get_logger("store.ledger")


class FetchStatus(StrEnum):
    """抓取状态 / Fetch status."""

    PENDING = "pending"
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True)
class ArticleRecord:
    """台账里的一行 / One row of the ledger."""

    id: str
    url: str
    canonical_url: str
    title: str
    feed_title: str
    source_id: str
    via: str
    author: str | None
    published_at: datetime | None
    first_seen_at: datetime
    fetched_at: datetime | None
    status: FetchStatus
    text_len: int
    image_count: int
    video_count: int
    store_dir: str | None
    error: str | None
    tags: list[str]
    fetch_count: int

    @property
    def is_usable(self) -> bool:
        """是否可用于生成内容 / Whether this article can feed content generation."""
        return self.status in (FetchStatus.OK, FetchStatus.DEGRADED)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ArticleRecord:
        """由数据库行构造 / Build from a database row."""
        return cls(
            id=row["id"],
            url=row["url"],
            canonical_url=row["canonical_url"],
            title=row["title"],
            feed_title=row["feed_title"],
            source_id=row["source_id"],
            via=row["via"],
            author=row["author"],
            published_at=_parse_dt(row["published_at"]),
            first_seen_at=_parse_dt(row["first_seen_at"]) or datetime.now(),
            fetched_at=_parse_dt(row["fetched_at"]),
            status=FetchStatus(row["status"]),
            text_len=row["text_len"],
            image_count=row["image_count"],
            video_count=row["video_count"],
            store_dir=row["store_dir"],
            error=row["error"],
            tags=[t for t in (row["tags"] or "").split(",") if t],
            fetch_count=row["fetch_count"],
        )


class Ledger:
    """
    文章台账的读写接口 / Read/write interface of the article ledger.

    每个方法自开自关连接。这里的写入频率很低（一天一轮采集），
    不值得为此引入连接池或长连接带来的生命周期问题。
    Each method opens and closes its own connection. Writes are infrequent — one
    collection round per day — so a pool or long-lived connection would add lifecycle
    problems without buying anything.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    # -- 写入 / writes --------------------------------------------------------

    def register(self, item: RawItem, *, now: datetime | None = None) -> tuple[str, bool]:
        """
        登记一条采集到的条目 / Register a collected item.

        返回 / Returns:
            (article_id, 是否首次出现)

        **跨日去重的落点**：同一篇文章明天再出现在 feed 里，这里会返回 is_new=False，
        调用方据此跳过重复抓取。判重按规范化 URL，因此带不同追踪参数的链接算同一篇。
        This is where cross-day de-duplication happens: an article reappearing tomorrow
        returns is_new=False so the caller can skip re-fetching. Matching is by
        canonical URL, so links differing only in tracking parameters count as one.
        """
        article_id = url_hash(item.url)
        canonical = canonicalize_url(item.url)
        moment = now or datetime.now()

        with open_db(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id, title FROM articles WHERE id = ?", (article_id,)
            ).fetchone()

            if existing is not None:
                if item.title:
                    # feed_title 每次都以源为准刷新：源才是标题的权威出处，
                    # 而 title 可能已被降级抽取污染成站点通用名（issues/004）。
                    # 这样被污染的历史数据会在下一轮采集时自愈。
                    # feed_title is refreshed from the source every time: the source is
                    # authoritative for it, whereas title may have been overwritten by a
                    # degraded extraction (issues/004). Corrupted rows self-heal on the
                    # next collection round.
                    if not existing["title"]:
                        # 用户投递的链接初始没有标题，这时一并补上显示标题
                        conn.execute(
                            "UPDATE articles SET title = ?, feed_title = ? WHERE id = ?",
                            (item.title, item.title, article_id),
                        )
                    else:
                        conn.execute(
                            "UPDATE articles SET feed_title = ? WHERE id = ?",
                            (item.title, article_id),
                        )
                return article_id, False

            conn.execute(
                """
                INSERT INTO articles (
                    id, url, canonical_url, title, feed_title, source_id, via,
                    published_at, first_seen_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article_id,
                    item.url,
                    canonical,
                    item.title,
                    item.title,
                    item.source_id,
                    str(item.via),
                    _fmt_dt(item.published_at),
                    _fmt_dt(moment),
                    FetchStatus.PENDING.value,
                ),
            )
        return article_id, True

    def record_fetch(
        self,
        article_id: str,
        article: Article,
        *,
        store_dir: str | None = None,
        image_count: int = 0,
        video_count: int | None = None,
        now: datetime | None = None,
    ) -> FetchStatus:
        """
        记录一次成功的抓取 / Record a successful fetch.

        `extraction_ok=False` 记为 degraded 而非 failed：正文没抽出来，
        但标题和链接仍然可用，这条内容依然能进日报。
        `extraction_ok=False` is recorded as degraded rather than failed: the body is
        missing but the title and link remain usable, so the item can still appear in
        the digest.
        """
        status = FetchStatus.OK if article.extraction_ok else FetchStatus.DEGRADED

        # 调用方传入的是**实际下载成功**的数量；没传才退回统计抽取到的链接数。
        # 两者含义不同：抽到 3 个视频链接但一个都没下下来，台账写 3 会误导。
        # The caller passes how many were actually downloaded; only without that does
        # this fall back to counting extracted links. Recording 3 when three links were
        # found but none downloaded would be misleading.
        if video_count is None:
            video_count = sum(1 for m in article.media if m.kind is MediaKind.VIDEO)

        with open_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE articles SET
                    title = ?, author = ?, published_at = COALESCE(?, published_at),
                    fetched_at = ?, status = ?, text_len = ?,
                    image_count = ?, video_count = ?, store_dir = ?,
                    error = NULL, fetch_count = fetch_count + 1
                WHERE id = ?
                """,
                (
                    article.title,
                    article.author,
                    _fmt_dt(article.published_at),
                    _fmt_dt(now or datetime.now()),
                    status.value,
                    len(article.text),
                    image_count,
                    video_count,
                    store_dir,
                    article_id,
                ),
            )
        return status

    def set_body(
        self,
        article_id: str,
        body: str,
        *,
        title: str = "",
        now: datetime | None = None,
    ) -> None:
        """
        记录人工补写的正文 / Record a body that was filled in by hand.

        状态直接升到 `ok`：正文的来源是人，而不是抽取器，它比任何自动抽取都可靠。
        不升级状态的话，这篇会一直被当作「没内容」而排除在日报之外。
        The status goes straight to `ok`: the text came from a person rather than the
        extractor, making it more reliable than any automatic result. Leaving the status
        alone would keep the article excluded from the digest as "empty".
        """
        with open_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE articles SET
                    status = ?, text_len = ?, error = NULL, fetched_at = ?,
                    title = COALESCE(NULLIF(?, ''), title)
                WHERE id = ?
                """,
                (
                    FetchStatus.OK.value,
                    len(body),
                    _fmt_dt(now or datetime.now()),
                    title.strip(),
                    article_id,
                ),
            )

    def record_failure(self, article_id: str, error: str, *, now: datetime | None = None) -> None:
        """记录一次抓取失败 / Record a failed fetch."""
        with open_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE articles SET
                    status = ?, error = ?, fetched_at = ?, fetch_count = fetch_count + 1
                WHERE id = ?
                """,
                (FetchStatus.FAILED.value, error[:500], _fmt_dt(now or datetime.now()), article_id),
            )

    def set_tags(self, article_id: str, tags: Sequence[str]) -> None:
        """设置标签 / Set the tags of an article."""
        with open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE articles SET tags = ? WHERE id = ?",
                (",".join(dict.fromkeys(t.strip() for t in tags if t.strip())), article_id),
            )

    # -- 读取 / reads ---------------------------------------------------------

    def get(self, article_id: str) -> ArticleRecord | None:
        """按 id 取一条 / Fetch one record by id."""
        with open_db(self.db_path) as conn:
            row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        return ArticleRecord.from_row(row) if row else None

    def find_by_url(self, url: str) -> ArticleRecord | None:
        """按 URL 取一条（规范化后比对）/ Fetch one record by URL, matched canonically."""
        with open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM articles WHERE canonical_url = ?", (canonicalize_url(url),)
            ).fetchone()
        return ArticleRecord.from_row(row) if row else None

    def list(
        self,
        *,
        status: FetchStatus | None = None,
        source_id: str | None = None,
        search: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ArticleRecord]:
        """
        按条件列出文章 / List articles matching the given filters.

        默认按首次出现时间倒序——最新采集到的排在最前，符合查看习惯。
        Ordered by first-seen time descending by default: the newest arrivals come
        first, which is how one actually reads such a list.
        """
        clauses: list[str] = []
        params: list[object] = []

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if search:
            clauses.append("(title LIKE ? OR url LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if since is not None:
            clauses.append("first_seen_at >= ?")
            params.append(_fmt_dt(since))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])

        with open_db(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM articles {where} ORDER BY first_seen_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [ArticleRecord.from_row(r) for r in rows]

    def count_by_status(self) -> dict[str, int]:
        """按状态统计 / Count articles grouped by status."""
        with open_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM articles GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def count_by_source(self) -> dict[str, int]:
        """按来源统计 / Count articles grouped by source."""
        with open_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT source_id, COUNT(*) AS n FROM articles"
                " GROUP BY source_id ORDER BY n DESC"
            ).fetchall()
        return {r["source_id"]: r["n"] for r in rows}

    def total(self) -> int:
        """总条数 / Total number of articles."""
        with open_db(self.db_path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0])

    def pending_ids(self, limit: int = 100) -> list[str]:
        """待抓正文的文章 id / Ids of articles whose body has not been fetched yet."""
        with open_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id FROM articles WHERE status = ? ORDER BY first_seen_at LIMIT ?",
                (FetchStatus.PENDING.value, limit),
            ).fetchall()
        return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# 时间格式 / datetime formatting
# ---------------------------------------------------------------------------


def _fmt_dt(value: datetime | None) -> str | None:
    """
    datetime → ISO 字符串 / Format a datetime as ISO 8601.

    SQLite 没有原生日期类型，存 ISO 字符串既可读又能直接用字符串比较排序。
    SQLite has no native date type; ISO strings stay readable and sort correctly under
    plain string comparison.
    """
    return value.isoformat(timespec="seconds") if value else None


def _parse_dt(value: str | None) -> datetime | None:
    """ISO 字符串 → datetime / Parse an ISO 8601 string."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


__all__ = ["ArticleRecord", "FetchStatus", "Ledger"]
