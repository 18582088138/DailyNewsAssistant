"""
test_score.py —— 重要性打分与 need_video 判定单元测试 / Scoring and video-flag unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/pipeline/test_score.py -v

对应的人工验证 / Matching manual check:
    dna digest --dry-run     # 每条都会打印评分明细，可核对排序是否符合预期

覆盖 / Covers:
    1. 权重合计为 1.0（改动某个权重时忘记调整其它的，这里会立刻暴露）
    2. 关键词：标题命中权重高于正文命中
    3. 关键词：归一化除以 3 而不是关键词总数（配 20 个词时不该让所有分都趋近 0）
    4. 多源：按不同 source_id 计数，同源发两遍不算多源
    5. 新鲜度：48 小时线性衰减；**无发布时间给 0.5 而不是 0**
    6. 新鲜度：未来时间（feed 时区错误）按最新处理，不出现负分
    7. 篇幅与配图信号
    8. need_video：命中视频关键词 / 有官方视频 / 高分，三者任一即可
    9. rank_clusters：先排序后截断，拿到的是分最高的 N 条
   10. ScoreBreakdown.explain() 能说清分是怎么来的

为什么用规则不用 LLM / Why rules rather than an LLM:
    可解释（知道该改哪）、稳定（同输入同分，排序不抖）、免费。
    LLM 更适合做摘要那种必须理解内容的活，排序可以用信号量化。

预期 / Expected:
    26 passed；耗时 < 1s；纯函数，无网络、无 LLM、零费用
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from dna.core.config import Profile
from dna.core.models import Cluster, MediaAsset, MediaKind, NewsItem, SourceKind
from dna.pipeline.score import (
    FRESHNESS_WINDOW_HOURS,
    W_FRESHNESS,
    W_KEYWORD,
    W_MEDIA,
    W_SOURCES,
    W_SUBSTANCE,
    needs_video,
    rank_clusters,
    score_cluster,
)

NOW = datetime(2026, 9, 2, 12, 0, 0)


def make_item(
    title: str,
    text: str = "",
    *,
    source: str = "s1",
    url: str = "https://e.com/1",
    when: datetime | None = None,
    media: list[MediaAsset] | None = None,
) -> NewsItem:
    """构造一条测试条目 / Build a test item."""
    return NewsItem(
        id=url,
        source_id=source,
        via=SourceKind.RSS,
        url=url,
        canonical_url=url,
        title=title,
        text=text,
        published_at=when,
        media=media or [],
    )


def make_cluster(*items: NewsItem) -> Cluster:
    """构造一个 cluster / Build a cluster."""
    return Cluster(id=items[0].id, members=list(items), canonical_url=items[0].canonical_url)


def profile(**kwargs) -> Profile:
    """构造一个偏好配置 / Build a profile."""
    kwargs.setdefault("focus_keywords", ["大模型", "多模态", "智能体"])
    kwargs.setdefault("video_keywords", ["发布", "开源", "突破"])
    return Profile(**kwargs)


# --- 权重 / weights ------------------------------------------------------------


def test_weights_sum_to_one() -> None:
    """
    五个权重必须合计为 1.0。

    调其中一个却忘了调别的，会让总分悄悄跑出 0~1 区间，而排序看起来还是「正常的」。
    """
    assert W_KEYWORD + W_SOURCES + W_FRESHNESS + W_SUBSTANCE + W_MEDIA == pytest.approx(1.0)


def test_score_stays_within_zero_and_one() -> None:
    """最理想的条目也不该超过 1.0，最差的也不该低于 0。"""
    best = make_cluster(
        make_item(
            "大模型 多模态 智能体 全部命中",
            "正文" * 2000,
            when=NOW,
            media=[MediaAsset(kind=MediaKind.VIDEO, url="https://e.com/v.mp4", source_url="https://e.com/1")],
        ),
        make_item("同一事件", "x", source="s2", url="https://e.com/2"),
        make_item("同一事件", "x", source="s3", url="https://e.com/3"),
        make_item("同一事件", "x", source="s4", url="https://e.com/4"),
    )
    worst = make_cluster(make_item("完全不相关的标题", "", when=NOW - timedelta(days=30)))

    assert 0.0 <= score_cluster(worst, profile(), now=NOW).total
    assert score_cluster(best, profile(), now=NOW).total <= 1.0


# --- 关键词 / keyword signal ---------------------------------------------------


def test_title_hit_outweighs_body_hit() -> None:
    """
    标题命中比正文命中更重要。

    标题里出现「多模态」说明这就是主题；正文里顺带提一句则弱得多。
    不区分的话，泛泛提及会挤掉真正相关的内容。
    """
    in_title = make_cluster(make_item("多模态大模型发布", "无关正文" * 50, when=NOW))
    in_body = make_cluster(make_item("一条普通的科技新闻", "文中提到了多模态和大模型。" * 20, when=NOW))

    assert score_cluster(in_title, profile(), now=NOW).keyword > (
        score_cluster(in_body, profile(), now=NOW).keyword
    )


def test_keyword_normalisation_does_not_flatten_with_many_keywords() -> None:
    """
    配了很多关键词时，命中 3 个就该拿满分。

    按关键词总数归一的话，配 20 个词会让所有条目的分都趋近 0，排序失去区分度——
    而「配了很多关键词」恰恰是正常用法。
    """
    many = profile(focus_keywords=["大模型", "多模态", "智能体"] + [f"词{i}" for i in range(20)])
    cluster = make_cluster(make_item("大模型 多模态 智能体 三个都在标题里", "正文" * 50, when=NOW))

    assert score_cluster(cluster, many, now=NOW).keyword == pytest.approx(1.0)


def test_no_keywords_configured_scores_zero_not_crash() -> None:
    """没配关键词时该项为 0，不能除零崩溃。"""
    cluster = make_cluster(make_item("任意标题", "正文" * 50, when=NOW))
    assert score_cluster(cluster, profile(focus_keywords=[]), now=NOW).keyword == 0.0


def test_matched_keywords_are_reported() -> None:
    """命中了哪些词要记下来——排序不对时才知道是关键词配错了还是别的信号有问题。"""
    cluster = make_cluster(make_item("多模态大模型的新进展", "正文" * 50, when=NOW))
    breakdown = score_cluster(cluster, profile(), now=NOW)

    assert set(breakdown.matched_keywords) >= {"多模态", "大模型"}
    assert "多模态" in breakdown.explain()


# --- 多源 / source signal ------------------------------------------------------


def test_multiple_sources_raise_the_score() -> None:
    """几家媒体都报了，说明是真事件而不是一家的软文。"""
    single = make_cluster(make_item("某事件", "正文" * 50, source="a", when=NOW))
    multi = make_cluster(
        make_item("某事件", "正文" * 50, source="a", url="https://e.com/1", when=NOW),
        make_item("某事件", "正文" * 50, source="b", url="https://e.com/2"),
        make_item("某事件", "正文" * 50, source="c", url="https://e.com/3"),
    )

    assert score_cluster(multi, profile(), now=NOW).sources > (
        score_cluster(single, profile(), now=NOW).sources
    )


def test_same_source_twice_is_not_corroboration() -> None:
    """
    同一个源发了两遍不算多源。

    按成员数计会让一个源的重复推送伪装成「多家媒体报道」，这是可以被刷的。
    """
    cluster = make_cluster(
        make_item("某事件", "正文" * 50, source="a", url="https://e.com/1", when=NOW),
        make_item("某事件", "正文" * 50, source="a", url="https://e.com/2"),
    )

    assert score_cluster(cluster, profile(), now=NOW).sources == 0.0


# --- 新鲜度 / freshness --------------------------------------------------------


def test_fresh_beats_stale() -> None:
    """昨天的新闻今天不该排在最前。"""
    fresh = make_cluster(make_item("某事件", "正文" * 50, when=NOW))
    stale = make_cluster(make_item("某事件", "正文" * 50, when=NOW - timedelta(hours=40)))

    assert score_cluster(fresh, profile(), now=NOW).freshness > (
        score_cluster(stale, profile(), now=NOW).freshness
    )


def test_missing_publish_time_scores_half_not_zero() -> None:
    """
    没有发布时间的给 0.5，不是 0。

    很多 feed 不提供 pubDate。当成「很旧」会把整个源沉到底部，
    等于在无意中把它封杀了——而它可能正是最有价值的源。
    """
    unknown = make_cluster(make_item("某事件", "正文" * 50, when=None))
    assert score_cluster(unknown, profile(), now=NOW).freshness == 0.5


def test_beyond_the_window_scores_zero() -> None:
    """超出 48 小时窗口的归零，不出现负分。"""
    old = make_cluster(
        make_item("某事件", "正文" * 50, when=NOW - timedelta(hours=FRESHNESS_WINDOW_HOURS + 10))
    )
    assert score_cluster(old, profile(), now=NOW).freshness == 0.0


def test_future_timestamp_is_treated_as_newest() -> None:
    """
    未来时间按最新处理，不能算出大于 1 的分。

    feed 里的时区处理错误相当常见，一条「明天发布」的新闻不该拿到超额加分。
    """
    future = make_cluster(make_item("某事件", "正文" * 50, when=NOW + timedelta(hours=5)))
    assert score_cluster(future, profile(), now=NOW).freshness == 1.0


# --- 篇幅与配图 / substance and media ------------------------------------------


def test_longer_body_scores_higher() -> None:
    """正文太短的多半是快讯占位。"""
    short = make_cluster(make_item("某事件", "短", when=NOW))
    long_ = make_cluster(make_item("某事件", "正文" * 800, when=NOW))

    assert score_cluster(long_, profile(), now=NOW).substance > (
        score_cluster(short, profile(), now=NOW).substance
    )


def test_video_material_scores_above_images() -> None:
    """视频素材最稀缺也最值钱，给满分；有图给 0.6；都没有给 0。"""
    src = "https://e.com/1"
    with_video = make_cluster(
        make_item("某事件", "正文" * 50, when=NOW, media=[MediaAsset(kind=MediaKind.VIDEO, url="https://e.com/v.mp4", source_url=src)])
    )
    with_image = make_cluster(
        make_item("某事件", "正文" * 50, when=NOW, media=[MediaAsset(kind=MediaKind.IMAGE, url="https://e.com/i.jpg", source_url=src)])
    )
    bare = make_cluster(make_item("某事件", "正文" * 50, when=NOW))

    assert score_cluster(with_video, profile(), now=NOW).media == 1.0
    assert score_cluster(with_image, profile(), now=NOW).media == 0.6
    assert score_cluster(bare, profile(), now=NOW).media == 0.0


# --- need_video ----------------------------------------------------------------


def test_video_keyword_flags_it() -> None:
    """命中视频关键词（有画面感的事件）即标记。"""
    cluster = make_cluster(make_item("某公司发布全新芯片", "正文" * 50, when=NOW))
    assert needs_video(cluster, profile(), 0.1)


def test_official_video_flags_it() -> None:
    """有官方视频素材的最省力，直接标记。"""
    cluster = make_cluster(
        make_item(
            "一条平淡无奇的消息",
            "正文" * 50,
            when=NOW,
            media=[MediaAsset(kind=MediaKind.VIDEO, url="https://e.com/v.mp4", source_url="https://e.com/1")],
        )
    )
    assert needs_video(cluster, profile(), 0.1)


def test_high_score_flags_it() -> None:
    """前排内容值得多做一种形态。"""
    cluster = make_cluster(make_item("一条平淡无奇的消息", "正文" * 50, when=NOW))
    assert needs_video(cluster, profile(), 0.9)
    assert not needs_video(cluster, profile(), 0.1)


def test_flagging_is_deliberately_permissive() -> None:
    """
    宁可误标不可漏标。

    误标了人在界面上取消勾选即可；漏标则那条内容根本不会出现在视频候选里，
    人不会主动去找。
    """
    borderline = make_cluster(make_item("某团队开源了新工具", "正文" * 50, when=NOW))
    assert needs_video(borderline, profile(), 0.0), "命中『开源』就该进候选，哪怕分数很低"


# --- 排序 / ranking ------------------------------------------------------------


def test_ranking_sorts_by_score_descending() -> None:
    """按分从高到低。"""
    clusters = [
        make_cluster(make_item("完全不相关的消息", "短", url="https://e.com/1", when=NOW - timedelta(hours=40))),
        make_cluster(make_item("多模态大模型重大突破", "正文" * 500, url="https://e.com/2", when=NOW)),
    ]

    ranked = rank_clusters(clusters, profile(), now=NOW)

    assert ranked[0][0].canonical.url == "https://e.com/2"
    assert ranked[0][1].total > ranked[1][1].total


def test_limit_is_applied_after_sorting() -> None:
    """
    先排序再截断，拿到的是分最高的 N 条，不是碰巧靠前的 N 条。

    顺序反了的话，日报里会是「采集顺序的前 N 条」，评分等于白算。
    """
    clusters = [
        make_cluster(make_item("无关消息一", "短", url="https://e.com/1", when=NOW - timedelta(hours=40))),
        make_cluster(make_item("无关消息二", "短", url="https://e.com/2", when=NOW - timedelta(hours=40))),
        make_cluster(make_item("多模态大模型重大突破", "正文" * 500, url="https://e.com/3", when=NOW)),
    ]

    ranked = rank_clusters(clusters, profile(), now=NOW, limit=1)

    assert len(ranked) == 1
    assert ranked[0][0].canonical.url == "https://e.com/3"


def test_ranking_returns_the_video_flag() -> None:
    """排序结果里带上 need_video，调用方不用再算一遍。"""
    clusters = [make_cluster(make_item("某公司发布新模型", "正文" * 50, when=NOW))]
    _, _, need_video = rank_clusters(clusters, profile(), now=NOW)[0]
    assert need_video is True


def test_explain_shows_every_component() -> None:
    """
    评分解释要能说清分是怎么来的。

    只给一个总分等于没法调——排序不对时无从下手。
    """
    cluster = make_cluster(make_item("多模态大模型发布", "正文" * 200, when=NOW))
    text = score_cluster(cluster, profile(), now=NOW).explain()

    for label in ("关键词", "多源", "新鲜度", "篇幅", "配图"):
        assert label in text


def test_empty_cluster_list() -> None:
    """空输入返回空结果。"""
    assert rank_clusters([], profile(), now=NOW) == []
