"""
产物任务注册表 / The production task registry.

一张表定义「这个应用能为一篇文章生成什么」。CLI、GUI 都读它——
**加一种产物只改这里**，两个前端自动跟上，不会出现「命令行支持但界面没有」。
One table defines what the application can produce for an article. Both the CLI and the
GUI read it, so adding a production kind means editing this file only and neither
front-end can drift out of sync.

每个任务声明它的元数据 / Each task declares its metadata:
    输出文件名、前置依赖、**是否进批量**、预估调用次数、是否要念出来
    the output filename, its prerequisite, whether it joins the batch, the estimated call
    count and whether it is meant to be spoken

**不含生成函数**：分派在 `service._generate` 里。放这里会让 tasks 反向依赖
narration 与 pipeline，而 tasks 现在是一张零依赖的纯数据表，两个前端都能安全导入。
No generator function lives here; dispatch is in `service._generate`. Putting it here
would make this table depend on `narration` and `pipeline`, whereas it is currently pure
data that both front-ends can import without pulling in the world.

为什么长文案不进批量 / Why long-form stays out of the batch:
    它是最贵的产物——提纲 1 次 + 每节 1 次，一篇要 5~9 次调用，
    而其余四项加起来才 3 次。让它跟着 `--all` 跑，一次误操作就是十几倍的账单。
    It is by far the most expensive: an outline call plus one per section, five to nine
    calls against three for all the others combined. Letting it ride along with `--all`
    would turn one mistaken keystroke into a bill an order of magnitude larger.

为什么音频也不进批量 / Why the audio kinds stay out too:
    它们**不花钱，但很花时间**——实测 RTF≈2.5，口播稿要等约 4 分钟，
    长文案约 37 分钟。批量里混进一个半小时的任务，等同于把界面按死。
    成本护栏防的是账单，时间护栏防的是「点一下之后这台机器就没法用了」，
    两者都要拦，拦的理由不同。
    They cost nothing but take a great deal of time: measured at RTF≈2.5, a narration
    takes about four minutes and a long-form script about thirty-seven. A batch carrying
    a half-hour task is a frozen machine. The money guard and the time guard are
    different guards for different reasons; both are needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProductionKind(StrEnum):
    """一篇文章可以生成的产物 / What can be produced for one article."""

    SUMMARY_ZH = "summary_zh"
    SUMMARY_EN = "summary_en"
    SHORTVIDEO = "shortvideo"
    NARRATION = "narration"
    LONGFORM = "longform"
    # 音频：由对应的稿子合成，不调用 LLM / audio, synthesised from the script above
    SHORTVIDEO_AUDIO = "shortvideo_audio"
    NARRATION_AUDIO = "narration_audio"
    LONGFORM_AUDIO = "longform_audio"


@dataclass(frozen=True)
class TaskSpec:
    """一种产物的定义 / The definition of one production kind."""

    kind: ProductionKind
    label: str
    """中文名，GUI 表头与 CLI 提示都用它 / the Chinese name shown in both front-ends."""

    filename: str
    """输出文件名，相对文章目录 / output file name, relative to the article directory."""

    requires: ProductionKind | None = None
    """
    前置产物 / prerequisite.

    英文总结依赖中文总结：翻译的输入是已写好的中文，不是原始正文。
    这样中英两版一定说的是同一件事，成本也低得多。
    The English summary translates the finished Chinese one rather than the raw body, so
    the two versions are guaranteed to say the same thing, at much lower cost.
    """

    in_batch: bool = True
    """是否参与 `--all` 批量 / whether `--all` includes this kind."""

    approx_calls: int = 1
    """
    预估 LLM 调用次数，用于在按下按钮**之前**把成本摆出来。
    Estimated LLM calls, shown before the button is pressed rather than after.
    """

    needs_variant: bool = False
    """是否需要指定形式（长文案的专题/访谈）/ whether a variant must be chosen."""

    min_body_chars: int = 0
    """正文低于此长度就不该生成 / below this body length the kind is not offered."""

    spoken: bool = False
    """
    这份产物是要被**念出来**的 / this production is meant to be read aloud.

    两个用处 / Two uses:
        1. 只有它才需要量时长、才会触发回炉重写（总结不需要）
        2. **它有一格对应的音频**（`audio_kind`）——界面据此决定哪几格该有合成按钮
    Only spoken kinds are measured against a duration window, and only they have a
    matching audio kind, which is how the workbench knows where to offer synthesis.
    """

    audio_of: ProductionKind | None = None
    """
    这一格是哪份稿子的音频 / which script this is the audio of.

    非 None 就意味着 / A non-None value means:
        · 生成它**不调用 LLM，不花钱**（花的是时间）
        · 输出是二进制，按字节写盘
        · 台账里记的 provider 是 TTS 后端，不是 LLM
    It costs time rather than money, writes bytes rather than text, and records the TTS
    backend where the other kinds record the LLM.
    """


TASKS: dict[ProductionKind, TaskSpec] = {
    ProductionKind.SUMMARY_ZH: TaskSpec(
        kind=ProductionKind.SUMMARY_ZH,
        label="总结",
        filename="summary.zh.md",
    ),
    ProductionKind.SUMMARY_EN: TaskSpec(
        kind=ProductionKind.SUMMARY_EN,
        label="英文总结",
        filename="summary.en.md",
        requires=ProductionKind.SUMMARY_ZH,
    ),
    ProductionKind.SHORTVIDEO: TaskSpec(
        kind=ProductionKind.SHORTVIDEO,
        label="短视频文案",
        filename="shortvideo.zh.md",
        approx_calls=1,
        spoken=True,
    ),
    ProductionKind.NARRATION: TaskSpec(
        kind=ProductionKind.NARRATION,
        label="口播文案",
        filename="narration.zh.md",
        approx_calls=1,
        spoken=True,
    ),
    ProductionKind.LONGFORM: TaskSpec(
        kind=ProductionKind.LONGFORM,
        label="长文案",
        filename="longform.zh.md",
        in_batch=False,  # 见模块文档：最贵的产物，必须显式指定
        approx_calls=7,
        needs_variant=True,
        min_body_chars=800,
        spoken=True,
    ),
    ProductionKind.SHORTVIDEO_AUDIO: TaskSpec(
        kind=ProductionKind.SHORTVIDEO_AUDIO,
        label="短视频音频",
        filename="shortvideo.zh.wav",
        requires=ProductionKind.SHORTVIDEO,
        in_batch=False,
        approx_calls=0,
        audio_of=ProductionKind.SHORTVIDEO,
    ),
    ProductionKind.NARRATION_AUDIO: TaskSpec(
        kind=ProductionKind.NARRATION_AUDIO,
        label="口播音频",
        filename="narration.zh.wav",
        requires=ProductionKind.NARRATION,
        in_batch=False,
        approx_calls=0,
        audio_of=ProductionKind.NARRATION,
    ),
    ProductionKind.LONGFORM_AUDIO: TaskSpec(
        kind=ProductionKind.LONGFORM_AUDIO,
        label="长文案音频",
        filename="longform.zh.wav",
        requires=ProductionKind.LONGFORM,
        in_batch=False,
        approx_calls=0,
        audio_of=ProductionKind.LONGFORM,
    ),
}

# 稿子 → 它的音频 / script kind to its audio kind
AUDIO_OF: dict[ProductionKind, ProductionKind] = {
    task.audio_of: kind for kind, task in TASKS.items() if task.audio_of is not None
}

# 批量顺序：依赖在前 / batch order, prerequisites first
BATCH_ORDER: tuple[ProductionKind, ...] = (
    ProductionKind.SUMMARY_ZH,
    ProductionKind.SUMMARY_EN,
    ProductionKind.SHORTVIDEO,
    ProductionKind.NARRATION,
)

# GUI 表格的列顺序 / column order in the workbench table
DISPLAY_ORDER: tuple[ProductionKind, ...] = (
    ProductionKind.SUMMARY_ZH,
    ProductionKind.SUMMARY_EN,
    ProductionKind.SHORTVIDEO,
    ProductionKind.NARRATION,
    ProductionKind.LONGFORM,
)


def spec(kind: ProductionKind | str) -> TaskSpec:
    """
    取一种产物的定义 / Look up one kind's definition.

    抛出 / Raises:
        KeyError: 未知的产物类型
    """
    return TASKS[ProductionKind(kind)]


def batch_kinds() -> tuple[ProductionKind, ...]:
    """`--all` 会跑哪几种 / Which kinds `--all` covers."""
    return tuple(k for k in BATCH_ORDER if TASKS[k].in_batch)


def estimate_calls(kinds: list[ProductionKind]) -> int:
    """
    预估这批任务要花多少次调用 / Estimate the calls a batch will cost.

    在执行**之前**告诉人要花多少——事后才知道是没有意义的。
    Told before the run, not after: after is too late to matter.
    """
    return sum(TASKS[k].approx_calls for k in kinds)


def audio_kind(kind: ProductionKind | str) -> ProductionKind | None:
    """
    这份稿子对应的音频产物 / The audio production for one script kind.

    不是口播类的返回 None —— 总结不会被念出来，界面上也就不该有合成按钮。
    Returns None for kinds that are not spoken, so no synthesis button is offered.
    """
    return AUDIO_OF.get(ProductionKind(kind))


def json_sidecar(spec_: TaskSpec) -> str | None:
    """
    该产物是否额外产出一份 JSON / Whether this kind writes a JSON sibling.

    只有长文案有：它要按发言人切成 turns 给 TTS 分配音色。
    Only the long-form script does: the TTS stage needs it split into speaker turns.
    """
    if spec_.kind is ProductionKind.LONGFORM:
        return spec_.filename.replace(".md", ".json")
    return None


__all__ = [
    "AUDIO_OF",
    "BATCH_ORDER",
    "DISPLAY_ORDER",
    "TASKS",
    "audio_kind",
    "ProductionKind",
    "TaskSpec",
    "batch_kinds",
    "estimate_calls",
    "json_sidecar",
    "spec",
]
