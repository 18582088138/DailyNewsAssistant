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

from dna.core.logging import get_logger
from dna.core.models import Cluster
from dna.llm.base import ChatMessage, LLMProvider, system, user

logger = get_logger("pipeline.summarize")

# 喂给模型的正文上限 / how much body text is sent
# 3000 字足够写出准确摘要，再多只是线性增加 input token 费用。
# 3000 characters is plenty for an accurate summary; more only scales the input bill.
MAX_BODY_CHARS = 3000

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
SYSTEM_PROMPT = """你是一位 AI 领域的资讯编辑，为每日 AI 日报撰写条目摘要。

要求：
1. 用 1~2 句话说清这条资讯的**核心事实**：谁、做了什么、关键数字或结论
2. **信息要完整**：原文讲了几件事就要都覆盖到。如果原文既讲了模型发布、
   又讲了第三方的量化/部署方案，两件事各占一句，不要只写前一半
3. **挑最有价值的指标，不是最先出现的指标**。优先级从高到低：
   榜单排名与得分 > 架构关键参数（总参数、激活参数、上下文长度）>
   能力对比结论 > 次要细节（文件体积、依赖版本号、硬件型号）
   句子放不下就舍弃低优先级的那个
4. **每句都要带数字或专有名词**，不要出现只有形容词的句子
5. 只写原文里有的内容，**不做任何推测、评价或补充背景**
6. 不要用「本文介绍了」「据报道」这类空话开头，直接说事实
7. 中文输出，60~120 字
8. 产品名、公司名、版本号、性能数字**原样保留**，不得四舍五入或改写

如果正文信息不足（只有标题），就基于标题写一句话，不要编造细节。"""


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
        description="1~2 句中文摘要，60~120 字，只陈述原文事实",
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


def build_messages(cluster: Cluster, *, instructions: str = "") -> list[ChatMessage]:
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

    prompt = SYSTEM_PROMPT + instruction_block(instructions)
    return [system(prompt), user("\n".join(lines))]


def summarize_cluster(
    cluster: Cluster, llm: LLMProvider, *, instructions: str = ""
) -> SummaryResult:
    """
    为一个事件生成摘要 / Summarise one event.

    失败时降级为标题，**不抛异常**——理由见模块文档。
    Failures degrade to the title rather than raising; see the module docstring.
    """
    try:
        out = llm.chat_json(
            build_messages(cluster, instructions=instructions), SummaryOut, temperature=0.3
        )
    except Exception as exc:  # noqa: BLE001 - 单条失败不能毁掉整期日报
        logger.warning("摘要失败，退回使用标题：%s —— %s", cluster.canonical.title[:40], exc)
        return SummaryResult(summary=cluster.canonical.title, tags=[], degraded=True)

    summary = out.summary.strip() or cluster.canonical.title
    return SummaryResult(summary=summary, tags=[t.strip() for t in out.tags if t.strip()])


def summarize_all(clusters: list[Cluster], llm: LLMProvider) -> list[SummaryResult]:
    """
    批量生成摘要 / Summarise a batch.

    **串行而不是并发**：DeepSeek 有速率限制，并发打过去换来的是 429 与重试，
    实际并不更快，还让费用与日志都难以追踪。一期日报几十条，串行完全可接受。
    Sequential rather than concurrent: DeepSeek rate-limits, so firing in parallel buys
    429s and retries rather than speed, while making cost and logs harder to follow. A
    few dozen entries per issue is perfectly acceptable serially.
    """
    results = [summarize_cluster(c, llm) for c in clusters]
    degraded = sum(1 for r in results if r.degraded)
    if degraded:
        logger.warning("摘要完成：%d 条，其中 %d 条降级为标题", len(results), degraded)
    else:
        logger.info("摘要完成：%d 条", len(results))
    return results


__all__ = [
    "MAX_BODY_CHARS",
    "SYSTEM_PROMPT",
    "SummaryOut",
    "SummaryResult",
    "build_messages",
    "summarize_all",
    "summarize_cluster",
]
