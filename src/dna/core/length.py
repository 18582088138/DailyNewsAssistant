"""
长度验收与回炉反馈 / Length acceptance and rewrite feedback.

**字数是验收标准，秒数只是参照。** 提示词里要求的单位与程序检查的单位必须是
同一个，否则两边各说一套：一份 200 字的稿子按中英混排能是 27 秒、按纯中文是
44 秒，拿秒数验收就会把合格的稿子反复回炉，而一份 168 字的稿子因为秒数刚好
落在窗口内就静静通过了。
The prompt and the check must speak one unit. The same 200 characters measure anywhere
from 27 to 44 seconds depending on how much Latin text they carry, so gating on seconds
sends compliant drafts back while letting short ones through.

放在 `core/` 是因为**摘要和文案都要用**：摘要在 `pipeline/`，文案在 `narration/`，
而依赖只能从 narration 流向 pipeline，不能反向。
It lives in `core/` because both the summariser (`pipeline/`) and the script builders
(`narration/`) need it, and dependencies only flow one way.
"""

from __future__ import annotations


def char_feedback(units: int, low: int, high: int, *, lang: str = "zh") -> str | None:
    """
    生成回炉重写用的反馈 / Build the feedback for a rewrite.

    落在区间内返回 None。超出时给出**确切差多少字**，而不是「请缩短」——
    模糊的指令换来的是模糊的修改，往往一次砍太多或几乎没变。
    提示词要求的单位和这里检查的单位相同，所以差值是减法，不需要按密度换算。
    Returns None inside the window; otherwise states the exact delta rather than "please
    shorten", since a vague instruction yields a vague edit. The prompt and the check
    speak the same unit, so the delta is a subtraction.

    参数 / Args:
        units: 上一稿的长度。中文数字符、英文数词（见 `duration.count_units`）
        lang:  反馈用**稿子本身的语言**写。给英文稿发中文指令，模型有相当概率
            改回中文输出——回炉反而把稿子毁了。
            The feedback is written in the draft's own language: a Chinese instruction
            attached to an English draft stands a real chance of flipping the output.
    """
    if low <= units <= high:
        return None

    if units > high:
        delta = units - high
        if lang == "zh":
            return (
                f"上一稿 {units} 字，超出上限 {high} 字。"
                f"请**删掉 {delta} 字**，最终落在 {low}~{high} 字——"
                f"优先删背景铺垫和重复论述，保留具体数字、方法名与结论。"
            )
        return (
            f"The previous draft is {units} words, over the {high}-word limit. "
            f"**Cut {delta} words** to land within {low}-{high}. "
            f"Drop background and repetition first; keep every figure and conclusion."
        )

    delta = low - units
    if lang == "zh":
        return (
            f"上一稿 {units} 字，不足下限 {low} 字。"
            f"请**补充 {delta} 字**，最终落在 {low}~{high} 字——"
            f"补技术细节、数据或对比，不要用背景介绍和套话凑长度。"
        )
    return (
        f"The previous draft is {units} words, under the {low}-word minimum. "
        f"**Add {delta} words** of technical detail, data or comparison "
        f"to land within {low}-{high}. Do not pad with background or filler."
    )


__all__ = ["char_feedback"]
