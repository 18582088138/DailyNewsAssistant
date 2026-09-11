"""
test_models.py —— 核心数据模型单元测试 / Core data model unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/core/test_models.py -v

覆盖 / Covers:
    1. NewsItem 校验：标题为空必须报错；未知字段必须报错（extra="forbid"）
    2. MediaAsset 必须携带 source_url，保证 references 能标注出处
    3. Cluster.canonical / Cluster.refs：代表条目正确、来源链接去重保序
    4. DigestEntry 双语回退：缺英文时 title(EN)/summary(EN) 回退中文
    5. DigestEntry.has_language()：判断某语言是否已成稿（补译功能依赖它）
    6. DailyDigest.video_entries()：只返回 need_video 的条目
    7. DailyDigest.entry_by_id()：命中与未命中
    8. **DailyDigest 的 JSON 往返无损**——_digest.json 是可重放事实源，
       重做功能完全依赖这一点，因此必须逐字段比对

预期 / Expected:
    耗时 < 2s；不联网、不落盘
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from dna.core.models import (
    Cluster,
    DailyDigest,
    DigestEntry,
    Language,
    MediaAsset,
    MediaKind,
    NewsItem,
    SourceKind,
)


# --- 校验 / validation -------------------------------------------------------


def test_news_item_rejects_blank_title() -> None:
    """空标题必须被拒绝 / A blank title must be rejected."""
    with pytest.raises(ValidationError, match="title must not be blank"):
        NewsItem(
            id="x",
            source_id="s",
            via=SourceKind.RSS,
            url="https://a.com",
            canonical_url="https://a.com",
            title="   ",
        )


def test_news_item_rejects_unknown_field() -> None:
    """未知字段必须报错，避免拼写错误静默生效 / Unknown fields must raise."""
    with pytest.raises(ValidationError):
        NewsItem(
            id="x",
            source_id="s",
            via=SourceKind.RSS,
            url="https://a.com",
            canonical_url="https://a.com",
            title="标题",
            typo_field="oops",  # type: ignore[call-arg]
        )


def test_media_asset_requires_source_url() -> None:
    """媒体资产必须带来源，否则 references 无法标注出处 / source_url is mandatory."""
    with pytest.raises(ValidationError):
        MediaAsset(kind=MediaKind.IMAGE, url="https://a.com/x.jpg")  # type: ignore[call-arg]


# --- 聚类 / clustering -------------------------------------------------------


def test_cluster_canonical_is_first_member(cluster: Cluster, news_item: NewsItem) -> None:
    """代表条目取第一个成员 / The canonical item is the first member."""
    assert cluster.canonical.id == news_item.id


def test_cluster_refs_dedup_and_keep_order(news_item: NewsItem) -> None:
    """来源链接去重且保序 / Reference links are de-duplicated, order preserved."""
    dup = news_item.model_copy(update={"id": "dup"})
    other = news_item.model_copy(update={"id": "o", "url": "https://other.com/b"})
    c = Cluster(id="c", members=[news_item, dup, other], canonical_url=news_item.canonical_url)
    assert c.refs == ["https://example.com/a?utm_source=x", "https://other.com/b"]


def test_cluster_requires_at_least_one_member() -> None:
    """空聚类无意义，必须报错 / An empty cluster is meaningless and must raise."""
    with pytest.raises(ValidationError):
        Cluster(id="c", members=[], canonical_url="https://a.com")


# --- 双语回退 / bilingual fallback -------------------------------------------


def test_entry_falls_back_to_chinese(digest_entry: DigestEntry) -> None:
    """缺英文时回退中文，保证渲染不会拿到 None / Falls back to Chinese when English is absent."""
    assert digest_entry.title(Language.EN) == digest_entry.title_zh
    assert digest_entry.summary(Language.EN) == digest_entry.summary_zh


def test_entry_uses_english_when_present(digest_entry: DigestEntry) -> None:
    """有英文时使用英文 / Uses English when available."""
    e = digest_entry.model_copy(update={"title_en": "Model released", "summary_en": "It is fast."})
    assert e.title(Language.EN) == "Model released"
    assert e.summary(Language.EN) == "It is fast."
    assert e.title(Language.ZH) == e.title_zh


def test_has_language(digest_entry: DigestEntry) -> None:
    """
    has_language 决定「是否需要补译」/ has_language drives the back-fill decision.
    """
    assert digest_entry.has_language(Language.ZH) is True
    assert digest_entry.has_language(Language.EN) is False

    bilingual = digest_entry.model_copy(update={"title_en": "T", "summary_en": "S"})
    assert bilingual.has_language(Language.EN) is True


# --- 日报聚合 / digest aggregation -------------------------------------------


def test_video_entries_filter(daily_digest: DailyDigest, digest_entry: DigestEntry) -> None:
    """只返回标记了 need_video 的条目 / Only entries flagged for video are returned."""
    plain = digest_entry.model_copy(update={"id": "e2", "rank": 2, "need_video": False})
    d = daily_digest.model_copy(update={"entries": [*daily_digest.entries, plain]})
    assert [e.id for e in d.video_entries()] == ["e1"]


def test_entry_by_id(daily_digest: DailyDigest) -> None:
    """按 id 查条目，供重做功能定位 / Entry lookup, used by the redo feature."""
    assert daily_digest.entry_by_id("e1") is not None
    assert daily_digest.entry_by_id("missing") is None


def test_digest_json_roundtrip_is_lossless(daily_digest: DailyDigest) -> None:
    """
    _digest.json 往返必须无损 —— 重做功能完全建立在「可重放」之上。
    The digest must survive a JSON round-trip untouched: the whole redo feature
    rests on the digest being replayable.
    """
    payload = daily_digest.model_dump_json()
    restored = DailyDigest.model_validate_json(payload)

    assert restored == daily_digest
    assert restored.date == date(2026, 9, 1)
    assert restored.entries[0].need_video is True
    assert restored.entries[0].refs == daily_digest.entries[0].refs
    assert restored.schema_version == 1
