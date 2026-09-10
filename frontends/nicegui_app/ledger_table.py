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
from dna.produce.tasks import DEFAULT_LANGUAGE
from dna.store.ledger import ProductionRecord
from frontends.nicegui_app import actions, detail_panel, theme
from frontends.nicegui_app.actions import RowView
from frontends.nicegui_app.audio_progress import AudioProgress

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
    ProductionKind.SUMMARY: "总结",
    ProductionKind.SHORTVIDEO: "短视频",
    ProductionKind.NARRATION: "口播",
    ProductionKind.LONGFORM: "长文案",
}

# 展开状态跨刷新保留 / the open panel survives a refresh
#
# 刷新会把整张表重建一遍，展开的面板本来会跟着关掉。生成完一份产物之后正是
# **最想看新内容的时刻**，而面板一关，看到的就是「点了一下，什么都没发生」——
# 内容其实已经更新，只是被收起来了。这里记住 (文章 id, 产物类型)，重建后自动展开。
# A refresh rebuilds the table and would close the panel exactly when the new content is
# what the user wants to see, making a successful run look like nothing happened.
_OPEN: dict[str, tuple[str, str] | None] = {"cell": None}

# 勾选状态跨刷新保留 / the selection survives a refresh
#
# 和 `_OPEN` 同一个理由，但更要紧：批量重抓跑完会刷新表格，选中如果被清掉，
# 「重抓完看看这几篇好了没有」就要从头再勾一遍十篇。
# 用 dict 当有序集合：确认框里列出的标题顺序要和人勾的顺序一致，
# 而 `set` 的迭代顺序是哈希序，每次刷新都可能不同。
# An ordered set: the confirmation dialog lists titles in the order they were picked,
# whereas a `set` would reorder them on every refresh.
_SELECTED: dict[str, None] = {}


def selected_ids() -> list[str]:
    """当前勾选的文章 id，按勾选顺序 / The picked article ids, in pick order."""
    return list(_SELECTED)


def clear_selection() -> None:
    """清空勾选 / Drop the whole selection."""
    _SELECTED.clear()


def render_table(
    container: ui.element, rows: list[RowView], *, on_change, on_select=None
) -> None:
    """
    渲染整张表 / Render the whole table.

    参数 / Args:
        on_change: 产物变化后的回调，用于刷新表格
        on_select: 勾选变化后的回调。**只重画批量操作条，不重建表格**——
            重建一次要几百个元素，而勾一下只是改了一个数字。
            Redraws only the batch bar: a rebuild costs hundreds of elements while a
            tick changed a single number.
    """
    container.clear()

    with container:
        if not rows:
            with ui.column().classes("w-full items-center gap-2 p-10"):
                ui.icon("inbox").classes("text-4xl").style("color: var(--wb-faint)")
                ui.label("没有匹配的文章").style("color: var(--wb-dim)")
                ui.label("先跑 dna fetch 或 dna add <链接>").classes("wb-path")
            return

        picks = _Selection(rows, on_select=on_select or (lambda: None))
        with ui.element("div").classes("wb-scroll w-full"):
            with ui.element("div").classes("wb-table"):
                _render_header(picks)
                for row in rows:
                    _render_row(row, on_change=on_change, picks=picks)
        picks.sync()


class _Selection:
    """
    本页的勾选控件 / The page's selection widgets.

    选中的 id 存在模块级的 `_SELECTED` 里（跨刷新保留），这个对象只持有**本页**
    那些控件的引用，用来把三态表头和行高亮同步过来。
    The picked ids live in the module-level `_SELECTED`; this object holds only the
    current page's widgets so the tri-state header and the row highlights stay in sync.

    「全选本页」只作用于本页 / "Select all" means this page:
        筛选之后本页可能只有 5 行，而 `_SELECTED` 里还有上一次勾的 20 篇。
        表头反映的是本页，批量条上的数字反映的是全部——两者都要说实话。
        The header speaks for this page and the batch bar for the whole selection.
    """

    def __init__(self, rows: list[RowView], *, on_select) -> None:
        self.page_ids = [row.article.id for row in rows]
        self.on_select = on_select
        self.header: ui.element | None = None
        self.boxes: dict[str, tuple[ui.checkbox, ui.element]] = {}

    def toggle(self, article_id: str, picked: bool) -> None:
        if picked:
            _SELECTED[article_id] = None
        else:
            _SELECTED.pop(article_id, None)
        self.sync()
        self.on_select()

    def toggle_page(self) -> None:
        """全选本页 ↔ 取消本页 / Select or clear this page."""
        picked = not all(i in _SELECTED for i in self.page_ids)
        for article_id in self.page_ids:
            if picked:
                _SELECTED[article_id] = None
            else:
                _SELECTED.pop(article_id, None)
        for article_id, (box, _) in self.boxes.items():
            box.set_value(article_id in _SELECTED)
        self.sync()
        self.on_select()

    def sync(self) -> None:
        """把状态刷到表头图标与行高亮上 / Push state into the header icon and rows."""
        if self.header is not None:
            self.header.props(f"name={page_icon(self.page_ids, _SELECTED)}")
        for article_id, (_, wrapper) in self.boxes.items():
            wrapper.classes(
                **({"add": "is-picked"} if article_id in _SELECTED else {"remove": "is-picked"})
            )


def page_icon(page_ids: list[str], selected) -> str:
    """
    「全选本页」该显示哪个图标 / Which icon the select-all control shows.

    三态：全不选 / 部分选 / 全选。**中间那一态不能省**——省掉的话「本页有几篇
    已经被勾了」就完全看不出来，人会以为点一下是全选，结果是全部取消。
    The middle state cannot be dropped: without it there is no way to tell that some of
    this page is already picked, and a click that looks like "select all" clears instead.
    """
    picked = sum(1 for i in page_ids if i in selected)
    if picked == 0:
        return "check_box_outline_blank"
    return "check_box" if picked == len(page_ids) else "indeterminate_check_box"


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


def _render_kind_cell(
    row: RowView, kind: ProductionKind, *, on_open, lang: str = DEFAULT_LANGUAGE
) -> ui.element:
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


# ---------------------------------------------------------------------------
# 生成与重做 / running a production
# ---------------------------------------------------------------------------


def _launch(
    row: RowView,
    kind: ProductionKind,
    *,
    force: bool,
    on_change,
    lang: str = DEFAULT_LANGUAGE,
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
    lang: str = DEFAULT_LANGUAGE,
) -> None:
    """长音频的耗时确认 / Confirm a long synthesis run."""
    wait = actions.audio_estimate_seconds(row.article, kind, lang)
    if wait < AUDIO_CONFIRM_SECONDS:
        _run(row, kind, variant=None, force=force, on_change=on_change, lang=lang)
        return

    task = spec(kind)
    with ui.dialog() as dialog, ui.card().classes("w-96").style(
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
    lang: str = DEFAULT_LANGUAGE,
    instructions: str = "",
) -> None:
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
    lang: str = DEFAULT_LANGUAGE,
    instructions: str = "",
) -> None:
    """执行生成并把结果告诉用户 / Run the production and report back."""
    task = spec(kind)
    label = task.label if lang == DEFAULT_LANGUAGE else f"{task.label}（EN）"
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
    return ui.card().classes("w-[30rem]").style(
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


__all__ = ["clear_selection", "page_icon", "render_batch_bar", "render_table", "selected_ids"]
