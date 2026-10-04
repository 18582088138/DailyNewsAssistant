"""表格的格子：标题、正文、媒体、四个产物格 / The table's cells。"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from dna.produce import DISPLAY_ORDER, ProductionKind, spec
from dna.store.ledger import ProductionRecord
from frontends.nicegui_app import actions, article_panel, detail_panel, jobs, theme
from frontends.nicegui_app.actions import RowView
from frontends.nicegui_app.ledger_table.run import _launch
from frontends.nicegui_app.ledger_table.state import (
    _HEAD_SHORT,
    _OPEN,
    _SELECTED,
    _STATUS_COLOUR,
    ARTICLE_CELL,
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

        # 当前展开的是哪一格；None 表示这一行是收起的。
        # 键是字符串：产物格用 `ProductionKind` 的值，标题格用 `ARTICLE_CELL`。
        # The open panel's key is a string: a ProductionKind value for a production cell,
        # `ARTICLE_CELL` for the article's own panel.
        state: dict[str, str | None] = {"open": None}
        cells: dict[str, ui.element] = {}

        def toggle(key: str, *, remember: bool = True) -> None:
            """点同一格收起，点别的格切过去 / Same cell closes, another switches."""
            if remember:
                remember_row(article.id)
            if state["open"] == key:
                state["open"] = None
                panel.visible = False
                wrapper.classes(remove="is-open")
                if remember:
                    _OPEN["cell"] = None
            else:
                state["open"] = key
                panel.visible = True
                wrapper.classes(add="is-open")
                if remember:
                    _OPEN["cell"] = (article.id, key)
                if key == ARTICLE_CELL:
                    article_panel.render(panel, row, on_change=on_change)
                else:
                    detail_panel.render(
                        panel, row, ProductionKind(key), on_change=on_change, on_redo=_launch
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
            _render_title_cell(row, on_open=lambda: toggle(ARTICLE_CELL))
            _render_body_cell(row)
            _render_media_cell(row, on_change=on_change)
            for kind in DISPLAY_ORDER:
                cells[str(kind)] = _render_kind_cell(row, kind, on_open=toggle)

        # 刷新前展开的是这一行的话，重新展开它 / re-open what was open before the refresh
        remembered = _OPEN["cell"]
        if remembered is not None and remembered[0] == article.id:
            toggle(remembered[1])

        _ = article  # 供调试时定位这一行 / kept for debugging identification


def _render_title_cell(row: RowView, *, on_open) -> None:
    """标题格：标题 + 来源 + 抓取状态 + 原文链接 / Title, source, status, link."""
    article = row.article
    custom = actions.is_custom(article)

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
            source = ui.label(article.source_id or "user").classes("wb-cell-meta")
            if custom:
                # 人工创作的文章没有原文地址，「来源」这一行就是**指向产物文件夹的入口**：
                # 没有它，用户没有任何地方能打开自己那篇文章的目录。
                # A hand-authored article has no source address, so the source line *is* the
                # way into its folder — without it there is nowhere to open that directory.
                directory = actions.article_directory(article)
                if directory:
                    source.classes(add="lnk cursor-pointer")
                    source.tooltip(f"打开产物目录　·　{directory}")
                    source.on("click", lambda p=directory: _reveal(Path(p)))
                    # 这一格的点击会展开文章面板，点来源名不该连带展开
                    source.on("click", js_handler="(e) => e.stopPropagation()")
            ui.badge(str(article.status)).props(
                f"color={_STATUS_COLOUR.get(str(article.status), 'grey')} outline"
            ).style("font-size:9px; padding:0 5px")
        # 原文链接单独一行，显示**地址本身**：域名就是可信度的一半，
        # 而「原文」两个字看不出这条是公众号转载还是 arXiv 原文。
        # The address itself: the domain carries half the credibility, which the word
        # "source" does not.
        if article.url and not custom:
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


def _render_media_cell(row: RowView, *, on_change) -> None:
    """
    媒体格 / The media cell.

    点一下做两件事：**打开素材文件夹**，并把里面的文件**顺手整理合规**（按文件头纠正
    扩展名、改成 `NN_<来源>.<ext>`、补写出处边车），然后把结果报出来。
    One click does two things: opens the asset folders and normalises what is inside them,
    then reports what it did.

    为什么要「顺手」而不是另给一个按钮 / Why it happens on the same click:
        用户的动作是「拷完文件回来点一下」，那一瞬间正是整理的最佳时机；再要求他去点
        第二个按钮，实际使用中就是「忘了点，然后困惑为什么计数没变」。
        Coming back after dropping files *is* the moment to tidy up. A second button would
        be forgotten, and the count would then silently stay wrong.

    不可点的规则按文章来历分 / Clickability depends on how the article came to be:
        抓取来的文章没有素材就不可点（不给一个点开是空的按钮）；
        人工创作的文章**始终可点**——空素材目录正是「把素材拷进来」的入口。
    A fetched article with no assets stays inert; a hand-authored one is always clickable,
    because its empty folders are the entry point for adding assets.
    """
    custom = actions.is_custom(row.article)
    targets = actions.media_targets(row.article)
    cell = ui.element("div").classes("wb-num" + (" clickable" if targets else ""))
    with cell:
        ui.label(row.media_label)

    if not targets:
        return

    if custom:
        cell.tooltip("打开素材文件夹并整理其中的文件　·　把图片和视频直接拷进去即可")
        cell.on("click", lambda: _tidy_and_reveal(row.article.id, targets, on_change=on_change))
        return

    names = "　".join(f"{p.name}/" for p in targets)
    cell.tooltip(f"在文件管理器里打开　·　{names}")
    cell.on("click", lambda ps=targets: [_reveal(p) for p in ps])


async def _tidy_and_reveal(article_id: str, targets: list, *, on_change) -> None:
    """
    打开素材文件夹并整理，然后报告结果 / Open the folders, tidy them, and report.

    整理结果**一定要报出来**，包括「认不出哪些」。静默改名会让人在发布时才发现某张图的
    名字变了；静默跳过认不出的文件则会让人以为程序没在工作。
    The outcome is always reported, including what could not be recognised: silently
    renaming would surprise the user at publishing time, and silently skipping would look
    like the feature does nothing.
    """
    for path in targets:
        _reveal(path)

    report = await actions.normalize_assets(article_id)
    details = report.details()
    message = report.summary()
    if details:
        shown = details[:6]
        message += "\n" + "\n".join(shown)
        if len(details) > len(shown):
            message += f"\n…… 另有 {len(details) - len(shown)} 条"

    ui.notify(
        message,
        type="info" if report.changed else "positive",
        multi_line=bool(details),
    )
    if report.changed:
        on_change()


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
    job = jobs.running_in_cell(row.article.id, kind)
    if job is not None:
        glyph, value = "◐", "合成中…" if job.audio else "生成中…"
        tone += " wb-running"
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

    已生成的显示**「字数 (约时长)」而不是「已完成」**：这两个数决定产物能不能用，
    而「已完成」不比一个填满的格子多说任何东西。
    A filled cell shows "chars (≈duration)" rather than the word "done", because those
    numbers decide usability and "done" adds nothing the fill does not already say.

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

    # 字数为主、时长是估算：人改过稿子后两者都跟着台账里的新一行变
    seconds = actions.cell_seconds(record)
    minutes = seconds / 60
    duration = f"{seconds:.0f}s" if minutes < 1.5 else f"{minutes:.0f}min"
    value = f"{record.chars}字 (约{duration})"
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
