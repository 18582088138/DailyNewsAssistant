"""
test_flow.py —— 流水线编排单元测试 / Pipeline orchestration unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/pipeline/test_flow.py -v

对应的人工验证 / Matching manual check:
    dna digest --dry-run          # 免费：只看会选出哪些条目
    dna digest --limit 3          # 计费：真的生成一期（人工看质量）

覆盖 / Covers:
    1. **dry_run 一次 LLM 都不调用**——这是「先看清单再付费」的核心保证
    2. dry_run 时摘要位置放标题，人能直接核对选题与排序
    3. 非 dry_run 且没给 llm 时明确报错，而不是悄悄跑出个没摘要的日报
    4. 完整流程产出的 DigestEntry 字段齐全：rank / score / need_video / refs / images
    5. rank 从 1 开始且连续
    6. **refs 取整个 cluster 的**，不只是代表条目那一家
    7. 图片与视频按 kind 正确分流
    8. bilingual=False 时不调用翻译；True 时回填 title_en / summary_en
    9. 翻译漏掉的条目 *_en 保持 None，而不是空串
   10. stats 记录 provider / model / 条目数 / 来源数
   11. PipelineReport.explain() 能逐条说明入选理由
   12. max_entries 截断在排序之后

费用分界 / The cost boundary:
    clean / dedup / score 免费；summarize / translate / trend 计费。
    dry_run 停在分界线上，让人确认选题对了再花钱。

预期 / Expected:
    16 passed；耗时 < 1s；**全部使用假 provider，零 LLM 调用、零费用**
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from dna.core.config import Profile
from dna.core.models import MediaAsset, MediaKind, NewsItem, SourceKind
from dna.pipeline.flow import run_daily
from tests.llm.fakes import ScriptedProvider

NOW = datetime(2026, 9, 2, 12, 0, 0)
TODAY = date(2026, 9, 2)


def make_item(
    title: str,
    text: str,
    *,
    url: str,
    source: str = "s1",
    media: list[MediaAsset] | None = None,
) -> NewsItem:
    return NewsItem(
        id=url,
        source_id=source,
        via=SourceKind.RSS,
        url=url,
        canonical_url=url,
        title=title,
        text=text,
        published_at=NOW,
        media=media or [],
    )


def profile(**kwargs) -> Profile:
    kwargs.setdefault("focus_keywords", ["大模型", "多模态"])
    kwargs.setdefault("video_keywords", ["发布", "开源"])
    kwargs.setdefault("digest_max_entries", 10)
    return Profile(**kwargs)


def items() -> list[NewsItem]:
    """
    五条互不相同的条目 / Five distinct items.

    用五条而不是三条：趋势节点有 MIN_ENTRIES_FOR_TREND=4 的门槛，
    样本少于它时综述会被（正确地）跳过，测不到完整链路。
    """
    return [
        make_item("多模态大模型正式发布", "关于模型的正文。" * 40, url="https://a.com/1", source="a"),
        make_item("显卡产能大幅提升", "关于显卡的正文。" * 40, url="https://b.com/2", source="b"),
        make_item("某公司完成新一轮融资", "关于融资的正文。" * 40, url="https://c.com/3", source="c"),
        make_item("推理框架迎来重要更新", "关于框架的正文。" * 40, url="https://d.com/4", source="d"),
        make_item("学术界提出新注意力机制", "关于论文的正文。" * 40, url="https://e.com/5", source="e"),
    ]


def summary_reply(text: str = "这是一条摘要，说明了核心事实内容。") -> str:
    return json.dumps({"summary": text, "tags": ["大模型"]}, ensure_ascii=False)


def trend_reply() -> str:
    return json.dumps(
        {
            "note": (
                "今天的几条消息共同指向推理成本的快速下降：模型能力提升的同时，"
                "单位推理开销正在被硬件与框架两端同时压低，此前受成本限制的应用"
                "场景重新变得可行。资本市场也在跟进这一判断。"
            ),
            "keywords": ["成本", "多模态"],
        },
        ensure_ascii=False,
    )


# --- dry-run：费用分界线 / the cost boundary -----------------------------------


def test_dry_run_makes_no_llm_call() -> None:
    """
    **dry_run 一次 LLM 都不调用。**

    这是「先看清单再付费」的核心保证：人先确认选出来的条目对不对，
    确认了再花钱做摘要。反过来的话，选错了也是付完钱才发现。
    """
    llm = ScriptedProvider("fake", [])
    digest, report = run_daily(items(), llm=llm, profile=profile(), when=TODAY, dry_run=True)

    assert llm.call_count == 0
    assert len(digest.entries) == 5
    assert report.dedup_result is not None


def test_dry_run_works_without_any_llm() -> None:
    """dry_run 连 provider 都不需要——它本来就不该调用。"""
    digest, _ = run_daily(items(), profile=profile(), when=TODAY, dry_run=True)
    assert len(digest.entries) == 5


def test_dry_run_puts_titles_where_summaries_will_go() -> None:
    """
    dry_run 时摘要位置放标题，让人直接核对选题与排序。

    留空的话人看到的是一串空条目，判断不了选得对不对。
    """
    digest, _ = run_daily(items(), profile=profile(), when=TODAY, dry_run=True)

    for entry in digest.entries:
        assert entry.summary_zh == entry.title_zh
        assert entry.summary_zh


def test_non_dry_run_without_llm_fails_loudly() -> None:
    """
    非 dry_run 却没给 llm 时明确报错。

    悄悄跑出一份没有摘要的日报比报错糟糕得多——它看起来是成功的。
    """
    with pytest.raises(ValueError, match="llm"):
        run_daily(items(), profile=profile(), when=TODAY, dry_run=False)


# --- 完整流程 / the full run ----------------------------------------------------


def test_full_run_produces_complete_entries() -> None:
    """完整跑一遍，字段齐全。"""
    llm = ScriptedProvider(
        "fake", [summary_reply()] * 5 + [trend_reply()]
    )
    digest, report = run_daily(items(), llm=llm, profile=profile(), when=TODAY)

    assert digest.date == TODAY
    assert len(digest.entries) == 5
    assert digest.trend_note_zh
    assert report.summaries_degraded == 0

    for entry in digest.entries:
        assert entry.summary_zh == "这是一条摘要，说明了核心事实内容。"
        assert entry.title_zh
        assert entry.refs
        assert 0.0 <= entry.score <= 1.0


def test_ranks_start_at_one_and_are_contiguous() -> None:
    """rank 从 1 开始且连续——发布模板按它排版。"""
    llm = ScriptedProvider("fake", [summary_reply()] * 5 + [trend_reply()])
    digest, _ = run_daily(items(), llm=llm, profile=profile(), when=TODAY)

    assert [e.rank for e in digest.entries] == [1, 2, 3, 4, 5]


def test_entries_are_ordered_by_score() -> None:
    """条目按分从高到低排列。"""
    llm = ScriptedProvider("fake", [summary_reply()] * 5 + [trend_reply()])
    digest, _ = run_daily(items(), llm=llm, profile=profile(), when=TODAY)

    scores = [e.score for e in digest.entries]
    assert scores == sorted(scores, reverse=True)


def test_refs_come_from_the_whole_cluster() -> None:
    """
    来源链接取整个 cluster，不只是被选中的那一家。

    多源报道时读者应该看到全部出处——这是 references 文件的要求。
    """
    body = "同一件事的报道正文。" * 60
    merged = [
        make_item("同一个事件的标题", body, url="https://a.com/1", source="a"),
        make_item("同一个事件的标题", body + "另有后续。" * 20, url="https://b.com/2", source="b"),
    ]
    llm = ScriptedProvider("fake", [summary_reply()])
    digest, _ = run_daily(merged, llm=llm, profile=profile(), when=TODAY)

    assert len(digest.entries) == 1
    assert set(digest.entries[0].refs) == {"https://a.com/1", "https://b.com/2"}


def test_images_and_videos_are_split_by_kind() -> None:
    """图片进 images、视频进 videos，发布应用按类型各取所需。"""
    src = "https://a.com/1"
    with_media = [
        make_item(
            "多模态大模型正式发布",
            "正文内容。" * 40,
            url=src,
            media=[
                MediaAsset(kind=MediaKind.IMAGE, url="https://a.com/i.jpg", source_url=src),
                MediaAsset(kind=MediaKind.VIDEO, url="https://a.com/v.mp4", source_url=src),
            ],
        )
    ]
    llm = ScriptedProvider("fake", [summary_reply()])
    digest, _ = run_daily(with_media, llm=llm, profile=profile(), when=TODAY)

    entry = digest.entries[0]
    assert [m.url for m in entry.images] == ["https://a.com/i.jpg"]
    assert [m.url for m in entry.videos] == ["https://a.com/v.mp4"]


def test_need_video_is_carried_into_the_digest() -> None:
    """need_video 标记要进 Digest——视频应用据此挑条目。"""
    llm = ScriptedProvider("fake", [summary_reply()] * 5 + [trend_reply()])
    digest, _ = run_daily(items(), llm=llm, profile=profile(), when=TODAY)

    flagged = [e for e in digest.entries if e.need_video]
    assert flagged, "命中『发布』关键词的那条应被标记"


def test_max_entries_truncates_after_sorting() -> None:
    """
    截断发生在排序之后，拿到的是分最高的 N 条。

    顺序反了的话日报里是「采集顺序的前 N 条」，评分等于白算。
    """
    llm = ScriptedProvider("fake", [summary_reply()])
    digest, _ = run_daily(items(), llm=llm, profile=profile(), when=TODAY, max_entries=1)

    assert len(digest.entries) == 1
    assert "多模态大模型" in digest.entries[0].title_zh


# --- 双语 / bilingual -----------------------------------------------------------


def test_monolingual_run_does_not_translate() -> None:
    """
    默认只出中文，不调用翻译。

    双语是渲染期参数——不需要英文版时不该为它付费。
    """
    llm = ScriptedProvider("fake", [summary_reply()] * 5 + [trend_reply()])
    digest, report = run_daily(items(), llm=llm, profile=profile(), when=TODAY, bilingual=False)

    assert report.translated_count == 0
    assert all(e.title_en is None for e in digest.entries)


def test_bilingual_run_fills_english() -> None:
    """bilingual=True 时回填英文标题与摘要。"""
    ids = [i.id for i in items()]
    translation = json.dumps(
        {
            "entries": [
                {"id": entry_id, "title_en": f"Title {n}", "summary_en": f"Summary {n}."}
                for n, entry_id in enumerate(ids, 1)
            ]
        },
        ensure_ascii=False,
    )
    llm = ScriptedProvider(
        "fake",
        [summary_reply()] * 5 + [trend_reply(), translation, "Today's news points one way."],
    )

    digest, report = run_daily(items(), llm=llm, profile=profile(), when=TODAY, bilingual=True)

    assert report.translated_count == 5
    assert all(e.title_en for e in digest.entries)
    assert digest.trend_note_en == "Today's news points one way."


def test_untranslated_entries_keep_none_not_empty_strings() -> None:
    """
    翻译漏掉的条目 *_en 保持 None。

    填空串会让英文版出现无标题的空条目，而 None 明确表示「这条没有英文版」，
    后续可以单独补译。
    """
    first_id = items()[0].id
    partial = json.dumps(
        {"entries": [{"id": first_id, "title_en": "Only one", "summary_en": "Only one."}]},
        ensure_ascii=False,
    )
    llm = ScriptedProvider(
        "fake", [summary_reply()] * 5 + [trend_reply(), partial, "Trend in English."]
    )

    digest, report = run_daily(items(), llm=llm, profile=profile(), when=TODAY, bilingual=True)

    assert report.translated_count == 1
    untranslated = [e for e in digest.entries if e.title_en is None]
    assert untranslated, "没译到的条目应保持 None"
    assert all(e.summary_en is None for e in untranslated)


# --- 统计与解释 / stats and explanation ----------------------------------------


def test_stats_record_the_run() -> None:
    """统计要记下 provider、模型与各阶段条目数，供台账记账。"""
    llm = ScriptedProvider("fake", [summary_reply()] * 5 + [trend_reply()])
    digest, _ = run_daily(items(), llm=llm, profile=profile(), when=TODAY)

    assert digest.stats.llm_provider == "fake"
    assert digest.stats.llm_model == "fake-model"
    assert digest.stats.raw_count == 5
    assert digest.stats.entry_count == 5
    assert digest.stats.source_count == 5


def test_report_explains_each_selection() -> None:
    """
    报告要能逐条说明入选理由。

    排序不符合预期时，人能立刻看出是关键词没配好还是新鲜度算错了。
    """
    _, report = run_daily(items(), profile=profile(), when=TODAY, dry_run=True)
    lines = report.explain()

    assert len(lines) == 5
    assert "关键词" in lines[0]
    assert "多模态大模型" in lines[0]


def test_empty_input_produces_an_empty_digest() -> None:
    """没有条目时产出空日报，不崩。"""
    digest, _ = run_daily([], profile=profile(), when=TODAY, dry_run=True)

    assert digest.entries == []
    assert digest.date == TODAY
