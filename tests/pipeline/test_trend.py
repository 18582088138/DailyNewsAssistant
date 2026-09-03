"""
test_trend.py —— 趋势综述节点单元测试 / Trend note node unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/pipeline/test_trend.py -v

对应的人工验证 / Matching manual check:
    dna digest --limit 6        # 综述会打印在日报开头，人工看是否言之有物（会计费）

覆盖 / Covers:
    1. 提示词里带上全部条目的标题与摘要，并**带编号**（模型可以说「第 2、5 条同属一类」）
    2. 提示词要求提炼共性主线，而不是逐条复述
    3. 条目太少时**不调用 LLM**——两三条谈不上趋势，硬写只会得到废话
    4. 正常响应解析成 note + keywords
    5. 失败时返回 (None, [])，日报主体照常
    6. 空 note 视为没有综述
    7. 只调用一次（输入是已压缩的摘要，不是原始正文）

为什么综述失败不算错误 / Why a failed note is not an error:
    日报没有综述照样成立——它是加分项不是必需品。
    为它失败一次就让整期日报生不出来是不合理的。
    A digest without a trend note is still a valid digest.

预期 / Expected:
    11 passed；耗时 < 1s；**全部使用假 provider，零 LLM 调用、零费用**
"""

from __future__ import annotations

import json

from dna.core.errors import RateLimitError
from dna.pipeline.trend import MIN_ENTRIES_FOR_TREND, build_messages, build_trend
from tests.llm.fakes import ScriptedProvider

ENTRIES = [
    ("OpenAI 发布新一代多模态模型", "支持视频理解，最长 15 秒。"),
    ("谷歌开源新的推理框架", "推理成本下降四成。"),
    ("英伟达新显卡量产", "产能提升三成。"),
    ("某创业公司完成 B 轮融资", "估值达到 20 亿美元。"),
    ("学术界提出新的注意力机制", "长上下文性能显著提升。"),
]

NOTE = (
    "今天的几条消息共同指向推理成本的快速下降：模型能力在提升的同时，"
    "单位推理开销正在被硬件与框架两端同时压低，这让此前受成本限制的应用场景"
    "重新变得可行。资本市场也在跟进这一判断。"
)


def reply(note: str = NOTE, keywords: list[str] | None = None) -> str:
    """构造一条合法的响应 / Build a well-formed response."""
    return json.dumps(
        {"note": note, "keywords": keywords or ["推理成本", "多模态"]}, ensure_ascii=False
    )


# --- 提示词构建 / prompt construction ------------------------------------------


def test_prompt_carries_every_entry() -> None:
    """全部条目的标题与摘要都要送进去——综述要的是跨条目的共性。"""
    text = " ".join(m.content for m in build_messages(ENTRIES))

    for title, summary in ENTRIES:
        assert title in text
        assert summary in text


def test_entries_are_numbered() -> None:
    """
    条目带编号送入。

    模型可以说「其中第 2、5 条同属一个方向」，比复述标题精炼得多。
    """
    text = build_messages(ENTRIES)[-1].content

    assert "1. OpenAI 发布新一代多模态模型" in text
    assert "5. 学术界提出新的注意力机制" in text


def test_prompt_says_order_encodes_importance() -> None:
    """顺序即重要性，要告诉模型，让它更重视靠前的条目。"""
    assert "按重要性排序" in build_messages(ENTRIES)[-1].content


def test_prompt_forbids_restating_each_entry() -> None:
    """
    提示词必须禁止逐条复述。

    不说的话模型会写成一段「今天有五条新闻，分别是……」的目录，
    而那是下面的条目本身要做的事，综述放在开头毫无价值。
    """
    system_prompt = build_messages(ENTRIES)[0].content
    assert "不要逐条复述" in system_prompt
    assert "共性主线" in system_prompt


def test_prompt_forbids_outside_information() -> None:
    """观点必须建立在给出的条目之上，不能引入外部信息——否则就是编造。"""
    assert "不引入外部信息" in build_messages(ENTRIES)[0].content


# --- 条目数量门槛 / the entry-count floor --------------------------------------


def test_too_few_entries_makes_no_call() -> None:
    """
    条目太少时**根本不调用 LLM**。

    两三条新闻谈不上「趋势」，硬写只会得到一段把标题重述一遍的废话，
    而且还要花钱。
    """
    llm = ScriptedProvider("fake", [reply()])
    note, keywords = build_trend(ENTRIES[: MIN_ENTRIES_FOR_TREND - 1], llm)

    assert note is None
    assert keywords == []
    assert llm.call_count == 0


def test_exactly_at_the_threshold_does_call() -> None:
    """刚好达到门槛就该正常生成。"""
    llm = ScriptedProvider("fake", [reply()])
    note, _ = build_trend(ENTRIES[:MIN_ENTRIES_FOR_TREND], llm)

    assert note is not None
    assert llm.call_count == 1


def test_empty_input_makes_no_call() -> None:
    """空输入不调用 LLM。"""
    llm = ScriptedProvider("fake", [])
    assert build_trend([], llm) == (None, [])
    assert llm.call_count == 0


# --- 响应解析 / response parsing -----------------------------------------------


def test_normal_response_is_parsed() -> None:
    """正常响应解析成综述与关键词。"""
    llm = ScriptedProvider("fake", [reply(keywords=["推理成本", "开源", "融资"])])
    note, keywords = build_trend(ENTRIES, llm)

    assert note == NOTE
    assert keywords == ["推理成本", "开源", "融资"]


def test_only_one_call_is_made() -> None:
    """
    只调用一次。

    输入是已经压缩过的摘要而不是原始正文——综述要的是跨条目共性，
    喂全文只会增加成本并让模型被细节淹没。
    """
    llm = ScriptedProvider("fake", [reply()])
    build_trend(ENTRIES, llm)

    assert llm.call_count == 1


def test_failure_returns_none_so_the_digest_still_works() -> None:
    """
    失败返回 (None, [])，不抛异常。

    日报没有综述照样成立——为一个加分项让整期生不出来是不合理的。
    """
    llm = ScriptedProvider("fake", [RateLimitError("429 限流")])
    assert build_trend(ENTRIES, llm) == (None, [])
