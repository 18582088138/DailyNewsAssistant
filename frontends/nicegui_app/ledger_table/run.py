"""从格子上发起生成：确认框与后台执行 / Launching a production from a cell。"""

from __future__ import annotations

from nicegui import ui

from dna.produce import ProductionKind, spec
from dna.produce.tasks import default_language, normalize_lang
from frontends.nicegui_app import actions, theme
from frontends.nicegui_app.actions import RowView
from frontends.nicegui_app.audio_progress import AudioProgress
from frontends.nicegui_app.ledger_table.state import (
    remember_row,
)

# ---------------------------------------------------------------------------
# 生成与重做 / running a production
# ---------------------------------------------------------------------------


def _launch(
    row: RowView,
    kind: ProductionKind,
    *,
    force: bool,
    on_change,
    lang: str = "",
    instructions: str = "",
) -> None:
    """
    发起一次生成 / Kick off one production.

    两种情况先弹确认框 / Two kinds of production ask first:
        长文案——一篇 5~9 次调用，是其余产物的好几倍，**不该一点就跑**
        长音频——不花钱，但要跑几十分钟，同样不该一点就跑

    两种代价不同，确认框的措辞也不同：一个说的是账单，一个说的是时间。
    用同一句话糊过去，人就分不清刚才点掉的是钱还是半小时。
    The two costs differ and so does the wording: conflating them leaves the user unsure
    which of the two they just spent.
    """
    # 归一化一次，往下四条路都拿到具体语言码：下游要用它当库里的查询条件、
    # 也要拿它跟默认语言比较（决定标题上加不加「（EN）」）。
    lang = normalize_lang(lang)
    remember_row(row.article.id)
    task = spec(kind)
    if task.needs_variant:
        _ask_longform(
            row, kind, force=force, on_change=on_change, lang=lang,
            instructions=instructions,
        )
        return
    if task.audio_of is not None:
        _ask_audio(row, kind, force=force, on_change=on_change, lang=lang)
        return
    _run(
        row, kind, variant=None, force=force, on_change=on_change, lang=lang,
        instructions=instructions,
    )


# 超过这么久就先问一句 / anything longer than this asks first
#
# 五分钟：短视频音频约 75 秒、口播约 4 分钟，都直接跑；长文案音频半小时以上，
# 必须先问。门槛设在这里，日常的两项不会被确认框打断，而真正长的那项跑不掉。
# Five minutes: the short-video and narration audio run straight away, while the
# long-form audio always asks. The routine cases stay unobstructed.
AUDIO_CONFIRM_SECONDS = 300


def _ask_audio(
    row: RowView,
    kind: ProductionKind,
    *,
    force: bool,
    on_change,
    lang: str = "",
) -> None:
    """长音频的耗时确认 / Confirm a long synthesis run."""
    lang = normalize_lang(lang)
    wait = actions.audio_estimate_seconds(row.article, kind, lang)
    if wait < AUDIO_CONFIRM_SECONDS:
        _run(row, kind, variant=None, force=force, on_change=on_change, lang=lang)
        return

    task = spec(kind)
    with ui.dialog() as dialog, ui.card().classes("w-96 wb-dialog").style(
        "background: var(--wb-panel); border: 1px solid var(--wb-line-strong)"
    ):
        ui.label(f"合成{task.label}").classes("text-lg font-medium").style(
            "color: var(--wb-accent)"
        )
        ui.label(theme.short_title(row.article.title, 60)).classes("wb-path")

        ui.html(
            f"本地合成，<b>不产生任何费用</b>，但预计要跑 "
            f"<b>{wait / 60:.0f} 分钟</b>。"
        ).classes("text-xs").style("color: var(--wb-warn)")
        ui.label(
            "期间界面可以继续用，进度会显示在提示条上；中途关掉页面会让这次合成白跑。"
        ).classes("wb-path")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat no-caps")
            ui.button(
                "开始合成",
                on_click=lambda: (
                    dialog.close(),
                    _run(
                        row, kind, variant=None, force=force, on_change=on_change,
                        lang=lang,
                    ),
                ),
            ).props("no-caps")

    dialog.open()


def _ask_longform(
    row: RowView,
    kind: ProductionKind,
    *,
    force: bool,
    on_change,
    lang: str = "",
    instructions: str = "",
) -> None:
    """长文案的形式选择与费用确认 / Mode choice and cost confirmation."""
    lang = normalize_lang(lang)
    task = spec(kind)

    with ui.dialog() as dialog, ui.card().classes("w-96 wb-dialog").style(
        "background: var(--wb-panel); border: 1px solid var(--wb-line-strong)"
    ):
        ui.label("生成长文案").classes("text-lg font-medium").style(
            "color: var(--wb-accent)"
        )
        ui.label(theme.short_title(row.article.title, 60)).classes("wb-path")

        mode = ui.radio(
            {"feature": "专题（单角色讲述）", "interview": "访谈（主持人 + 嘉宾）"},
            value="feature",
        ).props("inline dense")

        ui.label(
            f"⚠️ 预估 {task.approx_calls} 次 LLM 调用（提纲 1 次 + 每节 1 次），"
            f"是常规四项加起来的两倍多。"
        ).classes("text-xs").style("color: var(--wb-warn)")
        ui.label(
            f"正文 {row.article.text_len} 字，成稿约 "
            f"{actions.longform_estimate(row.article)}"
            "——时长跟文章体量走，不注水凑时长。"
        ).classes("wb-path")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat no-caps")
            ui.button(
                "确认生成",
                on_click=lambda: (
                    dialog.close(),
                    _run(
                        row, kind, variant=mode.value, force=force, on_change=on_change,
                        lang=lang, instructions=instructions,
                    ),
                ),
            ).props("no-caps").classes("wb-btn-cost")

    dialog.open()


def _run(
    row: RowView,
    kind: ProductionKind,
    *,
    variant,
    force: bool,
    on_change,
    lang: str = "",
    instructions: str = "",
) -> None:
    """执行生成并把结果告诉用户 / Run the production and report back."""
    lang = normalize_lang(lang)
    task = spec(kind)
    label = task.label if lang == default_language() else f"{task.label}（EN）"
    is_audio = task.audio_of is not None

    # 音频与文本的等待完全不是一个量级，提示也不该是同一种。
    #
    # 音频：几分钟到几十分钟，用右下角的**进度浮窗**（秒表 + 进度条 + 已产出秒数，
    #   见 `audio_progress.py`）—— 一个不动的转圈无法区分「在跑」和「卡死了」。
    # 文本：十几秒，一条带转圈的提示条足够，多摆一个浮窗反而吵。
    # The two waits differ by orders of magnitude, so the feedback differs too.
    panel = None
    notification = None
    progress: dict = {}
    if is_audio:
        panel = AudioProgress(
            label,
            expected_seconds=actions.audio_estimate_seconds(row.article, kind, lang),
        )
        progress = panel.progress
    else:
        notification = ui.notification(f"{label} 生成中…（调用 LLM，请稍候）",
                                       spinner=True, timeout=None)

    async def _go() -> None:
        try:
            result = await actions.run_production(
                row.article.id,
                kind,
                lang=lang,
                variant=variant,
                force=force,
                instructions=instructions,
                progress=progress,
            )
        finally:
            if panel is not None:
                panel.close()
            if notification is not None:
                notification.dismiss()

        if result.ok and result.skipped:
            ui.notify(f"{label}：已存在，未重新生成", type="info")
        elif result.ok and is_audio:
            # 音频报的是**真实时长**，不是估算；缺段时明确说出来
            warning = "" if result.within_target else "，⚠️ 部分段落合成失败，音频不完整"
            ui.notify(
                f"{label} 完成：{result.seconds or 0:.0f} 秒音频（{result.chars} 字）{warning}",
                type="positive" if result.within_target else "warning",
            )
        elif result.ok:
            # 报秒数，不只报「超出区间」：超时的稿子仍然可用，人要看着具体数字
            # 决定是手删两句还是重做；而写了修改指令时，多半就是那句话在拉长它。
            # The measured duration is stated, not just the fact of the overrun: an
            # overlong script is still usable and the number decides trim-or-redo.
            parts = [f"{result.chars} 字"]
            if result.seconds:
                parts.append(f"约 {result.seconds:.0f} 秒")
            parts.append(f"{result.calls} 次调用")
            warning = ""
            if not result.within_target:
                # 报确切数字，不只报「超出区间」：人要据此决定是手删两句还是重做。
                # 验收标准是**字数**（长文案的时长也是从字数推出来的），措辞跟着改过来
                # ——先前写「时长区间」，而摘要根本没有时长这个维度。
                # The concrete numbers decide trim-or-redo; the unit is characters, which
                # the wording previously got wrong for the summary kind.
                window = actions.target_window(kind)
                warning = "，⚠️ 超出目标字数区间"
                if window:
                    warning += f"（{result.chars} 字，目标 {window[0]}~{window[1]}）"
                if instructions:
                    warning += "（本次带了修改指令）"
            ui.notify(
                f"{label} 完成：{'　·　'.join(parts)}{warning}",
                type="warning" if not result.within_target else "positive",
            )
        else:
            # 失败原因原样显示，不概括成「生成失败」——人要据此决定是重试还是改配置
            ui.notify(f"{label} 失败：{result.error}", type="negative", timeout=10000)

        on_change()

    ui.timer(0.01, _go, once=True)
