"""
后台设置 / The settings panel.

改 `config/profile.yaml`（内容偏好、文案）与 `.env`（TTS、运行设置）。写入全部走
`dna.core.config_edit`——**这个文件里没有任何一行 open/write**。
Edits `config/profile.yaml` and `.env`; every write goes through `dna.core.config_edit`,
and there is no file I/O in this module at all.

五条护栏 / Five guards, each for a bug that already happened once:
    1. **密钥打码，留空即不改。** 界面会被截图，真值不能出现在任何一处。
    2. **被系统环境变量盖住的项标出来并禁用。** 优先级是环境变量 > `.env`，
       公司机器上 `HTTPS_PROXY` 往往是系统级设的——不标出来，人会改十遍都没反应。
    3. **模型下拉里写清哪个是推理模型。** 上一轮「GUI 卡住」的根因就是这一项，
       而界面上完全看不出来。
    4. **保存后说清哪些要重启。** 代理和日志级别在进程启动时就读走了。
    5. **字段清单只有一份（`FIELD_SPECS`）。** 页面归属只是表里的一列——
       页拆开而清单跟着分裂的话，某一页的控件建了但没人收，保存时静默丢掉。
       Page membership is a column, not a separate table: a split list would silently
       drop whatever page `_save` forgot.

为什么每一项都不即时保存 / Why nothing saves on change:
    字数窗口是**成对**的，改完下限还没改上限时中间那一刻是个写反的区间。
    逐项保存会把这个中间态落盘（现在会被模型挡下来，于是变成一串报错）。
    统一按「保存」提交，改的是一个完整的状态。
    The windows are pairs, and there is a moment mid-edit when the bounds are reversed.
    Saving per-field would persist that half-state; one Save button submits a whole one.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from nicegui import ui

from dna.narration.duration import seconds_for_units
from frontends.nicegui_app import actions

PAGE_CONTENT = "内容偏好"
PAGE_COPY = "文案"
PAGE_TTS = "TTS"
PAGE_RUNTIME = "运行设置"


@dataclass(frozen=True)
class FieldSpec:
    """
    `profile.yaml` 里的一项 / One entry in `profile.yaml`.

    `_render_*` 与 `_save` 都从这张表走一遍，所以「画了没收」不可能发生。
    """

    key: str
    kind: str
    """keywords / number / window / languages / text"""

    page: str
    label: str
    hint: str = ""
    unit: str = ""
    """window 专用：区间的单位。"""

    spoken: bool = False
    """这个字数区间对应的是**要念出来的**稿子，才值得估时长。"""

    duration_key: str = ""
    """与哪个时长区间对照 / which duration window to cross-check against."""


# 字段清单。**顺序就是界面上的顺序。**
FIELD_SPECS: tuple[FieldSpec, ...] = (
    # --- 内容偏好 ---
    FieldSpec("focus_keywords", "keywords", PAGE_CONTENT, "关注关键词",
              "命中的条目加分，更容易进日报"),
    FieldSpec("exclude_keywords", "keywords", PAGE_CONTENT, "排除关键词",
              "命中直接丢掉，在抓正文之前——不花网络也不花钱"),
    FieldSpec("video_keywords", "keywords", PAGE_CONTENT, "视频关键词",
              "命中则倾向标记 need_video；标错了在表格里不点就行"),
    FieldSpec("digest_max_entries", "number", PAGE_CONTENT, "日报条目上限"),
    FieldSpec("summary_max_sentences", "number", PAGE_CONTENT, "摘要句数"),
    # 多存是为了攒素材：日报只用 1~3 张，但长图、配图、封面都要挑，
    # 而**重抓拿不回当初那些图**——站点会换图删图。
    FieldSpec("max_images_per_article", "number", PAGE_CONTENT, "每篇存图上限",
              "日报只用 1~3 张，多存的是素材库；重抓拿不回站点已经换掉的图"),
    FieldSpec("max_videos_per_article", "number", PAGE_CONTENT, "每篇存视频上限"),
    FieldSpec("languages", "languages", PAGE_CONTENT, "输出语言"),
    # --- 文案 ---
    FieldSpec("summary_chars", "window", PAGE_COPY, "条目摘要", unit="字"),
    FieldSpec("shortvideo_chars", "window", PAGE_COPY, "短视频稿", unit="字",
              spoken=True, duration_key="video_duration_seconds"),
    FieldSpec("narration_chars", "window", PAGE_COPY, "口播稿", unit="字",
              spoken=True, duration_key="narration_duration_seconds"),
    FieldSpec("video_duration_seconds", "window", PAGE_COPY, "短视频", unit="秒"),
    FieldSpec("narration_duration_seconds", "window", PAGE_COPY, "口播", unit="秒"),
    FieldSpec("longform_duration_seconds", "window", PAGE_COPY, "长文案", unit="秒"),
    FieldSpec("cta_line", "text", PAGE_COPY, "视频稿结尾引导语",
              "账号品牌，换栏目就要换——所以它在配置里而不是写死在提示词里"),
)


def _specs(page: str, kind: str) -> list[FieldSpec]:
    return [s for s in FIELD_SPECS if s.page == page and s.kind == kind]


def open_dialog(*, on_saved=None) -> None:
    """
    弹出设置对话框 / Open the settings panel.

    参数 / Args:
        on_saved: 保存成功后的回调。字数窗口一改，表格里的超长标记就要重判
    """
    profile = actions.profile_values()
    inputs: dict[str, object] = {}

    with ui.dialog() as dialog, ui.card().classes("w-[860px] wb-dialog").style(
        "background: var(--wb-panel); border: 1px solid var(--wb-line-strong)"
    ):
        ui.label("设置").classes("text-lg font-medium").style("color: var(--wb-accent)")

        with ui.tabs().props("dense no-caps").classes("w-full") as tabs:
            tab_content = ui.tab(PAGE_CONTENT)
            tab_copy = ui.tab(PAGE_COPY)
            tab_tts = ui.tab(PAGE_TTS)
            tab_env = ui.tab(PAGE_RUNTIME)

        with ui.tab_panels(tabs, value=tab_content).classes(
            "w-full"
        ).style("background: transparent"):
            with ui.tab_panel(tab_content):
                _render_content(profile, inputs)
            with ui.tab_panel(tab_copy):
                _render_copy(profile, inputs)
            with ui.tab_panel(tab_tts):
                _render_tts(inputs)
            with ui.tab_panel(tab_env):
                _render_env(inputs)

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat no-caps")
            ui.button(
                "保存", icon="save",
                on_click=lambda: _save(dialog, profile, inputs, on_saved=on_saved),
            ).props("no-caps").style("color: var(--wb-accent)")

    dialog.open()


# ---------------------------------------------------------------------------
# 内容偏好 / content preferences
# ---------------------------------------------------------------------------


def _render_content(profile: dict, inputs: dict) -> None:
    """画内容偏好那一页 / Render the content-preferences tab."""
    ui.label("改完即时生效，不用重启。文件里的注释不会被动。").classes("wb-path")

    for spec in _specs(PAGE_CONTENT, "keywords"):
        current = list(profile.get(spec.key) or [])
        # `use-input` 是关键：Quasar 的 select **没有它就没有可打字的输入框**，
        # 于是 `new-value-mode=add-unique` 形同虚设——「支持新增」配了却用不了。
        # Without `use-input` Quasar renders no text field, so `add-unique` never fires.
        box = (
            ui.select(
                current, multiple=True, value=current, label=spec.label,
                new_value_mode="add-unique",
            )
            .props("outlined dense use-chips use-input input-debounce=0")
            .classes("w-full")
        )
        box.tooltip(spec.hint)
        inputs[spec.key] = box

    # 一个能打字却看不出能打字的控件，等于不能打字。
    ui.label("三个关键词框都可以直接打字，回车即新增；点 chip 上的 × 删除。").classes(
        "wb-path"
    )

    with ui.row().classes("w-full items-center gap-4"):
        for spec in _specs(PAGE_CONTENT, "number"):
            inputs[spec.key] = _number(spec.label, profile[spec.key], hint=spec.hint)

    languages = [str(x) for x in (profile.get("languages") or ["zh"])]
    inputs["languages"] = (
        ui.select({"zh": "中文", "en": "English"}, multiple=True, value=languages,
                  label="输出语言")
        .props("outlined dense use-chips")
        .classes("w-64")
    )


# ---------------------------------------------------------------------------
# 文案 / copy length
# ---------------------------------------------------------------------------


def _render_copy(profile: dict, inputs: dict) -> None:
    """
    画文案那一页 / Render the copy-length tab.

    这一页真正的价值是**把两组数字对上**：字数区间与时长区间原先各配各的，
    谁也不知道它们对不对得上，而验收只看字数。
    """
    windows = _specs(PAGE_COPY, "window")
    char_specs = [s for s in windows if s.unit == "字"]
    duration_specs = [s for s in windows if s.unit == "秒"]

    ui.label("目标字数区间（验收标准）").classes("wb-path").style(
        "color: var(--wb-accent)"
    )
    ui.label(
        "字数是程序验收用的唯一单位。超出区间的产物在表格里标成另一种颜色，"
        "不算失败——改完这里，历史产物会跟着重新判定。"
    ).classes("wb-path")

    estimates: list = []
    for spec in char_specs:
        with ui.row().classes("items-end gap-3 no-wrap"):
            boxes = _window(spec.label, profile[spec.key], unit=spec.unit)
            inputs[spec.key] = boxes
            if spec.spoken:
                estimates.append((spec, boxes, _estimate_label()))
            else:
                # 摘要是读的不是念的，给它标一个「音频时长」只会误导。
                ui.label("（摘要不朗读，不估时长）").classes("wb-path").style(
                    "margin-bottom:4px"
                )

    ui.label("时长区间（秒，仅参照）").classes("wb-path").style("margin-top:6px")
    ui.label(
        "只用于提示词的开场句与产物记账，不参与验收——同样 200 字的稿子，"
        "中英混排能是 27 秒、纯中文是 44 秒，拿秒数验收会把合格的稿子反复回炉。"
    ).classes("wb-path")
    with ui.row().classes("w-full items-center gap-6"):
        for spec in duration_specs:
            inputs[spec.key] = _window(spec.label, profile[spec.key], unit=spec.unit)

    def _refresh() -> None:
        for spec, boxes, label in estimates:
            text, warn = _estimate(spec, boxes, inputs.get(spec.duration_key))
            label.text = text
            label.style(replace=f"margin-bottom:4px; color: var(--wb-{'danger' if warn else 'dim'})")

    # 改数字的同一刻秒数就跟着动：算完再手动去点一下「保存」才看到结果，
    # 等于还是要靠试。
    for spec, boxes, _ in estimates:
        for box in boxes:
            box.on_value_change(lambda _e: _refresh())
    for spec in duration_specs:
        for box in inputs[spec.key]:
            box.on_value_change(lambda _e: _refresh())
    _refresh()

    spec = next(s for s in FIELD_SPECS if s.key == "cta_line")
    inputs["cta_line"] = (
        ui.input(spec.label, value=profile["cta_line"])
        .props("outlined dense")
        .classes("w-full")
    )
    inputs["cta_line"].tooltip(spec.hint)


def _estimate_label():
    """一行时长估算 / One estimate line，先建后填。"""
    return ui.label("").classes("wb-path").style("margin-bottom:4px")


def _estimate(spec: FieldSpec, boxes, duration_boxes) -> tuple[str, bool]:
    """
    把字数区间换算成秒并与时长区间对照 / Convert to seconds and cross-check.

    换算走 `dna.narration.duration.seconds_for_units`，**不另写一份**：
    那里的系数是 issue 009 实测出来的，自己再算一遍必然和验收用的那份漂开。

    返回 / Returns:
        (要显示的文字, 是否与时长区间打架)
    """
    low, high = (_int(b.value) for b in boxes)
    lo_s = seconds_for_units(low)
    hi_s = seconds_for_units(high)
    text = f"≈ {lo_s:.0f} ~ {hi_s:.0f} 秒"

    if duration_boxes is None:
        return text, False
    d_low, d_high = (_int(b.value) for b in duration_boxes)
    if hi_s < d_low or lo_s > d_high:
        # 只提示、不阻止保存：时长区间不参与验收，它对不上不构成错误，
        # 但两组数字互相矛盾时人应该知道自己在配什么。
        return f"{text}　·　⚠️ 与下面配的时长区间 {d_low}~{d_high} 秒不一致", True
    return text, False


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


# ---------------------------------------------------------------------------
# 运行设置 / runtime settings
# ---------------------------------------------------------------------------


def _render_env(inputs: dict) -> None:
    """画 .env 的那一页 / Render the runtime tab."""
    ui.label(
        "只列出可以从界面改的项。数据目录、数据库路径一类不在这里——"
        "改错了程序下次就找不到自己的数据。TTS 的项在上一页。"
    ).classes("wb-path")

    for group, fields in actions.env_groups(PAGE_RUNTIME):
        ui.label(group).classes("wb-path").style(
            "color: var(--wb-accent); margin-top:8px"
        )
        for field in fields:
            box = _env_widget(field)
            _field_note(field, box)
            inputs[f"env:{field.key}"] = box


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


# ---------------------------------------------------------------------------
# 保存 / saving
# ---------------------------------------------------------------------------


def _save(dialog, profile: dict, inputs: dict, *, on_saved) -> None:
    """收集改动并落盘 / Collect the changes and persist them."""
    profile_updates: dict = {}

    for spec in FIELD_SPECS:
        box = inputs[spec.key]
        if spec.kind == "keywords":
            profile_updates[spec.key] = [
                str(x).strip() for x in (box.value or []) if str(x).strip()
            ]
        elif spec.kind == "number":
            profile_updates[spec.key] = _int(box.value)
        elif spec.kind == "window":
            low_box, high_box = box
            profile_updates[spec.key] = [_int(low_box.value), _int(high_box.value)]
        elif spec.kind == "languages":
            profile_updates[spec.key] = [str(x) for x in (box.value or ["zh"])]
        else:
            profile_updates[spec.key] = str(box.value or "")

    # 只提交真的改了的项：`patch_yaml_values` 会重写每一个提交上来的 key，
    # 全量提交会让 `git diff config/profile.yaml` 出现一堆值没变的行（引号、间距）,
    # 而这个文件是要靠人读 diff 来确认改对了的。
    # Only changed keys are submitted: patching every key would fill the diff with lines
    # whose value did not change, and this file is reviewed by reading its diff.
    changed = {k: v for k, v in profile_updates.items() if not _same(v, profile.get(k))}

    # 被环境变量盖住、或**当前模式用不上**的项是禁用的，不提交：前者写进 `.env`
    # 也不会生效，后者会和服务端「两个参数不能同时给」的规则打架。
    # 两种情况下 `.env` 里原来的值都原样留着。
    # Shadowed and mode-irrelevant fields are disabled and not submitted; their existing
    # `.env` values survive untouched.
    env_updates = {
        key.split(":", 1)[1]: _env_text(box)
        for key, box in inputs.items()
        if key.startswith("env:") and box.enabled
    }

    try:
        message = actions.save_settings(changed, env_updates)
    except Exception as exc:  # noqa: BLE001 - 校验消息原样给用户，它已经指明了哪个字段
        ui.notify(f"保存失败：{exc}", type="negative", timeout=12000)
        return

    dialog.close()
    ui.notify(message, type="positive", timeout=10000)
    if on_saved is not None:
        on_saved()


def _env_text(box) -> str:
    """控件的值转成 `.env` 里的字符串 / One widget's value as a `.env` string."""
    value = box.value
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _same(new, old) -> bool:
    """两个值是否等价 / Whether the two values are equivalent."""
    if isinstance(new, list) and isinstance(old, (list, tuple)):
        return list(new) == list(old)
    return new == old


__all__ = ["FIELD_SPECS", "open_dialog"]
