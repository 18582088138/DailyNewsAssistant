"""停顿检测 / Pause detection.

复测 / rerun:
    C:\\Users\\75203\\miniforge3\\envs\\ov_env_py312\\python.exe -m pytest tests/tts/test_vad.py -q

覆盖 / covers: 分帧 RMS、语音电平取分位、静音区间、位深与声道的处理。
不含 / excludes: 真实 TTS 音频上的检出率（那要真合成，见 `-m live`）。
说明文档 / docs: docs/14_tts_guide.md
"""

from __future__ import annotations

import io
import math
import struct
import wave

import pytest

from dna.tts.vad import (
    UnsupportedWav,
    find_silences,
    frame_rms,
    pause_points,
    read_mono,
    speech_level,
)

RATE = 24_000


def make_wav(spans: list[tuple[str, float]], *, rate: int = RATE,
             channels: int = 1, width: int = 2) -> bytes:
    """
    造一段 WAV / Build a WAV out of alternating tone and silence.

    `spans` 是 `[("tone" | "silence", 秒数), …]`。语音用正弦而不是噪声：
    噪声的帧 RMS 起伏大，会让「静音判定」这条用例变得不稳定。
    A sine rather than noise: noise makes frame RMS jittery and the test flaky.
    """
    frames = bytearray()
    for kind, seconds in spans:
        count = int(rate * seconds)
        for index in range(count):
            if kind == "tone":
                value = int(0.6 * 32767 * math.sin(2 * math.pi * 220 * index / rate))
            else:
                value = 0
            for _ in range(channels):
                if width == 2:
                    frames += struct.pack("<h", value)
                else:
                    frames += struct.pack("<b", max(-128, min(127, value >> 8)))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


def test_finds_the_gap_between_two_utterances() -> None:
    """语音—静音—语音 中间那段静音必须被找到。"""
    wav = make_wav([("tone", 1.0), ("silence", 0.6), ("tone", 1.0)])
    spans = find_silences(wav)
    assert len(spans) == 1, f"应当只有一处静音，实际：{spans}"
    start, end = spans[0]
    # 静音从 1.0 s 开始、1.6 s 结束；边缘各收了 0.04 s
    assert 1.0 <= start <= 1.1, f"静音起点不对：{start}"
    assert 1.5 <= end <= 1.6, f"静音终点不对：{end}"


def test_short_dip_is_not_a_pause() -> None:
    """太短的凹陷不算停顿 —— 塞音闭合（b/p/t 的成阻段）就是这种形状。"""
    wav = make_wav([("tone", 1.0), ("silence", 0.05), ("tone", 1.0)])
    assert find_silences(wav) == []


def test_all_silence_returns_the_whole_span() -> None:
    """整段静音返回整段，而不是空 —— 空会被当成「没有停顿可用」。"""
    wav = make_wav([("silence", 1.0)])
    spans = find_silences(wav)
    assert len(spans) == 1
    assert spans[0][1] - spans[0][0] >= 0.9


def test_tone_only_has_no_silence() -> None:
    assert find_silences(make_wav([("tone", 1.0)])) == []


def test_pause_points_are_midpoints() -> None:
    wav = make_wav([("tone", 1.0), ("silence", 0.6), ("tone", 1.0)])
    points = pause_points(wav)
    assert len(points) == 1
    assert 1.2 <= points[0] <= 1.4, f"停顿中点应在静音中间，实际：{points[0]}"


def test_stereo_is_mixed_down() -> None:
    """双声道要混成单声道：只留左声道的话，右侧有声音时会被误判为静音。"""
    wav = make_wav([("tone", 0.5), ("silence", 0.4), ("tone", 0.5)], channels=2)
    samples, rate = read_mono(wav)
    assert rate == RATE
    assert abs(len(samples) / rate - 1.4) < 0.01, "混音后总时长应对得上"
    assert len(find_silences(wav)) == 1


def test_8bit_is_readable() -> None:
    wav = make_wav([("tone", 0.5), ("silence", 0.4), ("tone", 0.5)], width=1)
    assert len(find_silences(wav)) == 1


def test_unsupported_depth_raises() -> None:
    """位深不认识时**抛错**，不静默返回空样点。

    返回空会让停顿检测报「整段没停顿」——那看起来像「音频本来就连贯」，
    而实际是读不了。这两种情况必须能区分。
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(3)
        handle.setframerate(RATE)
        handle.writeframes(b"\x00" * 300)
    with pytest.raises(UnsupportedWav):
        read_mono(buffer.getvalue())


def test_not_a_wav_raises() -> None:
    with pytest.raises(UnsupportedWav):
        read_mono(b"this is not a wav file at all")


def test_speech_level_ignores_a_single_loud_frame() -> None:
    """语音电平取高分位：一个爆音不该把整段判成静音。"""
    frames = [0.01] * 99 + [5.0]
    level = speech_level(frames)
    assert level < 0.5, f"分位数被单个峰值带偏了：{level}"


def test_frame_rms_covers_the_tail() -> None:
    """不足一帧的尾巴也要计入，否则结尾的停顿检测不到。"""
    samples = [0.0] * 5
    assert len(frame_rms(samples, 2)) == 3
