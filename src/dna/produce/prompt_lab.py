"""
提示词调试台 / The prompt workbench —— `dna prompt` 的实现。

要解决的问题 / What it is for:
    提示词搬到 `config/prompts/` 之后，调一句话是改一个 Markdown 文件。但改完
    要能立刻回答两个问题：**真正发出去的提示词长什么样**，以及**这么改模型会
    怎么答**。这个模块两件都做，而且保证与应用里的行为一致。
    Once the prompts live in `config/prompts/`, tuning one is editing a Markdown file.
    This module answers the two questions that follow — what prompt actually goes out,
    and how the model answers it — with the same behaviour the app has.

一致性是靠**调同一个函数**保证的，不是靠纪律 / Consistency by construction:
    `render()` 与 `run()` 都走 `service.generate_text()`——`dna produce` 与 GUI
    点「重做」走的是同一个函数。所以时长区间取自同一份 `profile.yaml`、
    正文取自同一个落盘目录、`instructions` 接在提示词末尾的位置也完全一样。
    另写一份 if/elif 分派会省事，但两边迟早漂移，而漂移之后「在调试台里验证过」
    这句话就不再意味着任何东西。
    Both paths call `service.generate_text()` — the very function `dna produce` and the
    workbench's redo button call. A second dispatcher would be easier to write and would
    eventually drift, at which point "verified in the workbench" would mean nothing.

    唯一的差别是**注入的 provider**：`render()` 注入
    `CapturingProvider`（零调用零费用），`run()` 注入 `get_llm()`（真机、计费）。
    The only difference is the injected provider.

`render()` 为什么能拿到完整提示词 / Why the rendered prompt is the real one:
    拦在 provider 边界上，所以 `chat_json()` 追加的那条「只输出 JSON」指令
    （含完整 JSON Schema）也在里面。在 `build_messages()` 那一层看会漏掉它。
    见 `llm/capture.py` 的模块说明。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from dna.core.config import Profile, Settings, get_settings, safe_profile
from dna.core.logging import get_logger
from dna.llm.base import LLMProvider
from dna.llm.capture import CapturingProvider, RecordingProvider, render_messages
from dna.llm.factory import get_llm
from dna.produce.service import Generated, generate_text, load_article
from dna.produce.tasks import ProductionKind, normalize_lang, prerequisite, spec
from dna.store.ledger import Ledger

logger = get_logger("produce.prompt_lab")

# 可调试的任务 / the tunable tasks
#
# 只列会调 LLM 的产物。`summary` 的英文版走翻译节点（`translated_from`），
# 所以 `--task summary --lang en` 看到的是 translate 的提示词——这正是应用里
# 发生的事，不要为了「一个任务对一个文件」把它掰直。
# Only LLM-backed kinds. The English summary goes through the translation node, so
# `--task summary --lang en` shows the translator's prompt: that is what the app does.
TASKS: tuple[ProductionKind, ...] = (
    ProductionKind.SUMMARY,
    ProductionKind.SHORTVIDEO,
    ProductionKind.NARRATION,
    ProductionKind.LONGFORM,
)

# 任务 → 用到的提示词文件 / which prompt files each task reads
#
# 给 `dna prompt --list` 用：调提示词的人第一个问题就是「这个任务读哪个文件」。
PROMPT_FILES: dict[ProductionKind, tuple[str, ...]] = {
    ProductionKind.SUMMARY: (
        "summarize.md",
        "translate.md（--lang en 时）",
        "_shared/instruction_block.{zh,en}.md",
    ),
    ProductionKind.SHORTVIDEO: (
        "shortvideo.{zh,en}.md",
        "_shared/professionalism.{zh,en}.md",
        "_shared/instruction_block.{zh,en}.md",
    ),
    ProductionKind.NARRATION: (
        "narration.{zh,en}.md",
        "_shared/professionalism.{zh,en}.md",
        "_shared/instruction_block.{zh,en}.md",
    ),
    ProductionKind.LONGFORM: (
        "longform_outline.md",
        "longform_section.md",
        "_shared/professionalism.{zh,en}.md",
        "_shared/language_directive.en.md（--lang en 时）",
        "_shared/instruction_block.{zh,en}.md",
    ),
}

# 干跑用的假回复 / canned replies for the dry run
#
# 长度是刻意挑的：短视频 130 字落在 25~35 秒内、口播 400 字落在 1~2 分钟内，
# 所以**不会触发回炉重写**，干跑就是「一个任务一条提示词」，看着清楚。
# 想看回炉重写那一版的提示词，把这里的字数调到区间外即可——它会多打印两条。
# The lengths are chosen to land inside the duration windows so no rewrite is triggered
# and the dry run shows one prompt per stage. Push them outside the window to see the
# rewrite prompts too.
_SECTIONS = 4
_PLACEHOLDER_REPLIES: dict[ProductionKind, list[str]] = {
    ProductionKind.SUMMARY: [
        json.dumps({"summary": "占位摘要" * 8, "tags": ["占位"]}, ensure_ascii=False),
        # 英文版走 translate，返回结构不同；两条都给，多的那条用不到也无妨
        json.dumps(
            {"entries": [{"id": "", "title_en": "Placeholder", "summary_en": "Placeholder."}]},
            ensure_ascii=False,
        ),
    ],
    ProductionKind.SHORTVIDEO: [
        json.dumps(
            {"title": "占位主标题", "subtitle": "占位副标题", "script": "占" * 130},
            ensure_ascii=False,
        )
    ],
    ProductionKind.NARRATION: [
        json.dumps(
            {"title": "占位主标题", "subtitle": "占位副标题", "script": "占" * 400},
            ensure_ascii=False,
        )
    ],
    ProductionKind.LONGFORM: [
        json.dumps(
            {
                "sections": [
                    {
                        "title": f"占位第 {i} 节",
                        "points": ["占位要点一", "占位要点二"],
                        "target_chars": 400,
                    }
                    for i in range(1, _SECTIONS + 1)
                ]
            },
            ensure_ascii=False,
        ),
        *[
            json.dumps(
                {"turns": [{"speaker": "narrator", "text": "占位内容" * 25}]},
                ensure_ascii=False,
            )
        ]
        * _SECTIONS,
    ],
}


@dataclass
class LabResult:
    """
    一次调试的结果 / The outcome of one workbench run.

    `prompts` 是**每一次 LLM 调用**的完整 messages，按发生顺序排。长文案会有
    「提纲 + 每节一条」十几条；短视频若回炉重写过也会多出几条——调提示词时
    最要紧的信息之一就是「这一篇花了几次调用」。
    One entry per LLM call, in order. Long-form yields an outline plus one per section;
    a rewritten script adds entries. How many calls a piece cost is exactly what a
    prompt tuner needs to see.
    """

    task: ProductionKind
    lang: str
    variant: str | None
    article_id: str
    article_title: str
    body_chars: int
    prompts: list[str] = field(default_factory=list)
    output: str = ""
    """真跑时模型的产物全文；干跑时为空 / the generated text; empty on a dry run."""
    chars: int = 0
    seconds: float | None = None
    calls: int = 0
    within_target: bool | None = None
    provider: str = ""
    error: str = ""
    billed: bool = False
    """这次是否真的花了钱 / whether this run actually cost money."""

    @property
    def ok(self) -> bool:
        return not self.error

    def summary(self) -> str:
        """一行摘要 / A one-line summary."""
        head = f"{spec(self.task).label} / {self.lang}"
        if self.error:
            return f"{head}：失败 —— {self.error}"
        cost = "计费" if self.billed else "零费用"
        line = f"{head}：{len(self.prompts)} 条提示词、{self.calls} 次调用（{cost}）"
        if not self.output:
            return line + "；未生成产物（干跑）"
        duration = f"，约 {self.seconds:.0f} 秒" if self.seconds else ""
        target = "" if self.within_target is not False else "（未落入目标区间）"
        return line + f"，产物 {self.chars} 字{duration}{target}"


def render(
    article_id: str,
    task: ProductionKind,
    *,
    lang: str = "zh",
    variant: str | None = None,
    instructions: str = "",
    settings: Settings | None = None,
    profile: Profile | None = None,
) -> LabResult:
    """
    只渲染提示词，不调模型 / Render the prompt without calling a model.

    **零调用零费用。** 走的是与 `dna produce` 完全相同的生成路径，只是把 provider
    换成 `CapturingProvider`：它记下真正发出去的 messages，并返回预置的假回复让
    整条链路跑完，好把长文案十几条提示词一次全拿到。
    Zero calls, zero cost: the same generation path as `dna produce`, with the provider
    swapped for one that records and returns canned replies so the whole chain completes.
    """
    provider = CapturingProvider(list(_PLACEHOLDER_REPLIES.get(task, [])))
    result = _invoke(
        article_id,
        task,
        provider,
        lang=lang,
        variant=variant,
        instructions=instructions,
        settings=settings,
        profile=profile,
        billed=False,
    )
    # 干跑的「产物」是假回复拼出来的：字数、时长、达标与否全都是占位符的属性，
    # 不是模型的。留着会被当成真结果读——这比不给更糟。
    # The dry run's "output" is the canned reply, so its length and duration describe the
    # placeholder rather than the model. Leaving them in invites misreading.
    result.output = ""
    result.chars = 0
    result.seconds = None
    result.within_target = None
    return result


def run(
    article_id: str,
    task: ProductionKind,
    *,
    lang: str = "zh",
    variant: str | None = None,
    instructions: str = "",
    cache: bool = True,
    settings: Settings | None = None,
    profile: Profile | None = None,
    llm: LLMProvider | None = None,
) -> LabResult:
    """
    真机跑一次并把产物带回来 / Run it for real and bring the output back.

    ⚠️ **这个函数花钱。** 调用方（CLI）负责在此之前确认。
    与 `dna produce` 的差别只有一个：**不写文件、不记台账**。调试台跑十次不该在
    产物目录里留十份垃圾，也不该把台账的产物历史搅乱——那是产物层的账本，
    记的是「发布用的那一版是谁写的」。
    The only difference from `dna produce`: nothing is written to disk and nothing is
    recorded in the ledger. Ten tuning runs must not leave ten files behind, nor pollute
    the production history, which records which version was published.

    参数 / Args:
        cache: 是否走 LLM 磁盘缓存。**默认走**——改过提示词的话缓存键自然变了，
            必然 miss；没改而重跑一次是免费的。想在同一份提示词上再抽一个样本
            （temperature 不为 0，产出会变）才需要关掉它。
            On by default: an edited prompt misses the cache naturally, while re-running
            an unedited one is free. Turn it off only to draw another sample.
    """
    # 套一层记录：真机这一次发出去的提示词也要能看到（理由见 RecordingProvider）
    provider = RecordingProvider(llm or get_llm(cache=cache))
    return _invoke(
        article_id,
        task,
        provider,
        lang=lang,
        variant=variant,
        instructions=instructions,
        settings=settings,
        profile=profile,
        billed=llm is None,
    )


def prompt_files_for(task: ProductionKind) -> tuple[str, ...]:
    """这个任务读哪几个提示词文件 / Which prompt files one task reads."""
    return PROMPT_FILES.get(task, ())


def save(result: LabResult, root: Path) -> Path:
    """
    把这一次的提示词与产物存下来 / Save one run's prompts and output.

    落在 `outputs/prompt_lab/` 下，一次一个目录。为什么要存：调提示词是
    **前后对比**的活——改完一句话，要能 diff 出「这一改让产物哪里不同了」。
    只在终端里滚过去的话，上一版就找不回来了。
    Saved because prompt tuning is a before-and-after activity: after changing one line
    you need to diff what the change did to the output. Terminal scrollback loses it.
    """
    root.mkdir(parents=True, exist_ok=True)
    for index, prompt in enumerate(result.prompts, 1):
        (root / f"prompt_{index:02d}.txt").write_text(prompt, encoding="utf-8")
    if result.output:
        (root / "output.md").write_text(result.output, encoding="utf-8")
    (root / "meta.json").write_text(
        json.dumps(
            {
                "task": str(result.task),
                "lang": result.lang,
                "variant": result.variant,
                "article_id": result.article_id,
                "article_title": result.article_title,
                "body_chars": result.body_chars,
                "prompt_files": list(prompt_files_for(result.task)),
                "calls": result.calls,
                "chars": result.chars,
                "seconds": result.seconds,
                "within_target": result.within_target,
                "provider": result.provider,
                "billed": result.billed,
                "error": result.error,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _invoke(
    article_id: str,
    task: ProductionKind,
    provider: LLMProvider,
    *,
    lang: str,
    variant: str | None,
    instructions: str,
    settings: Settings | None,
    profile: Profile | None,
    billed: bool,
) -> LabResult:
    """
    共用的调用骨架 / The shared invocation skeleton.

    前置检查照抄 `service.produce()`：台账里有没有这篇、有没有落盘目录、
    正文够不够 `min_body_chars`。**不能省**——省掉的话调试台会在应用会拒绝的
    输入上跑出一份「看起来没问题」的提示词。
    The same pre-checks `produce()` runs: without them the workbench would render a
    plausible-looking prompt for input the app would refuse.
    """
    s = settings or get_settings()
    prof = profile or safe_profile()
    task_spec = spec(task)
    lang = normalize_lang(lang)

    result = LabResult(
        task=task,
        lang=lang,
        variant=variant,
        article_id=article_id,
        article_title="",
        body_chars=0,
        provider=str(provider.info),
        billed=billed,
    )

    record = Ledger(s.db_file).get(article_id)
    if record is None:
        result.error = f"台账里没有这篇文章：{article_id}"
        return result
    if not record.store_dir:
        result.error = "这篇文章还没有落盘目录"
        return result

    article = load_article(article_id, s)
    if article is None:
        result.error = "读不到这篇文章的正文"
        return result

    result.article_title = article.title
    result.body_chars = len(article.text)

    if task_spec.min_body_chars and len(article.text) < task_spec.min_body_chars:
        result.error = (
            f"正文只有 {len(article.text)} 字，不足 {task_spec.min_body_chars} 字，"
            f"本篇不适合生成{task_spec.label}"
        )
        return result

    # 前置产物缺失时**报错，不代为生成** / Missing prerequisites report rather than fill
    #
    # `produce()` 缺前置会自动补上（那时用户已经准备付钱了）。调试台不能这么做：
    # 它默认是免费命令，不该悄悄发起一次计费调用。
    # 但也不能什么都不说——英文总结的输入是**已写好的中文总结**，缺了的话
    # 渲染出来的是一份「摘要：」后面空着的提示词，看着像提示词写坏了。
    # `produce()` fills a missing prerequisite because the user is already paying by then.
    # The workbench must not, being a free command by default; but staying silent would
    # render a prompt with an empty summary field, which reads like a broken prompt.
    need = prerequisite(task, lang)
    if need is not None:
        need_kind, need_lang = need
        upstream = Ledger(s.db_file).latest_production(article_id, str(need_kind), need_lang)
        if upstream is None or not upstream.ok:
            need_spec = spec(need_kind)
            result.error = (
                f"这份产物的输入是「{need_spec.label} / {need_lang}」，而它还没生成。"
                f"先跑 dna produce {article_id[:8]} -k {need_kind} --lang {need_lang}"
                f"（会计费），或换一篇已经有的文章"
            )
            return result

    directory = s.output_path / record.store_dir
    generated: Generated | None = None
    try:
        generated = generate_text(
            task_spec,
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
        result.error = " ".join(str(exc).split())[:300]

    # 提示词无论成败都要交出来 / the prompts come back either way
    #
    # 干跑到最后一定是「预置回复用完」这个异常收场，而那时提示词早就记全了。
    # 只在成功路径上取提示词的话，这个工具在它最常用的模式下什么都不返回。
    if isinstance(provider, (CapturingProvider, RecordingProvider)):
        result.prompts = [render_messages(batch) for batch in provider.captured]
        result.calls = len(provider.captured)
        if isinstance(provider, CapturingProvider) and result.prompts:
            # 干跑拿到提示词就算成功——它本来就不要产物
            result.error = ""

    if generated is not None:
        result.output = generated.text
        result.chars = generated.chars
        result.seconds = generated.seconds
        result.calls = generated.calls or result.calls
        result.within_target = generated.within_target

    return result


__all__ = [
    "PROMPT_FILES",
    "TASKS",
    "LabResult",
    "prompt_files_for",
    "render",
    "run",
    "save",
]
