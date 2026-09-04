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
from dna.produce import DISPLAY_ORDER
from dna.store import Ledger
from frontends.nicegui_app import actions, import_dialog, ledger_table, theme

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
        "limit": 50,
        "only_gaps": False,
        "only_new": False,
    }

    ui.page_title(PAGE_TITLE)
    theme.apply(kind_count=len(DISPLAY_ORDER))

    with ui.header().classes("wb-header items-center justify-between px-4 py-2"):
        with ui.row().classes("items-baseline gap-2 no-wrap"):
            ui.label("DAILYNEWS").classes("wb-title text-base font-medium")
            ui.label("//").classes("wb-title text-base").style("color: var(--wb-faint)")
            ui.label("文章台账").classes("wb-title text-base accent")
        cost_label = ui.label("").classes("wb-path")

    # 容器先占位、**最后再挂到页面上**：筛选栏的回调里要调用 refresh()，
    # 而 refresh() 要往容器里画——两者互相引用，只能靠「先建对象、后定位置」拆开。
    # 直接在这里 `ui.column()` 会让表格排在筛选栏上面（NiceGUI 按创建顺序布局）。
    # The container is created first but placed last: the filter callbacks call refresh(),
    # which draws into the container, so the two reference each other. Creating it here
    # would also put the table above the filters, since NiceGUI lays out in creation order.
    table_container = ui.column().classes("w-full px-4 pb-4 gap-0")

    def refresh() -> None:
        """重新读数据并重建表格 / Reload and rebuild."""
        rows = actions.load_rows(
            status=state["status"],
            source=state["source"],
            search=state["search"],
            limit=state["limit"],
            only_gaps=state["only_gaps"],
            only_new=state["only_new"],
        )
        ledger_table.render_table(table_container, rows, on_change=refresh)

        # 新导入的条数单独报：粘完一批链接之后，这个数字就是「还有几条没动过」
        # Reported separately: right after pasting a batch this number is the backlog.
        fresh = sum(1 for row in rows if row.is_new)
        count_label.set_text(
            f"显示 {len(rows)} 条" + (f"　·　{fresh} 条新导入" if fresh else "")
        )
        # 费用在每次操作后刷新——它必须一直是当前值，否则等于没显示
        # Refreshed after every action: a stale cost readout is no better than none.
        cost_label.set_text(actions.cache_status())

    # -- 筛选栏 / filter bar --------------------------------------------------
    with ui.row().classes("wb-filters w-full items-center gap-3 px-4 pt-3 no-wrap"):
        search_input = ui.input(placeholder="搜索标题或链接").props("dense outlined clearable").classes("w-64")
        search_input.on(
            "keydown.enter", lambda: (state.update(search=search_input.value or None), refresh())
        )

        source_select = ui.select(
            _source_options(), value=None, label="来源", clearable=True
        ).props("dense outlined").classes("w-40")
        source_select.on_value_change(
            lambda e: (state.update(source=e.value), refresh())
        )

        status_select = ui.select(
            {None: "全部", "ok": "ok", "degraded": "degraded", "failed": "failed"},
            value=None,
            label="抓取状态",
        ).props("dense outlined").classes("w-36")
        status_select.on_value_change(
            lambda e: (state.update(status=e.value), refresh())
        )

        new_toggle = ui.switch("只看新导入").props("dense")
        new_toggle.tooltip("刚导入、还没调过 LLM 的（粘完一批链接后用它把它们挑出来）")
        new_toggle.on_value_change(
            lambda e: (state.update(only_new=e.value), refresh())
        )

        gaps_toggle = ui.switch("只看有缺口的").props("dense")
        gaps_toggle.on_value_change(
            lambda e: (state.update(only_gaps=e.value), refresh())
        )

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
        ui.button(icon="refresh", on_click=refresh).props("flat dense round").tooltip("刷新")

    # 图例：格子只有四种状态，写在表上方比让人猜快得多
    # A legend: four cell states, faster to state than to infer.
    with ui.row().classes("w-full items-center gap-4 px-4 py-2 no-wrap"):
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.label("NEW").classes("wb-new-badge")
            ui.label("刚导入，未调用 LLM").classes("wb-path")
        for glyph, text, tone in (
            ("●", "已生成", "wb-ok"),
            ("○", "未生成", "wb-empty"),
            ("▲", "失败", "wb-fail"),
            ("—", "本篇不适用", "wb-na"),
        ):
            with ui.row().classes("items-center gap-1 no-wrap"):
                ui.label(glyph).classes(tone).style("font-size:12px")
                ui.label(text).classes("wb-path")
        ui.space()
        ui.label("点格子看内容　·　重做按钮在展开面板里，不会误触").classes("wb-path")

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
    build()
    ui.run(host=host, port=port, show=show, reload=False, title=PAGE_TITLE, favicon="📰")


__all__ = ["build", "run"]
