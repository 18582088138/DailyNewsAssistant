"""
台账的行映射与查询片段 / Row mappings and the shared WHERE builder.

**没有任何 SQL 执行**，所以 `ledger.py` 与 `production_queries.py` 都能 import 它
而不产生环。`from_row` 放在数据类上而不是 Ledger 里：
「一行长什么样」是这个结构自己的知识。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from dna.core.logging import get_logger

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


@dataclass(frozen=True)
class ProductionRecord:
    """
    一次产物生成的记录 / One production run.

    记下 provider 与 model：内容出问题时要能回答「这段稿子是哪天用哪个模型写的」。
    换了模型之后旧产物质量参差不齐，没有这两列就只能全部重做。
    The provider and model are recorded so that "which model wrote this, and when" stays
    answerable. After a model change, old output is uneven in quality, and without these
    columns the only remedy would be redoing everything.
    """

    id: int
    article_id: str
    kind: str
    lang: str
    """输出语言 / the output language；见 `produce/tasks.py` 的「语言是维度，不是类型」。"""
    variant: str | None
    instructions: str | None
    """
    生成这一版时附带的额外要求 / the extra requirements this version was made with.

    重做时人会写「再专业一点」「加长到 40 秒」。记下来，下次重做就能从上次改过的
    地方接着调，而不是从零想一遍。
    Recorded so the next redo starts from what was asked last time.
    """
    status: str
    output_path: str | None
    chars: int
    est_seconds: float | None
    llm_provider: str | None
    llm_model: str | None
    tokens: int
    calls: int
    duration_ms: int
    error: str | None
    created_at: datetime | None
    redo_of_id: int | None

    @property
    def ok(self) -> bool:
        """是否生成成功 / Whether this run succeeded."""
        return self.status == "ok"

    @property
    def is_redo(self) -> bool:
        """是否是重做出来的版本 / Whether this version replaced an earlier one."""
        return self.redo_of_id is not None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ProductionRecord:
        """由数据库行构造 / Build from a database row."""
        return cls(
            id=row["id"],
            article_id=row["article_id"],
            kind=row["kind"],
            lang=row["lang"],
            variant=row["variant"],
            instructions=row["instructions"],
            status=row["status"],
            output_path=row["output_path"],
            chars=row["chars"],
            est_seconds=row["est_seconds"],
            llm_provider=row["llm_provider"],
            llm_model=row["llm_model"],
            tokens=row["tokens"],
            calls=row["calls"],
            duration_ms=row["duration_ms"],
            error=row["error"],
            created_at=_parse_dt(row["created_at"]),
            redo_of_id=row["redo_of_id"],
        )


def _where(
    *,
    status: FetchStatus | None = None,
    source_id: str | None = None,
    search: str | None = None,
    since: datetime | None = None,
) -> tuple[str, list[object]]:
    """
    拼出 `list()` 与 `count()` 共用的 WHERE / The WHERE shared by `list()` and `count()`.

    只有一份，因为「这一页的内容」和「一共多少条」必须出自同一组条件。
    One copy only: the page contents and the total must come from the same predicate.
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

    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params



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
