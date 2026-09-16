"""
台账表格 / The workbench table.

一篇文章一行，每种产物一列。这是整个应用被用得最频繁的界面。
One row per article, one column per production kind.

三个布局决定 / Three layout decisions:

1. **用 CSS Grid，不用 flex。** 先前是 `flex + 固定宽度 + no-wrap`，窗口一窄，
   `flex-1` 的标题列被压到零宽以下，后面的固定宽度列就**叠在标题上**。
2. **表头 sticky。** 列宽只有一百来像素，翻到第 30 行时没有表头根本认不出
   「口播」和「短视频」。
3. **格子只负责打开内容，重做按钮在展开面板里。** 两个动作的代价完全不对等
   （一个免费、一个计费），不该挨在一起。

为什么状态直接印在格子里：「生成了」和「生成得对不对」是两回事。一段 47 秒的
短视频稿状态上是成功的，但它超了上限、发不出去。数字摆在表面，一眼扫得出
哪几篇要重做。

分在哪几个文件里 / Where things live:
    state   跨刷新保留的状态（展开哪一格、勾了哪几行、滚回哪一行）
    shell   容器与表头装配
    cells   各种格子
    run     从格子上发起生成（确认框 + 后台执行）
    batch   批量操作条
"""

from frontends.nicegui_app.ledger_table.batch import (
    render_batch_bar,
)
from frontends.nicegui_app.ledger_table.shell import (
    render_table,
)
from frontends.nicegui_app.ledger_table.state import (
    _HEAD_SHORT,
    _SELECTED,
    _STATUS_COLOUR,
    clear_selection,
    page_icon,
    remember_row,
    selected_ids,
)

# 带下划线的几个也导出：测试要直接摆状态（勾选跨刷新保留就是靠 _SELECTED），
# 不导出就得让测试 import 子模块，等于把拆法固化进测试。
__all__ = [
    "_HEAD_SHORT",
    "_SELECTED",
    "_STATUS_COLOUR",
    "clear_selection",
    "page_icon",
    "remember_row",
    "render_batch_bar",
    "render_table",
    "selected_ids",
]
