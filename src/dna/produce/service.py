"""
单篇产物生成 / Per-article production service.

CLI 的 `dna produce` 与 GUI 的重做按钮**都调这里**——业务逻辑只有一份，
两个前端不会各写一套、各错一套。
Both `dna produce` and the workbench's redo buttons call into this, so the logic exists
once and the two front-ends cannot drift apart or break differently.

一次生成做的事 / What one run does:
    查前置 → 取文章 → 调生成器 → 写文件 → 记 productions 表

文本产物调 LLM（花钱），音频产物调 TTS（花时间）/ Two kinds of cost:
    两者走**同一条路**——同样的「已存在不重跑」、同样的失败入账、同样的逐格重做。
    差别只在生成器和护栏的理由：一个防账单，一个防「点一下机器就没法用了」。
    Both take the same path with the same guards; only the generator and the reason for
    the guard differ — one protects the bill, the other protects the machine.

三条保护 / Three guards:
    1. **已有产物且未指定 force 时直接返回**，不调 LLM。
       GUI 里按钮就在手边，误触一次就是一次计费；默认不重复花钱。
       An existing production is returned as-is unless forced: the buttons sit right
       there, and a mis-click must not cost money.
    2. **前置缺失时先自动补**（英文总结依赖中文总结），而不是报错让人手动跑一遍。
       A missing prerequisite is produced first rather than raising and making the user
       run it by hand.
    3. **失败也记一行**，状态为 failed 且带错误原因。不记的话表格里显示「未生成」，
       人会以为没跑过，于是再点一次、再失败一次。
       Failures are recorded too. Without a row the table shows "not generated", the
       user assumes it never ran, clicks again and fails again.

重做必须绕开 LLM 缓存 / A redo must bypass the response cache:
    响应缓存**故意不设过期**，键是「provider + 模型 + 完整提示词」——同样的输入
    永远给同样的输出，这正是它的价值。但「重做」的字面意思就是**要一个不一样的**，
    照常走缓存的话，模型确实被调了、文件确实被重写了，内容却一字未变——
    看起来就像「生成了但没保存」。所以 `force=True` 时 provider 不带缓存。
    The cache deliberately never expires and keys on the prompt, which is exactly what
    makes it valuable. But "redo" means "give me a different one": served from cache, the
    call happens, the file is rewritten, and not one character changes — which reads as
    "it generated but did not save". `force=True` therefore builds an uncached provider.
"""

from __future__ import annotations

import json
import time

from dna.core.config import Profile, Settings, get_settings, safe_profile
from dna.core.logging import get_logger
from dna.llm.base import LLMProvider
from dna.llm.factory import get_llm
from dna.produce.audio import _build_segments, _generate_audio
from dna.produce.documents import (
    replace_spoken,
)
from dna.produce.generate import generate_text, load_article, read_production
from dna.produce.results import Generated, ProduceResult
from dna.produce.tasks import (
    ProductionKind,
    batch_kinds,
    json_sidecar,
    normalize_lang,
    prerequisite,
    spec,
)
from dna.store.ledger import Ledger
from dna.tts.base import ProgressFn, SpeechSegment, TTSProvider
from dna.tts.factory import get_tts
from dna.tts.subtitle import write_srt

logger = get_logger("produce.service")



def produce(
    article_id: str,
    kind: ProductionKind | str,
    *,
    lang: str = "",
    variant: str | None = None,
    force: bool = False,
    instructions: str = "",
    settings: Settings | None = None,
    profile: Profile | None = None,
    llm: LLMProvider | None = None,
    tts: TTSProvider | None = None,
    on_progress: ProgressFn | None = None,
    segments: list[SpeechSegment] | None = None,
    rendered: dict[int, bytes] | None = None,
) -> ProduceResult:
    """
    为一篇文章生成一种产物 / Produce one kind for one article.

    参数 / Args:
        lang:        输出语言，`zh` 或 `en`。语言是产物的一个维度，不是另一种产物
        variant:     长文案的形式，`feature`（专题）或 `interview`（访谈）
        force:       已有产物时是否重做。**默认不重做，也就不花钱**
        instructions: 这一次的额外要求（「加长到 40 秒」「用词再专业一点」）。
                     它会接在提示词末尾并记进台账，下次重做能看到上次改了什么
        profile:     时长区间与结尾引导语来自这里；不给则读 profile.yaml
        llm:         注入 provider；不给则按 .env 构造（测试一律注入假的）
        tts:         音频产物的后端；不给则按 .env 构造
        on_progress: 音频合成的进度回调 `(已完成段, 总段, 已产出秒数)`。
                     长文案音频要跑半小时，没有进度就只能看着界面发呆
        segments:    音频产物专用：用外部调好的分段替掉自动切分。TTS 操作台里
                     逐段挑过音色之后走这里，**落盘路径与自动合成完全同一条**
                     （写台账、算时长、出字幕），否则两条路的产物迟早不一致。
                     为 `None` 时行为与从前一字不差。
                     Audio only: overrides the automatic segmentation with pieces tuned
                     in the TTS console, while keeping one single write path.
        rendered:    音频产物专用：`{段号: wav 字节}`，这些段直接用现成的波形，
                     不再送去合成。操作台里逐段试听过的段就是这么复用的 ——
                     RTF≈2.5，整篇重跑要几分钟，不复用等于逐段校对白做。
                     段号对应 `segments` 的下标（空段会被丢掉，见 `tts/service.py`）。
    """
    s = settings or get_settings()
    prof = profile or safe_profile()
    task = spec(kind)
    lang = normalize_lang(lang)
    ledger = Ledger(s.db_file)

    record = ledger.get(article_id)
    if record is None:
        return ProduceResult(kind=task.kind, ok=False, error=f"台账里没有这篇文章：{article_id}")
    if not record.store_dir:
        return ProduceResult(kind=task.kind, ok=False, error="这篇文章还没有落盘目录")

    directory = s.output_path / record.store_dir
    output = directory / task.filename_for(lang)

    existing = ledger.latest_production(article_id, str(task.kind), lang)
    if not force and existing is not None and existing.ok and output.exists():
        return ProduceResult(
            kind=task.kind,
            ok=True,
            skipped=True,
            path=output,
            chars=existing.chars,
            seconds=existing.est_seconds,
        )

    article = load_article(article_id, s)
    if article is None:
        return ProduceResult(kind=task.kind, ok=False, error="读不到这篇文章的正文")

    if task.min_body_chars and len(article.text) < task.min_body_chars:
        return ProduceResult(
            kind=task.kind,
            ok=False,
            error=(
                f"正文只有 {len(article.text)} 字，不足 {task.min_body_chars} 字，"
                f"本篇不适合生成{task.label}"
            ),
        )

    # 音频产物不在这里构造 LLM / audio does not build the LLM here
    #
    # 无条件 `get_llm()` 的话，只想合成一段音频也会先要求 API key，而
    # `TTS_PREPROCESS=false` 的机器根本不需要 key。所以朗读友好化那一次调用由
    # `prepare_for_speech` 自己按需构造，失败就退回原文（见 `tts/preprocess.py`）。
    #
    # 注意边界：**TTS service 从不调用 LLM**（它是纯 TTS）；那一次调用属于本项目，
    # 是把文案交出去之前的准备。合成本身仍然记 `calls=0`。
    # The service never calls an LLM; that one call belongs to this project, as
    # preparation before handing the copy over. Synthesis itself still bills nothing.
    is_audio = task.audio_of is not None
    provider = None if is_audio else (llm or get_llm(cache=not force))
    speaker = (tts or get_tts(s)) if is_audio else None
    generator = speaker.info if is_audio else provider.info

    # 前置缺失时先补上——报错让人手动跑一遍是没必要的摩擦
    #
    # **前置一律不 force**：重做音频不该顺手把稿子也重新调一遍 LLM。
    # 下游重做是免费的，上游重做是要花钱的，让一个动作同时触发两者是危险的默认值。
    # Prerequisites are never forced: redoing the audio must not re-bill the script.
    # The downstream redo is free while the upstream one costs money, and letting one
    # click trigger both is a dangerous default.
    need = prerequisite(task.kind, lang)
    if need is not None:
        need_kind, need_lang = need
        upstream_record = ledger.latest_production(article_id, str(need_kind), need_lang)
        if upstream_record is None or not upstream_record.ok:
            logger.info("先补前置产物：%s / %s", spec(need_kind).label, need_lang)
            upstream = produce(
                article_id,
                need_kind,
                lang=need_lang,
                settings=s,
                profile=prof,
                llm=llm,
                force=False,
            )
            if not upstream.ok:
                return ProduceResult(
                    kind=task.kind,
                    ok=False,
                    error=f"前置产物「{spec(need_kind).label}」未能生成：{upstream.error}",
                )

    started = time.perf_counter()
    try:
        if is_audio:
            result = _generate_audio(
                task, speaker, directory, lang=lang, settings=s,
                on_progress=on_progress, llm=llm, article_id=article_id,
                segments=segments, rendered=rendered,
            )
        else:
            result = generate_text(
                task,
                article,
                provider,
                directory,
                article_id,
                lang=lang,
                variant=variant,
                instructions=instructions,
                settings=s,
                profile=prof,
            )
    except Exception as exc:
        error = " ".join(str(exc).split())[:300]
        logger.warning("生成失败 %s / %s：%s", article_id[:8], task.kind, error)
        ledger.record_production(
            article_id,
            str(task.kind),
            status="failed",
            lang=lang,
            variant=variant,
            instructions=instructions,
            error=error,
            llm_provider=generator.name,
            llm_model=generator.model,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return ProduceResult(kind=task.kind, ok=False, error=error)

    directory.mkdir(parents=True, exist_ok=True)
    written_cues: list[tuple[float, float, str]] = []
    if result.audio is not None:
        output.write_bytes(result.audio)
        # 字幕和音频**同名同目录**（`narration_audio.srt`）：
        # 这样拖进剪辑软件时两个文件是一眼配对的，不必再想哪个配哪个。
        # Same stem, same folder: the pairing is obvious in the editor.
        if s.tts_subtitles and result.cues:
            written = write_srt(output.with_suffix(".srt"), result.cues)
            if written is not None:
                written_cues = list(result.cues)
                logger.info("字幕已导出：%s（%d 条）", written.name, len(result.cues))
    else:
        output.write_text(result.text, encoding="utf-8")

    if result.sidecar is not None:
        sidecar_name = json_sidecar(task, lang)
        if sidecar_name:
            (directory / sidecar_name).write_text(
                json.dumps(result.sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    ledger.record_production(
        article_id,
        str(task.kind),
        status="ok",
        lang=lang,
        variant=variant,
        instructions=instructions,
        output_path=output.relative_to(s.output_path).as_posix(),
        chars=result.chars,
        est_seconds=result.seconds,
        llm_provider=generator.name,
        llm_model=generator.model,
        calls=result.calls,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )

    return ProduceResult(
        kind=task.kind,
        ok=True,
        path=output,
        chars=result.chars,
        seconds=result.seconds,
        calls=result.calls,
        within_target=result.within_target,
        cues=written_cues,
    )


def produce_all(
    article_id: str,
    *,
    lang: str = "",
    force: bool = False,
    settings: Settings | None = None,
    profile: Profile | None = None,
    llm: LLMProvider | None = None,
) -> list[ProduceResult]:
    """
    批量生成 / Produce the batch kinds.

    **不含长文案**——它是最贵的产物，必须显式指定（见 `tasks.py`）。
    Excludes the long-form script, which must be requested explicitly.

    前一项失败不阻断后面的：四项彼此独立（除了英文总结依赖中文总结，
    而那条依赖由 `produce` 自己处理）。
    A failure does not stop the rest: the kinds are independent apart from the English
    summary's prerequisite, which `produce` handles itself.
    """
    lang = normalize_lang(lang)
    provider = llm or get_llm()
    prof = profile or safe_profile()
    return [
        produce(
            article_id,
            kind,
            lang=lang,
            force=force,
            settings=settings,
            profile=prof,
            llm=provider,
        )
        for kind in batch_kinds()
    ]




def speech_segments_for(
    article_id: str,
    kind: ProductionKind | str,
    *,
    lang: str = "",
    settings: Settings | None = None,
    llm: LLMProvider | None = None,
) -> list[SpeechSegment]:
    """
    这一格音频会念哪些分段 / The pieces this audio cell would speak.

    「高级配置」用它把稿子交给 TTS 图形界面 —— 界面里看到的分段与自动合成
    **完全一致**（同一份 `_build_segments`），否则在界面上调好的东西
    换成自动合成又不一样了。
    Used by the advanced hand-off so the workbench shows exactly what the pipeline would
    synthesise.
    """
    lang = normalize_lang(lang)
    s = settings or get_settings()
    task = spec(kind)
    if task.audio_of is None:
        return []

    record = Ledger(s.db_file).get(article_id)
    if record is None or not record.store_dir:
        return []
    segments, _ = _build_segments(
        task, s.output_path / record.store_dir, lang=lang, settings=s, llm=llm
    )
    return segments


def save_script_text(
    article_id: str,
    kind: ProductionKind | str,
    text: str,
    *,
    lang: str = "",
    settings: Settings | None = None,
) -> int:
    """
    把人工校对过的稿子写回文件与台账 / Write a proof-read script back to file and ledger.

    TTS 操作台里的文本是可编辑的，改完必须回到稿子文件里 —— 不写回的话，音频念的
    是操作台里的文本，而台账指向的稿子还是 LLM 那一版，「这一版到底念了什么」
    就没有答案了，而这正是台账存在的理由。
    The console's text is editable; without this the audio would speak one text while the
    ledger pointed at another, and the ledger's whole purpose is to answer what was said.

    **零费用、零 LLM**：只是覆盖正文并插一行 `calls=0` 的台账记录
    （`instructions="人工校对（TTS 操作台）"`），历史仍由 `redo_of_id` 链承担。
    不归档旧文件 —— 现在的重做也是直接覆盖，这里不新造一套。

    `kind` 给音频产物也可以，会自动落到它的稿子上（操作台手里拿的是音频那一格）。
    长文案除外：它的稿子按发言人分轮存在 `.json` 附件里，一段纯文本写不回去。

    返回新插入的台账行 id / Returns the id of the inserted ledger row.
    """
    s = settings or get_settings()
    task = spec(kind)
    if task.audio_of is not None:
        task = spec(task.audio_of)
    lang = normalize_lang(lang)

    if json_sidecar(task, lang):
        raise ValueError(f"{task.label}按发言人分轮保存，操作台改不了它的稿子")

    record = Ledger(s.db_file).get(article_id)
    if record is None or not record.store_dir:
        raise ValueError(f"台账里没有这篇文章或它还没落盘：{article_id}")

    path = s.output_path / record.store_dir / task.filename_for(lang)
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError:
        markdown = ""

    body = text.strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(replace_spoken(markdown, body, lang=lang), encoding="utf-8")

    logger.info("稿子已按人工校对写回：%s（%d 字）", path.name, len(body))
    return Ledger(s.db_file).record_production(
        article_id,
        str(task.kind),
        status="ok",
        lang=lang,
        instructions="人工校对（TTS 操作台）",
        output_path=path.relative_to(s.output_path).as_posix(),
        chars=len(body),
        # 人改的稿子没有模型，也没有调用 —— 这两个字段留空不是偷懒，
        # 是台账上「这一版不是 LLM 写的」的唯一标记。
        llm_provider=None,
        llm_model=None,
        calls=0,
    )

__all__ = [
    "Generated",
    "ProduceResult",
    "produce",
    "produce_all",
    "read_production",
    "save_script_text",
    "speech_segments_for",
]
