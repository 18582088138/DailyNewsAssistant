"""
台账工作台 / The article workbench.

    dna gui

装配与路由，**不含业务逻辑**——按钮的动作在 `actions.py`，实际的活在
`dna.produce`。这是本项目的铁律：前端可以整个换掉而核心不动。
Assembly and routing only. Button behaviour lives in `actions.py` and the actual work in
`dna.produce`. That separation is a standing rule here: the front-end can be replaced
wholesale without touching the core.

打开界面本身不产生任何费用 / Opening the workbench costs nothing:
    只有点「生成」按钮才会调用 LLM。表格数据全部来自本地 SQLite 与磁盘文件。
    Only the generate buttons call an LLM; everything shown comes from the local ledger
    and files on disk.
"""

from __future__ import annotations

from nicegui import ui

from dna.core.config import get_settings
from dna.core.logging import setup_logging
from dna.produce import DISPLAY_ORDER
from dna.store import Ledger
from frontends.nicegui_app import (
    actions,
    import_dialog,
    ledger_table,
    settings_dialog,
    source_dialog,
    theme,
)

PAGE_TITLE = "DailyNewsAssistant · 文章台账"


def build() -> None:
    """装配页面 / Assemble the page."""

    @ui.page("/")
    def index() -> None:  # noqa: ANN202 - NiceGUI 页面函数
        _render_page()


def _render_page() -> None:
    """渲染整个工作台 / Render the workbench."""
    state = {
        "status": None,
        "source": None,
        "search": None,
        "page": 1,
        "page_size": 50,
        "only_gaps": False,
        "only_new": False,
        "pager": None,      # 页码控件，建好后回填 / the pagination control
        "syncing": False,   # 正在回写页码，别再触发刷新 / suppress the echo
    }

    ui.page_title(PAGE_TITLE)
    theme.apply(kind_count=len(DISPLAY_ORDER))

    with ui.header().classes("wb-header items-center justify-between px-4 py-2"):
        with ui.row().classes("items-baseline gap-2 no-wrap"):
            ui.label("DAILYNEWS").classes("wb-title text-base font-medium")
            ui.label("//").classes("wb-title text-base").style("color: var(--wb-faint)")
            ui.label("文章台账").classes("wb-title text-base accent")
        with ui.row().classes("items-center gap-3 no-wrap"):
            tts_chip = ui.label("○ TTS ?").classes("wb-path cursor-pointer")
            cost_label = ui.label("").classes("wb-path")

    # TTS 服务状态一直挂在顶栏 / the TTS service state stays visible
    #
    # 合成那条路会自动拉起服务、并等模型冷启动最多两分钟，而这一步在界面上
    # 完全没有痕迹。芯片点一下＝显式启动，于是「服务到底起没起」不再靠猜。
    # The synthesis path silently spawns the service and waits out a cold start;
    # the chip makes that state visible and clicking it starts the service explicitly.
    async def probe_tts() -> None:
        status = await actions.tts_status()
        tts_chip.set_text(
            ("● TTS " if status.online else "○ TTS ") + status.url.split("//")[-1]
        )
        tts_chip.style(
            "color: var(--wb-accent)" if status.online else "color: var(--wb-faint)"
        )
        tts_chip.tooltip(status.detail + ("" if status.online else "　（点击启动）"))

    async def start_tts() -> None:
        # 已在线时 `ensure_service` 立即返回，所以这句话要同时覆盖「检查」
        # Wording covers both cases: ensure_service returns at once when already up.
        note = ui.notification("正在检查／启动 TTS 服务（模型冷启动可能要一两分钟）…",
                               spinner=True, timeout=None)
        try:
            status = await actions.tts_start()
            ui.notify(status.detail, type="positive")
        except Exception as exc:  # noqa: BLE001 - 原因原样显示，里面写了怎么排查
            ui.notify(f"TTS 服务启动失败：{exc}", type="negative", timeout=12000,
                      multi_line=True, close_button=True)
        finally:
            note.dismiss()
            await probe_tts()

    tts_chip.on("click", start_tts)
    ui.timer(0.1, probe_tts, once=True)

    # 容器先占位、**最后再挂到页面上**：筛选栏的回调里要调用 refresh()，
    # 而 refresh() 要往容器里画——两者互相引用，只能靠「先建对象、后定位置」拆开。
    # 直接在这里 `ui.column()` 会让表格排在筛选栏上面（NiceGUI 按创建顺序布局）。
    # The container is created first but placed last: the filter callbacks call refresh(),
    # which draws into the container, so the two reference each other. Creating it here
    # would also put the table above the filters, since NiceGUI lays out in creation order.
    table_container = ui.column().classes("w-full px-4 pb-4 gap-0")

    def refresh_batch_bar() -> None:
        """
        只重画批量操作条 / Redraw the batch bar alone.

        勾一下复选框只是让一个数字变了，没有理由重建整张表（几百个元素）。
        A tick changes one number; rebuilding hundreds of elements for it is waste.
        """
        ledger_table.render_batch_bar(batch_container, on_done=refresh)

    def refresh() -> None:
        """重新读数据并重建表格 / Reload and rebuild."""
        view = actions.load_rows(
            status=state["status"],
            source=state["source"],
            search=state["search"],
            page=state["page"],
            page_size=state["page_size"],
            only_gaps=state["only_gaps"],
            only_new=state["only_new"],
        )
        # 页码可能被后端夹回来（删了几条之后停在不存在的第 7 页）
        # The backend may clamp the page after rows disappear underneath it.
        state["page"] = view.page
        ledger_table.render_table(
            table_container, view.rows, on_change=refresh, on_select=refresh_batch_bar
        )
        refresh_batch_bar()

        # 新导入的条数单独报：粘完一批链接之后，这个数字就是「还有几条没动过」
        # Reported separately: right after pasting a batch this number is the backlog.
        fresh = sum(1 for row in view.rows if row.is_new)
        text = f"第 {view.page}/{view.pages} 页　·　共 {view.total} 条"
        if fresh:
            text += f"　·　本页 {fresh} 条新导入"
        if view.scanned_cap:
            text += f"　·　筛自最近 {actions.SCAN_CAP} 条"
        count_label.set_text(text)
        _sync_pagination(view)
        # 费用在每次操作后刷新——它必须一直是当前值，否则等于没显示
        # Refreshed after every action: a stale cost readout is no better than none.
        cost_label.set_text(actions.cache_status())

    def go_filter(**changes: object) -> None:
        """
        改筛选条件 / Change a filter —— **一律回到第 1 页**。

        不归位的话，在第 7 页上勾一个筛选、结果只有 3 条，看到的是一张空表，
        观感上等于「筛没了」。
        Without the reset, filtering while deep in the pages shows an empty table.
        """
        state.update(changes)
        state["page"] = 1
        refresh()

    def _sync_pagination(view) -> None:  # noqa: ANN001 - actions.PageView
        """
        把页码控件对齐到刚画出来的这一页 / Align the control with the drawn page.

        回写 `value` 会触发它自己的 `on_value_change`，所以要挡一下——
        否则「刷新→回写→再刷新」会转起来。
        Writing the value fires the control's own handler; the guard stops the loop.
        """
        pager = state.get("pager")
        if pager is None:
            return
        pager.set_visibility(view.pages > 1)
        pager.props(f"max={view.pages}")
        if pager.value != view.page:
            state["syncing"] = True
            try:
                pager.value = view.page
            finally:
                state["syncing"] = False

    # -- 筛选栏 / filter bar --------------------------------------------------
    with ui.row().classes("wb-filters w-full items-center gap-3 px-4 pt-3 no-wrap"):
        search_input = ui.input(placeholder="搜索标题或链接").props("dense outlined clearable").classes("w-64")
        search_input.on(
            "keydown.enter", lambda: go_filter(search=search_input.value or None)
        )

        source_select = ui.select(
            _source_options(), value=None, label="来源", clearable=True
        ).props("dense outlined").classes("w-40")
        source_select.on_value_change(lambda e: go_filter(source=e.value))

        status_select = ui.select(
            {None: "全部", "ok": "ok", "degraded": "degraded", "failed": "failed"},
            value=None,
            label="抓取状态",
        ).props("dense outlined").classes("w-36")
        status_select.on_value_change(lambda e: go_filter(status=e.value))

        new_toggle = ui.switch("只看新导入").props("dense")
        new_toggle.tooltip("刚导入、还没调过 LLM 的（粘完一批链接后用它把它们挑出来）")
        new_toggle.on_value_change(lambda e: go_filter(only_new=e.value))

        gaps_toggle = ui.switch("只看有缺口的").props("dense")
        gaps_toggle.on_value_change(lambda e: go_filter(only_gaps=e.value))

        ui.space()
        count_label = ui.label("").classes("wb-path")
        # 导入是这张表的入口，放在最显眼的位置；它**不调用 LLM、不花钱**
        # Import is how articles enter this table, so it gets the prominent slot. It calls
        # no LLM and costs nothing.
        ui.button(
            "导入链接", icon="add_link",
            on_click=lambda: import_dialog.open_dialog(on_done=refresh),
        ).props("no-caps unelevated dense").style(
            "background: var(--wb-accent); color: #06231a; font-weight:600"
        ).tooltip("粘贴一段带链接的文字，自动认出全部链接并抓取（不产生费用）")
        # 订阅导入放在导入链接旁边而不是塞进菜单：这两个是同一件事的两种来源，
        # 一个是手动投递、一个是订阅源，谁也不比谁次要。
        # Beside it rather than in a menu: the two are the same act from different sources.
        ui.button(
            "从订阅导入", icon="rss_feed",
            on_click=lambda: source_dialog.open_dialog(on_done=refresh),
        ).props("no-caps unelevated dense outline").style(
            "color: var(--wb-accent)"
        ).tooltip("把配置里启用的订阅源最近几天的新文章批量抓下来（不产生费用）")
        ui.button(icon="refresh", on_click=refresh).props("flat dense round").tooltip("刷新")
        # 设置改完字数窗口，表格里的超长标记要跟着重判——所以回调是 refresh
        ui.button(
            icon="settings",
            on_click=lambda: settings_dialog.open_dialog(on_saved=refresh),
        ).props("flat dense round").tooltip("设置：内容偏好与运行配置")

    # 图例 + 批量操作条同占一行：批量条只在有勾选时出现，平时这一行就是纯图例。
    # 放在这里而不是表格上方另起一行，是为了不让表格在勾选时上下跳。
    # They share the row so the table does not jump when a selection appears.
    with ui.row().classes("w-full items-center gap-4 px-4 py-2 no-wrap"):
        batch_container = ui.row().classes("items-center gap-2 no-wrap")
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.label("NEW").classes("wb-new-badge")
            ui.label("刚导入，未调用 LLM").classes("wb-path")
        for glyph, text, tone in (
            ("●", "已生成", "wb-ok"),
            ("●", "超出字数区间", "wb-over"),
            ("○", "未生成", "wb-empty"),
            ("▲", "失败", "wb-fail"),
            ("—", "本篇不适用", "wb-na"),
        ):
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.label(glyph).classes(tone).style("font-size:12px")
                ui.label(text).classes("wb-path")
        ui.space()
        ui.label("点格子看内容　·　重做按钮在展开面板里，不会误触").classes("wb-path")

        # 翻页 / paging
        #
        # 表格原来写死只显示最近 50 条，于是导入一批新文章就把手头在办的那些
        # 顶出了可见范围——用户看到的「订阅导入把之前的覆盖掉了」其实是这个。
        # 数据一直都在（`register()` 对已存在的行只刷新 feed_title）。
        # The table used to show only the newest 50 rows, so an import pushed
        # work-in-progress articles out of sight; nothing was ever overwritten.
        def _go_page(value: object) -> None:
            if state["syncing"]:
                return
            state["page"] = int(value or 1)
            refresh()

        def _go_size(value: object) -> None:
            state["page_size"] = int(value or 50)
            state["page"] = 1
            refresh()

        size_select = ui.select(
            [25, 50, 100, 200], value=state["page_size"], label="每页",
        ).props("dense outlined").classes("w-28")
        size_select.on_value_change(lambda e: _go_size(e.value))

        state["pager"] = ui.pagination(
            1, 1, value=1, direction_links=True,
            on_change=lambda e: _go_page(e.value),
        ).props("dense gutter=xs input")

    # 表格排到最后 / the table goes last
    #
    # 容器必须**先于筛选栏创建**（回调要引用它），但必须**后于筛选栏显示**——
    # NiceGUI 按创建顺序布局，不移的话表格会跑到筛选栏上面去。
    # The container must exist before the filter bar (the callbacks close over it) yet
    # appear after it; NiceGUI lays out in creation order, so it is moved into place.
    table_container.move(target_index=-1)
    refresh()


def _source_options() -> dict:
    """
    来源下拉选项 / The source dropdown options.

    从台账里实际出现过的来源生成，而不是读 sources.yaml——
    配置里启用的源不一定抓到过东西，抓到过的也可能已经被禁用。
    Built from the sources that actually appear in the ledger rather than sources.yaml:
    an enabled source may have produced nothing, and a disabled one may still have rows.
    """
    counts = Ledger(get_settings().db_file).count_by_source()
    options: dict = {None: "全部"}
    options.update({name: f"{name}（{n}）" for name, n in sorted(counts.items())})
    return options


def run(*, host: str = "127.0.0.1", port: int = 8080, show: bool = True) -> None:
    """
    启动服务 / Start the server.

    `reload=False` 是必须的：NiceGUI 的热重载会重新导入入口模块，
    而我们是从 CLI 的子命令里调进来的，重载会把整个 typer 应用再跑一遍。
    `reload=False` is required: NiceGUI's hot reload re-imports the entry module, and
    since this is invoked from a Typer subcommand that would re-run the whole CLI app.
    """
    # **日志必须落到文件。**界面把后端的报错压成一句话（「5 段全部合成失败」），
    # 而每段的真实原因走的是 `logger.warning`——没有文件的话那些行就只在这个
    # 没人看的终端里滚过去，实测因此查一个已知原因花掉了一整轮。
    # INFO 级：这个终端不是给人读的，页面才是界面，多一点上下文只有好处。
    # The page compresses backend errors into one line while the reasons go to the log;
    # without a file they scroll past in a terminal nobody is watching.
    settings = get_settings()
    setup_logging(level="INFO", log_dir=settings.data_path / "logs", log_file="dna.log")
    build()
    ui.run(host=host, port=port, show=show, reload=False, title=PAGE_TITLE, favicon="📰")


__all__ = ["build", "run"]
