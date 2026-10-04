"""
test_custom_article.py —— 人工创作文章单元测试 / Hand-authored article unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/store/test_custom_article.py -v

对应的人工验证 / Matching manual check:
    dna gui  → 右上角「新建」→ 表格里多出一行，来源显示 custom_article
    双击标题下的来源名 → 打开该文章的产物目录

覆盖 / Covers:
    1. 连建两篇得到**两个不同的 id** —— 空 URL 会让它们撞成同一行
    2. 占位标题**足够长**，不会被 `pipeline.clean` 当成空标题丢掉
    3. 落盘：目录、article.md、meta.json 都在，且 `read_body` 能**精确**读回正文
    4. 来源是 `custom_article`，状态是 ok，store_dir 已写进台账
    5. 改标题 / 改正文：台账、meta.json、article.md **三处同步**
    6. 素材文件夹：人工创作的文章按需创建并返回；抓取来的文章空目录不返回

为什么这几条值得单独守着 / Why these are worth guarding:
    id 与标题这两条错了都**不会报错**：id 撞车表现为「新建了但表格里还是一条」，
    标题太短表现为 `produce` 报「读不到这篇文章的正文」。
    两种症状都指不到真正的原因，所以用测试钉住。

预期 / Expected:
    耗时 < 2s；只写 tmp_path，不联网、不调 LLM
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from dna.core.config import Settings
from dna.core.models import Article, RawItem, SourceKind
from dna.core.urls import CUSTOM_SOURCE_LABEL, is_custom_url
from dna.pipeline.clean import MIN_TITLE_CHARS
from dna.store.article_render import read_body, read_title
from dna.store.custom_article import (
    CUSTOM_SOURCE_ID,
    PLACEHOLDER_BODY,
    PLACEHOLDER_TITLE,
    create_custom_article,
    media_folders,
    save_body,
    save_title,
)
from dna.store.ledger import Ledger

NOW = datetime(2026, 10, 4, 9, 0, 0)


def _ledger(settings: Settings) -> Ledger:
    return Ledger(settings.db_file)


def _directory(settings: Settings, store_dir: str) -> Path:
    return settings.output_path / store_dir


# ---------------------------------------------------------------------------
# 1. 身份 / identity
# ---------------------------------------------------------------------------


def test_two_custom_articles_get_distinct_ids(settings: Settings) -> None:
    """
    连点两次「新建」必须得到两篇，而不是第二篇覆盖第一篇。

    `url_hash("")` 是常量，所以只要 id 是从空 URL 推出来的，第二篇就会命中
    `register()` 的「已存在」分支，安静地返回第一篇的 id。
    """
    first = create_custom_article(settings=settings, now=NOW)
    second = create_custom_article(settings=settings, now=NOW)

    assert first.id != second.id, "空 URL 推 id 会让新建的文章互相撞车"
    assert len(_ledger(settings).list()) == 2


def test_source_and_status_are_produce_ready(settings: Settings) -> None:
    """
    来源是 `custom_article`，且状态与落盘目录都已就绪。

    `store_dir` 为空的台账行是废行：`produce` 第一道就报「还没有落盘目录」；
    `pending`/`failed` 会被 `pipeline.source` 静默跳过，症状同样是「读不到正文」。
    """
    record = create_custom_article(settings=settings, now=NOW)

    assert record.source_id == CUSTOM_SOURCE_ID == CUSTOM_SOURCE_LABEL
    assert record.url.startswith("custom://")
    assert is_custom_url(record.url)
    assert record.store_dir
    assert str(record.status) == "ok"
    assert _directory(settings, record.store_dir or "").is_dir()


# ---------------------------------------------------------------------------
# 2. 占位内容 / placeholders
# ---------------------------------------------------------------------------


def test_placeholder_title_survives_the_pipeline(settings: Settings) -> None:
    """
    占位标题清理后必须 ≥ `clean.MIN_TITLE_CHARS`。

    store 层不能 import pipeline（分层单向），所以这条约束只能靠测试来钉：
    占位标题一旦写短了，整篇文章会在 `to_news_item` 那一步被丢掉，
    而 `produce` 报出来的是「读不到这篇文章的正文」。
    """
    normalized = " ".join(PLACEHOLDER_TITLE.split())
    assert len(normalized) >= MIN_TITLE_CHARS, (
        f"占位标题「{PLACEHOLDER_TITLE}」只有 {len(normalized)} 字，"
        f"短于 MIN_TITLE_CHARS={MIN_TITLE_CHARS}，整篇会被 pipeline 丢掉"
    )

    record = create_custom_article(settings=settings, now=NOW)
    assert record.title == PLACEHOLDER_TITLE
    assert record.text_len > 0, "正文留空会让格子上显示「0 字」，看起来像坏了"


def test_explicit_title_and_body_are_honoured(settings: Settings) -> None:
    """显式传进来的标题与正文优先于占位内容 / Explicit values win over placeholders."""
    record = create_custom_article(
        settings=settings, title="我自己的文章", body="我自己的正文。", now=NOW
    )
    assert record.title == "我自己的文章"

    directory = _directory(settings, record.store_dir or "")
    assert json.loads((directory / "meta.json").read_text(encoding="utf-8"))["text"] == (
        "我自己的正文。"
    )


# ---------------------------------------------------------------------------
# 3. 落盘 / persistence
# ---------------------------------------------------------------------------


def test_folder_and_files_roundtrip(settings: Settings) -> None:
    """目录名带日期与 id 后缀，article.md / meta.json 都在，结构可反序列化。"""
    record = create_custom_article(settings=settings, now=NOW)
    directory = _directory(settings, record.store_dir or "")

    assert directory.name.endswith(f"__{record.id[:8]}")
    assert directory.parent.name == "20261004"
    assert (directory / "article.md").is_file()
    assert (directory / "meta.json").is_file()
    assert Article.model_validate(
        json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    ).url == record.url


def test_read_body_returns_the_body_verbatim(settings: Settings) -> None:
    """
    `read_body` 必须**一字不差**地读回正文 / `read_body` must return the body verbatim.

    人工创作的文章正文放在粘贴区之下，正是为了这一点：正常路径会丢掉所有
    `# `/`> ` 开头的行，用户手写的引用块和小标题会**安静地消失**。
    """
    body = "# 小标题\n\n> 这是我引用的原文\n\n普通段落。"
    record = create_custom_article(settings=settings, title="带引用块的文章", now=NOW)
    save_body(record.id, body, settings=settings)

    directory = _directory(settings, record.store_dir or "")
    assert read_body(directory / "article.md") == body
    assert read_title(directory / "article.md") == "带引用块的文章"


# ---------------------------------------------------------------------------
# 4. 编辑 / editing
# ---------------------------------------------------------------------------


def test_edit_title_moves_all_three_copies(settings: Settings) -> None:
    """改标题要同时改台账、meta.json、article.md —— 只改一处会互相矛盾。"""
    record = create_custom_article(settings=settings, now=NOW)
    updated = save_title(record.id, "  改过的标题  ", settings=settings)
    assert updated is not None
    # 目录会跟着标题一起改名，所以这里必须用**更新后**的 store_dir
    # The directory is renamed along with the title, so the updated path must be used.
    directory = _directory(settings, updated.store_dir or "")

    assert updated.title == "改过的标题", "首尾空白应当被折叠掉"
    assert updated.feed_title == "改过的标题", (
        "人工创作的文章没有「源」，占位标题留在 feed_title 里会在下次读取时顶掉新标题"
    )
    assert json.loads((directory / "meta.json").read_text(encoding="utf-8"))["title"] == (
        "改过的标题"
    )
    assert read_title(directory / "article.md") == "改过的标题"


def test_edit_body_moves_all_three_copies(settings: Settings) -> None:
    """改正文同理，且台账字数跟着变（格子上的「N 字」就是它）。"""
    record = create_custom_article(settings=settings, now=NOW)
    body = "换成了新的正文。" * 10
    updated = save_body(record.id, body, settings=settings)
    directory = _directory(settings, record.store_dir or "")

    assert updated is not None
    assert updated.text_len == len(body)
    assert json.loads((directory / "meta.json").read_text(encoding="utf-8"))["text"] == body
    assert read_body(directory / "article.md") == body


def test_blank_title_is_ignored_not_written(settings: Settings) -> None:
    """
    只输入空白的标题不该把文章改坏 / A whitespace-only title must not destroy the title.

    格子上的编辑框被清空后失焦是很常见的手滑；照写下去的话这篇就再也
    produce 不出来了（空标题会让 NewsItem 的校验直接抛错）。
    """
    record = create_custom_article(settings=settings, now=NOW)
    updated = save_title(record.id, "   ", settings=settings)

    assert updated is not None
    assert updated.title == PLACEHOLDER_TITLE


def test_renaming_the_title_renames_the_folder(settings: Settings) -> None:
    """
    改标题要**连产物目录一起改名** / A title change must rename the article's folder.

    目录名是 `<标题slug>__<id8>`。不搬的话，`save_article` 里的 `_remove_stale_dirs()`
    会在下一次落盘时按 id 后缀把旧目录**连里面的产物一起删掉**——
    于是「改个标题」变成「另存一份、稿子全丢」。
    The directory name embeds the title slug, and `save_article` deletes same-id
    directories, so not moving it turns a rename into data loss on the next save.
    """
    record = create_custom_article(settings=settings, title="旧标题够长", now=NOW)
    old_dir = _directory(settings, record.store_dir or "")
    # 先造一份产物，证明它会跟着搬（真实场景里是总结 / 短视频稿 / wav / tts 分段）
    (old_dir / "summary.zh.md").write_text("已经生成好的总结", encoding="utf-8")

    updated = save_title(record.id, "换成一个全新的标题", settings=settings)

    assert updated is not None
    new_dir = _directory(settings, updated.store_dir or "")
    assert new_dir.name == f"换成一个全新的标题__{record.id[:8]}"
    assert not old_dir.exists(), "旧目录必须已经搬走，不能留两份"
    assert (new_dir / "summary.zh.md").read_text(encoding="utf-8") == "已经生成好的总结"
    assert read_title(new_dir / "article.md") == "换成一个全新的标题"
    assert updated.title == "换成一个全新的标题"


def test_rename_keeps_the_original_day_folder(settings: Settings) -> None:
    """
    改名只在同一天里改 slug / Only the slug changes, inside the same day folder.

    日期层记的是「首次落盘那天」。跨天改个标题就把目录挪到新的一天，会让所有产物路径
    整体变动——本来只想改个名字。
    """
    record = create_custom_article(settings=settings, now=NOW)
    old_dir = _directory(settings, record.store_dir or "")

    updated = save_title(record.id, "隔天改的标题", settings=settings)

    assert updated is not None
    new_dir = _directory(settings, updated.store_dir or "")
    assert new_dir.parent.name == old_dir.parent.name == "20261004"


def test_editing_the_body_does_not_move_the_folder(settings: Settings) -> None:
    """只改正文不该动目录 / Editing the body leaves the folder alone."""
    record = create_custom_article(settings=settings, title="标题不动", now=NOW)
    before = record.store_dir

    updated = save_body(record.id, "换了正文。", settings=settings)

    assert updated is not None
    assert updated.store_dir == before
    assert _directory(settings, before or "").is_dir()


def test_rename_is_a_no_op_when_the_slug_is_unchanged(settings: Settings) -> None:
    """
    slug 没变就不搬 / The same slug means no move.

    `slugify` 只留 40 字，所以给标题加一长串尾巴可能得到**同一个**目录名。
    这时搬目录会撞上「目标已存在」而报错，而正确行为是原地不动、只更新标题。
    """
    long_title = "标题" * 30  # 60 字，超过 40 字上限，slug 会被截断

    record = create_custom_article(settings=settings, title=long_title, now=NOW)
    before = record.store_dir

    updated = save_title(record.id, long_title + "尾巴", settings=settings)

    assert updated is not None
    assert updated.title == long_title + "尾巴", "标题本身仍要更新"
    assert updated.store_dir == before, "slug 相同，目录不该动"


def test_edit_on_missing_article_returns_none(settings: Settings) -> None:
    """文章不存在时返回 None，不抛异常 / Missing articles return None rather than raise."""
    assert save_title("nope", "x", settings=settings) is None
    assert save_body("nope", "x", settings=settings) is None


def test_fetched_articles_are_read_only(settings: Settings) -> None:
    """
    **抓取来的文章不能在这里改。**改稿会整篇重渲染 `article.md`，对历史产物来说
    那是改写档案：抽取结果、原始链接、当时抓到的正文都可能被覆盖。

    守卫在 store 层，所以命令行也绕不过去（界面只是入口之一）。
    要重抓有 `dna refetch`，要补正文有 `dna sync`（它只读 `article.md`，不改写）。
    """
    ledger = _ledger(settings)
    article_id, _ = ledger.register(
        RawItem(
            source_id="qbitai",
            via=SourceKind.RSS,
            url="https://example.com/fetched",
            title="抓来的文章",
        )
    )
    directory = settings.output_path / "articles" / "20261004" / f"fetched__{article_id[:8]}"
    directory.mkdir(parents=True)
    (directory / "article.md").write_text("# 抓来的文章\n\n> 来源：https://example.com/fetched\n\n原始正文。\n", encoding="utf-8")
    ledger.set_store_dir(article_id, directory.relative_to(settings.output_path).as_posix())
    before = (directory / "article.md").read_text(encoding="utf-8")

    for call in (lambda: save_title(article_id, "改标题", settings=settings),
                 lambda: save_body(article_id, "改正文", settings=settings)):
        with pytest.raises(ValueError, match="只能改「新建」的文章"):
            call()

    assert (directory / "article.md").read_text(encoding="utf-8") == before, (
        "被拒绝的编辑一个字都不该落到历史产物上"
    )


def test_edits_do_not_wipe_the_body(settings: Settings) -> None:
    """
    改标题不能顺带把正文清掉 / Editing the title must not clear the body.

    `_edit` 会把整篇重新渲染回 article.md；只要忘了先读回 meta.json，
    正文就会以 Article 的默认值（空串）写回去——整个正文无声消失。
    这条在**改标题会连目录一起搬**之后更要紧：读回与重写必须都发生在搬完之后。
    This matters even more now that a title change also moves the directory: the read-back
    and the rewrite both have to happen after the move.
    """
    record = create_custom_article(settings=settings, now=NOW)
    save_body(record.id, "先写好正文。", settings=settings)
    updated = save_title(record.id, "再改标题", settings=settings)

    assert updated is not None
    directory = _directory(settings, updated.store_dir or "")
    assert read_body(directory / "article.md") == "先写好正文。"
    assert updated.text_len == len("先写好正文。")


# ---------------------------------------------------------------------------
# 5. 素材文件夹 / asset folders
# ---------------------------------------------------------------------------


def test_media_folders_are_created_for_custom_articles(settings: Settings) -> None:
    """
    人工创作的文章：空素材目录也要创建并返回。

    这是「把素材拷进来」这句话唯一的落脚点——返回空列表会让格子不可点，
    用户就没有任何入口能打开那个文件夹。
    """
    record = create_custom_article(settings=settings, now=NOW)
    folders = media_folders(record.id, settings=settings)

    assert [p.name for p in folders] == ["images", "videos"]
    assert all(p.is_dir() for p in folders)


def test_empty_media_folders_are_not_offered_for_fetched_articles(
    settings: Settings,
) -> None:
    """
    抓取来的文章保持原规则：空目录不返回。

    理由见 `frontends/nicegui_app/actions/paths.py`——不给一个点开是空的按钮。
    """
    ledger = _ledger(settings)
    from dna.core.models import RawItem, SourceKind

    article_id, _ = ledger.register(
        RawItem(
            source_id="qbitai",
            via=SourceKind.RSS,
            url="https://example.com/fetched",
            title="抓来的文章",
        )
    )
    directory = settings.output_path / "articles" / "20261004" / f"fetched__{article_id[:8]}"
    (directory / "images").mkdir(parents=True)
    ledger.set_store_dir(article_id, directory.relative_to(settings.output_path).as_posix())

    assert media_folders(article_id, settings=settings) == []

    # 放了文件之后才该被打开 / only once something is in there
    (directory / "images" / "01_x.jpg").write_bytes(b"x")
    assert [p.name for p in media_folders(article_id, settings=settings)] == ["images"]


def test_media_folders_of_missing_article_is_empty(settings: Settings) -> None:
    """不存在的文章返回空列表 / A missing article yields no folders."""
    assert media_folders("nope", settings=settings) == []


# ---------------------------------------------------------------------------
# 6. 落盘标记 / the on-disk spoken marker
# ---------------------------------------------------------------------------


def test_placeholder_body_is_not_empty() -> None:
    """占位正文不能是空串——见 `test_placeholder_title_survives_the_pipeline`。"""
    assert PLACEHOLDER_BODY.strip()
