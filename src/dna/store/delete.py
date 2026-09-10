"""
删除文章 / Deleting articles.

把一篇文章从台账里抹掉，连同它在 `outputs/articles/<日期>/<目录>/` 下的所有产物。
Removes an article from the ledger together with everything under its output directory.

两条护栏 / Two guards:

**先算再删。** `plan_delete()` 是纯查询，界面和 CLI 都先拿它的结果给人看
（要删几篇、几份产物、哪几个目录），确认之后才真删。删除是不可逆的，
而「我以为只选了一篇」是最常见的事故。
`plan_delete()` is read-only. Both front-ends show its numbers first — how many
articles, how many productions, which directories — and only then delete. Deletion is
irreversible and "I thought I had only selected one" is the usual way it goes wrong.

**目录必须落在 outputs/articles/ 之内才删。** `store_dir` 是数据库里的一个字符串，
可能被手工改坏、可能是历史遗留的绝对路径。没有这道校验，一个 `..` 或一个空值
就能让 `rmtree` 走到仓库根上去。
A directory is only removed if it really sits under `outputs/articles/`. `store_dir` is
just a string in the database — hand-edited or left over from an older layout — and
without this check a stray `..` would point `rmtree` at the repository root.

顺序 / Order:
    **先删文件，再删数据库行**（与 `migrate_layout` 同一个理由）。中途失败时台账
    还在，重跑能接着删；反过来的话行没了，那个目录就再也没有线索能找到。
    Files first, ledger row second. A failure part-way leaves the row intact so a rerun
    can finish the job; the other order would orphan the directory with nothing pointing
    at it.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dna.core.config import Settings, get_settings
from dna.core.logging import get_logger
from dna.store.ledger import Ledger
from dna.store.migrate_layout import ARTICLES_SUBDIR

logger = get_logger("store.delete")


@dataclass
class DeletePlan:
    """
    一次删除的计划与结果 / The plan for one deletion, and its outcome.

    `plan_delete()` 只填前半（要删什么），`delete_articles()` 跑完再填后半（删掉了什么）。
    同一个对象贯穿始终，确认框显示的数字和最终执行的就是同一份。
    `plan_delete()` fills in what would go; `delete_articles()` fills in what did. One
    object throughout, so the confirmation dialog and the execution cannot disagree.
    """

    article_ids: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    directories: list[Path] = field(default_factory=list)
    production_count: int = 0
    file_count: int = 0
    missing_ids: list[str] = field(default_factory=list)
    """台账里查不到的 id / Ids the ledger does not know."""
    unsafe_dirs: list[tuple[str, str]] = field(default_factory=list)
    """store_dir 不在 outputs/articles/ 之内，拒绝删 / Refused: outside the output root."""

    deleted_articles: int = 0
    deleted_productions: int = 0
    deleted_dirs: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def has_work(self) -> bool:
        """是否有可删的东西 / Whether there is anything to delete."""
        return bool(self.article_ids)

    def summary(self) -> str:
        """一行摘要，确认框直接显示这一句 / The one-liner shown in the dialog."""
        parts = [f"{len(self.article_ids)} 篇"]
        if self.production_count:
            parts.append(f"{self.production_count} 份产物")
        if self.directories:
            parts.append(f"{len(self.directories)} 个目录（{self.file_count} 个文件）")
        if self.missing_ids:
            parts.append(f"台账中不存在 {len(self.missing_ids)}")
        if self.unsafe_dirs:
            parts.append(f"目录异常拒删 {len(self.unsafe_dirs)}")
        return " · ".join(parts)

    def result_summary(self) -> str:
        """执行之后的一行摘要 / The one-liner after execution."""
        parts = [f"已删除 {self.deleted_articles} 篇"]
        if self.deleted_productions:
            parts.append(f"{self.deleted_productions} 份产物")
        if self.deleted_dirs:
            parts.append(f"{self.deleted_dirs} 个目录")
        if self.errors:
            parts.append(f"失败 {len(self.errors)}")
        return " · ".join(parts)


def plan_delete(
    article_ids: list[str] | tuple[str, ...], *, settings: Settings | None = None
) -> DeletePlan:
    """
    算出会删掉什么，不动任何东西 / Work out what would go, touching nothing.

    纯只读，可以放心反复跑。
    Read-only; safe to call repeatedly.
    """
    s = settings or get_settings()
    ledger = Ledger(s.db_file)
    plan = DeletePlan()

    for article_id in article_ids:
        record = ledger.get(article_id)
        if record is None:
            plan.missing_ids.append(article_id)
            continue

        plan.article_ids.append(record.id)
        plan.titles.append(record.title or record.url)
        plan.production_count += ledger.count_productions(record.id)

        if not record.store_dir:
            continue

        directory = safe_article_dir(record.store_dir, s)
        if directory is None:
            plan.unsafe_dirs.append((record.id, record.store_dir))
            continue
        if directory.is_dir():
            plan.directories.append(directory)
            plan.file_count += sum(1 for p in directory.rglob("*") if p.is_file())

    return plan


def delete_articles(
    article_ids: list[str] | tuple[str, ...],
    *,
    remove_files: bool = True,
    settings: Settings | None = None,
) -> DeletePlan:
    """
    删除文章 / Delete the articles.

    参数 / Args:
        remove_files: 是否连磁盘目录一起删。关掉就只清台账，
            盘上的正文、配图、视频原样留着。
            When false only the ledger rows go; everything on disk stays.

    单篇失败不影响其余 / One failure does not stop the rest:
        批量删十篇时第三篇的目录被别的程序占着（Windows 上很常见），
        不该让后面七篇也删不掉。失败的记进 `errors`，**台账行也不删**——
        文件还在盘上，行留着才找得回去。
        A directory locked by another program (routine on Windows) must not block the
        remaining articles. Such a failure is recorded and the ledger row is *kept*: the
        files are still there and the row is the only way back to them.
    """
    s = settings or get_settings()
    ledger = Ledger(s.db_file)
    plan = plan_delete(article_ids, settings=s)

    for article_id in list(plan.article_ids):
        record = ledger.get(article_id)
        if record is None:  # pragma: no cover - plan 刚查过，除非并发删除
            continue

        if remove_files and record.store_dir:
            directory = safe_article_dir(record.store_dir, s)
            if directory is not None and directory.is_dir():
                try:
                    shutil.rmtree(directory)
                except OSError as exc:
                    plan.errors.append((article_id, f"目录删除失败：{exc}"))
                    continue  # 文件还在，台账行留着
                plan.deleted_dirs += 1

        plan.deleted_productions += ledger.delete(article_id)
        plan.deleted_articles += 1

    if remove_files:
        _prune_empty_days(s.output_path / ARTICLES_SUBDIR)

    logger.info("删除完成：%s", plan.result_summary())
    return plan


def safe_article_dir(store_dir: str, settings: Settings) -> Path | None:
    """
    把 store_dir 解析成一个**可以安全删除**的绝对路径 / Resolve a directory safe to delete.

    不在 `outputs/articles/` 之内、或就是这个根目录本身，一律返回 None。
    判断用 `resolve()` 之后的路径比较，`..` 因此在比较之前就被折叠掉了。
    Returns None unless the resolved path really lies *inside* `outputs/articles/`, so a
    `..` in the stored string is collapsed before the check rather than after.
    """
    root = (settings.output_path / ARTICLES_SUBDIR).resolve()
    try:
        candidate = (settings.output_path / store_dir).resolve()
    except OSError:  # pragma: no cover - 非法路径字符
        return None

    if candidate == root:
        return None
    return candidate if candidate.is_relative_to(root) else None


def _prune_empty_days(root: Path) -> None:
    """
    删完之后清掉空掉的日期目录 / Drop date directories emptied by the deletion.

    只删空目录，不递归删内容——和 `migrate_layout._prune_empty` 同一条规矩。
    Empty directories only, never their contents.
    """
    if not root.is_dir():
        return
    for day_dir in sorted(root.iterdir()):
        if day_dir.is_dir() and not any(day_dir.iterdir()):
            day_dir.rmdir()


__all__ = ["DeletePlan", "delete_articles", "plan_delete", "safe_article_dir"]
