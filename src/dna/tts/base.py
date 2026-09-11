"""
TTS 抽象层 / The text-to-speech abstraction.

和 `dna/llm/` 完全同一套做法：**上层只认这个协议，不认具体实现**。
这套协议当初就是按「将来会换成服务」设计的，所以本地后端删掉、换成
`service.TTSServiceProvider` 之后，`produce/` 与两个前端**一行都没改**。
Mirrors `dna/llm/`. The protocol was written anticipating a service, which is why
replacing the local backends with an HTTP one changed nothing above this layer.

为什么 provider 收「一串分段」而不是「一段文本」/ Why providers take a list of segments:
    长文本必须切开合成再拼接（整段送进去又慢又容易崩），而**拼接必须在
    provider 内部完成**——只有它知道采样率、知道段间该留多长的静音。
    让上层自己拼，就等于把音频细节漏进业务层，而且每个 provider 漏得还不一样。
    双人访谈也落在同一个接口上：一个 turn 就是一个分段，各带各的音色。
    Long text must be synthesised in pieces and joined, and joining belongs inside the
    provider, which alone knows the sample rate and the pause length between pieces.
    Two-speaker interviews use the same interface: one turn is one segment with its own
    voice.

为什么返回 WAV 字节而不是 numpy 数组 / Why a WAV byte string rather than a numpy array:
    将来的 HTTP service 只能回字节。现在就定成字节，换 provider 时接口不变；
    定成 numpy 的话，服务化那天上层要跟着改。
    A future HTTP service can only return bytes. Fixing the contract as bytes now means
    the interface survives that move unchanged.
"""

from __future__ import annotations

import io
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from dna.core.errors import DNAError

# Qwen3-TTS 的输出采样率 / the sample rate Qwen3-TTS emits
DEFAULT_SAMPLE_RATE = 24_000

# 实测的实时率 / measured real-time factor
#
# 用来在**按下按钮之前**告诉人要等多久——长文案 15 分钟的稿子要算约 37 分钟，
# 这个数字不摆出来，人会以为界面卡死了。
# Used to state the wait before the button is pressed; without it the interface merely
# looks frozen for half an hour.
#
# ⚠️ 这是**一个 GPU 上的估算值**（2026-09-04 实测 0.6B OpenVINO 核显：63 字 →
# 11.0 秒音频、耗时 27.3 秒，RTF≈2.48）。服务侧换成 1.7B 之后：4060 上量级相近，
# **CPU 上实测 RTF≈13**，也就是这里会**少报五倍**。
# 没有跟着服务动态调整，是因为准确的等待时间要靠服务端逐段回报，而那已经在
# 界面的进度条上了 —— 这个常量只负责「点之前给个数量级」。
# The constant gives an order of magnitude before the click; the accurate figure comes
# from the service's per-piece progress, which the workbench already displays.
RTF_ESTIMATE = 2.5

# 段与段之间的静音 / silence inserted between segments
#
# 不留静音的话，两句会黏在一起像抢话；留太长又显得断裂。
# 双人访谈的换人处需要更长一点，听感上才像两个人在对话。
# Without a gap the sentences run together; too long and the delivery falls apart.
# A speaker change needs a longer one to sound like a conversation.
PAUSE_SECONDS = 0.25
SPEAKER_CHANGE_PAUSE_SECONDS = 0.5


class TTSError(DNAError):
    """语音合成失败 / Speech synthesis failed."""


@dataclass(frozen=True)
class VoiceSpec:
    """
    一个音色 / One voice.

    `instruct` 是用自然语言描述语气（「沉稳，语速稍慢」），Qwen3-TTS 支持；
    换成别的后端时不支持就忽略，不该因此让整个接口分叉。
    `instruct` describes the delivery in words. A backend that cannot honour it ignores
    it rather than forcing the interface to fork.
    """

    speaker: str
    language: str = "auto"
    instruct: str = ""

    mode: str = "custom_voice"
    """
    怎么发声 / How the voice is produced：`custom_voice` | `voice_clone` | `voice_design`。

    显式带上而不是让后端猜：`ref_audio` 一填就当克隆、一空就当内置音色这种推断，
    在「配了参考音频但文件不存在」时会**静默降级成另一把嗓子**，
    而那正是最需要报错的时刻。
    Carried explicitly rather than inferred: inferring from the presence of `ref_audio`
    would silently fall back to a different voice exactly when an error is wanted.
    """

    ref_audio: str = ""
    """克隆用的参考音频（本地路径）/ the reference audio for cloning."""

    ref_text: str = ""
    """参考音频里说的原话；空 = 走纯 x-vector / empty means x-vector-only cloning."""

    x_vector_only: bool = False
    """只取音色向量，不需要原话 / take the timbre only, no transcript needed."""

    seed: int | None = None
    """
    采样种子 / the sampling seed；None = 由服务端按配置决定。

    同一段文本换个种子会念出明显不同的一版，这是「这句念得不好听」时唯一的补救——
    文本、音色、参数都没错，只是这一次采样不好。所以它是**每段一个值**，
    不是全局设置：重掷的对象永远是某一段。
    Re-rolling the seed is the only remedy when the text and the voice are both right and
    only this take sounds wrong, so it belongs to the piece rather than the run.
    """


@dataclass(frozen=True)
class SpeechSegment:
    """
    一个待合成的片段 / One piece to synthesise.

    `role` 只用于日志与进度显示（「主持人 3/12」），合成本身只看 `voice`。
    The role is for logs and progress only; synthesis itself reads the voice.
    """

    text: str
    voice: VoiceSpec
    role: str = "narrator"

    pause_ms: int | None = None
    """
    这一段之后的静音 / the silence after this piece；None = 用 `PAUSE_SECONDS` 那套默认。

    只在人明确要求某处停久一点时才给值（操作台里插 `[pause:…]` 或调这一段）。
    默认交给上面那两个常量，是因为「换角色处停得更长」这条规则应当全局一致，
    不该由每段各自记一份。
    """


@dataclass(frozen=True)
class TTSInfo:
    """provider 的身份，记进台账 / The provider's identity, recorded in the ledger."""

    name: str
    model: str
    device: str

    def __str__(self) -> str:
        return f"{self.name} / {self.model} @ {self.device}"


@dataclass
class AudioClip:
    """
    一段合成好的音频 / One synthesised clip.

    `seconds` 是**真实时长**，不是估算——音频存在之后就没有必要再估了，
    台账里记真实值，界面显示的就不会和文件对不上。
    The duration is measured, not estimated: once the audio exists there is nothing left
    to guess, and recording the real value keeps the ledger and the file in agreement.
    """

    wav: bytes
    sample_rate: int = DEFAULT_SAMPLE_RATE
    seconds: float = 0.0
    segments: int = 0
    failed_segments: list[int] = field(default_factory=list)

    run: str = ""
    """服务端这次的产物目录名 / the run directory on the service side."""

    artifacts: list[str] = field(default_factory=list)
    """
    服务端自己留下的产物，形如 `run/文件名` / What the service kept, as `run/file`.

    只用于**溯源与日志**（这条音频的每一段在服务端叫什么）。
    要落到本地的逐段文件走 `pieces`，见下面那条为什么。
    For provenance only; local copies come from `pieces`.
    """

    cues: list[tuple[float, float, str]] = field(default_factory=list)
    """
    字幕时间轴 `(开始秒, 结束秒, 原文)` / the subtitle timeline.

    在 provider 里算，因为只有它知道**段间静音有多长** —— 上层拿到的是一条
    拼好的音频，从里面反推不出切点。漏算静音会让字幕越往后越提前。
    Computed by the provider, which alone knows the gap lengths; they cannot be
    recovered from the joined audio.
    """

    pieces: list[tuple[str, bytes]] = field(default_factory=list)
    """
    逐段音频的**本地副本**（文件名, WAV 字节）/ Per-piece audio, ready to write.

    为什么不回头去服务端下载 / Why these are not re-downloaded:
        服务端按「第几段 + 音色」命名（`seg_001_Serena.wav`）。一次合成里**同一个
        音色出现两次**时，两段会写到同一个文件名上，后一段把前一段覆盖掉 ——
        再下载回来就是两份相同的音频，而且是错的那一份。
        逐段的字节本来就已经在手上（合成时逐段回传的），直接写盘既准确又省一次往返。
        The service names files by piece index and voice, so two pieces sharing a voice
        collide and the later one overwrites the earlier. The bytes are already in hand.
    """

    @property
    def complete(self) -> bool:
        """是否每一段都合成成功 / Whether every segment succeeded."""
        return not self.failed_segments


# 进度回调：(已完成段数, 总段数, 已产出秒数) / (done, total, seconds so far)
ProgressFn = Callable[[int, int, float], None]


@runtime_checkable
class TTSProvider(Protocol):
    """语音合成后端 / A speech synthesis backend."""

    @property
    def info(self) -> TTSInfo:
        """身份信息 / Identity, recorded alongside every production."""
        ...

    def available_speakers(self) -> list[str]:
        """可用音色 / The voices this backend offers."""
        ...

    def synthesize(
        self,
        segments: Sequence[SpeechSegment],
        *,
        on_progress: ProgressFn | None = None,
        run: str | None = None,
        rendered: dict[int, bytes] | None = None,
    ) -> AudioClip:
        """
        合成并拼接 / Synthesise every segment and join them.

        参数 / Args:
            run: 后端侧的产物目录名。**同一格音频每次都给同一个名字**，
                 于是重做覆盖原目录，而不是每次多留一份（见 produce/service.py）。
                 A stable name per cell, so a redo overwrites instead of accumulating.
            rendered: `{段号: wav 字节}`，这些段直接用现成波形，不再请求后端。
                 后端不支持复用时忽略它即可——那只是慢一点，不是错。
                 A backend that cannot reuse may ignore this; the result is slower, not wrong.

        抛出 / Raises:
            TTSError: 后端不可用，或**一段都没成功**
        """
        ...


def encode_wav(samples, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """
    把 float32 波形编码成 16-bit PCM WAV 字节 / Encode a float waveform as 16-bit PCM WAV.

    用标准库的 `wave` 而不是 `soundfile`：这一步只是打包，没有重采样也没有格式转换，
    多拉一个二进制依赖进来不划算，而 `soundfile` 在 provider 内部读模型输出时才真的需要。
    Uses the standard library rather than `soundfile`: this step only frames the samples,
    so it does not justify a binary dependency.

    输入超出 [-1, 1] 时按峰值归一化 / Peaks beyond the unit range are normalised:
        直接裁剪会产生刺耳的削波失真，而模型偶尔会给出略微越界的样点。
        Clipping would produce audible distortion, and the model does occasionally
        return samples slightly out of range.
    """
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1.0:
        audio = audio / peak

    pcm = (audio * 32767.0).astype("<i2")

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def wav_seconds(wav: bytes) -> float:
    """读回 WAV 的时长 / Read a WAV's duration back."""
    with wave.open(io.BytesIO(wav), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
    return frames / rate if rate else 0.0


def estimate_synthesis_seconds(script_seconds: float, *, rtf: float = RTF_ESTIMATE) -> float:
    """
    合成这段稿子大约要等多久 / How long synthesising this script will take.

    在按钮上显示，而不是让人对着转圈猜。
    Shown on the button rather than left for the spinner to imply.
    """
    return max(0.0, script_seconds) * rtf


__all__ = [
    "DEFAULT_SAMPLE_RATE",
    "PAUSE_SECONDS",
    "RTF_ESTIMATE",
    "SPEAKER_CHANGE_PAUSE_SECONDS",
    "AudioClip",
    "ProgressFn",
    "SpeechSegment",
    "TTSError",
    "TTSInfo",
    "TTSProvider",
    "VoiceSpec",
    "encode_wav",
    "estimate_synthesis_seconds",
    "wav_seconds",
]
