"""
test_ledger.py —— 文章台账单元测试 / Article ledger unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/store/test_ledger.py -v

对应的人工验证 / Matching manual check:
    dna list        查看总表
    dna stats       查看统计
    dna show <id>   查看单篇详情

覆盖 / Covers:
    1. 建库与表结构初始化；重复打开不报错
    2. register：首次登记返回 is_new=True，再次登记返回 False（**跨日去重的落点**）
    3. register：**按规范化 URL 判重**——带不同追踪参数的同一篇算一篇
    4. register：用户投递的链接初始无标题，后续补上标题
    5. record_fetch：extraction_ok=True → ok，False → **degraded 而非 failed**
    6. record_fetch：fetch_count 累加，重抓时清除上次的 error
    7. record_failure：状态与错误信息落库，错误信息截断
    8. list：按状态 / 来源 / 关键词筛选，按首次出现时间倒序
    9. count_by_status / count_by_source / total
   10. find_by_url：按规范化 URL 反查
   11. pending_ids：取出待抓正文的文章

为什么台账要能跨日去重 / Why cross-day de-duplication matters here:
    同一篇文章会连续几天出现在 feed 里。没有台账就会天天重抓、天天进日报。
    An article stays in a feed for days. Without the ledger it would be re-fetched and
    re-published every single day.

预期 / Expected:
    21 passed；耗时 < 2s；只在 pytest 的 tmp_path 下建库，不碰真实 data/
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from dna.core.models import Article, MediaAsset, MediaKind, RawItem, SourceKind
from dna.store.ledger import FetchStatus, Ledger

NOW = datetime(2026, 9, 2, 10, 0, 0)


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    """指向临时目录的台账 / A ledger backed by a temp directory."""
    return Ledger(tmp_path / "dna.db")


def raw(url: str = "https://e.com/a", title: str = "标题", source: str = "qbitai") -> RawItem:
    """构造一条采集条目 / Build a collected item."""
    return RawItem(source_id=source, via=SourceKind.RSS, url=url, title=title)


def article(text: str = "正文" * 100, ok: bool = True, images: int = 0) -> Article:
    """构造一篇抽取结果 / Build an extraction result."""
    return Article(
        url="https://e.com/a",
        title="抽取后的标题",
        text=text,
        author="张三",
        media=[
            MediaAsset(kind=MediaKind.IMAGE, url=f"https://e.com/{i}.jpg", source_url="https://e.com/a")
            for i in range(images)
        ],
        extraction_ok=ok,
    )


# --- 建库 / schema ------------------------------------------------------------


def test_database_is_created_on_first_use(tmp_path: Path) -> None:
    """首次使用时自动建库建表 / The database and schema are created on first use."""
    db_path = tmp_path / "nested" / "dna.db"
    Ledger(db_path).total()
    assert db_path.exists()


def test_reopening_is_safe(ledger: Ledger) -> None:
    """重复打开不报错、不重置数据 / Reopening is safe and preserves data."""
    ledger.register(raw())
    assert Ledger(ledger.db_path).total() == 1


# --- 登记与跨日去重 / registration and cross-day de-duplication ----------------


def test_first_registration_is_new(ledger: Ledger) -> None:
    """首次登记返回 is_new=True / The first registration reports is_new."""
    _, is_new = ledger.register(raw())
    assert is_new is True
    assert ledger.total() == 1


def test_second_registration_is_not_new(ledger: Ledger) -> None:
    """
    再次登记返回 is_new=False / A repeat registration reports not-new.

    这是跨日去重的落点：同一篇文章明天还在 feed 里，不该被重抓、重发。
    """
    ledger.register(raw())
    _, is_new = ledger.register(raw())

    assert is_new is False
    assert ledger.total() == 1


def test_tracking_params_do_not_create_duplicates(ledger: Ledger) -> None:
    """
    **带不同追踪参数的同一篇算一篇。**
    The same article with different tracking parameters counts once.

    微信分享带 from=groupmessage、浏览器复制带 utm_source，按原始字符串比对会重复。
    """
    ledger.register(raw("https://e.com/a?from=groupmessage"))
    _, is_new = ledger.register(raw("https://e.com/a?utm_source=browser"))

    assert is_new is False
    assert ledger.total() == 1


def test_different_articles_are_separate(ledger: Ledger) -> None:
    """不同文章分别登记 / Distinct articles are registered separately."""
    ledger.register(raw("https://e.com/a"))
    ledger.register(raw("https://e.com/b"))
    assert ledger.total() == 2


def test_title_backfilled_for_user_links(ledger: Ledger) -> None:
    """
    用户投递的链接初始无标题，抓到后补上。
    A user-submitted link starts without a title and gets one once known.
    """
    article_id, _ = ledger.register(raw(title=""))
    assert ledger.get(article_id).title == ""

    ledger.register(raw(title="后来知道的标题"))
    assert ledger.get(article_id).title == "后来知道的标题"


def test_feed_title_is_preserved_separately(ledger: Ledger) -> None:
    """
    **源自带的标题单独保存，不被抽取结果覆盖。**
    The source's own title is stored separately and never overwritten by extraction.

    真机实测（oschina）：SPA 站点抽取降级时，页面标题是站点通用名。若只存一列
    标题，一次降级抽取就会**永久丢掉 RSS 里的正确标题**——之后重抓也拿不回来，
    因为重抓的兜底标题正是那个已被污染的值。
    Verified live on oschina: a degraded extraction of a SPA yields the site's generic
    name as the title. With a single title column one degraded fetch permanently
    destroys the correct RSS title, and a re-fetch cannot recover it because its
    fallback is the already-corrupted value.

    见 docs/issues/004
    """
    article_id, _ = ledger.register(raw(title="RSS 里的真实标题"))
    ledger.record_fetch(article_id, article(text="太短", ok=False))

    record = ledger.get(article_id)
    assert record.feed_title == "RSS 里的真实标题"


def test_feed_title_refreshes_from_source(ledger: Ledger) -> None:
    """
    每次采集都以源为准刷新 feed_title / feed_title is refreshed from the source each time.

    源才是标题的权威出处，因此被污染的历史数据会在下一轮采集时自愈。
    The source is authoritative for this field, so corrupted rows heal on the next
    collection round.
    """
    article_id, _ = ledger.register(raw(title="旧标题"))
    ledger.register(raw(title="源更新后的标题"))

    assert ledger.get(article_id).feed_title == "源更新后的标题"


def test_new_registration_is_pending(ledger: Ledger) -> None:
    """刚登记的文章状态为 pending / A freshly registered article is pending."""
    article_id, _ = ledger.register(raw())
    assert ledger.get(article_id).status is FetchStatus.PENDING


# --- 抓取结果 / fetch results -------------------------------------------------


def test_successful_fetch_recorded_as_ok(ledger: Ledger) -> None:
    """抽取成功记为 ok / A successful extraction is recorded as ok."""
    article_id, _ = ledger.register(raw())
    status = ledger.record_fetch(article_id, article(), image_count=3)

    record = ledger.get(article_id)
    assert status is FetchStatus.OK
    assert record.status is FetchStatus.OK
    assert record.text_len > 0
    assert record.image_count == 3
    assert record.author == "张三"


def test_degraded_extraction_is_not_failure(ledger: Ledger) -> None:
    """
    **抽取降级记为 degraded，不是 failed。**
    A degraded extraction is recorded as degraded, not failed.

    正文没抽出来，但标题和链接仍然可用，这条内容依然能进日报——
    记成 failed 会让它被后续环节整个跳过。
    The body is missing but the title and link remain usable, so the item can still be
    published. Recording it as failed would make later stages skip it entirely.
    """
    article_id, _ = ledger.register(raw())
    status = ledger.record_fetch(article_id, article(text="太短", ok=False))

    assert status is FetchStatus.DEGRADED
    assert ledger.get(article_id).is_usable is True


def test_failure_is_recorded_with_error(ledger: Ledger) -> None:
    """失败时记录状态与错误 / A failure records both the status and the error."""
    article_id, _ = ledger.register(raw())
    ledger.record_failure(article_id, "HTTP 403：被拒绝")

    record = ledger.get(article_id)
    assert record.status is FetchStatus.FAILED
    assert "403" in record.error
    assert record.is_usable is False


def test_long_error_is_truncated(ledger: Ledger) -> None:
    """超长错误信息截断，避免撑爆数据库 / Long error messages are truncated."""
    article_id, _ = ledger.register(raw())
    ledger.record_failure(article_id, "错误" * 1000)
    assert len(ledger.get(article_id).error) <= 500


def test_fetch_count_increments(ledger: Ledger) -> None:
    """抓取次数累加，含重抓 / The fetch count increments, re-fetches included."""
    article_id, _ = ledger.register(raw())
    ledger.record_fetch(article_id, article())
    ledger.record_fetch(article_id, article())

    assert ledger.get(article_id).fetch_count == 2


def test_successful_refetch_clears_previous_error(ledger: Ledger) -> None:
    """
    重抓成功后清除上次的错误 / A successful re-fetch clears the previous error.

    否则「已经修好的文章」会一直显示着旧报错，误导排查。
    """
    article_id, _ = ledger.register(raw())
    ledger.record_failure(article_id, "临时网络故障")
    ledger.record_fetch(article_id, article())

    record = ledger.get(article_id)
    assert record.status is FetchStatus.OK
    assert record.error is None


# --- 查询 / queries -----------------------------------------------------------


def test_list_filters_by_status(ledger: Ledger) -> None:
    """按状态筛选 / Filtering by status."""
    ok_id, _ = ledger.register(raw("https://e.com/1"))
    ledger.record_fetch(ok_id, article())
    ledger.register(raw("https://e.com/2"))

    assert len(ledger.list(status=FetchStatus.OK)) == 1
    assert len(ledger.list(status=FetchStatus.PENDING)) == 1


def test_list_filters_by_source(ledger: Ledger) -> None:
    """按来源筛选 / Filtering by source."""
    ledger.register(raw("https://e.com/1", source="qbitai"))
    ledger.register(raw("https://e.com/2", source="infoq-cn"))

    assert len(ledger.list(source_id="qbitai")) == 1


def test_list_filters_by_search(ledger: Ledger) -> None:
    """按关键词搜索标题与链接 / Searching titles and URLs."""
    ledger.register(raw("https://e.com/1", title="多模态大模型发布"))
    ledger.register(raw("https://e.com/2", title="芯片行业动态"))

    assert len(ledger.list(search="多模态")) == 1
    assert len(ledger.list(search="e.com")) == 2


def test_list_respects_limit(ledger: Ledger) -> None:
    """条数上限生效 / The row limit is applied."""
    for i in range(5):
        ledger.register(raw(f"https://e.com/{i}"))
    assert len(ledger.list(limit=2)) == 2


def test_find_by_url_matches_canonically(ledger: Ledger) -> None:
    """按规范化 URL 反查 / Lookup by URL matches canonically."""
    ledger.register(raw("https://e.com/a"))
    assert ledger.find_by_url("https://e.com/a?utm_source=x") is not None
    assert ledger.find_by_url("https://e.com/other") is None


def test_counts_by_status_and_source(ledger: Ledger) -> None:
    """统计接口 / The counting helpers."""
    ok_id, _ = ledger.register(raw("https://e.com/1", source="qbitai"))
    ledger.record_fetch(ok_id, article())
    ledger.register(raw("https://e.com/2", source="qbitai"))
    ledger.register(raw("https://e.com/3", source="infoq-cn"))

    assert ledger.count_by_status() == {"ok": 1, "pending": 2}
    assert ledger.count_by_source() == {"qbitai": 2, "infoq-cn": 1}


def test_pending_ids(ledger: Ledger) -> None:
    """取出待抓正文的文章 / Ids of articles awaiting a body fetch."""
    done_id, _ = ledger.register(raw("https://e.com/1"))
    ledger.record_fetch(done_id, article())
    pending_id, _ = ledger.register(raw("https://e.com/2"))

    assert ledger.pending_ids() == [pending_id]


def test_get_missing_returns_none(ledger: Ledger) -> None:
    """查不到时返回 None 而不是抛异常 / A missing id returns None rather than raising."""
    assert ledger.get("nonexistent") is None


# --- 人工补正文回写 / syncing a hand-written body -------------------------------


def test_set_body_promotes_a_degraded_article_to_ok(ledger: Ledger) -> None:
    """
    人工补写的正文让状态从 degraded 升到 ok。

    正文来自人而不是抽取器，比任何自动抽取都可靠。不升级状态的话，后续挑文章做
    日报时这篇会一直被当作空文章跳过——人辛苦补的正文等于白补。
    """
    article_id, _ = ledger.register(raw())
    ledger.record_fetch(article_id, article(text="", ok=False))
    assert ledger.get(article_id).status is FetchStatus.DEGRADED

    ledger.set_body(article_id, "我手动补的正文" * 20)

    record = ledger.get(article_id)
    assert record.status is FetchStatus.OK
    assert record.text_len == len("我手动补的正文" * 20)


def test_set_body_clears_the_previous_error(ledger: Ledger) -> None:
    """补上正文后，之前那条「抓取失败」的错误信息不该再显示。"""
    article_id, _ = ledger.register(raw())
    ledger.record_failure(article_id, "HTTP 403")

    ledger.set_body(article_id, "补上的正文")

    assert ledger.get(article_id).error is None


def test_video_count_records_downloads_not_extracted_links(ledger: Ledger) -> None:
    """
    台账里的视频数是**实际下载成功**的数量，不是抽到的链接数。

    抽到 3 个视频链接但一个都没下下来时写 3，会让人以为盘上有三个视频文件。
    """
    article_id, _ = ledger.register(raw())
    with_three_links = Article(
        url="https://e.com/a",
        title="标题",
        text="正文" * 100,
        media=[
            MediaAsset(kind=MediaKind.VIDEO, url=f"https://e.com/{i}.mp4", source_url="https://e.com/a")
            for i in range(3)
        ],
        extraction_ok=True,
    )

    ledger.record_fetch(article_id, with_three_links, video_count=0)

    assert ledger.get(article_id).video_count == 0


def test_set_body_updates_the_title_when_given_one(ledger: Ledger) -> None:
    """人工改对的标题要写进台账，空标题则保留原值（不能把标题清空）。"""
    article_id, _ = ledger.register(raw(title="从 URL 推出来的标题"))
    ledger.record_fetch(article_id, article(text="", ok=False))

    ledger.set_body(article_id, "正文", title="人工改对的标题")
    assert ledger.get(article_id).title == "人工改对的标题"

    ledger.set_body(article_id, "正文", title="")
    assert ledger.get(article_id).title == "人工改对的标题"
