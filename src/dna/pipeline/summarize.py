"""
摘要节点 / Summarisation node.

把一个事件压成 1~2 句中文总结，供日报正文使用。
Compresses one event into a one-to-two sentence Chinese summary for the digest body.

**这是第一个真正花钱的节点。** 因此：
    - 提示词构建与响应解析拆成纯函数，可以完全离线测试
    - `summarize_cluster` 接受注入的 provider，测试用假的
    - 单条失败降级为「用标题当摘要」，不抛异常也不重试整批
This is the first node that actually costs money, so prompt construction and response
parsing are pure functions testable offline, the provider is injected, and a single
failure degrades to "use the title" rather than aborting the batch.

为什么降级用标题而不是跳过 / Why a failed item falls back to its title:
    标题本身就是一句话摘要，虽然不如 LLM 写的贴切，但足以让读者判断要不要点开。
    因为一次 API 抖动就把一条真实资讯从日报里删掉，代价大得多。
    A headline is already a one-sentence summary — less apt than a written one, but
    enough for the reader to decide whether to open it. Dropping real news because of a
    transient API failure costs far more.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from dna.core.length import char_feedback
from dna.core.logging import get_logger
from dna.core.models import Cluster
from dna.core.prompts import render_prompt
from dna.llm.base import ChatMessage, LLMProvider, assistant, system, user

logger = get_logger("pipeline.summarize")

# 喂给模型的正文上限 / how much body text is sent
# 3000 字足够写出准确摘要，再多只是线性增加 input token 费用。
# 3000 characters is plenty for an accurate summary; more only scales the input bill.
MAX_BODY_CHARS = 3000

# 提示词正文在 `config/prompts/summarize.md` / The prompt text lives in that file.
PROMPT_NAME = "summarize"

# 摘要的回炉次数上限 / how many rewrites a summary is allowed
#
# **只给 1 次，文案给 2 次。** 摘要是**每条都跑**的——一期几十条，回炉一次就把
# 整期成本翻倍。文案是单篇按需跑的，多试一次只影响那一篇。
# Summaries run for every entry, so one rewrite already doubles an issue's cost, while a
# script is produced one article at a time on request.
MAX_REWRITES = 1

# 为什么要给指标排优先级 / Why the metrics are ranked
#
# 「1~2 句」是硬约束，装不下所有数字，所以**取舍规则必须写进提示词**。
# 不写的话模型按原文出现顺序取，而技术文章的开头往往是模型体积、依赖版本这类
# 次要细节；真正决定这条资讯价值的「激活参数只有 13B」可能在第三段。
# 实测对照：模型自选写了「原生 8-bit 精度模型文件 167GB」，而人工撰写的参考摘要
# 在同一位置写的是「激活参数 13B」——后者才是读者判断要不要点开的依据。
# The one-to-two sentence limit cannot hold every figure, so the selection rule has to be
# stated. Without it the model takes them in the order they appear, and technical articles
# open on secondary detail like file size and dependency versions while the fact that
# decides the item's worth sits three paragraphs down. Measured against a hand-written
# reference, the model chose "167GB of 8-bit weights" where the reference chose "13B
# activated parameters" — the latter is what tells a reader whether to open it.


DEFAULT_CHARS = (80, 100)
"""不传 `chars` 时提示词里写的字数区间 / the window quoted when none is supplied.

生产路径一律由 `profile.summary_chars` 提供；这个默认值只为让不带配置的调用
（单测、`build_messages` 的纯函数测试）继续可用。
"""


def system_prompt(chars: tuple[int, int] | None = None) -> str:
    """
    摘要节点的 system prompt / The summariser's system prompt.

    字数区间**注入提示词**：提示词里说的数字和 `summarize_cluster` 验收的数字
    必须是同一个，否则又回到「配置说一套、提示词说一套、验收看第三套」。
    The window is injected so the number quoted to the model and the number checked in
    code are the same one.
    """
    low, high = chars or DEFAULT_CHARS
    return render_prompt(PROMPT_NAME, lo_chars=low, hi_chars=high)


class SummaryOut(BaseModel):
    """
    摘要节点的结构化输出 / Structured output of the summarisation node.

    额外要 `tags`：日报要按主题分组，让模型顺手打标签比事后再调一次便宜得多。
    Tags are requested alongside: the digest groups by topic, and having the model label
    it in the same call is far cheaper than a second round-trip.
    """

    summary: str = Field(
        min_length=10,
        max_length=400,
        # 不写字数：字数由 profile.summary_chars 定、由提示词说、由程序验收。
        # 写在这里等于第二个来源——description 会随 JSON Schema 注入提示词，
        # 模型于是同时看到两个不同的数字，两个都不当真。
        description="1~2 句中文摘要，只陈述原文事实，长度按提示词的要求",
    )
    tags: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="1~4 个主题标签，如 大模型/开源/融资/芯片/应用",
    )


@dataclass
class SummaryResult:
    """一条摘要的结果 / The result for one entry."""

    summary: str
    tags: list[str]
    degraded: bool = False
    """True 表示 LLM 调用失败、退回用标题 / the LLM call failed and the title was used."""
    calls: int = 0
    """这条花了几次调用（含回炉）/ how many calls this entry cost, rewrites included."""
    within_target: bool = True
    """字数是否落在目标区间 / whether the length landed inside the target window."""


def build_messages(
    cluster: Cluster,
    *,
    instructions: str = "",
    chars: tuple[int, int] | None = None,
) -> list[ChatMessage]:
    """
    构造提示词 / Build the prompt.

    纯函数：**测试重点在这里**。断言提示词里带上了标题、正文与多源信息，
    比断言模型说了什么可靠得多，也不用花钱。
    A pure function and the main test target: asserting that the prompt carries the
    title, the body and the multi-source note is far more reliable than asserting what
    the model said — and costs nothing.
    """
    item = cluster.canonical
    body = item.text[:MAX_BODY_CHARS].strip()

    lines = [f"标题：{item.title}"]

    if len(cluster.members) > 1:
        # 告诉模型这是多家media报道的同一件事，摘要应写共同的核心事实
        # Tell the model several outlets covered this, so the summary states the shared
        # core fact rather than one outlet's angle.
        others = [m.title for m in cluster.members[1:4]]
        lines.append(f"（另有 {len(cluster.members) - 1} 家媒体报道同一事件：{'；'.join(others)}）")

    if body:
        lines.append(f"\n正文：\n{body}")
    else:
        lines.append("\n（正文抓取失败，只有标题可用——请基于标题写，不要编造细节）")

    from dna.narration.script_builder import instruction_block

    prompt = system_prompt(chars) + instruction_block(instructions)
    return [system(prompt), user("\n".join(lines))]


def summarize_cluster(
    cluster: Cluster,
    llm: LLMProvider,
    *,
    instructions: str = "",
    chars: tuple[int, int] | None = None,
) -> SummaryResult:
    """
    为一个事件生成摘要 / Summarise one event.

    参数 / Args:
        chars: 目标字数区间（来自 `profile.summary_chars`）。给了就**验收字数**：
            超出区间时带着确切差值回炉重写一次。不给则不检查长度。

    失败时降级为标题，**不抛异常**——理由见模块文档。
    Failures degrade to the title rather than raising; see the module docstring.
    """
    messages = build_messages(cluster, instructions=instructions, chars=chars)
    calls = 0
    summary = ""
    tags: list[str] = []

    for attempt in range(MAX_REWRITES + 1):
        try:
            out = llm.chat_json(messages, SummaryOut, temperature=0.3)
        except Exception as exc:  # noqa: BLE001 - 单条失败不能毁掉整期日报
            if calls:
                # 回炉那一次失败了，但上一稿还在手里：**用上一稿，不要退回标题**。
                # 长度不达标的摘要仍然是一条真摘要，比标题有信息量得多。
                # The rewrite failed but the previous draft is still here: a summary that
                # misses the window still beats falling back to the headline.
                logger.warning("摘要回炉失败，沿用上一稿：%s", exc)
                return SummaryResult(summary=summary, tags=tags, calls=calls, within_target=False)
            logger.warning("摘要失败，退回使用标题：%s —— %s", cluster.canonical.title[:40], exc)
            return SummaryResult(
                summary=cluster.canonical.title, tags=[], degraded=True, calls=0
            )

        calls += 1
        summary = out.summary.strip() or cluster.canonical.title
        tags = [t.strip() for t in out.tags if t.strip()]

        if chars is None:
            return SummaryResult(summary=summary, tags=tags, calls=calls)

        feedback = char_feedback(len(summary), chars[0], chars[1])
        if feedback is None:
            return SummaryResult(summary=summary, tags=tags, calls=calls)

        if attempt == MAX_REWRITES:
            # 差几个字不值得再花一次调用。**返回它并标记**，让调用方知道没达标。
            logger.info(
                "摘要 %d 字未落入 %d~%d 字，按现状返回：%s",
                len(summary), chars[0], chars[1], cluster.canonical.title[:30],
            )
            return SummaryResult(summary=summary, tags=tags, calls=calls, within_target=False)

        messages = [*messages, assistant(out.summary), user(feedback)]

    return SummaryResult(summary=summary, tags=tags, calls=calls)  # pragma: no cover


def summarize_all(
    clusters: list[Cluster],
    llm: LLMProvider,
    *,
    chars: tuple[int, int] | None = None,
) -> list[SummaryResult]:
    """
    批量生成摘要 / Summarise a batch.

    **串行而不是并发**：DeepSeek 有速率限制，并发打过去换来的是 429 与重试，
    实际并不更快，还让费用与日志都难以追踪。一期日报几十条，串行完全可接受。
    Sequential rather than concurrent: DeepSeek rate-limits, so firing in parallel buys
    429s and retries rather than speed, while making cost and logs harder to follow. A
    few dozen entries per issue is perfectly acceptable serially.
    """
    results = [summarize_cluster(c, llm, chars=chars) for c in clusters]
    degraded = sum(1 for r in results if r.degraded)
    off_target = sum(1 for r in results if not r.within_target)
    if off_target:
        logger.info("摘要完成：%d 条，其中 %d 条字数未达标", len(results), off_target)
    if degraded:
        logger.warning("摘要完成：%d 条，其中 %d 条降级为标题", len(results), degraded)
    else:
        logger.info("摘要完成：%d 条", len(results))
    return results


__all__ = [
    "DEFAULT_CHARS",
    "MAX_BODY_CHARS",
    "MAX_REWRITES",
    "PROMPT_NAME",
    "SummaryOut",
    "SummaryResult",
    "build_messages",
    "summarize_all",
    "summarize_cluster",
    "system_prompt",
]
