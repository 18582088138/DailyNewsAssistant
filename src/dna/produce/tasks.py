"""
产物任务注册表 / The production task registry.

一张表定义「这个应用能为一篇文章生成什么」。CLI、GUI 都读它——
**加一种产物只改这里**，两个前端自动跟上，不会出现「命令行支持但界面没有」。
One table defines what the application can produce for an article. Both the CLI and the
GUI read it, so adding a production kind means editing this file only and neither
front-end can drift out of sync.

每个任务声明它的元数据 / Each task declares its metadata:
    输出文件名、前置依赖、**是否进批量**、预估调用次数、是否要念出来、
    非母语版本是翻译还是原生生成
    the output filename, its prerequisite, whether it joins the batch, the estimated call
    count, whether it is meant to be spoken, and whether other languages are translated
    or generated natively

语言是**维度，不是类型** / Language is a dimension, not a kind:
    `summary_zh` 与 `summary_en` 曾经是两种产物类型，于是表格里占两列——
    可它们是同一份东西的两个语言版本。文稿类要加英文版时这个建模就撑不住了：
    照原样得再造 `shortvideo_en`、`narration_en`、`longform_en` 加各自的音频，
    枚举翻倍而语义没变清楚一点。
    现在 `(kind, lang)` 才是一份产物的完整标识，文件名走 `filename_for(lang)`。
    They were two kinds occupying two columns, though they are two language versions of
    one thing. `(kind, lang)` now identifies a production, and the filename follows.

**不含生成函数**：分派在 `service.generate_text` 里。放这里会让 tasks 反向依赖
narration 与 pipeline，而 tasks 现在是一张零依赖的纯数据表，两个前端都能安全导入。
No generator function lives here; dispatch is in `service.generate_text`. Putting it here
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
from typing import TYPE_CHECKING

from dna.core.naming import lang_suffix_name

if TYPE_CHECKING:  # 只为类型标注，运行期不引入 config / typing only, no runtime import
    from dna.core.config import Profile

# 支持的输出语言 / the output languages every kind supports
#
# 默认中文：这个应用是给中文读者做的，英文版是**可选的第二份**，不是并列的两份。
# 界面上的开关默认停在中文，切过去才生成英文——英文版同样计费。
# Chinese is the default: the English edition is an optional second version rather than a
# co-equal one, and generating it bills like anything else.
LANGUAGES: tuple[str, ...] = ("zh", "en")

# 兜底值，只在 `.env` 没配或配了不认识的值时用到。
# **默认语言的真正来源是 `DEFAULT_LANGUAGE`（.env）**，见 `default_language()`：
# 这里曾经是一个写死的 "zh"，于是界面上那个「默认语言」下拉、`.env` 里那一行
# 都是摆设——改了永远没反应，而 `dna config` 还照样把它显示出来。
FALLBACK_LANGUAGE = "zh"

LANGUAGE_LABELS = {"zh": "中文", "en": "English"}


class ProductionKind(StrEnum):
    """一篇文章可以生成的产物 / What can be produced for one article."""

    SUMMARY = "summary"
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

    stem: str
    """
    输出文件名的主干 / the stem of the output file name.

    真实文件名由 `filename_for(lang)` 拼出来：`summary` + `zh` + `md` → `summary.zh.md`。
    语言后缀走 `core/naming.lang_suffix_name`，和期次级产物用的是同一条规则。
    The real name is assembled per language through the same suffix rule the issue-level
    outputs use.
    """

    extension: str = "md"
    """输出扩展名 / the output file's extension（音频是 `wav`）。"""

    requires: ProductionKind | None = None
    """
    前置产物（**同语言**）/ prerequisite, in the same language.

    音频依赖它那份稿子：英文音频念英文稿，中文音频念中文稿。
    Audio depends on its script in the matching language.
    """

    translated_from: str = ""
    """
    非该语言的版本由这个语言翻译而来；空表示**每种语言都原生生成**。
    Other languages are translated from this one; empty means each is generated natively.

    总结类填 `zh`：翻译一份已写好的中文，比用英文重写一遍便宜，而且中英两版
    保证说的是同一件事——P3 已验证术语能原样保留。
    文稿类留空：**中文 30 秒的稿子翻成英文不是 30 秒的稿子**。时长是这三种文案的
    验收标准，刚按人工参考稿校准过一轮（issue 008），走翻译等于把那一轮作废，
    而且英文稿的实际时长会无声地不可控。
    Summaries translate: it is cheaper and guarantees both versions say the same thing.
    Scripts do not: a thirty-second Chinese script is not a thirty-second English one,
    and duration is precisely what these three kinds are accepted on.
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

    def filename_for(self, lang: str = "") -> str:
        """某个语言版本的文件名 / The file name of one language edition."""
        lang = normalize_lang(lang)
        return lang_suffix_name(self.stem, lang, self.extension)

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
    ProductionKind.SUMMARY: TaskSpec(
        kind=ProductionKind.SUMMARY,
        label="总结",
        stem="summary",
        translated_from="zh",  # 英文版翻译已写好的中文，见 translated_from 的说明
    ),
    ProductionKind.SHORTVIDEO: TaskSpec(
        kind=ProductionKind.SHORTVIDEO,
        label="短视频文案",
        stem="shortvideo",
        approx_calls=1,
        spoken=True,
    ),
    ProductionKind.NARRATION: TaskSpec(
        kind=ProductionKind.NARRATION,
        label="口播文案",
        stem="narration",
        approx_calls=1,
        spoken=True,
    ),
    ProductionKind.LONGFORM: TaskSpec(
        kind=ProductionKind.LONGFORM,
        label="长文案",
        stem="longform",
        in_batch=False,  # 见模块文档：最贵的产物，必须显式指定
        approx_calls=7,
        needs_variant=True,
        min_body_chars=800,
        spoken=True,
    ),
    ProductionKind.SHORTVIDEO_AUDIO: TaskSpec(
        kind=ProductionKind.SHORTVIDEO_AUDIO,
        label="短视频音频",
        stem="shortvideo",
        extension="wav",
        requires=ProductionKind.SHORTVIDEO,
        in_batch=False,
        approx_calls=0,
        audio_of=ProductionKind.SHORTVIDEO,
    ),
    ProductionKind.NARRATION_AUDIO: TaskSpec(
        kind=ProductionKind.NARRATION_AUDIO,
        label="口播音频",
        stem="narration",
        extension="wav",
        requires=ProductionKind.NARRATION,
        in_batch=False,
        approx_calls=0,
        audio_of=ProductionKind.NARRATION,
    ),
    ProductionKind.LONGFORM_AUDIO: TaskSpec(
        kind=ProductionKind.LONGFORM_AUDIO,
        label="长文案音频",
        stem="longform",
        extension="wav",
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
#
# **只跑默认语言。**英文版是可选的第二份，跟着 `--all` 一起跑等于每篇都翻倍计费，
# 而多数文章根本不需要英文版。要英文版就显式指定语言。
# Only the default language: the English edition is optional, and including it in the
# batch would double the bill on every article for something most do not need.
BATCH_ORDER: tuple[ProductionKind, ...] = (
    ProductionKind.SUMMARY,
    ProductionKind.SHORTVIDEO,
    ProductionKind.NARRATION,
)

# GUI 表格的列顺序 / column order in the workbench table
DISPLAY_ORDER: tuple[ProductionKind, ...] = (
    ProductionKind.SUMMARY,
    ProductionKind.SHORTVIDEO,
    ProductionKind.NARRATION,
    ProductionKind.LONGFORM,
)


def default_language() -> str:
    """
    默认输出语言，从 `.env` 的 `DEFAULT_LANGUAGE` 读 / The configured default language.

    每次调用都现读（`get_settings()` 自己有进程内缓存），不在导入时定住 ——
    定住的话测试里改了配置也不生效，而这正是它原先失效的方式。
    """
    from dna.core.config import get_settings

    value = str(get_settings().default_language).strip().lower()
    return value if value in LANGUAGES else FALLBACK_LANGUAGE


def normalize_lang(lang: str | None) -> str:
    """
    归一化语言代码，不认识的退回默认 / Normalise a language code, falling back to default.

    退回而不是报错：语言来自界面开关与命令行参数，写错了应该出配置里的那个语言，
    而不是让整次生成失败。
    Falls back rather than raising: the value comes from a switch and a flag, and a typo
    should yield the default edition rather than abort the run.
    """
    value = (lang or "").strip().lower()
    return value if value in LANGUAGES else default_language()


def prerequisite(kind: ProductionKind | str, lang: str) -> tuple[ProductionKind, str] | None:
    """
    这份产物依赖哪一份 / What this production depends on, if anything.

    返回 `(前置类型, 前置语言)`。两条规则，都由 `TaskSpec` 的字段推出来，
    不在这里写死 / Two rules, both derived from the spec rather than hard-coded here:

        音频       → 同语言的那份稿子（英文音频念英文稿）
        翻译型产物 → 源语言的那一份（英文总结依赖中文总结）
    """
    task = spec(kind)
    lang = normalize_lang(lang)

    if task.audio_of is not None:
        return task.audio_of, lang
    if task.translated_from and lang != task.translated_from:
        return task.kind, task.translated_from
    return None


def spec(kind: ProductionKind | str) -> TaskSpec:
    """
    取一种产物的定义 / Look up one kind's definition.

    抛出 / Raises:
        KeyError: 未知的产物类型
    """
    return TASKS[ProductionKind(kind)]


# 产物 → profile 里的字数窗口字段 / kind to the profile field holding its window
#
# **这张表是唯一的一份。** 生成时按它取窗口，界面判断「这一格超长了吗」也按它取，
# 所以两边不可能对不上——以前这三条映射在 `service.py` 里各写了一遍，
# 改了配置字段名就得记得三处都改。
# One table, read both when generating and when deciding whether a cell is over length,
# so the two cannot disagree. The mapping used to be spelled out three times in
# `service.py`.
_CHAR_WINDOW_FIELDS: dict[ProductionKind, str] = {
    ProductionKind.SUMMARY: "summary_chars",
    ProductionKind.SHORTVIDEO: "shortvideo_chars",
    ProductionKind.NARRATION: "narration_chars",
}


def char_window(kind: ProductionKind | str, profile: Profile) -> tuple[int, int] | None:
    """
    这种产物的字数区间 / The character window this kind is accepted against.

    返回 None 表示**不按字数验收**：长文案的目标是时长（字数由时长推算，见
    `narration/longform.py`），音频根本不是文本。对这些返回一个假窗口，
    界面就会把正常的长文案标成「超长」。
    None means the kind is not judged on characters: long-form derives its bounds from a
    duration target, and audio is not text at all. Returning a made-up window for them
    would paint every healthy long-form cell as over length.
    """
    field_name = _CHAR_WINDOW_FIELDS.get(ProductionKind(kind))
    if field_name is None:
        return None
    low, high = getattr(profile, field_name)
    return int(low), int(high)


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


def json_sidecar(spec_: TaskSpec, lang: str = "") -> str | None:
    """
    该产物是否额外产出一份 JSON / Whether this kind writes a JSON sibling.

    只有长文案有：它要按发言人切成 turns 给 TTS 分配音色。
    Only the long-form script does: the TTS stage needs it split into speaker turns.
    """
    lang = normalize_lang(lang)
    if spec_.kind is ProductionKind.LONGFORM:
        return lang_suffix_name(spec_.stem, lang, "json")
    return None


__all__ = [
    "AUDIO_OF",
    "BATCH_ORDER",
    "DISPLAY_ORDER",
    "TASKS",
    "ProductionKind",
    "TaskSpec",
    "audio_kind",
    "batch_kinds",
    "char_window",
    "estimate_calls",
    "json_sidecar",
    "spec",
]
