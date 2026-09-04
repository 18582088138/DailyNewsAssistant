"""
台账表格 / The workbench table.

一篇文章一行，每种产物一列。这是整个应用被用得最频繁的界面。
One row per article, one column per production kind — the screen this application gets
used from.

三个布局决定 / Three layout decisions:

1. **用 CSS Grid，不用 flex。** 先前是 `flex + 固定宽度 + no-wrap`，窗口一窄，
   `flex-1` 的标题列被压到零宽以下，后面的固定宽度列就**叠在标题上**。
   Grid 配 `min-width` 从根上不可能重叠——宽度不够就出横向滚动条。
   Grid rather than flex: the old flex row let the flex-1 title collapse past zero width
   and the fixed columns rode over it. A grid with a min-width scrolls instead.

2. **表头 sticky。** 表格自己是滚动容器，表头 `position: sticky; top: 0`。
   翻到第 30 行时还能看见哪一列是哪个功能——列宽只有 108px，
   没有表头根本认不出「口播」和「短视频」。
   The header sticks: at row thirty the columns are 108 px wide and indistinguishable
   without it.

3. **格子只负责打开内容，重做按钮在展开面板里。**
   两个动作的代价完全不对等（一个免费、一个计费），不该挨在一起。
   The cell opens content; redo lives in the panel. The two actions cost wildly different
   amounts and should not sit a dozen pixels apart.

为什么状态直接印在格子里 / Why the numbers sit in the cell:
    「生成了」和「生成得对不对」是两回事。一段 47 秒的短视频稿状态上是成功的，
    但它超了 35 秒上限、发不出去。数字摆在表面，一眼扫得出哪几篇要重做。
    A 47-second short-video script counts as a success yet overruns the ceiling and
    cannot be posted. Surfacing the number makes those scannable.
"""

from __future__ import annotations

from nicegui import ui

from dna.produce import DISPLAY_ORDER, ProductionKind, spec
from dna.store.ledger import ProductionRecord
from frontends.nicegui_app import actions, detail_panel, theme
from frontends.nicegui_app.actions import RowView

# 抓取状态 → 颜色 / fetch status to colour
_STATUS_COLOUR = {
    "ok": "positive",
    "degraded": "warning",
    "failed": "negative",
    "pending": "grey",
}

# 表头缩写：108px 放不下「短视频文案」四个字还带内边距
# Header abbreviations: 108 px cannot hold the full four-character labels with padding.
_HEAD_SHORT = {
    ProductionKind.SUMMARY_ZH: "总结",
    ProductionKind.SUMMARY_EN: "英文总结",
    ProductionKind.SHORTVIDEO: "短视频",
    ProductionKind.NARRATION: "口播",
    ProductionKind.LONGFORM: "长文案",
}


def render_table(container: ui.element, rows: list[RowView], *, on_change) -> None:
    """
    渲染整张表 / Render the whole table.

    参数 / Args:
        on_change: 产物变化后的回调，用于刷新表格
    """
    container.clear()

    with container:
        if not rows:
            with ui.column().classes("w-full items-center gap-2 p-10"):
                ui.icon("inbox").classes("text-4xl").style("color: var(--wb-faint)")
                ui.label("没有匹配的文章").style("color: var(--wb-dim)")
                ui.label("先跑 dna fetch 或 dna add <链接>").classes("wb-path")
            return

        with ui.element("div").classes("wb-scroll w-full"):
            with ui.element("div").classes("wb-table"):
                _render_header()
                for row in rows:
                    _render_row(row, on_change=on_change)


def _render_header() -> None:
    """
    表头 / The header row.

    `sticky top-0` 由 `.wb-head` 的 CSS 提供；背景必须**不透明**，
    否则滚上来的行会从表头底下透出来。
    Stickiness comes from the CSS. The background must be opaque or rows scrolling
    underneath show through.
    """
    with ui.element("div").classes("wb-head wb-grid"):
        with _cell("wb-h"):
            ui.label("标题 / 来源")
        with _cell("wb-h"):
            ui.label("正文")
        with _cell("wb-h"):
            ui.label("媒体")
        for kind in DISPLAY_ORDER:
            with _cell("wb-h"):
                ui.label(_HEAD_SHORT.get(kind, spec(kind).label)).tooltip(spec(kind).label)


def _cell(classes: str = ""):
    """一个格子容器 / One grid cell container."""
    return ui.element("div").classes(classes)


def _render_row(row: RowView, *, on_change) -> None:
    """
    一行 / One article row.

    展开状态**自己管理**，不用 `ui.expansion`：需要「点第 3 格 → 展开并停在第 3 格」
    这种定位，而 expansion 的 header 里任何点击都会触发它自己的开合，
    跟格子的点击事件打架。
    Expansion state is managed here rather than with `ui.expansion`, because clicking a
    specific cell must open the panel *on that cell*, and any click inside an expansion
    header also toggles the expansion itself.
    """
    article = row.article
    wrapper = ui.element("div").classes("wb-rowwrap")
    if row.is_new:
        wrapper.classes(add="is-new")

    with wrapper:
        grid = ui.element("div").classes("wb-row wb-grid")
        panel = ui.element("div").classes("wb-detail")
        panel.visible = False

        # 当前展开的是哪一格；None 表示这一行是收起的
        state: dict[str, ProductionKind | None] = {"open": None}
        cells: dict[ProductionKind, ui.element] = {}

        def toggle(kind: ProductionKind) -> None:
            """点同一格收起，点别的格切过去 / Same cell closes, another switches."""
            if state["open"] == kind:
                state["open"] = None
                panel.visible = False
                wrapper.classes(remove="is-open")
            else:
                state["open"] = kind
                panel.visible = True
                wrapper.classes(add="is-open")
                detail_panel.render(
                    panel, row, kind, on_change=on_change, on_redo=_launch
                )
            for k, element in cells.items():
                element.classes(**({"add": "sel"} if k == state["open"] else {"remove": "sel"}))

        with grid:
            _render_title_cell(row, on_open=lambda: toggle(_first_kind(row)))
            with _cell("wb-num"):
                ui.label(row.body_label)
            with _cell("wb-num"):
                ui.label(row.media_label)
            for kind in DISPLAY_ORDER:
                cells[kind] = _render_kind_cell(row, kind, on_open=toggle)

        _ = article  # 供调试时定位这一行 / kept for debugging identification


def _first_kind(row: RowView) -> ProductionKind:
    """点标题时默认展开哪一格 / Which cell the title click opens."""
    for kind in DISPLAY_ORDER:
        record = row.production(kind)
        if record is not None and record.ok:
            return kind
    return DISPLAY_ORDER[0]


def _render_title_cell(row: RowView, *, on_open) -> None:
    """标题格：标题 + 来源 + 抓取状态 + 原文链接 / Title, source, status, link."""
    article = row.article

    with ui.element("div").classes("wb-cell-title") as cell:
        with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
            # 标识放在标题**前面**：从上往下扫的时候，左对齐的东西才扫得到，
            # 挂在标题后面会随标题长短左右浮动。
            # Placed before the title: a left-aligned marker is scannable down a column,
            # whereas one trailing the title drifts with the title's length.
            if row.is_new:
                ui.label("NEW").classes("wb-new-badge shrink-0").tooltip(
                    "刚导入、还没调过 LLM。生成任意一项产物后这个标识就消失"
                )
            ui.label(theme.short_title(article.title)).classes("t").tooltip(
                article.title or "(无标题)"
            )
        with ui.row().classes("items-center gap-2 no-wrap").style("margin-top:3px"):
            ui.label(article.source_id or "user").classes("wb-cell-meta")
            ui.badge(str(article.status)).props(
                f"color={_STATUS_COLOUR.get(str(article.status), 'grey')} outline"
            ).style("font-size:9px; padding:0 5px")
            ui.link("原文", article.url, new_tab=True).classes("wb-cell-meta").style(
                "color: var(--wb-dim)"
            )

    # 链接的点击不该连带展开这一行 / a link click must not also toggle the row
    cell.on("click", lambda: on_open())


def _render_kind_cell(row: RowView, kind: ProductionKind, *, on_open) -> ui.element:
    """
    一个产物格 / One production cell.

    整格可点，点开的是**内容**。格子上没有任何会花钱的按钮。
    The whole cell is clickable and opens content. Nothing on it spends money.
    """
    task = spec(kind)
    record = row.production(kind)
    too_short = task.min_body_chars and row.article.text_len < task.min_body_chars

    if too_short:
        cell = ui.element("div").classes("wb-kind wb-na")
        with cell:
            ui.label("—").classes("glyph")
            ui.label("不适用").classes("val")
        cell.tooltip(
            f"正文 {row.article.text_len} 字，不足 {task.min_body_chars} 字，"
            f"本篇撑不起{task.label}"
        )
        return cell

    glyph, value, tone = _cell_state(record)
    cell = ui.element("div").classes(f"wb-kind {tone}")
    with cell:
        ui.label(glyph).classes("glyph")
        ui.label(value).classes("val")
    cell.tooltip(_cell_tooltip(record, task.label))
    cell.on("click", lambda k=kind: on_open(k))
    return cell


def _cell_state(record: ProductionRecord | None) -> tuple[str, str, str]:
    """
    格子的形状、数值与配色 / The cell's glyph, value and tone.

    **形状与颜色成对出现**，不能只靠颜色——色觉障碍者和黑白截图下都要能分辨。
    Glyph and colour always travel together: colour alone fails for colour-blind users and
    in greyscale screenshots.

    已生成的显示**时长而不是「已完成」**：时长决定这个产物能不能用，
    而「已完成」不比一个填满的格子多说任何东西。
    A filled cell shows its duration rather than the word "done", because the duration is
    what decides usability and "done" adds nothing the fill does not already say.
    """
    if record is None:
        return "○", "未生成", "wb-empty"
    if not record.ok:
        return "▲", "失败", "wb-fail"

    if record.est_seconds:
        minutes = record.est_seconds / 60
        value = f"{record.est_seconds:.0f}s" if minutes < 1.5 else f"{minutes:.0f}min"
    else:
        value = f"{record.chars}字"
    return "●", value, "wb-ok"


def _cell_tooltip(record: ProductionRecord | None, label: str) -> str:
    """悬停时的详情 / Details on hover."""
    if record is None:
        return f"{label}：尚未生成　·　点击展开后再决定是否生成"
    if not record.ok:
        return f"{label} 生成失败：{record.error or '未知原因'}　·　点击查看"

    parts = [f"{record.chars} 字"]
    if record.est_seconds:
        parts.append(f"约 {record.est_seconds:.0f} 秒")
    if record.variant:
        parts.append("专题" if record.variant == "feature" else "访谈")
    parts.append(f"{record.llm_model or '未知模型'}")
    if record.created_at:
        parts.append(record.created_at.strftime("%m-%d %H:%M"))
    if record.calls > 1:
        parts.append(f"{record.calls} 次调用")
    return f"{label}：{'　·　'.join(parts)}　·　点击展开查看全文"


# ---------------------------------------------------------------------------
# 生成与重做 / running a production
# ---------------------------------------------------------------------------


def _launch(row: RowView, kind: ProductionKind, *, force: bool, on_change) -> None:
    """
    发起一次生成 / Kick off one production.

    长文案先弹确认框：它一篇 5~9 次调用，是其余产物的好几倍，
    **不该和其它按钮一样一点就跑**。
    The long-form script asks first: at five to nine calls it costs several times more
    than the others and should not fire on a single click like the rest.
    """
    if spec(kind).needs_variant:
        _ask_longform(row, kind, force=force, on_change=on_change)
        return
    _run(row, kind, variant=None, force=force, on_change=on_change)


def _ask_longform(row: RowView, kind: ProductionKind, *, force: bool, on_change) -> None:
    """长文案的形式选择与费用确认 / Mode choice and cost confirmation."""
    task = spec(kind)

    with ui.dialog() as dialog, ui.card().classes("w-96").style(
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
                    _run(row, kind, variant=mode.value, force=force, on_change=on_change),
                ),
            ).props("no-caps").classes("wb-btn-cost")

    dialog.open()


def _run(row: RowView, kind: ProductionKind, *, variant, force: bool, on_change) -> None:
    """执行生成并把结果告诉用户 / Run the production and report back."""
    label = spec(kind).label
    notification = ui.notification(
        f"{label} 生成中…（调用 LLM，请稍候）", spinner=True, timeout=None
    )

    async def _go() -> None:
        try:
            result = await actions.run_production(
                row.article.id, kind, variant=variant, force=force
            )
        finally:
            notification.dismiss()

        if result.ok and result.skipped:
            ui.notify(f"{label}：已存在，未重新生成", type="info")
        elif result.ok:
            warning = "，⚠️ 超出目标时长区间" if not result.within_target else ""
            ui.notify(
                f"{label} 完成：{result.chars} 字，{result.calls} 次调用{warning}",
                type="warning" if not result.within_target else "positive",
            )
        else:
            # 失败原因原样显示，不概括成「生成失败」——人要据此决定是重试还是改配置
            ui.notify(f"{label} 失败：{result.error}", type="negative", timeout=10000)

        on_change()

    ui.timer(0.01, _go, once=True)


__all__ = ["render_table"]
