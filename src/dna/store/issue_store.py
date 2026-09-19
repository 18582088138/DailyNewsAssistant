"""
期次落盘与装配 / Issue-level persistence and assembly.

一期日报的目录 / The directory of one issue:

    outputs/20260902-DailyNews/
    ├── _digest.json     结构化事实源，可重放（三个发布应用的唯一输入）
    ├── _references.md   全期来源汇总：每条的出处、多源报道、图片与视频出处
    ├── graphic/         场景1 整期图文        ← P5
    └── podcast/         场景3 整期播客        ← P7

**期次目录里没有条目资产。** 正文、配图、视频、五种文案全都在
`outputs/articles/<日期>/<slug>__<id8>/`（P3.5 定下的唯一权威），期次这边只按 id 引用。
The issue directory holds no per-article assets: bodies, images, videos and all five
copy kinds live under `outputs/articles/...`, and the issue only references them by id.

为什么不复制一份到期次目录 / Why assets are referenced rather than copied:
    一篇文章可以进多期（跨日的后续报道），复制就会出现同一篇的两份副本，
    改了一份另一份还是旧的。而按 id 引用只有一处真相，重做条目产物之后，
    期次这边**自动就是新的**——不需要重新装配。
    An article can appear in more than one issue. Copying would create two copies that
    drift apart once one is regenerated. Referencing by id keeps a single source of
    truth, so redoing a per-article production is immediately reflected here with no
    reassembly.

`_references.md` 与条目目录里的 `references.md` 不重复 / The two reference files differ:
    条目那份记的是**下载下来的文件名**（`images/01_host.jpg` ← 原始地址）；
    这份记的是**本期用到了谁**，并指路到条目目录。各管一件事，不互相抄。
    The per-article one records downloaded filenames; this one records who was used in
    this issue and points at the directories. Neither duplicates the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime
from pathlib import Path

from dna.core.config import Settings, get_settings
from dna.core.logging import get_logger
from dna.core.models import DailyDigest, DigestEntry, MediaAsset
from dna.core.naming import ISSUE_SUFFIX, issue_dir_name
from dna.core.urls import url_hash
from dna.store.ledger import Ledger

logger = get_logger("store.issue_store")

DIGEST_FILENAME = "_digest.json"
REFERENCES_FILENAME = "_references.md"

# 期次级产物的子目录 / sub-directories for issue-level outputs
GRAPHIC_DIRNAME = "graphic"  # P5
PODCAST_DIRNAME = "podcast"  # P7


@dataclass
class IssuePaths:
    """
    一期日报的全部路径 / Every path of one issue.

    纯数据，不建目录 / Pure data; nothing is created here.
    """

    root: Path
    digest: Path
    references: Path
    graphic: Path
    podcast: Path

    def exists(self) -> bool:
        """这期是否已经生成过 / Whether this issue has been built."""
        return self.digest.exists()


@dataclass
class EntryLink:
    """
    一条目录引用 / One entry's link into the article store.

    `store_dir` 为 None 表示**没能定位到条目目录**——文章被手工删了、
    或者这份 digest 是在别的机器上生成的。这种情况必须显式暴露，
    不能静默地少写一行链接。
    A None `store_dir` means the directory could not be located. That must surface
    explicitly rather than silently dropping a link.
    """

    entry_id: str
    rank: int
    title: str
    store_dir: str | None = None

    @property
    def relative_path(self) -> str | None:
        """从期次目录出发的相对路径 / Path relative to the issue directory."""
        return f"../{self.store_dir}/" if self.store_dir else None


@dataclass
class SavedIssue:
    """一次期次落盘的结果 / The result of persisting one issue."""

    paths: IssuePaths
    links: list[EntryLink] = field(default_factory=list)

    @property
    def entry_count(self) -> int:
        return len(self.links)

    @property
    def unresolved(self) -> list[EntryLink]:
        """没能定位到条目目录的条目 / Entries whose directory was not found."""
        return [link for link in self.links if link.store_dir is None]

    def summary(self) -> str:
        """一行摘要 / A one-line summary."""
        text = f"{self.entry_count} 条"
        missing = len(self.unresolved)
        if missing:
            text += f"，其中 {missing} 条未能定位条目目录"
        return text


def issue_paths(day: Date | datetime, *, settings: Settings | None = None) -> IssuePaths:
    """
    计算一期日报的路径 / Work out the paths of one issue.

    纯函数，不建目录 / A pure function; it creates nothing.
    """
    s = settings or get_settings()
    root = s.output_path / issue_dir_name(day)
    return IssuePaths(
        root=root,
        digest=root / DIGEST_FILENAME,
        references=root / REFERENCES_FILENAME,
        graphic=root / GRAPHIC_DIRNAME,
        podcast=root / PODCAST_DIRNAME,
    )


def save_issue(
    digest: DailyDigest,
    *,
    settings: Settings | None = None,
    ledger: Ledger | None = None,
) -> SavedIssue:
    """
    把一期日报落盘 / Persist one issue.

    写两个文件：`_digest.json`（事实源）与 `_references.md`（来源汇总）。
    子目录 `graphic/` 与 `podcast/` **不预建**——空目录只会让人以为已经生成过了。
    Writes the digest and the reference roll-up. The `graphic/` and `podcast/`
    sub-directories are not pre-created: an empty directory reads as "already built".

    参数 / Args:
        ledger: 用来把条目 id 解析回落盘目录；不给则按 settings 打开台账

    这个函数**不调用 LLM，也不产生费用**——它只是把已有的 Digest 写下来。
    因此对着同一份 digest 反复跑是安全的，`_references.md` 也可以随时重建。
    This makes no LLM calls and costs nothing: it only writes an existing digest down, so
    it is safe to re-run and the reference file can be rebuilt at any time.
    """
    s = settings or get_settings()
    paths = issue_paths(digest.date, settings=s)
    paths.root.mkdir(parents=True, exist_ok=True)

    links = resolve_links(digest, settings=s, ledger=ledger)

    paths.digest.write_text(
        digest.model_dump_json(indent=2, exclude_none=False), encoding="utf-8"
    )
    paths.references.write_text(_render_references(digest, links), encoding="utf-8")

    saved = SavedIssue(paths=paths, links=links)
    if saved.unresolved:
        # 不是致命错误，但必须说出来：少写的链接不会自己出现在产物里
        logger.warning(
            "%d 条未能定位条目目录：%s",
            len(saved.unresolved),
            "、".join(link.entry_id[:8] for link in saved.unresolved),
        )
    logger.info("期次已落盘：%s（%s）", paths.root.name, saved.summary())
    return saved


def load_issue(
    day: Date | datetime, *, settings: Settings | None = None
) -> DailyDigest | None:
    """
    读回一期日报 / Read one issue back.

    读不到或结构不合法时返回 None，不抛异常——上层要能区分「没有这一期」
    和「这一期坏了」，两种都是「拿不到」，都由调用方决定怎么提示。
    Returns None rather than raising: the caller decides how to report both a missing and
    a corrupt issue.
    """
    paths = issue_paths(day, settings=settings)
    try:
        return DailyDigest.model_validate_json(paths.digest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("读不到期次 %s：%s", paths.root.name, exc)
        return None


def list_issues(*, settings: Settings | None = None) -> list[Date]:
    """
    列出已生成的期次日期，从新到旧 / List built issues, newest first.

    只认**已经有 `_digest.json`** 的目录：一个空的期次目录不算一期。
    Only directories carrying a digest count; an empty one is not an issue.
    """
    s = settings or get_settings()
    if not s.output_path.is_dir():
        return []

    days: list[Date] = []
    for entry in s.output_path.iterdir():
        if not entry.is_dir() or not entry.name.endswith(f"-{ISSUE_SUFFIX}"):
            continue
        if not (entry / DIGEST_FILENAME).exists():
            continue
        try:
            days.append(datetime.strptime(entry.name.split("-")[0], "%Y%m%d").date())
        except ValueError:
            logger.debug("期次目录名不合规，跳过：%s", entry.name)

    return sorted(days, reverse=True)


def refresh_references(
    day: Date | datetime,
    *,
    settings: Settings | None = None,
    ledger: Ledger | None = None,
) -> SavedIssue | None:
    """
    只重建 `_references.md` / Rebuild the reference file alone.

    条目目录会因为重抓（标题变了 → slug 变了）而改名，那之后期次里的链接就指错了。
    重建是纯计算，**不碰 `_digest.json`、不调用 LLM、不花钱**。
    A re-fetch can rename an article directory (a new title means a new slug), leaving the
    issue's links pointing nowhere. Rebuilding is pure computation: the digest is left
    untouched and nothing is billed.

    这一期不存在时返回 None / Returns None when the issue does not exist.
    """
    digest = load_issue(day, settings=settings)
    if digest is None:
        return None

    s = settings or get_settings()
    paths = issue_paths(digest.date, settings=s)
    links = resolve_links(digest, settings=s, ledger=ledger)
    paths.references.write_text(_render_references(digest, links), encoding="utf-8")

    return SavedIssue(paths=paths, links=links)


def resolve_links(
    digest: DailyDigest,
    *,
    settings: Settings | None = None,
    ledger: Ledger | None = None,
) -> list[EntryLink]:
    """
    把每条 digest 条目解析回它的落盘目录 / Resolve each entry to its article directory.

    条目 id 与台账主键都是 `url_hash(url)`（见 `core/urls.url_hash`），所以直接查得到。
    查不到时**退一步按来源链接再查一遍**：聚类时代表条目可能换过，
    而同一事件的其它来源仍然在台账里。
    Entry ids and ledger keys are both `url_hash(url)`, so a direct lookup works. When it
    misses, the other source links are tried as well, since clustering may have picked a
    representative that is not the one on record.
    """
    s = settings or get_settings()
    book = ledger or Ledger(s.db_file)

    links: list[EntryLink] = []
    for entry in digest.entries:
        store_dir = _lookup_store_dir(entry, book, settings=s)
        links.append(
            EntryLink(
                entry_id=entry.id,
                rank=entry.rank,
                title=entry.title_zh,
                store_dir=store_dir,
            )
        )
    return links


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _lookup_store_dir(entry: DigestEntry, ledger: Ledger, *, settings: Settings) -> str | None:
    """
    查一条目的落盘目录，找不到返回 None / Find one entry's directory, or None.

    台账里记着目录但目录已被删掉时，同样返回 None——写一个点开是 404 的链接，
    比不写更糟：人会以为文件在那里。
    A recorded directory that no longer exists also returns None: a link that 404s is
    worse than no link, because it reads as "the files are there".
    """
    candidates = [entry.id, *(url_hash(ref) for ref in entry.refs)]

    for article_id in dict.fromkeys(candidates):
        record = ledger.get(article_id)
        if record is None or not record.store_dir:
            continue
        if (settings.output_path / record.store_dir).is_dir():
            return record.store_dir
        logger.debug("台账记着目录但已不存在：%s", record.store_dir)

    return None


def _render_references(digest: DailyDigest, links: list[EntryLink]) -> str:
    """
    渲染全期来源汇总 / Render the issue-wide reference roll-up.

    这份文件是**发布时的合规依据**：图文/视频/播客发出去之前，
    每一张图、每一段视频的出处都要在这里查得到。
    This file is what attribution is checked against before publishing: every image and
    clip used in the issue must be traceable here.
    """
    by_id = {link.entry_id: link for link in links}
    lines = [
        f"# 来源 / References — {digest.date:%Y-%m-%d} 每日 AI 资讯",
        "",
        f"> 本期 {len(digest.entries)} 条，生成于 {digest.generated_at:%Y-%m-%d %H:%M}。",
        (
            "> 条目资产（正文·配图·视频·文案）在 `outputs/articles/` 下，"
            "本文件**按 id 引用，不复制文件**。"
        ),
        "> 每篇下载下来的文件名见各条目目录里的 `references.md`。",
        "",
    ]

    for entry in digest.entries:
        lines += _render_entry(entry, by_id.get(entry.id))

    lines += _render_footer(digest, links)
    return "\n".join(lines)


def _render_entry(entry: DigestEntry, link: EntryLink | None) -> list[str]:
    """渲染单条 / Render one entry's block."""
    lines = ["---", "", f"## {entry.rank:02d} · {entry.title_zh}", ""]

    origin = entry.refs[0] if entry.refs else ""
    if origin:
        lines.append(f"- 原文：{origin}")

    others = entry.refs[1:]
    if others:
        # 多源报道时全部列出：读者应该看到全部出处，而不只是被选中那一家
        lines.append(f"- 另有 {len(others)} 家报道：")
        lines += [f"  - {url}" for url in others]

    if link is not None and link.relative_path:
        lines.append(f"- 条目目录：[`{link.relative_path}`]({link.relative_path})")
    else:
        lines.append("- 条目目录：⚠️ 未能定位（文章目录可能已被删除）")

    lines += _render_media(entry.images, "配图 / Images")
    lines += _render_media(entry.videos, "视频 / Videos")
    lines.append("")
    return lines


def _render_media(assets: list[MediaAsset], heading: str) -> list[str]:
    """
    渲染媒体出处 / Render a media block.

    列的是**原始地址**而不是本地文件名：本地文件名会随重抓变化，
    原始地址才是署名要引用的那一个。
    Original addresses are listed rather than local filenames: the latter change on a
    re-fetch, while the former is what attribution cites.
    """
    if not assets:
        return []

    lines = ["", f"### {heading}（{len(assets)}）", ""]
    for asset in assets:
        credit = f"（来源：{asset.credit}）" if asset.credit else ""
        lines.append(f"- {asset.url}{credit}")
        if asset.caption:
            lines.append(f"  - 说明：{asset.caption}")
    return lines


def _render_footer(digest: DailyDigest, links: list[EntryLink]) -> list[str]:
    """渲染统计与未解析清单 / Render the tallies and the unresolved list."""
    images = sum(len(e.images) for e in digest.entries)
    videos = sum(len(e.videos) for e in digest.entries)
    sources = len({ref for e in digest.entries for ref in e.refs})
    unresolved = [link for link in links if link.store_dir is None]

    lines = [
        "---",
        "",
        "## 统计 / Tally",
        "",
        f"- 条目 {len(digest.entries)} · 来源链接 {sources} · 配图 {images} · 视频 {videos}",
    ]

    if unresolved:
        lines += [
            f"- ⚠️ 未能定位条目目录：{len(unresolved)} 条",
            *(f"  - {link.rank:02d} · {link.title}（{link.entry_id[:8]}）" for link in unresolved),
            "",
            "> 目录可能被手工删除，或这份 digest 来自另一台机器的台账。",
            "> 重抓对应文章后执行 `dna issue refresh` 可以修好这些链接。",
        ]

    lines.append("")
    return lines


__all__ = [
    "DIGEST_FILENAME",
    "GRAPHIC_DIRNAME",
    "PODCAST_DIRNAME",
    "REFERENCES_FILENAME",
    "EntryLink",
    "IssuePaths",
    "SavedIssue",
    "issue_paths",
    "list_issues",
    "load_issue",
    "refresh_references",
    "resolve_links",
    "save_issue",
]
