"""
字幕段内切分 / Splitting a segment into subtitle-sized cues.

一段音频一条字幕太长了 —— 一条挂十几秒、几十个字，看着累，也不像字幕。
这个模块把它切成短条。**纯函数**：不读音频、不问引擎，给文本与停顿点就出结果。
Pure functions: text plus pause points in, cues out.

两个文本 / Two texts, and why they differ:
    · **朗读稿**（`spoken`）—— 送进 TTS 的那一版，经过一次 LLM 改写：
      多音字换成同音字、`RTX 4060` 改成读法、加了 `[pause:400ms]` 这类标记。
    · **原文**（`source`）—— `clean_for_speech` 之后的稿子，与作者写的基本逐字对应。

    音频是按朗读稿合成的，所以**只有它能和时长成正比**；而字幕要给人看，
    该显示原文。两者字数可以差到 0.6~1.8 倍（见 `tts/preprocess.py` 的
    `_MIN_RATIO` / `_MAX_RATIO`），**所以按字数比例映射必然错位** ——
    这也是本模块只做「条数配对」而不做「字数比例」的原因。
    The audio follows the spoken form, so only it is proportional to the timeline;
    the cue text must come from the source form. Their lengths differ by design,
    which rules out per-character mapping.

取舍 / The trade-off, stated plainly:
    两者无法同时完美。这里选**文本优先**：条数与切点按文本定，时间轴吸附到
    最近的**真实停顿**上。代价是偶尔偏几十毫秒 —— 比文本错位轻得多。
    Text wins; timings snap to real pauses and may be tens of milliseconds off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 句末标点 / sentence terminators
SENTENCE_END = "。！？…；!?;"
# 次级标点（从句边界）/ clause boundaries
CLAUSE_END = "，、,:："
# 切分后不要留在条目开头的字符 / characters trimmed from a cue's head
LEADING_TRIM = "，。、！？…；：,.;!?: "

_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u30ff\uac00-\ud7af]")

# 控制标记不是台词 / control markup is not dialogue
_MARKUP = re.compile(r"\[[a-zA-Z_]+(?::[^\]]*)?\]")


@dataclass(frozen=True)
class SplitParams:
    """切分参数 / Segmentation parameters —— 按观感调的旋钮。"""

    max_chars: int = 20
    min_chars: int = 12
    chars_per_word: float = 4.0
    # 吸附窗口（秒）：理论边界离最近停顿超过它就**不吸附**，宁可留在原处
    snap_window: float = 0.45
    # 硬上限的倍数：达到 `max_chars` 只是「该切了」，超过这个倍数才**无条件切**
    #
    # 这个区分是为了一件事：中文没有词边界，一旦在字数上无条件切，就会切出
    # 「函 / 数」「基 / 本」这种半个词的字幕。达到软上限之后继续往前走到下一个
    # 标点，切口才落在词与词之间。
    # A soft ceiling followed by the next punctuation reads far better than a hard cut
    # at an arbitrary character, which splits Chinese words in half.
    hard_chars_ratio: float = 1.6
    # 短于此的条目并入相邻条目（秒）
    #
    # 停顿吸附之后可能出现很短的条：两个被吸附的停顿离得近，切出来的字幕
    # 一闪而过，观感上像抖动。实测见过 0.05 秒的一条。
    # Snapping two boundaries onto nearby pauses can leave a cue that flashes past.
    merge_below_seconds: float = 0.9

    @classmethod
    def from_tuning(cls, tuning: object | None) -> SplitParams:
        """
        从 `Profile.tuning` 取参数 / Build from the profile's tuning block.

        项目里**可调参数一律从配置读**（见 `CLAUDE.md` 的配置铁律）：这几个值
        直接影响字幕观感，写死在代码里意味着用户改不动它。缺字段（旧 profile）
        就退回默认值，不让配置结构的变化把字幕整个搞挂。
        These values shape how subtitles look, so they belong to the profile.
        """
        if tuning is None:
            return cls()
        return cls(
            max_chars=int(getattr(tuning, "subtitle_max_chars", 20)),
            min_chars=int(getattr(tuning, "subtitle_min_chars", 12)),
            merge_below_seconds=float(getattr(tuning, "subtitle_merge_below_seconds", 0.9)),
            snap_window=float(getattr(tuning, "subtitle_snap_window", 0.45)),
        )


@dataclass(frozen=True)
class Cue:
    """一条字幕 / one cue: 起止秒 + 显示的文本。"""

    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return self.end - self.start


def strip_markup(text: str) -> str:
    """去掉控制标记 / Drop the synthesiser's markup."""
    return " ".join(_MARKUP.sub(" ", text or "").split())


def weight(text: str, chars_per_word: float = 4.0) -> float:
    """
    估算「字数额度」/ Estimate the character budget of a piece of text.

    中文按字计；英文按**词**折算，否则一行英文的字面长度会比一行中文长得多，
    用 `len()` 当上限会让英文条被切得莫名其妙地碎。
    """
    cjk = len(_CJK.findall(text))
    rest = _CJK.sub(" ", text)
    words = len([w for w in re.split(r"\s+", rest) if w.strip()])
    other = len(re.sub(r"[\s\w]", "", rest))
    return cjk + words * chars_per_word + other


# ── 文本切分 / splitting text ────────────────────────────────────────────────


def sentences(text: str) -> list[str]:
    """按句末标点切句，**标点跟着前一句走** / Split on terminators, keeping them."""
    out: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        if char in SENTENCE_END:
            out.append(buffer)
            buffer = ""
    if buffer.strip():
        out.append(buffer)
    return [s for s in out if s.strip()]


def _atomic_units(text: str, params: SplitParams) -> list[str]:
    """
    切成**最小单元**：句子；单句超长时再按次级标点/字数拆 / Atomic units.

    最小单元是后面所有合并算法的输入 —— 有了它，「切成 n 条」就变成
    「把这些单元合并成 n 组」，不会出现把词劈开的结果。
    """
    units: list[str] = []
    for sentence in sentences(text):
        if weight(sentence, params.chars_per_word) <= params.max_chars * 1.5:
            units.append(sentence)
            continue
        units.extend(_split_long(sentence, params))
    return [u for u in units if u.strip()]


def _split_long(text: str, params: SplitParams) -> list[str]:
    """
    超长单句按次级标点切 / Break an over-long sentence on clauses.

    **标点绝对优先**：达到 `max_chars` 之后继续往前走，遇到次级标点就切；
    到 `max_chars * hard_chars_ratio` 时**先回头看有没有标点可退**，
    有就退到那里，没有才继续等。只有长到 `hard * 2` 仍然一个标点都没有
    （长串英文或数字）才就地切。
    Punctuation wins outright; the hard ceiling only backtracks to one, and cuts blind
    solely when a run has no punctuation at all.

    曾经的写法是「到 hard 就切」，结果是 `Anthropic 工程师 … 的中间件，` 里那个
    逗号还没轮到就被截胡，切口落在「的」前面 —— 字幕以助词起头，读着别扭。
    The older order cut before the comma arrived and left cues starting with 的.
    """
    hard = params.max_chars * params.hard_chars_ratio
    ceiling = hard * 2
    out: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        current = weight(buffer, params.chars_per_word)
        if char in CLAUSE_END and current >= params.min_chars:
            out.append(buffer)
            buffer = ""
        elif current >= hard:
            head, tail = _backtrack(buffer)
            if tail:
                out.append(head)
                buffer = tail
            elif current >= ceiling:
                # 长到两倍上限还没有标点：只能就地切，至少别让它无限长
                out.append(buffer)
                buffer = ""
    if buffer.strip():
        out.append(buffer)
    return out


def _backtrack(buffer: str) -> tuple[str, str]:
    """
    超限时回退到最近的**标点** / Backtrack to the nearest punctuation.

    只在标点处退，**不在空格退**：中文的空格几乎都是中英交界，退到那里会让
    下一条以「的」「了」这类助词起头，读着别扭。宁可这一条长一点、完整一点。
    Spaces are not break opportunities here: Chinese text after a Latin word would
    then start mid-phrase. A slightly long cue beats a cue starting with 的.

    返回 `(前段, 后段)`；找不到标点时后段为空，由调用方决定是否继续等。
    """
    for index in range(len(buffer) - 1, 0, -1):
        if buffer[index] in SENTENCE_END + CLAUSE_END:
            head = buffer[:index + 1]
            tail = buffer[index + 1:]
            # 回退太远（把大半条都退光）就不值得，让调用方继续等下一个标点
            if len(head) * 2 >= len(buffer):
                return head, tail
            break
    return buffer, ""


def split_by_rules(text: str, params: SplitParams | None = None) -> list[str]:
    """
    按标点与字数上限切，**条数不定** / Split by punctuation and length only.

    用在朗读稿上：它决定**该有几条字幕**。
    """
    p = params or SplitParams()
    cleaned = strip_markup(text)
    if not cleaned:
        return []

    out: list[str] = []
    buffer = ""
    for unit in _atomic_units(cleaned, p):
        if buffer and weight(buffer + unit, p.chars_per_word) > p.max_chars:
            out.append(buffer)
            buffer = unit
        else:
            buffer += unit
    if buffer.strip():
        out.append(buffer)
    return [chunk.strip() for chunk in out if chunk.strip()]


def split_to_n(text: str, n: int, params: SplitParams | None = None) -> list[str]:
    """
    把文本切成**恰好 n 条** / Split into exactly n chunks.

    这是「条数配对」的关键：时间轴切出几条，文本就必须给几条。
    切点按**累计字数等分**去找最近的单元边界，所以不会把词劈开，
    每条的篇幅也大致相当。
    Chunk boundaries land on unit edges nearest to an even character split.
    """
    p = params or SplitParams()
    cleaned = strip_markup(text)
    if n <= 1 or not cleaned:
        return [cleaned]

    units = _atomic_units(cleaned, p)
    if len(units) == n:
        # 单元数**正好**等于目标条数：一一对应，切点天然落在标点上
        return [unit.strip() for unit in units]
    if len(units) < n:
        # 单元不够才按字符补齐。这是最后手段，切口只能落在字之间
        return _split_by_chars(cleaned, n)

    weights = [weight(u, p.chars_per_word) for u in units]
    prefix = [0.0]
    for value in weights:
        prefix.append(prefix[-1] + value)
    total = prefix[-1] or 1.0

    out: list[str] = []
    previous = 0
    for k in range(1, n):
        target = total * k / n
        # 可切范围：至少给前面留下 k 个单元，后面留下 n-k 个
        low = previous + 1
        high = len(units) - (n - k)
        if low > high:
            break
        best = min(range(low, high + 1), key=lambda j: abs(prefix[j] - target))
        out.append("".join(units[previous:best]).strip())
        previous = best
    out.append("".join(units[previous:]).strip())
    return [chunk for chunk in out if chunk]


def _split_by_chars(text: str, n: int) -> list[str]:
    """按字符数均分 / Even split by character count（单元不够时的兜底）。"""
    size = max(1, len(text) // n)
    out: list[str] = []
    cursor = 0
    for _ in range(n - 1):
        if cursor >= len(text):
            break
        out.append(text[cursor:cursor + size].strip())
        cursor += size
    if cursor < len(text):
        out.append(text[cursor:].strip())
    while len(out) < n:
        out.append("")
    return out[:n] if out else [text]


# ── 时间轴 / the timeline ────────────────────────────────────────────────────


def even_boundaries(chunks: list[str], duration: float,
                    params: SplitParams | None = None) -> list[float]:
    """
    按累计字数比例给出**理论边界** / Even-split boundaries from character weight.

    只是理论值：朗读稿与实际语音之间仍有语速波动，所以调用方还要把它吸附到
    真实停顿上（见 `snap_to_pauses`）。
    """
    p = params or SplitParams()
    if not chunks:
        return [0.0, duration]
    weights = [max(0.01, weight(c, p.chars_per_word)) for c in chunks]
    total = sum(weights)
    bounds = [0.0]
    cursor = 0.0
    for value in weights[:-1]:
        cursor += value / total * duration
        bounds.append(round(cursor, 3))
    bounds.append(round(duration, 3))
    return bounds


def snap_to_pauses(bounds: list[float], pauses: list[float],
                   window: float = 0.45) -> list[float]:
    """
    把理论边界吸附到最近的**真实停顿** / Snap boundaries onto real pauses.

    规则 / rules:
        · 首尾（0 与总时长）不动 —— 它们是音频的事实，不是估计；
        · 每个内部边界找窗口内最近的停顿；
        · **同一个停顿不会被用两次**，且结果必须严格递增 ——
          两个边界吸到同一点会产生零长度字幕，某些播放器直接不加载整份字幕。
    Boundaries stay strictly increasing; a pause is consumed once.
    """
    if not pauses or len(bounds) <= 2:
        return list(bounds)

    used: set[int] = set()
    out = [bounds[0]]
    for index in range(1, len(bounds) - 1):
        target = bounds[index]
        floor = out[-1] + 0.05
        best_index = None
        best_distance = window
        for position, pause in enumerate(pauses):
            if position in used or pause <= floor:
                continue
            distance = abs(pause - target)
            if distance <= best_distance:
                best_distance = distance
                best_index = position
        if best_index is None:
            out.append(max(target, floor))
        else:
            used.add(best_index)
            out.append(pauses[best_index])
    out.append(bounds[-1])

    # 收尾保证单调：吸附之后仍可能有轻微倒挂
    for index in range(1, len(out)):
        if out[index] <= out[index - 1]:
            out[index] = out[index - 1] + 0.05
    return [round(value, 3) for value in out]


def align(
    spoken: str,
    source: str,
    duration: float,
    pauses: list[float],
    params: SplitParams | None = None,
) -> list[Cue]:
    """
    把「朗读稿的时间轴」与「原文的文本」配成字幕 / Pair the two texts into cues.

    参数 / Args:
        spoken: 送进 TTS 的朗读稿，**只用于估时间**（音频是按它合成的）
        source: 要显示的原文；为空则退回朗读稿
        duration: 该段音频的真实时长（秒）
        pauses: 该段内的真实停顿点（秒），来自 `tts/vad.py`

    返回 / Returns:
        `Cue` 列表，时间严格递增、覆盖 `[0, duration]`。

    **条数由要显示的文本决定**（文本优先）/ The displayed text decides the count:
        曾经的写法是「朗读稿切几条、原文就跟着切几条」，结果原文被迫迁就朗读稿
        的分段 —— 一旦两边对不上就整段退回朗读稿，于是字幕又变回
        「二点一点二八七」那种写给耳朵的写法。反过来才对：
        能显示的那份文本是**硬约束**，朗读稿只是估算时间的参考。
        The spoken form only supplies timing; the source form decides the cuts.

    说明 / note: 原文为空（例如改写失败后又没留下原文）时用朗读稿，
    而不是留一条空字幕 —— 空文本的 cue 在编辑器里是一条看不见的轨道块。
    """
    p = params or SplitParams()
    display = strip_markup(source) or strip_markup(spoken)
    if not display:
        return []

    target = split_by_rules(display, p)
    if not target:
        return []
    if len(target) == 1:
        return [Cue(0.0, round(duration, 3), _tidy(target[0]))]

    # 朗读稿只用来算**字数比例**，它被切成什么样都不显示
    reference = split_to_n(strip_markup(spoken) or display, len(target), p)
    if len(reference) != len(target):
        reference = target
    bounds = snap_to_pauses(even_boundaries(reference, duration, p), pauses, p.snap_window)

    cues: list[Cue] = []
    for index, text in enumerate(target):
        start = bounds[index]
        end = bounds[index + 1] if index + 1 < len(bounds) else duration
        if end <= start:
            end = start + 0.05
        cues.append(Cue(round(start, 3), round(end, 3), _tidy(text)))
    return _merge_short([cue for cue in cues if cue.text], p.merge_below_seconds)


def _merge_short(cues: list[Cue], threshold: float) -> list[Cue]:
    """
    把会一闪而过的条目并入相邻条目 / Fold cues that would flash past.

    为什么必须做这一步：时间边界是**吸附**到真实停顿上的，而两个停顿有时靠得很近
    （换气、一个短促的停顿），于是切出来的字幕只有几十毫秒 —— 观众只看到一次闪烁，
    还会以为播放器卡了。实测见过 0.05 秒的一条。

    合并后**文本会变长**（可能超过 `max_chars`），这是刻意的取舍：
    一条略长的字幕比一次闪烁好读得多。
    Merging makes the text longer on purpose: a slightly long cue beats a flash.
    """
    if not cues:
        return cues

    out: list[Cue] = []
    for cue in cues:
        if out and cue.duration < threshold:
            previous = out[-1]
            out[-1] = Cue(previous.start, max(previous.end, cue.end),
                          previous.text + cue.text)
            continue
        out.append(cue)

    # 首条自己就过短时没有「前一条」可并，往后并
    if len(out) >= 2 and out[0].duration < threshold:
        first, second = out[0], out[1]
        out[1] = Cue(first.start, second.end, first.text + second.text)
        out.pop(0)
    return out


def _tidy(text: str) -> str:
    """去开头的悬挂标点，**保留句末标点** / Trim the head only."""
    cleaned = (text or "").strip()
    return (cleaned.lstrip(LEADING_TRIM) or cleaned).strip()


__all__ = [
    "CLAUSE_END",
    "LEADING_TRIM",
    "SENTENCE_END",
    "Cue",
    "SplitParams",
    "align",
    "even_boundaries",
    "sentences",
    "snap_to_pauses",
    "split_by_rules",
    "split_to_n",
    "strip_markup",
    "weight",
]
