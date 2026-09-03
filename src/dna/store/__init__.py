"""
存储层 / Storage layer：文章落盘、台账数据库、入库流程。

    from dna.store import Ledger, FetchStatus, intake_sources, intake_urls

台账是「总表」：记录每篇文章的标题、链接、抓取状态与落盘位置，
既用于人工核对，也是后续「选文章做日报 / 口播 / 视频」的选取依据。
The ledger is the master table — titles, links, fetch status and storage locations —
used both for manual verification and as the selection basis for later features.
"""

from dna.store.article_store import (
    MANUAL_BODY_MARKER,
    SavedArticle,
    article_dir,
    read_body,
    read_title,
    save_article,
)
from dna.store.db import SCHEMA_VERSION, connect, open_db
from dna.store.intake import (
    IntakeResult,
    intake_sources,
    intake_urls,
    refetch_article,
    sync_manual_body,
)
from dna.store.ledger import ArticleRecord, FetchStatus, Ledger
from dna.store.video_store import download_videos, is_direct_video_url

__all__ = [
    "SCHEMA_VERSION",
    "ArticleRecord",
    "FetchStatus",
    "MANUAL_BODY_MARKER",
    "IntakeResult",
    "Ledger",
    "SavedArticle",
    "article_dir",
    "connect",
    "download_videos",
    "intake_sources",
    "intake_urls",
    "is_direct_video_url",
    "open_db",
    "read_body",
    "read_title",
    "refetch_article",
    "save_article",
    "sync_manual_body",
]
