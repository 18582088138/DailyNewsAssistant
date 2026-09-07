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
    host = (s.tts_voice_host or "").strip() or DEFAULT_SPEAKER
    guest = (s.tts_voice_guest or "").strip() or DEFAULT_GUEST_SPEAKER

    if guest.lower() == host.lower():
        guest = DEFAULT_GUEST_SPEAKER if host != DEFAULT_GUEST_SPEAKER else DEFAULT_SPEAKER
        logger.warning(
            "TTS_VOICE_HOST 与 TTS_VOICE_GUEST 相同，嘉宾改用 %s —— "
            "两个角色同一个嗓子听不出谁在说话",
            guest,
        )

    language = "chinese" if lang == "zh" else "english"
    return VoiceSpec(speaker=guest if role == "guest" else host, language=language)


__all__ = ["PROVIDERS", "get_tts", "reset_cache", "voice_for_role"]
