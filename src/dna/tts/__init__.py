"""
语音合成层 / The speech synthesis layer —— 一层**很薄的**服务客户端。

与 `dna/llm/` 平级的能力层：`produce/` 调它把文案变成音频，
它不知道文案从哪来，也不知道音频要拿去做什么。
A capability layer alongside `dna/llm/`: `produce/` calls it to turn copy into audio.

    produce/  ──▶  tts/  ──▶  TTS service（Agent_TTS_Module，HTTP）
                        │        权重加载 / 轮动换权重 / 失控重试 / 克隆 / 设计 / 字幕
                        └──▶  TTS 图形界面（「高级配置」跳过去精修）

**本项目内没有任何语音模型代码。** 先前的两个本地后端（OpenVINO / PyTorch）已经
删掉：同一套东西维护两份，两边的音质与参数迟早对不上，而 TTS 模块已经把这件事
做完并验证过了。换设备、换模型都是服务那边的配置。
No model code lives here any more. The two local backends were deleted: maintaining a
second copy of the same thing guarantees the two drift apart.

四件事在这一层 / Four things live in this layer:
    `preprocess` 朗读友好化（一次 LLM 调用）　`segment` 清洗与分段
    `service`    逐段合成 + 拼接　　　　　　　`supervisor` 探活与自动拉起
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
from dna.tts.client import TTSServiceClient
from dna.tts.factory import PROVIDERS, get_tts, reset_cache, voice_for_role
from dna.tts.preprocess import PreparedSpeech, prepare_for_speech
from dna.tts.segment import clean_for_speech, split_for_speech
from dna.tts.service import DEFAULT_GUEST_SPEAKER, DEFAULT_SPEAKER, PROVIDER_NAME
from dna.tts.supervisor import ServiceStatus, ensure_gui, ensure_service

__all__ = [
    "DEFAULT_GUEST_SPEAKER",
    "DEFAULT_SAMPLE_RATE",
    "DEFAULT_SPEAKER",
    "PROVIDERS",
    "PROVIDER_NAME",
    "RTF_ESTIMATE",
    "AudioClip",
    "PreparedSpeech",
    "ServiceStatus",
    "SpeechSegment",
    "TTSError",
    "TTSInfo",
    "TTSProvider",
    "TTSServiceClient",
    "VoiceSpec",
    "clean_for_speech",
    "encode_wav",
    "ensure_gui",
    "ensure_service",
    "estimate_synthesis_seconds",
    "get_tts",
    "prepare_for_speech",
    "reset_cache",
    "split_for_speech",
    "voice_for_role",
    "wav_seconds",
]
