"""
语音合成层 / The speech synthesis layer.

与 `dna/llm/` 平级的能力层：`produce/` 调它把文案变成音频，
它不知道文案从哪来，也不知道音频要拿去做什么。
A capability layer alongside `dna/llm/`: `produce/` calls it to turn copy into audio,
and it knows neither where the copy came from nor what the audio is for.

    produce/  ──▶  tts/  ──▶  qwen3_ov     Qwen3-TTS OpenVINO（Intel CPU/核显/NPU）
                        ├──▶  qwen3_torch  Qwen3-TTS PyTorch（NVIDIA CUDA / cpu / mps）
                        └──▶  http service （部署之后补，接口不变）

两个后端的调用接口完全一样，公共部分在 `qwen3_base.py`，各自只实现「怎么加载」。
换部署环境改 `.env` 里的 `TTS_PROVIDER` 一行。

本阶段（P4.5）只做条目级的三种音频；整期播客的串联在 P7，届时复用同一套接口
与长文案已经定好的角色轮次 JSON。
"""

from dna.tts.base import (
    DEFAULT_SAMPLE_RATE,
    RTF_ESTIMATE,
    AudioClip,
    SpeechSegment,
    TTSError,
    TTSInfo,
    TTSProvider,
    VoiceSpec,
    encode_wav,
    estimate_synthesis_seconds,
    wav_seconds,
)
from dna.tts.factory import PROVIDERS, get_tts, reset_cache, voice_for_role
from dna.tts.segment import clean_for_speech, split_for_speech

__all__ = [
    "DEFAULT_SAMPLE_RATE",
    "PROVIDERS",
    "RTF_ESTIMATE",
    "AudioClip",
    "SpeechSegment",
    "TTSError",
    "TTSInfo",
    "TTSProvider",
    "VoiceSpec",
    "clean_for_speech",
    "encode_wav",
    "estimate_synthesis_seconds",
    "get_tts",
    "reset_cache",
    "split_for_speech",
    "voice_for_role",
    "wav_seconds",
]
