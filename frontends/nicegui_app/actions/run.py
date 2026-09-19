"""跑生成：把一次产物生成扔进后台线程 / Running a production off the event loop。"""

from __future__ import annotations

from nicegui import run

from dna.core.config import get_settings
from dna.core.logging import get_logger
from dna.core.urls import extract_urls
from dna.produce import (
    ProduceResult,
    ProductionKind,
    produce,
    spec,
)
from dna.produce.tasks import audio_kind, normalize_lang
from dna.store import Ledger
from dna.store.ledger import ArticleRecord
from dna.tts.base import SpeechSegment

logger = get_logger("gui.actions")

async def run_production(
    article_id: str,
    kind: ProductionKind | str,
    *,
    lang: str = "",
    variant: str | None = None,
    force: bool = False,
    instructions: str = "",
    progress: dict | None = None,
    segments: list[SpeechSegment] | None = None,
    rendered: dict[int, bytes] | None = None,
) -> ProduceResult:
    """
    在后台线程里生成一种产物 / Produce one kind on a worker thread.

    provider 在线程里构造：`get_llm()` 会读 `.env` 并可能建立连接，
    放在事件循环里做同样会卡一下。
    The provider is built inside the thread: `get_llm()` reads `.env` and may open a
    connection, which would also stall the event loop.

    **音频产物不构造 LLM。**它一分钱都不花，却会因为 `get_llm()` 而要求 API key ——
    在没配 key 的机器上，合成语音本来是能跑的，却会在这一步先失败。
    Audio kinds do not build an LLM: they spend nothing, yet `get_llm()` would demand an
    API key and fail on a machine where synthesis would otherwise work fine.

    `segments` 只给音频产物用：TTS 操作台里逐段调好的分段从这里进来，
    走的仍是同一条落盘路径（台账、时长、字幕都一样）。
    `rendered` 是操作台里已经逐段生成好的波形，那些段不再重新合成。

    `progress` 是一个**共享字典**，工作线程往里写、界面定时读。
    跨线程直接改 NiceGUI 元素是不安全的；写字典再由 UI 侧轮询是最简单可靠的做法。
    A shared dict written by the worker and polled by the UI: mutating NiceGUI elements
    from another thread is unsafe, and polling a dict is the simplest safe alternative.
    """
    lang = normalize_lang(lang)
    is_audio = spec(kind).audio_of is not None

    def _on_progress(done: int, total: int, seconds: float) -> None:
        if progress is not None:
            progress.update(done=done, total=total, seconds=seconds)

    def _work() -> ProduceResult:
        # provider 交给 `produce` 自己构造：**重做时它要一个不带缓存的**。
        # 在这里先建好再传进去，就会把「重做要绕开缓存」这条规则绕过去——
        # 于是重做调了 LLM、写了文件，内容却一字未变。
        # `produce` builds the provider itself because a redo needs an uncached one;
        # constructing it here would bypass that rule and make every redo a no-op.
        return produce(
            article_id,
            kind,
            lang=lang,
            variant=variant,
            force=force,
            instructions=instructions,
            on_progress=_on_progress if is_audio else None,
            segments=segments,
            rendered=rendered,
        )

    return await run.io_bound(_work)


def audio_estimate_seconds(
    record: ArticleRecord, kind: ProductionKind | str, lang: str = ""
) -> float:
    """
    合成这一格音频大约要等多久 / How long synthesising this cell will take.

    按**稿子的估算时长 × 实测 RTF** 算。长文案要等半小时以上，
    这个数字必须在按钮按下之前就摆出来——否则界面看起来就是卡死了。
    Computed from the script's projected duration times the measured real-time factor. A
    long-form script runs past half an hour, and without saying so up front the interface
    merely looks frozen.
    """
    from dna.produce.tasks import spec as _spec
    from dna.tts import estimate_synthesis_seconds

    lang = normalize_lang(lang)
    script_kind = _spec(kind).audio_of
    if script_kind is None:
        return 0.0

    settings = get_settings()
    ledger = Ledger(settings.db_file)
    script = ledger.latest_production(record.id, str(script_kind), lang)
    if script is None:
        return 0.0
    # 倍率从配置读：写死的那个在 CPU 上少报五倍（issues/009）
    return estimate_synthesis_seconds(
        script.est_seconds or 0.0, rtf=settings.tts_rtf_estimate
    )


def last_instructions(
    record: ArticleRecord, kind: ProductionKind | str, lang: str = ""
) -> str:
    """
    上一版是带着什么额外要求生成的 / The extra requirements the last version used.

    用来预填「修改指令」输入框。**人调稿子是渐进的**——上次写了「用词再专业一点」，
    这次多半是在此基础上再加一条，而不是从零重想。每次都给一个空框，
    等于每次都要求人回忆上次改了什么。
    Used to pre-fill the instruction box. Tuning is incremental: the next round usually
    builds on the last one, and an empty box asks the user to remember what they wrote.
    """
    lang = normalize_lang(lang)
    production = Ledger(get_settings().db_file).latest_production(
        record.id, str(kind), lang
    )
    return (production.instructions or "") if production else ""


def audio_for(kind: ProductionKind | str) -> ProductionKind | None:
    """这份稿子对应的音频产物 / The audio kind matching a script kind."""
    return audio_kind(kind)


def preview_links(text: str) -> list[str]:
    """
    从粘贴的文本里认出有哪些链接 / Find the links in whatever was pasted.

    纯函数，**先给人看清单再动手抓**。粘一整段聊天记录进来时，「认出了哪几条」
    是唯一能提前发现「少粘了一条」或「多认了一个图片地址」的机会——
    抓完再发现就已经在台账里留下垃圾行了。
    A pure function that lists what was found before anything is fetched. When a whole
    chat log is pasted this is the only chance to notice a missing link or a stray image
    URL; discovering it afterwards means junk rows are already in the ledger.
    """
    return extract_urls(text or "")



def longform_estimate(record: ArticleRecord) -> str:
    """
    长文案的时长预估 / The projected duration of a long-form script.

    走 `plan_target_seconds` 而不是在界面里另算一遍。先前这里写着
    `min(text_len * 1.2, 4000) / 4.5 / 60`——那是**已经废弃的按字符数推导**，
    确认框里报的分钟数和实际生成的对不上。
    Delegates to `plan_target_seconds` instead of recomputing. The dialog previously
    carried its own character-based formula, long since replaced in the core, so the
    minutes it quoted no longer matched what was produced.
    """
    from dna.core.config import safe_profile
    from dna.core.models import Article
    from dna.narration.longform import plan_target_seconds

    profile = safe_profile()
    low, high = profile.longform_duration_seconds
    stub = Article(url=record.url, title=record.title, text="字" * record.text_len)
    seconds = plan_target_seconds(stub, low=low, high=high)
    return f"{seconds / 60:.0f} 分钟"
