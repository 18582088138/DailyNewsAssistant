"""逐段与整条字幕的产出 / Per-piece and whole subtitle output.

复测 / rerun:
    C:\\Users\\75203\\miniforge3\\envs\\ov_env_py312\\python.exe -m pytest tests/tts/test_subtitle.py -q

覆盖 / covers: `build_cues_segmented` 的时间轴拼接、逐段 srt 的命名与落盘、
原文优先于朗读稿。
不含 / excludes: 真实 TTS 音频（`FakeTTS` 给的是静音波形，没有真实停顿）。
说明文档 / docs: docs/14_tts_guide.md
"""

from __future__ import annotations

import io
import math
import struct
import wave

from dna.tts.subtitle import (
    build_cues_segmented,
    piece_srt_names,
    segment_piece,
    to_srt,
    write_srt,
)

RATE = 24_000


def make_wav(spans: list[tuple[str, float]], rate: int = RATE) -> bytes:
    """语音/静音交替的 WAV（与 test_vad.py 同一个造法）。"""
    frames = bytearray()
    for kind, seconds in spans:
        for index in range(int(rate * seconds)):
            value = (int(0.6 * 32767 * math.sin(2 * math.pi * 220 * index / rate))
                     if kind == "tone" else 0)
            frames += struct.pack("<h", value)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


def test_piece_cues_start_at_zero() -> None:
    """逐段字幕的时间**从 0 起算** —— 它描述这一段自己，不带整条的偏移。

    换某一句的时候要的就是这一段的独立时间轴；带着偏移反而没法直接用。
    """
    wav = make_wav([("tone", 1.0), ("silence", 0.5), ("tone", 1.0)])
    cues = segment_piece(wav, "第一句。第二句。", "第一句。第二句。", 2.5)
    assert cues
    assert cues[0][0] == 0.0


def test_whole_timeline_accumulates_gaps() -> None:
    """整条时间轴要把**段间静音**算进去，否则字幕越往后越提前。"""
    pieces = [(2.0, "甲。", "甲。"), (2.0, "乙。", "乙。")]
    wavs = [make_wav([("tone", 2.0)]), make_wav([("tone", 2.0)])]
    whole, per_piece = build_cues_segmented(pieces, wavs, [1.0, 0.0])
    assert len(per_piece) == 2
    assert len(whole) == 2
    # 第二段从 2.0 + 1.0 = 3.0 秒开始
    assert abs(whole[1][0] - 3.0) < 0.05, f"段间静音没算进去：{whole}"


def test_whole_timeline_is_monotonic() -> None:
    pieces = [(3.0, "一。二。三。", "一。二。三。")] * 3
    wavs = [make_wav([("tone", 3.0)]) for _ in range(3)]
    whole, _ = build_cues_segmented(pieces, wavs, [0.3, 0.3, 0.0])
    starts = [start for start, _, _ in whole]
    assert starts == sorted(starts), f"时间轴倒挂了：{starts}"
    assert all(end > start for start, end, _ in whole)


def test_segmented_display_source_not_spoken() -> None:
    """字幕显示原文：朗读稿里的同音字与合成指令都不该出现在屏幕上。"""
    spoken = "模形精度很高[pause:400ms]接下来看结果。"
    source = "模型精度很高，接下来看结果。"
    cues = segment_piece(make_wav([("tone", 2.0)]), spoken, source, 2.0)
    shown = "".join(text for _, _, text in cues)
    assert "模型" in shown
    assert "pause" not in shown


def test_piece_srt_names_match_audio() -> None:
    """逐段字幕与逐段音频**同名**，只换扩展名。"""
    pieces = [("seg_001_narrator.wav", b"x"), ("seg_002_Serena.wav", b"y")]
    assert piece_srt_names(pieces) == ["seg_001_narrator.srt", "seg_002_Serena.srt"]


def test_write_srt_keeps_bom(tmp_path) -> None:
    """SRT 要带 BOM，否则剪映读中文会乱码。"""
    target = write_srt(tmp_path / "a.srt", [(0.0, 1.0, "你好。")])
    assert target is not None
    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "少了 UTF-8 BOM"
    assert "你好。" in raw.decode("utf-8-sig")


def test_write_srt_empty_returns_none(tmp_path) -> None:
    """没有字幕时不留下一个空文件 —— 空文件比没有更麻烦。"""
    assert write_srt(tmp_path / "empty.srt", []) is None
    assert not (tmp_path / "empty.srt").exists()


def test_to_srt_numbers_cues_from_one() -> None:
    body = to_srt([(0.0, 1.5, "甲。"), (1.5, 3.0, "乙。")])
    lines = body.splitlines()
    assert lines[0] == "1"
    assert "-->" in lines[1]


def test_build_cues_segmented_survives_unreadable_wav() -> None:
    """波形读不了时退回按字数估算，**不能不出字幕**。"""
    pieces = [(4.0, "第一句在这里。第二句也在。", "第一句在这里。第二句也在。")]
    whole, per_piece = build_cues_segmented(pieces, [b"not a wav"], [0.0])
    assert whole and per_piece[0], "读不了波形就完全没字幕，属于降级失败"
    assert abs(whole[-1][1] - 4.0) < 0.5
