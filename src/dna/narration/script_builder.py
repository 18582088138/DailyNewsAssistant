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

from dataclasses import dataclass

from pydantic import BaseModel, Field

from dna.core.logging import get_logger
from dna.core.models import Article
from dna.llm.base import ChatMessage, LLMProvider, assistant, system, user
from dna.narration.duration import (
    count_units,
    estimate_seconds,
    length_feedback,
    prompt_char_budget,
)

logger = get_logger("narration.script_builder")

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
MAX_BODY_CHARS = 6000

# 视频稿结尾的引导语默认值 / default sign-off for video scripts
# 实际取值来自 `Profile.cta_line`，这里只是不传参时的兜底，让构建器保持可单测。
# The real value comes from `Profile.cta_line`; this is the fallback that keeps the
# builders unit-testable without config.
DEFAULT_CTA = "关注我，下期分享 AI 行业最新进展"

# 回炉重写次数上限 / how many rewrites are allowed
# 每次重写都是一次计费调用。两次之后仍不达标就接受现状——模型对这篇的长度
# 判断已经稳定，再试下去是在烧钱换一点点字数。
# Each rewrite is a billed call. After two the model's sense of length for this piece has
# settled, and further attempts spend money for a handful of characters.
MAX_REWRITES = 2

# 所有文案共同的专业性要求 / the professionalism rules every script obeys
#
# 第 1 条是最重要的一条 / Rule one carries the most weight:
#     稿子的成败在**信息密度**。时长是固定的（平台限制 + 发布时还会加速播放），
#     所以每一秒都要装下事实。一句没有数字、没有专有名词、没有结论的话，
#     就是白占了那两秒——而白话正是这么攒出来的，不是因为用词不够文雅。
#     A script succeeds or fails on information density. The duration is fixed — by the
#     platform, and further by the speed-up applied at publish time — so every second must
#     carry fact. A sentence with no figure, no proper noun and no conclusion has wasted
#     its two seconds, and that is how vague copy accumulates: not from inelegant wording.
PROFESSIONALISM = """
写作要求（所有文案通用）：
1. **信息密度优先，这是第一要求**。发布时视频会加速播放，所以在给定的字数里
   要尽可能多地装进原文的关键事实。**每一句都必须携带至少一个具体信息点**——
   一个数字、一个专有名词、一项能力、或一个明确结论。
   写不出信息点的句子直接删掉，不要留着占时长。
2. **使用准确的技术术语**。不要用「非常厉害」「大幅提升」「效果拔群」「迈上新台阶」
   这类白话替代具体指标——该说「在 Terminal Bench 2.1 上从 61.8 提升到 82.7」
   就这么说。
3. **模型名、版本号、benchmark 名称、数值必须与原文完全一致**，不得改写、
   不得四舍五入、不得换成近似说法。
4. 不确定的技术细节**宁可不写**，不要用「据说」「可能」「某种程度上」掩盖。
5. **禁止出现的内容**：
   - 「让我们一起来看看」「话不多说」这类填充语
   - 「这意味着什么」「值得关注的是」这类过渡句自身占一句话
   - 「我认为」「我最关注的是」这类第一人称主观表达（原文作者的建议要写成
     「原文建议」「官方推荐」，标明出处）
   - 用形容词堆出来的结尾，如「让企业级应用不再需要巨额硬件投入」
6. 面向的是懂技术的听众，不需要解释什么是大模型、什么是开源。
""".strip()

# 英文稿的同一套要求 / the same rules, for English scripts
#
# 为什么是重写而不是翻译这段规则 / Why this is rewritten rather than translated:
#     它是**给模型看的指令**，不是产物。指令用目标语言写，模型遵守得明显更好；
#     而且「白话」「套话」这些词在中文语境里的具体所指，直译过去会变成空泛的
#     "avoid vague language"，等于把最要紧的那条规则说没了。
#     These are instructions rather than output. Models follow them markedly better in the
#     target language, and a literal translation of the Chinese terms for padding and
#     filler degrades into a vague "avoid vague language" — losing the rule that matters
#     most.
PROFESSIONALISM_EN = """
Writing rules (all scripts):
1. **Information density comes first.** The clip is played back sped up, so every
   second must carry fact. **Every sentence must land at least one concrete item** —
   a number, a proper noun, a capability, or a definite conclusion.
   Delete any sentence that carries none; do not leave it occupying airtime.
2. **Use precise technical terms.** Never substitute "hugely improved" or
   "a major leap" for the actual figure — say "from 61.8 to 82.7 on Terminal Bench 2.1".
3. **Model names, version numbers, benchmark names and figures must match the source
   exactly.** Do not paraphrase, round, or approximate them.
4. If a technical detail is uncertain, **leave it out** rather than hedging with
   "reportedly" or "to some extent".
5. **Never include:**
   - filler openers such as "let's take a look" or "without further ado"
   - a whole sentence spent on a transition ("what does this mean?", "notably,")
   - first-person opinion ("I think", "what I find most interesting") — attribute
     recommendations to the source: "the authors recommend", "the official guidance is"
   - a closing line built from adjectives rather than facts
6. The audience is technical. Do not explain what an LLM or open source is.
""".strip()


def rules_for(lang: str) -> str:
    """该语言的写作要求 / The writing rules for one language."""
    return PROFESSIONALISM if lang == "zh" else PROFESSIONALISM_EN


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
    if lang == "zh":
        heading = "**本次的额外要求（优先级高于以上默认要求）**："
    else:
        heading = (
            "**Additional requirements for this run "
            "(these take precedence over the defaults above)**:"
        )
    return f"\n\n{heading}\n{text}"


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


class ShortVideoOut(BaseModel):
    """短视频文案的结构化输出 / Structured output of a short-video script."""

    title: str = Field(max_length=20, description="主标题，≤14 字，有冲击力，不用标点结尾")
    subtitle: str = Field(max_length=30, description="副标题，≤20 字，补充关键信息或数据")
    script: str = Field(min_length=40, description="口播正文，不含标题，不含角色名")


class NarrationOut(BaseModel):
    """口播文案的结构化输出 / Structured output of a voice-over script."""

    title: str = Field(max_length=20, description="主标题，≤14 字，写事件本身")
    subtitle: str = Field(max_length=30, description="副标题，≤20 字，堆亮点与意义")
    script: str = Field(min_length=120, description="口播正文，连贯成段，不分小标题")


def build_short_video(
    article: Article,
    llm: LLMProvider,
    *,
    low: float = 25.0,
    high: float = 35.0,
    cta: str = DEFAULT_CTA,
    lang: str = "zh",
    instructions: str = "",
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
            article, llm, low=low, high=high, instructions=instructions
        )

    lo_chars, hi_chars = prompt_char_budget(low), prompt_char_budget(high)
    prompt = f"""{PROFESSIONALISM}

任务：为下面这篇资讯写一条 {low:.0f}~{high:.0f} 秒的短视频口播文案。

结构要求：
- **主标题**：就写事件本身，谁做了什么，≤14 字（例：「DeepSeek V4 Flash 开源」）
- **副标题**：堆亮点与意义，≤20 字（例：「极限量化，成本骤降，本地部署迈入新时代」）
- **口播正文**控制在 **{lo_chars}~{hi_chars} 字**

正文写法：
- **第一句必须是事件本身**：谁、发布/开源了什么、叫什么名字（带完整版本号）。
  不要用悬念、设问或结论开场——观众得先知道在说什么
- 之后按「最硬的指标 → 关键参数 → 能力亮点」往下推，**每句换一个新事实**，
  不要在同一个点上展开第二句。**覆盖文章的主干，不要只挑一个点深挖**
- 优先选这几类事实：榜单得分与排名、参数规模（总参/激活参）、上下文长度、
  速度与成本、关键能力对比。原文有几个就尽量都放进去
- 可以用短句和感叹句制造节奏（「更狠的是」「直接打破天花板」），
  但**每个短句后面必须紧跟具体数据**，不能只有情绪
- 最后一句固定收尾：「{cta}」

正文是要被念出来的，写口语，但**不要白话**。""" + instruction_block(instructions)

    return _generate(
        llm,
        system_prompt=prompt,
        article=article,
        schema=ShortVideoOut,
        extract=lambda out: (out.script, out.title, out.subtitle),
        low=low,
        high=high,
        label="短视频",
    )


def _build_english_short_video(
    article: Article,
    llm: LLMProvider,
    *,
    low: float,
    high: float,
    instructions: str,
) -> ScriptResult:
    """
    英文版短视频稿 / The English short-video script.

    **原生写，不翻译中文稿。**中文 30 秒的稿子翻成英文不是 30 秒的稿子——
    时长正是这三种文案的验收标准，走翻译等于把时长交给译文长度去决定。
    Written natively rather than translated: a thirty-second Chinese script is not a
    thirty-second English one, and duration is what these kinds are accepted on.
    """
    lo_words = prompt_char_budget(low, lang="en")
    hi_words = prompt_char_budget(high, lang="en")
    prompt = f"""{PROFESSIONALISM_EN}

Task: write a {low:.0f}-{high:.0f} second short-video voice-over for the article below.

Structure:
- **title**: the event itself — who did what, at most 8 words
  (e.g. "DeepSeek V4 Flash goes open source")
- **subtitle**: the highlights and why they matter, at most 12 words
- **script**: **{lo_words}-{hi_words} words**

How to write the script:
- **The first sentence states the event**: who released or open-sourced what, by its
  full name and version. No teasers, no rhetorical questions — the viewer needs to know
  what this is about before anything else
- Then work down: hardest metric, key parameters, standout capability.
  **A new fact in every sentence** — never spend a second sentence on the same point.
  **Cover the trunk of the story rather than drilling into one detail**
- Prefer these facts: benchmark scores and rankings, parameter counts (total and
  activated), context length, speed and cost, head-to-head capability results.
  Fit in as many as the source provides
- Short punchy sentences are fine for pace, but **every one must be followed
  immediately by a concrete figure** — never rhythm alone

This is spoken, not read. Write natural spoken English, but never vague English."""
    prompt += instruction_block(instructions, "en")

    return _generate(
        llm,
        system_prompt=prompt,
        article=article,
        schema=ShortVideoOut,
        extract=lambda out: (out.script, out.title, out.subtitle),
        low=low,
        high=high,
        label="short video",
        lang="en",
    )


def build_narration(
    article: Article,
    llm: LLMProvider,
    *,
    low: float = 60.0,
    high: float = 120.0,
    cta: str = DEFAULT_CTA,
    lang: str = "zh",
    instructions: str = "",
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
            article, llm, low=low, high=high, instructions=instructions
        )

    lo_chars, hi_chars = prompt_char_budget(low), prompt_char_budget(high)
    prompt = f"""{PROFESSIONALISM}

任务：为下面这篇资讯写一段 {low / 60:.0f}~{high / 60:.0f} 分钟的口播文案。

结构要求：
- **主标题** ≤14 字（事件本身）、**副标题** ≤20 字（亮点与意义）
- **正文控制在 {lo_chars}~{hi_chars} 字**

正文写法：
- 开头两句把事件和最硬的指标说完：谁发布了什么、核心数据是多少
- 中间是这个格式存在的理由——**必须有技术深度**：说清它用了什么方法、
  关键数字是多少、这些数字为什么成立。只把结论念一遍是不合格的，
  那是短视频稿的活
- **信息要覆盖原文主干**。原文讲了几件事就都要提到，不要只展开其中一件，
  也不要因为要展开细节而漏掉另一半内容
- 如果原文提到了局限、代价、前提条件或适用门槛，**必须说**——
  只讲好处的稿子没有可信度
- 结尾**落在具体建议或具体数字上**，不要用形容词收束。
  例：该写「128GB 以下建议直接用 API」，不要写「让部署变得更加轻松」
- 最后一句固定收尾：「{cta}」

连贯成段，不要分小标题、不要写「第一点第二点」——这是念出来的不是看的。"""
    prompt += instruction_block(instructions)

    return _generate(
        llm,
        system_prompt=prompt,
        article=article,
        schema=NarrationOut,
        extract=lambda out: (out.script, out.title, out.subtitle),
        low=low,
        high=high,
        label="口播",
    )


def _build_english_narration(
    article: Article,
    llm: LLMProvider,
    *,
    low: float,
    high: float,
    instructions: str,
) -> ScriptResult:
    """英文版口播稿 / The English voice-over script（原生写，理由同短视频）。"""
    lo_words = prompt_char_budget(low, lang="en")
    hi_words = prompt_char_budget(high, lang="en")
    prompt = f"""{PROFESSIONALISM_EN}

Task: write a {low / 60:.0f}-{high / 60:.0f} minute voice-over for the article below.

Structure:
- **title** at most 8 words (the event), **subtitle** at most 12 words (why it matters)
- **script**: **{lo_words}-{hi_words} words**

How to write the script:
- The first two sentences settle the event and the hardest number: who shipped what,
  and the headline figure
- The middle is the entire reason this format exists — **it must carry technical
  depth**: the method used, the key figures, and why those figures hold. Restating the
  conclusion is not acceptable; that is the short-video script's job
- **Cover the trunk of the source.** Mention every distinct thing it reports rather than
  expanding one of them at the cost of the rest
- If the source names limitations, costs, prerequisites or thresholds, **say so** —
  a script that lists only upsides has no credibility
- End on a concrete recommendation or figure, never on adjectives.
  Write "under 128 GB, use the API instead", not "makes deployment easier than ever"

One continuous piece. No headings, no "firstly, secondly" — this is spoken, not read."""
    prompt += instruction_block(instructions, "en")

    return _generate(
        llm,
        system_prompt=prompt,
        article=article,
        schema=NarrationOut,
        extract=lambda out: (out.script, out.title, out.subtitle),
        low=low,
        high=high,
        label="voice-over",
        lang="en",
    )


# ---------------------------------------------------------------------------
# 共用骨架 / the shared skeleton
# ---------------------------------------------------------------------------


def _generate(
    llm: LLMProvider,
    *,
    system_prompt: str,
    article: Article,
    schema: type[BaseModel],
    extract,  # noqa: ANN001 - 各 schema 字段不同，由调用方给取值函数
    low: float,
    high: float,
    label: str,
    lang: str = "zh",
) -> ScriptResult:
    """
    生成 + 量时长 + 回炉 / Generate, measure, and rewrite if the duration misses.

    回炉时把**上一稿原文**作为 assistant 消息带回去，再附上具体差值。
    只发一句「太长了」而不给上一稿，模型会从头重写一篇完全不同的稿子，
    上一稿里写对的部分也一起丢了。
    The previous draft is sent back as an assistant message alongside the concrete delta.
    Saying only "too long" without the draft makes the model start over from scratch,
    discarding the parts that were already right.
    """
    messages = [system(system_prompt), user(article_block(article))]
    calls = 0
    result: ScriptResult | None = None

    for attempt in range(MAX_REWRITES + 1):
        out = llm.chat_json(messages, schema, temperature=0.6)
        calls += 1

        text, title, subtitle = extract(out)
        seconds = estimate_seconds(text)
        result = ScriptResult(
            text=text.strip(),
            seconds=seconds,
            calls=calls,
            within_target=True,
            title=title.strip(),
            subtitle=subtitle.strip(),
        )

        # 带上字符数：差值按**这一稿实测的字符密度**换算，而不是预设的 4.5 字/秒。
        # 技术稿实测能到 8 字符/秒，按 4.5 算会少要求删掉一半，第二稿仍然超时。
        # The character count is passed so the delta is converted at this draft's measured
        # density rather than a preset 4.5/s: technical copy reaches 8, and the preset
        # under-asks by half, leaving the rewrite still over the limit.
        # 单位随语言走：中文数字符、英文数词。混用会得出「上一稿 900 字符，
        # 请删掉 300 words」这种自相矛盾的指令。
        # The unit follows the language; mixing them yields a self-contradicting delta.
        feedback = length_feedback(
            seconds, low, high, lang=lang, chars=count_units(text, lang=lang)
        )
        if feedback is None:
            logger.info("%s文案完成：%.0f 秒，%d 字，%d 次调用", label, seconds, len(text), calls)
            return result

        if attempt == MAX_REWRITES:
            # 试到上限仍不达标：**返回它而不是报错**。一篇 38 秒的稿子仍然可用，
            # 人手动删两句就行；为此让整篇产物失败是不划算的。
            # Still off after the last attempt: return it rather than fail. A 38-second
            # script is still usable with a manual trim, and failing the whole production
            # over it is a bad trade.
            result.within_target = False
            logger.warning(
                "%s文案 %d 次仍未落入 %.0f~%.0f 秒（实际 %.0f 秒），按现状返回",
                label,
                calls,
                low,
                high,
                seconds,
            )
            return result

        logger.debug("%s文案 %.0f 秒，超出 %.0f~%.0f，回炉重写", label, seconds, low, high)
        messages = [*messages, assistant(text), user(feedback)]

    return result  # pragma: no cover - 循环必然返回


def article_block(article: Article) -> str:
    """
    把文章组织成提示词里的输入块 / Lay the article out as the prompt's input block.

    **`longform.py` 也用这一份。** 先前两个模块各写了一份，而 longform 那份
    漏掉了「正文为空时禁止编造」这条——两份同样的东西一定会漂移，
    漂移的方向还偏偏是把安全约束丢掉。
    Shared with `longform.py`. The two modules previously kept separate copies and the
    long-form one had lost the "do not fabricate when the body is empty" clause: two
    copies of the same thing drift, and this one drifted by dropping a safety rule.

    正文截到 3000 字。技术资讯的核心事实与数字几乎总在前半篇，后面多是延伸讨论；
    全文送进去只是线性增加 input 费用。
    The body is capped at 3000 characters: in technical news the core facts and figures
    are almost always in the first half, and sending the rest only scales the input bill.
    """
    lines = [f"标题：{article.title}"]
    if article.author:
        lines.append(f"作者：{article.author}")

    body = article.text[:MAX_BODY_CHARS].strip()
    if body:
        lines.append(f"\n正文：\n{body}")
    else:
        lines.append("\n（正文抓取失败，只有标题可用——请基于标题写，不要编造任何细节与数字）")

    return "\n".join(lines)


__all__ = [
    "DEFAULT_CTA",
    "article_block",
    "MAX_BODY_CHARS",
    "MAX_REWRITES",
    "PROFESSIONALISM",
    "NarrationOut",
    "ScriptResult",
    "ShortVideoOut",
    "build_narration",
    "build_short_video",
]
