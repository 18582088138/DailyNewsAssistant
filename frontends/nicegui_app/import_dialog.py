"""
链接导入 / Link import.

粘一段文字进来，把里面的链接**全部**抓下来。
Paste a block of text; every link inside it gets fetched.

为什么接受一整段文字而不是一行一个链接 / Why a whole block rather than one URL per line:
    实际的投递长这样——从微信、群聊、收藏夹里复制出来的一段话，链接夹在中文之间，
    结尾还带着中文句号。要求人先把链接一条条摘干净，等于把最烦的一步推给用户。
    `extract_urls` 已经处理了提取、去重、剥掉结尾标点。
    Real submissions look like a paragraph copied out of a chat with links embedded
    between Chinese sentences and trailing full-width punctuation. Making the user clean
    that up by hand pushes the tedious step onto them; `extract_urls` already handles
    extraction, de-duplication and trailing punctuation.

先认再抓 / Recognise before fetching:
    输入框旁边实时显示「识别到 N 条链接」并列出来。粘一整段聊天记录时，
    这是**唯一**能提前发现少粘了一条、或者多认了一个图片地址的机会——
    抓完再发现，台账里已经留下垃圾行了。
    The count and list update as you type. When a whole chat log is pasted this is the
    only chance to spot a missing link or a stray image URL before junk rows land in the
    ledger.
"""

from __future__ import annotations

from nicegui import ui

from frontends.nicegui_app import actions


def open_dialog(*, on_done) -> None:
    """
    弹出导入对话框 / Open the import dialog.

    参数 / Args:
        on_done: 导入结束后的回调，用来刷新表格
    """
    with ui.dialog() as dialog, ui.card().classes("w-[640px]").style(
        "background: var(--wb-panel); border: 1px solid var(--wb-line-strong)"
    ):
        ui.label("导入链接").classes("text-lg font-medium").style(
            "color: var(--wb-accent)"
        )
        ui.label(
            "支持一次多条：直接粘贴一整段带链接的文字也行，会自动认出里面所有链接。"
        ).classes("wb-path")

        box = (
            ui.textarea(
                placeholder=(
                    "https://mp.weixin.qq.com/s/xxxx\n"
                    "https://www.qbitai.com/2026/09/xxxx.html\n"
                    "\n或者直接粘一段：「这篇不错 https://… 还有这个 https://…」"
                )
            )
            .props("outlined autogrow input-style='min-height:150px'")
            .classes("w-full")
        )

        found = ui.label("").classes("wb-path")
        listing = ui.column().classes("w-full gap-0")

        def rescan() -> None:
            """实时列出认出来的链接 / List the recognised links as you type."""
            urls = actions.preview_links(box.value or "")
            listing.clear()
            if not urls:
                found.set_text("还没有识别到链接")
                return
            found.set_text(f"识别到 {len(urls)} 条链接")
            with listing:
                for index, url in enumerate(urls[:12], start=1):
                    ui.label(f"{index}. {url}").classes("wb-path")
                if len(urls) > 12:
                    ui.label(f"…… 另有 {len(urls) - 12} 条").classes("wb-path")

        box.on("keyup", rescan)
        box.on("paste", lambda: ui.timer(0.05, rescan, once=True))

        with ui.row().classes("w-full items-center gap-4"):
            images = ui.switch("下载配图", value=True).props("dense")
            videos = ui.switch("下载视频", value=True).props("dense")
            refetch = ui.switch("已抓过也重抓", value=False).props("dense")
            refetch.tooltip("默认跳过台账里已有的链接，避免重复下载")

        # 用 ui.html 而不是 ui.label：label 不解析 Markdown，星号会原样显示出来
        # `ui.html` rather than `ui.label`: a label renders the asterisks literally.
        ui.html(
            '抓取只下载正文与媒体，<b style="color:var(--wb-accent)">不调用 LLM、'
            "不产生费用</b>。要不要生成文案，抓完在表格里逐格决定。"
        ).classes("wb-path")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat no-caps")
            ui.button(
                "开始抓取", icon="download",
                on_click=lambda: _run(
                    dialog, box.value or "",
                    images=images.value, videos=videos.value,
                    refetch=refetch.value, on_done=on_done,
                ),
            ).props("no-caps").style("color: var(--wb-accent)")

    dialog.open()


def _run(dialog, text: str, *, images: bool, videos: bool, refetch: bool, on_done) -> None:
    """执行导入 / Perform the import."""
    urls = actions.preview_links(text)
    if not urls:
        ui.notify("没有识别到链接，检查一下粘贴的内容", type="warning")
        return

    dialog.close()
    notification = ui.notification(
        f"抓取 {len(urls)} 条链接中…（下载正文与媒体，不调用 LLM）",
        spinner=True,
        timeout=None,
    )

    async def _go() -> None:
        try:
            summary = await actions.import_links(
                text,
                download_images=images,
                download_videos=videos,
                refetch=refetch,
            )
        finally:
            notification.dismiss()

        ui.notify(f"导入完成：{summary}", type="positive", timeout=8000)
        on_done()

    ui.timer(0.01, _go, once=True)


__all__ = ["open_dialog"]
