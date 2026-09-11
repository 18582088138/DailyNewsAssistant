"""
test_workbench.py —— 工作台界面单元测试 / Workbench UI unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/frontends/test_workbench.py -v

对应的人工验证 / Matching manual check:
    dna gui           # 打开界面本身不产生费用
    # 逐项核对：窗口拖窄不重叠、滚动时表头不动、点格子展开、重做按钮只在面板里
    # 勾两篇 → 批量条出现 → 重新抓取；点正文格开 article.md、点媒体格开 images/
    # 点标题下面的链接**只开新标签页，不展开这一行**
    # 从订阅导入 → 选 1 个源、最近 3 天；设置里改一个 cta_line 存盘，
    # git diff config/profile.yaml 应只有那一行变化、注释无改动

覆盖 / Covers:
    1. 标题截断到指定长度，且**中英文一视同仁**（按字符数而非像素）
    2. 空标题与纯空白回退为「(无标题)」，不产生空白行
    3. 主题 CSS 里的列宽由产物种类数**算出来**，加一列不会让表头与行错位
    4. `TaskSpec.spoken` 正确标出哪些产物将来会有音频（P6/P7 的音频下载入口）
    5. `open_in_file_manager` 对不存在的路径**返回原因而不是抛异常**，且**接受文件**；
       非 Windows 不进 ctypes 分支；Windows 下目录走 explorer 且顶窗口是异步的
    6. `production_file` / `production_sidecar` 文件不在时返回 None（按钮据此禁用）
    7. `media_folders` 只返回**真实存在且非空**的素材目录
    8. **链接导入**：从一整段中文里认出全部链接、剥掉结尾中文标点、重复的只算一条
    9. 没有链接时返回空列表，界面据此拦住抓取而不是发一次空请求
   10. **NEW 标识用第四种颜色**，四种状态色互不相同（复用会被误读成「已完成」）
   11. 开了「减少动效」时只关呼吸动画，**标识本身仍然可见**
   12. 最小宽度把勾选列也算进去；那道重线的列序号跟着改成 5；标题列不被居中
   13. `short_url` 剥协议头、**从中间截**（从右边切会让同源的几行看起来一样）
   14. 「全选本页」三态，中间那一态不能省
   15. 勾选跨刷新保留，且顺序 = 勾选顺序
   16. `body_file` / `media_target` 缺文件时返回 None（格子据此不可点）
   17. `over_target` **两端都判**、跟 profile 走、不按字数验收的产物永远不标超长
   18. 设置面板：白名单与界面字段表**双向对齐**、密钥不回显、环境变量遮盖能识别
   19. 设置面板覆盖 `Profile` 的**每一个**字段（「全开」是确认过的范围）
   20. 保存提示说清哪些要重启、哪些即时生效，一项没改时不说「已保存」
   21. 订阅选项来自 `load_sources()` 而不是台账；配置坏了对话框仍能打开
   22. `tts_status()` 探活**不拉起服务**；离线时 `detail` 写清地址与下一步
   23. 翻页：逐页切开同一批文章不重不漏；页码越界夹回最后一页
   24. 后置筛选撞到扫描上限时 `scanned_cap=True`（不静默截断）
   25. 设置面板四个子页：字段表与 `Profile` 一一对应、每项只归一页、
       字数框的估算秒数走 `seconds_for_units`、与时长窗口不一致时告警
   26. 声音配置取值：内置音色清掉克隆参数 · **换了参考音频就丢掉参考文本**
       （原话只对那一条音频成立）· 解析不到时退回原音色 · 改过的正文与角色原样带走
   27. 换种子重掷：**第一版不带种子**（要和自动合成同一版）· 同一 `(段号, 次数)`
       稳定复现 · 相邻段与相邻次数都不撞
   28. 复用筛选：没动过的段复用 · **改过字或换过音色的段不复用**（否则拿旧波形
       拼新文本）· 空段丢掉后复用下标跟着前移 · 写回稿子的文本不插分隔符

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
    耗时 < 2s；**不启动服务器、不调用 LLM、零费用**
"""

from __future__ import annotations

import os
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

    five = theme.min_table_width(5)
    six = theme.min_table_width(6)

    assert six - five == COL_KIND


def test_min_width_counts_every_column_including_the_pick_column() -> None:
    """
    最小宽度必须把**每一列**都算进去，勾选列也不例外。

    加了勾选列却忘了加进宽度，横向滚动条会提前到位，最右边那一列被切掉一截——
    而被切掉的恰好是长文案，最贵的那一格。
    """
    from frontends.nicegui_app.theme import (
        COL_BODY,
        COL_KIND,
        COL_MEDIA,
        COL_PICK,
        COL_TITLE_MIN,
    )

    assert theme.min_table_width(5) == (
        COL_PICK + COL_TITLE_MIN + COL_BODY + COL_MEDIA + COL_KIND * 5
    )


def test_the_heavy_divider_counts_the_pick_column() -> None:
    """
    抓取列与产物列之间那道重线落在**第 5 列**。

    列顺序是 勾选 / 标题 / 正文 / 媒体 / 第一格产物。勾选列插到最前面时如果
    没把这个序号从 4 改成 5，重线会跑到「媒体」左边——于是那道「把抓取信息和
    产物分开」的线，分开的是「正文」和「媒体」，指示的分组是错的。
    """
    from frontends.nicegui_app.theme import _CSS

    assert ".wb-grid > div:nth-child(5)" in _CSS
    assert ".wb-grid > div:nth-child(4)" not in _CSS


def test_the_title_column_is_not_centred() -> None:
    """
    标题列左对齐——**不能用 `div + div` 选中「除第一列外」**。

    勾选列插到最前面之后，`div + div` 命中的第一个就是标题列，一整列中文标题
    被居中，长短不一的行左边缘全都对不齐，这张表就没法从上往下扫了。
    """
    from frontends.nicegui_app.theme import _CSS

    assert ".wb-head > div:nth-child(2) { justify-content: flex-start; }" in _CSS
    assert ".wb-head > div + div" not in _CSS


def test_over_length_tone_is_actually_used() -> None:
    """
    `wb-over` 这个类必须真的被表格用上。

    它在主题里定义了很久却全仓没人引用——定义了不用，等于超长的格子和合格的
    格子长得一模一样，而「超没超字数」正是决定要不要重做的唯一依据。
    """
    from frontends.nicegui_app import ledger_table
    from frontends.nicegui_app.theme import _CSS

    assert ".wb-over" in _CSS
    source = Path(ledger_table.__file__).read_text(encoding="utf-8")
    assert "wb-over" in source


# --- 原文链接 / the source link -------------------------------------------------


def test_source_url_drops_the_scheme() -> None:
    """`https://` 占 8 个字符却不携带任何信息，小字号那一行放不下这种浪费。"""
    assert theme.short_url("https://www.qbitai.com/x") == "www.qbitai.com/x"
    assert theme.short_url("http://a.com/b") == "a.com/b"


def test_long_url_is_clipped_in_the_middle() -> None:
    """
    从**中间**截，域名和尾巴都留着。

    从右边一刀切的话，同一个站点下的几篇文章会显示成一模一样的地址
    （`mp.weixin.qq.com/s/…` 后面全被切掉），那一行就等于没有信息。
    """
    url = "https://mp.weixin.qq.com/s/" + "A" * 200
    result = theme.short_url(url, limit=40)

    assert len(result) == 40
    assert result.startswith("mp.weixin.qq.com/s/")
    assert "…" in result
    assert result.endswith("A")


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_url_yields_nothing(value: str) -> None:
    """没有链接时返回空串，界面据此**不渲染**那一行，而不是渲染一个死链。"""
    assert theme.short_url(value) == ""


# --- 勾选 / selection -----------------------------------------------------------


@pytest.mark.parametrize(
    ("picked", "expected"),
    [
        ([], "check_box_outline_blank"),
        (["a"], "indeterminate_check_box"),
        (["a", "b"], "check_box"),
    ],
)
def test_select_all_is_tri_state(picked: list[str], expected: str) -> None:
    """
    「全选本页」有三态，中间那一态不能省。

    省掉的话「本页已经勾了几篇」完全看不出来：图标显示未选中，人点一下以为是
    全选，实际执行的是全部取消——刚勾好的十篇一次没了。
    """
    from frontends.nicegui_app.ledger_table import page_icon

    assert page_icon(["a", "b"], dict.fromkeys(picked)) == expected


def test_selection_survives_a_refresh() -> None:
    """
    勾选跨刷新保留。

    批量重抓跑完会刷新表格。勾选如果被清掉，「重抓完看看这几篇好了没有」
    就要从头再勾一遍十篇——而这正是刚做完那件事的下一步。
    """
    from frontends.nicegui_app import ledger_table

    ledger_table.clear_selection()
    try:
        ledger_table._SELECTED.update(dict.fromkeys(["x", "y"]))
        assert ledger_table.selected_ids() == ["x", "y"]  # 顺序 = 勾选顺序
    finally:
        ledger_table.clear_selection()
    assert ledger_table.selected_ids() == []


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
    assert not spec(ProductionKind.SUMMARY).spoken


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

    assert actions.production_file(record, ProductionKind.SUMMARY) is None

    (directory / spec(ProductionKind.SUMMARY).filename_for("zh")).write_text("x", encoding="utf-8")
    assert actions.production_file(record, ProductionKind.SUMMARY) is not None


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


def test_body_file_is_none_until_the_body_exists(settings: Settings, monkeypatch) -> None:
    """
    没有 `article.md` 就返回 None，界面据此**不把正文格做成可点的**。

    抓取失败的文章目录里根本没有正文文件。给一个点开报错的格子，比不给更糟——
    人会以为程序坏了，而实际上只是这一篇没抓到。
    """
    from frontends.nicegui_app import actions

    monkeypatch.setattr(actions, "get_settings", lambda: settings)
    article_id, directory = _seed(settings)
    record = Ledger(settings.db_file).get(article_id)

    assert actions.body_file(record) is None

    (directory / "article.md").write_text("# 标题\n\n正文", encoding="utf-8")
    assert actions.body_file(record) == directory / "article.md"


def test_media_target_prefers_images(settings: Settings, monkeypatch) -> None:
    """
    媒体格优先开配图目录，两者都没有时返回 None。

    绝大多数文章只有配图；两者都有时人要看的通常也是配图（它进图文版），
    视频在展开面板里另有入口。
    """
    from frontends.nicegui_app import actions

    monkeypatch.setattr(actions, "get_settings", lambda: settings)
    article_id, directory = _seed(settings)
    record = Ledger(settings.db_file).get(article_id)

    assert actions.media_target(record) is None

    (directory / "videos").mkdir()
    (directory / "videos" / "01.mp4").write_bytes(b"x")
    assert actions.media_target(record).name == "videos"

    (directory / "images").mkdir()
    (directory / "images" / "01.jpg").write_bytes(b"x")
    assert actions.media_target(record).name == "images"


def test_open_in_file_manager_accepts_a_file(tmp_path: Path, monkeypatch) -> None:
    """
    接受**文件**，不只是目录。

    「点正文栏打开对应的文件」要的就是这个：Windows 下 `os.startfile` 会用默认
    编辑器打开 `article.md`。先前这里写着 `is_dir()`，于是正文格永远只报
    「目录不存在」——文件明明就在那里。
    """
    from frontends.nicegui_app import actions

    target = tmp_path / "article.md"
    target.write_text("# 标题", encoding="utf-8")

    opened: list[Path] = []
    monkeypatch.setattr(actions, "_server_is_local", lambda: True)
    monkeypatch.setattr(actions.os, "startfile", opened.append, raising=False)
    monkeypatch.setattr(actions.sys, "platform", "win32")

    assert actions.open_in_file_manager(target) == ""
    assert opened == [target]


# --- 超出字数区间 / the over-length state ----------------------------------------


def _prod(chars: int, *, kind: str = "summary", status: str = "ok"):
    """造一条产物记录 / Build one production row."""
    from dna.store.ledger import ProductionRecord

    return ProductionRecord(
        id=1, article_id="a", kind=kind, lang="zh", variant=None, instructions=None,
        status=status, output_path=None, chars=chars, est_seconds=None,
        llm_provider=None, llm_model=None, tokens=0, calls=1, duration_ms=0,
        error=None, created_at=None, redo_of_id=None,
    )


@pytest.fixture
def narrow_profile(monkeypatch):
    """把字数窗口固定下来 / Pin the character windows."""
    from dna.core import config as config_module
    from dna.core.config import Profile

    profile = Profile(summary_chars=(80, 100), shortvideo_chars=(110, 160))
    monkeypatch.setattr(config_module, "safe_profile", lambda: profile)
    return profile


@pytest.mark.parametrize(
    ("chars", "over"),
    [(79, True), (80, False), (90, False), (100, False), (101, True)],
)
def test_over_target_uses_the_profile_window(narrow_profile, chars: int, over: bool) -> None:
    """
    区间是闭区间：边界上的那一条算合格。

    **两端都判**：太短和太长一样不能发，而只判上限的话，一段 30 字的「摘要」
    会安静地显示成合格。
    """
    from frontends.nicegui_app import actions

    assert actions.over_target(_prod(chars), "summary") is over


def test_over_target_follows_the_profile_not_the_database(monkeypatch) -> None:
    """
    改了 profile 的窗口，历史产物跟着重新判定——这正是不把这个布尔值存进库的理由。

    存下来的话，改完设置面板里的字数窗口，表格显示的合格标准和下一次生成用的
    标准就不是同一个了。
    """
    from dna.core import config as config_module
    from dna.core.config import Profile
    from frontends.nicegui_app import actions

    record = _prod(200)

    monkeypatch.setattr(config_module, "safe_profile", lambda: Profile(summary_chars=(80, 100)))
    assert actions.over_target(record, "summary") is True

    monkeypatch.setattr(config_module, "safe_profile", lambda: Profile(summary_chars=(150, 300)))
    assert actions.over_target(record, "summary") is False


@pytest.mark.parametrize("kind", ["longform", "shortvideo_audio"])
def test_kinds_without_a_window_are_never_over(narrow_profile, kind: str) -> None:
    """
    不按字数验收的产物永远不标超长。

    长文案按时长验收、音频根本不是文本。把它们也判一遍的话，`char_window`
    返回 None 会被当成 `(0, 0)`，于是每一格都变成琥珀色——图例就失效了。
    """
    from frontends.nicegui_app import actions

    assert actions.over_target(_prod(9999, kind=kind), kind) is False


def test_failed_and_missing_productions_are_not_over(narrow_profile) -> None:
    """失败和未生成不是「超长」——它们各有自己的形状（▲ 与 ○）。"""
    from frontends.nicegui_app import actions

    assert actions.over_target(None, "summary") is False
    assert actions.over_target(_prod(0, status="failed"), "summary") is False


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


def test_instructions_are_not_sent_when_the_switch_is_off() -> None:
    """
    关掉「修改指令」就必须真的不发那句话。

    实测报的是「关了开关照样生效」：输入框的文字与开关状态原本合成一个字段，
    关掉只是把框藏起来，预填的上一句要求照旧进了提示词——于是每次重做都还按
    「增加生成文本长度」写，稿子越写越长、回炉三次仍超时，而界面上看不出原因。
    """
    from frontends.nicegui_app.detail_panel import effective_instructions

    state = {"instructions": "增加生成文本长度", "use": True}
    assert effective_instructions(state) == "增加生成文本长度"

    # 关掉开关：不发，但**文字要留着**，再打开时还在
    state["use"] = False
    assert effective_instructions(state) == ""
    assert state["instructions"] == "增加生成文本长度"


# --- 设置面板 / the settings panel ---------------------------------------------


def test_every_writable_env_key_has_a_field() -> None:
    """
    白名单与界面字段表**双向对齐**。

    少一项：那一项永远改不到，而白名单看上去说它可以改。
    多一项：界面上摆着一个输入框，改完 `save_env` 静默拒写，只在日志里留一行 warning。
    两种漂移都不会报错，所以在这里锁住。
    """
    from dna.core.config_edit import ENV_ALLOWLIST
    from frontends.nicegui_app import actions

    drawn = {f.key for f in actions.ENV_FIELDS}
    assert drawn == set(ENV_ALLOWLIST)


def test_secret_fields_match_the_secret_keys() -> None:
    """标了 secret 的必须正好是 SECRET_KEYS——漏标一个，真密钥就会显示在界面上。"""
    from dna.core.config_edit import SECRET_KEYS
    from frontends.nicegui_app import actions

    assert {f.key for f in actions.ENV_FIELDS if f.secret} == set(SECRET_KEYS)


def test_secrets_are_never_displayed_in_full(monkeypatch) -> None:
    """密钥框里放的是打码值。界面会被截图，真值不能出现在任何一处。"""
    from dna.core.config import Settings as RealSettings
    from frontends.nicegui_app import actions

    secret = "sk-0123456789abcdefghijklmnopqrstuv"
    settings = RealSettings(deepseek_api_key=secret, _env_file=None)
    monkeypatch.setattr(actions, "get_settings", lambda: settings)

    field = next(f for f in actions.ENV_FIELDS if f.key == "DEEPSEEK_API_KEY")
    shown = actions.env_display(field)

    assert secret not in shown
    assert shown.startswith("sk-0123")
    assert str(len(secret)) in shown


def test_env_shadowing_is_detected(monkeypatch) -> None:
    """
    被系统环境变量盖住的项要标出来。

    优先级是环境变量 > `.env`。不标的话人会反复改同一项然后认为程序坏了。
    """
    from frontends.nicegui_app import actions

    field = next(f for f in actions.ENV_FIELDS if f.key == "HTTPS_PROXY")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    assert actions.env_shadowed(field) is False

    monkeypatch.setenv("HTTPS_PROXY", "http://somewhere:913")
    assert actions.env_shadowed(field) is True


def test_the_model_dropdown_warns_about_the_reasoning_model() -> None:
    """
    上一轮「GUI 卡住」的根因就是这一项被换成了推理模型，而界面上看不出来。

    提示里必须留着倍数——只写「较慢」的话，没人会把它和一个像死循环的界面联系起来。
    """
    from frontends.nicegui_app import actions

    field = next(f for f in actions.ENV_FIELDS if f.key == "DEEPSEEK_MODEL")

    assert "deepseek-chat" in field.options
    assert "30~50" in field.help


def test_the_settings_panel_covers_every_profile_field() -> None:
    """
    「profile.yaml 全开」是和用户确认过的范围，这里把它变成一条会失败的断言。

    往 `Profile` 加字段而忘了加控件时，界面会安静地少一项——
    而设置面板恰恰是「以为改了其实没改」最容易发生的地方。
    """
    from dna.core.config import Profile
    from frontends.nicegui_app import settings_dialog as sd

    covered = {spec.key for spec in sd.FIELD_SPECS}
    assert covered == set(Profile.model_fields)


def test_duration_windows_are_marked_as_reference_only() -> None:
    """
    时长只是参照，字数才是验收标准（issue 007-C 的续集）。

    两组窗口摆在同一个面板里，不写清楚的话人会去调时长指望产物变长。
    """
    from frontends.nicegui_app import settings_dialog

    source = Path(settings_dialog.__file__).read_text(encoding="utf-8")

    assert "仅参照" in source
    assert "不参与验收" in source


def test_nothing_changed_says_so() -> None:
    """一项都没改时不该报「已保存」——那会让人以为刚才那次编辑生效了。"""
    from frontends.nicegui_app import actions

    assert actions.save_settings({}, {}) == "没有改动"


def test_saving_names_what_needs_a_restart(monkeypatch) -> None:
    """
    代理与日志级别在进程启动时就被读走了。

    不说清楚的话人会以为没保存成功，然后反复点保存——而每次都真的写了文件。
    """
    from dna.core import config_edit
    from frontends.nicegui_app import actions

    monkeypatch.setattr(config_edit, "save_env", lambda updates: sorted(updates))
    monkeypatch.setattr(config_edit, "save_profile", lambda updates: None)

    message = actions.save_settings({}, {"HTTPS_PROXY": "http://x:913"})
    assert "HTTPS_PROXY" in message and "重启" in message

    # 模型换一下不需要重启：说要重启会让人白重启一次
    message = actions.save_settings({}, {"DEEPSEEK_MODEL": "deepseek-chat"})
    assert "重启" not in message


def test_profile_changes_say_the_markers_are_rejudged(monkeypatch) -> None:
    """改完字数窗口，表格里的超长标记会跟着重判——这是「现算不入库」的可见结果。"""
    from dna.core import config_edit
    from frontends.nicegui_app import actions

    monkeypatch.setattr(config_edit, "save_profile", lambda updates: None)

    message = actions.save_settings({"summary_chars": [90, 120]}, {})
    assert "超长标记" in message


# --- 订阅导入 / subscription import --------------------------------------------


def test_source_options_come_from_the_config_not_the_ledger(monkeypatch) -> None:
    """
    选项必须是**配置里启用的源**，不是台账里出现过的源。

    用台账的话，今天新加的源一次也没抓过 → 它不在选项里 → 永远抓不到；
    而已经停用的死源反倒还挂在列表上。筛选栏那个下拉问的是反过来的问题。
    """
    from dna.core import config as config_module
    from dna.core.config import SourceConfig
    from frontends.nicegui_app import actions

    monkeypatch.setattr(
        config_module,
        "load_sources",
        lambda: [SourceConfig(id="brand-new", name="新加的源", url="https://x/feed")],
    )

    assert actions.source_options() == {"brand-new": "新加的源"}


def test_a_broken_sources_file_does_not_break_the_dialog(monkeypatch) -> None:
    """
    sources.yaml 坏了应当是「对话框告诉你没有源」，不是点了按钮界面白屏。

    导入链接那条路还是通的，不该被订阅配置的问题一起拖下去。
    """
    from dna.core import config as config_module
    from dna.core.errors import ConfigError
    from frontends.nicegui_app import actions

    def _boom():
        raise ConfigError("订阅源 id 重复")

    monkeypatch.setattr(config_module, "load_sources", _boom)

    assert actions.source_options() == {}


# --------------------------------------------------------------- TTS 服务状态


def test_tts_status_reports_offline_with_a_reason(monkeypatch) -> None:
    """
    服务连不上时 `online=False` 且 `detail` 写清楚下一步做什么。

    这个芯片存在的唯一理由就是回答「服务到底起没起」——一个只写「离线」
    而不说怎么办的提示，等于把问题原样退回给人。
    """
    import asyncio

    from frontends.nicegui_app import actions

    class _DeadClient:
        def health(self, timeout: float = 3.0) -> bool:
            return False

    settings = Settings(tts_service_url="http://127.0.0.1:8300", tts_autostart=True)
    monkeypatch.setattr(actions, "get_settings", lambda: settings)
    monkeypatch.setattr(
        actions, "get_tts", lambda *a, **k: type("P", (), {"client": _DeadClient()})()
    )

    status = asyncio.run(actions.tts_status())

    assert status.online is False
    assert status.url == "http://127.0.0.1:8300"
    assert status.detail.strip()
    assert "127.0.0.1:8300" in status.detail


def test_tts_status_is_online_when_health_passes(monkeypatch) -> None:
    """健康检查通过就是在线；这一路**不拉起服务**（探活不能有副作用）。"""
    import asyncio

    from frontends.nicegui_app import actions

    class _LiveClient:
        def health(self, timeout: float = 3.0) -> bool:
            return True

    settings = Settings(tts_service_url="http://127.0.0.1:8300")
    monkeypatch.setattr(actions, "get_settings", lambda: settings)
    monkeypatch.setattr(
        actions, "get_tts", lambda *a, **k: type("P", (), {"client": _LiveClient()})()
    )

    assert asyncio.run(actions.tts_status()).online is True


# ------------------------------------------------------------------- 翻页


def _seed_many(settings: Settings, n: int, *, via=SourceKind.RSS) -> None:
    """放 n 篇文章 / Seed n articles."""
    ledger = Ledger(settings.db_file)
    for i in range(n):
        ledger.register(
            RawItem(source_id="s", via=via, url=f"https://e.com/{i}", title=f"第 {i} 篇")
        )


def test_paging_slices_the_same_list(settings: Settings, monkeypatch) -> None:
    """
    第 2 页接着第 1 页往下走，总数与页数由后端算好。

    表格原来写死只取最近 50 条，于是导入一批就把在办的文章顶出可见范围——
    用户看到的「订阅导入把之前的覆盖掉了」其实是这个，数据一直都在。
    """
    from frontends.nicegui_app import actions

    monkeypatch.setattr(actions, "get_settings", lambda: settings)
    _seed_many(settings, 7)

    first = actions.load_rows(page=1, page_size=3)
    second = actions.load_rows(page=2, page_size=3)
    last = actions.load_rows(page=3, page_size=3)

    assert (first.total, first.pages, first.page) == (7, 3, 1)
    assert [len(v.rows) for v in (first, second, last)] == [3, 3, 1]
    ids = [r.article.id for v in (first, second, last) for r in v.rows]
    assert len(set(ids)) == 7, "翻页不该重复或漏掉文章"
    assert first.scanned_cap is False


def test_page_beyond_the_end_is_clamped(settings: Settings, monkeypatch) -> None:
    """删到只剩一页之后停在不存在的第 7 页，应当夹回最后一页而不是显示空表。"""
    from frontends.nicegui_app import actions

    monkeypatch.setattr(actions, "get_settings", lambda: settings)
    _seed_many(settings, 2)

    view = actions.load_rows(page=7, page_size=50)

    assert view.pages == 1
    assert view.page == 1


def test_post_filters_report_when_they_hit_the_scan_cap(
    settings: Settings, monkeypatch
) -> None:
    """
    后置筛选撞到扫描上限要**说出来**。

    一个看起来「筛完了」而其实只筛了一部分的列表，比明说「只筛了最近 N 条」
    危险得多——人会据此以为剩下的都处理过了。
    """
    from frontends.nicegui_app import actions

    monkeypatch.setattr(actions, "get_settings", lambda: settings)
    monkeypatch.setattr(actions, "SCAN_CAP", 3)
    _seed_many(settings, 5)

    assert actions.load_rows(only_new=True).scanned_cap is True
    assert actions.load_rows(only_new=False).scanned_cap is False, "没有后置筛选就没有上限"


# --- 打开文件管理器 / revealing in the file manager -------------------------------


def test_non_windows_never_touches_the_windows_path(tmp_path: Path, monkeypatch) -> None:
    """
    非 Windows 平台不进那条 ctypes 分支。

    顶窗口那套是 `user32` 专有的；在 Linux/macOS 上连碰都不该碰，
    否则一个 `AttributeError` 会把「打开目录」这件必成功的事变成失败。
    """
    from frontends.nicegui_app import actions

    monkeypatch.setattr(actions, "_server_is_local", lambda: True)
    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.setattr(
        actions, "_open_on_windows", lambda _t: pytest.fail("非 Windows 不该走这里")
    )

    calls: list[list[str]] = []
    monkeypatch.setattr(actions.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))

    assert actions.open_in_file_manager(tmp_path) == ""
    assert calls == [["xdg-open", str(tmp_path)]]


def test_windows_opens_a_directory_with_explorer_and_raises_it(
    tmp_path: Path, monkeypatch
) -> None:
    """目录走 explorer，并把顶窗口那步扔到后台线程——它要轮询两秒，不能占着事件循环。"""
    from frontends.nicegui_app import actions

    monkeypatch.setattr(actions, "_server_is_local", lambda: True)
    monkeypatch.setattr(actions.sys, "platform", "win32")

    calls: list[list[str]] = []
    monkeypatch.setattr(actions.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))

    spawned: list[object] = []

    class _FakeThreading:
        @staticmethod
        def Thread(*, target, args, daemon):  # noqa: N802 - 冒充 threading.Thread
            spawned.append((target, args, daemon))
            return type("T", (), {"start": lambda self: None})()

    monkeypatch.setattr(actions, "threading", _FakeThreading)

    assert actions.open_in_file_manager(tmp_path) == ""
    assert calls == [["explorer", os.path.normpath(str(tmp_path))]]
    assert len(spawned) == 1, "顶窗口必须异步做"
    assert spawned[0][0] is actions._raise_explorer
    assert spawned[0][2] is True, "守护线程：顶不上来也不能拖住退出"


def test_raising_the_window_never_breaks_opening_it(tmp_path: Path, monkeypatch) -> None:
    """
    顶窗口失败要静默退回今天的行为。

    窗口其实已经开了，这时候提示「打不开」是**假的**——人会去找一个不存在的故障。
    """
    from frontends.nicegui_app import actions

    monkeypatch.setattr(actions.sys, "platform", "win32")
    actions._raise_explorer(tmp_path / "不存在", timeout=0.0)  # 不抛就算过


# --- 设置面板的字段表 / the settings field tables --------------------------------


def test_every_profile_field_spec_exists_on_the_model() -> None:
    """
    `FIELD_SPECS` 里的每个 key 都必须是 `Profile` 上真有的字段。

    拼错一个 key 会在 `extra="forbid"` 上炸，但那要等到有人点保存的时候；
    这条测试把它提前到改代码的那一刻。
    """
    from dna.core.config import Profile
    from frontends.nicegui_app import settings_dialog

    known = set(Profile.model_fields)
    for spec in settings_dialog.FIELD_SPECS:
        assert spec.key in known, f"{spec.key} 不是 Profile 的字段"


def test_field_specs_have_no_duplicates_and_one_page_each() -> None:
    """一个字段画两遍的话，人会在另一页看到自己刚改过的旧值。"""
    from frontends.nicegui_app import settings_dialog

    keys = [s.key for s in settings_dialog.FIELD_SPECS]
    assert len(keys) == len(set(keys))


def test_cross_checked_duration_keys_are_also_configured() -> None:
    """字数区间要对照的那个时长区间，必须自己也在表里——否则对照的是空气。"""
    from frontends.nicegui_app import settings_dialog

    keys = {s.key for s in settings_dialog.FIELD_SPECS}
    for spec in settings_dialog.FIELD_SPECS:
        if spec.duration_key:
            assert spec.duration_key in keys


def test_every_env_field_maps_to_a_settings_attribute() -> None:
    """
    每个 `EnvField` 都要在 `Settings` 上有同名属性。

    没有的话 `env_display` 只会安静地显示空串，看起来像「这一项没配」——
    人于是把真值填进去，保存后发现还是没反应。
    """
    from dna.core.config import Settings
    from frontends.nicegui_app import actions

    for field in actions.ENV_FIELDS:
        assert field.field in Settings.model_fields, f"{field.key} 在 Settings 上不存在"


def test_every_env_field_is_writable() -> None:
    """不在白名单里的项画出来也是白画：`save_env` 会跳过它，界面却显示保存成功。"""
    from dna.core.config_edit import ENV_ALLOWLIST
    from frontends.nicegui_app import actions

    for field in actions.ENV_FIELDS:
        assert field.key in ENV_ALLOWLIST, f"{field.key} 不在 ENV_ALLOWLIST 里"


def test_env_groups_split_the_tts_page_out_of_the_runtime_page() -> None:
    """TTS 的项只出现在 TTS 页，运行设置页不再重复显示它们。"""
    from frontends.nicegui_app import actions
    from frontends.nicegui_app.settings_dialog import PAGE_RUNTIME, PAGE_TTS

    tts = {f.key for _, fields in actions.env_groups(PAGE_TTS) for f in fields}
    runtime = {f.key for _, fields in actions.env_groups(PAGE_RUNTIME) for f in fields}

    assert "TTS_SERVICE_URL" in tts
    assert "TTS_MODE" in tts
    assert not (tts & runtime), "同一项不能画在两页上"
    assert tts | runtime == {f.key for f in actions.ENV_FIELDS}, "有字段一页都没排上"
    assert all(fields for _, fields in actions.env_groups(PAGE_TTS))


# --- 字数 → 时长的估算 / the character-to-duration estimate ----------------------


class _Box:
    """够 `_estimate` 用的假控件 / A stand-in with just `.value`."""

    def __init__(self, value):
        self.value = value


def test_estimate_matches_the_shared_duration_helper() -> None:
    """
    估算必须走 `seconds_for_units`，不能另写一份换算。

    那里的系数是 issue 009 实测出来的；面板自己再算一遍，显示的秒数
    迟早和验收、和产物记账用的那份漂开。
    """
    from dna.narration.duration import seconds_for_units
    from frontends.nicegui_app import settings_dialog

    spec = next(s for s in settings_dialog.FIELD_SPECS if s.key == "shortvideo_chars")
    text, warn = settings_dialog._estimate(spec, (_Box(110), _Box(160)), None)

    assert f"{seconds_for_units(110):.0f}" in text
    assert f"{seconds_for_units(160):.0f}" in text
    assert warn is False, "没有给时长区间就没有可对照的对象"


def test_estimate_warns_when_the_two_windows_disagree() -> None:
    """两组数字互相矛盾时要说出来——它们现在各配各的，谁也不知道对不对得上。"""
    from frontends.nicegui_app import settings_dialog

    spec = next(s for s in settings_dialog.FIELD_SPECS if s.key == "shortvideo_chars")

    # 110~160 字估约 16~24 秒；配一个 60~90 秒的时长区间，完全不重叠
    _, warn = settings_dialog._estimate(spec, (_Box(110), _Box(160)), (_Box(60), _Box(90)))
    assert warn is True

    # 有重叠就不报：区间只要沾上边就算说得通，卡死会让人为了消警告去改一个不参与验收的数
    _, warn = settings_dialog._estimate(spec, (_Box(110), _Box(160)), (_Box(20), _Box(35)))
    assert warn is False


def test_estimate_survives_an_empty_box() -> None:
    """改到一半清空输入框是常态，这一刻不能抛异常把整个面板打挂。"""
    from frontends.nicegui_app import settings_dialog

    spec = next(s for s in settings_dialog.FIELD_SPECS if s.key == "narration_chars")
    text, _ = settings_dialog._estimate(spec, (_Box(None), _Box("")), None)
    assert "秒" in text


# --- TTS 操作台的取值逻辑 / the console's value logic ---------------------------


def _controls(mode: str, ref: str, base, *, instruct: str = "", x_vector_only=False):
    """一组只装了假控件的声音配置 / Voice controls wired to stand-ins.

    `VoiceControls.__init__` 不建任何界面元素（元素在 `render()` 里建），所以取值逻辑
    可以在没有事件循环的地方测——这也是把它单独拆一个模块的理由之一。
    """
    from frontends.nicegui_app.voice_controls import VoiceControls

    controls = VoiceControls(voices=[], base=base)
    controls.mode_box = _Box(mode)
    controls.ref_box = _Box(ref)
    controls.voice_box = _Box(base.speaker)
    controls.instruct_box = _Box(instruct)
    controls.xvec_box = _Box(x_vector_only)
    return controls


def _console(controls, texts: list[str], base, *, role: str = "narrator"):
    """一个只装了假文本框的操作台 / A console wired to stand-in textareas."""
    from dna.produce import ProductionKind
    from frontends.nicegui_app import tts_panel

    panel = tts_panel._Panel(None, ProductionKind.NARRATION_AUDIO, "zh")
    panel.shared = controls
    panel.uniform_box = _Box(True)
    panel.pieces = [
        tts_panel._Piece(base=base, role=role, text_box=_Box(text), number=i)
        for i, text in enumerate(texts, start=1)
    ]
    return panel


def test_builtin_mode_drops_the_reference_audio() -> None:
    """
    内置音色下参考音频必须清空。

    服务端把「同时给 ref_audio 和音色名」当成互相冲突的参数**直接报错**，
    所以切回内置音色时不能把上一次的克隆参数留在 VoiceSpec 里。
    """
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app import voice_controls

    base = VoiceSpec(speaker="/data/ref_audio/host.wav", mode="voice_clone",
                     ref_audio="/data/ref_audio/host.wav", ref_text="原话")
    controls = _controls(voice_controls.MODE_BUILTIN, "", base)
    controls.voice_box.value = "Serena"

    voice = controls.spec()

    assert voice.mode == "custom_voice"
    assert voice.speaker == "Serena"
    assert voice.ref_audio == ""
    assert voice.ref_text == ""


def test_switching_the_reference_clip_drops_the_transcript(tmp_path, monkeypatch) -> None:
    """
    **换了参考音频就不能再带原来的参考文本。**

    参考文本只对配置里那一条音频成立。带着它去克隆另一个文件，上游会按 ICL 模式
    对齐一段根本不是这个音频说的话，克隆质量明显变差；没有原话时走纯 x-vector
    反而是对的（`x_vector_only`）。
    The transcript belongs to one clip only; carrying it over makes ICL align against
    words never spoken in the new file.
    """
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app import actions, voice_controls

    other = tmp_path / "guest.wav"
    other.write_bytes(b"RIFF")
    monkeypatch.setattr(actions, "ref_audio_file", lambda raw: other)

    base = VoiceSpec(speaker="/data/ref_audio/host.wav", mode="voice_clone",
                     ref_audio="/data/ref_audio/host.wav", ref_text="原话")
    controls = _controls(voice_controls.MODE_CLONE, "ref_audio/guest.wav", base)

    voice = controls.spec()

    assert voice.ref_audio == str(other)
    assert voice.speaker == str(other)   # 克隆没有音色名，放路径（与 factory 一致）
    assert voice.ref_text == ""
    assert voice.x_vector_only is True


def test_keeping_the_configured_clip_keeps_its_transcript(tmp_path, monkeypatch) -> None:
    """没换音频时参考文本要留着——有原话的克隆质量明显更好。"""
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app import actions, voice_controls

    clip = tmp_path / "host.wav"
    clip.write_bytes(b"RIFF")
    monkeypatch.setattr(actions, "ref_audio_file", lambda raw: clip)

    base = VoiceSpec(speaker=str(clip), mode="voice_clone",
                     ref_audio=str(clip), ref_text="原话")
    controls = _controls(voice_controls.MODE_CLONE, "ref_audio/host.wav", base)

    voice = controls.spec()

    assert voice.ref_text == "原话"
    assert voice.x_vector_only is False


def test_unresolvable_reference_falls_back_to_the_original_voice(monkeypatch) -> None:
    """
    参考音频解析不到时不送空路径去合成。

    送空路径的结果是服务端按内置音色念完几分钟，而没人知道换了嗓子。
    """
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app import actions, voice_controls

    monkeypatch.setattr(actions, "ref_audio_file", lambda raw: None)
    base = VoiceSpec(speaker="Serena")
    controls = _controls(voice_controls.MODE_CLONE, "ref_audio/nope.wav", base)

    assert controls.spec() == base


def test_the_panel_keeps_the_text_and_role_of_every_piece() -> None:
    """
    面板里改过的正文原样送去合成，角色不变。

    正文**可编辑**（2026-09-10 用户要求：校对就在这里做），所以这条断言的是
    「人改的字真的进了合成」——不是「文本没被动过」。
    """
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app import voice_controls

    base = VoiceSpec(speaker="Serena")
    controls = _controls(voice_controls.MODE_BUILTIN, "", base)
    panel = _console(controls, ["改过的第一句。", "第二句。"], base)
    panel.pieces[1].role = "guest"

    segments, _ = panel.plan()

    assert [s.text for s in segments] == ["改过的第一句。", "第二句。"]
    assert [s.role for s in segments] == ["narrator", "guest"]


# --- 换种子重掷 / re-rolling the seed ------------------------------------------


def test_the_first_take_sends_no_seed() -> None:
    """
    **没重掷过就不带种子。**

    带一个自己算的种子会让「面板里生成的第一版」和「自动合成出来的那版」不一样，
    而人此时并没有要求换一版。
    """
    from frontends.nicegui_app.voice_controls import reroll_seed

    assert reroll_seed(3, 0) is None


def test_rerolls_diverge_and_stay_reproducible() -> None:
    """
    两次重掷要落在不同的种子上，而同一个 `(段号, 次数)` 必须稳定复现。

    不稳定的话，人听到满意的一版之后按「全部合成并保存」拿到的是第三版。
    同一次重掷里各段的种子也必须不同：否则整篇的采样偏好一起变，像换了个人。
    """
    from frontends.nicegui_app.voice_controls import reroll_seed

    assert reroll_seed(1, 1) != reroll_seed(1, 2)
    assert reroll_seed(1, 1) == reroll_seed(1, 1)
    assert reroll_seed(1, 1) != reroll_seed(2, 1)


# --- 复用已生成的段 / reusing the rendered pieces -------------------------------


def _ready(panel, wav: bytes = b"RIFFfake") -> None:
    """把所有段标成「已生成」/ Mark every piece as rendered."""
    for piece in panel.pieces:
        piece.wav = wav
        piece.rendered_text = piece.text()
        piece.rendered_voice = panel.voice_for(piece)


def test_untouched_pieces_are_reused() -> None:
    """生成过又没动过的段直接复用——RTF≈2.5，整篇重跑要几分钟。"""
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app import voice_controls

    base = VoiceSpec(speaker="Serena")
    panel = _console(_controls(voice_controls.MODE_BUILTIN, "", base), ["一。", "二。"], base)
    _ready(panel)

    segments, rendered = panel.plan()

    assert len(segments) == 2
    assert set(rendered) == {0, 1}


def test_an_edited_piece_is_not_reused() -> None:
    """
    **改过字的段不能复用。**

    复用的话会拿旧波形去拼新文本，落盘的音频与稿子对不上——而这件事人只有
    整篇听完才会发现。
    """
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app import tts_panel, voice_controls

    base = VoiceSpec(speaker="Serena")
    panel = _console(_controls(voice_controls.MODE_BUILTIN, "", base), ["一。", "二。"], base)
    _ready(panel)
    panel.pieces[0].text_box.value = "一，改过了。"

    segments, rendered = panel.plan()

    assert panel.state(panel.pieces[0]) == tts_panel.STATE_STALE
    assert set(rendered) == {1}
    assert segments[0].text == "一，改过了。"


def test_changing_the_voice_invalidates_the_cache() -> None:
    """换了音色也要重合成——文本没变，但念它的嗓子变了。"""
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app import voice_controls

    base = VoiceSpec(speaker="Serena")
    controls = _controls(voice_controls.MODE_BUILTIN, "", base)
    panel = _console(controls, ["一。", "二。"], base)
    _ready(panel)
    controls.voice_box.value = "Uncle_Fu"

    _, rendered = panel.plan()

    assert rendered == {}


def test_blank_pieces_drop_out_and_the_reuse_indices_follow() -> None:
    """
    **空段丢掉之后，复用的下标要跟着往前挪。**

    后端也是先滤空段再逐段编号，两边错一位的表现是「某一段的音频跑到了别的段上」，
    整篇听起来像乱序——而每一段单独听都是好的，所以极难查。
    """
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app import voice_controls

    base = VoiceSpec(speaker="Serena")
    panel = _console(_controls(voice_controls.MODE_BUILTIN, "", base),
                     ["一。", "   ", "三。"], base)
    _ready(panel)

    segments, rendered = panel.plan()

    assert [s.text for s in segments] == ["一。", "三。"]
    assert rendered == {0: b"RIFFfake", 1: b"RIFFfake"}


def test_the_script_is_joined_back_without_separators() -> None:
    """
    写回稿子的文本是各段原样拼起来的。

    中文之间插空格会让写回去的稿子每段之间多一个空格，下一次拆条又把它带上，
    几轮之后正文里全是空隙。
    """
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app import voice_controls

    base = VoiceSpec(speaker="Serena")
    panel = _console(_controls(voice_controls.MODE_BUILTIN, "", base),
                     ["第一句。", " ", "第二句。"], base)

    assert panel.script_text() == "第一句。第二句。"


# --- 下拉框的初值 / the initial value of the two selects ------------------------


def test_the_voice_select_never_takes_a_clone_path_as_its_value() -> None:
    """
    **克隆配置下打开面板不能崩。**

    `.env` 配了克隆音色时 `base.speaker` 是一条 wav 的绝对路径（`_clone_voice` 的写法），
    而音色下拉的选项是服务端给的音色名。把路径当初值塞进 `ui.select`，NiceGUI 在
    构造时就抛 `ValueError: Invalid value: C:\\…\\qwen3-tts-cpu.wav`，整个操作台打不开
    ——用户实测踩到的就是这个。
    """
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app.voice_controls import voice_choice

    clip = r"C:\Users\test\Downloads\xkd\DailyNewsAssistant\data\ref_audio\qwen3-tts-cpu.wav"
    base = VoiceSpec(speaker=clip, mode="voice_clone", ref_audio=clip)

    options, value = voice_choice(["Serena", "Ethan"], base)

    assert clip not in options
    assert value in options


def test_an_offline_service_leaves_the_voice_select_empty_not_broken() -> None:
    """音色表取不到时初值必须是空的——空字符串同样不是合法初值，只有 None 能过。"""
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app.voice_controls import voice_choice

    options, value = voice_choice([], VoiceSpec(speaker="Serena"))
    assert (options, value) == (["Serena"], "Serena")

    options, value = voice_choice([], VoiceSpec(speaker="/x/clip.wav", mode="voice_clone"))
    assert (options, value) == ([], "")


def test_the_reference_select_folds_an_absolute_path_back_to_its_option() -> None:
    """
    配置解析完是绝对路径，下拉选项是相对路径——两种形式混在一个下拉里同样会
    撞上 `Invalid value`。能对上的折回相对形式，人看到的就是自己在 `.env` 里写的那条。
    """
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app.voice_controls import ref_choice

    absolute = r"C:\Users\test\xkd\DailyNewsAssistant\data\ref_audio\qwen3-tts-cpu.wav"
    options, value = ref_choice(
        ["ref_audio/qwen3-tts-cpu.wav", "ref_audio/other.wav"],
        VoiceSpec(speaker=absolute, mode="voice_clone", ref_audio=absolute),
    )

    assert value == "ref_audio/qwen3-tts-cpu.wav"
    assert options == ["ref_audio/qwen3-tts-cpu.wav", "ref_audio/other.wav"]


def test_a_reference_clip_from_elsewhere_is_listed_as_is() -> None:
    """`data/ref_audio/` 之外的文件不能被同名的那条悄悄换掉，只能原样列出来。"""
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app.voice_controls import ref_choice

    outside = r"D:\voices\qwen3-tts-cpu.wav"
    options, value = ref_choice(
        ["ref_audio/qwen3-tts-cpu.wav"],
        VoiceSpec(speaker=outside, mode="voice_clone", ref_audio=outside),
    )

    assert value == outside
    assert options == ["ref_audio/qwen3-tts-cpu.wav", outside]


# --- 音色设计 / voice design ----------------------------------------------------


def test_voice_design_carries_only_the_description() -> None:
    """
    音色设计只吃 `instruct`：服务端要求有描述，且 `mode=voice_design` **不能带
    ref_audio**（`SynthRequest._check_consistency` 直接报错）。
    """
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app import voice_controls

    base = VoiceSpec(speaker="/data/ref_audio/host.wav", mode="voice_clone",
                     ref_audio="/data/ref_audio/host.wav", ref_text="原话")
    controls = _controls(voice_controls.MODE_DESIGN, "ref_audio/host.wav", base,
                         instruct="沉稳的中年男声，播音腔")

    voice = controls.spec()

    assert voice.mode == "voice_design"
    assert voice.instruct == "沉稳的中年男声，播音腔"
    assert (voice.speaker, voice.ref_audio, voice.ref_text) == ("", "", "")
    assert voice.x_vector_only is False


def test_leaving_clone_does_not_reuse_the_clip_path_as_a_voice_name() -> None:
    """
    从克隆切回内置音色、又没挑音色时**不能拿 `base.speaker` 兜底**：
    那里放的是 wav 路径，送去合成会被当成一个不存在的音色名。
    """
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app import voice_controls

    base = VoiceSpec(speaker="/data/ref_audio/host.wav", mode="voice_clone",
                     ref_audio="/data/ref_audio/host.wav")
    controls = _controls(voice_controls.MODE_BUILTIN, "", base)
    controls.voice_box.value = ""

    assert controls.spec().speaker == ""


def test_a_missing_required_field_is_named_before_synthesis() -> None:
    """
    三种模式各有一条服务端会当场拒掉的前置条件。合成一段要等几十秒、整篇要几分钟，
    所以缺什么必须在点下去之前就说出来。
    """
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app.voice_controls import voice_problem

    assert "音色描述" in (voice_problem(VoiceSpec(speaker="", mode="voice_design")) or "")
    assert voice_problem(VoiceSpec(speaker="", mode="voice_design", instruct="播音腔")) is None

    assert "参考音频" in (voice_problem(VoiceSpec(speaker="", mode="voice_clone")) or "")
    assert voice_problem(VoiceSpec(speaker="/x/a.wav", mode="voice_clone", ref_audio="/x/a.wav")) is None

    assert "音色" in (voice_problem(VoiceSpec(speaker="")) or "")
    assert voice_problem(VoiceSpec(speaker="Serena")) is None
