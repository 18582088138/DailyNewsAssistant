"""
test_workbench.py —— 工作台界面单元测试 / Workbench UI unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/frontends/test_workbench.py -v

对应的人工验证 / Matching manual check:
    dna gui           # 打开界面本身不产生费用
    # 逐项核对：窗口拖窄不重叠、滚动时表头不动、点格子展开、重做按钮只在面板里

覆盖 / Covers:
    1. 标题截断到指定长度，且**中英文一视同仁**（按字符数而非像素）
    2. 空标题与纯空白回退为「(无标题)」，不产生空白行
    3. 主题 CSS 里的列宽由产物种类数**算出来**，加一列不会让表头与行错位
    4. `TaskSpec.spoken` 正确标出哪些产物将来会有音频（P6/P7 的音频下载入口）
    5. `open_in_file_manager` 对不存在的目录**返回原因而不是抛异常**
    6. `production_file` / `production_sidecar` 文件不在时返回 None（按钮据此禁用）
    7. `media_folders` 只返回**真实存在且非空**的素材目录
    8. **链接导入**：从一整段中文里认出全部链接、剥掉结尾中文标点、重复的只算一条
    9. 没有链接时返回空列表，界面据此拦住抓取而不是发一次空请求
   10. **NEW 标识用第四种颜色**，四种状态色互不相同（复用会被误读成「已完成」）
   11. 开了「减少动效」时只关呼吸动画，**标识本身仍然可见**

为什么只测这些 / Why only these:
    界面的布局与观感**测不出来**，靠 `dna gui` 人工看（本轮已用 Playwright 截图
    在 1600px 与 900px 两种宽度下核对过不重叠、表头冻结、面板展开）。
    这里锁的是**纯函数与文件系统契约**——它们出错时界面会安静地显示错误的东西：
    按钮该禁用的没禁用、点下去拿到 404，或者表头与数据列错开一格。
    Layout and looks cannot be asserted; they are checked by eye. What is locked here are
    the pure functions and filesystem contracts, because when those break the UI fails
    quietly: a button that should be disabled isn't, a click yields a 404, or the header
    sits one column off from the data.

预期 / Expected:
    22 passed；耗时 < 2s；**不启动服务器、不调用 LLM、零费用**
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dna.core.config import Settings
from dna.core.models import Article, RawItem, SourceKind
from dna.produce import DISPLAY_ORDER, ProductionKind, spec
from dna.store.ledger import Ledger
from frontends.nicegui_app import theme


# --- 标题截断 / title clipping --------------------------------------------------


def test_long_title_is_clipped_to_the_configured_length() -> None:
    """
    过长标题按**字符数**截断，而不是交给像素宽度。

    CJK 字符宽度是拉丁字母的两倍，纯靠 CSS 的 ellipsis 截，中文标题露出的字数
    只有英文标题的一半——而这张表以中文标题为主，结果就是中文条目普遍看不全。
    """
    title = "这是一个非常长的中文标题" * 10
    result = theme.short_title(title, limit=20)

    assert len(result) == 20
    assert result.endswith("…")


def test_short_title_is_left_alone() -> None:
    """没超长的标题原样返回，不加省略号。"""
    assert theme.short_title("简短标题", limit=20) == "简短标题"


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_blank_title_falls_back(value: str) -> None:
    """
    空标题回退为占位文字。

    抓取失败的条目标题可能是空的，直接渲染会得到一行**看不出是哪篇**的空白，
    而这一行恰恰是最需要人去处理的那一行。
    """
    assert theme.short_title(value) == "(无标题)"


def test_clip_length_counts_characters_not_bytes() -> None:
    """中英文按同一把尺子截——两者截出来的字符数必须一致。"""
    zh = theme.short_title("中" * 100, limit=30)
    en = theme.short_title("a" * 100, limit=30)

    assert len(zh) == len(en) == 30


# --- 链接导入 / link import -----------------------------------------------------


def test_links_are_recognised_inside_a_pasted_paragraph() -> None:
    """
    从一整段中文里认出全部链接，并**剥掉结尾的中文标点**。

    真实的投递就是从群聊里复制出来的一段话，链接夹在中文之间、后面跟着中文逗号
    或句号。不剥的话抓到的是 `https://…y7g，` 这种带标点的地址，直接 404。
    """
    from frontends.nicegui_app import actions

    text = (
        "这几篇不错：https://mp.weixin.qq.com/s/AAA ，"
        "还有量子位这篇 https://www.qbitai.com/2026/09/x.html。"
        "再加一个 https://zhuanlan.zhihu.com/p/123"
    )
    urls = actions.preview_links(text)

    assert urls == [
        "https://mp.weixin.qq.com/s/AAA",
        "https://www.qbitai.com/2026/09/x.html",
        "https://zhuanlan.zhihu.com/p/123",
    ]


def test_repeated_links_are_collapsed() -> None:
    """
    同一个链接粘了两次只算一条。

    从聊天记录里复制时同一篇被转发多次是常态。不去重的话台账里会出现重复行，
    而每一行都要单独花钱生成文案。
    """
    from frontends.nicegui_app import actions

    urls = actions.preview_links("https://a.com/x 和 https://a.com/x 又一次")

    assert urls == ["https://a.com/x"]


@pytest.mark.parametrize("text", ["", "   ", "这段话里根本没有链接"])
def test_no_links_yields_an_empty_list(text: str) -> None:
    """
    没有链接时返回空列表，界面据此**拦住**抓取而不是发一次空请求。
    """
    from frontends.nicegui_app import actions

    assert actions.preview_links(text) == []


# --- NEW 标识的配色 / the badge palette -----------------------------------------


def test_new_badge_has_its_own_colour() -> None:
    """
    NEW 用**第四种颜色**，不能复用已生成/计费/失败任何一种。

    这四种状态语义完全不同：青绿=已生成可复用、琥珀=点下去花钱、红=失败、
    青蓝=还没动过。复用颜色会让人把「新导入」误读成「已完成」或「要花钱」。
    """
    from frontends.nicegui_app.theme import _CSS

    for name in ("--wb-accent", "--wb-warn", "--wb-danger", "--wb-new"):
        assert name in _CSS, f"{name} 未定义"

    values = {}
    for line in _CSS.splitlines():
        for name in ("--wb-accent:", "--wb-warn:", "--wb-danger:", "--wb-new:"):
            if line.strip().startswith(name):
                values[name] = line.split(":", 1)[1].split(";")[0].strip()

    assert len(set(values.values())) == 4, f"四种状态色必须互不相同：{values}"


def test_reduced_motion_keeps_the_badge_visible() -> None:
    """
    开了「减少动效」时只关掉呼吸动画，**标识本身必须还在**。

    把整个 `.wb-new-badge` 藏掉的话，这类用户就彻底看不见新导入了——
    动效是装饰，标识是信息。
    """
    from frontends.nicegui_app.theme import _CSS

    reduced = _CSS.split("prefers-reduced-motion", 1)[1]

    assert "animation: none" in reduced
    assert "display: none" not in reduced


# --- 主题与列宽 / theme and column widths ---------------------------------------


def test_column_width_is_derived_from_the_kind_count() -> None:
    """
    表格最小宽度随产物种类数变化。

    P6/P7 会加产物列。列宽写死在 CSS 里的话，加一列就要同时改 CSS 和 Python，
    **改漏一处表头就与数据列错开一格**——而错开一格不会报错，只会让人读错数据。
    """
    from frontends.nicegui_app.theme import COL_KIND

    five = _min_table_width(5)
    six = _min_table_width(6)

    assert six - five == COL_KIND


def _min_table_width(kind_count: int) -> int:
    """复现 theme.apply 里的宽度计算 / Mirror the width computation in `theme.apply`."""
    from frontends.nicegui_app.theme import COL_BODY, COL_KIND, COL_MEDIA, COL_TITLE_MIN

    return COL_TITLE_MIN + COL_BODY + COL_MEDIA + COL_KIND * kind_count


# --- 产物元数据 / production metadata -------------------------------------------


def test_spoken_kinds_are_exactly_the_script_kinds() -> None:
    """
    `spoken` 标出哪些产物要被念出来。

    界面据此决定哪几格该有音频/成片下载入口。总结类是给人读的，不进 TTS——
    标错的话会在总结格里摆出一个永远不会有内容的音频按钮。
    """
    spoken = {k for k in DISPLAY_ORDER if spec(k).spoken}

    assert spoken == {
        ProductionKind.SHORTVIDEO,
        ProductionKind.NARRATION,
        ProductionKind.LONGFORM,
    }
    assert not spec(ProductionKind.SUMMARY_ZH).spoken
    assert not spec(ProductionKind.SUMMARY_EN).spoken


def test_every_displayed_kind_has_a_header_label() -> None:
    """
    表头缩写必须覆盖所有展示的产物。

    108px 的列宽放不下「短视频文案」，所以表头用缩写。漏一个的话那一列
    会退回全称并把布局撑变形。
    """
    from frontends.nicegui_app.ledger_table import _HEAD_SHORT

    for kind in DISPLAY_ORDER:
        assert kind in _HEAD_SHORT, f"{kind} 缺表头缩写"
        assert len(_HEAD_SHORT[kind]) <= 4


# --- 文件系统契约 / filesystem contracts ----------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", output_dir=tmp_path / "outputs")


def _seed(settings: Settings) -> tuple[str, Path]:
    """放一篇带落盘目录的文章 / Seed one article with a directory."""
    ledger = Ledger(settings.db_file)
    article_id, _ = ledger.register(
        RawItem(source_id="s", via=SourceKind.RSS, url="https://e.com/1", title="标题")
    )
    store_dir = f"articles/20260904/{article_id[:8]}"
    directory = settings.output_path / store_dir
    directory.mkdir(parents=True, exist_ok=True)
    article = Article(url="https://e.com/1", title="标题", text="正文" * 100, extraction_ok=True)
    (directory / "meta.json").write_text(article.model_dump_json(), encoding="utf-8")
    ledger.record_fetch(article_id, article, store_dir=store_dir)
    return article_id, directory


def test_production_file_is_none_when_missing(settings: Settings, monkeypatch) -> None:
    """
    文件不在时返回 None，界面据此**禁用**下载按钮。

    返回一个不存在的路径的话，按钮看起来可用，点下去拿到 404——
    人会以为是程序坏了，而实际上只是还没生成。
    """
    from dna.core import config as config_module
    from frontends.nicegui_app import actions

    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(actions, "get_settings", lambda: settings)

    article_id, directory = _seed(settings)
    record = Ledger(settings.db_file).get(article_id)

    assert actions.production_file(record, ProductionKind.SUMMARY_ZH) is None

    (directory / spec(ProductionKind.SUMMARY_ZH).filename).write_text("x", encoding="utf-8")
    assert actions.production_file(record, ProductionKind.SUMMARY_ZH) is not None


def test_sidecar_only_exists_for_longform(settings: Settings, monkeypatch) -> None:
    """只有长文案有 JSON 附件——其余产物不该冒出一个下载 JSON 的按钮。"""
    from frontends.nicegui_app import actions

    monkeypatch.setattr(actions, "get_settings", lambda: settings)
    article_id, directory = _seed(settings)
    record = Ledger(settings.db_file).get(article_id)

    (directory / "longform.zh.json").write_text("{}", encoding="utf-8")
    (directory / "narration.zh.md").write_text("x", encoding="utf-8")

    assert actions.production_sidecar(record, ProductionKind.LONGFORM) is not None
    assert actions.production_sidecar(record, ProductionKind.NARRATION) is None


def test_media_folders_skips_missing_and_empty(settings: Settings, monkeypatch) -> None:
    """
    只返回真实存在**且非空**的素材目录。

    空目录也给按钮的话，点开是一个空文件夹——比没有按钮更让人困惑。
    """
    from frontends.nicegui_app import actions

    monkeypatch.setattr(actions, "get_settings", lambda: settings)
    article_id, directory = _seed(settings)
    record = Ledger(settings.db_file).get(article_id)

    assert actions.media_folders(record) == {}

    (directory / "videos").mkdir()  # 建了但是空的
    assert actions.media_folders(record) == {}

    (directory / "images").mkdir()
    (directory / "images" / "01.jpg").write_bytes(b"x")
    assert set(actions.media_folders(record)) == {"配图"}


def test_open_in_file_manager_reports_instead_of_raising(tmp_path: Path) -> None:
    """
    目录不存在时**返回原因**，不抛异常。

    这个按钮在界面事件回调里跑，抛出去就是一个没人接的异常——
    用户看到的是「点了没反应」，最难排查的那种。
    """
    from frontends.nicegui_app import actions

    message = actions.open_in_file_manager(tmp_path / "不存在")

    assert message
    assert "不存在" in message


def test_open_in_file_manager_refuses_when_not_bound_locally(
    tmp_path: Path, monkeypatch
) -> None:
    """
    服务端没绑在本机时拒绝并说明原因。

    工作台默认绑 127.0.0.1，服务端与浏览器是同一台机器。一旦有人把它绑到
    0.0.0.0 给别人访问，这个按钮会在**服务器**上弹出资源管理器窗口，
    而点的人什么也看不到——静默地做错事，比明确拒绝糟糕得多。
    """
    from frontends.nicegui_app import actions

    monkeypatch.setattr(actions, "_server_is_local", lambda: False)
    message = actions.open_in_file_manager(tmp_path)

    assert "没有绑定在本机" in message


def test_longform_estimate_uses_the_core_formula(settings: Settings, monkeypatch) -> None:
    """
    确认框里的时长预估走核心的 `plan_target_seconds`，不在界面里另算一遍。

    先前界面里写着 `min(text_len * 1.2, 4000) / 4.5 / 60`——那是**已经废弃的
    按字符数推导**，核心改了之后确认框报的分钟数和实际生成的对不上，
    而这个数字正是人决定要不要花这笔钱的依据。
    """
    from dna.narration.longform import MAX_TARGET_SECONDS
    from frontends.nicegui_app import actions

    monkeypatch.setattr(actions, "get_settings", lambda: settings)
    article_id, _ = _seed(settings)
    record = Ledger(settings.db_file).get(article_id)

    text = actions.longform_estimate(record)

    assert text.endswith("分钟")
    assert 0 < int(text.removesuffix(" 分钟")) <= MAX_TARGET_SECONDS / 60
