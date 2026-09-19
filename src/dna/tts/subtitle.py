"""
字幕导出 / Subtitle export —— 一段音频一条字幕，SRT。

为什么是 SRT 而不是 txt / Why SRT:
    剪映、Premiere、DaVinci 都能直接导入 SRT，导入之后**字幕自己就贴在时间轴上**。
    txt 要人手动对时间，那等于没导出。
    SRT lands on the timeline by itself; a txt file leaves the timing to a human.

两条容易错的地方 / Two things that are easy to get wrong:
    1. **时间轴必须算进段间静音。** 漏算的话字幕会越往后越提前，尾部差好几秒。
       The gaps between pieces are part of the timeline.
    2. **文件要带 BOM。** 不带的话剪映读中文 SRT 会乱码 —— 内容全对，屏幕上全是问号。
       Without a BOM, Chinese subtitles come out as mojibake in some editors.
"""

from __future__ import annotations

import re
from pathlib import Path

# 控制标记不是台词 / control markup is not dialogue
#
# `[pause:400ms]`、`[laugh]` 这些是给合成器的指令，念不出来也不该显示在字幕上。
_MARKUP = re.compile(r"\[[a-zA-Z_]+(?::[^\]]*)?\]")

# 一条字幕 / one cue: (开始秒, 结束秒, 文字)
Cue = tuple[float, float, str]


def strip_markup(text: str) -> str:
    """去掉控制标记，留下能念也能看的部分 / Drop the markup, keep the words."""
    return " ".join(_MARKUP.sub(" ", text or "").split())


def build_cues(pieces: list[tuple[float, str]], gaps: list[float]) -> list[Cue]:
    """
    逐段时长 + 段间静音 → 字幕时间轴 / Durations and gaps to a timeline.

    参数 / Args:
        pieces: `[(时长秒, 原文), …]`，顺序与音频一致
        gaps:   每段**之后**的静音秒数，最后一段那个会被忽略

    文字为空的段跳过（但**时间照走**）：空文字的字幕在编辑器里是一条看不见的
    轨道块，删起来比没有更麻烦。
    Empty text is skipped while its time still advances.
    """
    cues: list[Cue] = []
    clock = 0.0
    for index, (seconds, text) in enumerate(pieces):
        words = strip_markup(text)
        if words:
            cues.append((clock, clock + seconds, words))
        clock += seconds + (gaps[index] if index < len(gaps) else 0.0)
    return cues


def to_srt(cues: list[Cue]) -> str:
    """字幕列表 → SRT 文本 / Cues to SRT text."""
    blocks = [
        f"{number}\n{_stamp(start)} --> {_stamp(end)}\n{text}\n"
        for number, (start, end, text) in enumerate(cues, start=1)
    ]
    return "\n".join(blocks)


def write_srt(path: Path, cues: list[Cue]) -> Path | None:
    """
    写 SRT / Write the SRT file，没有字幕时返回 None（不留空文件）。

    `utf-8-sig` 是刻意的，见模块开头第 2 条。
    """
    if not cues:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_srt(cues), encoding="utf-8-sig")
    return path


def _stamp(seconds: float) -> str:
    """秒 → `HH:MM:SS,mmm` / Seconds to an SRT timestamp."""
    total = max(0.0, seconds)
    hours, rest = divmod(int(total), 3600)
    minutes, secs = divmod(rest, 60)
    millis = round((total - int(total)) * 1000)
    if millis == 1000:                      # 四舍五入到整秒，别写出 ,1000
        secs, millis = secs + 1, 0
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


__all__ = ["Cue", "build_cues", "strip_markup", "to_srt", "write_srt"]
