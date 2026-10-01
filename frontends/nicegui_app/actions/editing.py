"""工作台就地编辑的后端动作 / Backend actions behind in-place editing。"""

from __future__ import annotations

from nicegui import run

from dna.produce import ProductionKind, is_editable, save_production_text
from dna.produce.tasks import normalize_lang


def production_editable(kind: ProductionKind | str, lang: str = "") -> bool:
    """这一格的文案能不能双击编辑 / Whether this cell's text can be edited in place。"""
    lang = normalize_lang(lang)
    return is_editable(kind, lang)


async def save_production(
    article_id: str, kind: ProductionKind | str, text: str, *, lang: str = ""
) -> int:
    """写回人工编辑的整份文案；零 LLM、零费用 / Write an edited production back。"""
    lang = normalize_lang(lang)

    def _work() -> int:
        return save_production_text(article_id, kind, text, lang=lang)

    return await run.io_bound(_work)


__all__ = ["production_editable", "save_production"]
