"""表格外壳：容器、表头装配、滚回原位 / The table shell。"""

from __future__ import annotations

from nicegui import ui

from frontends.nicegui_app.actions import RowView
from frontends.nicegui_app.ledger_table.cells import _render_header, _render_row
from frontends.nicegui_app.ledger_table.state import (
    _ANCHOR,
    _Selection,
)


def render_table(
    container: ui.element, rows: list[RowView], *, on_change, on_select=None
) -> None:
    """
    渲染整张表 / Render the whole table.

    参数 / Args:
        on_change: 产物变化后的回调，用于刷新表格
        on_select: 勾选变化后的回调。**只重画批量操作条，不重建表格**——
            重建一次要几百个元素，而勾一下只是改了一个数字。
            Redraws only the batch bar: a rebuild costs hundreds of elements while a
            tick changed a single number.
    """
    container.clear()

    with container:
        if not rows:
            with ui.column().classes("w-full items-center gap-2 p-10"):
                ui.icon("inbox").classes("text-4xl").style("color: var(--wb-faint)")
                ui.label("没有匹配的文章").style("color: var(--wb-dim)")
                ui.label("先跑 dna fetch 或 dna add <链接>").classes("wb-path")
            return

        picks = _Selection(rows, on_select=on_select or (lambda: None))
        with (
            ui.element("div").classes("wb-scroll w-full"),
            ui.element("div").classes("wb-table"),
        ):
            _render_header(picks)
            for row in rows:
                _render_row(row, on_change=on_change, picks=picks)
        picks.sync()
        _scroll_to_anchor(picks)


def _scroll_to_anchor(picks: _Selection) -> None:
    """
    滚回刚才操作的那一行 / Scroll back to the row just acted upon.

    `getHtmlElement` 是 NiceGUI 自带的 JS 全局（`static/nicegui.js`），按元素 id
    取 DOM 节点——比自己往元素上塞一个 id 属性再去查稳。
    A NiceGUI-provided JS global; steadier than attaching our own id attribute.
    """
    target = picks.boxes.get(_ANCHOR["id"] or "")
    if target is None:
        return
    ui.run_javascript(
        f"getHtmlElement({target[1].id})?.scrollIntoView"
        "({block:'center', behavior:'instant'})"
    )
