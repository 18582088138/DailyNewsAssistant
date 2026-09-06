"""
双语翻译节点 / Bilingual translation node.

把已成稿的中文标题与摘要译成英文，回填进同一份 Digest。
Translates finished Chinese titles and summaries into English, filled back into the same
digest.

**双语是渲染期的事，不是采集期的事。** 因此这个节点：
    - 输入是「已经写好的中文」，不是原始正文——译摘要比重新用英文写一遍便宜得多，
      而且中英两版说的一定是同一件事
    - 可以对**任何一份已有的 `_digest.json`** 事后补跑，不用重新采集或重新摘要
    Bilingual output is a render-time concern. This node therefore takes already-written
    Chinese as input rather than the raw body: translating a summary is much cheaper than
    writing a second one in English, and it guarantees the two versions say the same
    thing. It can also be run later against any existing `_digest.json` without
    re-collecting or re-summarising anything.

批量而不是逐条 / Batched rather than per-entry:
    一次把十几条一起译，一次调用抵十几次。翻译任务彼此独立、上下文短，
    合并不会降低质量，但能显著压低单条成本与总耗时。
    A dozen entries go in one call instead of a dozen. Translation tasks are independent
    and short, so batching does not hurt quality while markedly cutting per-entry cost
    and total latency.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from dna.core.logging import get_logger
from dna.llm.base import ChatMessage, LLMProvider, system, user

logger = get_logger("pipeline.translate")

# 一次翻译多少条 / entries per call
# 太大容易超出输出长度并让模型漏译；10 条是质量与成本的折中。
# Too large risks hitting the output limit and having entries silently skipped; ten is
# the compromise between quality and cost.
BATCH_SIZE = 10

SYSTEM_PROMPT = """You are a bilingual editor for a daily AI-news digest.

Translate each Chinese entry into natural, publication-ready English.

Rules:
1. Translate meaning, not words — the result must read as if originally written in English
2. Keep product names, company names, version numbers and metrics EXACTLY as given
   (GPT-4o stays GPT-4o; 通义千问 becomes Qwen; 智谱 becomes Zhipu AI)
3. **Be shorter than the Chinese.** One or two sentences, at most 45 words.
   English needs more characters than Chinese to say the same thing, so a faithful
   translation always renders longer — and these summaries sit in a fixed-width
   column where the overflow is what the reader actually notices.
   Compress by dropping hedges and connectives, never by dropping a figure:
   every number, model name and benchmark in the Chinese must survive
4. Titles stay headline-style: no trailing period, no "The" padding
5. Do NOT add information the Chinese does not contain, and do NOT omit any

Return one object per input entry, in the same order, keeping each entry's id."""


class TranslatedEntry(BaseModel):
    """一条翻译结果 / One translated entry."""

    id: str = Field(description="必须与输入的 id 完全一致 / must match the input id exactly")
    title_en: str = Field(min_length=1, max_length=300)
    # 400 而不是 800：上限是**这条规则的最后一道防线**。提示词里说了「至多 45 词」，
    # 但提示词是建议，schema 是约束——放着一个两倍于目标的上限，等于告诉模型
    # 写到 800 也算合格。
    # The ceiling is the last line of defence for the length rule: leaving it at twice the
    # target tells the model that twice the target passes.
    summary_en: str = Field(min_length=1, max_length=400)


class TranslationOut(BaseModel):
    """一批翻译的结构化输出 / Structured output of one translation batch."""

    entries: list[TranslatedEntry] = Field(default_factory=list)


def build_messages(
    items: list[tuple[str, str, str]], *, instructions: str = ""
) -> list[ChatMessage]:
    """
    构造提示词 / Build the prompt.

    参数 / Args:
        items: [(id, 中文标题, 中文摘要), …]

    纯函数，测试重点。id 一并送进去并要求原样返回，是为了让结果能可靠地对回原条目——
    只靠顺序对齐的话，模型漏译一条就会导致后面全部错位，而且错位不会报错。
    A pure function and the main test target. The id is sent and required back verbatim
    so results can be matched reliably: relying on order alone means one skipped entry
    silently shifts every following one, with no error raised.
    """
    lines = []
    for entry_id, title, summary in items:
        lines.append(f"[id: {entry_id}]\n标题：{title}\n摘要：{summary}\n")

    from dna.narration.script_builder import instruction_block

    prompt = SYSTEM_PROMPT + instruction_block(instructions, "en")
    return [system(prompt), user("\n".join(lines))]


def translate_batch(
    items: list[tuple[str, str, str]], llm: LLMProvider, *, instructions: str = ""
) -> dict[str, tuple[str, str]]:
    """
    翻译一批条目 / Translate one batch.

    返回 / Returns:
        {id: (title_en, summary_en)}；失败或漏译的 id **不会出现在结果里**

    调用方据此判断哪些没译成，而不是拿到一个看起来完整、实则掺了空串的字典。
    The caller can therefore see which entries failed, rather than receiving a
    seemingly complete mapping padded with empty strings.
    """
    if not items:
        return {}

    try:
        out = llm.chat_json(
            build_messages(items, instructions=instructions), TranslationOut, temperature=0.2
        )
    except Exception as exc:  # noqa: BLE001 - 翻译失败只影响英文版，中文版照常
        logger.warning("翻译失败（%d 条），本批跳过英文版：%s", len(items), exc)
        return {}

    wanted = {entry_id for entry_id, _, _ in items}
    result: dict[str, tuple[str, str]] = {}

    for entry in out.entries:
        if entry.id not in wanted:
            # 模型编了一个不存在的 id，丢掉——把它塞进去会污染别的条目
            # The model invented an id; dropping it, since keeping it would corrupt
            # another entry.
            logger.debug("翻译返回了未知 id，忽略：%s", entry.id)
            continue
        result[entry.id] = (entry.title_en.strip(), entry.summary_en.strip())

    missing = wanted - result.keys()
    if missing:
        logger.warning("翻译漏了 %d 条，这些条目将没有英文版", len(missing))

    return result


def translate_all(
    items: list[tuple[str, str, str]],
    llm: LLMProvider,
    *,
    batch_size: int = BATCH_SIZE,
) -> dict[str, tuple[str, str]]:
    """
    分批翻译全部条目 / Translate every entry, batch by batch.

    某一批失败不影响其余批次——十几条里翻车一批，另外几批的英文版仍然可用。
    A failing batch does not affect the others: one bad batch still leaves the rest of
    the English version usable.
    """
    result: dict[str, tuple[str, str]] = {}
    for start in range(0, len(items), batch_size):
        result.update(translate_batch(items[start : start + batch_size], llm))

    logger.info("翻译完成：%d / %d 条拿到英文版", len(result), len(items))
    return result


def translate_text(text: str, llm: LLMProvider, *, kind: str = "段落") -> str | None:
    """
    翻译一段独立文本（趋势综述用）/ Translate one standalone passage, used for the trend note.

    失败返回 None 而不是原文：把中文塞进英文版里比缺一段更糟，
    读者会以为是排版事故而不是翻译缺失。
    Returns None rather than the original on failure: leaving Chinese inside the English
    edition is worse than omitting the passage, since it reads as a layout accident
    rather than a missing translation.
    """
    if not text.strip():
        return None

    messages = [
        system(SYSTEM_PROMPT),
        user(f"Translate this {kind} into English. Return only the translation.\n\n{text}"),
    ]
    try:
        return llm.chat_text(messages, temperature=0.2).strip() or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("整段翻译失败，英文版将缺少该段：%s", exc)
        return None


__all__ = [
    "BATCH_SIZE",
    "SYSTEM_PROMPT",
    "TranslatedEntry",
    "TranslationOut",
    "build_messages",
    "translate_all",
    "translate_batch",
    "translate_text",
]
