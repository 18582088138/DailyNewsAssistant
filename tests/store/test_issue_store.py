"""
test_issue_store.py —— 期次落盘与装配单元测试 / Issue assembly and persistence tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/store/test_issue_store.py -v

对应的人工验证 / Matching manual check:
    dna digest --dry-run          # 先确认选题（零费用）
    dna digest                    # 生成一期
    dna issue --list              # 列出已生成的期次
    dna issue                     # 看最新一期：条目、目录是否都定位到
    dna issue --refresh           # 重抓改名后修复 _references.md（零费用）

覆盖 / Covers:
    1. 期次目录结构：`_digest.json` + `_references.md`，`graphic/` `podcast/` **不预建**
    2. `_digest.json` 往返无损 —— 重做完全建立在「可重放」之上
    3. `_references.md` 列出每条的原文、多源报道、图片与视频**原始地址**
    4. 条目按 id 引用到 `../articles/...`，**不复制文件**
    5. **台账查不到的条目不静默跳过**：文件里写 ⚠️，`SavedIssue.unresolved` 里也有
    6. **台账记着目录但目录已被删**同样算未定位（写一个 404 的链接比不写更糟）
    7. 代表条目换过时，按 `refs` 里的其它来源链接兜底查
    8. `save_issue` 可重复执行且不调用 LLM（幂等、零费用）
    9. `refresh_references` 只重写来源文件，**不碰 `_digest.json`**
   10. `list_issues` 只认有 `_digest.json` 的目录，空目录不算一期
   11. `load_issue` 读不到或文件损坏时返回 None，不抛异常

为什么条目资产不复制进期次目录 / Why per-article assets are not copied:
    一篇文章可以进多期（跨日的后续报道）。复制会产生两份各自漂移的副本，
    重做了一份另一份还是旧的。按 id 引用只有一处真相。

预期 / Expected:
    耗时 < 1s；只用 tmp_path，无网络、无 LLM、零费用
"""

from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path

import pytest

from dna.core.config import Settings
from dna.core.models import (
    Article,
    DailyDigest,
    DigestEntry,
    MediaAsset,
    MediaKind,
    RawItem,
    SourceKind,
)
from dna.store.issue_store import (
    issue_paths,
    list_issues,
    load_issue,
    refresh_references,
    resolve_links,
    save_issue,
)
from dna.store.ledger import Ledger

ISSUE_DAY = date(2026, 9, 2)
URL_MAIN = "https://qbitai.com/2026/09/deepseek-v4-flash"
URL_OTHER = "https://jiqizhixin.com/articles/deepseek-v4-flash"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None, data_dir=tmp_path / "data", output_dir=tmp_path / "outputs"
    )


def seed_article(
    settings: Settings, *, url: str = URL_MAIN, title: str = "DeepSeek-V4-Flash 开源"
) -> str:
    """
    在台账里放一篇已落盘的文章 / Seed one persisted article in the ledger.

    目录**真的建出来**：`_lookup_store_dir` 会检查目录是否存在，
    只写台账而不建目录测不出「链接指向真实存在的目录」这条。
    The directory is really created, because the lookup checks for its existence.
    """
    ledger = Ledger(settings.db_file)
    article_id, _ = ledger.register(
        RawItem(source_id="qbitai", via=SourceKind.RSS, url=url, title=title)
    )

    store_dir = f"articles/20260902/{title}__{article_id[:8]}"
    (settings.output_path / store_dir).mkdir(parents=True, exist_ok=True)
    ledger.record_fetch(
        article_id,
        Article(url=url, title=title, text="正文" * 100, extraction_ok=True),
        store_dir=store_dir,
    )
    return article_id


def make_digest(*, entry_id: str, refs: list[str] | None = None) -> DailyDigest:
    """构造一份最小但完整的 Digest / Build a minimal but complete digest."""
    return DailyDigest(
        date=ISSUE_DAY,
        entries=[
            DigestEntry(
                id=entry_id,
                rank=1,
                cluster_id=entry_id,
                title_zh="DeepSeek-V4-Flash 开源",
                summary_zh="304B 总参数、激活 13B，多项基准超越预览版。",
                score=0.812,
                tags=["开源模型"],
                images=[
                    MediaAsset(
                        kind=MediaKind.IMAGE,
                        url="https://i.qbitai.com/2026/09/bench.png",
                        source_url=URL_MAIN,
                        credit="量子位",
                        caption="基准测试对比",
                    )
                ],
                videos=[
                    MediaAsset(
                        kind=MediaKind.VIDEO,
                        url="https://v.qbitai.com/demo.mp4",
                        source_url=URL_MAIN,
                    )
                ],
                refs=refs if refs is not None else [URL_MAIN],
            )
        ],
        trend_note_zh="本日主线是开源模型的成本下探。",
        generated_at=datetime(2026, 9, 2, 9, 30),
    )


# --- 目录结构 / layout --------------------------------------------------------


def test_writes_digest_and_references(settings: Settings) -> None:
    """期次目录里就这两个文件 / An issue directory carries exactly these two files."""
    article_id = seed_article(settings)
    saved = save_issue(make_digest(entry_id=article_id), settings=settings)

    assert saved.paths.root.name == "20260902-DailyNews"
    assert saved.paths.digest.exists()
    assert saved.paths.references.exists()


def test_does_not_precreate_empty_subdirs(settings: Settings) -> None:
    """
    `graphic/` 与 `podcast/` 不预建。

    空目录会被读成「已经生成过了」——P5/P7 还没做的东西不该在盘上留下痕迹。
    An empty directory reads as "already built", which would be a lie until P5/P7.
    """
    article_id = seed_article(settings)
    saved = save_issue(make_digest(entry_id=article_id), settings=settings)

    assert not saved.paths.graphic.exists()
    assert not saved.paths.podcast.exists()


def test_digest_round_trips(settings: Settings) -> None:
    """
    `_digest.json` 往返无损 —— 「重做不用重跑流水线」完全建立在这一条上。
    Loss-free round-tripping is what "redo without re-running the pipeline" rests on.
    """
    article_id = seed_article(settings)
    original = make_digest(entry_id=article_id)
    save_issue(original, settings=settings)

    loaded = load_issue(ISSUE_DAY, settings=settings)

    assert loaded is not None
    assert loaded.model_dump() == original.model_dump()


# --- 来源汇总 / the reference roll-up ------------------------------------------


def test_references_list_origin_and_media(settings: Settings) -> None:
    """
    每张图、每段视频的**原始地址**都要在来源汇总里查得到——这是发布时的合规依据。
    Every image and clip must be traceable here; this file is what attribution is
    checked against before publishing.
    """
    article_id = seed_article(settings)
    saved = save_issue(make_digest(entry_id=article_id), settings=settings)

    text = saved.paths.references.read_text(encoding="utf-8")

    assert URL_MAIN in text
    assert "https://i.qbitai.com/2026/09/bench.png" in text
    assert "https://v.qbitai.com/demo.mp4" in text
    assert "量子位" in text  # 署名
    assert "基准测试对比" in text  # 图注


def test_references_link_to_article_dir_without_copying(settings: Settings) -> None:
    """
    条目按 id 引用到 `../articles/...`，**期次目录里不出现任何副本**。
    Entries are referenced, not copied: no duplicate ever lands in the issue directory.
    """
    article_id = seed_article(settings)
    saved = save_issue(make_digest(entry_id=article_id), settings=settings)

    text = saved.paths.references.read_text(encoding="utf-8")

    assert f"../articles/20260902/DeepSeek-V4-Flash 开源__{article_id[:8]}/" in text
    assert not (saved.paths.root / "articles").exists()
    assert sorted(p.name for p in saved.paths.root.iterdir()) == [
        "_digest.json",
        "_references.md",
    ]


def test_multi_source_lists_every_origin(settings: Settings) -> None:
    """
    多源报道时全部出处都要列出，而不只是被选中那一家。
    With multi-source coverage the reader must see every origin.
    """
    article_id = seed_article(settings)
    digest = make_digest(entry_id=article_id, refs=[URL_MAIN, URL_OTHER])

    saved = save_issue(digest, settings=settings)
    text = saved.paths.references.read_text(encoding="utf-8")

    assert URL_MAIN in text
    assert URL_OTHER in text
    assert "另有 1 家报道" in text


# --- 定位不到条目目录 / unresolved entries -------------------------------------


def test_unknown_entry_is_reported_not_skipped(settings: Settings) -> None:
    """
    台账里没有这篇时**不能静默少写一行链接**：文件里要有 ⚠️，返回值里也要有。
    A missing article must not silently drop its link: it is flagged in both the file
    and the return value.
    """
    saved = save_issue(make_digest(entry_id="0" * 16), settings=settings)

    assert len(saved.unresolved) == 1
    assert "未能定位" in saved.paths.references.read_text(encoding="utf-8")
    assert "未能定位条目目录" in saved.summary()


def test_deleted_directory_counts_as_unresolved(settings: Settings) -> None:
    """
    台账记着目录但目录已被手工删掉，同样算未定位。

    写一个点开是 404 的链接比不写更糟：人会以为文件就在那里。
    A link that 404s is worse than no link, because it reads as "the files are there".
    """
    article_id = seed_article(settings)
    record = Ledger(settings.db_file).get(article_id)
    shutil.rmtree(settings.output_path / record.store_dir)

    saved = save_issue(make_digest(entry_id=article_id), settings=settings)

    assert len(saved.unresolved) == 1


def test_falls_back_to_other_refs(settings: Settings) -> None:
    """
    代表条目换过时，按 `refs` 里的其它来源链接兜底查。

    聚类会挑代表条目，挑中的那篇未必是台账里存着落盘目录的那篇。
    Clustering picks a representative that may not be the one on record.
    """
    other_id = seed_article(settings, url=URL_OTHER, title="机器之心报道")
    digest = make_digest(entry_id="f" * 16, refs=[URL_MAIN, URL_OTHER])

    links = resolve_links(digest, settings=settings)

    assert links[0].store_dir is not None
    assert other_id[:8] in links[0].store_dir


# --- 幂等与重建 / idempotence and rebuilding ------------------------------------


def test_save_is_idempotent(settings: Settings) -> None:
    """
    对同一份 digest 反复落盘结果一致 —— 落盘不调用 LLM，重跑不花钱。
    Re-persisting the same digest is stable; nothing here calls an LLM.
    """
    article_id = seed_article(settings)
    digest = make_digest(entry_id=article_id)

    first = save_issue(digest, settings=settings)
    text_1 = first.paths.references.read_text(encoding="utf-8")
    second = save_issue(digest, settings=settings)
    text_2 = second.paths.references.read_text(encoding="utf-8")

    assert text_1 == text_2
    assert first.paths.root == second.paths.root


def test_refresh_rewrites_references_only(settings: Settings) -> None:
    """
    `--refresh` 只重算引用，**不碰 `_digest.json`**。

    重抓会让标题变、slug 变、目录改名，期次里的链接因此指错；
    修这个不该有重跑流水线的风险。
    A re-fetch renames the directory and breaks the links; repairing them must not risk
    touching the digest.
    """
    article_id = seed_article(settings)
    saved = save_issue(make_digest(entry_id=article_id), settings=settings)

    saved.paths.references.write_text("被破坏的内容", encoding="utf-8")
    digest_before = saved.paths.digest.read_text(encoding="utf-8")

    result = refresh_references(ISSUE_DAY, settings=settings)

    assert result is not None
    assert URL_MAIN in saved.paths.references.read_text(encoding="utf-8")
    assert saved.paths.digest.read_text(encoding="utf-8") == digest_before


def test_refresh_missing_issue_returns_none(settings: Settings) -> None:
    """没有这一期时返回 None，由前端决定怎么提示 / Absent issue returns None."""
    assert refresh_references(date(2020, 1, 1), settings=settings) is None


# --- 列举与读回 / listing and loading -------------------------------------------


def test_list_issues_newest_first(settings: Settings) -> None:
    """按日期倒序列出 / Issues are listed newest first."""
    article_id = seed_article(settings)
    save_issue(make_digest(entry_id=article_id), settings=settings)

    older = make_digest(entry_id=article_id)
    older.date = date(2026, 8, 30)
    save_issue(older, settings=settings)

    assert list_issues(settings=settings) == [ISSUE_DAY, date(2026, 8, 30)]


def test_empty_dir_is_not_an_issue(settings: Settings) -> None:
    """
    空的期次目录不算一期 —— 只认有 `_digest.json` 的。
    A directory without a digest is not an issue.
    """
    (settings.output_path / "20260901-DailyNews").mkdir(parents=True)

    assert list_issues(settings=settings) == []


def test_load_corrupt_issue_returns_none(settings: Settings) -> None:
    """
    文件损坏时返回 None 而不是抛异常 —— 上层要能区分「没有」和「坏了」并自行提示。
    A corrupt digest returns None rather than raising.
    """
    paths = issue_paths(ISSUE_DAY, settings=settings)
    paths.root.mkdir(parents=True)
    paths.digest.write_text("{ 不是合法 JSON", encoding="utf-8")

    assert load_issue(ISSUE_DAY, settings=settings) is None
