"""
口播时长估算 / Narration duration estimation.

纯函数，无依赖、无 I/O，可以随便调用。
Pure functions with no dependencies or I/O.

为什么需要它 / Why this exists:
    平台对时长有硬限制，但**模型不会数秒，只会数字数**。所以长度用字数管，
    这里负责把字数换算成「大概多少秒」供人参照，以及提供计量用的纯函数。
    The platform caps duration, but a model cannot count seconds — only characters. Length
    is therefore governed in characters; this module converts to an approximate duration
    for human reference and provides the counting primitives.

语速取值 / The speaking rates:
    中文 **4.5 字/秒**、英文 **2.6 词/秒**。这是常规播报语速——太快听不清，
    太慢显得拖。TTS 合成时可以微调，但文案长度得先按这个来。
    Normal broadcast pace. TTS can adjust slightly, but the script length must be
    written against these numbers in the first place.

**验收看字数，不看秒数** / Acceptance is on the character count, not the duration:
    字数区间来自 `profile.yaml`（`shortvideo_chars` 等），提示词里说的和程序检查的
    是同一个数——见 `core/length.py::char_feedback`。
    `estimate_seconds` 退为**参照**：记进产物、显示在界面上，但不参与验收。
    因为同一个字数在中英比例不同的稿子上实测差 50%（200 字：混排约 27 秒、
    纯中文约 44 秒），拿它当验收标准会把合格的稿子反复回炉，
    而偏短的稿子只要秒数恰好落在窗口内就静静通过。
    The window comes from `profile.yaml`, so the prompt and the check quote one number.
    `estimate_seconds` is demoted to reference: recorded and displayed, never gating.
    The same character count measures up to 50% apart depending on how much Latin text a
    draft carries, so gating on it sends compliant drafts back and lets short ones pass.
"""

from __future__ import annotations

import re

from dna.core.length import char_feedback

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

    **英文不放大** / English is not scaled up:
        1.5 这个系数量的是「中文字数 vs 中英混排稿的实际字符数」——英文稿的单位
        本来就是词，不存在这个折算。照样乘 1.5 会让英文稿超长 50%，
        而这正是「英文输出文本量太大」的来源。
        The factor measures Chinese characters against the character count of mixed
        technical copy. English is counted in words to begin with, so scaling it would
        simply make every English script half again too long.
    """
    if lang != "zh":
        return target_chars(seconds, lang=lang)
    return int(target_chars(seconds, lang=lang) * MIXED_COPY_CHAR_FACTOR)


def count_units(text: str, *, lang: str = "zh") -> int:
    """
    按语言数出「一稿有多少个单位」/ Count the units a draft is measured in.

    中文数字符，英文数词 —— 回炉反馈里的差值必须和提示词里用的单位一致，
    否则会出现「上一稿 900 字符 → 请删掉 300 words」这种自相矛盾的指令。
    Characters for Chinese, words for English: the rewrite delta has to be expressed in
    the same unit the prompt used, or the instruction contradicts itself.
    """
    clean = spoken_text(text)
    return len(_LATIN_WORD_RE.findall(clean)) if lang != "zh" else len(clean)


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


def unit_window(low_chars: int, high_chars: int, *, lang: str = "zh") -> tuple[int, int]:
    """
    配置里的中文字数区间 → 该语言的验收单位区间 / Config's window in the draft's unit.

    中文原样返回；英文换成**词数**。折算走「中文字 → 秒 → 英文词」两步，
    而不是直接乘一个字/词比例：
        200 字 ÷ 6.75 字/秒 ＝ 29.6 秒 × 2.6 词/秒 ＝ 77 词
    English goes through duration, not a direct character-to-word ratio.

    为什么不能直接按字数比例折算 / Why a direct ratio is wrong:
        中文一个字约 0.15 秒，英文一个词约 0.38 秒——一个词顶两个半汉字的时长。
        按 1:1 或按字符数折算，英文稿会长出一倍多，而时长才是平台的硬约束。
        A word takes about two and a half times as long to speak as a Chinese
        character, so any character-count ratio makes English scripts run long.
    """
    if lang == "zh":
        return low_chars, high_chars
    zh_rate = CHARS_PER_SECOND_ZH * MIXED_COPY_CHAR_FACTOR
    scale = WORDS_PER_SECOND_EN / zh_rate
    return max(1, int(low_chars * scale)), max(2, int(high_chars * scale))


def seconds_for_units(units: int, *, lang: str = "zh") -> float:
    """
    字数（或词数）→ 预计口播秒数 / Units to the duration they are expected to speak.

    **只用于参照与记账，不用于验收。** 验收看字数（见 `char_feedback`）：
    模型能数字数，数不了秒数，而同一个字数在中英混排比例不同的稿子上
    实测能差出 50%——拿一个浮动 50% 的量当验收标准，产出必然被反复回炉。
    For reference and bookkeeping only; acceptance is on the unit count. A model can
    count characters but not seconds, and the same character count measures up to 50%
    apart depending on how much Latin text a draft carries.
    """
    rate = CHARS_PER_SECOND_ZH * MIXED_COPY_CHAR_FACTOR if lang == "zh" else WORDS_PER_SECOND_EN
    return round(units / rate, 1) if rate else 0.0


__all__ = [
    "CHARS_PER_SECOND_ZH",
    "MIXED_COPY_CHAR_FACTOR",
    "WORDS_PER_SECOND_EN",
    "char_feedback",
    "count_units",
    "estimate_seconds",
    "observed_chars_per_second",
    "prompt_char_budget",
    "seconds_for_units",
    "spoken_text",
    "target_chars",
    "unit_window",
    "within",
]
