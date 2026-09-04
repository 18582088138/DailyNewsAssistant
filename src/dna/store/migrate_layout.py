"""
落盘目录迁移 / Storage layout migration.

把文章目录从 `data/articles/` 搬到 `outputs/articles/`，并改写台账里的 `store_dir`。
Moves article directories from `data/articles/` to `outputs/articles/` and rewrites the
ledger's `store_dir`.

为什么搬 / Why:
    文章目录里放的是**产物**——正文、配图、视频，接下来还要加总结、口播稿、
    短视频稿。这些是用户要打开、要拷走、要发布的东西，属于 `outputs/`。
    `data/` 留给程序自己的东西：台账数据库与 LLM 缓存。
    The article directory holds deliverables — body, images, video, and soon summaries
    and scripts. Those are what the user opens, copies out and publishes, so they belong
    in `outputs/`. `data/` keeps what only the program cares about: the ledger and cache.

顺序不能反 / The order matters:
    **先移文件，成功后再改数据库。** 反过来的话，改完 DB 而文件没移成，
    台账里每一行都指向不存在的目录，且没有任何线索能找回去。
    按这个顺序，中途失败时 DB 还是旧的，重跑即可从断点继续——
    已经搬走的目录在源位置找不到，会被当作「已完成」跳过。
    Files move first and the database is updated only after. The other way round, a
    failure would leave every ledger row pointing at a directory that does not exist,
    with nothing to trace it back. In this order a failure leaves the database untouched
    and a rerun resumes: already-moved directories are simply absent from the source and
    are skipped as done.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dna.core.config import Settings, get_settings
from dna.core.logging import get_logger
from dna.store.ledger import Ledger

logger = get_logger("store.migrate_layout")

# 旧位置与新位置的相对子路径 / the sub-path under each root
ARTICLES_SUBDIR = "articles"


@dataclass
class MigrationPlan:
    """
    一次迁移的计划与结果 / The plan for one migration, and its outcome.

    `--dry-run` 时只填 `moves`，不动任何文件——**先看会发生什么再执行**。
    In a dry run only `moves` is populated and nothing is touched: see what would happen
    before it happens.
    """

    moves: list[tuple[Path, Path]] = field(default_factory=list)
    already_done: list[str] = field(default_factory=list)
    ledger_updates: int = 0
    skipped_missing: list[str] = field(default_factory=list)
    conflicts: list[tuple[Path, Path]] = field(default_factory=list)
    orphans: list[Path] = field(default_factory=list)
    """
    旧位置里没有任何台账行引用的目录 / Directories no ledger row points at.

    **只报告，不删除也不搬走。** 它们多半是历史遗留的重名副本（见 issues/004），
    但「没人引用」不等于「可以删」——盘上的东西是用户的，该由用户决定。
    Reported only, never moved or deleted. They are usually stale duplicates left by
    earlier bugs, but "unreferenced" is not "disposable": what is on disk belongs to the
    user and the decision is theirs.
    """
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def has_work(self) -> bool:
        """是否有可搬的东西 / Whether there is anything to move."""
        return bool(self.moves)

    def summary(self) -> str:
        """一行摘要 / A one-line summary."""
        parts = [f"待迁移 {len(self.moves)} 个目录"]
        if self.already_done:
            parts.append(f"已在新位置 {len(self.already_done)}")
        if self.skipped_missing:
            parts.append(f"源目录不存在 {len(self.skipped_missing)}")
        if self.conflicts:
            parts.append(f"目标已存在 {len(self.conflicts)}")
        if self.orphans:
            parts.append(f"无台账引用 {len(self.orphans)}")
        if self.ledger_updates:
            parts.append(f"台账更新 {self.ledger_updates}")
        if self.errors:
            parts.append(f"失败 {len(self.errors)}")
        return "，".join(parts)


def plan_migration(settings: Settings | None = None) -> MigrationPlan:
    """
    只计算要做什么，不动任何文件 / Work out what would be done, touching nothing.

    纯粹的只读操作，可以放心反复跑。
    Purely read-only; safe to run repeatedly.
    """
    s = settings or get_settings()
    plan = MigrationPlan()

    old_root = s.data_path / ARTICLES_SUBDIR
    new_root = s.output_path / ARTICLES_SUBDIR

    for record in Ledger(s.db_file).list(limit=100_000):
        if not record.store_dir:
            continue

        relative = _relative_to_articles(record.store_dir)
        if relative is None:
            # store_dir 不是文章目录的形状，交给人处理而不是猜
            plan.errors.append((record.id, f"无法识别的 store_dir：{record.store_dir}"))
            continue

        source = old_root / relative
        target = new_root / relative

        if target.exists() and not source.exists():
            plan.already_done.append(record.id)
            continue
        if not source.exists():
            plan.skipped_missing.append(record.id)
            continue
        if target.exists():
            # 两边都在：不猜哪个是新的，交给人决定
            plan.conflicts.append((source, target))
            continue

        plan.moves.append((source, target))

    plan.orphans = _find_orphans(old_root, {src for src, _ in plan.moves})
    return plan


def _find_orphans(old_root: Path, referenced: set[Path]) -> list[Path]:
    """
    找出旧位置里没有台账行引用的目录 / Directories under the old root nobody references.

    典型来源：历史 bug 让同一篇文章留下过两份重名副本（issues/004）。
    列出来让人自己判断，**不代为删除**。
    Typically stale duplicates left by earlier bugs. They are listed for the user to
    judge; nothing is deleted on their behalf.
    """
    if not old_root.is_dir():
        return []

    orphans: list[Path] = []
    for day_dir in sorted(old_root.iterdir()):
        if not day_dir.is_dir():
            continue
        for candidate in sorted(day_dir.iterdir()):
            if candidate.is_dir() and candidate not in referenced:
                orphans.append(candidate)
    return orphans


def migrate(settings: Settings | None = None, *, dry_run: bool = False) -> MigrationPlan:
    """
    执行迁移 / Perform the migration.

    参数 / Args:
        dry_run: 只计算不执行

    逐个目录处理，**每搬完一个就更新对应的台账行**，而不是全部搬完再统一更新。
    这样中途失败时，已搬走的行已经指向新位置，没搬的还指向旧位置，两边都自洽。
    Each directory is moved and its ledger row updated immediately, rather than moving
    everything and then updating in bulk. A failure part-way therefore leaves moved rows
    pointing at the new location and unmoved rows at the old one — both self-consistent.
    """
    s = settings or get_settings()
    plan = plan_migration(s)

    if dry_run or not plan.has_work:
        if not dry_run:
            _sync_ledger_paths(s, plan)
        logger.info("迁移计划：%s", plan.summary())
        return plan

    ledger = Ledger(s.db_file)
    new_root = s.output_path / ARTICLES_SUBDIR
    moved: list[tuple[Path, Path]] = []

    for source, target in plan.moves:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        except OSError as exc:
            plan.errors.append((source.name, f"移动失败：{exc}"))
            continue

        moved.append((source, target))
        relative = target.relative_to(s.output_path).as_posix()
        for article_id in _ids_for(ledger, target.name):
            ledger.set_store_dir(article_id, relative)
            plan.ledger_updates += 1

    plan.moves = moved
    _sync_ledger_paths(s, plan)
    _prune_empty(s.data_path / ARTICLES_SUBDIR)

    logger.info("迁移完成：%s", plan.summary())
    return plan


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _relative_to_articles(store_dir: str) -> str | None:
    """
    从 store_dir 里取出 `<日期>/<目录名>` 部分 / Extract the date/name part.

    历史上 store_dir 存的是相对 `data/` 的路径（`articles/20260902/xxx__ab12`），
    迁移后要变成相对 `outputs/` 的同名路径。这里把两端共有的部分抠出来。
    Historically `store_dir` was relative to `data/`; afterwards it is relative to
    `outputs/`. This pulls out the part the two forms share.
    """
    parts = [p for p in Path(store_dir).as_posix().split("/") if p]
    if not parts:
        return None
    if parts[0] == ARTICLES_SUBDIR:
        parts = parts[1:]
    return "/".join(parts) if parts else None


def _ids_for(ledger: Ledger, directory_name: str) -> list[str]:
    """
    从目录名反查文章 id / Find the article ids owning a directory name.

    目录名形如 `<slug>__<id 前 8 位>`，用后缀反查即可。
    返回列表而不是单个：理论上 8 位前缀可能撞车，撞了就两行都更新——
    它们本来就共用一个目录名，指向同一个位置是正确的。
    The name ends in the first eight characters of the id. A list is returned because
    that prefix could in principle collide; if it does, both rows are updated, which is
    correct since they already share the directory name.
    """
    suffix = directory_name.rsplit("__", 1)[-1]
    return [r.id for r in ledger.list(limit=100_000) if r.id.startswith(suffix)]


def _sync_ledger_paths(settings: Settings, plan: MigrationPlan) -> None:
    """
    把已经在新位置、但台账仍写着旧前缀的行修正过来 / Fix rows already in the new place.

    应对「文件被人手工搬过、或上次迁移中途失败」的情况：文件在新位置，
    台账里却还是 `articles/...` 相对 data 的写法。这里让两者重新对齐。
    Covers directories moved by hand or a previous run that failed part-way: the files
    are in the new location while the ledger still holds the old-style path.
    """
    ledger = Ledger(settings.db_file)
    new_root = settings.output_path / ARTICLES_SUBDIR

    for record in ledger.list(limit=100_000):
        if not record.store_dir:
            continue
        relative = _relative_to_articles(record.store_dir)
        if relative is None:
            continue

        expected = f"{ARTICLES_SUBDIR}/{relative}"
        if record.store_dir.replace("\\", "/") == expected:
            continue
        if (new_root / relative).exists():
            ledger.set_store_dir(record.id, expected)
            plan.ledger_updates += 1


def _prune_empty(root: Path) -> None:
    """
    清掉搬空之后剩下的空目录 / Remove directories left empty by the move.

    只删空目录，绝不递归删内容——万一有没搬走的东西，宁可留着让人看见。
    Only empty directories are removed, never their contents: anything left behind should
    stay visible rather than be quietly deleted.
    """
    if not root.is_dir():
        return

    for child in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if child.is_dir() and not any(child.iterdir()):
            child.rmdir()

    if not any(root.iterdir()):
        root.rmdir()


__all__ = ["ARTICLES_SUBDIR", "MigrationPlan", "migrate", "plan_migration"]
