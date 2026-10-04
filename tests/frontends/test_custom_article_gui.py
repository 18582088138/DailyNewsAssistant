"""
test_custom_article_gui.py —— 人工创作文章的界面测试 / Hand-authored article UI tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/frontends/test_custom_article_gui.py -v

对应的人工验证 / Matching manual check:
    dna gui → 右上角「新建」→ 表格第 1 行出现一篇 `未命名文章`，来源写着 custom_article
    点标题格 → 展开「文章」面板；双击标题或正文 → 改成自己的内容，点别处保存
    点「媒体」格 → 弹出 images/ 与 videos/，拷几张图进去再点一次 → 提示条报告整理结果

覆盖 / Covers:
    1. 「新建」按钮真的在页面上（装配根 `build()` 里能找到）
    2. 「新建」的动作建出一行，来源是 `custom_article`，并且**立刻能在表格里查到**
    3. 标题格里来源名可点（人工创作的文章靠它进产物目录）
    4. 「文章」面板能画出来，且带标题与正文两段可编辑文本
    5. 产物面板在**一格都没有**时也画得出来（走骨架模板那条新路）
    6. 媒体格对人工创作的文章**始终可点**（空目录也要能打开）

不测什么：不测真实点击与输入（这套测试不驱动浏览器），不测样式长相。
只测「建得出来、数据对得上」——这条线以下的错会让界面白屏或按钮点了没反应。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from nicegui import ui
from nicegui.client import Client
from nicegui.page import page as page_decorator

from dna.core.config import Settings
from dna.core.models import RawItem, SourceKind
from dna.store.custom_article import CUSTOM_SOURCE_ID
from dna.store.ledger import ArticleRecord, FetchStatus, Ledger
from frontends.nicegui_app import actions

NOW = datetime(2026, 10, 4, 9, 0, 0)


@pytest.fixture
def offscreen() -> Iterator[Client]:
    """一个不连浏览器的 client，元素建在内存里 / An in-memory client, no browser."""
    client = Client(page_decorator("/test"), request=None)
    with client:
        yield client


@pytest.fixture
def gui_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_actions_settings):
    """
    指向临时目录的配置，并把各层模块里的 `get_settings` 一起替掉。

    必须连 **store 层**一起替：`actions.create_blank_article()` 内部调的是
    `dna.store.custom_article`，而它持有自己那一份 `get_settings` 绑定。只替
    `actions` 的话，这个测试会往**用户真实的 `data/dna.db`** 里插文章，
    而且一行错误都不报——2026-10-04 真的这样污染过一次真实台账。
    The **store** layer must be replaced too: the action calls
    `dna.store.custom_article`, which holds its own `get_settings` binding. Patching only
    `actions` made this test insert articles into the user's real `data/dna.db` without
    raising anything — which happened for real on 2026-10-04.
    """
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", output_dir=tmp_path / "out")
    patch_actions_settings(settings)
    return settings


def _elements(offscreen: Client) -> list:
    """
    一个 client 上的全部元素 / Every element on one client.

    `build()` 把界面建在 client 的顶层元素之下，而顶层只有布局骨架（layout / page /
    container）——按钮都在它们的子孙里。只遍历顶层会得到一张空清单，
    于是「按钮在不在」这类断言永远失败，看起来像功能没做。
    The widgets hang below the client's top-level layout elements, so the roots alone yield an
    empty list and any "is the button there" assertion fails as if the feature were missing.
    """
    found: dict[int, object] = {}
    for root in offscreen.elements.values():
        found[id(root)] = root
        for child in root.descendants():
            found[id(child)] = child
    return list(found.values())


def _buttons(element) -> list[str]:
    """收集按钮文字 / Collect the button labels."""
    nodes = _elements(element) if isinstance(element, Client) else list(element.descendants())
    return [
        str(child.text)
        for child in nodes
        if isinstance(child, ui.button) and child.text
    ]


def _labels(element) -> list[str]:
    """
    收集屏幕上的文字 / Collect the on-screen text.

    骨架是 `ui.markdown` 画的，只收集 `ui.label` 会漏掉它——而断言「骨架在不在」
    正是靠这一段文字。
    The skeleton is drawn with `ui.markdown`; collecting only labels would miss exactly the
    text those assertions look for.
    """
    nodes = _elements(element) if isinstance(element, Client) else list(element.descendants())
    found = []
    for child in nodes:
        if isinstance(child, ui.label):
            found.append(str(child.text))
        elif isinstance(child, ui.markdown):
            found.append(str(child.content))
    return found


def _row(settings: Settings, article_id: str) -> actions.RowView:
    """从台账取一行并组装成表格用的 RowView / Build the RowView the table renders."""
    view = actions.load_rows(page_size=50)
    for row in view.rows:
        if row.article.id == article_id:
            return row
    raise AssertionError("新建的文章没有出现在表格数据里")


# ---------------------------------------------------------------------------
# 1. 按钮在不在 / is the button there
# ---------------------------------------------------------------------------


def test_new_button_is_on_the_page(offscreen: Client, gui_settings: Settings) -> None:
    """
    「新建」必须在页面上（整页渲染得出来，按钮也在）。

    这里直接调 `_render_page()` 而不是 `build()`：`build()` 只**注册** `@ui.page("/")`，
    真正的装配要等浏览器请求 `/`，测试里永远不会发生。`test_render_smoke` 的
    `test_顶层页面能装配` 只验证注册不抛异常，盖不到「按钮有没有接线」。
    `_render_page()` is called directly because `build()` only registers the page and the real
    assembly waits for a browser request that never comes in a test.
    """
    from frontends.nicegui_app import main as gui_main

    gui_main._render_page()

    labels = _buttons(offscreen)
    assert "新建" in labels, f"页面上没有「新建」按钮，现有按钮：{labels}"


# ---------------------------------------------------------------------------
# 2. 新建的动作 / what the action produces
# ---------------------------------------------------------------------------


def test_new_button_creates_a_custom_row(gui_settings: Settings) -> None:
    """
    点一次「新建」＝ 台账多一行，来源 `custom_article`，且立刻查得到。

    「立刻查得到」是关键：界面刷新走的就是 `load_rows`，这一步查不到的话，
    用户看到的就是「点了新建，表格没变」。
    """
    record = asyncio.run(actions.create_blank_article())

    assert record.source_id == CUSTOM_SOURCE_ID
    assert record.store_dir, "没落盘的行在界面上是废行：正文与媒体格都不可点"
    assert actions.is_custom(record)

    row = _row(gui_settings, record.id)
    assert row.article.title == record.title
    assert row.is_new, "刚建的文章应当带 NEW 标识（还没调过 LLM）"


def test_two_clicks_create_two_rows(gui_settings: Settings) -> None:
    """连点两次得到两篇，而不是第二篇覆盖第一篇。"""
    first = asyncio.run(actions.create_blank_article())
    second = asyncio.run(actions.create_blank_article())

    assert first.id != second.id
    assert Ledger(gui_settings.db_file).count() == 2


# ---------------------------------------------------------------------------
# 3-4. 标题格与文章面板 / the title cell and the article panel
# ---------------------------------------------------------------------------


def test_title_cell_renders_for_a_custom_article(
    offscreen: Client, gui_settings: Settings
) -> None:
    """标题格画得出来，来源名是 `custom_article`，且没有「原文链接」那一行。"""
    from frontends.nicegui_app import ledger_table

    record = asyncio.run(actions.create_blank_article())
    row = _row(gui_settings, record.id)

    container = ui.column()
    ledger_table.render_table(container, [row], on_change=lambda: None)

    texts = _labels(container)
    assert CUSTOM_SOURCE_ID in texts
    # 伪 URL 不该被当成原文地址显示出来
    assert not any("custom://" in text for text in texts)


def test_article_panel_renders_with_editable_fields(
    offscreen: Client, gui_settings: Settings
) -> None:
    """
    「文章」面板画得出来，并列出标题与正文两节。

    它是人工创作文章的主要填内容入口，画不出来等于这个功能不存在。
    """
    from frontends.nicegui_app import article_panel

    record = asyncio.run(actions.create_blank_article())
    row = _row(gui_settings, record.id)

    container = ui.column()
    article_panel.render(container, row, on_change=lambda: None)

    texts = _labels(container)
    assert "标题" in texts
    assert "正文" in texts
    assert "媒体" in texts
    assert any("拷进这两个文件夹" in text for text in texts), "要写清素材放在哪里"


def test_editable_boxes_are_marked_editable(
    offscreen: Client, gui_settings: Settings
) -> None:
    """
    标题与正文两段文本都带 `editable` 类 —— 双击编辑靠的就是它。

    少了这个类，`dblclick` 处理器根本不会挂上，用户双击毫无反应，
    而界面上看不出任何异常。
    """
    from frontends.nicegui_app import article_panel

    record = asyncio.run(actions.create_blank_article())
    row = _row(gui_settings, record.id)

    container = ui.column()
    article_panel.render(container, row, on_change=lambda: None)

    editable = [
        child
        for child in container.descendants()
        if "editable" in child.classes and "wb-body" in child.classes
    ]
    assert len(editable) == 2, f"标题与正文两段都该可编辑，实际 {len(editable)} 段"


def test_article_panel_is_read_only_for_fetched_articles(
    offscreen: Client, gui_settings: Settings
) -> None:
    """
    抓取来的文章在文章面板里**只读**，并指明该走哪条路。

    改稿会把整篇 `article.md` 重渲染一遍，对历史产物来说那是改写档案。
    守卫在 store 层（改不动），这里只是别把一个必然会失败的编辑框摆出来。
    """
    from frontends.nicegui_app import article_panel

    ledger = Ledger(gui_settings.db_file)
    article_id, _ = ledger.register(
        RawItem(source_id="qbitai", via=SourceKind.RSS, url="https://e.com/x", title="抓来的文章")
    )
    directory = gui_settings.output_path / "articles" / "20261004" / f"fetched__{article_id[:8]}"
    directory.mkdir(parents=True)
    (directory / "article.md").write_text(
        "# 抓来的文章\n\n> 来源：https://e.com/x\n\n原始正文。\n", encoding="utf-8"
    )
    ledger.set_store_dir(article_id, directory.relative_to(gui_settings.output_path).as_posix())

    container = ui.column()
    article_panel.render(container, _row(gui_settings, article_id), on_change=lambda: None)

    editable = [
        child
        for child in container.descendants()
        if "editable" in child.classes and "wb-body" in child.classes
    ]
    assert editable == [], "抓取来的文章不该出现可编辑框"
    assert any("只读档案" in text for text in _labels(container)), "要说清为什么不能改、该走哪条路"


# ---------------------------------------------------------------------------
# 5. 一格产物都没有时的产物面板 / the production panel with nothing generated
# ---------------------------------------------------------------------------


def test_detail_panel_renders_the_skeleton(offscreen: Client, gui_settings: Settings) -> None:
    """
    一格产物都没有时展开「中视频」，面板里给的是**骨架**而不是一句「还没有生成」。

    这是新增的那条路：骨架要能被 `spoken_text` 读回正文，排版错了不会报错，
    只会让手写的稿子合成出空音频。
    """
    from dna.produce import ProductionKind
    from frontends.nicegui_app import detail_panel

    record = asyncio.run(actions.create_blank_article())
    row = _row(gui_settings, record.id)
    assert row.production(ProductionKind.NARRATION) is None, "前提：这一格确实还没生成"

    container = ui.column()
    detail_panel.render(
        container,
        row,
        ProductionKind.NARRATION,
        on_change=lambda: None,
        on_redo=lambda *a, **k: None,
    )

    texts = " ".join(_labels(container))
    assert "骨架" in texts, "要写明可以双击骨架自己写"
    assert "（在这里写正文" in texts


def test_detail_panel_renders_for_longform_without_a_skeleton(
    offscreen: Client, gui_settings: Settings
) -> None:
    """长视频没有骨架，退回原来的「还没有生成」提示 / The long-form script gets no skeleton."""
    from dna.produce import ProductionKind
    from frontends.nicegui_app import detail_panel

    record = asyncio.run(actions.create_blank_article())
    row = _row(gui_settings, record.id)

    container = ui.column()
    detail_panel.render(
        container,
        row,
        ProductionKind.LONGFORM,
        on_change=lambda: None,
        on_redo=lambda *a, **k: None,
    )

    texts = " ".join(_labels(container))
    assert "还没有生成" in texts
    assert "（在这里写正文" not in texts


# ---------------------------------------------------------------------------
# 6. 媒体格 / the media cell
# ---------------------------------------------------------------------------


def test_media_cell_is_clickable_for_a_custom_article(
    offscreen: Client, gui_settings: Settings
) -> None:
    """
    人工创作的文章即使素材目录是空的，媒体格也**可点**。

    不可点的话，用户就没有任何入口能把素材拷进去——这个功能等于不存在。
    （抓取来的文章仍然遵守「空目录不返回」的老规则。）
    """
    from frontends.nicegui_app import ledger_table

    record = asyncio.run(actions.create_blank_article())
    row = _row(gui_settings, record.id)
    assert row.article.image_count == 0 and row.article.video_count == 0

    targets = actions.media_targets(row.article)
    assert [p.name for p in targets] == ["images", "videos"]
    assert all(p.is_dir() for p in targets)

    container = ui.column()
    ledger_table.render_table(container, [row], on_change=lambda: None)
    clickable = [
        child
        for child in container.descendants()
        if "wb-num" in child.classes and "clickable" in child.classes
    ]
    assert clickable, "媒体格应当可点"


def test_fetched_article_media_cell_stays_inert(offscreen: Client) -> None:
    """
    抓取来的文章、没有素材时媒体格仍然不可点（不给一个点开是空的按钮）。
    """
    record = ArticleRecord(
        id="b" * 16,
        url="https://example.com/b",
        canonical_url="https://example.com/b",
        title="抓来的文章",
        feed_title="示例源",
        source_id="qbitai",
        via="rss",
        author=None,
        published_at=NOW,
        first_seen_at=NOW,
        fetched_at=NOW,
        status=FetchStatus.OK,
        text_len=100,
        image_count=0,
        video_count=0,
        store_dir=None,
        error=None,
        tags=[],
        fetch_count=1,
    )
    assert actions.media_targets(record) == []
