"""
TTS 操作台的后端能力 / Backend helpers for the TTS console.

界面要问三件事：有哪些音色、有哪些参考音频、某个参考音频到底解析到哪个文件。
这三件都不能在前端自己算——音色表在服务端，参考音频的相对路径按 `data_dir`
解析（不是仓库根），前端另写一套的结果就是「界面上看着有、合成时找不到」。
The UI needs the voice list, the reference-audio candidates, and how a given path
resolves. None of these may be recomputed in a front-end: the voice list lives in the
service and relative reference paths resolve against `data_dir`, not the repo root.

**这里的函数都不产生费用**：本地 TTS 全程零费用，而且这三个连合成都不做。
"""

from __future__ import annotations

from pathlib import Path

from dna.core.config import Settings, get_settings
from dna.core.logging import get_logger
from dna.core.naming import slugify
from dna.tts.base import SpeechSegment, TTSProvider, VoiceSpec
from dna.tts.factory import _ref_audio_path, get_tts
from dna.tts.segment import DEFAULT_MAX_SEGMENT_CHARS, split_for_speech

logger = get_logger(__name__)

REF_AUDIO_DIRNAME = "ref_audio"
REF_AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".m4a")


def available_voices(settings: Settings | None = None) -> list[str]:
    """
    服务端提供的音色名 / The voice names the service offers.

    **服务离线时返回空表，不抛异常。** 设置面板在服务没起来的时候也要能打开——
    这时候退回自由文本框，而不是整个面板打不开。
    Returns an empty list when the service is offline rather than raising: the settings
    panel must still open, degrading to a free-text field.
    """
    s = settings or get_settings()
    try:
        return list(get_tts(s).available_speakers())
    except Exception as exc:  # noqa: BLE001 - 离线是常态，不是错误
        logger.debug("音色表取不到（服务多半没起）：%s", exc)
        return []


def ref_audio_choices(settings: Settings | None = None) -> list[str]:
    """
    `data_dir/ref_audio/` 下的候选参考音频 / Reference-audio candidates.

    返回的是**相对路径**（`ref_audio/xxx.wav`），和 `.env` 里该写的形式一致——
    返回绝对路径的话，把它存进 `.env` 会让配置绑死在这台机器的目录上。
    Relative paths are returned because that is what belongs in `.env`; an absolute one
    would pin the configuration to this machine.
    """
    s = settings or get_settings()
    directory = s.data_path / REF_AUDIO_DIRNAME
    if not directory.is_dir():
        return []
    names = [
        f"{REF_AUDIO_DIRNAME}/{p.name}"
        for p in sorted(directory.iterdir())
        if p.is_file() and p.suffix.lower() in REF_AUDIO_SUFFIXES
    ]
    return names


def resolve_ref_audio(raw: str, settings: Settings | None = None) -> Path | None:
    """
    参考音频解析到哪个文件 / Which file a reference-audio setting resolves to，不存在回 None。

    直接复用 `factory._ref_audio_path`，**不复制那条规则**：界面显示的路径与合成时
    真正读的路径必须是同一个，否则「界面上是绿的、合成出来是内置音色」。
    """
    return _ref_audio_path(raw, settings or get_settings())


def save_ref_audio(filename: str, data: bytes, settings: Settings | None = None) -> str:
    """
    收下一份上传的参考音频 / Adopt one uploaded reference clip，返回相对路径。

    写进 `data_dir/ref_audio/`，返回 `ref_audio/xxx.wav` 这种**相对路径** ——
    和 `ref_audio_choices()` 同一形式，界面拿到就能直接放进下拉框，也能直接存进 `.env`。

    文件名过一遍 `slugify`：上传的名字来自别人的机器，中文空格、冒号、路径分隔符
    都可能在里面，直接落盘在 Windows 上会失败或者写到别的目录去。
    重名不覆盖 —— 覆盖会**悄悄换掉**别的产物正在用的那把音色。
    The name is slugified because it comes from another machine, and a clash is renamed
    rather than overwritten: overwriting would silently change the voice other
    productions are already using.
    """
    s = settings or get_settings()
    source = Path(filename or "clip.wav")
    suffix = source.suffix.lower() if source.suffix.lower() in REF_AUDIO_SUFFIXES else ".wav"
    stem = slugify(source.stem)

    directory = s.data_path / REF_AUDIO_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{stem}{suffix}"
    index = 2
    while target.exists():
        target = directory / f"{stem}-{index}{suffix}"
        index += 1

    target.write_bytes(data)
    logger.info("参考音频已保存：%s（%.1f KB）", target.name, len(data) / 1024)
    return f"{REF_AUDIO_DIRNAME}/{target.name}"


def resplit(text: str, *, max_chars: int = DEFAULT_MAX_SEGMENT_CHARS) -> list[str]:
    """
    把一整段文本重新拆条 / Re-split one blob of text into pieces.

    直接转 `split_for_speech`，**不走服务端的 `/tts/split`**：操作台里看到的分段
    必须就是自动合成真会切出来的那批，两套切法迟早给出不同的段数。
    Delegates to the pipeline's own splitter so the console shows exactly what automatic
    synthesis would produce.

    纯函数、零费用；**不做朗读友好化** —— 那是一次 LLM 调用，而面板里的文本
    早就是预处理过的了，再跑一次等于对改写过的文本再改写一遍。
    """
    return split_for_speech(text, max_chars=max_chars)


PREVIEW_RUN = "preview"
"""试听在服务端固定用这一个产物目录：每试一次留一个时间戳目录会堆到没人清。"""


def preview_segment(
    text: str,
    voice: VoiceSpec,
    *,
    role: str = "narrator",
    settings: Settings | None = None,
    tts: TTSProvider | None = None,
) -> bytes:
    """
    单段试听 / Synthesise one piece for listening，返回 wav 字节。

    **不写 production 行。**试听是免费且无副作用的；写一行成功/失败记录会污染
    那一格的历史，让「上次生成到底是什么」变得说不清。
    Writes no ledger row: an audition must not pollute the cell's history.

    走的是 `provider.synthesize()` 而不是底层 client——参数拼装（尤其
    「克隆模式不能同时给音色名」那条护栏）只有一份。复制出来的第二份
    迟早漏掉一条，然后表现成服务端报参数冲突。
    Goes through the provider so the parameter assembly, including the clone-mode guard,
    exists exactly once.

    抛出 / Raises:
        TTSError: 服务不可用或这一段合成失败——试听要立刻说清原因
    """
    s = settings or get_settings()
    provider = tts or get_tts(s)
    clip = provider.synthesize(
        [SpeechSegment(text=text, voice=voice, role=role)], run=PREVIEW_RUN
    )
    return clip.wav


__all__ = [
    "available_voices",
    "preview_segment",
    "ref_audio_choices",
    "resolve_ref_audio",
    "resplit",
    "save_ref_audio",
]
