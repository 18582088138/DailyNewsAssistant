"""
productions 表的读写 / Reading and writing the productions table.

做成 **mixin** 被 `Ledger` 继承，不是独立的类：两张表共用同一个连接与同一个
事务边界（记一行产物要顺带更新文章行）。拆成两个对象就得把连接传来传去，
或者开两条连接 —— 后者在 SQLite 上会互相锁。
A mixin rather than a separate object: both tables share one connection and one
transaction boundary, and two connections would lock each other on SQLite.

表语义（为什么这么设计）见 `docs/07_db_schema.md`，这里不复述。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from dna.core.logging import get_logger
from dna.store.db import open_db
from dna.store.ledger_models import ProductionRecord, _fmt_dt

logger = get_logger("store.production_queries")


class ProductionQueries:
    """productions 表那一半 / The productions half of the ledger."""

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
