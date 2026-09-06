"""
展开面板 / The expanded detail panel.

点某个产物格 → 这块展开，显示该产物的全文，并把**这一格的操作**收在里面：
重做、下载、打开素材文件夹。
Clicking a production cell opens this panel with that production's full text and the
actions for it: redo, download, reveal the folder.

为什么重做按钮在这里而不在格子上 / Why redo lives here rather than on the cell:
    格子挤在表格里，按钮只有十几像素宽，**点开内容和点重做挨在一起**——
    而这两件事的代价完全不对等：前者免费，后者是一次计费调用。
    把重做放进展开面板，等于要求「先看见内容，再决定要不要重做」，
    这正是重做之前本来就该走的一步。
    On the cell a button is barely a dozen pixels from the one that merely opens the
    content, and the two are not equivalent: one is free, the other is a billed call.
    Moving redo into the panel forces the sequence that should happen anyway — look at
    what is there, then decide whether to pay for another one.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from dna.produce import ProductionKind, spec
from frontends.nicegui_app import actions
from frontends.nicegui_app.actions import RowView


def render(
    container: ui.element,
    row: RowView,
    kind: ProductionKind,
    *,
    on_change,
    on_redo,
) -> None:
    """
    把某个产物的详情画进容器 / Draw one production's detail into the container.

    参数 / Args:
        on_redo: 重做的发起函数，由表格层提供（长文案要先弹形式选择框）
    """
    container.clear()
    record = row.production(kind)
    task = spec(kind)

    with container:
        with ui.row().classes("w-full items-center justify-between gap-3 no-wrap"):
            _render_summary(row, kind)
            _render_actions(row, kind, on_change=on_change, on_redo=on_redo)

        if record is None or not record.ok:
            _render_empty(record, task.label)
            return

        # 内容是**展开时才读**的。表格一页 50 行 × 5 种产物，进页面就全读一遍
        # 是 250 次磁盘 I/O，而其中绝大多数没人会看。
        # Read on open. Loading every cell up front would be 250 disk reads per page for
        # content nobody has asked to see.
        text = actions.production_text(row.article.id, kind)
        with ui.element("div").classes("wb-body w-full"):
            ui.markdown(text or "_（文件读不到，可能被移动或删除）_")


def _render_summary(row: RowView, kind: ProductionKind) -> None:
    """左侧：这一版是什么时候、用哪个模型生成的 / When and with what it was made."""
    record = row.production(kind)
    task = spec(kind)

    with ui.column().classes("gap-1 min-w-0"):
        ui.label(task.label).classes("text-sm font-medium").style("color: var(--wb-accent)")

        if record is None:
            ui.label("尚未生成").classes("wb-path")
            return

        parts = [f"{record.chars} 字"]
        if record.est_seconds:
            parts.append(f"约 {record.est_seconds:.0f} 秒")
        if record.variant:
            parts.append("专题" if record.variant == "feature" else "访谈")
        if record.llm_model:
            parts.append(record.llm_model)
        if record.created_at:
            parts.append(record.created_at.strftime("%m-%d %H:%M"))
        if record.calls > 1:
            parts.append(f"{record.calls} 次调用")
        ui.label("　·　".join(parts)).classes("wb-path")


def _render_actions(row: RowView, kind: ProductionKind, *, on_change, on_redo) -> None:
    """右侧：重做 / 下载 / 打开文件夹 / Redo, download, reveal."""
    record = row.production(kind)
    task = spec(kind)
    done = bool(record and record.ok)

    with ui.row().classes("items-center gap-1 no-wrap"):
        _render_downloads(row, kind, done=done)
        _render_folder_buttons(row)

        ui.separator().props("vertical").classes("mx-1")

        _render_audio_button(row, kind, done=done, on_change=on_change, on_redo=on_redo)

        # 重做与生成用词不同，且重做是琥珀色——**颜色本身就是「这会花钱」的提示**
        # The wording differs and redo is amber: the colour itself is the cost warning.
        label = f"重做（{task.approx_calls} 次调用）" if done else "生成"
        button = ui.button(
            label, icon="refresh" if done else "play_arrow",
            on_click=lambda: on_redo(row, kind, force=done, on_change=on_change),
        ).props("flat dense no-caps")
        if done:
            button.classes("wb-btn-cost").tooltip("重新调用 LLM，会产生费用")
        else:
            button.tooltip(f"调用 LLM 生成{task.label}，约 {task.approx_calls} 次调用")


def _render_audio_button(
    row: RowView, kind: ProductionKind, *, done: bool, on_change, on_redo
) -> None:
    """
    合成音频 / Synthesise the audio.

    **不花钱，但很花时间**，所以它不是琥珀色（那个颜色专门表示「这会计费」），
    而是普通样式配一个把预估等待写进去的 tooltip。把两种代价用同一个颜色标出来，
    等于让「花钱」这个信号贬值。
    Free but slow, so it does not take the amber styling reserved for billed actions;
    reusing that colour for a different kind of cost would devalue the money signal.

    稿子还没生成时按钮禁用——先有稿子才有音频，这个顺序不该由一次失败来教会用户。
    Disabled until the script exists: the order is inherent, and a failed run is a poor
    way to teach it.
    """
    audio_kind = actions.audio_for(kind)
    if audio_kind is None:
        return

    record = row.production(audio_kind)
    has_audio = bool(record and record.ok)
    wait = actions.audio_estimate_seconds(row.article, audio_kind) if done else 0.0

    if not done:
        ui.button("合成音频", icon="graphic_eq").props(
            "flat dense no-caps disable"
        ).tooltip(f"先生成{spec(kind).label}，才能合成音频")
        return

    hint = f"约需 {wait / 60:.0f} 分钟" if wait >= 60 else f"约需 {wait:.0f} 秒"
    ui.button(
        "重新合成" if has_audio else "合成音频",
        icon="graphic_eq",
        on_click=lambda: on_redo(row, audio_kind, force=has_audio, on_change=on_change),
    ).props("flat dense no-caps").tooltip(
        f"本地 TTS 合成，不产生费用，{hint}"
        + ("（已有音频，会覆盖）" if has_audio else "")
    )


def _render_downloads(row: RowView, kind: ProductionKind, *, done: bool) -> None:
    """
    下载按钮 / The download buttons.

    文案随时可下。**音频与视频按钮先摆出来但禁用**，tooltip 写明要等哪个阶段——
    不摆的话没人知道这个功能将来会有，摆了却能点则是欺骗。
    The script downloads today. The audio and video buttons are shown but disabled with a
    tooltip naming the stage that will fill them: omitting them hides a planned capability,
    while enabling them would promise something that does not exist.
    """
    script = actions.production_file(row.article, kind) if done else None
    ui.button(
        icon="description",
        on_click=lambda p=script: ui.download.file(p, p.name),
    ).props(f"flat dense round {'' if script else 'disable'}").tooltip(
        f"下载文案 {script.name}" if script else "还没有文案可下载"
    )

    sidecar = actions.production_sidecar(row.article, kind) if done else None
    if sidecar is not None:
        ui.button(
            icon="data_object",
            on_click=lambda p=sidecar: ui.download.file(p, p.name),
        ).props("flat dense round").tooltip(
            f"下载 {sidecar.name}——按发言人切好的轮次，供 TTS 分配音色"
        )

    # 要念出来的产物有音频；成片还没有 / spoken kinds have audio, video is still pending
    if spec(kind).spoken:
        audio_kind = actions.audio_for(kind)
        audio = actions.production_file(row.article, audio_kind) if audio_kind else None
        ui.button(
            icon="graphic_eq",
            on_click=lambda p=audio: ui.download.file(p, p.name),
        ).props(f"flat dense round {'' if audio else 'disable'}").tooltip(
            f"下载音频 {audio.name}" if audio else "还没有音频。用右侧「合成音频」生成"
        )
        ui.button(icon="movie").props("flat dense round disable").tooltip(
            "下载成片：P6 视频合成后可用"
        )


def _render_folder_buttons(row: RowView) -> None:
    """
    打开素材文件夹 / Reveal the material folders.

    直接开到 `images/` / `videos/`，不是只开文章根目录——**要拷的是素材本身**，
    多点一层在每天重复几十次的操作里是实打实的摩擦。
    Opens straight into `images/` or `videos/` rather than the article root: the material
    is what gets copied out, and one extra click is real friction in a task repeated
    dozens of times a day.
    """
    directory = actions.article_directory(row.article)
    if not directory:
        return

    ui.button(
        icon="folder_open", on_click=lambda: _reveal(directory)
    ).props("flat dense round").tooltip(f"打开产物目录\n{directory}")

    for label, path in actions.media_folders(row.article).items():
        icon = "image" if label == "配图" else "video_library"
        ui.button(
            icon=icon, on_click=lambda p=path: _reveal(p)
        ).props("flat dense round").tooltip(f"打开{label}文件夹\n{path}")


def _reveal(path: str | Path) -> None:
    """打开目录，失败时说清原因 / Reveal a folder, reporting why when it fails."""
    error = actions.open_in_file_manager(path)
    if error:
        ui.notify(error, type="warning")


def _render_empty(record, label: str) -> None:  # noqa: ANN001 - ProductionRecord | None
    """还没有产物时显示什么 / What to show before anything exists."""
    if record is not None and not record.ok:
        # 失败原因**原样显示**：人要据此判断是重试、改配置，还是这篇本来就不该做
        with ui.element("div").classes("wb-body w-full"):
            ui.label(f"上次生成失败：{record.error or '未知原因'}").style(
                "color: var(--wb-danger)"
            )
        return

    ui.label(f"{label}还没有生成。点右侧「生成」开始（会调用 LLM）。").classes(
        "wb-path"
    ).style("padding: 8px 2px")


__all__ = ["render"]
