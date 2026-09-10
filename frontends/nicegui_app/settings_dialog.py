"""
后台设置 / The settings panel.

改 `config/profile.yaml`（内容偏好）与 `.env`（运行设置）。写入全部走
`dna.core.config_edit`——**这个文件里没有任何一行 open/write**。
Edits `config/profile.yaml` and `.env`; every write goes through `dna.core.config_edit`,
and there is no file I/O in this module at all.

四条护栏 / Four guards, each for a bug that already happened once:
    1. **密钥打码，留空即不改。** 界面会被截图，真值不能出现在任何一处。
    2. **被系统环境变量盖住的项标出来并禁用。** 优先级是环境变量 > `.env`，
       公司机器上 `HTTPS_PROXY` 往往是系统级设的——不标出来，人会改十遍都没反应。
    3. **模型下拉里写清哪个是推理模型。** 上一轮「GUI 卡住」的根因就是这一项，
       而界面上完全看不出来。
    4. **保存后说清哪些要重启。** 代理和日志级别在进程启动时就读走了。

为什么每一项都不即时保存 / Why nothing saves on change:
    字数窗口是**成对**的，改完下限还没改上限时中间那一刻是个写反的区间。
    逐项保存会把这个中间态落盘（现在会被模型挡下来，于是变成一串报错）。
    统一按「保存」提交，改的是一个完整的状态。
    The windows are pairs, and there is a moment mid-edit when the bounds are reversed.
    Saving per-field would persist that half-state; one Save button submits a whole one.
"""

from __future__ import annotations

from nicegui import ui

from frontends.nicegui_app import actions

# 三个字数窗口 / the character windows —— 这三个是**验收标准**
CHAR_WINDOWS: tuple[tuple[str, str], ...] = (
    ("summary_chars", "条目摘要"),
    ("shortvideo_chars", "短视频稿"),
    ("narration_chars", "口播稿"),
)

# 三个时长窗口 / the duration windows —— 只进提示词与记账，不参与验收
DURATION_WINDOWS: tuple[tuple[str, str], ...] = (
    ("video_duration_seconds", "短视频"),
    ("narration_duration_seconds", "口播"),
    ("longform_duration_seconds", "长文案"),
)

KEYWORD_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("focus_keywords", "关注关键词", "命中的条目加分，更容易进日报"),
    ("exclude_keywords", "排除关键词", "命中直接丢掉，**在抓正文之前**——不花网络也不花钱"),
    ("video_keywords", "视频关键词", "命中则倾向标记 need_video；标错了在表格里不点就行"),
)

# 整数项 / the plain integer fields
NUMBER_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("digest_max_entries", "日报条目上限", ""),
    ("summary_max_sentences", "摘要句数", ""),
    # 多存是为了攒素材：日报只用 1~3 张，但长图、配图、封面都要挑，
    # 而**重抓拿不回当初那些图**——站点会换图删图。
    ("max_images_per_article", "每篇存图上限",
     "日报只用 1~3 张，多存的是素材库；重抓拿不回站点已经换掉的图"),
    ("max_videos_per_article", "每篇存视频上限", ""),
)

# 其余各自一种控件的项 / the remaining one-off fields
OTHER_FIELDS: tuple[str, ...] = ("languages", "cta_line")


def open_dialog(*, on_saved=None) -> None:
    """
    弹出设置对话框 / Open the settings panel.

    参数 / Args:
        on_saved: 保存成功后的回调。字数窗口一改，表格里的超长标记就要重判
    """
    profile = actions.profile_values()
    inputs: dict[str, object] = {}

    with ui.dialog() as dialog, ui.card().classes("w-[820px]").style(
        "background: var(--wb-panel); border: 1px solid var(--wb-line-strong)"
    ):
        ui.label("设置").classes("text-lg font-medium").style("color: var(--wb-accent)")

        with ui.tabs().props("dense no-caps").classes("w-full") as tabs:
            tab_profile = ui.tab("内容偏好")
            tab_env = ui.tab("运行设置")

        with ui.tab_panels(tabs, value=tab_profile).classes(
            "w-full"
        ).style("background: transparent"):
            with ui.tab_panel(tab_profile):
                _render_profile(profile, inputs)
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


def _render_profile(profile: dict, inputs: dict) -> None:
    """画 profile.yaml 的那一页 / Render the preferences tab."""
    ui.label("改完即时生效，不用重启。文件里的注释不会被动。").classes("wb-path")

    for key, label, hint in KEYWORD_FIELDS:
        current = list(profile.get(key) or [])
        # `new_value_mode="add-unique"`：直接打字回车就是新标签，不用先去配置文件里加。
        # 关键词是最常调的一项，让它比开记事本更快才有人真的用。
        box = (
            ui.select(
                current, multiple=True, value=current, label=label,
                new_value_mode="add-unique",
            )
            .props("outlined dense use-chips")
            .classes("w-full")
        )
        box.tooltip(hint.replace("**", ""))
        inputs[key] = box

    with ui.row().classes("w-full items-center gap-4"):
        for key, label, hint in NUMBER_FIELDS:
            inputs[key] = _number(label, profile[key], hint=hint)

    ui.label("目标字数区间（验收标准）").classes("wb-path").style(
        "color: var(--wb-accent); margin-top:6px"
    )
    ui.label(
        "字数是程序验收用的唯一单位。超出区间的产物在表格里标成另一种颜色，"
        "**不算失败**——改完这里，历史产物会跟着重新判定。"
    ).classes("wb-path")
    with ui.row().classes("w-full items-center gap-6"):
        for key, label in CHAR_WINDOWS:
            inputs[key] = _window(label, profile[key], unit="字")

    ui.label("时长区间（秒，仅参照）").classes("wb-path").style("margin-top:6px")
    ui.label(
        "只用于提示词的开场句与产物记账，**不参与验收**——同样 200 字的稿子，"
        "中英混排能是 27 秒、纯中文是 44 秒，拿秒数验收会把合格的稿子反复回炉。"
    ).classes("wb-path")
    with ui.row().classes("w-full items-center gap-6"):
        for key, label in DURATION_WINDOWS:
            inputs[key] = _window(label, profile[key], unit="秒")

    languages = [str(x) for x in (profile.get("languages") or ["zh"])]
    inputs["languages"] = (
        ui.select({"zh": "中文", "en": "English"}, multiple=True, value=languages, label="输出语言")
        .props("outlined dense use-chips")
        .classes("w-64")
    )

    inputs["cta_line"] = (
        ui.input("视频稿结尾引导语", value=profile["cta_line"])
        .props("outlined dense")
        .classes("w-full")
    )
    inputs["cta_line"].tooltip("账号品牌，换栏目就要换——所以它在配置里而不是写死在提示词里")


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


# ---------------------------------------------------------------------------
# 运行设置 / runtime settings
# ---------------------------------------------------------------------------


def _render_env(inputs: dict) -> None:
    """画 .env 的那一页 / Render the runtime tab."""
    ui.label(
        "只列出可以从界面改的项。数据目录、数据库路径一类不在这里——"
        "改错了程序下次就找不到自己的数据。"
    ).classes("wb-path")

    for group, fields in actions.env_groups():
        ui.label(group).classes("wb-path").style(
            "color: var(--wb-accent); margin-top:8px"
        )
        for field in fields:
            inputs[f"env:{field.key}"] = _env_row(field)


def _env_row(field) -> object:
    """一个 `.env` 项 / One `.env` entry."""
    value = actions.env_display(field)
    shadowed = actions.env_shadowed(field)

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
            ui.select(options, value=value, label=field.label, new_value_mode="add-unique")
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

    if shadowed:
        # 标红 + 禁用，而不是只标红：能改的输入框会让人先改一遍才发现没用。
        box.disable()
        ui.label(
            f"⚠️ 当前值来自系统环境变量 {field.key}，改 .env 不生效"
            "（要改就在系统环境变量里改，或者把它删掉）"
        ).classes("wb-path").style("color: var(--wb-danger)")
    elif field.help:
        ui.label(field.help.replace("**", "")).classes("wb-path")

    return box


# ---------------------------------------------------------------------------
# 保存 / saving
# ---------------------------------------------------------------------------


def _save(dialog, profile: dict, inputs: dict, *, on_saved) -> None:
    """收集改动并落盘 / Collect the changes and persist them."""
    profile_updates: dict = {}

    for key, _, _ in KEYWORD_FIELDS:
        profile_updates[key] = [str(x).strip() for x in (inputs[key].value or []) if str(x).strip()]

    for key, _, _ in NUMBER_FIELDS:
        profile_updates[key] = int(inputs[key].value or 1)

    for key, _ in CHAR_WINDOWS + DURATION_WINDOWS:
        low_box, high_box = inputs[key]
        profile_updates[key] = [int(low_box.value or 1), int(high_box.value or 1)]

    profile_updates["languages"] = [str(x) for x in (inputs["languages"].value or ["zh"])]
    profile_updates["cta_line"] = str(inputs["cta_line"].value or "")

    # 只提交真的改了的项：`patch_yaml_values` 会重写每一个提交上来的 key，
    # 全量提交会让 `git diff config/profile.yaml` 出现一堆值没变的行（引号、间距）,
    # 而这个文件是要靠人读 diff 来确认改对了的。
    # Only changed keys are submitted: patching every key would fill the diff with lines
    # whose value did not change, and this file is reviewed by reading its diff.
    changed = {k: v for k, v in profile_updates.items() if not _same(v, profile.get(k))}

    # 被环境变量盖住的项是**禁用**的，不提交：写进 `.env` 也不会生效，
    # 只会让文件里留下一个与实际行为不符的值，下一个查问题的人要多绕一圈。
    # Shadowed fields are disabled and not submitted: writing them would leave a value in
    # the file that does not match actual behaviour.
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


__all__ = ["open_dialog"]
