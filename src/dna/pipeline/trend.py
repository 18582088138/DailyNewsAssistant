"""
趋势综述节点 / Trend note node.

看完当天全部条目后，写一段「今天 AI 圈发生了什么」的主线提炼。
Reads the day's entries as a whole and writes the through-line: what happened in AI today.

它是日报的开篇，也是播客的开场白。
It opens the digest and doubles as the podcast's opening remarks.

只调用一次 / Exactly one call:
    输入是全部条目的标题与摘要（已经压缩过的），不是原始正文。
    综述要的是「跨条目的共性」，而摘要已经把每条压到了核心事实，
    再喂全文只会增加成本并让模型被细节淹没。
    The input is every entry's title and summary — already compressed — not the raw
    bodies. A through-line needs what the entries have in common, and the summaries
    already carry each one's core fact; feeding full text would only add cost and bury
    the model in detail.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from dna.core.logging import get_logger
from dna.core.prompts import load_prompt
from dna.llm.base import ChatMessage, LLMProvider, system, user

logger = get_logger("pipeline.trend")

# 少于这么多条就不写综述 / below this many entries, no note is written
# 两三条新闻谈不上「趋势」，硬写只会得到一段把标题重述一遍的废话。
# Two or three items are not a trend; forcing it yields a paragraph that merely restates
# the headlines.
MIN_ENTRIES_FOR_TREND = 4

# 提示词正文在 `config/prompts/trend.md` / The prompt text lives in that file.
PROMPT_NAME = "trend"


def system_prompt() -> str:
    """趋势节点的 system prompt / The trend node's system prompt。"""
    return load_prompt(PROMPT_NAME)


class TrendOut(BaseModel):
    """趋势综述的结构化输出 / Structured output of the trend node."""

    note: str = Field(
        min_length=30,
        max_length=800,
        description="150~250 字的主线提炼，2~4 句",
    )
    keywords: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="2~5 个当天关键词",
    )


def build_messages(entries: list[tuple[str, str]]) -> list[ChatMessage]:
    """
    构造提示词 / Build the prompt.

    参数 / Args:
        entries: [(标题, 摘要), …]，已按重要性排好序

    编号送进去：模型可以说「其中第 2、5 条同属一个方向」，比复述标题精炼。
    顺序即重要性，模型会自然地更重视靠前的条目。
    Entries are numbered so the model can say "items 2 and 5 point the same way" instead
    of restating headlines. The order encodes importance and the model weights the
    earlier ones accordingly.
    """
    lines = [f"{index}. {title}\n   {summary}" for index, (title, summary) in enumerate(entries, 1)]
    return [
        system(system_prompt()),
        user("今日条目（按重要性排序）：\n\n" + "\n\n".join(lines)),
    ]


def build_trend(entries: list[tuple[str, str]], llm: LLMProvider) -> tuple[str | None, list[str]]:
    """
    生成当日主线提炼 / Produce the day's trend note.

    返回 / Returns:
        (综述文本或 None, 关键词列表)

    条目太少或调用失败时返回 `(None, [])`。日报**没有综述照样成立**——
    它是加分项，不是必需品；为它失败一次就让整期日报生不出来是不合理的。
    Returns (None, []) when there are too few entries or the call fails. A digest without
    a trend note is still a valid digest: the note is a bonus, not a requirement, and
    failing the whole issue over it would be unreasonable.
    """
    if len(entries) < MIN_ENTRIES_FOR_TREND:
        logger.info("条目仅 %d 条，不足以提炼主线，跳过综述", len(entries))
        return None, []

    try:
        out = llm.chat_json(build_messages(entries), TrendOut, temperature=0.5)
    except Exception as exc:  # noqa: BLE001 - 综述失败不影响日报主体
        logger.warning("主线提炼失败，本期日报将没有开篇综述：%s", exc)
        return None, []

    note = out.note.strip()
    keywords = [k.strip() for k in out.keywords if k.strip()]
    logger.info("主线提炼完成：%d 字，关键词 %s", len(note), "、".join(keywords) or "无")
    return note or None, keywords


__all__ = [
    "MIN_ENTRIES_FOR_TREND",
    "PROMPT_NAME",
    "TrendOut",
    "build_messages",
    "build_trend",
    "system_prompt",
]
