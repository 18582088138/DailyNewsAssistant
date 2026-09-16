"""
pytest 公共夹具 / Shared pytest fixtures.

复测命令 / Re-run (全部离线测试 / all offline tests):
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest -v

说明 / Notes:
    默认只跑离线快测（pyproject 里 addopts 排除了 live 与 slow）。
    跑全量：  python -m pytest -m "" -v
    只跑联网： python -m pytest -m live -v
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from dna.core.config import Settings
from dna.core.models import (
    Cluster,
    DailyDigest,
    DigestEntry,
    MediaAsset,
    MediaKind,
    NewsItem,
    SourceKind,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """
    指向临时目录的配置对象，避免测试污染真实 outputs/ 与 data/。
    A Settings instance pointing at a temp directory so tests never touch the real
    outputs/ or data/ folders.
    """
    return Settings(
        _env_file=None,  # 不读真实 .env，保证测试结果与本机配置无关
        llm_provider="deepseek",
        deepseek_api_key="sk-test-key-000000",
        output_dir=tmp_path / "outputs",
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "dna.db",
        # TTS 预处理默认关掉：它是**一次真的 LLM 调用**，开着的话每个音频用例都
        # 会去调注入的假 provider，把「音频不重新计费」这类断言搅浑。
        # 预处理本身在 tests/tts/test_tts.py 里单独测。
        # Off by default: it is a real LLM call and would muddy the audio assertions.
        tts_preprocess=False,
        inbox_enabled=False,
    )


@pytest.fixture
def news_item() -> NewsItem:
    """一条最小可用的新闻条目 / A minimal valid news item."""
    return NewsItem(
        id="abc123",
        source_id="jiqizhixin",
        via=SourceKind.RSS,
        url="https://example.com/a?utm_source=x",
        canonical_url="https://example.com/a",
        title="某公司发布新一代多模态大模型",
        text="正文内容。",
        published_at=datetime(2026, 9, 1, 9, 0, 0),
        media=[
            MediaAsset(
                kind=MediaKind.IMAGE,
                url="https://example.com/img.jpg",
                source_url="https://example.com/a",
            )
        ],
    )


@pytest.fixture
def cluster(news_item: NewsItem) -> Cluster:
    """一个包含两个来源的聚类 / A cluster holding two sources of the same event."""
    second = news_item.model_copy(
        update={
            "id": "def456",
            "source_id": "qbitai",
            "url": "https://other.com/b",
            "canonical_url": "https://other.com/b",
        }
    )
    return Cluster(id="c1", members=[news_item, second], canonical_url=news_item.canonical_url)


@pytest.fixture
def digest_entry() -> DigestEntry:
    """一条成稿单元（仅中文）/ A digest entry with Chinese copy only."""
    return DigestEntry(
        id="e1",
        rank=1,
        cluster_id="c1",
        title_zh="某公司发布新一代多模态大模型",
        summary_zh="该模型支持图文音三模态输入。官方称推理成本下降四成。",
        score=8.5,
        tags=["ai", "model"],
        need_video=True,
        refs=["https://example.com/a", "https://other.com/b"],
    )


@pytest.fixture
def daily_digest(digest_entry: DigestEntry) -> DailyDigest:
    """一期最小可用日报 / A minimal valid daily digest."""
    return DailyDigest(
        date=date(2026, 9, 1),
        entries=[digest_entry],
        trend_note_zh="今日主线是多模态模型的成本下探。",
    )


@pytest.fixture
def patch_actions_settings(monkeypatch):
    """
    把 `actions` 包各子模块里的 `get_settings` 一起替掉。

    **这个夹具存在的唯一理由是一个容易误判的坑。** `frontends/nicegui_app/actions`
    是一个包（facade + 8 个子模块），每个子模块都写 `from dna.core.config import
    get_settings` —— 于是每个模块里都有一份**独立的绑定**。
    只 `monkeypatch.setattr(actions, "get_settings", …)` 打的是 facade 上那一份，
    子模块里的原函数照旧被调用，测试会去读真实的 `.env` 与真实的 `data/`。
    而报错长得像「测试写错了」，不像「打的位置不对」。

    Each submodule holds its own binding, so patching the facade alone has no effect.
    """

    def apply(settings) -> None:
        import sys

        import frontends.nicegui_app.actions  # noqa: F401  确保子模块都已导入

        prefix = "frontends.nicegui_app.actions"
        for name, module in list(sys.modules.items()):
            if name.startswith(prefix) and hasattr(module, "get_settings"):
                monkeypatch.setattr(module, "get_settings", lambda: settings)

    return apply
