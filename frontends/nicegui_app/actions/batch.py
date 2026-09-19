"""批量重抓与批量删除 / Batch refetch and delete。"""

from __future__ import annotations

from nicegui import run

from dna.core.logging import get_logger
from dna.store import FetchStatus, IntakeResult
from dna.store.ledger import ArticleRecord

logger = get_logger("gui.actions")

async def batch_refetch(
    article_ids: list[str],
    *,
    download_images: bool = True,
    download_videos: bool = True,
    on_step=None,
) -> str:
    """
    批量重新抓取 / Re-fetch a batch of articles.

    **逐篇过 `run.io_bound`，不是把整批丢进一个线程。** 一批十篇要跑好几分钟，
    整批一个线程的话进度条从头到尾不动，和卡死看起来一模一样；逐篇回来才能
    报「第 3/10 篇」。这也是 `audio_progress.py` 存在的同一个理由。
    One thread per article rather than one for the batch: a ten-article run takes minutes,
    and a progress indicator that never moves is indistinguishable from a hang.

    **单篇失败不中断其余。** 抓取失败的原因大多是这一个站点的问题
    （403、超时、改版），后面九篇没有理由跟着不抓。
    A single failure never aborts the rest: its cause is almost always specific to that
    one site.

    参数 / Args:
        on_step: `(已完成, 总数, 标题)` 回调，界面用它更新提示条

    返回 / Returns:
        一行汇总。媒体告警单独计数——它**不算失败**（正文已经入库）。
    """
    from dna.store import refetch_article

    ok = degraded = failed = 0
    warnings: list[tuple[str, str]] = []

    for index, article_id in enumerate(article_ids, start=1):
        detail = IntakeResult()

        def _work(aid: str = article_id, d: IntakeResult = detail) -> ArticleRecord | None:
            return refetch_article(
                aid,
                download_images=download_images,
                download_videos=download_videos,
                result=d,
            )

        try:
            updated = await run.io_bound(_work)
        except Exception as exc:
            failed += 1
            logger.warning("重抓失败 %s：%s", article_id[:8], exc)
            updated = None
        else:
            if updated is None:
                failed += 1
            elif str(updated.status) == str(FetchStatus.OK):
                ok += 1
            else:
                degraded += 1

        warnings.extend(detail.media_warnings)
        if on_step is not None:
            on_step(index, len(article_ids), (updated.title if updated else "") or article_id[:8])

    parts = [f"重抓 {len(article_ids)} 篇"]
    if ok:
        parts.append(f"成功 {ok}")
    if degraded:
        parts.append(f"降级 {degraded}（正文没抓到，可用 dna sync 手动补）")
    if failed:
        parts.append(f"失败 {failed}")
    if warnings:
        # **原因要带上第一条。** 只说「媒体未下载 2」等于没说：视频失败的原因
        # （地域限制、封禁下载、拿回来是错误页）决定了要不要人工去弄，
        # 而完整清单在文章目录的 `references.md` 里。
        parts.append(
            f"媒体未下载 {len(warnings)}（不影响正文）——{warnings[0][1]}"
            + ("；其余见文章目录的 references.md" if len(warnings) > 1 else "")
        )
    return "，".join(parts)


def plan_batch_delete(article_ids: list[str]):
    """
    算出这批会删掉什么 / Work out what a batch delete would remove.

    纯查询，给确认框用。**确认框上的数字必须来自真正要删的那批对象**，
    不能在界面里另数一遍——「我以为只选了一篇」是删除事故最常见的形态。
    Read-only, for the confirmation dialog. The numbers must come from the very objects
    about to go; counting them again in the view is how "I thought I only picked one"
    happens.
    """
    from dna.store import plan_delete

    return plan_delete(article_ids)


async def batch_delete(article_ids: list[str], *, remove_files: bool = True) -> str:
    """
    批量删除 / Delete a batch of articles.

    磁盘删除会等（几十个文件 + Windows 上的杀软扫描），所以照样走 io_bound。
    Disk removal blocks long enough to matter, so it goes off the event loop too.
    """
    from dna.store import delete_articles

    def _work():
        return delete_articles(article_ids, remove_files=remove_files)

    plan = await run.io_bound(_work)
    message = plan.result_summary()
    if plan.errors:
        # 失败原因原样带出来：Windows 上「目录被占用」要人去关掉资源管理器，
        # 概括成「删除失败」的话人不知道该做什么。
        message += "　·　" + "；".join(f"{i[:8]} {r}" for i, r in plan.errors[:3])
    return message
