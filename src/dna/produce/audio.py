"""
音频产物那条路 / The audio path.

和文本产物走同一套护栏（已存在不重跑、失败入账），但代价完全不同：
一个防账单，一个防「点一下这台机器几十分钟没法用」。

分段与音色的逻辑必须只有一份：TTS 操作台看到的分段与自动合成出来的
**必须完全一致**，否则在界面上调好的东西换成自动合成又不一样了。
"""

from __future__ import annotations

from pathlib import Path

from dna.core.config import Settings
from dna.core.logging import get_logger
from dna.llm.base import LLMProvider
from dna.produce.documents import (
    longform_turns,
    spoken_text,
)
from dna.produce.results import Generated
from dna.produce.tasks import (
    TaskSpec,
    json_sidecar,
    normalize_lang,
    spec,
)
from dna.tts.base import ProgressFn, SpeechSegment, TTSProvider
from dna.tts.factory import voice_for_role
from dna.tts.preprocess import prepare_for_speech
from dna.tts.segment import split_for_speech

logger = get_logger("produce.service")

def _generate_audio(
    task: TaskSpec,
    tts: TTSProvider,
    directory: Path,
    *,
    lang: str,
    settings: Settings,
    on_progress: ProgressFn | None,
    llm: LLMProvider | None = None,
    article_id: str = "",
    segments: list[SpeechSegment] | None = None,
    rendered: dict[int, bytes] | None = None,
) -> Generated:
    """
    把已有的稿子合成为音频 / Synthesise the existing script into audio.

    **输入是稿子文件，不是原文。**稿子已经是为朗读写的了，再回头找原文只会
    念出一篇没人打算念的东西。
    The input is the script file, not the article: the script is already written to be
    read aloud, whereas the article is not.

    两条取文路径 / Two routes into the text:
        长文案走 `.json` 附件——它已经按发言人切好 turns，访谈的两个角色就是
        两个音色；其余走 Markdown，解析出「口播」那一段。
        Long-form reads the JSON sidecar, already split into speaker turns; the others
        parse the spoken section out of the Markdown.

    三步，顺序不能反 / Three steps in this order:
        1. **朗读友好化**（一次 LLM 调用，见 `tts/preprocess.py`）：型号、公式、
           多音字、断句。必须在分段**之前**做 —— 它会调整断句与停顿标记，
           先切好再改写，切点就落在了改写前的位置上
        2. **分段**：切成一段段独立合成的长度，只在句子边界切
        3. **合成**：交给 TTS 服务，逐段回报进度
        Preprocessing precedes segmentation because it rewrites the very punctuation the
        segmentation depends on.
    """
    # 外部给了分段就不再切一遍：那份分段本来就是 `speech_segments_for()` 切出来的，
    # 重切会把 TTS 操作台里逐段挑好的音色丢掉，还会**再调一次朗读友好化**
    # （预处理在切分之前，重跑等于对已改写过的文本再改写一遍）。
    # Re-segmenting would discard the per-piece voices and re-run preprocessing on text
    # that has already been rewritten once.
    if segments is None:
        segments, spoken_chars = _build_segments(
            task, directory, lang=lang, settings=settings, llm=llm
        )
    else:
        spoken_chars = sum(len(seg.text) for seg in segments)
    if not segments:
        raise RuntimeError(f"{spec(task.audio_of).label}里没有可朗读的内容")

    logger.info("开始合成 %s：%d 段 · %d 字", task.label, len(segments), spoken_chars)
    # 服务端产物目录名**按「文章 + 产物 + 语言」固定**：重做覆盖同一个目录，
    # 而不是每次多留一份时间戳目录（那样「哪份是最新的」只能靠人比时间戳）。
    # A stable name per cell: a redo overwrites rather than accumulating.
    run = "-".join(filter(None, [article_id[:8] or "adhoc", str(task.kind), lang]))
    if rendered:
        logger.info(
            "复用已生成的 %d 段，本次只合成 %d 段", len(rendered), len(segments) - len(rendered)
        )
    clip = tts.synthesize(segments, on_progress=on_progress, run=run, rendered=rendered)

    # 服务端留下的逐段 wav 与字幕拷进文章目录 —— 产物必须在**本项目的**输出里
    # 找得齐，否则想换一句话或拿字幕去剪辑时得翻到另一个仓库的 outputs/ 下。
    # The service's own files are copied next to the production so everything for one
    # article stays in one place.
    _copy_tts_artifacts(tts, clip, directory, settings)

    if not clip.complete:
        # 缺了几段的音频照样存下来——重跑要几十分钟，把能用的先留住，
        # 但**必须说出来缺了几段**，否则人会把残缺的音频当成成品发出去。
        # Incomplete audio is still saved because re-running costs half an hour, but the
        # gap is reported: otherwise a defective file gets published as finished.
        logger.warning(
            "%s 有 %d/%d 段合成失败，音频不完整",
            task.label,
            len(clip.failed_segments),
            clip.segments,
        )

    return Generated(
        audio=clip.wav,
        chars=spoken_chars,
        seconds=clip.seconds,
        calls=0,
        within_target=clip.complete,
        cues=list(getattr(clip, "cues", [])),
    )


def _build_segments(
    task: TaskSpec,
    directory: Path,
    *,
    lang: str,
    settings: Settings,
    llm: LLMProvider | None,
) -> tuple[list[SpeechSegment], int]:
    """
    稿子文件 → 一串待合成的分段 / The script file to a list of pieces.

    两条路径都在这里，**两个入口共用它**：流水线合成（`_generate_audio`）与
    交给 TTS 界面精修（`speech_segments_for`）必须切得一模一样，
    否则界面上看到的分段和自动合成出来的对不上。
    Shared by both entry points so the pieces a person sees in the TTS workbench are
    exactly the pieces the pipeline would have synthesised.
    """
    # 在这里归一化：下面要拿 lang 去选音色，空值会被当成英文
    lang = normalize_lang(lang)
    script_spec = spec(task.audio_of)
    script_path = directory / script_spec.filename_for(lang)
    try:
        markdown = script_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"读不到{script_spec.label}：{script_path.name}") from exc

    sidecar_name = json_sidecar(script_spec, lang)
    sidecar = directory / sidecar_name if sidecar_name else None
    turns = longform_turns(sidecar) if sidecar and sidecar.exists() else []

    if turns:
        pairs = [(role, text) for role, text in turns]
    else:
        pairs = [("narrator", spoken_text(markdown))]

    segments: list[SpeechSegment] = []
    spoken_chars = 0
    for role, text in pairs:
        prepared = prepare_for_speech(text, llm=llm, settings=settings)
        if prepared.reason:
            # 退回原文也要留一行日志：音质问题还在，只是没挡住音频
            logger.info("TTS 预处理未生效（%s）：%s", role, prepared.reason)
        elif prepared.notes:
            logger.info("TTS 预处理（%s）：%s", role, "；".join(prepared.notes[:5]))

        # 音色跟着语言走：英文稿用英文音色，否则模型会用中文的发音习惯念英文
        # The voice follows the language; otherwise English is read with Chinese phonetics.
        voice = voice_for_role(role, settings, lang=lang)
        for piece in split_for_speech(prepared.text):
            segments.append(SpeechSegment(text=piece, voice=voice, role=role))
            spoken_chars += len(piece)

    return segments, spoken_chars


def _copy_tts_artifacts(
    tts: TTSProvider, clip, directory: Path, settings: Settings
) -> list[Path]:
    """
    把 TTS 服务那边的产物取回来 / Pull the service's artifacts over.

    provider 不一定实现（协议里是可选的），所以用 `getattr` 探一下 ——
    将来换个不落盘的 provider 时，这里不该因此报错。
    Optional in the protocol, so its absence is not an error.

    取不回来只记一条警告：**主音频已经在手上了**，拿不到逐段文件不该让整次
    生成算失败。
    The main track is already in hand; a failed copy must not void the production.
    """
    fetch = getattr(tts, "fetch_artifacts", None)
    if fetch is None or not getattr(clip, "pieces", None):
        return []

    # 一篇文章一个文件夹，**不按 run 再分一层**：重做直接覆盖同名文件。
    # 分层的话「这篇的音频是哪一份」要靠人比时间戳，而人只会打开最上面那个。
    # One folder per article, overwritten on redo.
    target = directory / (settings.tts_artifact_dirname or "tts")
    try:
        saved = fetch(clip, target)
    except Exception as exc:
        logger.warning("TTS 产物拷贝失败：%s", exc)
        return []
    if saved:
        logger.info("已拷回 %d 个 TTS 产物 → %s", len(saved), target)
    return saved
