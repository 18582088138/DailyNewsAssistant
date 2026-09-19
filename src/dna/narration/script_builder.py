"""
文案构建 / Script construction.

短视频稿（25~35s）与口播稿（1~2min）的生成。长文案（10~15min）在 `longform.py`，
因为它需要分段生成，结构上和这两个不是一回事。
Short-video (25–35 s) and voice-over (1–2 min) scripts. The long-form script lives in
`longform.py`: it needs sectioned generation and is structurally a different job.

两个构建器共用一套骨架 / Both builders share one skeleton:
    构造提示词 → 调 LLM → 量时长 → 超区间就带着具体差值回炉 → 最多重写 2 次
    Build the prompt, call the LLM, measure the duration, send it back with a concrete
    delta when it misses the window, at most twice.

核心要求是信息密度 / The governing requirement is information density:
    时长固定（平台限制，发布时还会加速播放），所以稿子的成败在于**同样的秒数里
    装进多少事实**。「不要白话」不是用词问题——一句没有数字、没有专有名词的话，
    就是白占了那两秒。字数预算因此走 `prompt_char_budget` 而不是 `target_chars`：
    后者会让模型写出一篇时长达标、信息量少三分之一的稿子。
    The duration is fixed — by the platform, and again by the speed-up applied at publish
    time — so a script is judged on how much fact it fits into the same seconds. Avoiding
    vague copy is not a matter of wording: a sentence carrying no figure and no proper
    noun has wasted its two seconds. The budget therefore goes through
    `prompt_char_budget`, not `target_chars`, which would yield a draft that meets the
    duration while carrying a third less information.

为什么输入用正文而不是摘要 / Why the body rather than the summary:
    摘要已经把技术细节压掉了（它的任务是 60~120 字的快速浏览）。拿摘要写口播，
    模型手里除了几句结论什么都没有，只能用形容词填满一分钟——**白话正是这么来的**。
    The summary has already discarded the technical detail; its job is a 60–120 character
    skim. Writing a voice-over from it leaves the model with nothing but conclusions to
    work from, so it fills the minute with adjectives — which is exactly where vague,
    padded copy comes from.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from dna.core.logging import get_logger
from dna.core.models import Article
from dna.core.prompts import load_prompt, render_prompt
from dna.llm.base import LLMProvider
from dna.narration.duration import (
    prompt_char_budget,
    unit_window,
)
from dna.narration.script_engine import (
    DEFAULT_MAX_REWRITES,
    SCRIPT_BODY_LIMIT,
    ScriptResult,
    _generate,
    article_block,
)

logger = get_logger("narration.script_builder")


# 视频稿结尾的引导语默认值 / default sign-off for video scripts
# 实际取值来自 `Profile.cta_line`，这里只是不传参时的兜底，让构建器保持可单测。
# The real value comes from `Profile.cta_line`; this is the fallback that keeps the
# builders unit-testable without config.
DEFAULT_CTA = "关注我，下期分享 AI 行业最新进展"


# 提示词正文都在 `config/prompts/` / Every prompt text lives under that directory
#     shortvideo.zh.md · shortvideo.en.md · narration.zh.md · narration.en.md
#     _shared/professionalism.{zh,en}.md   四种文案共用的写作要求
#     _shared/instruction_block.{zh,en}.md 额外要求的抬头
PROMPT_SHORTVIDEO = "shortvideo"
PROMPT_NARRATION = "narration"
PROMPT_RULES = "_shared/professionalism"
PROMPT_INSTRUCTION_BLOCK = "_shared/instruction_block"

# 关于那份共用的写作要求 / About the shared writing rules
#
# 第 1 条是最重要的一条 / Rule one carries the most weight:
#     稿子的成败在**信息密度**。时长是固定的（平台限制 + 发布时还会加速播放），
#     所以每一秒都要装下事实。一句没有数字、没有专有名词、没有结论的话，
#     就是白占了那两秒——而白话正是这么攒出来的，不是因为用词不够文雅。
#     A script succeeds or fails on information density. The duration is fixed — by the
#     platform, and further by the speed-up applied at publish time — so every second must
#     carry fact. A sentence with no figure, no proper noun and no conclusion has wasted
#     its two seconds, and that is how vague copy accumulates: not from inelegant wording.
#
# 为什么英文版是重写而不是翻译 / Why the English rules are rewritten, not translated:
#     它是**给模型看的指令**，不是产物。指令用目标语言写，模型遵守得明显更好；
#     而且「白话」「套话」这些词在中文语境里的具体所指，直译过去会变成空泛的
#     "avoid vague language"，等于把最要紧的那条规则说没了。
#     These are instructions rather than output. Models follow them markedly better in the
#     target language, and a literal translation of the Chinese terms for padding and
#     filler degrades into a vague "avoid vague language" — losing the rule that matters
#     most.


def rules_for(lang: str) -> str:
    """该语言的写作要求 / The writing rules for one language."""
    return load_prompt(f"{PROMPT_RULES}.{'zh' if lang == 'zh' else 'en'}")


def instruction_block(instructions: str, lang: str = "zh") -> str:
    """
    把人写的额外要求接到提示词末尾 / Append the operator's extra requirements.

    **放在最后**：提示词里后出现的指令权重更高，而这段正是用来覆盖前面默认要求的
    ——人点「重做」并写下「加长到 40 秒」「用词再专业一点」，要的就是推翻默认。
    Placed last because later instructions carry more weight, and overriding the defaults
    is exactly what this block is for: the operator writes it precisely to change them.

    没有额外要求时返回空串，提示词与不带这个功能时**逐字相同**——
    否则每篇都会因为多一个空标题而错开 LLM 缓存，白花一轮钱。
    An empty string when there is nothing to add, so the prompt is byte-identical to one
    built without the feature; otherwise every call would miss the response cache over an
    empty heading.
    """
    text = (instructions or "").strip()
    if not text:
        return ""
    heading = load_prompt(f"{PROMPT_INSTRUCTION_BLOCK}.{'zh' if lang == 'zh' else 'en'}")
    return f"\n\n{heading}\n{text}"


def _window(
    chars: tuple[int, int] | None, low: float, high: float, lang: str
) -> tuple[int, int]:
    """
    定出这一篇的字数区间 / Resolve this piece's length window.

    `chars` 来自 `profile.yaml`（中文字数），是**验收标准**。
    没给的话按秒数推导——这条退路只为让不带配置的调用（单测）继续可用，
    生产路径一律由 profile 提供。
    `chars` comes from the profile and is what acceptance is measured against. The
    seconds-derived fallback exists only so calls without config (unit tests) keep
    working; the production path always supplies it.
    """
    if chars is not None:
        return unit_window(chars[0], chars[1], lang=lang)
    return prompt_char_budget(low, lang=lang), prompt_char_budget(high, lang=lang)



class ShortVideoOut(BaseModel):
    """短视频文案的结构化输出 / Structured output of a short-video script."""

    # description 里不写字数上限：那会和提示词里的数字打架，而提示词才是可配的那个。
    # max_length 只是最后一道防线，比提示词的要求留出余量。
    title: str = Field(max_length=24, description="主标题，有冲击力，不用标点结尾")
    subtitle: str = Field(max_length=36, description="副标题，补充关键信息或数据")
    script: str = Field(min_length=40, description="口播正文，不含标题，不含角色名")


class NarrationOut(BaseModel):
    """口播文案的结构化输出 / Structured output of a voice-over script."""

    title: str = Field(max_length=24, description="主标题，写事件本身")
    subtitle: str = Field(max_length=36, description="副标题，堆亮点与意义")
    script: str = Field(min_length=120, description="口播正文，连贯成段，不分小标题")


def build_short_video(
    article: Article,
    llm: LLMProvider,
    *,
    low: float = 25.0,
    high: float = 35.0,
    chars: tuple[int, int] | None = None,
    cta: str = DEFAULT_CTA,
    lang: str = "zh",
    instructions: str = "",
    max_rewrites: int | None = None,
    body_chars: int | None = None,
) -> ScriptResult:
    """
    生成短视频文案 / Build a short-video script.

    **要的是主干事实链，不是单点深挖。** 这一条是从人工撰写的参考稿反推出来的：
    30 秒里塞进了「谁开源了什么 → 榜单得分与排名 → 激活参数 → 上下文长度 →
    速度与成本 → 能力追平旗舰」六个事实，每句一个，句句带数字。
    A short clip carries the trunk of the story, not one point in depth. This is reverse
    engineered from a hand-written reference: thirty seconds holding six facts — who
    open-sourced what, the index score and rank, the activated parameter count, the
    context length, speed and cost, and parity with flagship models — one per sentence,
    every one carrying a figure.

    先前的版本要求「一条只讲一个点」，产出的稿子技术上正确、却**没把文章讲清楚**：
    观众听完知道 UD-Q8_K_XL 的困惑度没变，却不知道这是哪个模型、发布了没有。
    An earlier version asked for a single point per clip. The result was technically
    correct yet failed to convey the story: the viewer learned that UD-Q8_K_XL's
    perplexity held, without learning which model it was or that it had shipped.
    """
    if lang != "zh":
        return _build_english_short_video(
            article, llm, low=low, high=high, chars=chars, instructions=instructions,
            max_rewrites=max_rewrites,
        body_chars=body_chars,
        )

    lo_chars, hi_chars = _window(chars, low, high, "zh")
    prompt = render_prompt(
        f"{PROMPT_SHORTVIDEO}.zh",
        rules=rules_for("zh"),
        low=f"{low:.0f}",
        high=f"{high:.0f}",
        lo_chars=lo_chars,
        hi_chars=hi_chars,
        cta=cta,
    ) + instruction_block(instructions)

    return _generate(
        llm,
        system_prompt=prompt,
        article=article,
        schema=ShortVideoOut,
        extract=lambda out: (out.script, out.title, out.subtitle),
        low_units=lo_chars,
        high_units=hi_chars,
        label="短视频",
        max_rewrites=max_rewrites,
        body_chars=body_chars,
    )


def _build_english_short_video(
    article: Article,
    llm: LLMProvider,
    *,
    low: float,
    high: float,
    chars: tuple[int, int] | None,
    instructions: str,
    max_rewrites: int | None = None,
    body_chars: int | None = None,
) -> ScriptResult:
    """
    英文版短视频稿 / The English short-video script.

    **原生写，不翻译中文稿。**中文 30 秒的稿子翻成英文不是 30 秒的稿子——
    时长正是这三种文案的验收标准，走翻译等于把时长交给译文长度去决定。
    Written natively rather than translated: a thirty-second Chinese script is not a
    thirty-second English one, and duration is what these kinds are accepted on.
    """
    lo_words, hi_words = _window(chars, low, high, "en")
    prompt = render_prompt(
        f"{PROMPT_SHORTVIDEO}.en",
        rules=rules_for("en"),
        low=f"{low:.0f}",
        high=f"{high:.0f}",
        lo_words=lo_words,
        hi_words=hi_words,
    )
    prompt += instruction_block(instructions, "en")

    return _generate(
        llm,
        system_prompt=prompt,
        article=article,
        schema=ShortVideoOut,
        extract=lambda out: (out.script, out.title, out.subtitle),
        low_units=lo_words,
        high_units=hi_words,
        label="short video",
        max_rewrites=max_rewrites,
        body_chars=body_chars,
        lang="en",
    )


def build_narration(
    article: Article,
    llm: LLMProvider,
    *,
    low: float = 60.0,
    high: float = 120.0,
    chars: tuple[int, int] | None = None,
    cta: str = DEFAULT_CTA,
    lang: str = "zh",
    instructions: str = "",
    max_rewrites: int | None = None,
    body_chars: int | None = None,
) -> ScriptResult:
    """
    生成口播文案 / Build a voice-over script.

    1~2 分钟的中视频口播：有空间把方法说清楚，因此**必须有技术深度**——
    这是它区别于短视频稿的全部理由。只是把短视频稿拉长就白做了。
    A one-to-two minute piece has room to explain the method, and technical depth is the
    entire reason it exists. Merely stretching the short-video script wastes the format.

    深度不等于可以松散 / Depth is not licence to ramble:
        密度要求与短视频完全相同，只是**多出来的时间用于展开方法和取舍**，
        不是用来加过渡句和感想。它同样带主副标题——发布时要用。
        The density requirement is identical; the extra time buys method and trade-offs,
        not transitions and reflections. It carries a title pair too, needed at publish.
    """
    if lang != "zh":
        return _build_english_narration(
            article, llm, low=low, high=high, chars=chars, instructions=instructions,
            max_rewrites=max_rewrites,
        body_chars=body_chars,
        )

    lo_chars, hi_chars = _window(chars, low, high, "zh")
    prompt = render_prompt(
        f"{PROMPT_NARRATION}.zh",
        rules=rules_for("zh"),
        low_min=f"{low / 60:.0f}",
        high_min=f"{high / 60:.0f}",
        lo_chars=lo_chars,
        hi_chars=hi_chars,
        cta=cta,
    )
    prompt += instruction_block(instructions)

    return _generate(
        llm,
        system_prompt=prompt,
        article=article,
        schema=NarrationOut,
        extract=lambda out: (out.script, out.title, out.subtitle),
        low_units=lo_chars,
        high_units=hi_chars,
        label="口播",
        max_rewrites=max_rewrites,
        body_chars=body_chars,
    )


def _build_english_narration(
    article: Article,
    llm: LLMProvider,
    *,
    low: float,
    high: float,
    chars: tuple[int, int] | None,
    instructions: str,
    max_rewrites: int | None = None,
    body_chars: int | None = None,
) -> ScriptResult:
    """英文版口播稿 / The English voice-over script（原生写，理由同短视频）。"""
    lo_words, hi_words = _window(chars, low, high, "en")
    prompt = render_prompt(
        f"{PROMPT_NARRATION}.en",
        rules=rules_for("en"),
        low_min=f"{low / 60:.0f}",
        high_min=f"{high / 60:.0f}",
        lo_words=lo_words,
        hi_words=hi_words,
    )
    prompt += instruction_block(instructions, "en")

    return _generate(
        llm,
        system_prompt=prompt,
        article=article,
        schema=NarrationOut,
        extract=lambda out: (out.script, out.title, out.subtitle),
        low_units=lo_words,
        high_units=hi_words,
        label="voice-over",
        max_rewrites=max_rewrites,
        body_chars=body_chars,
        lang="en",
    )



__all__ = [
    "DEFAULT_CTA",
    "DEFAULT_MAX_REWRITES",
    "PROMPT_INSTRUCTION_BLOCK",
    "PROMPT_NARRATION",
    "PROMPT_RULES",
    "PROMPT_SHORTVIDEO",
    "SCRIPT_BODY_LIMIT",
    "NarrationOut",
    "ScriptResult",
    "ShortVideoOut",
    "article_block",
    "build_narration",
    "build_short_video",
    "instruction_block",
    "rules_for",
]
