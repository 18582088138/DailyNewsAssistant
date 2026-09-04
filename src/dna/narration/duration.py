"""
口播时长估算 / Narration duration estimation.

纯函数，无依赖、无 I/O，可以随便调用。
Pure functions with no dependencies or I/O.

为什么需要它 / Why this exists:
    短视频稿必须落在 25~35 秒，口播稿必须落在 1~2 分钟——**时长是硬约束**，
    超了在平台上就发不出去或会被截断。但 LLM 不会数秒，只会大致遵守字数提示。
    因此生成之后必须由程序量一遍，超出区间就带着「你写长了 N 字」回炉重写。
    Duration is a hard constraint: a script that overruns gets truncated or rejected by
    the platform. An LLM cannot count seconds and only loosely honours a character hint,
    so the result must be measured in code and sent back with a concrete delta when it
    misses the window.

语速取值 / The speaking rates:
    中文 **4.5 字/秒**、英文 **2.6 词/秒**。这是常规播报语速——太快听不清，
    太慢显得拖。TTS 合成时可以微调，但文案长度得先按这个来。
    Normal broadcast pace. TTS can adjust slightly, but the script length must be
    written against these numbers in the first place.

「告诉模型写多少字」和「验收时量多少秒」是两件事 / Budget and acceptance are separate:
    验收永远按 `estimate_seconds`——它是物理量，不容商量。
    但提示词里的字数预算走 `prompt_char_budget`，按中英混排的实际密度放大 1.5 倍。
    两者混用会让稿子**时长达标而信息量不足**：模型照 135 字写完，30 秒的位置
    只填了三分之二。见 `MIXED_COPY_CHAR_FACTOR` 的实测数据。
    Acceptance always goes through `estimate_seconds`, which is physical and not
    negotiable. The budget quoted in prompts goes through `prompt_char_budget`, scaled to
    the density mixed copy actually has. Conflating the two yields scripts that meet the
    duration while under-filling it — see the measurements on `MIXED_COPY_CHAR_FACTOR`.
"""

from __future__ import annotations

import re

# 语速 / speaking rates
CHARS_PER_SECOND_ZH = 4.5
WORDS_PER_SECOND_EN = 2.6

# 中英混排技术稿的字符密度系数 / character-density factor for mixed technical copy
#
# 纯中文 4.5 字/秒，但技术口播稿里 `UD-Q8_K_XL`、`Artificial Analysis` 这类标识符
# **占字符多、占时长少**。实测两篇 30 秒档的稿子：
#
#     人工撰写的参考稿   199 字符 → 27.8 秒 ＝ 7.2 字符/秒
#     本系统生成的稿子   185 字符 → 25.8 秒 ＝ 7.2 字符/秒
#
# 同样的 30 秒，技术稿装得下约 1.5 倍的字符。
#
# **这个系数只用于给提示词的初始字数预算**（`prompt_char_budget`），
# 不参与验收。按 4.5 直接换算会告诉模型「写 112~157 字」，而 30 秒实际装得下
# 200 字——模型照做，稿子就停在区间下沿，信息量凭空少掉三分之一。
# 这是「文案信息量不够」的机械原因，不是提示词写得不好。
# Used only for the initial character budget handed to the prompt, never for acceptance:
# converting at 4.5 tells the model to write 112–157 characters when thirty seconds
# actually holds 200, so the draft lands at the bottom of the window and loses a third of
# its information — a mechanical cause of thin copy, not a prompt-wording problem.
MIXED_COPY_CHAR_FACTOR = 1.5

# 中日韩统一表意文字 / CJK ideographs
_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’\-]*")

# 不发音的内容：Markdown 标记、角色名前缀、括注的舞台提示
# Non-spoken content: Markdown syntax, speaker prefixes, parenthesised stage directions.
_MARKUP_RE = re.compile(r"[*_`#>\[\]()（）]|^\s*[-•]\s*", re.MULTILINE)
_SPEAKER_PREFIX_RE = re.compile(r"^\s*\*{0,2}[^：:\n]{1,12}\*{0,2}\s*[：:]\s*", re.MULTILINE)


def spoken_text(text: str) -> str:
    """
    去掉不会被念出来的部分 / Strip everything that will not be spoken.

    角色名（`主持人：`）、Markdown 标记、方括号里的提示都不进 TTS，
    把它们算进时长会让估算系统性偏长，于是稿子被误判超时、白白回炉重写。
    Speaker labels, Markdown syntax and bracketed directions never reach the TTS engine.
    Counting them would inflate every estimate, wrongly flag scripts as overlong and
    trigger pointless (billed) rewrites.
    """
    without_speakers = _SPEAKER_PREFIX_RE.sub("", text or "")
    return _MARKUP_RE.sub("", without_speakers)


def estimate_seconds(text: str, *, lang: str = "zh") -> float:
    """
    估算口播时长（秒）/ Estimate how long the text takes to read aloud.

    中英混排按各自语速分别计算再相加——技术稿里「DeepSeek-V4-Flash 在
    Terminal Bench 上达到 82.7」这种句子很常见，一律按中文字数算会低估英文部分。
    Mixed scripts are measured per language and summed: technical copy routinely embeds
    English model names and benchmarks, and counting everything as Chinese characters
    would underestimate them.

    >>> round(estimate_seconds("这是一段中文口播稿件内容"), 1)
    2.7
    """
    clean = spoken_text(text)
    if not clean.strip():
        return 0.0

    cjk = len(_CJK_RE.findall(clean))
    words = len(_LATIN_WORD_RE.findall(clean))

    seconds = cjk / CHARS_PER_SECOND_ZH + words / WORDS_PER_SECOND_EN
    return round(seconds, 1)


def target_chars(seconds: float, *, lang: str = "zh") -> int:
    """
    某个时长对应多少字 / How many characters a given duration allows.

    用来把「25~35 秒」翻译成提示词里能用的「110~160 字」——
    模型对字数的遵守程度远好于对秒数的。
    Used to turn "25 to 35 seconds" into the "110 to 160 characters" a prompt can
    actually work with: models honour character counts far better than durations.
    """
    rate = CHARS_PER_SECOND_ZH if lang == "zh" else WORDS_PER_SECOND_EN
    return int(seconds * rate)


def prompt_char_budget(seconds: float, *, lang: str = "zh") -> int:
    """
    给提示词的字数预算 / The character budget handed to the prompt.

    与 `target_chars` 的区别很重要 / The distinction from `target_chars` matters:
        `target_chars` 是 `estimate_seconds` 的**严格逆运算**（纯中文），是物理量；
        这个函数是**要告诉模型写多少字**，按中英混排的实际密度放大。
        `target_chars` is the strict inverse of `estimate_seconds` for all-Chinese text —
        a physical quantity. This is what the model is *told* to write, scaled up to the
        density mixed Chinese-English copy actually has.

    为什么不能直接用 `target_chars` / Why `target_chars` cannot be used directly:
        30 秒按 4.5 字/秒是 135 字，但实测技术稿 30 秒装得下约 200 字。
        用前者当提示，模型会写出一篇**刚好卡在区间下沿**的稿子——时长达标，
        信息量少掉三分之一。
        Thirty seconds converts to 135 characters at 4.5/s, but a technical script of that
        length measures nearer 200. Prompting with the former yields a draft that just
        clears the lower bound: compliant on duration, a third short on substance.
    """
    return int(target_chars(seconds, lang=lang) * MIXED_COPY_CHAR_FACTOR)


def observed_chars_per_second(chars: int, seconds: float) -> float:
    """
    这一稿实测的字符密度 / The character density this particular draft achieved.

    回炉重写时用它换算差值，**比任何预设系数都准**——它量的就是这篇稿子自己的
    中英比例。密度随题材变化很大（纯中文资讯约 5 字符/秒，堆满量化档位名的技术稿
    接近 9），预设一个常数必然对一半的稿子算错。
    Used to convert the duration delta on a rewrite, and more accurate than any fixed
    factor because it measures this draft's own Chinese-English mix. Density varies widely
    by subject — around five characters per second for plain Chinese news, close to nine
    for copy dense with quantisation tier names — so a constant is wrong half the time.
    """
    if seconds <= 0 or chars <= 0:
        return CHARS_PER_SECOND_ZH * MIXED_COPY_CHAR_FACTOR
    return chars / seconds


def within(seconds: float, low: float, high: float) -> bool:
    """时长是否落在区间内 / Whether the duration falls inside the window."""
    return low <= seconds <= high


def length_feedback(
    seconds: float,
    low: float,
    high: float,
    *,
    lang: str = "zh",
    chars: int = 0,
) -> str | None:
    """
    生成回炉重写用的反馈 / Build the feedback for a rewrite.

    落在区间内返回 None。超出时给出**具体差多少字**，而不是「请缩短」——
    模糊的指令换来的是模糊的修改，往往一次砍太多或几乎没变。
    Returns None when inside the window. Otherwise it states the concrete character
    delta rather than "please shorten": a vague instruction yields a vague edit, usually
    cutting far too much or barely anything.

    参数 / Args:
        chars: 上一稿的字符数。给了就按**这一稿实测的字符密度**换算差值，
            不给则退回预设系数。这是自校准的关键：中英混排稿的密度差异很大，
            用固定的 4.5 字/秒换算，一篇 8 字符/秒的技术稿会被要求「删 45 字」，
            而实际需要删掉 80 字——于是第二稿仍然超时，白花一次调用。
            The previous draft's character count. When supplied the delta is converted at
            that draft's own measured density instead of a preset rate. This is what makes
            the loop self-correcting: converting at a flat 4.5 characters per second tells
            a draft running at 8 to cut 45 characters when 80 are needed, so the rewrite
            still overruns and the call is wasted.
    """
    if within(seconds, low, high):
        return None

    unit = "字" if lang == "zh" else " words"
    fallback = CHARS_PER_SECOND_ZH if lang == "zh" else WORDS_PER_SECOND_EN
    rate = observed_chars_per_second(chars, seconds) if chars else fallback

    if seconds > high:
        delta = max(1, int((seconds - high) * rate))
        return (
            f"上一稿约 {seconds:.0f} 秒，超出上限 {high:.0f} 秒。"
            f"请**删掉约 {delta}{unit}**——优先删背景铺垫和重复论述，"
            f"保留具体数字、方法名与结论。"
        )

    delta = max(1, int((low - seconds) * rate))
    return (
        f"上一稿约 {seconds:.0f} 秒，不足下限 {low:.0f} 秒。"
        f"请**补充约 {delta}{unit}**——补技术细节、数据或对比，"
        f"不要用背景介绍和套话凑长度。"
    )


__all__ = [
    "CHARS_PER_SECOND_ZH",
    "MIXED_COPY_CHAR_FACTOR",
    "WORDS_PER_SECOND_EN",
    "estimate_seconds",
    "length_feedback",
    "observed_chars_per_second",
    "prompt_char_budget",
    "spoken_text",
    "target_chars",
    "within",
]
