"""
test_migrate_layout.py —— 落盘目录迁移单元测试 / Storage layout migration tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/store/test_migrate_layout.py -v

对应的人工验证 / Matching manual check:
    dna migrate-layout --dry-run     # 先看会移动什么
    dna migrate-layout
    dna show <id>                    # 落盘位置应指向 outputs/articles/

覆盖 / Covers:
    1. `--dry-run` **不动任何文件、不改数据库**
    2. 迁移把目录从 data/ 移到 outputs/，并改写台账 store_dir
    3. **重跑幂等**：已经在新位置的不会被再搬一次
    4. 中途失败时数据库保持自洽（先移文件再改库）
    5. 两边都存在时**不猜哪个是新的**，列为冲突交给人处理
    6. **没有台账引用的孤儿目录只报告、不删除**——盘上的东西是用户的
    7. 搬空后的空目录被清理，但有内容的目录绝不递归删除
    8. 目录内容完整搬过去（正文、图片、sidecar 一个不少）

为什么先移文件再改库 / Why files move before the database:
    反过来的话，改完 DB 而文件没移成，台账里每一行都指向不存在的目录，
    且没有任何线索能找回去。按这个顺序，中途失败时 DB 还是旧的，重跑即可继续。
    The other order would leave every ledger row pointing at a directory that does not
    exist, with nothing to trace it back.

预期 / Expected:
    耗时 < 1s；只用 tmp_path，无网络、无 LLM、零费用
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from dna.core.config import Settings
from dna.core.models import Article, RawItem, SourceKind
from dna.store.ledger import Ledger
from dna.store.migrate_layout import migrate, plan_migration


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None, data_dir=tmp_path / "data", output_dir=tmp_path / "outputs"
    )


def seed(settings: Settings, *, name: str = "某篇文章", url: str = "https://e.com/1") -> str:
    """
    在**旧位置**放一篇文章 / Seed one article in the old location.

    模拟迁移之前的状态：目录在 data/articles 下，台账 store_dir 也指向那里。
    """
    ledger = Ledger(settings.db_file)
    article_id, _ = ledger.register(
        RawItem(source_id="qbitai", via=SourceKind.RSS, url=url, title=name)
    )

    store_dir = f"articles/20260903/{name}__{article_id[:8]}"
    old = settings.data_path / store_dir
    (old / "images").mkdir(parents=True, exist_ok=True)
    (old / "article.md").write_text(f"# {name}\n\n正文内容", encoding="utf-8")
    (old / "meta.json").write_text("{}", encoding="utf-8")
    (old / "images" / "01_host.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    (old / "images" / "01_host.jpg.json").write_text('{"source_url": "x"}', encoding="utf-8")

    ledger.record_fetch(
        article_id,
        Article(url=url, title=name, text="正文", extraction_ok=True, published_at=datetime.now()),
        store_dir=store_dir,
    )
    return article_id


# --- dry-run ------------------------------------------------------------------


def test_dry_run_touches_nothing(settings: Settings) -> None:
    """
    `--dry-run` 不动文件也不改库。

    迁移是不可逆操作（文件真的被移走了），先看清楚再执行是最基本的要求。
    """
    article_id = seed(settings)
    old_dir = settings.data_path / "articles/20260903"

    plan = plan_migration(settings)

    assert len(plan.moves) == 1
    assert old_dir.exists(), "dry-run 不该移动文件"
    assert Ledger(settings.db_file).get(article_id).store_dir.startswith("articles/")
    assert not (settings.output_path / "articles").exists()


# --- 正常迁移 / the happy path -------------------------------------------------


def test_migrate_moves_files_and_rewrites_the_ledger(settings: Settings) -> None:
    """迁移后文件在新位置，台账指向新位置。"""
    article_id = seed(settings)

    plan = migrate(settings)

    assert len(plan.moves) == 1
    assert plan.ledger_updates >= 1

    record = Ledger(settings.db_file).get(article_id)
    new_dir = settings.output_path / record.store_dir
    assert new_dir.exists()
    assert (new_dir / "article.md").exists()


def test_directory_contents_survive_intact(settings: Settings) -> None:
    """
    目录内容一个不少地搬过去。

    图片旁边的 .json sidecar 尤其重要——它是图片出处的唯一记录，
    丢了就无法追溯，产物也就不能发布。
    """
    article_id = seed(settings)
    migrate(settings)

    record = Ledger(settings.db_file).get(article_id)
    new_dir = settings.output_path / record.store_dir

    assert (new_dir / "article.md").read_text(encoding="utf-8").startswith("# 某篇文章")
    assert (new_dir / "meta.json").exists()
    assert (new_dir / "images" / "01_host.jpg").exists()
    assert (new_dir / "images" / "01_host.jpg.json").exists()


def test_migration_is_idempotent(settings: Settings) -> None:
    """
    重跑不会再搬一次。

    中途失败后重跑是标准操作，第二次必须安全。
    """
    seed(settings)
    migrate(settings)

    second = migrate(settings)

    assert second.moves == []
    assert len(second.already_done) == 1


def test_multiple_articles_all_move(settings: Settings) -> None:
    """多篇文章一起迁移。"""
    ids = [
        seed(settings, name=f"第{i}篇", url=f"https://e.com/{i}") for i in range(1, 4)
    ]

    migrate(settings)

    ledger = Ledger(settings.db_file)
    for article_id in ids:
        record = ledger.get(article_id)
        assert (settings.output_path / record.store_dir / "article.md").exists()


# --- 冲突与孤儿 / conflicts and orphans ----------------------------------------


def test_conflict_is_reported_not_guessed(settings: Settings) -> None:
    """
    两边都存在时不猜哪个是新的。

    自动覆盖可能毁掉用户手工整理过的内容；自动跳过可能让旧版本一直留着。
    两种猜法都会错，交给人判断才是对的。
    """
    article_id = seed(settings)
    record = Ledger(settings.db_file).get(article_id)

    # 在新位置也造一份
    target = settings.output_path / record.store_dir
    target.mkdir(parents=True, exist_ok=True)
    (target / "article.md").write_text("新位置已有的内容", encoding="utf-8")

    plan = plan_migration(settings)

    assert plan.moves == []
    assert len(plan.conflicts) == 1
    assert (settings.data_path / record.store_dir).exists(), "冲突时不该移动"


def test_orphan_directories_are_reported_not_deleted(settings: Settings) -> None:
    """
    没有台账引用的目录**只报告、不删除**。

    它们多半是历史遗留的重名副本，但「没人引用」不等于「可以删」——
    盘上的东西是用户的，该由用户决定。
    """
    seed(settings)
    orphan = settings.data_path / "articles/20260903/无人引用的目录__deadbeef"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "article.md").write_text("孤儿内容", encoding="utf-8")

    plan = migrate(settings)

    assert len(plan.orphans) == 1
    assert plan.orphans[0].name == "无人引用的目录__deadbeef"
    assert orphan.exists(), "孤儿目录不该被删除"
    assert (orphan / "article.md").exists()


def test_empty_directories_are_pruned_but_non_empty_are_kept(settings: Settings) -> None:
    """
    搬空后的空目录清掉，有内容的绝不递归删除。

    只删空目录是安全的；递归删内容一旦判断错就是不可逆的数据丢失。
    """
    seed(settings)
    orphan = settings.data_path / "articles/20260903/留着的目录__cafe1234"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "keep.md").write_text("要保留", encoding="utf-8")

    migrate(settings)

    assert orphan.exists()
    assert (orphan / "keep.md").exists()


def test_orphan_only_migration_leaves_the_tree_alone(settings: Settings) -> None:
    """没有任何可搬的东西时，旧目录树原样保留。"""
    orphan = settings.data_path / "articles/20260903/只有孤儿__11111111"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "article.md").write_text("内容", encoding="utf-8")

    plan = migrate(settings)

    assert plan.moves == []
    assert orphan.exists()


# --- 边界 / edges --------------------------------------------------------------


def test_missing_source_directory_is_skipped(settings: Settings) -> None:
    """
    台账有记录但目录不存在时跳过，不报错。

    目录可能被手工删过，而台账行仍然有效（标题和链接还在）。
    """
    article_id = seed(settings)
    record = Ledger(settings.db_file).get(article_id)

    import shutil

    shutil.rmtree(settings.data_path / record.store_dir)

    plan = migrate(settings)

    assert plan.moves == []
    assert len(plan.skipped_missing) == 1


def test_empty_ledger_is_a_no_op(settings: Settings) -> None:
    """空台账时什么都不做，不崩。"""
    plan = migrate(settings)

    assert plan.moves == []
    assert not plan.has_work
    assert "待迁移 0 个目录" in plan.summary()
