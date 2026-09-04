"""
文案层 / Narration layer：短视频稿 · 口播稿 · 长文案（专题/访谈）。

    from dna.narration import build_short_video, build_narration, build_longform

三种时长，一套专业性要求 / Three durations, one set of professionalism rules:

| 产物 | 时长 | 字数 | 角色 |
|---|---|---|---|
| 短视频 | 25~35s | 110~160 | 单 |
| 口播 | 1~2min | 270~540 | 单 |
| 长文案 | 10~15min | 2700~4000 | 专题单角色 / 访谈双角色 |

共同的写作约束写在 `script_builder.PROFESSIONALISM` 里，三种文案都套用它：
准确的技术术语、数值与原文完全一致、不确定的宁可不写。
The shared writing rules live in `script_builder.PROFESSIONALISM` and apply to all three.

**时长是硬约束**，因此生成后由 `duration` 量一遍，超区间带着具体差值回炉重写
（最多 2 次）。长文案不量总时长——它是分节生成的，按节控制字数更可靠。
Duration is a hard constraint, so output is measured and sent back with a concrete
delta when it misses the window. The long-form script controls length per section
instead, which is more reliable than measuring the whole.
"""

from dna.narration.duration import estimate_seconds, length_feedback, target_chars, within
from dna.narration.longform import (
    LongformMode,
    LongformResult,
    build_longform,
    can_build_longform,
    plan_target_chars,
)
from dna.narration.script_builder import (
    PROFESSIONALISM,
    ScriptResult,
    build_narration,
    build_short_video,
)

__all__ = [
    "PROFESSIONALISM",
    "LongformMode",
    "LongformResult",
    "ScriptResult",
    "build_longform",
    "build_narration",
    "build_short_video",
    "can_build_longform",
    "estimate_seconds",
    "length_feedback",
    "plan_target_chars",
    "target_chars",
    "within",
]
