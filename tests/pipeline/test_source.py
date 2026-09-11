"""
test_source.py —— 台账取数层单元测试 / Ledger-to-pipeline loading unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/pipeline/test_source.py -v

对应的人工验证 / Matching manual check:
    dna digest --dry-run                     # 看取到多少条候选
    dna digest --dry-run -a <id> -a <id>     # 人工指定几篇

覆盖 / Covers:
    1. 正文从 outputs/ 下的 meta.json 读回（台账只存长度，内容在落盘目录里）
    2. 媒体资产一并读回，且损坏的单项不影响其余
    3. **failed / pending 的记录被跳过**——它们没有可用内容
    4. **degraded 的记录被保留**——只有标题也是真实资讯
    5. 落盘目录被删时退化成「标题 + 链接」，不抛异常
    6. meta.json 损坏时同样退化，不抛异常
    7. article_ids 指定时忽略时间窗与来源过滤（人工挑选的就是想要的）
    8. source_ids 过滤生效
    9. 空台账返回空列表

为什么正文不存数据库 / Why the body is not in the database:
    台账的职责是索引与状态，不是内容仓库。几百篇全文塞进 SQLite 会让这张
    「总表」又大又慢，而它每天都要被查询与统计。
    The ledger indexes and tracks status; it is not a content store.

预期 / Expected:
    耗时 < 1s；只用 tmp_path 建库，无网络、无 LLM、零费用
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from dna.core.config import Settings
from dna.core.models import Article, MediaAsset, MediaKind, RawItem, SourceKind
from dna.pipeline.source import load_candidates
from dna.store.ledger import Ledger


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """指向临时目录的配置 / Settings pointing at a temp directory."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "outputs"))
    return Settings(data_dir=str(tmp_path / "data"), output_dir=str(tmp_path / "outputs"))


def seed(
    settings: Settings,
    *,
    url: str = "https://e.com/1",
    title: str = "一条足够长的新闻标题",
    body: str = "正文内容。" * 30,
    source: str = "qbitai",
    ok: bool = True,
    media: list[MediaAsset] | None = None,
    write_meta: bool = True,
) -> str:
    """
    往台账里放一条记录，并按需写出 meta.json / Seed one ledger row and its meta.json.
    """
    ledger = Ledger(settings.db_file)
    article_id, _ = ledger.register(
        RawItem(source_id=source, via=SourceKind.RSS, url=url, title=title)
    )

    article = Article(
        url=url,
        title=title,
        text=body if ok else "",
        media=media or [],
        extraction_ok=ok,
        published_at=datetime.now(),
    )

    store_dir = f"articles/20260902/{article_id[:8]}"
    if write_meta:
        directory = settings.output_path / store_dir
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "meta.json").write_text(
            article.model_dump_json(indent=2), encoding="utf-8"
        )

    ledger.record_fetch(article_id, article, store_dir=store_dir)
    return article_id


# --- 正常读取 / the happy path -------------------------------------------------


def test_body_is_read_back_from_meta_json(settings: Settings) -> None:
    """
    正文从 meta.json 读回。

    台账只存正文长度——把几百篇全文塞进 SQLite 会让这张每天都要查询和统计的
    「总表」又大又慢，而它的职责是索引与状态。
    """
    seed(settings, body="这是落盘的正文内容。" * 20)
    items = load_candidates(settings=settings)

    assert len(items) == 1
    assert "这是落盘的正文内容。" in items[0].text
    assert len(items[0].text) > 100


def test_media_is_read_back(settings: Settings) -> None:
    """配图与视频一并读回——后续做长图和视频要用素材。"""
    seed(
        settings,
        media=[
            MediaAsset(kind=MediaKind.IMAGE, url="https://e.com/i.jpg", source_url="https://e.com/1"),
            MediaAsset(kind=MediaKind.VIDEO, url="https://e.com/v.mp4", source_url="https://e.com/1"),
        ],
    )
    items = load_candidates(settings=settings)

    kinds = {m.kind for m in items[0].media}
    assert kinds == {MediaKind.IMAGE, MediaKind.VIDEO}


def test_corrupt_media_entry_does_not_lose_the_others(settings: Settings) -> None:
    """单个媒体项损坏时跳过它，其余照常——不能因为一张图丢掉整条内容。"""
    article_id = seed(
        settings,
        media=[
            MediaAsset(kind=MediaKind.IMAGE, url="https://e.com/i.jpg", source_url="https://e.com/1")
        ],
    )
    meta_path = settings.output_path / f"articles/20260902/{article_id[:8]}/meta.json"
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    data["media"].append({"kind": "not-a-real-kind"})
    meta_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    items = load_candidates(settings=settings)

    assert len(items[0].media) == 1


# --- 状态筛选 / status filtering -----------------------------------------------


def test_degraded_records_are_kept(settings: Settings) -> None:
    """
    只有标题的降级记录仍然要。

    抽取失败的文章是一条真实资讯，读者点链接就能看——为一个技术问题把它
    从日报里剔掉不划算。这是全项目一以贯之的取舍。
    """
    seed(settings, ok=False, body="", title="知乎那篇被反爬拦下的文章")
    items = load_candidates(settings=settings)

    assert len(items) == 1
    assert items[0].title == "知乎那篇被反爬拦下的文章"
    assert items[0].text == ""


def test_failed_records_are_skipped(settings: Settings) -> None:
    """抓取失败的记录没有任何可用内容，跳过。"""
    ledger = Ledger(settings.db_file)
    article_id, _ = ledger.register(
        RawItem(source_id="s1", via=SourceKind.RSS, url="https://e.com/x", title="失败的那条")
    )
    ledger.record_failure(article_id, "HTTP 403")

    assert load_candidates(settings=settings) == []


def test_pending_records_are_skipped(settings: Settings) -> None:
    """还没抓正文的记录跳过——它连状态都还没定。"""
    ledger = Ledger(settings.db_file)
    ledger.register(
        RawItem(source_id="s1", via=SourceKind.RSS, url="https://e.com/y", title="还没抓的那条")
    )

    assert load_candidates(settings=settings) == []


# --- 落盘缺失时的降级 / degrading when the directory is gone --------------------


def test_missing_directory_degrades_to_title_and_link(settings: Settings) -> None:
    """
    落盘目录被手工删掉时退回「标题 + 链接」，不抛异常。

    目录可能被清理过，但台账里的标题和链接仍是有效资讯。
    """
    seed(settings, write_meta=False, title="目录已被删掉的那篇文章")
    items = load_candidates(settings=settings)

    assert len(items) == 1
    assert items[0].title == "目录已被删掉的那篇文章"
    assert items[0].text == ""


def test_corrupt_meta_json_degrades_instead_of_raising(settings: Settings) -> None:
    """meta.json 损坏时同样降级——读不到内容是遗憾，不是故障。"""
    article_id = seed(settings, title="meta 损坏的那篇文章")
    meta_path = settings.output_path / f"articles/20260902/{article_id[:8]}/meta.json"
    meta_path.write_text("{ 这不是合法 JSON", encoding="utf-8")

    items = load_candidates(settings=settings)

    assert len(items) == 1
    assert items[0].text == ""


# --- 挑选 / selection ----------------------------------------------------------


def test_article_ids_bypass_the_time_window_and_source_filter(settings: Settings) -> None:
    """
    人工指定 id 时忽略时间窗与来源过滤。

    这是「在总表里勾选几篇做日报」的落点——人点名要的就是想要的，
    再拿时间和来源去筛会很违反直觉。
    """
    wanted = seed(settings, url="https://a.com/1", title="人工挑中的那篇文章", source="a")
    seed(settings, url="https://b.com/2", title="没被挑中的那篇文章", source="b")

    items = load_candidates(settings=settings, article_ids=[wanted], since_days=0, source_ids=["z"])

    assert len(items) == 1
    assert items[0].title == "人工挑中的那篇文章"


def test_source_filter_applies(settings: Settings) -> None:
    """按来源过滤生效。"""
    seed(settings, url="https://a.com/1", title="来自甲源的文章标题", source="a")
    seed(settings, url="https://b.com/2", title="来自乙源的文章标题", source="b")

    items = load_candidates(settings=settings, source_ids=["a"])

    assert [i.source_id for i in items] == ["a"]


def test_empty_ledger_returns_empty(settings: Settings) -> None:
    """空台账返回空列表，不崩。"""
    assert load_candidates(settings=settings) == []
