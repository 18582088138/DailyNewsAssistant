"""
一次生成的结果与生成器的产出 / The two records a production run passes around.

单独一个文件是因为**谁都要用它**：编排层、两个生成器、前端都要。
放在 `service.py` 里的话，`audio.py` 为了一个 dataclass 就得反过来 import 编排层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dna.core.logging import get_logger
from dna.produce.tasks import (
    ProductionKind,
    spec,
)

logger = get_logger("produce.service")

@dataclass
class ProduceResult:
    """
    一次生成的结果 / The outcome of one production run.

    `skipped` 与 `ok` 是两回事：跳过表示「已有产物，没花钱」，
    成功表示「刚生成，花了钱」。GUI 要能把这两种情况区分显示。
    `skipped` and `ok` are different: skipped means an existing production was reused at
    no cost, ok means it was just generated and billed. The workbench distinguishes them.
    """

    kind: ProductionKind
    ok: bool
    skipped: bool = False
    path: Path | None = None
    chars: int = 0
    seconds: float | None = None
    calls: int = 0
    error: str = ""
    within_target: bool = True

    cues: list[tuple[float, float, str]] = field(default_factory=list)
    """
    **实际写进 .srt 的**字幕条目 / the cues actually written to the `.srt`.

    只有落盘成功才填：界面拿它报「字幕 N 条」，而 `tts_subtitles` 关掉时
    字幕是算出来了但没写文件——照 `Generated.cues` 报数就是报一个不存在的文件。
    """

    def summary(self) -> str:
        """一行摘要 / A one-line summary."""
        label = spec(self.kind).label
        if self.skipped:
            return f"{label}：已存在，跳过（未调用 LLM）"
        if not self.ok:
            return f"{label}：失败 —— {self.error}"

        parts = [f"{self.chars} 字"]
        if self.seconds is not None:
            parts.append(f"约 {self.seconds:.0f} 秒")
        # 超长提醒不能挂在 seconds 上：**总结没有秒数**，挂进去就永远不显示，
        # 而总结正是最容易写超的一种。字数才是验收标准（见 `core/length.py`）。
        # The warning cannot hang off `seconds`: a summary has none, so it would never
        # appear — and the summary is the kind that most often overruns.
        if not self.within_target:
            parts.append("⚠️ 超出目标字数区间")
        # 音频不调 LLM，报「0 次调用」只会让人以为出了问题
        # Audio makes no LLM calls; reporting zero of them would read as a fault.
        if self.calls:
            parts.append(f"{self.calls} 次调用")
        return f"{label}：{'，'.join(parts)}"


@dataclass
class Generated:
    """
    生成器的产出 / What a generator hands back.

    文本产物填 `text`，音频产物填 `audio`，**两者互斥**。
    用一个结构而不是一串元组：加一种载荷（将来的图片、字幕）时，
    改一个字段而不是改所有调用点的解包。
    Text kinds fill `text`, audio kinds fill `audio`. A record rather than a tuple, so a
    future payload kind adds a field instead of breaking every unpacking site.
    """

    text: str = ""
    audio: bytes | None = None
    chars: int = 0
    """
    记进台账的字数：**正文本身**，不含抬头与主副标题；音频产物是被朗读的字数。

    不能填 `len(text)`：抬头带着标题和整条 URL，一篇 250 字的短视频稿会显示成
    454 字，与 `seconds`（只量正文）和文件里那行「约 N 秒 · M 字」互相矛盾——
    三个数字说的不是同一件事，人只会以为是哪里算错了。
    Never the whole document: the header carries the title and a full URL, so a 250-word
    script reports as 454 and contradicts both `seconds` and the file's own count.
    """
    seconds: float | None = None
    """文本产物是估算时长，音频产物是**真实时长**——音频存在之后没必要再估。"""
    calls: int = 0
    within_target: bool = True
    sidecar: dict | None = None

    cues: list[tuple[float, float, str]] = field(default_factory=list)
    """音频产物的字幕时间轴 / the subtitle timeline of an audio production."""
