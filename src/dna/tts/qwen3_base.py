"""
Qwen3-TTS 两个后端的公共部分 / What both Qwen3-TTS backends share.

OpenVINO 版与 PyTorch 版的**调用接口完全一样**：
`generate_custom_voice(text, speaker, language, instruct) → (wavs, sample_rate)`。
不一样的只有「怎么把模型加载起来」。因此分段循环、拼接、静音、进度、
逐段容错全部写在这里一份，两个后端各自只实现 `_load()`。
The two backends expose the same call signature and differ only in how the model is
loaded, so the segment loop, joining, pauses, progress and per-segment fault tolerance
live here once.

**这正是当初要 provider 抽象的理由**：加一个部署环境（CUDA 机器）只多一个
`_load()`，业务层、前端、台账一行都不改。
Adding a deployment environment costs one `_load()`; nothing above it changes.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dna.core.logging import get_logger
from dna.tts.base import (
    DEFAULT_SAMPLE_RATE,
    PAUSE_SECONDS,
    SPEAKER_CHANGE_PAUSE_SECONDS,
    AudioClip,
    ProgressFn,
    SpeechSegment,
    TTSError,
    TTSInfo,
    encode_wav,
)

logger = get_logger("tts.qwen3")

# 默认音色 / default voices
#
# 九个音色里 serena 的中文最自然。双人访谈需要两个能听出区别的，
# 主持人 serena、嘉宾 uncle_fu——音高差得开，不戴耳机也分得出谁在说话。
# Of the nine voices `serena` reads Chinese most naturally. An interview needs two that
# are told apart easily, hence the pitch gap between host and guest.
DEFAULT_SPEAKER = "serena"
DEFAULT_GUEST_SPEAKER = "uncle_fu"


class Qwen3Backend:
    """
    Qwen3-TTS 后端的公共实现 / The shared half of a Qwen3-TTS backend.

    子类只需要实现 `_load()` 与 `info` / Subclasses implement only `_load()` and `info`.

    **模型加载要 10 秒上下，实例应当复用**（见 `factory.get_tts` 的缓存）。
    每点一次按钮都重新加载，等待时间会被这 10 秒白白拉长。
    Loading takes about ten seconds, so instances are meant to be reused.
    """

    def __init__(self, model_dir: Path | str, *, device: str, repo_dir: Path | str | None) -> None:
        self.model_dir = Path(model_dir)
        self.device = device
        self.repo_dir = Path(repo_dir) if repo_dir else None
        self._model: Any | None = None

    # -- 子类实现 / implemented by subclasses -------------------------------

    @property
    def info(self) -> TTSInfo:
        raise NotImplementedError

    def _load_model(self) -> Any:
        """把模型加载起来 / Load the model. Called at most once."""
        raise NotImplementedError

    # -- 公共实现 / shared ---------------------------------------------------

    def available_speakers(self) -> list[str]:
        """可用音色 / The voices this model offers."""
        model = self._model_ready()
        getter = getattr(model, "get_supported_speakers", None)
        if getter is None:  # torch 版把它挂在内层 model 上
            getter = getattr(getattr(model, "model", None), "get_supported_speakers", None)
        return sorted(getter()) if callable(getter) else []

    def available_languages(self) -> list[str]:
        """可用语言 / The languages this model accepts."""
        model = self._model_ready()
        getter = getattr(model, "get_supported_languages", None)
        if getter is None:
            getter = getattr(getattr(model, "model", None), "get_supported_languages", None)
        return sorted(getter()) if callable(getter) else []

    def synthesize(
        self, segments: Sequence[SpeechSegment], *, on_progress: ProgressFn | None = None
    ) -> AudioClip:
        """
        逐段合成并拼接 / Synthesise piece by piece and join.

        为什么一段一段来而不是整批送 / Why one at a time rather than one batched call:
            模型支持批量，但批量是**要么全成要么全败**，而长文案有几十段，
            跑到第 40 段崩掉就把前面 39 段的半小时一起赔进去。
            逐段来还能报进度——半小时的等待没有进度条是不可接受的。
            The model accepts batches, but a batch is all-or-nothing, and a long-form
            script runs to dozens of pieces: failing at the fortieth would discard the
            half hour already spent. Going piece by piece also makes progress reportable,
            which a half-hour wait requires.

        抛出 / Raises:
            TTSError: 模型加载不了，或**每一段都失败**
        """
        import numpy as np

        pieces = [s for s in segments if s.text.strip()]
        if not pieces:
            raise TTSError("没有可朗读的内容 / nothing to speak")

        model = self._model_ready()
        chunks: list[Any] = []
        failed: list[int] = []
        sample_rate = DEFAULT_SAMPLE_RATE
        produced = 0.0
        started = time.perf_counter()
        previous_role: str | None = None

        for index, piece in enumerate(pieces):
            try:
                wave, sample_rate = self._synthesize_one(model, piece, sample_rate)
            except Exception as exc:  # noqa: BLE001 - 一段失败不该毁掉整篇
                failed.append(index)
                logger.warning("第 %d/%d 段合成失败，跳过：%s", index + 1, len(pieces), exc)
                continue

            if previous_role is not None:
                gap = (
                    SPEAKER_CHANGE_PAUSE_SECONDS
                    if piece.role != previous_role
                    else PAUSE_SECONDS
                )
                chunks.append(np.zeros(int(sample_rate * gap), dtype=np.float32))
            previous_role = piece.role

            chunks.append(wave)
            produced += len(wave) / sample_rate
            if on_progress is not None:
                on_progress(index + 1, len(pieces), produced)

        if not chunks:
            raise TTSError(f"{len(pieces)} 段全部合成失败 / every segment failed")

        audio = np.concatenate(chunks)
        elapsed = time.perf_counter() - started
        seconds = len(audio) / sample_rate

        logger.info(
            "合成完成：%d 段 → %.1f 秒音频，耗时 %.1f 秒（RTF %.2f）%s",
            len(pieces),
            seconds,
            elapsed,
            elapsed / seconds if seconds else 0.0,
            f"，{len(failed)} 段失败" if failed else "",
        )
        return AudioClip(
            wav=encode_wav(audio, sample_rate),
            sample_rate=sample_rate,
            seconds=seconds,
            segments=len(pieces),
            failed_segments=failed,
        )

    # -- 内部实现 / internals ------------------------------------------------

    def _synthesize_one(self, model: Any, piece: SpeechSegment, fallback_rate: int):
        """合成一段 / Synthesise one piece."""
        import numpy as np

        wavs, rate = model.generate_custom_voice(
            text=piece.text,
            speaker=piece.voice.speaker,
            language=piece.voice.language or "auto",
            instruct=piece.voice.instruct or None,
        )
        raw = wavs[0]
        # OV 版可能回 torch tensor，torch 版回 ndarray
        wave = raw.detach().cpu().numpy() if hasattr(raw, "detach") else np.asarray(raw)
        return wave.astype(np.float32).reshape(-1), int(rate or fallback_rate)

    def _model_ready(self) -> Any:
        """加载模型（只加载一次）/ Load the model, once."""
        if self._model is not None:
            return self._model

        if not self.model_dir.is_dir():
            raise TTSError(f"找不到模型目录：{self.model_dir}　核对 .env 里的路径配置")

        started = time.perf_counter()
        logger.info("加载 %s…", self.info)
        self._model = self._load_model()
        logger.info("加载完成，耗时 %.1f 秒", time.perf_counter() - started)
        return self._model

    def _ensure_qwen_tts_importable(self) -> None:
        """
        让 `qwen_tts` 包可导入 / Make the `qwen_tts` package importable.

        两个后端都依赖它（OV 版用它的 processor 与 config，torch 版用它的模型类）。
        它是 editable 安装的，**仓库一旦被移动，安装记录就指向不存在的路径**——
        2026-09-04 实测就是这样：模型从 openvino_notebooks 搬到 Models/ 之后，
        `import qwen_tts` 直接 ModuleNotFoundError，而错误信息完全看不出是路径搬了。
        所以这里显式把配置的仓库目录加进 `sys.path`，并在报错时说清要配哪一项。
        Both backends need it. It is installed in editable mode, so moving the repository
        breaks the install with a `ModuleNotFoundError` that says nothing about the move.
        The configured repository directory is therefore put on the path explicitly.
        """
        if self.repo_dir is not None:
            path = str(self.repo_dir)
            if path not in sys.path:
                sys.path.insert(0, path)

        try:
            import qwen_tts  # noqa: F401
        except ImportError as exc:
            raise TTSError(
                f"导入不到 qwen_tts 包：{exc}　"
                "在 .env 里设 QWEN3_TTS_REPO_DIR 指向 Qwen3-TTS 源码仓库目录"
                "（里面有 qwen_tts/ 子目录）"
            ) from exc


__all__ = ["DEFAULT_GUEST_SPEAKER", "DEFAULT_SPEAKER", "Qwen3Backend"]
