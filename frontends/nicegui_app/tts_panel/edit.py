"""每段的编辑动作：插标记、删段、重新拆条、回填 / Per-piece editing。"""

from __future__ import annotations

from frontends.nicegui_app.tts_panel.model import (
    _Panel,
    _Piece,
)

# --- 每段的动作 / the per-piece actions ----------------------------------------


def _insert(panel: _Panel, piece: _Piece, marker: str) -> None:
    """
    往这一段末尾插一个事件标记 / Append one event marker to this piece.

    和 TTS 图形界面同一个做法（那边也是追加到末尾）：光标位置在浏览器里，
    为了插在中间去写一段 JS 取 `selectionStart`，坏起来是「点了没反应」——
    而文本框本来就能手打，这条路更值得信。
    """
    box = piece.text_box
    box.set_value(f"{box.value or ''}{marker}")   # type: ignore[union-attr]
    panel.refresh()


def _remove(panel: _Panel, piece: _Piece) -> None:
    """删掉一段 / Drop one piece（连它已生成的波形一起）。"""
    if piece.container is not None:
        piece.container.delete()
    if piece in panel.pieces:
        panel.pieces.remove(piece)
    panel.refresh()





