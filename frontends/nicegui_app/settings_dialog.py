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

from dataclasses import dataclass

from nicegui import ui

from dna.narration.duration import seconds_for_units
from frontends.nicegui_app import actions
from frontends.nicegui_app.settings_tts_page import _render_tts
from frontends.nicegui_app.settings_widgets import (
    PAGE_CONTENT,
    PAGE_COPY,
    PAGE_RUNTIME,
    PAGE_TTS,
    _env_widget,
    _field_note,
    _int,
    _number,
    _window,
)


@dataclass(frozen=True)
class FieldSpec:
    """
    `profile.yaml` 里的一项 / One entry in `profile.yaml`.

    `_render_*` 与 `_save` 都从这张表走一遍，所以「画了没收」不可能发生。
    """

    key: str
    kind: str
    """keywords / number / window / text"""

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
    FieldSpec("copy_max_rewrites", "number", PAGE_CONTENT, "文案回炉上限",
              "超出字数区间时最多重写几次；每次都是一次计费调用"),
    FieldSpec("summary_max_rewrites", "number", PAGE_CONTENT, "摘要回炉上限",
              "摘要每条都跑，这里加一次就是整期翻倍"),
    # 多存是为了攒素材：日报只用 1~3 张，但长图、配图、封面都要挑，
    # 而**重抓拿不回当初那些图**——站点会换图删图。
    FieldSpec("max_images_per_article", "number", PAGE_CONTENT, "每篇存图上限",
              "日报只用 1~3 张，多存的是素材库；重抓拿不回站点已经换掉的图"),
    FieldSpec("max_videos_per_article", "number", PAGE_CONTENT, "每篇存视频上限"),
    # 这里曾经有一个「输出语言」多选（`languages`）。它是**假配置**：全仓没有任何
    # 地方读 `profile.languages`，语言实际是每次生成时的参数（`--lang` / 展开面板
    # 里的中英开关）。配了没用的开关比没有开关更坏，字段与控件一并删掉。
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
    # 两个数不一样是刻意的：摘要每条都跑，上限乘以条目数；文案单篇按需跑。
    FieldSpec("summary_body_chars", "number", PAGE_COPY, "摘要读多少正文",
              "每条都跑，上限直接乘以条目数"),
    FieldSpec("script_body_chars", "number", PAGE_COPY, "文案读多少正文",
              "截短是有代价的：被切掉的尾部往往正是「必须说局限」要用的料"),
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
            "w-full wb-dialog-body"
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
            tone = "danger" if warn else "dim"
            label.style(replace=f"margin-bottom:4px; color: var(--wb-{tone})")

    # 改数字的同一刻秒数就跟着动：算完再手动去点一下「保存」才看到结果，
    # 等于还是要靠试。
    for _spec, boxes, _ in estimates:
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

    # 「读多少正文」这两项漏画过一次：清单里有、页面上没有，于是 `_save` 按清单
    # 取值时 `KeyError`。护栏 5 说的正是这件事——页面必须画完属于自己那一页的
    # 每一种 kind，不能只挑 window 和 text。
    ui.label("喂给模型多少正文（字）").classes("wb-path").style("margin-top:6px")
    with ui.row().classes("w-full items-center gap-4"):
        for spec in _specs(PAGE_COPY, "number"):
            inputs[spec.key] = _number(spec.label, profile[spec.key], hint=spec.hint)


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
    except Exception as exc:
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
