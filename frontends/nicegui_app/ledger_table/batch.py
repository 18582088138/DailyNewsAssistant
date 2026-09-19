"""批量操作条：重新抓取与删除 / The batch bar。"""

from __future__ import annotations

from nicegui import ui

from frontends.nicegui_app import actions, theme
from frontends.nicegui_app.ledger_table.state import (
    clear_selection,
    selected_ids,
)

# ---------------------------------------------------------------------------
# 批量操作 / batch actions
# ---------------------------------------------------------------------------
#
# 真正的活在 `actions.batch_refetch` / `actions.batch_delete` 里，这里只画对话框。
# 判据是那句铁律：「这段代码换到另一个前端里要不要重写？」——对话框要，
# 逐篇重抓与先算再删不要，所以它们在 actions/store 层。
# The work lives in `actions`; only the dialogs are here.


def render_batch_bar(container: ui.element, *, on_done) -> None:
    """
    批量操作条 / The batch action bar.

    选中数为 0 时**整条不渲染**，不是渲染成禁用态——一条常驻的灰色按钮条
    每天都在那儿占一行，却一年只用几次。
    Not rendered at all when nothing is picked, rather than rendered disabled: a
    permanently present bar costs a row of screen every day for a few uses a year.
    """
    container.clear()
    ids = selected_ids()
    if not ids:
        return

    with container, ui.row().classes("wb-batch items-center gap-2 no-wrap"):
        ui.label(f"已选 {len(ids)} 篇").classes("count")
        ui.button(
            "重新抓取",
            icon="refresh",
            on_click=lambda: _ask_batch_refetch(on_done=on_done),
        ).props("flat dense no-caps").tooltip("重抓正文与媒体，不调用 LLM、不产生费用")
        ui.button(
            "删除",
            icon="delete_outline",
            on_click=lambda: _ask_batch_delete(on_done=on_done),
        ).props("flat dense no-caps").style("color: var(--wb-danger)")
        ui.button(
            "清空选择", on_click=lambda: (clear_selection(), on_done())
        ).props("flat dense no-caps").style("color: var(--wb-dim)")


def _dialog_card():
    """确认框的外壳 / The shell every confirmation dialog shares."""
    return ui.card().classes("w-[30rem] wb-dialog").style(
        "background: var(--wb-panel); border: 1px solid var(--wb-line-strong)"
    )


def _ask_batch_refetch(*, on_done) -> None:
    """重抓前确认 / Confirm a batch re-fetch."""
    ids = selected_ids()
    if not ids:
        return

    with ui.dialog() as dialog, _dialog_card():
        ui.label(f"重新抓取 {len(ids)} 篇").classes("text-lg font-medium").style(
            "color: var(--wb-accent)"
        )
        with ui.row().classes("items-center gap-4 no-wrap"):
            images = ui.switch("下载配图", value=True).props("dense")
            videos = ui.switch("下载视频", value=True).props("dense")

        ui.html("<b>不调用 LLM、不产生费用</b>，只重抓正文与媒体。").classes(
            "text-xs"
        ).style("color: var(--wb-accent)")
        # 这一句是真的会咬人：重抓会先清空 images/ 与 videos/，而站点换过图之后
        # 旧图就再也拿不回来了。已经生成的文案不受影响（它们是独立的产物行）。
        # This genuinely bites: the media folders are cleared first, and images the site
        # has since swapped are gone for good.
        ui.label(
            "已有的正文与媒体会被这次结果覆盖；站点换过图的话，旧图拿不回来。"
            "已生成的文案不受影响。"
        ).classes("wb-path")
        ui.label(
            "视频抓不下来只提醒（地域限制、会员墙这类重试也没用），不影响正文入库。"
        ).classes("wb-path")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat no-caps")
            ui.button(
                "开始重抓",
                on_click=lambda: (
                    dialog.close(),
                    _run_batch_refetch(
                        ids,
                        download_images=bool(images.value),
                        download_videos=bool(videos.value),
                        on_done=on_done,
                    ),
                ),
            ).props("no-caps")

    dialog.open()


def _run_batch_refetch(
    ids: list[str], *, download_images: bool, download_videos: bool, on_done
) -> None:
    """
    执行批量重抓 / Run the batch re-fetch.

    提示条上的 `i/N` **必须真的在动**：一批十篇要跑好几分钟，一个不动的转圈
    和卡死看起来一模一样（`audio_progress.py` 存在的同一个理由）。
    The `i/N` counter has to move: a batch runs for minutes and a static spinner is
    indistinguishable from a hang.
    """
    total = len(ids)
    notification = ui.notification(
        f"重新抓取 0/{total}…", spinner=True, timeout=None
    )

    def _step(done: int, count: int, title: str) -> None:
        notification.message = (
            f"重新抓取 {done}/{count}　·　{theme.short_title(title, 20)}"
        )

    async def _go() -> None:
        try:
            message = await actions.batch_refetch(
                ids,
                download_images=download_images,
                download_videos=download_videos,
                on_step=_step,
            )
        finally:
            notification.dismiss()
        ui.notify(message, type="info", timeout=10000)
        on_done()

    ui.timer(0.01, _go, once=True)


def _ask_batch_delete(*, on_done) -> None:
    """
    删除前确认 / Confirm a batch delete.

    数字全部来自 `plan_delete()`，不在这里另数一遍——确认框上写的就是接下来
    真要删的那批对象。删除是这套工具里唯一不可撤销的操作。
    Every number comes from `plan_delete()` so the dialog and the execution cannot
    disagree; this is the one irreversible operation here.
    """
    ids = selected_ids()
    if not ids:
        return

    plan = actions.plan_batch_delete(ids)
    if not plan.has_work:
        ui.notify("选中的文章在台账里都已经不存在了", type="warning")
        clear_selection()
        on_done()
        return

    with ui.dialog() as dialog, _dialog_card():
        ui.label("删除文章").classes("text-lg font-medium").style(
            "color: var(--wb-danger)"
        )
        ui.label(f"将删除 {plan.summary()}").classes("text-sm")

        with ui.column().classes("gap-0 w-full"):
            for title in plan.titles[:10]:
                ui.label(f"· {theme.short_title(title, 40)}").classes("wb-path")
            if len(plan.titles) > 10:
                ui.label(f"…… 另有 {len(plan.titles) - 10} 篇").classes("wb-path")

        remove_files = ui.switch("同时删除磁盘文件", value=True).props("dense")
        remove_files.tooltip("关掉只清台账，正文与媒体留在盘上（之后只能手工找）")

        for directory in plan.directories[:3]:
            ui.label(str(directory)).classes("wb-path")
        if len(plan.directories) > 3:
            ui.label(f"…… 另有 {len(plan.directories) - 3} 个目录").classes("wb-path")

        # 目录不安全时明确说出来：台账行照删，目录留给人手工处理
        # Stated explicitly: the row still goes, the directory is left for a human.
        for article_id, store_dir in plan.unsafe_dirs:
            ui.label(
                f"⚠ {article_id[:8]} 的目录 `{store_dir}` 不在 outputs 之内，拒绝删除"
            ).classes("text-xs").style("color: var(--wb-warn)")

        ui.label("不可撤销：重抓也拿不回已经下线的图和视频。").classes("text-xs").style(
            "color: var(--wb-warn)"
        )

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat no-caps")
            ui.button(
                "确认删除",
                on_click=lambda: (
                    dialog.close(),
                    _run_batch_delete(
                        plan.article_ids,
                        remove_files=bool(remove_files.value),
                        on_done=on_done,
                    ),
                ),
            ).props("no-caps").style("color: var(--wb-danger)")

    dialog.open()


def _run_batch_delete(ids: list[str], *, remove_files: bool, on_done) -> None:
    """执行批量删除 / Run the batch delete."""
    notification = ui.notification("删除中…", spinner=True, timeout=None)

    async def _go() -> None:
        try:
            message = await actions.batch_delete(ids, remove_files=remove_files)
        finally:
            notification.dismiss()
        # 删完清空选择：留着的话批量条上还挂着一串已经不存在的 id，
        # 下一次点「删除」会得到一个空计划。
        # Cleared afterwards, or the bar would still count ids that no longer exist.
        clear_selection()
        ui.notify(message, type="positive", timeout=8000)
        on_done()

    ui.timer(0.01, _go, once=True)
