"""
test_translate.py —— 双语翻译节点单元测试 / Bilingual translation node unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/pipeline/test_translate.py -v

对应的人工验证 / Matching manual check:
    dna digest --limit 3 --lang zh,en    # 真机跑，人工看英文质量（会计费）

覆盖 / Covers:
    1. 提示词里带上每条的 id、中文标题与摘要
    2. **要求模型原样返回 id**——只靠顺序对齐时漏译一条会让后面全部错位且不报错
    3. 模型返回未知 id 时丢弃，不污染别的条目
    4. 漏译的条目**不出现在结果里**（调用方据此知道哪些没译成）
    5. 翻译失败时返回空字典，中文版照常可用
    6. 分批：超过 BATCH_SIZE 时拆成多次调用
    7. 分批：某一批失败不影响其余批次
    8. translate_text 单段翻译；失败返回 None 而不是原文
    9. 空输入不调用 LLM

为什么按 id 对齐而不是按顺序 / Why ids rather than positional alignment:
    模型漏译一条时，顺序对齐会让第 3 条的英文安到第 2 条头上，
    而且完全不报错——中英两版说的是不同的事，读者无从察觉。
    A skipped entry silently shifts every following one, attaching the wrong English to
    each Chinese item with no error raised.

预期 / Expected:
    13 passed；耗时 < 1s；**全部使用假 provider，零 LLM 调用、零费用**
"""

from __future__ import annotations

import json

from dna.core.errors import ProviderTimeoutError
from dna.pipeline.translate import (
    BATCH_SIZE,
    build_messages,
    translate_all,
    translate_batch,
    translate_text,
)
from tests.llm.fakes import ScriptedProvider

ITEMS = [
    ("id-1", "OpenAI 发布新一代多模态模型", "该模型支持视频理解，最长 15 秒。"),
    ("id-2", "英伟达新显卡开始量产", "产能较上一代提升三成。"),
]


def reply(*pairs: tuple[str, str, str]) -> str:
    """构造一条合法的翻译响应 / Build a well-formed translation response."""
    return json.dumps(
        {
            "entries": [
                {"id": i, "title_en": t, "summary_en": s} for i, t, s in pairs
            ]
        },
        ensure_ascii=False,
    )


# --- 提示词构建 / prompt construction ------------------------------------------


def test_prompt_carries_ids_titles_and_summaries() -> None:
    """每条的 id、标题、摘要都要送进去。"""
    text = " ".join(m.content for m in build_messages(ITEMS))

    for entry_id, title, summary in ITEMS:
        assert entry_id in text
        assert title in text
        assert summary in text


def test_prompt_requires_ids_to_be_echoed_back() -> None:
    """
    提示词必须要求模型原样返回 id。

    只靠顺序对齐的话，模型漏译一条就会让后面全部错位——第 3 条的英文安到
    第 2 条头上，中英两版说的是不同的事，而且**不会报任何错**。
    """
    system_prompt = build_messages(ITEMS)[0].content
    assert "id" in system_prompt.lower()
    assert "same order" in system_prompt.lower() or "keeping each" in system_prompt.lower()


def test_prompt_protects_proper_nouns() -> None:
    """产品名、版本号、数字必须原样保留，否则 GPT-4o 会被译成别的东西。"""
    system_prompt = build_messages(ITEMS)[0].content
    assert "EXACTLY" in system_prompt


# --- 响应解析 / response parsing -----------------------------------------------


def test_normal_response_maps_by_id() -> None:
    """正常响应按 id 映射回原条目。"""
    llm = ScriptedProvider(
        "fake",
        [reply(("id-1", "OpenAI releases new model", "It handles video."),
               ("id-2", "Nvidia starts mass production", "Capacity is up 30%."))],
    )

    result = translate_batch(ITEMS, llm)

    assert result["id-1"] == ("OpenAI releases new model", "It handles video.")
    assert result["id-2"] == ("Nvidia starts mass production", "Capacity is up 30%.")


def test_unknown_id_is_discarded() -> None:
    """
    模型编造的 id 被丢弃。

    留着它会覆盖或污染真实条目——而这种错误在成稿里看不出来。
    """
    llm = ScriptedProvider(
        "fake",
        [reply(("id-1", "Correct", "Correct summary."),
               ("id-999", "Hallucinated", "Made-up summary."))],
    )

    result = translate_batch(ITEMS, llm)

    assert set(result) == {"id-1"}


def test_missing_entries_are_simply_absent() -> None:
    """
    漏译的条目不出现在结果里，而不是填一个空串。

    填空串会让调用方拿到一个「看起来完整」的字典，然后把空标题写进英文版。
    """
    llm = ScriptedProvider("fake", [reply(("id-1", "Only this one", "Only this summary."))])

    result = translate_batch(ITEMS, llm)

    assert set(result) == {"id-1"}
    assert "id-2" not in result


def test_failure_returns_empty_so_chinese_still_works() -> None:
    """
    翻译失败返回空字典，不抛异常。

    英文版是附加产物，它失败不该让中文版也发不出去。
    """
    llm = ScriptedProvider("fake", [ProviderTimeoutError("超时")])
    assert translate_batch(ITEMS, llm) == {}


def test_empty_input_makes_no_call() -> None:
    """空输入不调用 LLM——空调用也是要计费的。"""
    llm = ScriptedProvider("fake", [])
    assert translate_batch([], llm) == {}
    assert llm.call_count == 0


# --- 分批 / batching -----------------------------------------------------------


def test_large_input_is_split_into_batches() -> None:
    """
    超过 BATCH_SIZE 时拆批。

    一次塞太多会超出输出长度并让模型静默漏译，而漏译是看不出来的。
    """
    many = [(f"id-{i}", f"标题{i}", f"摘要{i}") for i in range(BATCH_SIZE + 3)]
    responses = [
        reply(*[(f"id-{i}", f"Title {i}", f"Summary {i}") for i in range(BATCH_SIZE)]),
        reply(*[(f"id-{i}", f"Title {i}", f"Summary {i}") for i in range(BATCH_SIZE, BATCH_SIZE + 3)]),
    ]
    llm = ScriptedProvider("fake", responses)

    result = translate_all(many, llm)

    assert llm.call_count == 2
    assert len(result) == BATCH_SIZE + 3


def test_one_failing_batch_does_not_lose_the_others() -> None:
    """某一批翻车，其余批次的英文版仍然可用。"""
    many = [(f"id-{i}", f"标题{i}", f"摘要{i}") for i in range(BATCH_SIZE + 2)]
    llm = ScriptedProvider(
        "fake",
        [
            ProviderTimeoutError("第一批超时"),
            reply(*[(f"id-{i}", f"Title {i}", f"Summary {i}") for i in range(BATCH_SIZE, BATCH_SIZE + 2)]),
        ],
    )

    result = translate_all(many, llm)

    assert len(result) == 2
    assert f"id-{BATCH_SIZE}" in result


# --- 单段翻译 / standalone passage ---------------------------------------------


def test_translate_text_returns_the_translation() -> None:
    """趋势综述这类整段文本走单独的翻译入口。"""
    llm = ScriptedProvider("fake", ["Today's AI news points in one direction."])
    assert translate_text("今天的 AI 新闻指向同一个方向。", llm) == (
        "Today's AI news points in one direction."
    )


def test_translate_text_returns_none_on_failure() -> None:
    """
    失败返回 None 而不是原文。

    把中文塞进英文版里比缺一段更糟——读者会以为是排版事故而不是翻译缺失。
    """
    llm = ScriptedProvider("fake", [ProviderTimeoutError("超时")])
    assert translate_text("今天的 AI 新闻。", llm) is None


def test_translate_text_skips_empty_input() -> None:
    """空文本不调用 LLM。"""
    llm = ScriptedProvider("fake", [])
    assert translate_text("   ", llm) is None
    assert llm.call_count == 0
