"""
SQLite 连接与建表 / SQLite connection and schema.

用标准库 `sqlite3` 而不是 ORM：本项目的查询都很简单（按状态/来源/日期筛选、计数），
引入 ORM 只会增加一层需要理解的东西，而换不来什么。
The standard-library `sqlite3` is used rather than an ORM: every query here is simple
(filter by status, source or date, plus counts), so an ORM would add a layer to
understand without buying anything.

版本管理用 `PRAGMA user_version`：后续加字段时按版本号逐级迁移，
不需要用户删库重来——台账里积累的抓取历史是有价值的。
Schema versioning uses `PRAGMA user_version` so later columns can be migrated step by
step instead of asking the user to delete the database: the accumulated fetch history
is worth keeping.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from dna.core.errors import StoreError
from dna.core.logging import get_logger

logger = get_logger("store.db")

SCHEMA_VERSION = 2

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS articles (
    id            TEXT PRIMARY KEY,              -- url_hash，跨次运行稳定
    url           TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    source_id     TEXT NOT NULL DEFAULT '',
    via           TEXT NOT NULL DEFAULT '',
    author        TEXT,
    published_at  TEXT,                          -- ISO 8601，源未提供则为空
    first_seen_at TEXT NOT NULL,                 -- 首次出现在采集里的时间
    fetched_at    TEXT,                          -- 最近一次成功抓取正文的时间
    status        TEXT NOT NULL,                 -- pending|ok|degraded|failed
    text_len      INTEGER NOT NULL DEFAULT 0,
    image_count   INTEGER NOT NULL DEFAULT 0,
    video_count   INTEGER NOT NULL DEFAULT 0,
    store_dir     TEXT,                          -- 相对 data/ 的落盘目录
    error         TEXT,
    tags          TEXT NOT NULL DEFAULT '',
    fetch_count   INTEGER NOT NULL DEFAULT 0     -- 抓取次数，含重抓
);

CREATE INDEX IF NOT EXISTS idx_articles_status   ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_source   ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_seen     ON articles(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_articles_canonical ON articles(canonical_url);
"""

# v2：保留源自带的标题，永不被抽取结果覆盖
# v2: keep the title as the source published it, never overwritten by extraction
_MIGRATE_V2 = """
ALTER TABLE articles ADD COLUMN feed_title TEXT NOT NULL DEFAULT '';
UPDATE articles SET feed_title = title WHERE feed_title = '';
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """
    打开数据库连接并确保表结构就绪 / Open the database and ensure the schema is present.

    抛出 / Raises:
        StoreError: 目录不可创建或数据库无法打开
    """
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
    except (OSError, sqlite3.Error) as exc:
        raise StoreError(f"无法打开台账数据库 {db_path}：{exc} / cannot open ledger") from exc

    # 按列名取值，读代码时不用数下标
    conn.row_factory = sqlite3.Row
    # 外键约束默认关闭，显式打开以便后续加关联表
    conn.execute("PRAGMA foreign_keys = ON")

    _migrate(conn)
    return conn


@contextmanager
def open_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    """
    以上下文管理器方式使用数据库 / Use the database as a context manager.

    正常退出时提交，异常时回滚——避免半条记录留在库里。
    Commits on clean exit and rolls back on error, so no half-written row survives.
    """
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """
    按版本号逐级迁移表结构 / Migrate the schema step by step by version number.

    目前只有 v1；后续加字段时在这里追加分支，不要直接改 _SCHEMA_V1，
    否则老库不会得到新列。
    Only v1 exists so far. Later columns are added as new branches here rather than by
    editing _SCHEMA_V1, which would leave existing databases without the new columns.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]

    if current > SCHEMA_VERSION:
        raise StoreError(
            f"台账数据库版本 v{current} 高于本程序支持的 v{SCHEMA_VERSION}，"
            f"请升级程序 / ledger schema is newer than this build supports"
        )

    if current == SCHEMA_VERSION:
        return

    if current == 0:
        conn.executescript(_SCHEMA_V1)
        current = 1

    if current == 1:
        # 老库补上 feed_title 列，并用现有标题回填——已被污染的那部分找不回来了，
        # 但至少从此以后源标题不会再丢。
        conn.executescript(_MIGRATE_V2)
        current = 2

    conn.execute(f"PRAGMA user_version = {current}")
    conn.commit()
    logger.debug("台账表结构已迁移到 v%d", current)


__all__ = ["SCHEMA_VERSION", "connect", "open_db"]
