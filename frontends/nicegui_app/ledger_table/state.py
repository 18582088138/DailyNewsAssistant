"""
表格的跨刷新状态 / State that survives a table rebuild.

展开的是哪一格、勾了哪几行、刚操作的是哪一行 —— 三样都必须**跨刷新保留**：
刷新会把整张表重建一遍，而生成完一份产物之后正是最想看新内容的时刻，
面板一关、勾选一清，看到的就是「点了一下，什么都没发生」。
All three survive a rebuild, or a successful run would look like nothing happened.
"""

from __future__ import annotations

from nicegui import ui

from dna.produce import ProductionKind
from frontends.nicegui_app.actions import RowView

_STATUS_COLOUR = {
    "ok": "positive",
    "degraded": "warning",
    "failed": "negative",
    "pending": "grey",
}

# 表头缩写：108px 放不下「短视频文案」四个字还带内边距
# Header abbreviations: 108 px cannot hold the full four-character labels with padding.
_HEAD_SHORT = {
    ProductionKind.SUMMARY: "总结",
    ProductionKind.SHORTVIDEO: "短视频",
    ProductionKind.NARRATION: "口播",
    ProductionKind.LONGFORM: "长文案",
}

# 展开状态跨刷新保留 / the open panel survives a refresh
#
# 刷新会把整张表重建一遍，展开的面板本来会跟着关掉。生成完一份产物之后正是
# **最想看新内容的时刻**，而面板一关，看到的就是「点了一下，什么都没发生」——
# 内容其实已经更新，只是被收起来了。这里记住 (文章 id, 产物类型)，重建后自动展开。
# A refresh rebuilds the table and would close the panel exactly when the new content is
# what the user wants to see, making a successful run look like nothing happened.
_OPEN: dict[str, tuple[str, str] | None] = {"cell": None}

# 勾选状态跨刷新保留 / the selection survives a refresh
#
# 和 `_OPEN` 同一个理由，但更要紧：批量重抓跑完会刷新表格，选中如果被清掉，
# 「重抓完看看这几篇好了没有」就要从头再勾一遍十篇。
# 用 dict 当有序集合：确认框里列出的标题顺序要和人勾的顺序一致，
# 而 `set` 的迭代顺序是哈希序，每次刷新都可能不同。
# An ordered set: the confirmation dialog lists titles in the order they were picked,
# whereas a `set` would reorder them on every refresh.
_SELECTED: dict[str, None] = {}

# 上一次动过的那一行 / the row that was last acted upon
#
# 和 `_OPEN` / `_SELECTED` 同一个做法、同一个理由。表格重建之后浏览器回到页首，
# 于是在第 30 行点了个「生成」，回来看到的是第 1 行——人得重新找一遍自己刚才在哪。
# 只在这一行**还在本页**时才滚；不在（翻了页、被筛掉）就停在页首，
# 硬滚到一个不存在的位置只会得到一次莫名其妙的跳动。
# A rebuild scrolls the browser back to the top, losing the user's place. Only scrolled
# when the anchor row is still on this page.
_ANCHOR: dict[str, str | None] = {"id": None}


def remember_row(article_id: str) -> None:
    """记下正在操作的这一行 / Remember the row being acted upon."""
    _ANCHOR["id"] = article_id


def selected_ids() -> list[str]:
    """当前勾选的文章 id，按勾选顺序 / The picked article ids, in pick order."""
    return list(_SELECTED)


def clear_selection() -> None:
    """清空勾选 / Drop the whole selection."""
    _SELECTED.clear()


class _Selection:
    """
    本页的勾选控件 / The page's selection widgets.

    选中的 id 存在模块级的 `_SELECTED` 里（跨刷新保留），这个对象只持有**本页**
    那些控件的引用，用来把三态表头和行高亮同步过来。
    The picked ids live in the module-level `_SELECTED`; this object holds only the
    current page's widgets so the tri-state header and the row highlights stay in sync.

    「全选本页」只作用于本页 / "Select all" means this page:
        筛选之后本页可能只有 5 行，而 `_SELECTED` 里还有上一次勾的 20 篇。
        表头反映的是本页，批量条上的数字反映的是全部——两者都要说实话。
        The header speaks for this page and the batch bar for the whole selection.
    """

    def __init__(self, rows: list[RowView], *, on_select) -> None:
        self.page_ids = [row.article.id for row in rows]
        self.on_select = on_select
        self.header: ui.element | None = None
        self.boxes: dict[str, tuple[ui.checkbox, ui.element]] = {}

    def toggle(self, article_id: str, picked: bool) -> None:
        if picked:
            _SELECTED[article_id] = None
        else:
            _SELECTED.pop(article_id, None)
        self.sync()
        self.on_select()

    def toggle_page(self) -> None:
        """全选本页 ↔ 取消本页 / Select or clear this page."""
        picked = not all(i in _SELECTED for i in self.page_ids)
        for article_id in self.page_ids:
            if picked:
                _SELECTED[article_id] = None
            else:
                _SELECTED.pop(article_id, None)
        for article_id, (box, _) in self.boxes.items():
            box.set_value(article_id in _SELECTED)
        self.sync()
        self.on_select()

    def sync(self) -> None:
        """把状态刷到表头图标与行高亮上 / Push state into the header icon and rows."""
        if self.header is not None:
            self.header.props(f"name={page_icon(self.page_ids, _SELECTED)}")
        for article_id, (_, wrapper) in self.boxes.items():
            wrapper.classes(
                **({"add": "is-picked"} if article_id in _SELECTED else {"remove": "is-picked"})
            )


def page_icon(page_ids: list[str], selected) -> str:
    """
    「全选本页」该显示哪个图标 / Which icon the select-all control shows.

    三态：全不选 / 部分选 / 全选。**中间那一态不能省**——省掉的话「本页有几篇
    已经被勾了」就完全看不出来，人会以为点一下是全选，结果是全部取消。
    The middle state cannot be dropped: without it there is no way to tell that some of
    this page is already picked, and a click that looks like "select all" clears instead.
    """
    picked = sum(1 for i in page_ids if i in selected)
    if picked == 0:
        return "check_box_outline_blank"
    return "check_box" if picked == len(page_ids) else "indeterminate_check_box"
