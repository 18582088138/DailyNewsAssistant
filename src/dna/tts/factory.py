"""
TTS provider 工厂 / The TTS provider factory.

和 `llm/factory.py` 同一套：**上层拿到的永远是协议，不知道背后是谁**。
Intel 机器用 OpenVINO，NVIDIA 机器用 PyTorch CUDA，将来部署成服务再加一个 provider——
`produce/` 与两个前端一行都不改。
Same shape as `llm/factory.py`: callers receive the protocol and never learn which
backend answered. Intel machines run OpenVINO, NVIDIA machines run PyTorch CUDA, and a
deployed service will be a third — none of which reaches the layers above.

为什么要缓存实例 / Why instances are cached:
    加载模型实测 10.4 秒。GUI 里每点一次「生成音频」都重新加载的话，
    这 10 秒会加在每一次等待上，而它本来只该付一次。
    Loading measured 10.4 seconds. Reloading on every click would add that to every wait
    when it need only be paid once.
"""

from __future__ import annotations

from pathlib import Path

from dna.core.config import Settings, get_settings
from dna.core.logging import get_logger
from dna.tts.base import TTSError, TTSProvider, VoiceSpec
from dna.tts.qwen3_base import DEFAULT_GUEST_SPEAKER, DEFAULT_SPEAKER
from dna.tts.qwen3_openvino import PROVIDER_NAME as OPENVINO_PROVIDER
from dna.tts.qwen3_openvino import Qwen3OpenVINOTTS
from dna.tts.qwen3_torch import PROVIDER_NAME as TORCH_PROVIDER
from dna.tts.qwen3_torch import Qwen3TorchTTS

logger = get_logger("tts.factory")

PROVIDERS = (OPENVINO_PROVIDER, TORCH_PROVIDER)

# (provider, 模型目录, 设备, 精度) → 实例 / cached by backend identity
_CACHE: dict[tuple[str, str, str, str], TTSProvider] = {}


def get_tts(settings: Settings | None = None, *, fresh: bool = False) -> TTSProvider:
    """
    按配置取一个 TTS provider / Get the configured TTS provider.

    参数 / Args:
        fresh: 跳过缓存，重新构造。改了 `.env` 想立刻生效时用

    抛出 / Raises:
        TTSError: `TTS_PROVIDER` 不认识，或该后端必需的路径没配
    """
    s = settings or get_settings()
    name = (s.tts_provider or OPENVINO_PROVIDER).strip().lower()
    repo_dir = _optional_path(s.qwen3_tts_repo_dir)

    if name == OPENVINO_PROVIDER:
        model_dir = _required_path(
            s.qwen3_tts_model_dir, "QWEN3_TTS_MODEL_DIR", "已转换的 OpenVINO IR 目录"
        )
        device = (s.tts_device or "GPU").strip().upper()
        key = (name, str(model_dir), device, "")
        if fresh or key not in _CACHE:
            _CACHE[key] = Qwen3OpenVINOTTS(
                model_dir,
                device=device,
                helper_dir=_optional_path(s.qwen3_tts_helper_dir),
                repo_dir=repo_dir,
            )

    elif name == TORCH_PROVIDER:
        model_dir = _required_path(
            s.qwen3_tts_torch_model_dir,
            "QWEN3_TTS_TORCH_MODEL_DIR",
            "原始 HuggingFace 权重目录",
        )
        device = (s.tts_device or "cuda:0").strip().lower()
        dtype = (s.tts_dtype or "bfloat16").strip().lower()
        key = (name, str(model_dir), device, dtype)
        if fresh or key not in _CACHE:
            _CACHE[key] = Qwen3TorchTTS(
                model_dir, device=device, dtype=dtype, repo_dir=repo_dir
            )

    else:
        raise TTSError(
            f"未知的 TTS provider：{name}　可选：{'、'.join(PROVIDERS)}。"
            "服务化的 http provider 在 TTS 部署成服务之后补上。"
        )

    provider = _CACHE[key]
    logger.debug("TTS provider：%s", provider.info)
    return provider


def reset_cache() -> None:
    """丢掉缓存的实例，释放显存 / Drop cached instances and free device memory."""
    _CACHE.clear()


def voice_for_role(role: str, settings: Settings | None = None) -> VoiceSpec:
    """
    按角色取音色 / Pick the voice for a speaker role.

    `narrator` / `host` 用 `TTS_VOICE_HOST`，`guest` 用 `TTS_VOICE_GUEST`；
    没配就用默认值。**两个角色必须落在不同音色上**——访谈稿两个人同一个嗓子，
    听众分不出谁在说话，双角色这件事就白做了。
    Falls back to defaults when unset, and guarantees the two roles differ: an interview
    read in a single voice leaves the listener unable to tell who is speaking, which
    defeats the point of writing two parts.
    """
    s = settings or get_settings()
    host = (s.tts_voice_host or "").strip() or DEFAULT_SPEAKER
    guest = (s.tts_voice_guest or "").strip() or DEFAULT_GUEST_SPEAKER

    if guest == host:
        guest = DEFAULT_GUEST_SPEAKER if host != DEFAULT_GUEST_SPEAKER else DEFAULT_SPEAKER
        logger.warning(
            "TTS_VOICE_HOST 与 TTS_VOICE_GUEST 相同，嘉宾改用 %s —— "
            "两个角色同一个嗓子听不出谁在说话",
            guest,
        )

    return VoiceSpec(speaker=guest if role == "guest" else host, language="chinese")


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _required_path(raw: str, key: str, what: str) -> Path:
    """必填路径 / A path the backend cannot start without."""
    value = (raw or "").strip()
    if not value:
        raise TTSError(f"没有配置 {key}（{what}），无法合成语音")
    return Path(value)


def _optional_path(raw: str) -> Path | None:
    value = (raw or "").strip()
    return Path(value) if value else None


__all__ = ["PROVIDERS", "get_tts", "reset_cache", "voice_for_role"]
