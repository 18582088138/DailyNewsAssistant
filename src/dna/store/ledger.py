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

    def set_store_dir(self, article_id: str, store_dir: str) -> None:
        """
        改写落盘目录 / Rewrite where an article is stored.

        目录迁移时用。单拎出来是因为它不该顺带改动状态、抓取次数等任何其它字段——
        搬个位置不是一次「抓取」。
        Used by the layout migration. It is separate precisely so that relocating an
        article touches nothing else: moving a folder is not a fetch.
        """
        with open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE articles SET store_dir = ? WHERE id = ?", (store_dir, article_id)
            )

    def delete(self, article_id: str) -> int:
        """
        删除一篇文章及其全部产物记录 / Delete an article row and all its productions.

        返回删掉的产物条数 / Returns how many production rows went with it.

        **先删产物再删文章**：两张表之间没有外键约束（schema v1 起就没有），
        顺序反过来而中途失败的话，产物行会永远指向一个不存在的文章 id，
        既不显示也删不掉。
        Productions go first: there is no foreign key between the two tables, so the
        other order would leave orphan rows pointing at a vanished article — invisible
        in every view and impossible to remove.
        """
        with open_db(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM productions WHERE article_id = ?", (article_id,))
            removed = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
            conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        return removed

    def count_productions(self, article_id: str) -> int:
        """这篇文章有多少条产物记录 / How many production rows this article has."""
        with open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM productions WHERE article_id = ?", (article_id,)
            ).fetchone()
        return int(row[0]) if row else 0

    # -- 产物台账 / production ledger -----------------------------------------

    def record_production(
        self,
        article_id: str,
        kind: str,
        *,
        status: str = "ok",
        lang: str = "zh",
        variant: str | None = None,
        instructions: str | None = None,
        output_path: str | None = None,
        chars: int = 0,
        est_seconds: float | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        tokens: int = 0,
        calls: int = 1,
        duration_ms: int = 0,
        error: str | None = None,
        now: datetime | None = None,
    ) -> int:
        """
        记录一次产物生成 / Record one production run.

        **每次都插入新行**，并把 `redo_of_id` 指向上一版，而不是原地更新。
        原地更新会把上一版连同它的模型与时间一起抹掉，而「这段稿子是哪天用哪个模型
        写的」在内容出问题时是必须能回答的。
        A new row is always inserted, with `redo_of_id` pointing at the version it
        replaces. Updating in place would erase the previous version along with the model
        and timestamp behind it, and "which model wrote this, and when" has to stay
        answerable when the content turns out wrong.

        返回 / Returns:
            新插入行的 id
        """
        previous = self.latest_production(article_id, kind, lang)

        with open_db(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO productions (
                    article_id, kind, lang, variant, instructions, status, output_path,
                    chars, est_seconds, llm_provider, llm_model, tokens, calls,
                    duration_ms, error, created_at, redo_of_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article_id,
                    kind,
                    lang,
                    variant,
                    (instructions or "").strip() or None,
                    status,
                    output_path,
                    chars,
                    est_seconds,
                    llm_provider,
                    llm_model,
                    tokens,
                    calls,
                    duration_ms,
                    (error or "")[:500] or None,
                    _fmt_dt(now or datetime.now()),
                    previous.id if previous else None,
                ),
            )
            return int(cursor.lastrowid or 0)

    def latest_production(
        self, article_id: str, kind: str, lang: str = "zh"
    ) -> ProductionRecord | None:
        """
        取某篇某种产物、某个语言的最新一版 / The newest production of one kind and language.

        按 id 倒序而不是 created_at：同一秒内重做两次时时间戳会相同，
        而自增 id 永远能分出先后。
        Ordered by id rather than created_at: two redos within the same second share a
        timestamp, whereas the autoincrement id always disambiguates.

        **语言是查询条件的一部分。**漏掉它的话，生成过英文版之后再查中文版会拿到
        英文那一行——「已存在就跳过」于是拿英文版冒充中文版，一次都不会报错。
        Language is part of the key: omitting it would return the English row when the
        Chinese one is asked for, and the skip-if-exists guard would silently pass off one
        edition as the other.
        """
        with open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM productions WHERE article_id = ? AND kind = ? AND lang = ? "
                "ORDER BY id DESC LIMIT 1",
                (article_id, kind, lang),
            ).fetchone()
        return ProductionRecord.from_row(row) if row else None

    def production_matrix(
        self, article_ids: Sequence[str]
    ) -> dict[str, dict[tuple[str, str], ProductionRecord]]:
        """
        一次查出多篇文章的全部最新产物 / Latest productions for many articles at once.

        返回 `{article_id: {(kind, lang): record}}`。GUI 的表格一页几十行、
        每行四种产物两种语言，逐格查库会变成几百次查询，界面肉眼可见地卡。
        Returns a mapping keyed by kind and language. The table shows dozens of rows with
        four kinds in two languages each; querying per cell would mean hundreds of round
        trips and visible lag.
        """
        if not article_ids:
            return {}

        placeholders = ",".join("?" * len(article_ids))
        with open_db(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM productions
                WHERE id IN (
                    SELECT MAX(id) FROM productions
                    WHERE article_id IN ({placeholders})
                    GROUP BY article_id, kind, lang
                )
                """,
                tuple(article_ids),
            ).fetchall()

        matrix: dict[str, dict[tuple[str, str], ProductionRecord]] = {}
        for row in rows:
            record = ProductionRecord.from_row(row)
            matrix.setdefault(record.article_id, {})[(record.kind, record.lang)] = record
        return matrix

    def production_history(
        self, article_id: str, kind: str, lang: str = "zh"
    ) -> list[ProductionRecord]:
        """某篇某种产物某个语言的全部历史版本，最新在前 / Every version, newest first."""
        with open_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM productions WHERE article_id = ? AND kind = ? AND lang = ? "
                "ORDER BY id DESC",
                (article_id, kind, lang),
            ).fetchall()
        return [ProductionRecord.from_row(r) for r in rows]

    def count_productions_by_kind(self) -> dict[str, int]:
        """按产物类型统计（只算最新版）/ Count the latest production per kind."""
        with open_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT kind, COUNT(*) AS n FROM productions
                WHERE id IN (SELECT MAX(id) FROM productions GROUP BY article_id, kind, lang)
                  AND status = 'ok'
                GROUP BY kind
                """
            ).fetchall()
        return {r["kind"]: int(r["n"]) for r in rows}


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


__all__ = ["ArticleRecord", "FetchStatus", "Ledger", "ProductionRecord"]
