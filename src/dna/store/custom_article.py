"""
人工创作的文章 / Hand-authored articles.

用户在界面上点「新建」就得到一篇空文章，自己填标题、正文，往素材文件夹里拷图与视频。
后续的文案 / 音频 / 视频流程与抓取来的文章**完全共用同一套代码**——它是台账里一篇
普通文章，只是没有原文地址。

A blank article created from the UI, filled in by hand (title, body, and assets dropped
into the media folders). Every downstream step — scripts, audio, video — reuses exactly
the same code as a fetched article, because this is an ordinary ledger row that merely
has no source address.

两个必须记住的约束 / Two constraints worth remembering:

    1. **id 不能用空 URL 推。** `url_hash("")` 是常量，空 URL 的每一篇都会撞成同一行
       （见 `core/urls.py` 的 `CUSTOM_URL_SCHEME`）。所以造一个 `custom://<uuid>`
       当身份，id 仍然是 `url_hash(url)`，与全仓其它地方一致。
    2. **占位标题必须够长。** 标题清理后不足 4 字会被 `pipeline.clean` 丢掉，
       症状是 `produce` 报一句极具误导性的「读不到这篇文章的正文」
       （`clean.MIN_TITLE_CHARS`，见 `tests/store/test_custom_article.py` 的守卫用例）。

This module sits in the store layer so that the GUI and the CLI call the same functions;
neither frontend owns any of this logic.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dna.core.config import Settings, get_settings
from dna.core.logging import get_logger
from dna.core.models import Article, RawItem, SourceKind
from dna.core.urls import CUSTOM_SOURCE_LABEL, CUSTOM_URL_SCHEME, is_custom_url
from dna.store.article_render import _render_markdown, read_body, read_title
from dna.store.article_store import article_dir, save_article
from dna.store.ledger import ArticleRecord, Ledger
from dna.store.media_normalize import IMAGE_DIR, VIDEO_DIR

logger = get_logger("store.custom_article")

# 台账里的来源标识，同时也是界面上显示的来源名 / the ledger source id and the UI label
CUSTOM_SOURCE_ID = CUSTOM_SOURCE_LABEL

# 占位内容 / placeholder content.
#
# 标题刻意写成 5 个字：`clean.MIN_TITLE_CHARS` 是 4，短于它的标题会让整篇文章
# 在 `to_news_item` 那一步被静默丢掉，而报错指向「读不到正文」，查起来极费时间。
# 正文不留空：空正文在界面上显示成「0 字」，看起来像坏了；这里留一句说明，
# 用户替换掉它就是自己的内容了。
PLACEHOLDER_TITLE = "未命名文章"
PLACEHOLDER_BODY = (
    "（在此写正文：可以自己写，也可以把原文粘贴进来。"
    "替换掉这一行之后，就能用与普通文章完全相同的流程生成总结 / 短视频 / 中视频 / 长视频。）"
)


def is_custom_record(record: ArticleRecord) -> bool:
    """这条台账记录是不是人工创作的 / Whether this ledger row was hand-authored."""
    return is_custom_url(record.url)


def create_custom_article(
    *,
    settings: Settings | None = None,
    title: str = "",
    body: str = "",
    now: datetime | None = None,
) -> ArticleRecord:
    """
    新建一篇空白文章并落盘 / Create a blank article and persist it.

    落盘不是可选项：`store_dir` 为空的台账行是**废行**——`produce` 第一道就报
    「这篇文章还没有落盘目录」，界面上的正文与媒体格子也不可点。
    Persisting is not optional: a row without `store_dir` is inert — `produce` refuses it
    outright and the body/media cells cannot be opened.

    返回新建的台账记录 / Returns the new ledger record.

    抛出 / Raises:
        RuntimeError: 落盘后台账里读不回这一篇（磁盘或库有问题）
    """
    s = settings or get_settings()
    ledger = Ledger(s.db_file)
    moment = now or datetime.now()

    # 唯一性来自 uuid，不来自标题：用户连点两次「新建」必须得到两篇，
    # 而不是第二篇覆盖第一篇。
    # Uniqueness comes from the uuid, not the title: two clicks must yield two articles.
    url = f"{CUSTOM_URL_SCHEME}://{uuid4().hex}"

    article_id, created = ledger.register(
        RawItem(
            source_id=CUSTOM_SOURCE_ID,
            via=SourceKind.GUI,
            url=url,
            title=title.strip() or PLACEHOLDER_TITLE,
            fetched_at=moment,
        ),
        now=moment,
    )
    if not created:  # pragma: no cover - uuid 撞车只可能是随机数发生器坏了
        raise RuntimeError(f"新建失败：id 重复 {article_id}")

    article = Article(
        url=url,
        title=title.strip() or PLACEHOLDER_TITLE,
        text=body or PLACEHOLDER_BODY,
        extracted_at=moment,
    )
    saved = save_article(
        article,
        article_id,
        root=s.output_path,
        when=moment,
        # 没有源可抓：不下载任何媒体，素材由用户自己拷进目录
        download_images=False,
        download_videos_too=False,
    )
    ledger.record_fetch(
        article_id,
        article,
        store_dir=saved.directory.relative_to(s.output_path).as_posix(),
        image_count=0,
        video_count=0,
        now=moment,
    )

    # 素材目录在建文章时就建出来，而不是等界面点开「媒体」格再懒建。
    # 那两个空目录是用户拷文件的落点：命令行下没有「点一下媒体格」这个动作，
    # 懒建的话 `dna new` 之后用户根本不知道文件该往哪儿放。
    # The asset folders are created here rather than lazily on the media cell's first click:
    # in the CLI there is no such click, and the user would have nowhere to put files.
    for name in (IMAGE_DIR, VIDEO_DIR):
        (saved.directory / name).mkdir(parents=True, exist_ok=True)

    record = ledger.get(article_id)
    if record is None:  # pragma: no cover - 刚写进去就没了，只可能是库被换掉
        raise RuntimeError(f"新建后读不回这一篇：{article_id}")
    logger.info("已新建空白文章：%s（%s）", article_id[:8], saved.directory.name)
    return record


def save_title(
    article_id: str,
    title: str,
    *,
    settings: Settings | None = None,
) -> ArticleRecord | None:
    """
    改标题并三处同步 / Rewrite the title in all three places.

    台账、`meta.json`、`article.md` 必须一起改：`produce` 读的是 `meta.json`，
    人打开看的是 `article.md`，而表格显示的是台账。只改一处就会出现
    「表格里是新标题、产物抬头里是旧标题」。
    All three must move together: `produce` reads `meta.json`, a person opens
    `article.md`, and the table shows the ledger. Updating one alone yields a new title in
    the table and the old one inside the generated artefact.

    **只对人工创作的文章开放**，理由见 `_require_hand_authored`。
    Only hand-authored articles may be edited this way; see `_require_hand_authored`.

    返回更新后的记录；文章不存在或未落盘时返回 None / Returns the updated record.
    """
    cleaned = " ".join((title or "").split())
    if not cleaned:
        return _fresh(article_id, settings=settings)
    return _edit(article_id, settings=settings, title=cleaned)


def save_body(
    article_id: str,
    body: str,
    *,
    settings: Settings | None = None,
) -> ArticleRecord | None:
    """
    改正文并三处同步 / Rewrite the body in all three places，理由同 `save_title`。

    正文是后续所有生成步骤的输入，所以 `meta.json` 那一份就是权威；
    `article.md` 同步写是为了让人打开看到的和机器用的是同一份内容。
    The body is the input to every later step, so `meta.json` holds the authoritative
    copy; `article.md` is written in step with it so that what a person reads and what the
    machine reads never differ.

    **只对人工创作的文章开放**，理由见 `_require_hand_authored`。
    Only hand-authored articles may be edited this way; see `_require_hand_authored`.

    返回更新后的记录；文章不存在或未落盘时返回 None / Returns the updated record.
    """
    return _edit(article_id, settings=settings, body=body)


def article_text(article_id: str, *, settings: Settings | None = None) -> tuple[str, str]:
    """
    读回一篇的标题与正文 / Read one article's title and body back off disk。

    读的是 `article.md`，与 `dna sync` **同一份**（`read_body` / `read_title`）；
    不在 `meta.json` 上再开一个读取口，否则「界面里看到的正文」与「回写时的正文」
    可能出自两份不同的文件。
    Read from `article.md`, the same source `dna sync` uses, rather than opening a second
    reading path into `meta.json`: otherwise what the UI shows and what a write-back reads
    could come from two different files.

    返回 / Returns:
        (标题, 正文)；文章不存在或未落盘时返回 ("", "")
    """
    folders = _folders_for(article_id, settings=settings)
    if folders is None:
        return "", ""

    _, directory = folders
    article_path = directory / "article.md"
    try:
        return read_title(article_path), read_body(article_path)
    except OSError as exc:
        logger.warning("读不到正文，界面会显示为空：%s —— %s", article_path.name, exc)
        return "", ""


def media_folders(article_id: str, *, settings: Settings | None = None) -> list[Path]:
    """
    这篇的素材文件夹，供界面打开 / This article's asset folders, for the UI to open.

    两种行为，按文章来历分 / Two behaviours, decided by how the article came to be:

        - **人工创作**：`images/` 与 `videos/` **按需创建**并返回。空目录本身就是
          「把素材拷进来」这句话的落脚点；把格子做成不可点，等于这个功能不存在。
        - **抓取来的**：只返回**已存在且非空**的目录，不凭空造目录——理由见
          `frontends/nicegui_app/actions/paths.py` 的原注释：不给一个点开是空的按钮。
    """
    folders = _folders_for(article_id, settings=settings)
    if folders is None:
        return []

    record, directory = folders
    names = ("images", "videos")
    if not is_custom_record(record):
        return [
            directory / name
            for name in names
            if (directory / name).is_dir() and any((directory / name).iterdir())
        ]

    for name in names:
        (directory / name).mkdir(parents=True, exist_ok=True)
    return [directory / name for name in names]


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _fresh(article_id: str, *, settings: Settings | None) -> ArticleRecord | None:
    """只读回记录，什么都不改 / Return the record without changing anything."""
    s = settings or get_settings()
    return Ledger(s.db_file).get(article_id)


def _require_hand_authored(record: ArticleRecord) -> None:
    """
    只允许改人工创作的文章 / Only hand-authored articles may be rewritten.

    **改稿会整篇重渲染 `article.md`**，对抓来的文章来说那就是一次**对历史产物的改写**：
    抽取结果、原始链接、当时抓到的正文都可能被覆盖掉。历史产物是**只读的档案**——
    要重新抓有 `dna refetch`，要补正文有 `dna sync`（它只读 `article.md`，不改写它）。
    Rewriting a fetched article would overwrite its extraction result, its original link and
    the body captured at the time. Those artefacts are a read-only archive: re-fetching has
    `dna refetch`, and pasting a missing body has `dna sync`, which only *reads*
    `article.md`.

    守卫放在 store 层而不是界面上：界面只是入口之一，命令行也能调到这两个函数。
    The guard lives in the store rather than the UI: the UI is only one caller, and the CLI
    can reach these functions too.
    """
    if not is_custom_record(record):
        raise ValueError(
            "只能改「新建」的文章。抓取来的文章是只读档案："
            "要重新抓有 `dna refetch`，要补正文可改 article.md 后执行 `dna sync`。"
        )


def _edit(
    article_id: str,
    *,
    settings: Settings | None,
    title: str | None = None,
    body: str | None = None,
) -> ArticleRecord | None:
    """
    改标题 / 正文的公共实现 / The shared implementation behind both edits.

    用 `_render_markdown` 整篇重渲染，而不是对文件做字符串替换：排版只有一处权威，
    替换式改法在排版变化时会安静地改错地方。
    The whole file is re-rendered rather than patched by string surgery: the layout has a
    single authority, and a replace-based edit would quietly target the wrong spot the day
    the layout changes.
    """
    s = settings or get_settings()
    ledger = Ledger(s.db_file)
    record = ledger.get(article_id)
    if record is None or not record.store_dir:
        return record
    _require_hand_authored(record)

    # 目录名带标题 slug，所以改标题必须**同时搬目录**，否则旧目录会被下一次落盘删掉
    # （见 `_relocate`）。
    # The directory name embeds the title slug, so a title change has to move the directory
    # too; otherwise the next save deletes the old one (see `_relocate`).
    directory = s.output_path / record.store_dir
    if title is not None and title != record.title:
        directory = _relocate(record, title, ledger=ledger, settings=s)

    article = _read_meta(directory) or Article(url=record.url, title=record.title)
    if title is not None:
        article.title = title
    if body is not None:
        article.text = body

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "meta.json").write_text(
        article.model_dump_json(indent=2, exclude_none=False), encoding="utf-8"
    )
    (directory / "article.md").write_text(
        _render_markdown(article, article_id, manual_body=is_custom_url(article.url)),
        encoding="utf-8",
    )

    if title is not None:
        ledger.set_title(article_id, article.title, also_feed_title=is_custom_record(record))
    if body is not None:
        # 正文是人写的，状态直接升到 ok——与 `dna sync` 同理（见 `Ledger.set_body`）
        ledger.set_body(article_id, article.text)
    return ledger.get(article_id)


def _relocate(
    record: ArticleRecord,
    title: str,
    *,
    ledger: Ledger,
    settings: Settings,
) -> Path:
    """
    标题变了就把产物目录一起改名 / Rename the article directory when its title changes.

    目录名是 `<标题slug>__<id8>`，所以改标题**必须**同时搬目录，否则两件事都出问题：

    1. 目录名与标题长期不一致，人按文件夹找文章时对不上号。
    2. 更要紧的：`save_article` 里有个 `_remove_stale_dirs()`，它按 id 后缀**删掉同 id
       的其它目录**。标题改了而目录没搬的话，下次任何一次落盘（重抓、重新落盘）都会用
       **新标题**算出新目录，然后把旧目录连同里面**已经生成的全部产物**一起删掉——
       总结、短视频稿、`wav`、`tts/` 分段全没了。改个标题不该毁掉稿子。

    The directory name embeds the title slug, and `save_article` deletes same-id
    directories. A title change must therefore move the directory, or the next save would
    compute a new path from the new title and delete the old one along with every artefact
    already generated inside it.

    只在**同一个日期层**里改 slug：日期层记的是「首次落盘那天」，跨天改标题不该把目录
    挪到新的一天——那会让所有产物路径整体变动。
    Only the slug changes, within the same day folder, which records the day the article was
    first written.

    返回改完之后该用的目录（没搬就返回原目录）。
    """
    old = settings.output_path / (record.store_dir or "")
    if not old.is_dir() or not old.name.endswith(f"__{record.id[:8]}"):
        # 目录不在、或名字不像我们生成的（有人手工挪过）→ 只改文件，别去动目录
        logger.warning("产物目录形状不对，改标题时不重命名：%s", old)
        return old

    try:
        when = datetime.strptime(old.parent.name, "%Y%m%d")
    except ValueError:
        logger.warning("日期层不是 YYYYMMDD，改标题时不重命名：%s", old.parent.name)
        return old

    target = article_dir(settings.output_path, record.id, title, when)
    if target == old:
        # 标题被截断成同一个 slug（`slugify` 只留 40 字），或者只改了大写之类
        return old
    if target.exists():
        raise ValueError(f"产物目录已存在，没有重命名：{target.name}。请手工确认后重试。")

    old.rename(target)
    ledger.set_store_dir(record.id, target.relative_to(settings.output_path).as_posix())
    logger.info("标题已改，产物目录跟着改名：%s → %s", old.name, target.name)
    return target


def _read_meta(directory: Path) -> Article | None:
    """
    从 `meta.json` 读回完整的 Article / Read the whole Article back out of `meta.json`.

    与 `pipeline/source._read_persisted` **契约不同，故意不合并**：那边只要正文与媒体，
    且允许降级成「空正文 + 无媒体」；这边要用完整结构把文章重渲染回 `article.md`
    （url / author / published_at / extracted_at 都得在），坏掉时宁可退回台账字段。
    Deliberately not merged with `pipeline/source._read_persisted`: that one wants only the
    body and media and degrades to empty, whereas re-rendering `article.md` needs the full
    structure, so a broken file falls back to the ledger fields instead.
    """
    meta_path = directory / "meta.json"
    try:
        return Article.model_validate(json.loads(meta_path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        logger.warning("读不到 meta.json，改稿会退回台账字段：%s —— %s", directory.name, exc)
        return None


def _folders_for(
    article_id: str, *, settings: Settings | None
) -> tuple[ArticleRecord, Path] | None:
    """记录与它所在的目录 / The record and the directory it lives in."""
    s = settings or get_settings()
    record = Ledger(s.db_file).get(article_id)
    if record is None or not record.store_dir:
        return None
    return record, s.output_path / record.store_dir


__all__ = [
    "CUSTOM_SOURCE_ID",
    "PLACEHOLDER_BODY",
    "PLACEHOLDER_TITLE",
    "article_text",
    "create_custom_article",
    "is_custom_record",
    "media_folders",
    "save_body",
    "save_title",
]
