"""
test_filters.py —— 条目过滤单元测试 / Item filtering unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/sources/test_filters.py -v

覆盖 / Covers:
    1. exclude 命中即丢弃，且**优先级最高**（同时命中 include 也要丢）
    2. include 非空时，一条都没命中就丢弃；include 为空表示不限制
    3. 匹配范围是**标题 + 源自带摘要**，不只是标题
    4. 大小写不敏感（英文源的关键词不该因大小写漏匹配）
    5. min_title_length 滤掉空标题/超短标题
    6. max_age_days 滤掉过旧条目；**没有发布时间的条目不因此被丢**
    7. build_filter：全局排除词对所有源生效，源级规则在其上追加
    8. build_filter：**include 只取源级**，不从全局继承
    9. 无规则时走快路径，全量保留
   10. FilterOutcome 记录丢弃原因，供用户理解「今天为什么只有 N 条」

为什么过滤放在抓正文之前 / Why filtering precedes body fetching:
    一条被过滤掉的条目不产生 HTTP 请求、不下载图片、不占存储，后续也不进 LLM。
    放在这一步省下的是全链路成本。
    A filtered item costs no request, no image download, no storage, and never reaches
    the LLM stage — filtering here saves cost along the whole chain.

预期 / Expected:
    耗时 < 1s；纯函数，无 I/O
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from dna.core.config import Profile, SourceConfig, SourceFilter
from dna.core.models import RawItem, SourceKind
from dna.sources.filters import apply_filters, build_filter, should_keep

NOW = datetime(2026, 9, 2, 10, 0, 0)


def item(
    title: str = "某公司发布多模态大模型",
    summary: str = "",
    published: datetime | None = None,
) -> RawItem:
    """构造一条待过滤的条目 / Build an item to filter."""
    return RawItem(
        source_id="test",
        via=SourceKind.RSS,
        url="https://e.com/a",
        title=title,
        summary_raw=summary,
        published_at=published,
    )


# --- exclude ------------------------------------------------------------------


def test_exclude_drops_item() -> None:
    """命中排除词即丢弃 / An excluded keyword drops the item."""
    keep, reason = should_keep(item("某公司发布招聘启事"), SourceFilter(exclude=["招聘"]))
    assert keep is False
    assert "招聘" in reason


def test_exclude_beats_include() -> None:
    """
    exclude 优先级高于 include / exclude wins over include.

    宁可少收不要错收：既命中关注词又命中排除词时，应当丢弃。
    Better to miss one than to let a bad one through: when both match, drop it.
    """
    rules = SourceFilter(include=["大模型"], exclude=["广告"])
    keep, _ = should_keep(item("大模型广告推广"), rules)
    assert keep is False


def test_exclude_matches_summary_not_only_title() -> None:
    """
    摘要里的排除词也算数 / Excluded keywords in the summary count too.

    很多软文标题看不出来，正文摘要里才露馅。
    """
    keep, _ = should_keep(item("行业观察", summary="本文由某品牌赞助广告"), SourceFilter(exclude=["广告"]))
    assert keep is False


def test_exclude_is_case_insensitive() -> None:
    """大小写不敏感 / Matching is case-insensitive."""
    keep, _ = should_keep(item("A New JOB Opening"), SourceFilter(exclude=["job"]))
    assert keep is False


# --- include ------------------------------------------------------------------


def test_include_keeps_matching() -> None:
    """命中关注词则保留 / An item matching an include keyword is kept."""
    keep, reason = should_keep(item("大模型发布"), SourceFilter(include=["大模型"]))
    assert keep is True
    assert reason == ""


def test_include_drops_non_matching() -> None:
    """一条关注词都没命中则丢弃 / An item matching no include keyword is dropped."""
    keep, reason = should_keep(item("本地美食推荐"), SourceFilter(include=["大模型", "AI"]))
    assert keep is False
    assert "未命中" in reason


def test_empty_include_means_no_restriction() -> None:
    """include 为空表示不限制 / An empty include list imposes no restriction."""
    keep, _ = should_keep(item("任意标题"), SourceFilter(include=[]))
    assert keep is True


def test_include_matches_summary() -> None:
    """摘要里命中关注词也算 / An include keyword in the summary counts."""
    keep, _ = should_keep(
        item("行业周报", summary="本期聚焦多模态大模型进展"), SourceFilter(include=["大模型"])
    )
    assert keep is True


# --- 标题长度 / title length ---------------------------------------------------


def test_min_title_length_drops_short() -> None:
    """标题过短则丢弃 / A too-short title is dropped."""
    keep, reason = should_keep(item("快讯"), SourceFilter(min_title_length=6))
    assert keep is False
    assert "标题过短" in reason


def test_min_title_length_allows_long_enough() -> None:
    """标题够长则保留 / A long-enough title is kept."""
    keep, _ = should_keep(item("某公司发布多模态大模型"), SourceFilter(min_title_length=6))
    assert keep is True


# --- 时效 / recency -----------------------------------------------------------


def test_max_age_drops_old_items() -> None:
    """过旧的条目被丢弃 / Items older than the cutoff are dropped."""
    old = item(published=NOW - timedelta(days=10))
    keep, reason = should_keep(old, SourceFilter(max_age_days=3), now=NOW)
    assert keep is False
    assert "过旧" in reason


def test_max_age_keeps_recent_items() -> None:
    """近期条目保留 / Recent items are kept."""
    recent = item(published=NOW - timedelta(hours=5))
    keep, _ = should_keep(recent, SourceFilter(max_age_days=3), now=NOW)
    assert keep is True


def test_missing_published_date_is_not_dropped_by_age() -> None:
    """
    **没有发布时间的条目不因时效被丢。**
    Items without a publication date are never dropped by the age rule.

    不少 feed 不提供 pubDate（P2 已确认这在真实数据里很常见）。若把「没有时间」
    当成「很旧」，会把这些源的内容全部误杀。
    Many feeds omit pubDate — confirmed common in real data during P2. Treating a
    missing date as "old" would silently discard everything from those sources.
    """
    keep, _ = should_keep(item(published=None), SourceFilter(max_age_days=1), now=NOW)
    assert keep is True


# --- 规则合并 / rule composition ----------------------------------------------


def test_global_excludes_apply_to_all_sources() -> None:
    """全局排除词对所有源生效 / The global exclude list applies to every source."""
    profile = Profile(exclude_keywords=["招聘", "广告"])
    source = SourceConfig(id="a", name="A", url="https://e.com/feed")

    rules = build_filter(source, profile)
    assert "招聘" in rules.exclude
    assert "广告" in rules.exclude


def test_source_excludes_are_appended_to_global() -> None:
    """源级排除词追加在全局之上 / Per-source excludes are added to the global ones."""
    profile = Profile(exclude_keywords=["招聘"])
    source = SourceConfig(
        id="a", name="A", url="https://e.com/feed", filters=SourceFilter(exclude=["融资快讯"])
    )

    rules = build_filter(source, profile)
    assert {"招聘", "融资快讯"} <= set(rules.exclude)


def test_merged_excludes_are_deduplicated() -> None:
    """重复的排除词只留一次 / Duplicate excludes collapse."""
    profile = Profile(exclude_keywords=["招聘"])
    source = SourceConfig(
        id="a", name="A", url="https://e.com/feed", filters=SourceFilter(exclude=["招聘"])
    )
    assert build_filter(source, profile).exclude.count("招聘") == 1


def test_include_is_not_inherited_from_global() -> None:
    """
    **include 只取源级，不从全局继承。**
    `include` is per-source only and never inherited globally.

    全局强制「必须命中某关键词」会把大量正常资讯误杀——focus_keywords 是给
    P3 打分加权用的，不是过滤条件。
    Enforcing "must match a keyword" globally would discard large amounts of
    legitimate news; focus_keywords exist to boost scoring in P3, not to filter.
    """
    profile = Profile(focus_keywords=["大模型"], exclude_keywords=[])
    source = SourceConfig(id="a", name="A", url="https://e.com/feed")

    assert build_filter(source, profile).include == []


# --- 批量应用 / batch application ---------------------------------------------


def test_apply_filters_splits_kept_and_dropped() -> None:
    """保留与丢弃分开返回 / Kept and dropped items are returned separately."""
    items = [item("大模型发布"), item("招聘启事"), item("多模态进展")]
    outcome = apply_filters(items, SourceFilter(exclude=["招聘"]))

    assert len(outcome.kept) == 2
    assert outcome.dropped_count == 1


def test_apply_filters_records_reasons() -> None:
    """
    丢弃原因要能统计 / Drop reasons must be countable.

    用户看到「今天只有 6 条」时，应当能立刻知道其余的去哪了。
    """
    items = [item("招聘启事"), item("广告推广"), item("快讯")]
    rules = SourceFilter(exclude=["招聘", "广告"], min_title_length=6)
    outcome = apply_filters(items, rules)

    reasons = outcome.reasons()
    assert reasons["命中排除词"] == 2
    assert reasons["标题过短"] == 1


def test_no_rules_keeps_everything() -> None:
    """无规则时全量保留 / With no rules every item is kept."""
    items = [item("A" * 10), item("B" * 10)]
    outcome = apply_filters(items, SourceFilter())

    assert len(outcome.kept) == 2
    assert outcome.dropped_count == 0


@pytest.mark.parametrize(
    ("rules", "expected_empty"),
    [
        (SourceFilter(), True),
        (SourceFilter(include=["x"]), False),
        (SourceFilter(exclude=["x"]), False),
        (SourceFilter(min_title_length=1), False),
        (SourceFilter(max_age_days=0), False),
    ],
)
def test_is_empty_detection(rules: SourceFilter, expected_empty: bool) -> None:
    """判断规则是否为空，用于走快路径 / Emptiness check, used to take the fast path."""
    assert rules.is_empty() is expected_empty
