"""表格的格子：标题、正文、媒体、五个产物格 / The table's cells。"""

from __future__ import annotations

from nicegui import ui

from dna.produce import DISPLAY_ORDER, ProductionKind, spec
from dna.store.ledger import ProductionRecord
from frontends.nicegui_app import actions, detail_panel, theme
from frontends.nicegui_app.actions import RowView
from frontends.nicegui_app.ledger_table.run import _launch
from frontends.nicegui_app.ledger_table.state import (
    _HEAD_SHORT,
    _OPEN,
    _SELECTED,
    _STATUS_COLOUR,
    _Selection,
    page_icon,
    remember_row,
)


def _render_header(picks: _Selection) -> None:
    """
    表头 / The header row.

    `sticky top-0` 由 `.wb-head` 的 CSS 提供；背景必须**不透明**，
    否则滚上来的行会从表头底下透出来。
    Stickiness comes from the CSS. The background must be opaque or rows scrolling
    underneath show through.
    """
    with ui.element("div").classes("wb-head wb-grid"):
        with _cell("wb-h wb-pick"):
            picks.header = ui.icon(page_icon(picks.page_ids, _SELECTED)).classes(
                "cursor-pointer"
            ).style("color: var(--wb-dim); font-size:18px")
            picks.header.on("click", picks.toggle_page)
            picks.header.tooltip("全选/取消本页（跨页的勾选保留，数字在批量条上）")
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


def _render_row(row: RowView, *, on_change, picks: _Selection) -> None:
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

        def toggle(kind: ProductionKind, *, remember: bool = True) -> None:
            """点同一格收起，点别的格切过去 / Same cell closes, another switches."""
            if remember:
                remember_row(article.id)
            if state["open"] == kind:
                state["open"] = None
                panel.visible = False
                wrapper.classes(remove="is-open")
                if remember:
                    _OPEN["cell"] = None
            else:
                state["open"] = kind
                panel.visible = True
                wrapper.classes(add="is-open")
                if remember:
                    _OPEN["cell"] = (article.id, str(kind))
                detail_panel.render(
                    panel, row, kind, on_change=on_change, on_redo=_launch
                )
            for k, element in cells.items():
                element.classes(**({"add": "sel"} if k == state["open"] else {"remove": "sel"}))

        with grid:
            with _cell("wb-pick"):
                box = ui.checkbox(value=article.id in _SELECTED).props("dense size=xs")
                box.on_value_change(
                    lambda e, aid=article.id: picks.toggle(aid, bool(e.value))
                )
                picks.boxes[article.id] = (box, wrapper)
            _render_title_cell(row, on_open=lambda: toggle(_first_kind(row)))
            _render_body_cell(row)
            _render_media_cell(row)
            for kind in DISPLAY_ORDER:
                cells[kind] = _render_kind_cell(row, kind, on_open=toggle)

        # 刷新前展开的是这一行的话，重新展开它 / re-open what was open before the refresh
        remembered = _OPEN["cell"]
        if remembered is not None and remembered[0] == article.id:
            toggle(ProductionKind(remembered[1]))

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
        # 原文链接单独一行，显示**地址本身**：域名就是可信度的一半，
        # 而「原文」两个字看不出这条是公众号转载还是 arXiv 原文。
        # The address itself: the domain carries half the credibility, which the word
        # "source" does not.
        if article.url:
            link = (
                ui.link(theme.short_url(article.url), article.url, new_tab=True)
                .classes("lnk block")
                .style("margin-top:2px")
            )
            link.tooltip(article.url)
            # 链接的点击不该连带展开这一行 / a link click must not also toggle the row
            #
            # 这一处先前是**坏的**：注释写着不该连带展开，可处理器挂在整个标题格上，
            # 点链接会同时开新标签页并展开面板。用 `js_handler` 在浏览器里就地
            # 拦下冒泡，比注册一个空回调好——后者每次点击都要往服务端跑一趟。
            # This used to be broken: the handler sits on the whole cell, so a link click
            # also toggled the panel. Stopping propagation in the browser costs nothing,
            # whereas a no-op Python callback would make a server round trip per click.
            link.on("click", js_handler="(e) => e.stopPropagation()")

    cell.on("click", lambda: on_open())


def _render_body_cell(row: RowView) -> None:
    """
    正文格 / The body cell.

    有 `article.md` 就整格可点，点开的是**文件本身**（Windows 上交给默认编辑器）。
    没有就保持不可点——抓取失败的文章目录里没有正文，给一个点开报错的格子
    比不给更糟。
    Clickable only when `article.md` exists; a failed fetch has no body and a cell that
    errors on click is worse than an inert one.
    """
    target = actions.body_file(row.article)
    cell = ui.element("div").classes("wb-num" + (" clickable" if target else ""))
    with cell:
        ui.label(row.body_label)
    if target is None:
        return
    cell.tooltip(f"打开正文文件　·　{target.name}")
    cell.on("click", lambda p=target: _reveal(p))


def _render_media_cell(row: RowView) -> None:
    """
    媒体格 / The media cell.

    点开配图目录（没有配图时是视频目录）。数量为 0 时不可点：
    `media_target` 只返回**真实存在且非空**的目录。
    Opens the images folder, or the videos folder when there are no images. Never
    clickable when empty, since `media_target` only returns non-empty directories.
    """
    target = actions.media_target(row.article)
    cell = ui.element("div").classes("wb-num" + (" clickable" if target else ""))
    with cell:
        ui.label(row.media_label)
    if target is None:
        return
    cell.tooltip(f"在文件管理器里打开　·　{target.name}/")
    cell.on("click", lambda p=target: _reveal(p))


def _reveal(path) -> None:
    """打开文件或目录，失败只提示 / Open a path, reporting failures inline."""
    message = actions.open_in_file_manager(path)
    if message:
        ui.notify(message, type="warning")


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

    glyph, value, tone = _cell_state(record, kind)
    cell = ui.element("div").classes(f"wb-kind {tone}")
    with cell:
        ui.label(glyph).classes("glyph")
        ui.label(value).classes("val")
        # 有英文版时在格子上挂一个小标记 / a small mark when an English edition exists
        #
        # 收起状态下语言开关是看不见的（它在展开面板里），没有这个标记就无从知道
        # 哪几篇已经出过英文版——而那正是「还要不要再花一次钱」的判断依据。
        # The language switch lives in the panel and is invisible while collapsed, so
        # without this mark there is no way to tell which articles already have an
        # English edition — which is what decides whether to spend again.
        if row.has_language(kind, "en"):
            ui.label("EN").classes("wb-lang-chip")

    cell.tooltip(
        _cell_tooltip(record, task.label, kind, has_en=row.has_language(kind, "en"))
    )
    cell.on("click", lambda k=kind: on_open(k))
    return cell


def _cell_state(
    record: ProductionRecord | None, kind: ProductionKind | str
) -> tuple[str, str, str]:
    """
    格子的形状、数值与配色 / The cell's glyph, value and tone.

    **形状与颜色成对出现**，不能只靠颜色——色觉障碍者和黑白截图下都要能分辨。
    Glyph and colour always travel together: colour alone fails for colour-blind users and
    in greyscale screenshots.

    已生成的显示**时长而不是「已完成」**：时长决定这个产物能不能用，
    而「已完成」不比一个填满的格子多说任何东西。
    A filled cell shows its duration rather than the word "done", because the duration is
    what decides usability and "done" adds nothing the fill does not already say.

    超出字数区间的仍然是 `●`，只换颜色 / An over-length cell keeps the filled glyph:
        它确实生成了、文件就在盘上、也确实能发——只是可能要手删两句。
        换成 `▲` 会让它和「生成失败」混在一起，而两者要做的事完全不同：
        一个是重试，一个是修一修。
        It really was generated and is publishable; reusing the failure glyph would
        conflate "retry it" with "trim two sentences".
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
    return "●", value, "wb-over" if actions.over_target(record, kind) else "wb-ok"


def _cell_tooltip(
    record: ProductionRecord | None,
    label: str,
    kind: ProductionKind | str,
    *,
    has_en: bool = False,
) -> str:
    """悬停时的详情 / Details on hover。`record` 是**中文版**那一条。"""
    extra = "　·　已有英文版" if has_en else ""
    if record is None:
        return f"{label}：尚未生成　·　点击展开后再决定是否生成{extra}"
    if not record.ok:
        return f"{label} 生成失败：{record.error or '未知原因'}　·　点击查看{extra}"

    parts = [f"{record.chars} 字"]
    # 超长的说清楚超在哪：报出区间，人才知道是手删两句还是重做
    # The window is quoted so the reader can decide between trimming and regenerating.
    if actions.over_target(record, kind):
        window = actions.target_window(kind)
        if window:
            parts.append(
                f"⚠️ 未落入 {window[0]}~{window[1]} 字，可手动删减或重做"
            )
    if record.est_seconds:
        parts.append(f"约 {record.est_seconds:.0f} 秒")
    if record.variant:
        parts.append("专题" if record.variant == "feature" else "访谈")
    parts.append(f"{record.llm_model or '未知模型'}")
    if record.created_at:
        parts.append(record.created_at.strftime("%m-%d %H:%M"))
    if record.calls > 1:
        parts.append(f"{record.calls} 次调用")
    return f"{label}：{'　·　'.join(parts)}{extra}　·　点击展开查看全文"
