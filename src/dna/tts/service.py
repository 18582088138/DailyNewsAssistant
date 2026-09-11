"""
唯一的 TTS 后端：远端的 TTS service / The only TTS backend: the remote service.

本项目内**没有任何语音模型代码**。权重加载、8GB 卡上的轮动换权重、失控生成的
拦截与换种子重试、音色克隆/设计、字幕，全部在 Agent_TTS_Module 那一侧。
这里只有：拆好的段落 → 一段段发给服务 → 拼成一条音频。
No model code lives in this project. Everything about synthesis lives in the service;
this file only sends pieces and joins what comes back.

为什么一段一个请求，而不是把整批丢给 `/tts/batch` / Why one request per piece:
    1. **进度**：整批一个请求，界面在几十分钟里只能看一个转圈；
       一段一个请求才能报「第 7/23 段」。
    2. **容错**：整批失败就是全丢；逐段失败只丢那一段，其余照样拼出成品。
    服务端本来就是串行合成（一把锁），逐段发不会更慢。
    Per-piece requests are what make progress reporting and partial failure possible,
    and cost nothing: the service synthesises serially anyway.

为什么拼接在这一侧做 / Why joining happens here:
    `base.py` 的契约是「provider 收一串分段、回一整条音频」，因为只有 provider
    知道采样率与段间该留多长静音。走服务之后这条契约不变——变的只是"谁来合成"。
    The contract is unchanged from the local backends; only the synthesiser moved.
"""

from __future__ import annotations

import io
import time
import wave
from collections.abc import Sequence
from pathlib import Path

from dna.core.config import Settings, get_settings
from dna.core.logging import get_logger
from dna.tts.base import (
    PAUSE_SECONDS,
    SPEAKER_CHANGE_PAUSE_SECONDS,
    AudioClip,
    ProgressFn,
    SpeechSegment,
    TTSError,
    TTSInfo,
)
from dna.tts.client import TTSServiceClient
from dna.tts.subtitle import build_cues
from dna.tts.supervisor import ensure_service

logger = get_logger("tts.service")

PROVIDER_NAME = "tts_service"

# 默认音色 / default voices —— 服务端内置 speaker 的名字，**大小写要对得上**
DEFAULT_SPEAKER = "Serena"
DEFAULT_GUEST_SPEAKER = "Uncle_Fu"

# `VoiceSpec.language` → 服务端的语言名 / to the service's language names
_LANGUAGES = {"zh": "Chinese", "chinese": "Chinese",
              "en": "English", "english": "English",
              "auto": "Chinese", "": "Chinese"}


class TTSServiceProvider:
    """
    走 HTTP 的 TTS provider / The HTTP-backed TTS provider.

    满足 `TTSProvider` 协议，因此 `produce/` 与两个前端一行都不用改。
    Satisfies the `TTSProvider` protocol, so nothing above it changes.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = TTSServiceClient(
            self.settings.tts_service_url, timeout=self.settings.tts_request_timeout
        )
        self._info: TTSInfo | None = None
        self._speakers: list[str] | None = None

    # ------------------------------------------------------------ 身份 / identity

    @property
    def info(self) -> TTSInfo:
        """
        引擎身份，记进台账 / Engine identity, recorded in the ledger.

        **不可抛异常**：调用方在合成之前就会读它来填台账，
        在这里抛出会把「服务没起来」变成一个发生在无关位置的错误。
        Never raises: the caller reads this before synthesis to fill the ledger, and
        failing here would surface "service down" at an unrelated place.
        """
        if self._info is None:
            try:
                body = self.client.info()
                engine = body.get("engine", {})
                self._info = TTSInfo(
                    name=PROVIDER_NAME,
                    model=str(engine.get("backend") or body.get("backend") or "unknown"),
                    device=str(engine.get("device") or "-"),
                )
            except TTSError:
                self._info = TTSInfo(PROVIDER_NAME, "unknown", self.settings.tts_service_url)
        return self._info

    def available_speakers(self) -> list[str]:
        """可用音色 / The voices the service offers（含服务端的音色档案）。"""
        if self._speakers is None:
            self._speakers = self.client.speakers()
        return list(self._speakers)

    # -------------------------------------------------------- 合成 / synthesis

    def synthesize(
        self,
        segments: Sequence[SpeechSegment],
        *,
        on_progress: ProgressFn | None = None,
        run: str | None = None,
        rendered: dict[int, bytes] | None = None,
    ) -> AudioClip:
        """
        逐段合成并拼接 / Synthesise each piece and join them.

        参数 / Args:
            run: 服务端产物目录名。**同一格音频每次都传同一个名字**，重做就覆盖，
                 不会在服务端堆一串时间戳目录（谁是最新的只能靠人比时间）。
                 A stable name means a redo overwrites instead of accumulating.
            rendered: `{段号: wav 字节}`——这些段不再请求服务端，直接拿现成的波形。
                 TTS 操作台里逐段生成好的音频靠它复用：RTF≈2.5，整篇重跑要几分钟，
                 逐段校对的结果不该在最后一步被推翻重做一遍。
                 段号是**过滤掉空白段之后**的下标，也就是这个循环里的 `index`。
                 Pre-rendered waveforms are reused instead of re-requested.

        抛出 / Raises:
            TTSError: 服务不可用（含自动拉起失败），或**一段都没成功**
        """
        pieces = [s for s in segments if s.text.strip()]
        if not pieces:
            raise TTSError("没有可朗读的内容")

        status = ensure_service(self.settings, client=self.client)
        logger.info("%s；开始合成 %d 段", status.summary(), len(pieces))

        # 没给名字才退回时间戳（命令行试听这种一次性调用）
        run = run or f"dna-{time.strftime('%Y%m%d-%H%M%S')}"
        wavs: list[bytes] = []
        gaps: list[float] = []
        failed: list[int] = []
        reasons: list[str] = []
        artifacts: list[str] = []
        local: list[tuple[str, bytes]] = []
        spoken: list[tuple[float, str]] = []      # 字幕要的 (时长, 原文)
        seconds = 0.0

        for index, piece in enumerate(pieces):
            voice = piece.voice
            cloning = voice.mode == "voice_clone" and bool(voice.ref_audio)
            ready = (rendered or {}).get(index)
            try:
                if ready is not None:
                    # 现成的波形只补一个 `seconds`，下面的路一个字不改：拼接、
                    # 真实时长、字幕仍走同一个 `_join` / `build_cues`。唯一的差别是
                    # 这一段没有服务端产物路径，`artifacts` 因此少一条（允许，
                    # `_copy_tts_artifacts` 本来就只在拿不到时记一条警告）。
                    body = {"wav": ready, "seconds": _wav_seconds(ready)}
                else:
                    body = self.client.synthesize(
                        piece.text,
                        # 克隆模式**不能带 voice**：服务端把「有 ref_audio 又有音色名」
                        # 视为互相冲突的参数并直接报错（它故意不做静默忽略）。
                        # In clone mode a voice name is a conflicting parameter, not a hint.
                        voice=None if cloning else self._voice_name(voice.speaker),
                        language=_LANGUAGES.get((voice.language or "").lower(), "Chinese"),
                        instruct=voice.instruct or None,
                        role=piece.role,
                        run=run,
                        mode=voice.mode or None,
                        ref_audio=voice.ref_audio or None,
                        ref_text=voice.ref_text or None,
                        x_vector_only=voice.x_vector_only,
                        seed=voice.seed,
                        pause_ms=piece.pause_ms,
                    )
            except TTSError as exc:
                # 一段失败不毁掉整条：几十分钟的合成里丢一句，比全部重来划算得多。
                # 但**必须记下来**——上层据此告诉人「音频不完整」。
                # One failed piece does not void the take, but it is recorded so the
                # layer above can say the audio is incomplete.
                logger.warning("第 %d/%d 段合成失败：%s", index + 1, len(pieces), exc)
                failed.append(index)
                reasons.append(str(exc))
                if on_progress is not None:
                    on_progress(index + 1, len(pieces), seconds)
                continue

            wavs.append(body["wav"])
            gaps.append(_gap_after(index, pieces))
            seconds += float(body.get("seconds") or 0.0)
            if body.get("path"):
                artifacts.append(_relative(body["path"]))
            # 逐段副本用**我们自己的编号**命名：服务端按「段号+音色」命名，
            # 同一个音色在一次合成里出现两次就会互相覆盖（实测踩到）。
            local.append((f"seg_{index + 1:03d}_{piece.role or 'narrator'}.wav",
                          body["wav"]))
            # 字幕时长用**波形自己的长度**，不用服务端报的秒数：
            # 字幕的误差是累积的，几十段之后半秒的偏差会变成看得见的错位。
            # Measured from the waveform: subtitle drift accumulates.
            spoken.append((_wav_seconds(body["wav"]), piece.text))
            if on_progress is not None:
                on_progress(index + 1, len(pieces), seconds)

        if not wavs:
            # **原因就在手里，不能让人去翻另一个进程的日志。**实测踩过一次：
            # 环境里 torch 与 torchaudio 的 ABI 不匹配，每段都倒在同一处，
            # 而界面上只有一句「看服务端日志」——查一个已知原因花掉了一整轮。
            # The reason is in hand; sending someone to another process's log for it
            # cost a whole round once already.
            raise TTSError(
                f"{len(pieces)} 段全部合成失败：{reasons[0] if reasons else '原因不明'}"
            )

        joined, rate, real_seconds = _join(wavs, gaps[:-1] if gaps else [])
        return AudioClip(
            cues=build_cues(spoken, gaps),
            wav=joined,
            sample_rate=rate,
            seconds=real_seconds,
            segments=len(pieces),
            failed_segments=failed,
            run=run,
            artifacts=artifacts,
            pieces=local,
        )

    # -------------------------------------------------------- 产物 / artifacts

    def fetch_artifacts(self, clip: AudioClip, dest_dir: Path) -> list[Path]:
        """
        把逐段音频落到文章目录 / Write the per-piece audio next to the production.

        想单独换某一句、或把某一段拿去剪辑时，不必翻到 TTS 那个仓库的 outputs/ 里去找。
        Keeps everything for one article in one place.

        **写手上的字节，不回头下载**：服务端按「段号+音色」命名，一次合成里同一个
        音色出现两次会互相覆盖（实测踩到），下载回来的是错的那一份。见 `AudioClip.pieces`。
        The bytes are written from memory: the service's names collide when two pieces
        share a voice, so re-downloading returns the wrong audio.
        """
        if not clip.pieces:
            return []
        dest_dir.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        for name, payload in clip.pieces:
            target = dest_dir / name
            try:
                target.write_bytes(payload)
            except OSError as exc:
                logger.warning("逐段音频写盘失败 %s：%s", name, exc)
                continue
            saved.append(target)
        return saved

    # ------------------------------------------------------ 内部 / internals

    def _voice_name(self, name: str) -> str:
        """
        把音色名对到服务端的写法 / Match the voice name to the service's spelling.

        服务端的内置音色是 `Serena` / `Uncle_Fu` 这样首字母大写的，而 `.env` 里
        很容易写成小写。**大小写不符会在加载完权重之后才报错**——那时已经等了几十秒。
        这里先按服务端给的清单纠正一次；对不上就原样发过去，让服务端报出可选项。
        The service spells its built-ins capitalised while `.env` easily holds lower
        case, and a mismatch only surfaces after the weights have loaded.
        """
        wanted = (name or "").strip()
        if not wanted:
            return DEFAULT_SPEAKER
        try:
            known = self.available_speakers()
        except TTSError:
            return wanted
        if wanted in known:
            return wanted
        lowered = {k.lower(): k for k in known}
        return lowered.get(wanted.lower(), wanted)


# ---------------------------------------------------------------------------
# 波形拼接 / joining waveforms
# ---------------------------------------------------------------------------


def _gap_after(index: int, pieces: Sequence[SpeechSegment]) -> float:
    """
    这一段之后留多长静音 / How much silence follows this piece.

    换人处更长：两个人的话黏在一起听起来像抢话，分不出是谁在说。
    A speaker change gets more, or the two voices run together.
    """
    if index + 1 >= len(pieces):
        return 0.0
    if pieces[index + 1].role != pieces[index].role:
        return SPEAKER_CHANGE_PAUSE_SECONDS
    return PAUSE_SECONDS


def _join(wavs: list[bytes], gaps: list[float]) -> tuple[bytes, int, float]:
    """
    把一串 WAV 拼成一条 / Join WAV byte strings into one.

    只用标准库 `wave`：这一步是**帧的搬运**，没有重采样也没有格式转换，
    为它拉一个 numpy 依赖不划算（只装 tts 那一组的机器上未必有）。
    Stdlib only: this moves frames around, so it does not justify a numpy dependency.

    采样率不一致时以第一段为准并**说出来**：服务端只有一个引擎，
    出现这种情况说明中途换了配置，静默按第一段处理会得到变调的音频。
    A rate mismatch means the service changed configuration mid-run; taking the first
    rate silently would produce pitch-shifted audio, so it is reported.
    """
    frames: list[bytes] = []
    rate = 0
    width = 2
    channels = 1

    for position, raw in enumerate(wavs):
        with wave.open(io.BytesIO(raw), "rb") as handle:
            if position == 0:
                rate, width, channels = (handle.getframerate(),
                                         handle.getsampwidth(),
                                         handle.getnchannels())
            elif handle.getframerate() != rate:
                logger.warning("第 %d 段采样率是 %d，与首段 %d 不一致，按首段拼接",
                               position + 1, handle.getframerate(), rate)
            frames.append(handle.readframes(handle.getnframes()))

        gap = gaps[position] if position < len(gaps) else 0.0
        if gap > 0:
            frames.append(b"\x00" * (int(rate * gap) * width * channels))

    payload = b"".join(frames)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(payload)

    total = len(payload) / (rate * width * channels) if rate else 0.0
    return buffer.getvalue(), rate, total


def _wav_seconds(payload: bytes) -> float:
    """一段 WAV 的真实时长 / One WAV's measured duration."""
    with wave.open(io.BytesIO(payload), "rb") as handle:
        rate = handle.getframerate()
        return handle.getnframes() / rate if rate else 0.0


def _relative(path: str) -> str:
    """
    服务端的绝对路径 → `run/文件名` / Absolute server path to a fetchable pair.

    `GET /outputs/{run}/{file}` 只认这两段。用**路径里真实的父目录名**而不是我们
    请求时给的 run 名：产物目录重名时服务端会自动加后缀（`-2`），写死就取不到了。
    The real parent name is used because the service appends a suffix on collision.
    """
    target = Path(path)
    return f"{target.parent.name}/{target.name}"


__all__ = [
    "DEFAULT_GUEST_SPEAKER",
    "DEFAULT_SPEAKER",
    "PROVIDER_NAME",
    "TTSServiceProvider",
]
