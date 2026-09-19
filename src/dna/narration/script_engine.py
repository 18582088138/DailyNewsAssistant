"""
生成 → 数字数 → 回炉 的那台引擎 / The generate-count-rewrite engine.

**验收看字数，秒数只记账。** 提示词里要求的单位与这里检查的单位是同一个，
所以差值是减法。先前验收看秒数、提示词说字数 —— 同一个字数在中英比例不同的
稿子上实测差 50%，于是合格的稿子被反复回炉，偏短的稿子静静通过。

回炉时把**上一稿原文**作为 assistant 消息带回去，再附上具体差值。
只发一句「太长了」而不给上一稿，模型会从头重写一篇完全不同的稿子。
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from dna.core.config import Profile, field_default
from dna.core.logging import get_logger
from dna.core.models import Article
from dna.llm.base import LLMProvider, assistant, system, user
from dna.narration.duration import char_feedback, count_units, estimate_seconds

logger = get_logger("narration.script_engine")

# ---------------------------------------------------------------------------
# 共用骨架 / the shared skeleton
# ---------------------------------------------------------------------------


def _generate(
    llm: LLMProvider,
    *,
    system_prompt: str,
    article: Article,
    schema: type[BaseModel],
    extract,
    low_units: int,
    high_units: int,
    label: str,
    lang: str = "zh",
    max_rewrites: int | None = None,
    body_chars: int | None = None,
) -> ScriptResult:
    """
    生成 + 数字数 + 回炉 / Generate, count, and rewrite if the length misses.

    **验收看字数，秒数只记账。** 提示词里要求的单位与这里检查的单位是同一个，
    所以差值是减法。先前验收看秒数，而提示词说的是字数——同一个字数在中英比例
    不同的稿子上实测差 50%，于是合格的稿子被反复回炉，偏短的稿子静静通过。
    Acceptance is on the unit count while the duration is only recorded. The prompt and
    this check speak one unit, so the delta is a subtraction. Gating on seconds while
    prompting in characters sent compliant drafts back and let short ones through.

    回炉时把**上一稿原文**作为 assistant 消息带回去，再附上具体差值。
    只发一句「太长了」而不给上一稿，模型会从头重写一篇完全不同的稿子，
    上一稿里写对的部分也一起丢了。
    The previous draft is sent back as an assistant message alongside the concrete delta.
    Saying only "too long" without the draft makes the model start over from scratch,
    discarding the parts that were already right.
    """
    messages = [system(system_prompt), user(article_block(article, body_chars=body_chars))]
    cap = DEFAULT_MAX_REWRITES if max_rewrites is None else max(0, max_rewrites)
    result: ScriptResult | None = None

    for attempt in range(cap + 1):
        calls = attempt + 1
        out = llm.chat_json(messages, schema, temperature=0.6)

        text, title, subtitle = extract(out)
        # 单位随语言走：中文数字符、英文数词。混用会得出「上一稿 900 字符，
        # 请删掉 300 words」这种自相矛盾的指令。
        # The unit follows the language; mixing them yields a self-contradicting delta.
        units = count_units(text, lang=lang)
        seconds = estimate_seconds(text)
        result = ScriptResult(
            text=text.strip(),
            seconds=seconds,
            calls=calls,
            within_target=True,
            title=title.strip(),
            subtitle=subtitle.strip(),
        )

        feedback = char_feedback(units, low_units, high_units, lang=lang)
        if feedback is None:
            logger.info(
                "%s文案完成：%d 字（目标 %d~%d），约 %.0f 秒，%d 次调用",
                label, units, low_units, high_units, seconds, calls,
            )
            return result

        if attempt == cap:
            # 试到上限仍不达标：**返回它而不是报错**。差十几个字的稿子仍然可用，
            # 人手动删两句就行；为此让整篇产物失败是不划算的。
            # Still off after the last attempt: return it rather than fail — a draft a
            # dozen characters out is usable with a manual trim.
            result.within_target = False
            logger.warning(
                "%s文案 %d 次仍未落入 %d~%d 字（实际 %d 字，约 %.0f 秒），按现状返回",
                label, calls, low_units, high_units, units, seconds,
            )
            return result

        logger.debug(
            "%s文案 %d 字，超出 %d~%d，回炉重写", label, units, low_units, high_units
        )
        messages = [*messages, assistant(text), user(feedback)]

    return result  # pragma: no cover - 循环必然返回


def article_block(article: Article, *, body_chars: int | None = None) -> str:
    """
    把文章组织成提示词里的输入块 / Lay the article out as the prompt's input block.

    **`longform.py` 也用这一份。** 先前两个模块各写了一份，而 longform 那份
    漏掉了「正文为空时禁止编造」这条——两份同样的东西一定会漂移，
    漂移的方向还偏偏是把安全约束丢掉。
    Shared with `longform.py`. The two modules previously kept separate copies and the
    long-form one had lost the "do not fabricate when the body is empty" clause: two
    copies of the same thing drift, and this one drifted by dropping a safety rule.

    正文截到 `SCRIPT_BODY_LIMIT`（6000 字）——上限的取值理由见该常量旁的注释。
    The body is capped at `SCRIPT_BODY_LIMIT`; see that constant for why it sits where it does.
    """
    lines = [f"标题：{article.title}"]
    if article.author:
        lines.append(f"作者：{article.author}")

    body = article.text[: body_chars or SCRIPT_BODY_LIMIT].strip()
    if body:
        lines.append(f"\n正文：\n{body}")
    else:
        lines.append("\n（正文抓取失败，只有标题可用——请基于标题写，不要编造任何细节与数字）")

    return "\n".join(lines)

@dataclass
class ScriptResult:
    """
    一次文案生成的结果 / The result of one script generation.

    带上 `seconds` 与 `calls`：前者让调用方知道是否达标，后者让费用可见——
    回炉两次意味着这一篇花了三次调用。
    `seconds` tells the caller whether the target was met; `calls` keeps the cost
    visible, since two rewrites mean this piece cost three calls.
    """

    text: str
    seconds: float
    calls: int = 1
    within_target: bool = True
    title: str = ""
    subtitle: str = ""

    @property
    def chars(self) -> int:
        return len(self.text)

# 回炉重写次数上限的**兜底值** / fallback cap on rewrites
#
# 真正的取值来自 `Profile.copy_max_rewrites`（`config/profile.yaml`）。
# 这里曾经是写死的 1，而 `profile.yaml` 的注释写的是「文案最多 2 次」——
# 同一件事两个答案，而读文档的人永远不会发现代码少给了一次机会。
# 每次重写都是一次计费调用，所以它必须是个能调的旋钮，不是常量。
# Each rewrite is a billed call, so this belongs in config rather than here.
DEFAULT_MAX_REWRITES: int = field_default(Profile, "copy_max_rewrites")

# 喂给模型的正文上限 / how much body text is sent
#
# 比摘要节点的 3000 字宽一倍，因为两者的成本结构完全不同 / Twice the summariser's
# 3000, because the cost structures differ:
#     摘要是**每条都跑**的，一期几十条，正文上限直接乘以条目数；
#     文案是**单篇按需**跑的，宽一点只影响这一次调用。
#     Summarisation runs per entry — dozens per issue — so the cap is multiplied by the
#     entry count. Scripts are produced one article at a time on request, where a wider
#     cap costs one call's input.
#
# 3000 字为什么不够：实测一篇 4069 字的技术稿，被切掉的最后 1069 字里有采样参数
# （temperature / top_p / Think Max 需要 384K 上下文）和一条关键局限——官方没给
# Jinja chat template，「能跑」和「跑对」是两回事。这些正是「必须说局限」要用的料，
# 而模型根本没看到。
# A measured 4069-character article lost sampling parameters and a key caveat — no
# official Jinja chat template, so "runs" and "runs correctly" differ — in the truncated
# tail. That is precisely the material the "state the limitations" rule needs, and the
# model never saw it.
# 指向模型字段，不再自己写一遍数字（同一个参数两处写死是本项目反复出过的问题）
SCRIPT_BODY_LIMIT: int = field_default(Profile, "script_body_chars")
