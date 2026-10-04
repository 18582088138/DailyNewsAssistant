"""
人工创作文章的动作 / Actions behind hand-authored articles.

界面上的「新建」按钮、文章面板里的标题与正文编辑、以及「媒体」格的素材合规化都走这里。
**不含业务逻辑**：每个函数都只是把 `dna.store` 的函数丢进 `io_bound` 再回传结果，
真正的规则（id 怎么造、占位内容是什么、什么算合规）全在 store 层，命令行调的是同一批。
The New button, the article panel's title/body editing and the media cell's asset
normalisation all land here. No business logic: each function hands a `dna.store` call to
`io_bound` and returns the result, so the rules live in the store and the CLI shares them.
"""

from __future__ import annotations

from nicegui import run

from dna.store.custom_article import (
    article_text,
    create_custom_article,
    is_custom_record,
)
from dna.store.ledger import ArticleRecord
from dna.store.media_normalize import MediaReport, normalize_media

__all__ = [
    "article_body",
    "create_blank_article",
    "is_custom",
    "normalize_assets",
]


def is_custom(record: ArticleRecord) -> bool:
    """这篇是不是人工创作的 / Whether this article was hand-authored."""
    return is_custom_record(record)


def article_body(article_id: str) -> str:
    """
    读回文章正文，供面板显示 / Read the article body back for the panel.

    同步读本地一个小文件，不值得再往后台线程丢一次。
    A small local file read; a round trip through a worker thread would cost more than it
    saves.
    """
    _, body = article_text(article_id)
    return body


async def create_blank_article() -> ArticleRecord:
    """
    新建一篇空白文章 / Create one blank article.

    会**立刻落盘**（目录 + `article.md` + `meta.json`）。不落盘的行在界面上是废行：
    正文与媒体格都不可点，生成时第一道就报「还没有落盘目录」。
    Persisted immediately: a row without a directory is inert in the UI and is refused by
    the first guard in `produce()`.
    """
    return await run.io_bound(create_custom_article)


async def normalize_assets(article_id: str) -> MediaReport:
    """
    整理这一篇的素材文件夹 / Normalise this article's asset folders.

    零费用、不联网：只认文件头、改名、补出处边车。**不删除任何文件。**
    Free and offline: it reads magic bytes, renames and writes sidecars, and never deletes
    anything.
    """
    return await run.io_bound(lambda: normalize_media(article_id))
