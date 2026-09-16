"""
YAML 层 / The YAML layer.

订阅源清单与个人偏好（`config/*.yaml`），可热改、可进 git、**由用户手工调整**。
The source list and personal preferences: human-editable and committed.

写回只能按行 patch（见 `core/config_edit.py`）：`yaml.safe_dump` 会抹掉
`profile.yaml` 里那些「为什么」注释，而那是这个项目最贵的资产之一。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dna.core.config.settings import DEFAULT_CONFIG_DIR
from dna.core.errors import ConfigError
from dna.core.logging import get_logger
from dna.core.models import Language, SourceKind

_logger = get_logger("core.config")



class SourceFilter(BaseModel):
    """
    条目过滤规则 / Item filtering rules.

    在**抓正文之前**执行，因此过滤掉的条目完全不产生网络与存储开销。
    Applied before the body is fetched, so filtered items cost no network or storage.

    规则次序 / Rule order:
        exclude 命中 → 丢弃（优先级最高，宁可少收不要错收）
        include 非空且一条都没命中 → 丢弃
        标题过短 / 过旧 → 丢弃
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    include: list[str] = Field(
        default_factory=list,
        description="命中任一才保留；留空不限制 / keep if one matches, empty means no limit",
    )
    exclude: list[str] = Field(
        default_factory=list, description="命中任一即丢弃 / drop if any matches"
    )
    min_title_length: int = Field(
        default=0, ge=0, description="标题最少字数，滤掉「快讯」这类空标题 / minimum title length"
    )
    max_age_days: int | None = Field(
        default=None,
        ge=0,
        description="只要最近 N 天的；None 不限 / keep only the last N days, None = no limit",
    )

    def is_empty(self) -> bool:
        """是否没有任何规则 / Whether no rule is configured at all."""
        return not (
            self.include or self.exclude or self.min_title_length or self.max_age_days is not None
        )


class SourceConfig(BaseModel):
    """一个订阅源的配置 / Configuration of a single news source."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str
    name: str
    kind: SourceKind = SourceKind.RSS
    url: str
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    max_items: int | None = Field(default=None, description="覆盖全局上限 / overrides global cap")
    lang: Language | None = Field(default=None, description="源语言，决定是否需要翻译")
    filters: SourceFilter | None = Field(
        default=None, description="该源专属的过滤规则 / filtering rules specific to this source"
    )
    # 按源覆盖媒体上限：arXiv 摘要页根本没有配图，微信长文却可能有二十几张。
    # 用一个全局值伺候所有源，要么浪费带宽要么漏素材。
    # Per-source overrides: an arXiv abstract has no images at all while a long WeChat
    # post may carry twenty. One global value either wastes bandwidth or misses material.
    max_images: int | None = Field(default=None, description="覆盖 profile 的配图上限")
    max_videos: int | None = Field(default=None, description="覆盖 profile 的视频上限")


class Tuning(BaseModel):
    """
    进阶旋钮 / Advanced tuning knobs.

    **只在 `config/profile.yaml` 里改，不进设置面板。** 它们全都曾经写死在代码里
    （`score.py` 的权重、`dedup.py` 的阈值、`longform.py` 的章节数、`duration.py`
    的语速），而每一个都直接影响「日报收录什么、一篇花多少钱、稿子多长」——
    按用户定的优先级（env > config > docs > code），这些必须可配。

    为什么不摆进界面：它们要懂后果才该动（改判重阈值会让两件事混成一条，
    改语速会连带推翻已经校准过的字数窗口）。面板是日常设置，这里是调参。
    Deliberately file-only: changing these requires understanding the consequences, and
    the panel is for everyday settings.
    """

    model_config = ConfigDict(extra="forbid")

    # -- 选题打分 / topic scoring（决定日报收录什么）--------------------------
    # 用规则不用 LLM：可解释、稳定、免费。LLM 打分会飘，排序跟着抖。
    weight_keyword: float = Field(default=0.35, ge=0, le=1)
    weight_multi_source: float = Field(default=0.25, ge=0, le=1)
    weight_freshness: float = Field(default=0.20, ge=0, le=1)
    weight_substance: float = Field(default=0.12, ge=0, le=1)
    weight_media: float = Field(default=0.08, ge=0, le=1)

    source_saturation: int = Field(default=4, ge=1, description="几家媒体报道算满分")
    substance_saturation: int = Field(default=1500, ge=1, description="正文多少字算满分")
    freshness_window_hours: int = Field(default=48, ge=1, description="新鲜度衰减到零的小时数")

    # 关键词只在正文前若干字里扫：全文扫一遍在几十条上不划算，
    # 但**窗口太小会让靠后出现的关键词命不中**，等于悄悄改变了选题口味。
    video_scan_chars: int = Field(default=600, ge=100)
    focus_scan_chars: int = Field(default=2000, ge=100)

    # -- 判重 / de-duplication ------------------------------------------------
    # 阈值保守：漏合并读者看得见（两条相似的），错合并看不见（两件事混成一条）。
    hamming_threshold: int = Field(default=3, ge=0, le=64)
    simhash_sample_chars: int = Field(default=500, ge=100)

    # -- 长文案 / long-form（最贵的产物）-------------------------------------
    longform_min_body_chars: int = Field(default=800, ge=0, description="正文不足这么长就拒绝")
    longform_expansion_ratio: float = Field(default=1.2, gt=0, description="目标字数 = 原文 × 此值")
    longform_min_sections: int = Field(default=4, ge=1)
    longform_max_sections: int = Field(default=8, ge=1, description="每节一次调用，这就是费用上限")

    # -- 语速与停顿：**刻意不放在这里** / deliberately not configurable yet -----
    #
    # `duration.py` 的 `CHARS_PER_SECOND_ZH` / `WORDS_PER_SECOND_EN` /
    # `MIXED_COPY_CHAR_FACTOR` 与 `tts/base.py` 的两个停顿仍是代码常量。
    # 不是漏了，是**故意的**：上面那些字数窗口是按当前语速校准出来的
    # （issues/008），动语速就等于同时推翻它们，而校准是靠人工参考稿做的。
    # issues/009 里用户的决定也是「已量出，本阶段不改」。
    # 真要开放，得连着重做一轮校准 —— 到那时再加，别现在放一个改了就错的旋钮。
    # Left in code on purpose: the character windows above were calibrated against the
    # current rates, so exposing them without redoing that calibration would invite a
    # change that silently invalidates the acceptance criteria.

    # -- 趋势综述 / trend note ------------------------------------------------
    min_entries_for_trend: int = Field(default=4, ge=1, description="低于此不写综述，省一次调用")

    @model_validator(mode="after")
    def _sections_must_not_be_reversed(self) -> Tuning:
        if self.longform_min_sections > self.longform_max_sections:
            raise ValueError(
                f"longform 章节数下限大于上限："
                f"({self.longform_min_sections}, {self.longform_max_sections})"
            )
        return self


class Profile(BaseModel):
    """
    个人偏好：决定选题倾向、篇幅与语言 / Personal preferences driving selection and length.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    focus_keywords: list[str] = Field(default_factory=list, description="命中加权")
    exclude_keywords: list[str] = Field(default_factory=list, description="命中即排除")
    video_keywords: list[str] = Field(
        default_factory=list, description="命中则倾向标记 need_video / hints for the video flag"
    )
    digest_max_entries: int = 15

    # 超出字数区间时最多回炉几次 / how many rewrite rounds a too-long draft gets
    #
    # 文案与摘要分开：摘要**每条都跑**，一期几十条，回炉一次成本就翻倍；
    # 文案单篇按需跑，多给一次机会划得来。
    # 这两个值以前写死在 `narration/script_builder.py` 与 `pipeline/summarize.py`
    # 里（都是 1），而 `profile.yaml` 的注释写的是「文案最多 2 次，摘要最多 1 次」
    # —— 按 config > code 的优先级，配置那份才是意图。
    copy_max_rewrites: int = 2
    summary_max_rewrites: int = 1

    # 喂给模型的正文上限 / how much body text each node sends
    #
    # 两个数不一样是**刻意的**，不是漂移：摘要每条都跑，上限直接乘以条目数；
    # 文案单篇按需跑，宽一点只影响这一次调用。
    # 它们此前分别写死在 `summarize.py` 与 `script_builder.py` 里，而且**同名**
    # （都叫 MAX_BODY_CHARS）—— 读到哪一个全看 import 了谁。
    # 截短的代价是实测过的：一篇 4069 字的技术稿，被切掉的尾部含采样参数和一条
    # 关键局限，而那正是「必须说局限」要用的料（issues/008）。
    summary_body_chars: int = 3000
    script_body_chars: int = 6000

    # 每篇最多存几张图/几个视频。多存是为了攒素材：日报只用 1~3 张，但做长图、
    # 口播配图、视频封面时都要挑图，而**重抓拿不回当初那些图**——站点会换图删图。
    # Kept generous because the extras are a material library: the digest uses one to
    # three, while long images, voice-over stills and covers all need choices, and a
    # re-fetch cannot recover images the site has since swapped or deleted.
    max_images_per_article: int = 10
    max_videos_per_article: int = 2

    # 各任务的目标字数区间（中文字数）/ target length windows, in Chinese characters
    #
    # **字数是验收标准，秒数只是参照。** 提示词里要求的单位和程序验收的单位必须
    # 是同一个，否则两边各说一套：一份 200 字的稿子按中英混排能是 27 秒、
    # 按纯中文是 44 秒，拿秒数验收就会把一份合格的稿子反复回炉，
    # 而一份 168 字的稿子因为秒数刚好落在窗口内就静静通过了。（issue 007-C 的续集）
    # The prompt and the check must speak one unit. The same 200 characters measure
    # anywhere from 27 to 44 seconds depending on how much Latin text they carry, so
    # gating on seconds sends compliant drafts back while letting short ones through.
    #
    # 英文按语速折算成词数，不按字符（见 `duration.unit_window`）。
    summary_chars: tuple[int, int] = (80, 100)
    # 与 config/profile.yaml 保持一致：两处不同的话，装了 profile 的机器按 300、
    # 缺 profile 的机器按 250，而两者都「看起来是对的」。
    shortvideo_chars: tuple[int, int] = (200, 300)
    narration_chars: tuple[int, int] = (400, 800)

    # 时长区间（秒）/ duration windows —— 只用于提示词的开场句与产物记账
    video_duration_seconds: tuple[int, int] = (25, 35)
    narration_duration_seconds: tuple[int, int] = (60, 120)
    # 长文案的下限是**下限而不是目标**：原文短就写短，宁可 6 分钟也不注水凑 15 分钟。
    # 默认 300 与 `longform.MIN_TARGET_SECONDS` 一致——两处写不同的值，
    # 就会出现「配置说 10 分钟起、代码按 5 分钟起」这种谁也说不清的行为。
    # The lower bound is a floor, not a target: a short source yields a short script.
    # Kept equal to `longform.MIN_TARGET_SECONDS`, since two different values would mean
    # the config claims a ten-minute floor while the code applies five.
    longform_duration_seconds: tuple[int, int] = (300, 900)

    # 视频稿结尾的固定引导语 / the fixed sign-off at the end of a video script
    #
    # 放配置而不是写死在提示词里：这是**账号的品牌**，换账号、换栏目就要换，
    # 而提示词是「怎么写好文案」的规则，两者的变更频率完全不同。
    # Config rather than a hard-coded prompt line: this is channel branding that changes
    # with the account, whereas the prompt encodes how to write well. They change at
    # entirely different rates.
    cta_line: str = "关注我，下期分享 AI 行业最新进展"

    # 进阶旋钮（只在文件里改，不进设置面板）/ file-only advanced knobs
    tuning: Tuning = Field(default_factory=Tuning)

    @model_validator(mode="after")
    def _windows_must_not_be_reversed(self) -> Profile:
        """
        区间不能写反 / A window's lower bound may not exceed its upper bound.

        `(100, 80)` 能通过类型检查，然后让 `low <= n <= high` **永远为假**——
        每一篇产物都被标成超长，而配置文件看上去毫无问题。设置面板放开了这些字段之后
        写反只需要一次手滑，所以在模型上挡住，命令行和界面同时受益。
        A reversed window type-checks and then makes `low <= n <= high` never true, marking
        every production over-length while the file looks fine. Rejected at the model so
        both front-ends benefit.
        """
        for name in (
            "summary_chars",
            "shortvideo_chars",
            "narration_chars",
            "video_duration_seconds",
            "narration_duration_seconds",
            "longform_duration_seconds",
        ):
            low, high = getattr(self, name)
            if low > high:
                raise ValueError(f"{name} 的下限大于上限：({low}, {high})")
        return self

    # 「NEW」标识没有时间窗，也就没有对应的配置项 / the badge has no window and no setting
    #
    # 曾经有过一个 `new_badge_hours`（24 小时），已删除：时间窗解决错了问题。
    # 它让**昨天粘进来、今天还没处理**的链接第二天失去标识，而那恰恰是最需要
    # 标识的一条。判定改成看来源（人工投递 vs RSS 抓取），见
    # `produce.is_new_article`。
    # A 24-hour window used to live here and has been removed: it stripped the badge from
    # exactly the rows that still needed it. The predicate now keys on provenance.


def _read_yaml(path: Path) -> Any:
    """读取 YAML；文件缺失或格式错误时抛 ConfigError。"""
    if not path.exists():
        raise ConfigError(f"配置文件不存在 / config file not found: {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - 依赖 yaml 内部错误信息
        raise ConfigError(f"配置文件解析失败 / failed to parse {path}: {exc}") from exc


def load_sources(path: Path | None = None) -> list[SourceConfig]:
    """
    加载订阅源清单 / Load the source list from config/sources.yaml.

    只返回 enabled 为真的源；id 重复会直接报错，避免后续静默覆盖。
    Only enabled sources are returned. Duplicate ids raise, to avoid silent overwrites.
    """
    path = path or (DEFAULT_CONFIG_DIR / "sources.yaml")
    data = _read_yaml(path)
    raw_list = data.get("sources", []) if isinstance(data, dict) else data
    if not isinstance(raw_list, list):
        raise ConfigError(f"sources.yaml 顶层应为 sources 列表 / expected a list: {path}")

    sources = [SourceConfig(**item) for item in raw_list]

    seen: set[str] = set()
    for s in sources:
        if s.id in seen:
            raise ConfigError(f"订阅源 id 重复 / duplicate source id: {s.id}")
        seen.add(s.id)

    return [s for s in sources if s.enabled]


def load_profile(path: Path | None = None) -> Profile:
    """加载个人偏好 / Load personal preferences from config/profile.yaml."""
    path = path or (DEFAULT_CONFIG_DIR / "profile.yaml")
    return Profile(**_read_yaml(path))


def safe_profile(path: Path | None = None) -> Profile:
    """
    读取偏好，缺失或格式错误时用默认值 / Load preferences, falling back to defaults.

    `load_profile` 会抛异常，而调用方几乎都不该因为偏好读不到就停下来：
    采集、流水线、单篇生成、界面各有各的理由，但结论是同一个——**用默认值继续**。
    先前这段 try/except 在四个模块里各抄了一份（`pipeline/flow`、`store/intake`、
    `produce/service`、`nicegui_app/actions`），四份的差别只有日志文案，
    而其中界面那份还漏了日志。
    `load_profile` raises, and almost no caller should stop because preferences are
    unreadable. This try/except previously existed in four copies differing only in log
    wording — and the front-end copy had dropped the log entirely.

    什么时候**不该**用它 / When not to use it:
        `dna config` 这类「就是要显示配置对不对」的地方要用 `load_profile`，
        让错误浮出来。静默降级只适合「配置是辅助、主流程不能停」的场合。
        Commands whose whole purpose is to show whether the config is valid should call
        `load_profile` and let the error surface.
    """
    try:
        return load_profile(path)
    except Exception as exc:  # 偏好缺失不该阻断主流程
        _logger.warning("读取 profile.yaml 失败，使用默认值：%s", exc)
        return Profile()
