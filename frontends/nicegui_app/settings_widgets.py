"""
设置面板的共用控件 / Widgets shared by the settings panel's tabs.

四个子页分了文件，但页名常量与几个控件工厂是共用的 —— 放在这里，
两个方向都只 import 它，**不互相 import**（拆分时 view↔page 成环就是这么来的）。
"""

from __future__ import annotations

from nicegui import ui

from frontends.nicegui_app import actions

PAGE_CONTENT = "内容偏好"
PAGE_COPY = "文案"
PAGE_TTS = "TTS"
PAGE_RUNTIME = "运行设置"

def _env_widget(field) -> object:
    """一个 `.env` 项的控件 / The widget for one `.env` entry."""
    value = actions.env_display(field)

    if field.boolean:
        box = ui.switch(field.label, value=str(value).lower() in {"1", "true", "yes", "on"})
        box.props("dense")
    elif field.options:
        # 当前值不在预设选项里时先把它加进去（比如换过一个自定义模型名）——
        # 否则下拉会显示成空的，看起来像「这一项没配」，一保存就把真值抹了。
        # The live value is added to the options when missing: otherwise the dropdown looks
        # empty, reads as unconfigured, and saving would wipe the real value.
        options = list(field.options)
        if value and value not in options:
            options.append(value)
        box = (
            # 空字符串不是合法初值（只有 None 能过），没配的项会撞上
            ui.select(options, value=value or None, label=field.label,
                      new_value_mode="add-unique")
            .props("outlined dense")
            .classes("w-full max-w-[420px]")
        )
    else:
        box = (
            ui.input(field.label, value=value)
            .props("outlined dense")
            .classes("w-full max-w-[560px]")
        )
        if field.secret:
            # 输入框里放的是打码值，`save_env` 会认出来并跳过——所以不用清空它。
            # 清空反而更糟：看起来像「还没配」，人会重新去翻密钥。
            # The box holds the mask, which `save_env` recognises and skips. Clearing it
            # would look unconfigured and send the user hunting for the key again.
            box.props('type=text autocomplete=off')

    if field.help:
        box.tooltip(field.help.replace("**", ""))
    return box


def _field_note(field, box):
    """字段下面那行小字 / The line under a field；被环境变量盖住时标红并禁用。"""
    if actions.env_shadowed(field):
        # 标红 + 禁用，而不是只标红：能改的输入框会让人先改一遍才发现没用。
        box.disable()
        return ui.label(
            f"⚠️ 当前值来自系统环境变量 {field.key}，改 .env 不生效"
            "（要改就在系统环境变量里改，或者把它删掉）"
        ).classes("wb-path").style("color: var(--wb-danger)")
    if field.help:
        return ui.label(field.help.replace("**", "")).classes("wb-path")
    return None



def _number(label: str, value, *, hint: str = ""):
    """一个整数输入框 / One integer field."""
    box = (
        ui.number(label, value=value, min=1, format="%.0f")
        .props("outlined dense")
        .classes("w-40")
    )
    if hint:
        box.tooltip(hint)
    return box


def _window(label: str, value, *, unit: str) -> tuple:
    """
    一个区间的两个输入框 / The two fields of one window.

    返回一对控件，取值时再组成列表——**不在这里合成**，因为改下限的那一刻
    区间是写反的，此时组出来的值会被模型拒掉。
    Returned as a pair and combined only on save: mid-edit the bounds are reversed.
    """
    low, high = value
    with ui.column().classes("gap-0"):
        ui.label(f"{label}（{unit}）").classes("wb-path")
        with ui.row().classes("items-center gap-1 no-wrap"):
            low_box = ui.number(value=low, min=1, format="%.0f").props(
                "outlined dense"
            ).classes("w-20")
            ui.label("~").classes("wb-path")
            high_box = ui.number(value=high, min=1, format="%.0f").props(
                "outlined dense"
            ).classes("w-20")
    return (low_box, high_box)


def _int(value) -> int:
    try:
        return int(value or 1)
    except (TypeError, ValueError):
        return 1



