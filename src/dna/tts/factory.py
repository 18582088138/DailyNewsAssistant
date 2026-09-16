"""
TTS provider 工厂 / The TTS provider factory.

现在只有一个后端 —— 远端的 **TTS service**（Agent_TTS_Module）。
本项目内不再有任何模型代码：换设备（CPU → 4060）、换模型（Qwen3-TTS →
Breeze-TTS 2 / IndexTTS-2）都是服务那一侧的配置，这里一行都不用改。
One backend remains: the remote TTS service. Swapping devices or models is entirely a
service-side configuration change.

工厂仍然留着，理由和 `llm/factory.py` 一样：**上层拿到的是协议，不是实现**。
将来若要加一个「云端 TTS API」的 provider，改的只有这个文件。
The factory survives for the same reason as the LLM one: callers receive a protocol.

为什么要缓存实例 / Why instances are cached:
    provider 会缓存 `/info` 与音色清单。每点一次按钮重新构造，就要多两次
    往返去问同样的问题。
    The provider caches `/info` and the voice list; rebuilding it re-asks both.
"""

from __future__ import annotations

from pathlib import Path

from dna.core.config import Settings, get_settings
from dna.core.logging import get_logger
from dna.tts.base import TTSProvider, VoiceSpec
from dna.tts.service import (
    DEFAULT_GUEST_SPEAKER,
    DEFAULT_SPEAKER,
    PROVIDER_NAME,
    TTSServiceProvider,
)

logger = get_logger("tts.factory")

PROVIDERS = (PROVIDER_NAME,)

# 服务地址 → 实例 / cached by service address
_CACHE: dict[str, TTSProvider] = {}


def get_tts(settings: Settings | None = None, *, fresh: bool = False) -> TTSProvider:
    """
    取 TTS provider / Get the TTS provider.

    参数 / Args:
        fresh: 跳过缓存重新构造。改了 `.env` 或重启过服务想立刻生效时用

    **不在这里探活**：构造是免费的，探活与自动拉起发生在真正要合成的时候
    （见 `supervisor.ensure_service`）。否则只是想列一下音色也会先等一次超时。
    Liveness is not checked here: construction is free, and probing belongs to the
    moment work is actually requested.
    """
    s = settings or get_settings()
    key = (s.tts_service_url or "").rstrip("/")

    if fresh or key not in _CACHE:
        _CACHE[key] = TTSServiceProvider(s)
        logger.debug("TTS provider：%s @ %s", PROVIDER_NAME, key)
    return _CACHE[key]


def reset_cache() -> None:
    """丢掉缓存的实例 / Drop cached instances（服务重启后想重读 /info 时用）。"""
    _CACHE.clear()


def voice_for_role(
    role: str, settings: Settings | None = None, *, lang: str = "zh"
) -> VoiceSpec:
    """
    按角色取音色 / Pick the voice for a speaker role.

    `narrator` / `host` 用 `TTS_VOICE_HOST`，`guest` 用 `TTS_VOICE_GUEST`；
    没配就用默认值。**两个角色必须落在不同音色上**——访谈稿两个人同一个嗓子，
    听众分不出谁在说话，双角色这件事就白做了。
    Falls back to defaults when unset, and guarantees the two roles differ: an interview
    read in a single voice leaves the listener unable to tell who is speaking, which
    defeats the point of writing two parts.

    `lang` 决定的是**发音语言**，不是音色。同一把嗓子念中文和英文都行，但必须告诉
    模型这段是哪种语言——按中文念英文稿会得到一串带中文腔的拼读。
    `lang` sets the pronunciation, not the voice: the same speaker reads either language,
    but the model has to be told which, or English comes out with Chinese phonetics.
    """
    s = settings or get_settings()
    # 只有明确要英文才用英文音色。写成「不是 zh 就是 english」的话，任何没归一化过的
    # 空值都会静默变成英文音色——中文稿被用英文发音习惯念出来，而日志里什么都没有。
    language = "english" if lang == "en" else "chinese"

    # 默认走音色克隆 / cloning is the default
    #
    # 内置音色**跟着权重变**：同一个 `Serena` 在 0.6B 与 1.7B 上不是同一把嗓子，
    # 服务端换个 checkpoint 声音就变了。克隆锁的是一个音频文件，文件不换声音就不换。
    # 参考音频不存在时**不静默降级**——那会换成另一把嗓子，而且没人会发现。
    # A built-in speaker changes with the checkpoint; a reference file does not. A
    # missing file is reported rather than silently swapped for another voice.
    if (s.tts_mode or "").strip().lower() == "voice_clone":
        clone = _clone_voice(role, s, language)
        if clone is not None:
            return clone

    host = (s.tts_voice_host or "").strip() or DEFAULT_SPEAKER
    guest = (s.tts_voice_guest or "").strip() or DEFAULT_GUEST_SPEAKER

    if guest.lower() == host.lower():
        guest = DEFAULT_GUEST_SPEAKER if host != DEFAULT_GUEST_SPEAKER else DEFAULT_SPEAKER
        logger.warning(
            "TTS_VOICE_HOST 与 TTS_VOICE_GUEST 相同，嘉宾改用 %s —— "
            "两个角色同一个嗓子听不出谁在说话",
            guest,
        )

    return VoiceSpec(speaker=guest if role == "guest" else host, language=language)


def _clone_voice(role: str, s: Settings, language: str) -> VoiceSpec | None:
    """
    这个角色的克隆音色 / The cloned voice for one role，配不齐就回 None。

    嘉宾**没配自己的参考音频时回 None**，于是落回内置音色 —— 两个角色必须听得出
    区别，都克隆同一个文件的话，访谈稿两个人一把嗓子，双角色就白做了。
    A guest without its own reference falls back to a built-in speaker, because the two
    roles have to be distinguishable.
    """
    if role == "guest":
        raw, ref_text = s.tts_ref_audio_guest, s.tts_ref_audio_guest and s.tts_ref_text_guest
        if not (raw or "").strip():
            logger.info("嘉宾没配 TTS_REF_AUDIO_GUEST，改用内置音色（两个角色要听得出区别）")
            return None
    else:
        raw, ref_text = s.tts_ref_audio, s.tts_ref_text

    path = _ref_audio_path(raw, s)
    if path is None:
        logger.warning(
            "参考音频不存在：%s（相对路径按数据目录 %s 解析），改用内置音色。"
            "克隆是默认方式，这条警告意味着**声音和你预期的不是同一把嗓子**",
            raw, s.data_path,
        )
        return None

    transcript = (ref_text or "").strip()
    return VoiceSpec(
        speaker=str(path),          # 克隆没有"音色名"，这里放路径只为日志可读
        language=language,
        mode="voice_clone",
        ref_audio=str(path),
        ref_text=transcript,
        # 没有原话就只能走纯 x-vector：ICL 模式上游硬要求 ref_text，
        # 而随便编一句会让克隆质量明显变差
        x_vector_only=not transcript,
    )


def _ref_audio_path(raw: str, s: Settings) -> Path | None:
    """参考音频的绝对路径 / The absolute path of the reference audio，不存在回 None。"""
    value = (raw or "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = s.data_path / path
    return path if path.is_file() else None


__all__ = ["PROVIDERS", "get_tts", "reset_cache", "voice_for_role"]
