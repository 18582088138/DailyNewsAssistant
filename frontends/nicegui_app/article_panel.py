"""
文章自身的展开面板 / The panel for the article itself.

点标题格展开的是这一个（产物格展开的是各自那份稿子）。它管的是**文章自己的三样东西**：
标题、正文、媒体。人工创作的文章就在这里填内容。

Clicking the title cell opens this panel; clicking a production cell opens that script. It
covers the article's own three things — title, body and media — which is where a
hand-authored article gets its content.

为什么单独一个面板、而不是塞进 `detail_panel` / Why not fold this into `detail_panel`:
    那个面板的每个按钮都围绕**一份稿子**（重做、下载、合成音频、TTS 操作台），
    而这里是**文章本身**，两者能做的事几乎不重叠。混在一起的话，用户在「总结」格里
    会看到一个能改标题的框，而改标题与总结那格没有任何关系。
    Every button in that panel concerns one script; this one concerns the article itself,
    and the two share almost nothing. Merging them would put a title editor inside the
    Summary cell, where it has no business being.

**只有人工创作的文章能在这里改。**抓取来的文章按只读展示，并指明该走哪条路——
改稿会把整篇 `article.md` 重渲染，对历史产物来说那是改写档案。
Only hand-authored articles are editable here; fetched ones are shown read-only with a
pointer to the right command. Rewriting would re-render the whole `article.md`, which for a
historical artefact means overwriting the archive.

编辑方式与产物完全一致：**双击文本 → 点别处保存**（`text_editor.render_field`）。
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from frontends.nicegui_app import actions, text_editor, theme
from frontends.nicegui_app.actions import RowView


def render(container: ui.element, row: RowView, *, on_change) -> None:
    """
    把文章面板画进容器 / Draw the article panel into the container.

    参数 / Args:
        on_change: 改动成功后的回调（表格层用它刷新标题、字数与媒体计数）
    """
    container.clear()
    article = row.article
    custom = actions.is_custom(article)
    title, body = _content(article)

    with container:
        with ui.row().classes("w-full items-center justify-between gap-3 no-wrap"):
            ui.label("文章").classes("text-lg font-medium").style("color: var(--wb-accent)")
            _render_actions(article)

        ui.label(theme.short_title(article.title, 90)).classes("wb-path")

        if not custom:
            _render_readonly_hint()
            ui.label("标题").classes("wb-subhead")
            ui.label(title or "（无标题）").classes("wb-body w-full")
            ui.label("正文").classes("wb-subhead")
            ui.label(body or "（正文没抓到）").classes("wb-body w-full")
            ui.label("媒体").classes("wb-subhead")
            _render_media(article)
            return

        ui.label("标题").classes("wb-subhead")
        text_editor.render_article_field(
            article.id,
            "title",
            title or article.title,
            on_saved=on_change,
            saved_message="标题已保存",
        )

        ui.label("正文").classes("wb-subhead")
        text_editor.render_article_field(
            article.id,
            "body",
            body,
            on_saved=on_change,
            saved_message="正文已保存，后续生成会用新内容",
        )

        ui.label("媒体").classes("wb-subhead")
        _render_media(article)


def _render_readonly_hint() -> None:
    """
    抓取来的文章是只读的，并说清该走哪条路 / Fetched articles are read-only; say which way to go.

    这里**不给编辑框**，因为改稿会把整篇 `article.md` 重渲染一遍——对历史产物来说
    那是改写档案（抽取结果、原始链接、当时抓到的正文都可能被覆盖）。守卫在 store 层，
    这里只是不把一个必然会失败的按钮摆出来。
    No editors here: rewriting would re-render the whole `article.md`, i.e. overwrite the
    archive — extraction result, original link and the captured body included. The guard is
    in the store; this only avoids offering a button that can only fail.
    """
    ui.label(
        "抓取来的文章是只读档案，不能在这里改。"
        "要重新抓用 `dna refetch`；正文没抓到或要修正时，改 article.md 后执行 `dna sync`。"
    ).classes("wb-hint")


def _content(article) -> tuple[str, str]:
    """
    读回落盘上的标题与正文 / Read the title and body back off disk.

    正文可能很长（一篇抓取来的文章动辄几千字），但这里只在**展开时**读一次，
    与产物正文同样的道理：进页面就全读一遍是几百次没人会看的磁盘 I/O。
    Read once, on open, for the same reason the production bodies are: reading every row up
    front would be hundreds of disk reads nobody asked to see.
    """
    return article.title, actions.article_body(article.id)


def _render_actions(article) -> None:
    """右上角：目录、正文文件、素材文件夹 / Top right: directory and files."""
    with ui.row().classes("items-center gap-1 no-wrap"):
        directory = actions.article_directory(article)
        if directory:
            ui.button(
                "产物目录", icon="folder_open",
                on_click=lambda p=directory: _reveal(Path(p)),
            ).props("flat dense no-caps").classes("wb-path").tooltip(directory)

        body = actions.body_file(article)
        if body is not None:
            ui.button(
                "正文文件", icon="description",
                on_click=lambda p=body: _reveal(p),
            ).props("flat dense no-caps").classes("wb-path").tooltip(
                f"用系统默认程序打开　·　{body.name}"
            )


def _render_media(article) -> None:
    """媒体一节：说清怎么放素材，并给出打开文件夹的按钮 / How to add assets, and where."""
    folders = actions.media_targets(article)
    ui.label(
        "把图片和视频直接拷进这两个文件夹，然后在表格里点一次「媒体」格，"
        "程序会按文件头纠正扩展名、改成合规文件名并补写出处（不删除任何文件）。"
    ).classes("wb-path")
    if not folders:
        ui.label("这两个文件夹还不存在，点表格里的「媒体」格可以建出来。").classes("wb-hint")
        return

    with ui.row().classes("items-center gap-2"):
        for folder in folders:
            ui.button(
                f"{folder.name}/", icon="folder",
                on_click=lambda p=folder: _reveal(p),
            ).props("flat dense no-caps").classes("wb-path").tooltip(str(folder))


def _reveal(path: Path) -> None:
    """打开文件或目录，失败只提示 / Open a path, reporting failures inline."""
    message = actions.open_in_file_manager(path)
    if message:
        ui.notify(message, type="warning")


__all__ = ["render"]
