"""
展开面板里的就地编辑 / In-place editing inside the detail panel.

双击文案 → 变成文本框；点别处（失焦）→ 有改动才写回，没改动不落盘、不插台账行。
长文案的稿子按发言人分轮存在 `.json` 附件里，这里只读，提示去 TTS 操作台改。
"""

from __future__ import annotations

from nicegui import ui

from dna.produce import ProductionKind
from frontends.nicegui_app import actions

LONGFORM_HINT = "长文案只读：请在 TTS 操作台里改"


def render_body(article_id: str, kind: ProductionKind, lang: str, text: str, *, on_saved) -> None:
    """画出文案正文；可编辑的产物双击进入编辑 / Draw the body, editable on double-click。"""
    editable = actions.production_editable(kind, lang)
    state = {"text": text, "editing": False}
    box = ui.element("div").classes("wb-body w-full")

    def show() -> None:
        state["editing"] = False
        box.clear()
        with box:
            ui.markdown(state["text"] or "_（文件读不到，可能被移动或删除）_")
        if editable:
            box.classes(add="editable")
            box.tooltip("双击编辑，点别处自动保存")

    async def save(editor: ui.textarea) -> None:
        new = (editor.value or "").strip()
        if not new or new == state["text"].strip():
            show()
            return
        try:
            await actions.save_production(article_id, kind, new, lang=lang)
        except (OSError, ValueError) as error:
            ui.notify(f"保存失败：{error}", type="negative")
            show()
            return
        state["text"] = new
        show()
        ui.notify("已保存，字数与估算时长已更新", type="positive")
        on_saved()

    def edit() -> None:
        if state["editing"]:
            return
        state["editing"] = True
        box.clear()
        with box:
            editor = ui.textarea(value=state["text"]).props("autogrow outlined autofocus")
            editor.classes("w-full wb-editor")
            editor.on("blur", lambda: save(editor))

    show()
    if editable:
        box.on("dblclick", edit)
    elif text:
        ui.label(LONGFORM_HINT).classes("wb-hint")


__all__ = ["LONGFORM_HINT", "render_body"]
