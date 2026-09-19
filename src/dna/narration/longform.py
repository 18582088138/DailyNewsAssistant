"""
长文案 / Long-form scripts（10~15 分钟）.

两种形式 / Two modes:
    **专题**（feature）  —— 单角色讲述
    **访谈**（interview）—— 主持人 + 嘉宾对谈

为什么不能一次生成 / Why this cannot be one call:
    10~15 分钟约合 2700~4000 字，远超单次输出的可靠范围。硬要一次生成，
    模型会在中途丢失结构、重复前面说过的话、越写越水——而「不要白话」
    恰恰是这批文案的核心要求。
    Two to four thousand characters is well past what one call produces reliably: the
    model loses the structure part-way, repeats itself and thins out — which is precisely
    the failure this format cannot afford.

三步 / Three steps:
    1. 出提纲（1 次调用）—— 5~8 个章节，每节有要点与目标字数
    2. 逐节展开（每节 1 次）—— 带上一节结尾做衔接，保证不断裂
    3. 拼装 —— 不做全文重写：太贵，而且分节写好的稿子并不需要

时长跟文章体量走 / Length follows the source:
    目标字数由正文长度推导，**不足就写短**。宁可产出 8 分钟也不注水凑 15 分钟——
    注水恰恰是「白话」的来源，模型会用背景铺垫和重复论述填满剩下的时间。
    The target scales with the body and falls short when the material does. Eight honest
    minutes beat fifteen padded ones: padding is where vague filler comes from, since the
    model reaches for background and repetition to fill the gap.

角色轮次 JSON / The speaker-turn JSON:
    产物同时写一份 `.json`，按发言人切成 turns，供 P6/P7 的 TTS 分配音色。
    专题模式也写同样结构（全部 `narrator`），**格式统一**，TTS 侧不必分两条路径。
    A `.json` sibling splits the script into speaker turns so the TTS stage can assign
    voices. Feature mode emits the same structure with a single `narrator` speaker, so
    the TTS side never needs two code paths.
"""

from __future__ import annotations

from dna.core.logging import get_logger
from dna.core.models import Article
from dna.core.prompts import load_prompt, render_prompt
from dna.llm.base import LLMProvider, system, user
from dna.narration.duration import estimate_seconds, prompt_char_budget
from dna.narration.longform_models import (
    MAX_SECTIONS,
    MIN_SECTIONS,
    SPEAKERS,
    LongformMode,
    LongformResult,
    OutlinePlan,
    SectionPlan,
    SectionScript,
    Turn,
)
from dna.narration.script_builder import article_block, instruction_block, rules_for

logger = get_logger("narration.longform")

# 正文短于这个字数就不做长文案 / below this the source cannot carry a long script
# 800 字的资讯撑不起 10 分钟。硬做出来的一定是注水稿，既花钱又没法用。
# An 800-character item cannot carry ten minutes; forcing it produces padding that costs
# money and cannot be used.
MIN_BODY_FOR_LONGFORM = 800

# 目标字数 = 正文字数 × 这个系数，再截到上限
# 口播稿比原文长是正常的（要展开、要解释），但超过 1.5 倍基本就是在注水。
# A script longer than its source is normal — it expands and explains — but beyond about
# 1.5× it is padding.
EXPANSION_RATIO = 1.2

# 目标时长区间（秒）/ the duration window
# **只有这一处定义长度**。字数上下限由它换算而来，不另设常量——
# 同一件事的两种单位各写一份，迟早会漂移到互相矛盾。
# This is the single definition of length. The character bounds are derived from it
# rather than declared separately: two units for one quantity eventually contradict.
MIN_TARGET_SECONDS = 300.0   # 5 分钟：再短就不该叫「长文案」
MAX_TARGET_SECONDS = 900.0   # 15 分钟：用户给的上限


# 提示词正文在 `config/prompts/` / The prompt texts live under that directory
#     longform_outline.md  主块 + `@shape.feature` / `@shape.interview`
#     longform_section.md  主块 + `@context` / `@role.feature` / `@role.interview`
#     _shared/language_directive.en.md
#
# 任务指令本身保持中文，语言只通过 `{{rules}}` 与 `{{language_directive}}`
# 两个注入点切换——所以这两个文件不带语言后缀。
# The task instructions stay Chinese; the output language switches only through those two
# injection points, so neither file carries a language suffix.
PROMPT_OUTLINE = "longform_outline"
PROMPT_SECTION = "longform_section"
PROMPT_LANGUAGE_DIRECTIVE = "_shared/language_directive.en"



def unit_word(lang: str) -> str:
    """提示词里说「多少字」还是「多少 words」/ The unit the prompt asks for."""
    return "字" if lang == "zh" else "words"


def language_directive(lang: str) -> str:
    """
    输出语言的硬性声明 / The hard statement of output language.

    长文案是**分十几次调用**拼起来的，每一节都是独立的一次请求。
    只在第一节说一次「用英文写」，后面几节的模型看不到那句话，会跟着中文原文
    一起滑回中文——拼出来就是中英夹杂的半成品，而这时钱已经花完了。
    所以每一节的提示词都重复一遍，这不是冗余。
    A long-form script is assembled from a dozen independent calls. Stating the output
    language once would leave every later section unaware of it, and each would drift back
    to the source article's language — yielding a half-Chinese script after the money is
    already spent. Repeating it per section is not redundancy.
    """
    if lang == "zh":
        return ""
    return "\n\n" + load_prompt(PROMPT_LANGUAGE_DIRECTIVE)


def can_build_longform(
    article: Article, *, min_body_chars: int | None = None
) -> tuple[bool, str]:
    """
    判断这篇能不能做长文案 / Whether this article can carry a long-form script.

    返回 `(可以吗, 原因)`。**在花钱之前判断**——不够料的文章做出来一定是注水稿，
    而长文案是最贵的产物（提纲 1 次 + 每节 1 次）。
    Returns whether it can, and why not. Checked before spending anything: an
    insufficient source can only yield padding, and this is the most expensive
    production there is.
    """
    floor = min_body_chars or MIN_BODY_FOR_LONGFORM
    if len(article.text) < floor:
        return False, (
            f"正文只有 {len(article.text)} 字，不足 {floor} 字，"
            f"撑不起 10 分钟的长文案。强行生成只会得到注水稿。"
        )
    return True, ""


def plan_target_seconds(
    article: Article,
    *,
    low: float = MIN_TARGET_SECONDS,
    high: float = MAX_TARGET_SECONDS,
    expansion_ratio: float | None = None,
) -> float:
    """
    按正文体量推导目标时长 / Derive the target duration from the source.

    **在时长空间里推导，而不是字符数空间。** 两者不是一回事：技术稿里
    `UD-Q8_K_XL`、`DeepSeek-V4-Flash-0731` 这类标识符一个就占十几个字符，
    但念出来的时间远不成比例。按字符数推导会让「4000 字的原文」和
    「4000 字的成稿」看起来对等，实际口播时长差出一倍。
    Derived in duration space rather than character space, because the two diverge:
    identifiers like `UD-Q8_K_XL` occupy a dozen characters each while taking nowhere
    near proportional time to read. Scaling by character count makes a 4000-character
    source and a 4000-character script look equivalent when their spoken lengths differ
    by a factor of two.

    **不硬凑 10~15 分钟**：原文短就写短。宁可产出 8 分钟也不注水凑长度——
    注水恰恰是「白话」的来源。
    The window is not forced: a short source yields a short script. Eight honest minutes
    beat fifteen padded ones, and padding is where vague filler comes from.
    """
    source_seconds = estimate_seconds(article.text)
    ratio = expansion_ratio or EXPANSION_RATIO
    return max(low, min(source_seconds * ratio, high))


def plan_target_chars(
    article: Article,
    *,
    low: float = MIN_TARGET_SECONDS,
    high: float = MAX_TARGET_SECONDS,
    lang: str = "zh",
    expansion_ratio: float | None = None,
) -> int:
    """
    目标字数 / The character target handed to the prompt.

    由目标时长换算而来。给提示词的必须是**字数**——模型数不了秒数，
    但能大致数字；而验收看的是秒数，所以两者必须由同一个换算关系连接。
    Converted from the target duration. The prompt must speak in characters — a model
    cannot count seconds but can approximate a character budget — while acceptance is
    measured in seconds, so a single conversion must connect the two.

    走 `prompt_char_budget` 而不是 `target_chars` / Via `prompt_char_budget`:
        按纯中文语速换算会**系统性少要**：实测一篇 3900 字的访谈稿只有 443 秒，
        因为 `UD-Q8_K_XL`、`DeepSeek-V4-Flash-0731` 这类标识符占字符多、占时长少。
        按混排密度放大后，15 分钟的目标才对应到能真正念满 15 分钟的字数。
        Converting at the all-Chinese rate systematically under-asks: a measured
        3900-character interview ran only 443 seconds, because identifiers cost characters
        far more than they cost time. Scaling to mixed density makes a fifteen-minute
        target correspond to a script that actually fills fifteen minutes.
    """
    return prompt_char_budget(
        plan_target_seconds(article, low=low, high=high), lang=lang
    )


def build_longform(
    article: Article,
    llm: LLMProvider,
    *,
    mode: LongformMode = LongformMode.FEATURE,
    low: float = MIN_TARGET_SECONDS,
    high: float = MAX_TARGET_SECONDS,
    lang: str = "zh",
    instructions: str = "",
    expansion_ratio: float | None = None,
    sections: tuple[int, int] | None = None,
) -> LongformResult:
    """
    生成长文案 / Build a long-form script.

    参数 / Args:
        low / high: 目标时长窗口（秒），来自 `Profile.longform_duration_seconds`。
            `low` 是**下限而不是目标**——原文短就写短，见 `plan_target_seconds`。
            The window in seconds, from the profile. `low` is a floor rather than a
            target: a short source yields a short script.

    抛出 / Raises:
        ValueError: 文章体量不足（先用 `can_build_longform` 判断可避免）
    """
    ok, reason = can_build_longform(article)
    if not ok:
        raise ValueError(reason)

    total_target = plan_target_chars(
        article, low=low, high=high, lang=lang, expansion_ratio=expansion_ratio
    )
    logger.info(
        "长文案开始：%s 模式 / %s，目标 %d 字（约 %.0f 分钟）",
        lang,
        mode,
        total_target,
        plan_target_seconds(
            article, low=low, high=high, expansion_ratio=expansion_ratio
        ) / 60,
    )

    outline = _build_outline(
        article, llm, mode=mode, total_target=total_target, lang=lang,
        instructions=instructions, sections=sections,
    )
    result = LongformResult(mode=mode, sections=outline.sections, calls=1)

    previous_tail = ""
    for index in range(1, len(outline.sections) + 1):
        turns = _expand_section(
            article, llm, mode=mode, sections=outline.sections,
            index=index, previous_tail=previous_tail, lang=lang,
            instructions=instructions,
        )
        result.turns.extend(turns)
        result.calls += 1
        previous_tail = turns[-1].text[-120:] if turns else ""

    result.seconds = estimate_seconds(result.text)
    logger.info(
        "长文案完成：%d 节，%d 字，约 %.0f 分钟，%d 次调用",
        len(outline.sections),
        result.chars,
        result.seconds / 60,
        result.calls,
    )
    return result


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _build_outline(
    article: Article,
    llm: LLMProvider,
    *,
    mode: LongformMode,
    total_target: int,
    lang: str = "zh",
    instructions: str = "",
    sections: tuple[int, int] | None = None,
) -> OutlinePlan:
    """
    第一步：出提纲 / Step one: the outline.

    提纲里带每节的目标字数，展开时按它写——不给字数的话各节长短会差出好几倍，
    拼起来读着极不均匀。
    Each section carries its own target length. Without one the sections come out wildly
    uneven and the assembled script reads lopsided.
    """
    shape = load_prompt(PROMPT_OUTLINE, f"shape.{mode.value}")

    prompt = render_prompt(
        PROMPT_OUTLINE,
        rules=rules_for(lang),
        language_directive=language_directive(lang),
        total_target=total_target,
        unit=unit_word(lang),
        shape=shape,
        min_sections=(sections or (MIN_SECTIONS, MAX_SECTIONS))[0],
        max_sections=(sections or (MIN_SECTIONS, MAX_SECTIONS))[1],
    )
    prompt += instruction_block(instructions, lang)

    return llm.chat_json(
        [system(prompt), user(article_block(article))], OutlinePlan, temperature=0.5
    )


def _expand_section(
    article: Article,
    llm: LLMProvider,
    *,
    mode: LongformMode,
    sections: list[SectionPlan],
    index: int,
    previous_tail: str,
    lang: str = "zh",
    instructions: str = "",
) -> list[Turn]:
    """
    第二步：展开一节 / Step two: expand one section.

    给模型两样上下文 / The model gets two kinds of context:

    1. **上一节的结尾**（120 字）——保证接缝处不断裂。上一节刚说完某个数字，
       这一节又从头介绍一遍同一件事，读起来就是两篇稿子拼在一起。
       The previous section's tail, so the seam does not break: one section ends on a
       figure and the next reintroduces the same thing from scratch.

    2. **完整提纲**，并标出哪几节已经讲过、哪几节留给后面。
       **只给上一节的结尾是不够的**——真机实测（CES 那篇，7 节）第 7 节把第 6 节
       的产品几乎逐个重讲了一遍，9 个实体全部重复。因为写第 7 节时模型只看见
       第 6 节最后 120 字，不知道前面六节各自覆盖了什么，于是把「其他创新硬件」
       理解成「再说一遍我知道的硬件」。
       The full outline, marking which sections are done and which are still to come.
       The previous tail alone is not enough: in a measured seven-section run the last
       section restated the previous one almost entity for entity, because all it could
       see was the preceding 120 characters and it had no idea what the earlier six had
       already covered.
    """
    section = sections[index - 1]
    total = len(sections)

    # 两段片段都以 `\n` 起头拼进主块，与 `@role.*` / `@context` 两个块里
    # 逐字节对应；空的 previous_tail 不产生 context 段。
    role_rule = "\n" + load_prompt(PROMPT_SECTION, f"role.{mode.value}")
    context = (
        "\n" + render_prompt(PROMPT_SECTION, "context", previous_tail=previous_tail)
        if previous_tail
        else ""
    )

    prompt = render_prompt(
        PROMPT_SECTION,
        rules=rules_for(lang),
        language_directive=language_directive(lang),
        index=index,
        total=total,
        target_chars=section.target_chars,
        unit=unit_word(lang),
        outline_map=_outline_map(sections, index),
        points="\n".join(f"- {p}" for p in section.points),
        context=context,
        role_rule=role_rule,
    )
    prompt += instruction_block(instructions, lang)

    out = llm.chat_json(
        [system(prompt), user(article_block(article))], SectionScript, temperature=0.6
    )

    allowed = set(SPEAKERS[mode])
    turns: list[Turn] = []
    for turn in out.turns:
        speaker = turn.speaker.strip().lower()
        if speaker not in allowed:
            # 模型偶尔会自创角色名（「专家」「记者」）。归到默认角色而不是丢掉——
            # 内容是好的，只是标签错了；丢掉会让这一节出现空洞。
            # The model occasionally invents a speaker. The turn is reassigned rather
            # than dropped: the content is fine and only the label is wrong, whereas
            # dropping it would leave a hole in the section.
            speaker = "guest" if mode is LongformMode.INTERVIEW else "narrator"
        turns.append(Turn(speaker=speaker, text=turn.text.strip()))

    return turns


def _outline_map(sections: list[SectionPlan], current: int) -> str:
    """
    把提纲画成「已讲过 / 现在写这节 / 留给后面」的地图 / The outline as a map.

    纯函数，可单测。**每节的边界要在提示词里可见**——模型看不见全局时，
    「其他创新硬件」这种节名会被理解成「再说一遍我知道的硬件」。
    A pure, testable function. The boundaries have to be visible in the prompt: without
    the global picture a section titled "other hardware" reads as "restate what I know".
    """
    lines = []
    for i, section in enumerate(sections, start=1):
        if i < current:
            marker = "（已讲过）"
        elif i == current:
            marker = "　← 现在写这节"
        else:
            marker = "（留给后面）"
        lines.append(f"{i}. {section.title}{marker}")
    return "\n".join(lines)


__all__ = [
    "MAX_SECTIONS",
    "MAX_TARGET_SECONDS",
    "MIN_BODY_FOR_LONGFORM",
    "MIN_SECTIONS",
    "MIN_TARGET_SECONDS",
    "PROMPT_LANGUAGE_DIRECTIVE",
    "PROMPT_OUTLINE",
    "PROMPT_SECTION",
    "SPEAKERS",
    "LongformMode",
    "LongformResult",
    "OutlinePlan",
    "SectionPlan",
    "Turn",
    "build_longform",
    "can_build_longform",
    "plan_target_chars",
    "plan_target_seconds",
]
