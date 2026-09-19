"""TTS 操作台的后端动作 / Backend actions behind the TTS console。"""

from __future__ import annotations

from dataclasses import dataclass

from nicegui import run

from dna.core.config import get_settings
from dna.core.logging import get_logger
from dna.produce import (
    ProductionKind,
    spec,
)
from dna.produce.tasks import json_sidecar, normalize_lang
from dna.tts.base import SpeechSegment, VoiceSpec
from dna.tts.factory import get_tts

logger = get_logger("gui.actions")

@dataclass
class TTSStatus:
    """
    TTS 服务此刻在不在 / Whether the TTS service is up right now.

    界面必须一直显示这个。合成那条路会**自动拉起**服务并等最多
    `tts_start_timeout` 秒（模型冷启动），期间画面上什么都没有 ——
    于是「点了没反应」和「正在冷启动」是同一个观感。
    The synthesis path auto-spawns the service and waits out a cold start with nothing
    on screen, making "nothing happened" indistinguishable from "still loading".
    """

    online: bool
    url: str
    detail: str


async def tts_voices() -> list[str]:
    """服务端的音色表 / The voice list，离线返回空表。**HTTP 调用，不能在事件循环里做。**"""
    from dna.tts.console import available_voices

    return await run.io_bound(available_voices)


async def speech_segments(
    article_id: str,
    kind: ProductionKind | str,
    *,
    lang: str = "",
) -> list[SpeechSegment]:
    """
    这一格会念哪些分段 / The pieces this cell would speak.

    **会走一次朗读友好化**（`TTS_PREPROCESS=true` 时是一次 LLM 调用，很便宜），
    所以操作台把拿到的分段一直拿在手里，合成时原样传回 `run_production`——
    重新取一次不只是慢，还会再调一次 LLM，且切点可能与试听过的那批不同。
    Preprocessing happens here, so the console keeps the pieces it got and hands the very
    same list back at synthesis time.
    """
    from dna.produce.service import speech_segments_for

    lang = normalize_lang(lang)

    def _work() -> list[SpeechSegment]:
        return speech_segments_for(article_id, kind, lang=lang)

    return await run.io_bound(_work)


async def preview_voice(text: str, voice: VoiceSpec, *, role: str = "narrator") -> bytes:
    """
    试听一段 / Audition one piece，返回 wav 字节，**不写台账**。

    参数拼装在 `dna.tts.console.preview_segment` 里（那里复用 provider 自己的
    那一份），界面不碰。
    """
    from dna.tts.console import preview_segment

    def _work() -> bytes:
        return preview_segment(text, voice, role=role)

    return await run.io_bound(_work)


async def save_script(
    article_id: str,
    kind: ProductionKind | str,
    text: str,
    *,
    lang: str = "",
) -> int:
    """
    把操作台里校对过的文本写回稿子 / Write the console's proof-read text back.

    零 LLM、零费用；台账里多一行 `calls=0` 的「人工校对」。
    **先写稿子行、再写音频行** —— 顺序反了的话，音频那一格的前置稿子还是旧的。
    """
    from dna.produce.service import save_script_text

    lang = normalize_lang(lang)

    def _work() -> int:
        return save_script_text(article_id, kind, text, lang=lang)

    return await run.io_bound(_work)


def script_is_editable(kind: ProductionKind | str, *, lang: str = "") -> bool:
    """
    这一格的稿子能不能从操作台写回 / Whether this cell's script can be written back.

    长文案的稿子**按发言人分轮**存在 JSON 边车里，一段纯文本写不回去（`save_script_text`
    会直接拒绝）。面板要提前知道，好在打开时就说清「这里改的字不会写回稿子」——
    等到人校对完一整篇再报错，那份工就白做了。
    """
    lang = normalize_lang(lang)
    task = spec(kind)
    if task.audio_of is not None:
        task = spec(task.audio_of)
    return not json_sidecar(task, lang)


def resplit_text(text: str, *, max_chars: int | None = None) -> list[str]:
    """
    重新拆条 / Re-split，纯函数、零费用，直接在事件循环里算（微秒级）。

    切法与自动合成同一份（`tts/segment.py`）—— 面板里看到的分段必须就是
    真会合成的那批。`max_chars` 由操作台上那个输入框给：默认值就是自动合成用的那个，
    调大调小只影响这一次拆分。
    """
    from dna.tts.console import resplit

    if max_chars:
        return resplit(text, max_chars=int(max_chars))
    return resplit(text)


async def upload_ref_audio(filename: str, data: bytes) -> str:
    """
    收下一份上传的参考音频 / Adopt an uploaded reference clip，返回相对路径。

    落盘是 I/O，交给工作线程；返回的相对路径可以直接进下拉框，也能存进 `.env`。
    """
    from dna.tts.console import save_ref_audio

    def _work() -> str:
        return save_ref_audio(filename, data)

    return await run.io_bound(_work)


async def tts_status() -> TTSStatus:
    """探一次 TTS 服务 / Probe the TTS service. 不拉起、不抛异常。"""

    def _work() -> TTSStatus:
        settings = get_settings()
        url = settings.tts_service_url
        client = get_tts(settings).client
        if client.health():
            return TTSStatus(online=True, url=url, detail=f"TTS 服务在线：{url}")
        hint = "点一下启动" if settings.tts_autostart else "自动启动已关闭（TTS_AUTOSTART）"
        return TTSStatus(online=False, url=url, detail=f"TTS 服务离线：{url}　·　{hint}")

    return await run.io_bound(_work)


async def tts_start() -> TTSStatus:
    """
    拉起 TTS 服务 / Bring the TTS service up.

    失败原因**原样上抛**：`ensure_service` 抛出的那句话里已经写了怎么排查
    （模块目录、Python 路径、端口占用），改写成「启动失败」就把它扔了。
    The reason is propagated verbatim; it already says where to look.
    """
    from dna.tts.supervisor import ensure_service

    def _work() -> TTSStatus:
        settings = get_settings()
        client = get_tts(settings).client
        ensure_service(settings, client=client)
        return TTSStatus(
            online=True, url=settings.tts_service_url,
            detail=f"TTS 服务已就绪：{settings.tts_service_url}",
        )

    return await run.io_bound(_work)
