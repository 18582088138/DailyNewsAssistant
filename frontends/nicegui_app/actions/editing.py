"""工作台就地编辑的后端动作 / Backend actions behind in-place editing。"""

from __future__ import annotations

from nicegui import run

from dna.core.models import Article
from dna.produce import ProductionKind, is_editable, save_production_text, skeleton_for
from dna.produce.tasks import normalize_lang
from dna.store.custom_article import save_body, save_title
from dna.store.ledger import ArticleRecord

# 文章自身的字段 → store 里的写回函数 / an article field → the store call that writes it
_ARTICLE_FIELDS = {
    "title": save_title,
    "body": save_body,
}


def production_editable(kind: ProductionKind | str, lang: str = "") -> bool:
    """这一格的文案能不能双击编辑 / Whether this cell's text can be edited in place。"""
    lang = normalize_lang(lang)
    return is_editable(kind, lang)


def skeleton_text(
    record: ArticleRecord, kind: ProductionKind | str, lang: str = ""
) -> str:
    """
    还没生成时给用户手写的空稿骨架 / The empty script offered before anything is generated。

    返回**空串**表示这一类没有骨架（音频、长视频），调用方应退回原来的「还没有生成」提示。
    骨架按台账里的标题与 URL 生成，所以它就是表格上那一行的口径。
    An empty string means this kind gets no skeleton, and the caller falls back to the plain
    note. The title and URL come from the ledger row, so the skeleton matches the table.
    """
    lang = normalize_lang(lang)
    return skeleton_for(Article(url=record.url, title=record.title), kind, lang)


async def save_production(
    article_id: str, kind: ProductionKind | str, text: str, *, lang: str = ""
) -> int:
    """写回人工编辑的整份文案；零 LLM、零费用 / Write an edited production back。"""
    lang = normalize_lang(lang)

    def _work() -> int:
        return save_production_text(article_id, kind, text, lang=lang)

    return await run.io_bound(_work)


async def save_article_field(article_id: str, field: str, value: str) -> None:
    """
    写回文章自己的一个字段（标题 / 正文）/ Write back one of the article's own fields。

    未登记的字段名直接拒绝，而不是静默什么都不做——静默的话界面会弹「已保存」，
    而盘上的文件一个字节都没变。
    An unregistered field name is rejected rather than silently ignored: ignoring it would
    show "saved" while nothing on disk changed.
    """
    writer = _ARTICLE_FIELDS.get(field)
    if writer is None:
        raise ValueError(f"未知的文章字段：{field}")

    await run.io_bound(lambda: writer(article_id, value))


__all__ = [
    "production_editable",
    "save_article_field",
    "save_production",
    "skeleton_text",
]

