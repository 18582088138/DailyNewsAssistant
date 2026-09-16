"""
从订阅源导入 / Importing from the configured sources.

`dna fetch` 在命令行上天天在用，界面这边先前只有「导入链接」——粘贴一条是一条。
这个对话框把同一个 `intake_sources` 接到界面上，顺便把「最近几天」做成当次选择。
`dna fetch` has always existed on the command line while the workbench only offered
paste-a-link. This dialog wires the same `intake_sources` into the UI.

为什么天数是对话框上的选项，而不是改 `sources.yaml` / Why the day count lives here:
    `sources.yaml` 里那份按源配置是**长期偏好**（这个源发得慢，放宽到 14 天），
    对话框上选的是「这一次我只想要最近三天」的临时意图。写回配置文件的话，
    今天想补一批旧的，明天所有源就永久变宽了。
    The per-source setting is a standing preference; the number picked here is this run's
    intent. Writing it back would make a one-off catch-up permanent.
"""

from __future__ import annotations

from nicegui import ui

from frontends.nicegui_app import actions
from frontends.nicegui_app.intake_progress import IntakeProgress


def open_dialog(*, on_done) -> None:
    """
    弹出订阅导入对话框 / Open the subscription import dialog.

    参数 / Args:
        on_done: 导入结束后的回调，用来刷新表格
    """
    options = actions.source_options()

    with ui.dialog() as dialog, ui.card().classes("w-[640px] wb-dialog").style(
        "background: var(--wb-panel); border: 1px solid var(--wb-line-strong)"
    ):
        ui.label("从订阅导入").classes("text-lg font-medium").style(
            "color: var(--wb-accent)"
        )

        if not options:
            # 一个源都没有时不画那些开关：点了也没东西可抓，摆着只会让人以为程序坏了。
            # None enabled: the switches would do nothing, so they are not drawn.
            ui.label(
                "config/sources.yaml 里没有启用的订阅源。"
                "先在那里把源的 enabled 改成 true，或者用「导入链接」逐条粘。"
            ).classes("wb-path")
            with ui.row().classes("w-full justify-end"):
                ui.button("知道了", on_click=dialog.close).props("flat no-caps")
            dialog.open()
            return

        ui.label(
            f"配置里有 {len(options)} 个启用的源。不选就是全部。"
        ).classes("wb-path")

        picked = (
            ui.select(options, multiple=True, label="订阅源", value=list(options))
            .props("outlined dense use-chips")
            .classes("w-full")
        )

        with ui.row().classes("w-full items-center gap-6"):
            days = ui.number("最近几天", value=3, min=1, max=60, format="%.0f").props(
                "outlined dense"
            ).classes("w-32")
            days.tooltip("按条目的发布时间过滤，覆盖每个源在 sources.yaml 里的设置")
            per_source = ui.number(
                "每源上限", value=10, min=1, max=100, format="%.0f"
            ).props("outlined dense").classes("w-32")
            per_source.tooltip("先按上限取，再过滤——上限调大只是多取几条候选，不额外花钱")

        with ui.row().classes("w-full items-center gap-4"):
            images = ui.switch("下载配图", value=True).props("dense")
            videos = ui.switch("下载视频", value=True).props("dense")
            videos.tooltip("比图片慢得多；失败会提醒但不影响正文")
            refetch = ui.switch("已抓过也重抓", value=False).props("dense")
            refetch.tooltip("默认跳过台账里已有的链接")

        # 用 ui.html 而不是 ui.label：label 不解析标签，加粗会原样显示出来
        ui.html(
            '抓取只下载正文与媒体，<b style="color:var(--wb-accent)">不调用 LLM、'
            "不产生费用</b>。"
        ).classes("wb-path")
        # 这一句必须写在界面上：大量 feed 不给 pubDate，按「未知即旧」丢弃会误杀整个源,
        # 所以程序选择保留它们——但选了「最近 3 天」却抓回来几条旧文章的人会以为是 bug。
        # Many feeds omit pubDate and dropping them would silently kill whole sources, so
        # they are kept — which looks like a bug to someone who asked for three days.
        ui.label(
            "没有发布时间的条目不受天数限制，会照常导入（很多 feed 不给发布时间，"
            "按「未知即旧」丢掉会整个源都抓不到）。"
        ).classes("wb-path")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat no-caps")
            ui.button(
                "开始抓取", icon="rss_feed",
                on_click=lambda: _run(
                    dialog,
                    source_ids=list(picked.value or []),
                    total=len(options),
                    days=int(days.value or 3),
                    per_source=int(per_source.value or 10),
                    images=images.value,
                    videos=videos.value,
                    refetch=refetch.value,
                    on_done=on_done,
                ),
            ).props("no-caps").style("color: var(--wb-accent)")

    dialog.open()


def _run(
    dialog,
    *,
    source_ids: list[str],
    total: int,
    days: int,
    per_source: int,
    images: bool,
    videos: bool,
    refetch: bool,
    on_done,
) -> None:
    """执行采集 / Perform the collection."""
    dialog.close()
    count = len(source_ids) or total
    notice = IntakeProgress(
        f"从 {count} 个源采集最近 {days} 天（不调用 LLM）"
    )

    async def _go() -> None:
        try:
            summary = await actions.import_from_sources(
                source_ids,
                max_age_days=days,
                limit_per_source=per_source,
                download_images=images,
                download_videos=videos,
                refetch=refetch,
                progress=notice.progress,
            )
        except Exception as exc:
            ui.notify(f"采集失败：{exc}", type="negative", timeout=10000)
            return
        finally:
            notice.close()

        ui.notify(f"采集完成：{summary}", type="positive", timeout=10000)
        on_done()

    ui.timer(0.01, _go, once=True)


__all__ = ["open_dialog"]
