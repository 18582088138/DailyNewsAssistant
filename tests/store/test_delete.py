"""
test_delete.py —— 文章删除单元测试 / Article deletion unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/store/test_delete.py -v

对应的人工验证 / Matching manual check:
    dna delete <id>              # 先看列出的数字与目录路径，再确认
    dna list                     # 该文章已不在总表
    然后核对 outputs/articles/<日期>/ 下对应目录已消失

覆盖 / Covers:
    1. `plan_delete` 是纯查询：算完之后文件与台账都没动
    2. plan 里的篇数/产物数/文件数与实际删掉的对得上
    3. 删除同时清掉 productions 行——没有外键约束，得靠代码保证
    4. `remove_files=False` 只删台账行，磁盘文件留着
    5. `store_dir` 指到 outputs 之外时**拒绝删目录**，但台账行照删
    6. 空掉的日期目录被顺手清掉，非空的不动
    7. 不存在的 id 记进 missing_ids，不抛异常

为什么先算再删 / Why the plan comes first:
    删除是这套工具里唯一不可撤销的操作——重抓拿不回已经下线的图和视频。
    确认框上的数字必须来自真正要删的那批对象，而不是另算一遍。
    Deletion is the one irreversible operation here, so the numbers shown in the
    confirmation must come from the very objects about to be removed.

预期 / Expected:
    耗时 < 1s；只写 tmp_path，不联网、不调用 LLM
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from dna.core.config import Settings
from dna.core.models import Article, RawItem, SourceKind
from dna.produce.tasks import ProductionKind
from dna.store.delete import delete_articles, plan_delete, safe_article_dir
from dna.store.ledger import Ledger


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", output_dir=tmp_path / "outputs")


def seed(
    settings: Settings,
    *,
    name: str = "某篇文章",
    url: str = "https://e.com/1",
    date: str = "20260903",
    productions: int = 0,
) -> str:
    """落一篇文章到新布局下 / Seed one article in the current layout."""
    ledger = Ledger(settings.db_file)
    article_id, _ = ledger.register(
        RawItem(source_id="qbitai", via=SourceKind.RSS, url=url, title=name)
    )

    store_dir = f"articles/{date}/{name}__{article_id[:8]}"
    directory = settings.output_path / store_dir
    (directory / "images").mkdir(parents=True, exist_ok=True)
    (directory / "article.md").write_text(f"# {name}\n\n正文", encoding="utf-8")
    (directory / "meta.json").write_text("{}", encoding="utf-8")
    (directory / "images" / "01.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

    ledger.record_fetch(
        article_id,
        Article(url=url, title=name, text="正文", extraction_ok=True, published_at=datetime.now()),
        store_dir=store_dir,
    )
    for index in range(productions):
        ledger.record_production(
            article_id,
            kind=ProductionKind.SUMMARY,
            status="ok",
            output_path=f"{store_dir}/summary.zh.md",
            chars=90 + index,
        )
    return article_id


# --- 先算再删 / plan first ------------------------------------------------------


def test_plan_touches_nothing(settings: Settings) -> None:
    """`plan_delete` 只查不改：目录、正文、台账行全都还在。"""
    article_id = seed(settings, productions=2)
    directory = settings.output_path / Ledger(settings.db_file).get(article_id).store_dir

    plan = plan_delete([article_id], settings=settings)

    assert plan.has_work
    assert plan.article_ids == [article_id]
    assert plan.production_count == 2
    assert plan.file_count == 3  # article.md + meta.json + images/01.jpg
    assert directory.exists()
    assert Ledger(settings.db_file).get(article_id) is not None


def test_plan_numbers_match_what_gets_deleted(settings: Settings) -> None:
    """plan 报的篇数与产物数，就是实际删掉的数量。"""
    first = seed(settings, name="甲", url="https://e.com/a", productions=2)
    second = seed(settings, name="乙", url="https://e.com/b", productions=1)

    plan = plan_delete([first, second], settings=settings)
    done = delete_articles([first, second], settings=settings)

    assert (plan.production_count, len(plan.article_ids)) == (3, 2)
    assert (done.deleted_productions, done.deleted_articles) == (3, 2)


def test_missing_id_is_reported_not_raised(settings: Settings) -> None:
    """不存在的 id 不抛异常——批量删除时一个失效 id 不该让其余都删不掉。"""
    article_id = seed(settings)

    done = delete_articles([article_id, "ffffffffffffffff"], settings=settings)

    assert done.missing_ids == ["ffffffffffffffff"]
    assert done.deleted_articles == 1


# --- 真删 / actually deleting ---------------------------------------------------


def test_delete_removes_rows_and_directory(settings: Settings) -> None:
    """台账行、产物行、磁盘目录三者都清掉。"""
    article_id = seed(settings, productions=2)
    ledger = Ledger(settings.db_file)
    directory = settings.output_path / ledger.get(article_id).store_dir

    done = delete_articles([article_id], settings=settings)

    assert done.deleted_articles == 1
    assert done.deleted_productions == 2
    assert not directory.exists()
    assert Ledger(settings.db_file).get(article_id) is None
    assert Ledger(settings.db_file).count_productions(article_id) == 0


def test_keep_files_only_clears_the_ledger(settings: Settings) -> None:
    """`remove_files=False`：素材还想留着，只是不想在总表里看见它。"""
    article_id = seed(settings)
    directory = settings.output_path / Ledger(settings.db_file).get(article_id).store_dir

    done = delete_articles([article_id], remove_files=False, settings=settings)

    assert done.deleted_articles == 1
    assert done.deleted_dirs == 0
    assert (directory / "article.md").exists()
    assert Ledger(settings.db_file).get(article_id) is None


def test_empty_date_directory_is_pruned(settings: Settings) -> None:
    """删空了的日期目录顺手清掉；同一天还有别的文章时不动。"""
    alone = seed(settings, name="独苗", url="https://e.com/x", date="20260901")
    kept = seed(settings, name="留下", url="https://e.com/y", date="20260902")
    seed(settings, name="同天", url="https://e.com/z", date="20260902")

    delete_articles([alone, kept], settings=settings)

    assert not (settings.output_path / "articles/20260901").exists()
    assert (settings.output_path / "articles/20260902").exists()


# --- 路径护栏 / path guard ------------------------------------------------------


@pytest.mark.parametrize(
    "store_dir",
    [
        "../../../Windows",  # 上溯逃出 outputs
        "",  # 空值：resolve 之后正好等于 outputs 本身
        ".",
        "C:/Windows",  # 绝对路径覆盖
    ],
)
def test_unsafe_store_dir_is_refused(settings: Settings, store_dir: str) -> None:
    """
    `store_dir` 是库里的一个字符串，一个手工改坏的值不该变成 `rmtree("C:\\")`。

    A hand-corrupted string in the database must never turn into a recursive delete of
    somewhere else on the disk.
    """
    settings.output_path.mkdir(parents=True, exist_ok=True)
    assert safe_article_dir(store_dir, settings) is None


def test_escaped_directory_is_left_alone_but_row_is_deleted(settings: Settings) -> None:
    """
    目录不安全时：拒删目录、记进 unsafe_dirs，台账行照删。

    行留着只会让总表里永远挂着一篇打不开的文章；目录不碰，人可以手动处理。
    """
    article_id = seed(settings)
    outsider = settings.output_path.parent / "别处"
    outsider.mkdir(parents=True, exist_ok=True)
    (outsider / "keep.txt").write_text("x", encoding="utf-8")
    Ledger(settings.db_file).set_store_dir(article_id, "../别处")

    plan = plan_delete([article_id], settings=settings)
    done = delete_articles([article_id], settings=settings)

    assert plan.unsafe_dirs and plan.unsafe_dirs[0][0] == article_id
    assert done.deleted_dirs == 0
    assert (outsider / "keep.txt").exists()
    assert Ledger(settings.db_file).get(article_id) is None
