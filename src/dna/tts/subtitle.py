"""
字幕导出 / Subtitle export —— 段内切分后的短字幕，SRT。

**一条字幕多长、文本用哪一版**，都由 `tts/cue_split.py` 决定（那是纯函数，
可以单独测）；这里只管时间轴的拼接、SRT 的排版与落盘。
This module owns the timeline and the SRT format; `cue_split` owns the splitting.

为什么是 SRT 而不是 txt / Why SRT:
    剪映、Premiere、DaVinci 都能直接导入 SRT，导入之后**字幕自己就贴在时间轴上**。
    txt 要人手动对时间，那等于没导出。
    SRT lands on the timeline by itself; a txt file leaves the timing to a human.

三条容易错的地方 / Three things that are easy to get wrong:
    1. **时间轴必须算进段间静音。** 漏算的话字幕会越往后越提前，尾部差好几秒。
       The gaps between pieces are part of the timeline.
    2. **文件要带 BOM。** 不带的话剪映读中文 SRT 会乱码 —— 内容全对，屏幕上全是问号。
       Without a BOM, Chinese subtitles come out as mojibake in some editors.
    3. **逐段字幕的时间从 0 起算**，整条那份才是累积时间轴（见 `build_cues_segmented`）。
       Per-piece cues are zero-based; only the joined list accumulates.
"""

from __future__ import annotations

from pathlib import Path

from dna.core.logging import get_logger
from dna.tts.cue_split import SplitParams, align
from dna.tts.vad import UnsupportedWav, pause_points

logger = get_logger("tts.subtitle")

# 一条字幕 / one cue: (开始秒, 结束秒, 文字)
Cue = tuple[float, float, str]


def to_srt(cues: list[Cue]) -> str:
    """字幕列表 → SRT 文本 / Cues to SRT text."""
    blocks = [
        f"{number}\n{_stamp(start)} --> {_stamp(end)}\n{text}\n"
        for number, (start, end, text) in enumerate(cues, start=1)
    ]
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# 段内切分 / splitting a segment into subtitle-sized cues
# ---------------------------------------------------------------------------


def segment_piece(wav: bytes, spoken: str, source: str, seconds: float,
                  params: SplitParams | None = None) -> list[Cue]:
    """
    把**一段**音频切成段内字幕 / Split one piece into cues.

    `spoken` 决定条数与时间（音频是按它合成的），`source` 是要显示的文本。
    段内停顿从波形里现测 —— 段落之间的静音由调用方另算，不在这里。
    Pauses are measured from this piece's own waveform.

    读不了波形或没有停顿都**不算失败**：`align` 会退回按字数估算，
    字幕顶多偏一点，不会不出。
    An unreadable waveform degrades to an estimate rather than producing nothing.

    返回**元组**形式的 cue（本模块一直用 `Cue = tuple`），
    而 `cue_split.align` 给的是 dataclass —— 在这里转一次，免得让两种表示
    漏到调用方去。
    """
    pauses: list[float] = []
    try:
        pauses = pause_points(wav)
    except (UnsupportedWav, ValueError, OSError) as exc:
        logger.warning("停顿检测跳过这一段（%s）", " ".join(str(exc).split())[:120])
    return [(cue.start, cue.end, cue.text) for cue in align(spoken, source, seconds,
                                                            pauses, params)]


def build_cues_segmented(
    pieces: list[tuple[float, str, str]],
    wavs: list[bytes],
    gaps: list[float],
    params: SplitParams | None = None,
) -> tuple[list[Cue], list[list[Cue]]]:
    """
    逐段切分并拼出整条时间轴 / Split every piece and join the timelines.

    参数 / Args:
        pieces: `[(时长秒, 朗读稿, 原文), …]`，顺序与音频一致
        wavs:   逐段 WAV 字节，与 `pieces` 一一对应
        gaps:   每段**之后**的静音秒数，最后一段那个被忽略

    返回 / Returns:
        `(整条 cues, 逐段 cues)`。

        逐段那份的时间**从 0 起算**：它描述的是这一段自己，拿去替换某一句时
        不该还带着整条的时间偏移。整条那份是拼接后的时间轴，两者用途不同。
        Per-piece cues are zero-based; the joined list is for the final timeline.

    为什么要在段内再切 / Why split inside a piece:
        TTS 的分段上限是 120 字，一段可以念十几秒 —— 那样的一条不像字幕，
        看着累。切分的规则与文本来源见 `tts/cue_split.py`。
    """
    per_piece: list[list[Cue]] = []
    whole: list[Cue] = []
    clock = 0.0

    for index, (seconds, spoken, source) in enumerate(pieces):
        payload = wavs[index] if index < len(wavs) else b""
        cues = (segment_piece(payload, spoken, source, seconds, params) if payload
                else [(cue.start, cue.end, cue.text)
                      for cue in align(spoken, source, seconds, [], params)])
        per_piece.append(cues)
        for start, end, text in cues:
            whole.append((round(clock + start, 3), round(clock + end, 3), text))
        clock += seconds + (gaps[index] if index < len(gaps) else 0.0)

    return whole, per_piece


def piece_srt_names(pieces: list[tuple[str, bytes]]) -> list[str]:
    """
    逐段字幕的文件名 / Filenames for the per-piece subtitles.

    与逐段音频**同名同目录、只换扩展名**（`seg_001_narrator.wav` →
    `seg_001_narrator.srt`）：一片文章一个文件夹时，靠名字就能对上，
    不必再记一张表。
    Same stem as the audio, so the pairing survives a glance at the folder.
    """
    return [str(Path(name).with_suffix(".srt")) for name, _ in pieces]



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


__all__ = [
    "Cue",
    "build_cues_segmented",
    "piece_srt_names",
    "segment_piece",
    "to_srt",
    "write_srt",
]
