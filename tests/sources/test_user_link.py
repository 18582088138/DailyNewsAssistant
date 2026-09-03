"""
test_user_link.py —— 用户投递链接单元测试 / User link submission tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/sources/test_user_link.py -v

覆盖 / Covers:
    1. 从一段中文文本里提取链接并生成 RawItem
    2. 一条消息里多个链接 → 多个条目，保持顺序
    3. **按规范化 URL 去重**：同一篇文章的不同分享链接（追踪参数不同）只留一条
    4. 标题留空，交给抽取层补全（此时还没抓过页面）
    5. via 标记正确（GUI 手动粘贴 vs 飞书投递）
    6. 无链接文本 / 空文本 → 空列表，不报错
    7. UserLinkSource：submit 累积、跨批次去重、fetch 上限、clear

为什么按规范化 URL 去重 / Why canonical de-duplication here:
    用户在手机上很容易把同一篇文章发两次——一次从微信分享（带 from=groupmessage），
    一次从浏览器复制（带 utm_source）。按原始字符串比对会当成两条，日报里就重复了。
    The same article is easily submitted twice from a phone: once via a WeChat share
    (carrying from=groupmessage) and once copied from a browser (carrying utm_source).
    A raw string comparison would treat them as two items and duplicate the digest entry.

预期 / Expected:
    16 passed；耗时 < 1s；纯内存操作，无网络请求
"""

from __future__ import annotations

from datetime import datetime

from dna.core.models import SourceKind
from dna.sources.user_link import USER_SOURCE_ID, UserLinkSource, items_from_text


# --- 文本 → 条目 / text to items ----------------------------------------------


def test_single_link_from_chinese_text() -> None:
    """从中文句子里提取链接 / Extract a link from Chinese prose."""
    items = items_from_text("这篇不错 https://news.example.com/a 值得一看")

    assert len(items) == 1
    assert items[0].url == "https://news.example.com/a"
    assert items[0].source_id == USER_SOURCE_ID


def test_multiple_links_keep_order() -> None:
    """多个链接按出现顺序 / Multiple links keep their order."""
    items = items_from_text("先看 https://a.com/1 再看 https://b.com/2")
    assert [i.url for i in items] == ["https://a.com/1", "https://b.com/2"]


def test_title_left_empty_for_extraction_layer() -> None:
    """
    标题留空 —— 此时还没抓过页面，标题由抽取层补全。
    The title is left empty: the page has not been fetched yet, so the extraction
    layer fills it in.
    """
    items = items_from_text("https://a.com/x")
    assert items[0].title == ""


def test_duplicate_share_links_are_merged() -> None:
    """
    同一篇文章的不同分享链接只保留一条。
    Different share links to the same article collapse into one item.

    这是真实场景：微信分享带 from=groupmessage，浏览器复制带 utm_source，
    按原始字符串比对会重复。
    """
    text = (
        "https://news.example.com/a?from=groupmessage 和 "
        "https://news.example.com/a?utm_source=browser"
    )
    items = items_from_text(text)
    assert len(items) == 1


def test_exact_duplicates_merged() -> None:
    """完全相同的链接只留一条 / Identical links collapse."""
    assert len(items_from_text("https://a.com/x https://a.com/x")) == 1


def test_different_articles_not_merged() -> None:
    """不同文章不能被合并 / Distinct articles must not be merged."""
    assert len(items_from_text("https://a.com/1 https://a.com/2")) == 2


def test_via_marks_submission_channel() -> None:
    """via 应标明投递渠道 / via records the submission channel."""
    gui = items_from_text("https://a.com/x", via=SourceKind.GUI)
    inbox = items_from_text("https://a.com/x", via=SourceKind.INBOX)

    assert gui[0].via is SourceKind.GUI
    assert inbox[0].via is SourceKind.INBOX


def test_submitted_at_is_recorded() -> None:
    """投递时间可指定 / The submission time can be supplied."""
    moment = datetime(2026, 9, 1, 8, 30)
    items = items_from_text("https://a.com/x", submitted_at=moment)
    assert items[0].fetched_at == moment


def test_text_without_links() -> None:
    """没有链接时返回空列表，不报错 / Text without links yields an empty list."""
    assert items_from_text("今天天气不错，没有链接") == []


def test_empty_text() -> None:
    """空文本安全 / Empty text is safe."""
    assert items_from_text("") == []


# --- UserLinkSource ----------------------------------------------------------


def test_submit_accumulates() -> None:
    """多次投递会累积 / Successive submissions accumulate."""
    source = UserLinkSource()
    source.submit("https://a.com/1")
    source.submit("https://b.com/2")

    assert len(source) == 2
    assert len(source.fetch()) == 2


def test_submit_returns_only_new_items() -> None:
    """submit 返回本次新增的条目 / submit returns only what it added."""
    source = UserLinkSource()
    source.submit("https://a.com/1")

    added = source.submit("https://a.com/1 https://b.com/2")
    assert [i.url for i in added] == ["https://b.com/2"]


def test_cross_batch_deduplication() -> None:
    """
    跨批次去重 —— 用户隔几小时又发了同一条链接，不应重复入库。
    De-duplication across submissions: re-sending the same link hours later must not
    create a second item.
    """
    source = UserLinkSource()
    source.submit("https://a.com/x")
    source.submit("https://a.com/x")

    assert len(source) == 1


def test_fetch_respects_limit() -> None:
    """fetch 遵守条数上限 / fetch honours the item cap."""
    source = UserLinkSource()
    source.submit("https://a.com/1 https://a.com/2 https://a.com/3")
    assert len(source.fetch(limit=2)) == 2


def test_clear() -> None:
    """清空后归零 / Clearing empties the source."""
    source = UserLinkSource()
    source.submit("https://a.com/x")
    source.clear()
    assert len(source) == 0


def test_default_config_is_usable() -> None:
    """默认配置可直接使用 / The default configuration works out of the box."""
    source = UserLinkSource()
    assert source.id == USER_SOURCE_ID
    assert source.name == "用户投递"
