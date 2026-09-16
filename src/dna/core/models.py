"""
核心数据模型 / Core data models.

数据流向 / Data flow:
    RawItem  ──采集 fetch──▶  Article  ──清洗 clean──▶  NewsItem
             ──去重聚类 dedup & cluster──▶  Cluster
             ──摘要+双语+打分 summarize──▶  DigestEntry
             ──汇总 assemble──▶  DailyDigest   ← 三个发布应用的唯一输入

DailyDigest 是全流程的唯一事实源（序列化为 _digest.json），
图文 / 视频 / 播客三个应用只读它，因此任何一种输出都能脱离流水线单独重跑。
DailyDigest is the single source of truth (serialised to _digest.json). The three
publishing apps only read from it, so any single output can be regenerated
independently without re-running collection or the LLM summarisation.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# 枚举 / Enums
# ---------------------------------------------------------------------------


class Language(StrEnum):
    """输出语言 / Output language."""

    ZH = "zh"
    EN = "en"


class SourceKind(StrEnum):
    """信息源类型 / Kind of news source."""

    RSS = "rss"
    RSSHUB = "rsshub"
    INBOX = "inbox"  # 用户通过飞书机器人投递 / submitted via the Feishu bot
    GUI = "gui"  # 用户在界面上手动粘贴 / pasted manually in the GUI


class MediaKind(StrEnum):
    """媒体资产类型 / Kind of media asset."""

    IMAGE = "image"
    VIDEO = "video"


class AppKind(StrEnum):
    """发布形态 / Publishing app (output form)."""

    GRAPHIC = "graphic"  # 场景1 图文版
    VIDEO = "video"  # 场景2 视频版
    PODCAST = "podcast"  # 场景3 播客版


# ---------------------------------------------------------------------------
# 基础模型 / Base model
# ---------------------------------------------------------------------------


class _Base(BaseModel):
    """统一模型配置 / Shared model configuration."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


# ---------------------------------------------------------------------------
# 采集与抽取 / Collection and extraction
# ---------------------------------------------------------------------------


class MediaAsset(_Base):
    """
    一个媒体资产（配图或官方视频），必须携带来源信息以便在 references 中标注出处。
    A media asset (image or official video). It must always carry its origin so the
    reference file can credit the source.
    """

    kind: MediaKind
    url: str
    source_url: str = Field(description="该资产所在文章的 URL / URL of the article it came from")
    local_path: str | None = Field(
        default=None, description="下载后的本地相对路径 / local path once downloaded"
    )
    caption: str | None = None
    credit: str | None = Field(default=None, description="版权/来源署名 / copyright or attribution")
    width: int | None = None
    height: int | None = None


class RawItem(_Base):
    """
    采集到的原始条目，尚未抽取正文 / A freshly collected item, before body extraction.
    """

    source_id: str
    via: SourceKind
    url: str
    title: str = ""
    summary_raw: str = Field(default="", description="源自带的摘要 / summary provided by the feed")
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=datetime.now)
    raw_html: str | None = None


class Article(_Base):
    """
    抽取后的文章 / An article after body and media extraction.
    """

    url: str
    title: str
    text: str = Field(default="", description="正文纯文本 / plain-text body")
    author: str | None = None
    published_at: datetime | None = None
    media: list[MediaAsset] = Field(default_factory=list)
    extracted_at: datetime = Field(default_factory=datetime.now)
    extraction_ok: bool = Field(
        default=True,
        description="抽取是否成功；失败时降级为仅标题+链接 / "
        "False means degraded to title+link only",
    )


class NewsItem(_Base):
    """
    清洗归一化后进入流水线的新闻条目 / A normalised news item entering the pipeline.
    """

    id: str = Field(
        description="稳定标识，取 canonical_url 的哈希 / stable id, hash of canonical_url"
    )
    source_id: str
    via: SourceKind
    url: str
    canonical_url: str = Field(
        description="去除 utm 等追踪参数后的规范 URL / URL with tracking params stripped"
    )
    title: str
    text: str = ""
    published_at: datetime | None = None
    media: list[MediaAsset] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank / 标题不能为空")
        return v


# ---------------------------------------------------------------------------
# 聚类与成稿 / Clustering and digest
# ---------------------------------------------------------------------------


class Cluster(_Base):
    """
    同一事件的多源聚合 / Multiple reports of the same event, merged.

    members 里第一条为代表条目（canonical），其余为补充来源，
    全部来源链接都会写进最终的 references 文件。
    The first member is the canonical one; the rest are supplementary sources.
    Every member's link ends up in the generated reference file.
    """

    id: str
    members: list[NewsItem] = Field(min_length=1)
    canonical_url: str

    @property
    def canonical(self) -> NewsItem:
        """代表条目 / The representative item of this cluster."""
        return self.members[0]

    @property
    def refs(self) -> list[str]:
        """全部来源链接（去重保序）/ All source links, de-duplicated, order preserved."""
        seen: dict[str, None] = {}
        for m in self.members:
            seen.setdefault(m.url, None)
        return list(seen)


class DigestEntry(_Base):
    """
    日报中的一条成稿单元 / One ready-to-publish entry in the daily digest.

    中英双语字段成对出现：仅出中文时 *_en 为 None，
    补出英文版时只需回填这些字段，无需重跑采集。
    Bilingual fields come in pairs; *_en stays None for a Chinese-only run and can
    be back-filled later without re-collecting anything.
    """

    id: str
    rank: int = Field(
        ge=1, description="在日报中的排序，从 1 开始 / 1-based position in the digest"
    )
    cluster_id: str

    title_zh: str
    title_en: str | None = None
    summary_zh: str = Field(description="1~2 句中文总结 / a one-to-two sentence Chinese summary")
    summary_en: str | None = None

    score: float = Field(default=0.0, description="重要性评分 / importance score")
    tags: list[str] = Field(default_factory=list)
    need_video: bool = Field(
        default=False,
        description="是否需要制作短视频；LLM+规则判定，可人工覆盖 / "
        "flagged for the short-video app",
    )

    images: list[MediaAsset] = Field(default_factory=list)
    videos: list[MediaAsset] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list, description="全部来源链接 / all source links")

    def title(self, lang: Language) -> str:
        """按语言取标题，英文缺失时回退中文 / Title for a language, falling back to Chinese."""
        return (self.title_en or self.title_zh) if lang is Language.EN else self.title_zh

    def summary(self, lang: Language) -> str:
        """按语言取摘要，英文缺失时回退中文 / Summary for a language, falling back to Chinese."""
        return (self.summary_en or self.summary_zh) if lang is Language.EN else self.summary_zh

    def has_language(self, lang: Language) -> bool:
        """该条目是否已有对应语言的成稿 / Whether this entry already has copy in a language."""
        if lang is Language.ZH:
            return bool(self.title_zh and self.summary_zh)
        return bool(self.title_en and self.summary_en)


class DigestStats(_Base):
    """一期日报的统计信息 / Per-issue statistics."""

    raw_count: int = 0
    after_dedup_count: int = 0
    cluster_count: int = 0
    entry_count: int = 0
    source_count: int = 0
    llm_provider: str | None = None
    llm_model: str | None = None
    total_tokens: int = 0
    duration_ms: int = 0


class DailyDigest(_Base):
    """
    一期日报的完整结构化产物 / The complete structured artefact for one issue.

    这是三个发布应用（图文 / 视频 / 播客）的唯一输入。
    This is the only input consumed by the graphic / video / podcast apps.
    """

    date: Date
    entries: list[DigestEntry] = Field(default_factory=list)
    trend_note_zh: str | None = Field(
        default=None, description="当日主线与观点提炼 / daily trend note"
    )
    trend_note_en: str | None = None
    stats: DigestStats = Field(default_factory=DigestStats)
    generated_at: datetime = Field(default_factory=datetime.now)
    schema_version: int = Field(
        default=1, description="结构版本，用于旧产物兼容 / for backward compatibility"
    )

    def trend_note(self, lang: Language) -> str | None:
        """按语言取趋势段落 / Trend note for a language, falling back to Chinese."""
        if lang is Language.EN:
            return self.trend_note_en or self.trend_note_zh
        return self.trend_note_zh

    def video_entries(self) -> list[DigestEntry]:
        """需要制作短视频的条目 / Entries flagged for the short-video app."""
        return [e for e in self.entries if e.need_video]

    def entry_by_id(self, entry_id: str) -> DigestEntry | None:
        """按 id 取条目，供「重做指定输出」使用 / Look up an entry, used by the redo feature."""
        return next((e for e in self.entries if e.id == entry_id), None)


__all__ = [
    "AppKind",
    "Article",
    "Cluster",
    "DailyDigest",
    "DigestEntry",
    "DigestStats",
    "Language",
    "MediaAsset",
    "MediaKind",
    "NewsItem",
    "RawItem",
    "SourceKind",
]
