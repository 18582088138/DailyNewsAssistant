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

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, Field

from dna.core.logging import get_logger
from dna.core.models import Article
from dna.llm.base import LLMProvider, system, user
from dna.narration.duration import estimate_seconds, prompt_char_budget
from dna.narration.script_builder import PROFESSIONALISM, article_block

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

# 章节数量范围 / how many sections
MIN_SECTIONS = 4
MAX_SECTIONS = 8


class LongformMode(StrEnum):
    """长文案形式 / Long-form mode."""

    FEATURE = "feature"  # 专题：单角色
    INTERVIEW = "interview"  # 访谈：双角色


# 角色名 / speaker labels
SPEAKERS = {
    LongformMode.FEATURE: {"narrator": "旁白"},
    LongformMode.INTERVIEW: {"host": "主持人", "guest": "嘉宾"},
}


class SectionPlan(BaseModel):
    """提纲里的一节 / One section of the outline."""

    title: str = Field(max_length=40, description="小节标题，用于人工核对结构，不会被念出来")
    points: list[str] = Field(
        min_length=1, max_length=5, description="本节要讲的要点，每条一句话"
    )
    # 上限跟着 prompt_char_budget 走：15 分钟的混排密度预算约 6000 字，
    # 分 4 节就是每节 1500。上限压在 1200 会让模型的规划被 Schema 反复驳回，
    # 白花修复重试的调用。
    # The ceiling tracks `prompt_char_budget`: a fifteen-minute budget is around 6000
    # characters at mixed density, or 1500 across four sections. Capping at 1200 makes the
    # schema reject the model's own plan and burns repair retries.
    target_chars: int = Field(ge=150, le=2000, description="本节目标字数")


class OutlinePlan(BaseModel):
    """长文案提纲 / The long-form outline."""

    sections: list[SectionPlan] = Field(min_length=1, max_length=MAX_SECTIONS)


class Turn(BaseModel):
    """一次发言 / One speaker turn."""

    speaker: str = Field(description="feature 模式固定 narrator；interview 模式为 host 或 guest")
    text: str = Field(min_length=1)


class SectionScript(BaseModel):
    """一节展开后的稿子 / One expanded section."""

    turns: list[Turn] = Field(min_length=1)


@dataclass
class LongformResult:
    """一篇长文案的结果 / The result of one long-form script."""

    mode: LongformMode
    turns: list[Turn] = field(default_factory=list)
    sections: list[SectionPlan] = field(default_factory=list)
    calls: int = 0
    seconds: float = 0.0

    @property
    def text(self) -> str:
        """拼成人读的全文 / The whole script as a person reads it."""
        labels = SPEAKERS[self.mode]
        if self.mode is LongformMode.FEATURE:
            return "\n\n".join(t.text for t in self.turns)
        return "\n\n".join(
            f"**{labels.get(t.speaker, t.speaker)}：** {t.text}" for t in self.turns
        )

    @property
    def chars(self) -> int:
        return sum(len(t.text) for t in self.turns)

    def to_json_dict(self) -> dict:
        """
        导出给 TTS 的结构 / The structure the TTS stage consumes.

        两种模式产出同一种结构，只是 speaker 取值不同——TTS 侧照着 speaker
        分配音色即可，不必知道这篇是专题还是访谈。
        Both modes emit the same shape and differ only in the speaker values, so the TTS
        stage assigns voices without needing to know which mode produced the script.
        """
        return {
            "mode": str(self.mode),
            "speakers": SPEAKERS[self.mode],
            "est_seconds": self.seconds,
            "turns": [{"speaker": t.speaker, "text": t.text} for t in self.turns],
        }


def can_build_longform(article: Article) -> tuple[bool, str]:
    """
    判断这篇能不能做长文案 / Whether this article can carry a long-form script.

    返回 `(可以吗, 原因)`。**在花钱之前判断**——不够料的文章做出来一定是注水稿，
    而长文案是最贵的产物（提纲 1 次 + 每节 1 次）。
    Returns whether it can, and why not. Checked before spending anything: an
    insufficient source can only yield padding, and this is the most expensive
    production there is.
    """
    if len(article.text) < MIN_BODY_FOR_LONGFORM:
        return False, (
            f"正文只有 {len(article.text)} 字，不足 {MIN_BODY_FOR_LONGFORM} 字，"
            f"撑不起 10 分钟的长文案。强行生成只会得到注水稿。"
        )
    return True, ""


def plan_target_seconds(
    article: Article,
    *,
    low: float = MIN_TARGET_SECONDS,
    high: float = MAX_TARGET_SECONDS,
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
    return max(low, min(source_seconds * EXPANSION_RATIO, high))


def plan_target_chars(
    article: Article,
    *,
    low: float = MIN_TARGET_SECONDS,
    high: float = MAX_TARGET_SECONDS,
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
    return prompt_char_budget(plan_target_seconds(article, low=low, high=high))


def build_longform(
    article: Article,
    llm: LLMProvider,
    *,
    mode: LongformMode = LongformMode.FEATURE,
    low: float = MIN_TARGET_SECONDS,
    high: float = MAX_TARGET_SECONDS,
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

    total_target = plan_target_chars(article, low=low, high=high)
    logger.info(
        "长文案开始：%s 模式，目标 %d 字（约 %.0f 分钟）",
        mode,
        total_target,
        plan_target_seconds(article, low=low, high=high) / 60,
    )

    outline = _build_outline(article, llm, mode=mode, total_target=total_target)
    result = LongformResult(mode=mode, sections=outline.sections, calls=1)

    previous_tail = ""
    for index in range(1, len(outline.sections) + 1):
        turns = _expand_section(
            article, llm, mode=mode, sections=outline.sections,
            index=index, previous_tail=previous_tail,
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
    article: Article, llm: LLMProvider, *, mode: LongformMode, total_target: int
) -> OutlinePlan:
    """
    第一步：出提纲 / Step one: the outline.

    提纲里带每节的目标字数，展开时按它写——不给字数的话各节长短会差出好几倍，
    拼起来读着极不均匀。
    Each section carries its own target length. Without one the sections come out wildly
    uneven and the assembled script reads lopsided.
    """
    shape = (
        "这是一篇单人讲述的专题稿。"
        if mode is LongformMode.FEATURE
        else "这是一期双人访谈：主持人负责提问、追问和转场，嘉宾负责给出技术内容。"
    )

    prompt = f"""{PROFESSIONALISM}

任务：为下面这篇资讯规划一篇 **{total_target} 字**的长文案提纲。{shape}

要求：
- 分 {MIN_SECTIONS}~{MAX_SECTIONS} 节，各节 target_chars 之和接近 {total_target}
- **按内容的逻辑分节**（背景问题 → 方法 → 数据 → 局限 → 影响），
  不要按原文段落顺序切
- 每节的 points 要具体：写「解释 2TP×4USP 混合并行为什么能降显存」，
  不要写「介绍技术方案」
- **提纲要把原文的关键事实分配完**。原文里的每一个重要数字、方法名、对比结论
  都应该出现在某一节的 points 里。清点一遍，别把内容留在原文里没用上
- **各节内容必须互斥**：同一个产品、同一项技术、同一批数据只能归给一节。
  不要出现「智能眼镜」和「其他创新硬件」这样边界模糊的两节——
  它们会各自把同一批产品讲一遍
- **不要用「其他」「其余」「补充」命名任何一节**。这类节没有明确边界，
  写的时候必然去重复前面已经讲过的内容
- **必须有一节讲局限、代价、前提条件或未解决的问题**。只讲好处的长稿没有
  可信度，听众听十分钟不是为了听广告。原文没有明说局限时，就讲这批技术/产品
  共同的短板或尚未解决的问题——但只能基于原文里的事实，不要凭空推测
- 不要规划「总结回顾」这种把前面重说一遍的节——那是注水
- 不要规划「背景介绍」占满一节。背景最多一两句带过，长稿的价值在细节不在铺垫"""

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
    if mode is LongformMode.INTERVIEW:
        role_rule = """
- 输出为对话轮次，speaker 只能是 `host`（主持人）或 `guest`（嘉宾）
- **主持人只提问、追问、转场，不讲技术内容**；技术内容全部由嘉宾说
- 主持人的问题要具体，追问要针对嘉宾刚说的那句话
- **不要互相吹捧**。不要写「您说得太好了」「这个问题问得很专业」——
  这类话占时长、没信息，而且一听就假
- 本节 2~5 轮对话"""
    else:
        role_rule = """
- 输出为单个轮次，speaker 固定为 `narrator`
- 连贯成段，不要分小标题"""

    context = f"\n上一节的结尾是：「…{previous_tail}」\n请自然衔接，不要重复上面已经说过的内容。" if previous_tail else ""

    prompt = f"""{PROFESSIONALISM}

任务：写长文案的第 {index}/{total} 节，约 **{section.target_chars} 字**。

全文提纲（**只写标着「← 现在写这节」的那一节**）：
{_outline_map(sections, index)}

本节要点：
{chr(10).join(f"- {p}" for p in section.points)}
{context}
边界要求（**重要**）：
- 标着「已讲过」的内容**不要再讲**，一个产品名、一个数字都不要重复
- 标着「留给后面」的内容**不要提前讲**，那几节会自己讲
- 本节的料不够写满 {section.target_chars} 字时，去原文里找本节主题下更细的数据，
  **不要去写别节的内容凑长度**

额外要求：{role_rule}
- **本节要点必须全部讲到，每个要点都要落到原文的具体数字或方法名上**。
  写不到 {section.target_chars} 字就说明细节没展开够，去原文里找数据，
  不要用背景铺垫和形容词凑长度
- 只写本节内容，不要写小标题，不要写「接下来我们看」这种过渡到别节的话
- 这是要被念出来的，写口语，但**不要白话**"""

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
