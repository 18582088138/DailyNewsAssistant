"""
设置面板的 TTS 子页 / The TTS tab of the settings panel.

三处比「一个输入框」更该做的：音色下拉从服务实时取、参考音频给候选下拉与
就地试听、换合成模式把无关字段**灰掉而不是隐藏**（隐藏会让人以为没这个功能）。
理由见 `docs/04_architecture_frontends.md`。
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field

from nicegui import ui

from frontends.nicegui_app import actions
from frontends.nicegui_app.settings_widgets import (
    PAGE_TTS,
    _env_widget,
    _field_note,
)

# ---------------------------------------------------------------------------
# TTS / the TTS tab
# ---------------------------------------------------------------------------


@dataclass
class _TTSPage:
    """这一页上要联动的控件 / The widgets that react to the mode switch."""

    mode_box: object = None
    gated: list = dc_field(default_factory=list)
    """(field, box, 说明标签) —— 按 needs_mode 灰掉。"""


def _render_tts(inputs: dict) -> None:
    """画 TTS 那一页 / Render the TTS tab."""
    ui.label(
        "本地 TTS 全程零费用，但很慢（RTF≈2.5）。改完服务相关的项要重启 TTS 服务。"
    ).classes("wb-path")

    voices = actions.tts_voice_options()
    if not voices:
        ui.label(
            "⚠️ TTS 服务离线，音色表取不到——音色只能按名字手填。"
            "手打一个不存在的名字，要等几分钟的合成跑完才会报错。"
        ).classes("wb-path").style("color: var(--wb-danger)")

    page = _TTSPage()
    for group, fields in actions.env_groups(PAGE_TTS):
        ui.label(group).classes("wb-path").style(
            "color: var(--wb-accent); margin-top:8px"
        )
        for f in fields:
            box, note = _tts_row(f, voices)
            inputs[f"env:{f.key}"] = box
            if f.key == "TTS_MODE":
                page.mode_box = box
            if f.needs_mode:
                page.gated.append((f, box, note))

    if page.mode_box is not None:
        page.mode_box.on_value_change(lambda _e: _apply_mode(page))
        _apply_mode(page)


def _tts_row(field, voices: list[str]):
    """
    TTS 页上的一项 / One TTS entry.

    音色与参考音频不能只给一个输入框：一个填错的音色名要等几分钟的合成跑完
    才报错，一个不存在的参考音频**根本不报错**——它静默退回内置音色，
    于是「声音不对」成了唯一的现象。
    """
    if field.key in {"TTS_VOICE_HOST", "TTS_VOICE_GUEST"} and voices:
        value = actions.env_display(field)
        options = list(voices)
        if value and value not in options:
            options.append(value)
        box = (
            # `value or None`：**空字符串不是合法初值**，NiceGUI 只放过 None，
            # 否则 `ValueError: Invalid value:` 会把整个设置面板打不开。
            # 没配 TTS_VOICE_GUEST（很常见）+ 服务在线（音色表非空，走的就是这条分支）
            # 就会撞上——用户实测踩到的是这个。
            ui.select(options, value=value or None, label=field.label,
                      new_value_mode="add-unique")
            .props("outlined dense use-input input-debounce=0")
            .classes("w-full max-w-[420px]")
        )
        note = _field_note(field, box)
        return box, note

    if field.key in {"TTS_REF_AUDIO", "TTS_REF_AUDIO_GUEST"}:
        return _ref_audio_row(field)

    box = _env_widget(field)
    note = _field_note(field, box)
    return box, note


def _ref_audio_row(field):
    """参考音频：候选下拉 + 就地试听 + 解析结果 / Candidates, preview, and resolution."""
    value = actions.env_display(field)
    options = actions.ref_audio_options()
    if value and value not in options:
        options.append(value)

    box = (
        # 同上：没配参考音频时 value 是空字符串，直接塞进去会抛 Invalid value
        ui.select(options, value=value or None, label=field.label,
                  new_value_mode="add-unique")
        .props("outlined dense use-input input-debounce=0")
        .classes("w-full max-w-[560px]")
    )
    box.tooltip(field.help.replace("**", ""))

    resolved = ui.label("").classes("wb-path")
    player = ui.audio("").props("controls").classes("w-full max-w-[560px]")

    def _sync() -> None:
        path = actions.ref_audio_file(str(box.value or ""))
        if path is None:
            # 不存在时**必须标红**：合成时它只写一条 warning 就退回内置音色，
            # 界面上不说的话，唯一的现象是「声音不是我选的那把嗓子」。
            resolved.text = f"⚠️ 找不到这个文件（相对路径按数据目录解析）：{box.value or '（空）'}"
            resolved.style("color: var(--wb-danger)")
            player.set_visibility(False)
            return
        resolved.text = f"解析到 {path}"
        resolved.style("color: var(--wb-dim)")
        player.set_source(path)
        player.set_visibility(True)

    box.on_value_change(lambda _e: _sync())
    _sync()

    if actions.env_shadowed(field):
        box.disable()
    return box, resolved


def _apply_mode(page: _TTSPage) -> None:
    """
    按当前模式灰掉无关的项 / Grey out what this mode ignores.

    服务端把「同时给 ref_audio 和音色名」当成互相冲突的参数**直接报错**。
    灰掉的项不会被提交（`_save` 只收 `enabled` 的），所以 `.env` 里原来的值
    原样留着——切回去还在，不用重填。
    Disabled fields are not submitted, so their `.env` values survive a mode switch.
    """
    mode = str(getattr(page.mode_box, "value", "") or "")
    for field, box, note in page.gated:
        active = field.needs_mode == mode
        if actions.env_shadowed(field):
            continue  # 环境变量盖住的项本来就是禁用的，别把它放开
        if active:
            box.enable()
        else:
            box.disable()
        if note is not None:
            note.set_visibility(active)
