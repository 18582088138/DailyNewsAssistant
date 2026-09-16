"""
界面渲染冒烟测试：每个对话框/面板都能在无头环境里建出来、不抛异常。

    $PY -m pytest tests/frontends/test_render_smoke.py -q

**为什么需要它。** `test_workbench.py` 里有八十多个用例，但**一个都没有真正渲染过**
——它们查的是规格表、CSS 变量、字段清单。于是这一类错全都漏在网外：

- `issues/013` 的 `Invalid value`：一个 `ui.select` 的候选里有空串，
  Quasar 在**渲染那一刻**抛异常。规格层面完全正确，一渲染就崩。
- 拆分前端文件时最容易出的错是「某个模块 import 环了」或「控件建了但 slot
  不对」，这两种也只在渲染时暴露。

不需要浏览器：NiceGUI 的 `Client` 能当上下文管理器用，元素就建在内存里。
所以这套测试是**离线、零费用、秒级**的，可以进日常循环。
No browser needed: the client doubles as a context manager and the widgets are built
in memory, so this stays offline and fast enough for the routine loop.

不测什么：不测交互（点击、输入），不测样式长相。只测「建得出来」——
这条线以下的错误会让整个界面白屏，而这条线以上的错误顶多是不好看。

**一行没有产物的数据不够。** 这套测试原先构造的 `RowView` 产物字典是空的，
于是「查不到产物」和「查到了但画不出来」两条路都没走过 —— 语言哨兵漏到
`LANGUAGE_LABELS[lang]` 那次（展开面板直接 KeyError）就是这么漏过去的。
所以下面既有空产物的行，也有带一条真产物记录的行。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from nicegui import ui
from nicegui.client import Client
from nicegui.page import page as page_decorator

from dna.store.ledger import ArticleRecord, FetchStatus, Ledger
from frontends.nicegui_app import actions


@pytest.fixture
def offscreen() -> Iterator[Client]:
    """一个不连浏览器的 client，元素建在内存里。"""
    client = Client(page_decorator("/test"), request=None)
    with client:
        yield client


@pytest.fixture
def workbench_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_actions_settings
) -> Path:
    """
    一个装了一篇文章的临时台账。

    渲染表格必须有真数据：空表走的是「没有匹配的文章」那条分支，
    而崩溃恰恰发生在画格子的那条路上。
    """
    from dna.core.config import Settings, get_settings

    settings = Settings(_env_file=None, data_dir=tmp_path, output_dir=tmp_path / "out")
    monkeypatch.setattr("dna.core.config.settings.get_settings", lambda: settings)
    patch_actions_settings(settings)
    get_settings.cache_clear()

    ledger = Ledger(settings.db_file)
    from dna.core.models import RawItem, SourceKind

    ledger.register(
        RawItem(
            source_id="gui",
            via=SourceKind.GUI,
            url="https://example.com/a",
            title="某个标题够长足以通过成稿门槛",
        )
    )
    return tmp_path


def _row() -> actions.RowView:
    """构造一行表格数据，不碰数据库。"""
    now = datetime(2026, 9, 11, 10, 0)
    record = ArticleRecord(
        id="a" * 16,
        url="https://example.com/a",
        canonical_url="https://example.com/a",
        title="某个标题够长足以通过成稿门槛",
        feed_title="示例源",
        source_id="gui",
        via="gui",
        author=None,
        published_at=now,
        first_seen_at=now,
        fetched_at=now,
        status=FetchStatus.OK,
        text_len=4069,
        image_count=10,
        video_count=1,
        store_dir="20260911/x__aaaaaaaa",
        error=None,
        tags=["大模型"],
        fetch_count=1,
    )
    return actions.RowView(article=record, productions={}, is_new=True)


def _row_with_production() -> actions.RowView:
    """一行**有产物**的数据；键按库里那样是 `zh`，不是哨兵空串。"""
    from dna.store.ledger import ProductionRecord

    record = ProductionRecord(
        id=1,
        article_id="a" * 16,
        kind="summary",
        lang="zh",
        variant=None,
        instructions="用词再专业一点",
        status="ok",
        output_path="summary.md",
        chars=180,
        est_seconds=42.0,
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        tokens=900,
        calls=2,
        duration_ms=4200,
        error=None,
        created_at=datetime(2026, 9, 11, 10, 5),
        redo_of_id=None,
    )
    row = _row()
    return actions.RowView(
        article=row.article,
        productions={("summary", "zh"): record},
        is_new=False,
    )


# ---------------------------------------------------------------------------
# 表格 / the table
# ---------------------------------------------------------------------------


def test_表格能画出来(offscreen: Client) -> None:
    """一行真数据走完「表头 → 行 → 五个产物格」整条路。"""
    from frontends.nicegui_app import ledger_table

    container = ui.column()
    ledger_table.render_table(container, [_row()], on_change=lambda: None)
    assert container.default_slot.children, "表格一个元素都没建出来"


def test_有产物的行也能画(offscreen: Client, workbench_db: Path) -> None:
    """
    格子里画的是**查到的那条产物**，不是「未生成」。

    不带 `lang` 调 `row.production()` 时传下去的是哨兵空串，而字典键是 `zh`——
    不归一化的话这里一条都查不到，整张表静默地显示成「未生成」。
    """
    from dna.produce import ProductionKind
    from frontends.nicegui_app import ledger_table

    row = _row_with_production()
    assert row.production(ProductionKind.SUMMARY) is not None, (
        "不带 lang 查不到中文版产物：哨兵空串当成语言码用了"
    )

    container = ui.column()
    ledger_table.render_table(container, [row], on_change=lambda: None)
    assert container.default_slot.children


def test_展开面板能画出来(offscreen: Client, workbench_db: Path) -> None:
    """
    展开面板走的是另一条路：它拿 `lang` 当 `LANGUAGE_LABELS` 的下标。

    表格的冒烟测试盖不到它——点开格子才会调 `detail_panel.render`，
    而这套测试不测点击。所以这里直接调它。
    """
    from dna.produce import ProductionKind
    from frontends.nicegui_app import detail_panel

    container = ui.column()
    for kind in (ProductionKind.SUMMARY, ProductionKind.NARRATION_AUDIO):
        detail_panel.render(
            container,
            _row_with_production(),
            kind,
            on_change=lambda: None,
            on_redo=lambda *a, **k: None,
        )
        assert container.default_slot.children


def test_空表也能画(offscreen: Client) -> None:
    """空表走的是另一条分支（「先跑 dna fetch」那块提示），同样不能崩。"""
    from frontends.nicegui_app import ledger_table

    container = ui.column()
    ledger_table.render_table(container, [], on_change=lambda: None)
    assert container.default_slot.children


# ---------------------------------------------------------------------------
# 对话框与面板 / dialogs and panels
# ---------------------------------------------------------------------------


def test_设置面板能打开(offscreen: Client, workbench_db: Path) -> None:
    """
    四个子页一次全建出来。

    这是最容易崩的一个：它把 `profile.yaml` 与 `.env` 的每个字段都变成控件，
    任何一个字段的候选值里混进空串就会在渲染时抛 `Invalid value`。
    """
    from frontends.nicegui_app import settings_dialog

    settings_dialog.open_dialog()


def test_设置面板的分页能自己滚(offscreen: Client, workbench_db: Path) -> None:
    """
    「运行设置」那一页有几十项，必须能滚。

    Quasar 的 `q-panel-parent` 是 `overflow: hidden`：分页比它高就直接裁掉，
    **而且不出滚动条**，于是底下几项根本点不到（用户实测报的就是这个）。
    靠 `.wb-dialog` 上那句 `overflow: auto` 是不行的——卡片本身没超高。

    样式好不好看测不了，但「这个类还在、CSS 里那条规则还在」测得了：
    这两样任意一个被删掉，界面就回到裁内容的状态。
    """
    from frontends.nicegui_app import settings_dialog, theme

    assert ".wb-dialog-body" in theme._CSS, "滚动规则从主题里消失了"
    assert "overflow-y: auto" in theme._CSS

    settings_dialog.open_dialog()
    panels = [
        e for e in offscreen.elements.values()
        if "wb-dialog-body" in getattr(e, "_classes", [])
    ]
    assert panels, "设置面板的 tab_panels 没挂 wb-dialog-body，内容会被裁掉"


def test_导入链接对话框能打开(offscreen: Client) -> None:
    from frontends.nicegui_app import import_dialog

    import_dialog.open_dialog(on_done=lambda: None)


def test_从订阅导入对话框能打开(offscreen: Client) -> None:
    """它的源下拉来自 `sources.yaml`，被禁用的源不能混进候选里。"""
    from frontends.nicegui_app import source_dialog

    source_dialog.open_dialog(on_done=lambda: None)


def test_声音配置控件能建出来(offscreen: Client) -> None:
    """
    三种模式（内置音色 / 音色设计 / 克隆参考音频）的控件一次全建。

    切模式时无关控件是**灰掉而不是隐藏**，所以三组控件始终都在，
    任何一组的候选有问题都会当场崩。
    """
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app.voice_controls import VoiceControls

    # 音色列表里刻意放一个空串：issues/013 那个 Invalid value 就是这么来的
    VoiceControls(voices=["Serena", ""], base=VoiceSpec(speaker="Serena"))


def test_顶层页面能装配(offscreen: Client, workbench_db: Path) -> None:
    """
    `build()` 是整个界面的装配根：顶栏 + 筛选 + 表格 + 分页。

    它崩了就是白屏，而白屏在开发时是最难定位的一种错——
    浏览器控制台里只有一个 WebSocket 断开。
    """
    from frontends.nicegui_app import main as gui_main

    gui_main.build()
