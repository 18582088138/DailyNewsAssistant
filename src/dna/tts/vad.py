"""
停音检测 / Finding pauses in synthesised speech.

给字幕切分用的**停顿点**。之所以自己写而不是拉一个 VAD 库：这个项目的
`tts` 那一组依赖只有 `httpx`（`pyproject.toml` 明说「连 numpy 都不需要」），
为几十秒的干净音频引入 onnxruntime 不划算。
A hand-rolled detector keeps the tts extra at one HTTP client.

**为什么能量法在这里够用 / Why energy is enough here**:
    这里的音频是 TTS **刚合成出来的**，不是现场录音：没有背景音乐、没有环境噪声、
    没有混响尾巴。静音段是真的接近零，语音段是真的有能量 —— 两者几乎不重叠。
    真实录音那一套（谱熵、自适应噪声估计）要解决的问题在这里根本不存在。
    The input is freshly synthesised, not a field recording: true silence sits near zero
    and the speech band is well above it, so the hard part of VAD does not arise.

判据 / the rule: 分帧算 RMS，低于「本段语音电平」的某个比例即判为静音；
语音电平取**高分位**而不是峰值 —— 峰值会被一个爆破音带偏，分位数不会。
Frame RMS against a high quantile of the same signal, not against its peak.
"""

from __future__ import annotations

import io
import wave
from array import array

# 分帧长度（毫秒）/ frame length
#
# 20 ms 是语音处理的常规取法：短到能分辨字与字之间的停顿，长到不至于
# 把单个辅音当成静音。再短会让帧内样点太少，RMS 抖得厉害。
FRAME_MS = 20.0

# 短于这个的低声区不算停顿 / anything shorter is not a pause
#
# 0.12 s 是「换气」的量级；更短的凹陷多半是塞音闭合（b/p/t 的成阻段），
# 切在那里会把一个字劈成两半。
MIN_SILENCE_SECONDS = 0.12

# 判定阈值：低于语音电平的这个比例算静音 / relative threshold
#
# 实测 TTS 输出里静音段与语音段相差一两个数量级，0.05 有很宽的余量。
SILENCE_RATIO = 0.05

# 语音电平取第几分位 / which quantile counts as the speech level
SPEECH_QUANTILE = 0.9

# 静音区两端向内收多少（秒）/ trim, so a cue does not eat the next onset
EDGE_PAD_SECONDS = 0.04


class UnsupportedWav(ValueError):
    """不是本项目能直接读的 WAV / a WAV this module cannot read plainly."""


def read_mono(wav: bytes) -> tuple[array, int]:
    """
    读成单声道样点 / Read a WAV into mono samples.

    返回 `(样点, 采样率)`，样点归一到 `[-1, 1]`。多声道在**读的时候**就混掉：
    后面只关心「这里有没有声音」，声道信息没有用，而留着它会让每个下游函数
    都要再判断一次维度。
    Multi-channel input is mixed down here so no downstream function has to care.

    只处理 8 / 16 / 32-bit 整数 PCM —— TTS 产物就是这些。遇到别的一律抛
    `UnsupportedWav`，**不静默返回空**：空样点会让停顿检测报「整段没停顿」，
    那看起来像「音频本来就连贯」，而实际是读不了。
    """
    try:
        with wave.open(io.BytesIO(wav), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except (wave.Error, EOFError) as exc:
        raise UnsupportedWav(f"不是可读的 WAV：{exc}") from exc

    if width == 2:
        data = array("h", frames)
        scale = 32768.0
    elif width == 1:
        data = array("b", frames)
        scale = 128.0
    elif width == 4:
        data = array("i", frames)
        scale = 2147483648.0
    else:
        raise UnsupportedWav(f"不支持的位深：{width * 8} bit")

    if channels > 1:
        # 混成单声道：逐声道取平均，避免只留左声道而那一侧恰好是空的
        mono = array("d", [0.0] * (len(data) // channels))
        for index in range(len(mono)):
            total = 0
            base = index * channels
            for channel in range(channels):
                total += data[base + channel]
            mono[index] = total / channels / scale
        return mono, rate

    return array("d", [value / scale for value in data]), rate


def frame_rms(samples: array, frame_len: int) -> list[float]:
    """逐帧 RMS / Per-frame RMS.

    最后不足一帧的尾巴也计入：丢掉它会让结尾的停顿检测不到。
    """
    if frame_len <= 0:
        return []
    frames: list[float] = []
    for start in range(0, len(samples), frame_len):
        chunk = samples[start:start + frame_len]
        if not chunk:
            continue
        total = 0.0
        for value in chunk:
            total += value * value
        frames.append((total / len(chunk)) ** 0.5)
    return frames


def speech_level(frames: list[float], quantile: float = SPEECH_QUANTILE) -> float:
    """
    本段的语音电平 / The speech level of this clip.

    用**高分位**而不是最大值：一个爆破音或一次爆音就能把峰值抬高一个数量级，
    那样的阈值会把整段都判成静音。分位数对个别异常帧不敏感。
    """
    if not frames:
        return 0.0
    ordered = sorted(frames)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * quantile)))
    return ordered[index]


def find_silences(
    wav: bytes,
    *,
    min_seconds: float = MIN_SILENCE_SECONDS,
    ratio: float = SILENCE_RATIO,
    frame_ms: float = FRAME_MS,
    edge_pad: float = EDGE_PAD_SECONDS,
) -> list[tuple[float, float]]:
    """
    找静音区间 / Locate silent spans.

    返回 `[(开始秒, 结束秒), …]`，按时间升序。找不到就返回空列表 ——
    那意味着「这一段没有可用的停顿」，调用方据此退回按字数估算。
    An empty list means no usable pause was found, not that detection failed.
    """
    samples, rate = read_mono(wav)
    if rate <= 0 or not samples:
        return []

    frame_len = max(1, int(rate * frame_ms / 1000.0))
    frames = frame_rms(samples, frame_len)
    level = speech_level(frames)
    if level <= 0.0:
        # 整段都是数字零 —— 那就是一整段静音
        return [(0.0, len(samples) / rate)]

    # 绝对下限兜底：极安静的录音里 `level * ratio` 会小到把本底噪声也算成语音
    threshold = max(level * ratio, 1e-4)

    spans: list[tuple[float, float]] = []
    start_index: int | None = None
    for index, value in enumerate(frames):
        if value < threshold:
            if start_index is None:
                start_index = index
        elif start_index is not None:
            spans.append((start_index * frame_ms / 1000.0, index * frame_ms / 1000.0))
            start_index = None
    if start_index is not None:
        spans.append((start_index * frame_ms / 1000.0, len(frames) * frame_ms / 1000.0))

    total = len(samples) / rate
    out: list[tuple[float, float]] = []
    for start, end in spans:
        start = max(0.0, start + edge_pad)
        end = min(total, end - edge_pad)
        if end - start >= min_seconds:
            out.append((round(start, 3), round(end, 3)))
    return out


def pause_points(wav: bytes, **kwargs) -> list[float]:
    """
    停顿的**中点**（秒）/ Midpoints of the silent spans.

    切字幕要的是一个边界位置，不是一个区间；取中点是因为静音的头尾常带一点
    淡入淡出，中点离两侧的语音都最远，切在那里最不容易啃到字。
    The midpoint sits furthest from both onsets.
    """
    return [round((start + end) / 2.0, 3) for start, end in find_silences(wav, **kwargs)]


__all__ = [
    "EDGE_PAD_SECONDS",
    "FRAME_MS",
    "MIN_SILENCE_SECONDS",
    "SILENCE_RATIO",
    "SPEECH_QUANTILE",
    "UnsupportedWav",
    "find_silences",
    "frame_rms",
    "pause_points",
    "read_mono",
    "speech_level",
]
