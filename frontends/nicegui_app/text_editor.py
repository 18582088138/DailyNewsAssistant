"""
展开面板里的就地编辑 / In-place editing inside the detail panel.

双击一段文本 → 变成文本框；点别处（失焦）→ **有改动才**写回，没改动不落盘、不插台账行。
长视频的稿子按发言人分轮存在 `.json` 附件里，这里只读，提示去 TTS 操作台改。
Double-click a block of text and it becomes a textarea; clicking away saves only when
something actually changed. The long-form script keeps its speaker turns in a JSON
sidecar, so it stays read-only here and points at the TTS console instead.

两处用途共用 `render_field` / Both callers share `render_field`:
    1. 产物正文（总结 / 短视频 / 中视频），写回走 `dna.produce.save_production_text`
    2. 文章自己的标题与正文（人工创作的文章主要靠它填内容），写回走 `dna.store`

    「双击编辑、点别处保存」是同一个动作，分成两份实现迟早只有一份被修好。
    The gesture is one gesture; two implementations would drift until only one gets fixed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from nicegui import ui

from dna.produce import ProductionKind
from frontends.nicegui_app import actions

LONGFORM_HINT = "长视频只读：请在 TTS 操作台里改"
EDIT_HINT = "双击编辑，点别处自动保存"
MISSING_FILE = "_（文件读不到，可能被移动或删除）_"

OnSave = Callable[[str], Awaitable[None]]


def render_field(
    value: str,
    *,
    on_save: OnSave,
    editable: bool = True,
    on_saved: Callable[[], None] | None = None,
    saved_message: str = "已保存",
    readonly_hint: str = "",
) -> None:
    """
    画一段可双击编辑的文本 / Draw a block of text that becomes editable on double-click.

    参数 / Args:
        on_save:      异步写回；抛 `OSError` / `ValueError` 时本函数把原因提示出来并回退显示
        on_saved:     写成功之后的回调（表格层用它刷新那一格的字数与估算时长）
        readonly_hint: 不可编辑时显示的提示；给句提示比让双击毫无反应要好
    """
    state = {"text": value, "editing": False}
    box = ui.element("div").classes("wb-body w-full")

    def show() -> None:
        state["editing"] = False
        box.clear()
        with box:
            ui.markdown(state["text"] or MISSING_FILE)
        if editable:
            box.classes(add="editable")
            box.tooltip(EDIT_HINT)

    async def save(editor: ui.textarea) -> None:
        new = (editor.value or "").strip()
        # 空内容与非改动都不写盘。失焦是极常见的动作，一次误触不该覆盖已有内容，
        # 也不该凭空往台账里插一行「人工编辑」。
        if not new or new == state["text"].strip():
            show()
            return
        try:
            await on_save(new)
        except (OSError, ValueError) as error:
            ui.notify(f"保存失败：{error}", type="negative")
            show()
            return
        state["text"] = new
        show()
        ui.notify(saved_message, type="positive")
        if on_saved is not None:
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
    elif readonly_hint and value:
        ui.label(readonly_hint).classes("wb-hint")


def render_body(article_id: str, kind: ProductionKind, lang: str, text: str, *, on_saved) -> None:
    """画出产物正文；可编辑的产物双击进入编辑 / Draw a production body, editable on double-click."""
    editable = actions.production_editable(kind, lang)

    async def save(new: str) -> None:
        await actions.save_production(article_id, kind, new, lang=lang)

    render_field(
        text,
        on_save=save,
        editable=editable,
        on_saved=on_saved,
        saved_message="已保存，字数与估算时长已更新",
        readonly_hint=LONGFORM_HINT,
    )


def render_article_field(
    article_id: str,
    field: str,
    value: str,
    *,
    on_saved,
    saved_message: str = "已保存",
) -> None:
    """
    画文章自己的一个字段（标题 / 正文）/ Draw one of the article's own fields.

    与产物正文分开，是因为写回的目标不同：产物写进 `produce/editing.py`（那里还要重算
    字数与估算时长），文章字段写进 `meta.json` 与 `article.md`（还要同步台账的标题与字数）。
    A separate entry point because the write target differs: productions go through
    `produce/editing.py`, article fields through the store's own metadata.
    """

    async def save(new: str) -> None:
        await actions.save_article_field(article_id, field, new)

    render_field(
        value,
        on_save=save,
        on_saved=on_saved,
        saved_message=saved_message,
    )


__all__ = [
    "EDIT_HINT",
    "LONGFORM_HINT",
    "MISSING_FILE",
    "render_article_field",
    "render_body",
    "render_field",
]
