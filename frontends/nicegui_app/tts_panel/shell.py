"""操作台入口：`open_panel` / The console entry point。"""

from __future__ import annotations

from nicegui import ui

from dna.produce import ProductionKind, spec
from dna.produce.tasks import normalize_lang
from frontends.nicegui_app import actions, theme
from frontends.nicegui_app.actions import RowView
from frontends.nicegui_app.tts_panel.model import (
    _Panel,
)
from frontends.nicegui_app.tts_panel.view import _render


def open_panel(
    row: RowView,
    kind: ProductionKind,
    *,
    lang: str = "",
    on_change=None,
) -> None:
    """
    打开 TTS 操作台 / Open the console for one audio cell.

    参数 / Args:
        kind:      音频产物（`*_audio`），不是稿子
        on_change: 合成落盘后刷新表格

    **这是唯一的 TTS 界面**：内置音色 / 音色设计 / 克隆三种模式、逐段校对、
    逐段生成、换种子、插事件、上传参考音频都在这里，不再往外跳。
    从前右上角那个「打开完整 TTS 界面」按钮已经删掉——它拉起的是 TTS 模块自带的
    另一个 NiceGUI 进程，那个进程一关窗就整体退出（`forrtl: error (200)`），
    而且它写盘的位置不受 DNA 台账管。同一台机器上两个界面各管一半，是这轮返工的根因。
    This is now the only TTS surface; the hand-off to the upstream GUI process is gone.

    **打开这个面板不会开始合成**（用户实测踩过的就是这个），但会做一次
    朗读友好化——`TTS_PREPROCESS=true` 时那是一次很便宜的 LLM 调用。
    """
    # `_Panel` 拿 lang 去跟 `"zh"` 比（决定分段拼接要不要加空格），空串会当成英文
    lang = normalize_lang(lang)
    panel = _Panel(row, kind, lang)
    script_kind = spec(kind).audio_of
    script_label = spec(script_kind).label if script_kind else spec(kind).label
    panel.editable = actions.script_is_editable(kind, lang=lang)

    with ui.dialog() as dialog, ui.card().classes("w-[1080px] wb-dialog").style(
        "background: var(--wb-panel); border: 1px solid var(--wb-line-strong)"
    ):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label(f"{script_label} · TTS 操作台").classes("text-lg font-medium").style(
                "color: var(--wb-accent)"
            )
        ui.label(theme.short_title(row.article.title, 70)).classes("wb-path")

        status_row = ui.row().classes("items-center gap-2 no-wrap")
        body = ui.column().classes("w-full gap-1")
        footer = ui.row().classes("w-full items-center justify-between no-wrap")

    async def _refresh_status() -> None:
        status = await actions.tts_status()
        status_row.clear()
        with status_row:
            colour = "var(--wb-accent)" if status.online else "var(--wb-danger)"
            ui.label(("● " if status.online else "○ ") + status.detail).classes(
                "wb-path"
            ).style(f"color: {colour}")
            if not status.online:
                ui.button("启动服务", icon="play_arrow", on_click=_start).props(
                    "flat dense no-caps size=sm"
                )

    async def _start() -> None:
        note = ui.notification("正在启动 TTS 服务（模型冷启动要等一会）…",
                               spinner=True, timeout=None)
        try:
            await actions.tts_start()
        except Exception as exc:
            ui.notify(f"启动失败：{exc}", type="negative", timeout=12000,
                      multi_line=True, close_button=True)
        finally:
            note.dismiss()
        await _refresh_status()

    async def _load() -> None:
        with body:
            spinner = ui.row().classes("items-center gap-2")
            with spinner:
                ui.spinner(size="sm")
                ui.label("正在切分稿子（会做一次朗读友好化）…").classes("wb-path")
        await _refresh_status()
        try:
            segments = await actions.speech_segments(row.article.id, kind, lang=lang)
            panel.voices = await actions.tts_voices()
        except Exception as exc:
            spinner.delete()
            with body:
                ui.label(f"取不到分段：{exc}").style("color: var(--wb-danger)")
            return
        spinner.delete()
        if not segments:
            with body:
                ui.label("这一格还没有可朗读的稿子——先生成稿子").style(
                    "color: var(--wb-danger)"
                )
            return
        _render(panel, segments, body, footer, dialog, on_change=on_change)

    dialog.open()
    ui.timer(0.01, _load, once=True)
