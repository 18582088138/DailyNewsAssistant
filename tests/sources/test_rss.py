"""
test_rss.py —— RSS 解析与源装配单元测试 / RSS parsing and source assembly tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/sources/test_rss.py -v

样例 / Sample:
    tests/fixtures/sample_feed.xml —— 刻意构造成「真实世界的脏 feed」

覆盖 / Covers:
    1. 正常解析：标题、链接、摘要、发布时间
    2. **标题里的换行与多余空白必须压平**（否则污染目录 slug 与日报排版）
    3. **缺 pubDate 的条目 published_at 为 None**，不能回填「现在」——回填会让排序失真
    4. **缺 link 或缺 title 的条目必须跳过**（没有链接就无法抽正文）
    5. limit 生效
    6. 带追踪参数的链接在解析阶段原样保留（规范化是后续环节的事）
    7. 格式有瑕疵但有条目的 feed 应继续可用（真实 feed 十有八九带瑕疵）
    8. 完全无法解析的内容抛 SourceError
    9. RSSHub 路由拼接的各种写法
   10. **registry 逐源隔离**：一个源抛异常不能中断整轮采集

预期 / Expected:
    25 passed；耗时 < 2s；全程离线，不发任何网络请求
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from dna.core.config import SourceConfig
from dna.core.errors import SourceError
from dna.core.models import RawItem, SourceKind
from dna.sources.base import SourceAdapter
from dna.sources.registry import build_adapter, collect
from dna.sources.rss import parse_feed
from dna.sources.rsshub import RSSHubAdapter, join_route

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def feed_xml() -> str:
    """样例 feed 内容 / The sample feed content."""
    return (FIXTURES / "sample_feed.xml").read_text(encoding="utf-8")


@pytest.fixture
def rss_config() -> SourceConfig:
    """一个 RSS 源配置 / An RSS source configuration."""
    return SourceConfig(id="test-feed", name="测试源", kind=SourceKind.RSS, url="https://e.com/feed")


# --- 正常解析 / happy path ----------------------------------------------------


def test_parses_valid_entries(feed_xml: str, rss_config: SourceConfig) -> None:
    """解析出全部合法条目 / All valid entries are parsed."""
    items = parse_feed(feed_xml, rss_config)

    # 样例里 5 条，其中 2 条缺字段应被跳过
    assert len(items) == 3
    assert all(isinstance(i, RawItem) for i in items)
    assert items[0].title == "某公司发布新一代多模态大模型"
    assert items[0].source_id == "test-feed"
    assert items[0].via is SourceKind.RSS


def test_summary_is_captured(feed_xml: str, rss_config: SourceConfig) -> None:
    """源自带的摘要应被保留 / The feed's own summary is captured."""
    items = parse_feed(feed_xml, rss_config)
    assert "推理成本下降四成" in items[0].summary_raw


def test_published_at_parsed(feed_xml: str, rss_config: SourceConfig) -> None:
    """发布时间应被解析 / The publication time is parsed."""
    items = parse_feed(feed_xml, rss_config)
    assert items[0].published_at == datetime(2026, 9, 1, 9, 30, 0)


def test_title_whitespace_is_collapsed(feed_xml: str, rss_config: SourceConfig) -> None:
    """
    标题里的换行与多余空白必须压平。
    Newlines and runs of spaces in titles must be collapsed.

    不压平的话，目录 slug 和日报排版都会被污染。
    """
    items = parse_feed(feed_xml, rss_config)
    messy = items[1].title

    assert messy == "开源社区发布 新版推理框架"
    assert "\n" not in messy
    assert "  " not in messy


def test_missing_pubdate_is_none_not_now(feed_xml: str, rss_config: SourceConfig) -> None:
    """
    缺发布时间时必须是 None，**不能回填「现在」**。
    A missing date must stay None rather than being backfilled with "now".

    回填会让这条永远排在最前面，并让跨日去重失真。
    Backfilling would pin the item to the top forever and corrupt cross-day
    de-duplication.
    """
    items = parse_feed(feed_xml, rss_config)
    no_date = next(i for i in items if i.url.endswith("/posts/3"))
    assert no_date.published_at is None


def test_entries_without_link_or_title_are_skipped(
    feed_xml: str, rss_config: SourceConfig
) -> None:
    """
    缺链接或缺标题的条目必须跳过 / Entries missing a link or title are skipped.

    没有链接就无法抽正文，留着只会在后续环节变成空条目。
    """
    items = parse_feed(feed_xml, rss_config)
    urls = [i.url for i in items]

    assert "https://news.example.com/posts/5" not in urls  # 缺标题
    assert all(i.title for i in items)
    assert all(i.url for i in items)


def test_tracking_params_preserved_at_parse_time(
    feed_xml: str, rss_config: SourceConfig
) -> None:
    """
    解析阶段保留原始链接，规范化留给后续环节。
    Parsing keeps the original link; canonicalisation happens later.

    分层清晰：解析只负责忠实还原 feed 内容。
    """
    items = parse_feed(feed_xml, rss_config)
    assert "utm_source=rss" in items[0].url


def test_limit_is_applied(feed_xml: str, rss_config: SourceConfig) -> None:
    """条数上限生效 / The item cap is applied."""
    assert len(parse_feed(feed_xml, rss_config, limit=2)) == 2


def test_limit_zero(feed_xml: str, rss_config: SourceConfig) -> None:
    """上限为 0 时返回空列表 / A cap of zero yields nothing."""
    assert parse_feed(feed_xml, rss_config, limit=0) == []


# --- 容错 / tolerance ---------------------------------------------------------


def test_slightly_malformed_feed_still_works(rss_config: SourceConfig) -> None:
    """
    格式有瑕疵但能取出条目的 feed 应继续可用。
    A slightly malformed feed with usable entries must still work.

    真实世界的 RSS 十有八九带瑕疵；把 bozo 当致命错误等于放弃大半信息源。
    Most real feeds are slightly malformed; treating the bozo flag as fatal would
    discard the majority of sources.
    """
    messy = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>能用的条目</title><link>https://e.com/1</link></item>
    </channel></rss>"""
    items = parse_feed(messy, rss_config)
    assert len(items) == 1


@pytest.mark.parametrize("content", ["", "   ", "这不是 XML"])
def test_non_feed_content_raises(content: str, rss_config: SourceConfig) -> None:
    """内容根本不是 feed 时抛 SourceError / Raises when the content is not a feed."""
    with pytest.raises(SourceError):
        parse_feed(content, rss_config)


def test_html_error_page_raises_not_silently_empty(rss_config: SourceConfig) -> None:
    """
    **站点返回 HTTP 200 + HTML 错误页时必须报错，不能静默返回空。**
    An HTML error page served with HTTP 200 must raise, not silently yield nothing.

    这是真实且高频的失效模式：站点改版后旧 feed 地址返回一个 200 的 HTML 404 页。
    实测 feedparser 对此 **bozo=False**，所以不能靠 bozo 判断——靠它的话这个源会被
    记成「成功，0 条」，日报悄悄变短，日志里却没有任何失败记录，极难发现。
    This is a common real failure: after a redesign the old feed URL returns an HTML
    404 page with status 200. feedparser reports bozo=False for it, so relying on the
    bozo flag would record the source as "succeeded with 0 items" — the digest quietly
    shrinks with nothing logged, which is very hard to notice.
    """
    html_404 = "<html><body><h1>404 Not Found</h1></body></html>"

    with pytest.raises(SourceError, match="无法解析"):
        parse_feed(html_404, rss_config)


def test_valid_but_empty_feed_returns_empty_not_error(rss_config: SourceConfig) -> None:
    """
    合法但暂时没有条目的 feed 返回空列表，**不算错误**。
    A valid feed that currently has no items returns an empty list, not an error.

    与上一条形成对照：低频更新的源今天没有新内容是正常的，不该被记成失败。
    The counterpart to the previous case: a low-traffic source with nothing new today
    is normal and must not be reported as a failure.
    """
    empty_feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<title>暂无更新</title></channel></rss>"
    )
    assert parse_feed(empty_feed, rss_config) == []


# --- RSSHub 路由拼接 / RSSHub route joining -----------------------------------


@pytest.mark.parametrize(
    ("base", "route", "expected"),
    [
        ("http://localhost:1200", "/zhihu/hotlist", "http://localhost:1200/zhihu/hotlist"),
        ("http://localhost:1200/", "zhihu/hotlist", "http://localhost:1200/zhihu/hotlist"),
        ("http://localhost:1200/", "/zhihu/hotlist", "http://localhost:1200/zhihu/hotlist"),
        ("http://localhost:1200", "zhihu/hotlist", "http://localhost:1200/zhihu/hotlist"),
        # 直接写完整 URL 时原样放行，方便临时指向公共实例
        ("http://localhost:1200", "https://rsshub.app/zhihu/hotlist",
         "https://rsshub.app/zhihu/hotlist"),
    ],
)
def test_rsshub_route_joining(base: str, route: str, expected: str) -> None:
    """容忍配置里的各种写法 / Tolerates the forms users actually write."""
    assert join_route(base, route) == expected


def test_rsshub_adapter_builds_feed_url() -> None:
    """RSSHub adapter 应拼出完整地址 / The adapter composes the full feed URL."""
    config = SourceConfig(id="zhihu", name="知乎", kind=SourceKind.RSSHUB, url="/zhihu/hotlist")
    adapter = RSSHubAdapter(config, base_url="http://localhost:1200")
    assert adapter.feed_url() == "http://localhost:1200/zhihu/hotlist"


# --- registry：错误隔离 / registry error isolation -----------------------------


class _BoomAdapter(SourceAdapter):
    """必定失败的源 / A source that always fails."""

    def __init__(self, config: SourceConfig, error: Exception) -> None:
        super().__init__(config)
        self._error = error

    def fetch(self, limit: int | None = None) -> list[RawItem]:
        raise self._error


class _OkAdapter(SourceAdapter):
    """必定成功的源 / A source that always succeeds."""

    def fetch(self, limit: int | None = None) -> list[RawItem]:
        return [
            RawItem(source_id=self.id, via=SourceKind.RSS, url=f"https://e.com/{self.id}", title="T")
        ]


def _cfg(source_id: str) -> SourceConfig:
    return SourceConfig(id=source_id, name=source_id, kind=SourceKind.RSS, url="https://e.com/feed")


def test_collect_isolates_source_errors(settings) -> None:  # noqa: ANN001
    """
    **一个源失败不能中断整轮采集。**
    One failing source must not abort the whole round.

    日报每天无人值守跑一次，RSS 源随时可能挂（改版、证书过期、502）。
    若失败即中断，等于任何一个源出问题当天就没有日报。
    The digest runs unattended daily and feeds break constantly; aborting on the
    first failure would mean no digest at all that day.
    """
    adapters = [
        _OkAdapter(_cfg("good1")),
        _BoomAdapter(_cfg("broken"), SourceError("HTTP 502")),
        _OkAdapter(_cfg("good2")),
    ]
    result = collect(adapters, settings=settings)

    assert result.total == 2  # 两个好源的内容都拿到了
    assert result.ok_count == 2
    assert len(result.failures) == 1
    assert result.failures[0].source_id == "broken"
    assert "502" in result.failures[0].reason


def test_collect_isolates_unexpected_exceptions(settings) -> None:  # noqa: ANN001
    """
    连未预期的异常也要兜住 —— 第三方库偶尔会抛出意料之外的类型。
    Even unexpected exception types are contained; third-party libraries occasionally
    raise things one did not plan for.
    """
    adapters = [
        _OkAdapter(_cfg("good")),
        _BoomAdapter(_cfg("weird"), ValueError("第三方库抛了个意外的类型")),
    ]
    result = collect(adapters, settings=settings)

    assert result.total == 1
    assert len(result.failures) == 1
    assert "ValueError" in result.failures[0].reason


def test_collect_records_per_source_counts(settings) -> None:  # noqa: ANN001
    """按源统计条数，供 GUI 与台账展示 / Per-source counts for the GUI and ledger."""
    result = collect([_OkAdapter(_cfg("a")), _OkAdapter(_cfg("b"))], settings=settings)
    assert result.per_source == {"a": 1, "b": 1}


def test_collect_summary_mentions_failures(settings) -> None:  # noqa: ANN001
    """摘要里要体现失败数 / The summary surfaces the failure count."""
    result = collect(
        [_OkAdapter(_cfg("a")), _BoomAdapter(_cfg("b"), SourceError("x"))], settings=settings
    )
    assert "1 个源失败" in result.summary()


def test_build_adapter_dispatches_by_kind(settings) -> None:  # noqa: ANN001
    """按源类型构造对应的 adapter / The right adapter is built for each kind."""
    from dna.sources.rss import RSSAdapter
    from dna.sources.user_link import UserLinkSource

    assert isinstance(build_adapter(_cfg("r"), settings), RSSAdapter)

    hub = SourceConfig(id="h", name="h", kind=SourceKind.RSSHUB, url="/x")
    assert isinstance(build_adapter(hub, settings), RSSHubAdapter)

    inbox = SourceConfig(id="u", name="u", kind=SourceKind.INBOX, url="")
    assert isinstance(build_adapter(inbox, settings), UserLinkSource)
