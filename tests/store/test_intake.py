"""
test_intake.py —— 采集入库参数单元测试 / Intake parameter unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/store/test_intake.py -v

对应的人工验证 / Matching manual check:
    dna fetch --days 3 -n 3 --dry-run    # 天数过滤生效，且不写任何文件
    dna gui                              # 「从订阅导入」里选「最近 3 天」

覆盖 / Covers:
    1. `max_age_days` 覆盖每个源在 sources.yaml 里的设置，且**只作用于这一次**
    2. 不传 `max_age_days` 时，源自己的设置照旧生效
    3. **没有发布时间的条目不受天数限制**，照常入库
    4. `media_warnings` 从 `SavedArticle.skipped_videos` 填上来，且不算失败
    5. `on_progress` 在每篇**开抓之前**触发，序号 1..N，报的是当前那一篇
    6. 进度的分母是过滤之后的数量
    7. 不传 `on_progress` 时行为不变

为什么覆盖而不是改配置 / Why override instead of editing the config:
    `sources.yaml` 里的 `max_age_days` 是长期偏好；界面上选的天数是
    「这一次我只想要最近三天」的临时意图。两者混在一起，用户会发现自己
    在界面上随手选的一个数字永久改了配置。
    The YAML value is a standing preference; the number picked in the UI is a one-off
    intent. Conflating them means a casual UI choice silently rewrites the config.

预期 / Expected:
    耗时 < 1s；采集被 monkeypatch 拦截，`extract=False`，不联网、不调用 LLM
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from dna.core.config import Profile, Settings, SourceConfig, SourceFilter
from dna.core.models import RawItem, SourceKind
from dna.sources.registry import CollectResult
from dna.store import intake as intake_module
from dna.store.intake import intake_sources
from dna.store.ledger import Ledger

SOURCE_ID = "qbitai"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", output_dir=tmp_path / "outputs")


def _item(title: str, *, age_days: int | None) -> RawItem:
    """造一条采集结果 / Build one collected item."""
    published = (
        None if age_days is None else datetime.now() - timedelta(days=age_days)
    )
    return RawItem(
        source_id=SOURCE_ID,
        via=SourceKind.RSS,
        url=f"https://e.com/{title}",
        title=title,
        published_at=published,
    )


@pytest.fixture
def stub_collection(monkeypatch):
    """
    拦掉采集与源配置 / Stub out collection and source configuration.

    返回一个装载器：给它一批条目和源级 `max_age_days`，它接好 monkeypatch。
    """

    def install(items: list[RawItem], *, source_max_age_days: int | None = None) -> None:
        config = SourceConfig(
            id=SOURCE_ID,
            name="量子位",
            url="https://e.com/feed",
            filters=SourceFilter(max_age_days=source_max_age_days),
        )
        monkeypatch.setattr(intake_module, "_select_configs", lambda _ids: [config])
        monkeypatch.setattr(intake_module, "build_adapters", lambda *a, **k: [])
        monkeypatch.setattr(
            intake_module,
            "collect",
            lambda *a, **k: CollectResult(items=items, per_source={SOURCE_ID: len(items)}),
        )

    return install


def _titles(settings: Settings) -> set[str]:
    return {r.title for r in Ledger(settings.db_file).list(limit=100)}


# --- 天数覆盖 / the day-limit override -----------------------------------------


def test_max_age_days_overrides_the_source_setting(settings: Settings, stub_collection) -> None:
    """源里写的是 30 天，这一次只要 2 天——参数说了算。"""
    stub_collection(
        [_item("新的", age_days=1), _item("旧的", age_days=10)], source_max_age_days=30
    )

    result = intake_sources(settings=settings, profile=Profile(), extract=False, max_age_days=2)

    assert result.filtered_out == 1
    assert _titles(settings) == {"新的"}


def test_source_setting_applies_when_no_override(settings: Settings, stub_collection) -> None:
    """不传参数时，源自己的设置照旧生效——覆盖是临时的，不改配置。"""
    stub_collection(
        [_item("新的", age_days=1), _item("旧的", age_days=10)], source_max_age_days=3
    )

    result = intake_sources(settings=settings, profile=Profile(), extract=False)

    assert result.filtered_out == 1
    assert _titles(settings) == {"新的"}


def test_items_without_a_published_date_are_kept(settings: Settings, stub_collection) -> None:
    """
    没有发布时间的条目不受天数限制。

    大量 feed 不给 pubDate，按「未知即旧」处理会整源丢空——这与打分里
    「缺 published_at 给 0.5 分」是同一条理由。
    """
    stub_collection([_item("没日期", age_days=None), _item("很旧", age_days=99)])

    result = intake_sources(settings=settings, profile=Profile(), extract=False, max_age_days=1)

    assert _titles(settings) == {"没日期"}
    assert result.filtered_out == 1


# --- 媒体告警 / media warnings -------------------------------------------------


def test_media_warnings_are_collected_but_not_failures(
    settings: Settings, stub_collection, monkeypatch
) -> None:
    """
    视频抓不下来只提醒：正文已经入库，少一个视频不影响这篇文章可用。

    A failed video is a warning, not a failure: the body is already stored and the
    article remains publishable.
    """
    stub_collection([_item("带视频的", age_days=1)])

    from dna.core.models import Article

    def fake_fetch(url, **kwargs):
        return Article(url=url, title="带视频的", text="正文" * 50, extraction_ok=True)

    def fake_save(*args, **kwargs):
        from dna.store.article_store import SavedArticle

        directory = settings.output_path / "articles/20260910/带视频的__abcd1234"
        directory.mkdir(parents=True, exist_ok=True)
        return SavedArticle(
            directory=directory,
            article_path=directory / "article.md",
            meta_path=directory / "meta.json",
            references_path=directory / "references.md",
            skipped_videos=[("https://v.qq.com/x/1", "地域限制")],
        )

    monkeypatch.setattr(intake_module, "fetch_article", fake_fetch)
    monkeypatch.setattr(intake_module, "save_article", fake_save)

    result = intake_sources(settings=settings, profile=Profile(), extract=True)

    assert result.fetched_ok == 1
    assert len(result.media_warnings) == 1
    assert "地域限制" in result.media_warnings[0][1]
    assert "媒体未下载 1" in result.summary()


# --- 进度回调 / the progress callback ------------------------------------------


def test_on_progress_reports_the_item_being_processed(
    settings: Settings, stub_collection
) -> None:
    """
    每篇开抓**之前**报一次，报的是正在处理的那一篇。

    报「刚做完的那一篇」的话，最后一篇抓得最久的时候进度停在倒数第二个标题上，
    看起来就是卡在了一篇已经做完的文章上。
    """
    stub_collection([_item(f"第{i}篇", age_days=1) for i in range(1, 4)])

    seen: list[tuple[int, int, str]] = []
    intake_sources(
        settings=settings,
        profile=Profile(),
        extract=False,
        on_progress=lambda done, total, title: seen.append((done, total, title)),
    )

    assert seen == [(1, 3, "第1篇"), (2, 3, "第2篇"), (3, 3, "第3篇")]


def test_progress_total_counts_only_what_survived_filtering(
    settings: Settings, stub_collection
) -> None:
    """总数是过滤之后的数量——分母里混进被丢掉的条目，进度会永远走不到头。"""
    stub_collection([_item("新的", age_days=1), _item("旧的", age_days=10)])

    seen: list[tuple[int, int, str]] = []
    intake_sources(
        settings=settings,
        profile=Profile(),
        extract=False,
        max_age_days=2,
        on_progress=lambda *args: seen.append(args),
    )

    assert seen == [(1, 1, "新的")]


def test_intake_works_without_a_progress_callback(
    settings: Settings, stub_collection
) -> None:
    """不传回调时行为一字不变（CLI 的 --dry-run、测试、脚本都不会传）。"""
    stub_collection([_item("新的", age_days=1)])

    result = intake_sources(settings=settings, profile=Profile(), extract=False)

    assert result.collected == 1
    assert _titles(settings) == {"新的"}
