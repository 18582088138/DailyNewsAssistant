"""导入：粘链接与从订阅抓 / Bringing articles in, by link or by feed。"""

from __future__ import annotations

from nicegui import run

from dna.core.logging import get_logger
from dna.store import intake_urls
from frontends.nicegui_app.actions.progress import _progress_writer

logger = get_logger("gui.actions")

async def import_links(
    text: str,
    *,
    download_images: bool = True,
    download_videos: bool = True,
    max_images: int | None = None,
    refetch: bool = False,
    progress: dict | None = None,
) -> str:
    """
    导入用户粘贴的链接 / Ingest the links the user pasted.

    **不调用 LLM，不产生费用**——只是抓正文和媒体。抓完是否要生成文案，
    仍然由表格里的格子逐个决定。
    Calls no LLM and costs nothing: it fetches bodies and media only. Whether to write any
    copy afterwards remains a per-cell decision in the table.

    走 `intake_urls` 而不是在界面里另写一套：链接提取、去重、按 canonical_url 判重、
    落盘、写台账全都在那里，重写一遍就会出现「命令行抓的和界面抓的不一样」。
    Delegates to `intake_urls` rather than reimplementing: extraction, de-duplication by
    canonical URL, storage and ledger writes all live there, and a second copy would mean
    the CLI and the workbench ingest differently.
    """

    def _work() -> str:
        result = intake_urls(
            text,
            download_images=download_images,
            max_images=max_images,
            download_videos=download_videos,
            refetch=refetch,
            on_progress=_progress_writer(progress),
        )
        if result.collected == 0:
            return "没有识别到任何链接"

        # 每一类都报出来，账要对得上：collected = 跳过 + 成功 + 降级 + 失败。
        # 只报「入了 N 条」的话，少掉的那几条去哪了没人知道。
        # Every bucket is reported so the counts reconcile; a bare "ingested N" leaves the
        # missing ones unexplained.
        parts = [f"识别 {result.collected} 条"]
        if result.fetched_ok:
            parts.append(f"成功 {result.fetched_ok}")
        if result.fetched_degraded:
            parts.append(f"降级 {result.fetched_degraded}（正文没抓到，可用 dna sync 手动补）")
        if result.skipped_existing:
            parts.append(f"已存在跳过 {result.skipped_existing}")
        if result.failed:
            parts.append(f"失败 {result.failed}")
        return "，".join(parts)

    return await run.io_bound(_work)




# ---------------------------------------------------------------------------
# 订阅采集 / collecting from the configured sources
# ---------------------------------------------------------------------------


def source_options() -> dict[str, str]:
    """
    订阅源下拉选项 / The subscription dropdown options.

    从 `load_sources()` 来，**不是** `Ledger.count_by_source()`——后者是台账里出现过的源，
    里面有已经停用的死源；这里要的是配置里当前启用的源，包括**一次还没抓过的新源**。
    筛选栏那个下拉正好相反，它问的是「已经抓到的东西里挑哪些看」。
    From `load_sources()` rather than the ledger: the ledger lists sources that have
    appeared before, including disabled dead ones, while this needs the currently enabled
    ones — including a source added today that has never run. The filter bar's dropdown
    asks the opposite question.
    """
    from dna.core.config import load_sources

    try:
        sources = load_sources()
    except Exception as exc:
        logger.warning("读取订阅源失败：%s", exc)
        return {}
    return {s.id: (s.name or s.id) for s in sources}


async def import_from_sources(
    source_ids: list[str] | None = None,
    *,
    max_age_days: int | None = 3,
    limit_per_source: int | None = 10,
    download_images: bool = True,
    download_videos: bool = True,
    refetch: bool = False,
    progress: dict | None = None,
) -> str:
    """
    从订阅源采集并入库 / Collect from the sources and ingest.

    **不调用 LLM，不产生费用。** 与 `dna fetch` 调的是同一个 `intake_sources`——
    命令行抓的和界面抓的必须是同一批东西，否则「昨天命令行抓到了、今天界面抓不到」
    这类问题永远查不清是配置差异还是代码差异。
    Calls no LLM. Delegates to the same `intake_sources` as `dna fetch`, so the two
    front-ends cannot collect differently.

    `progress` 与 `run_production` 是同一个做法：**共享字典由工作线程写、UI 定时读**。
    整趟采集在一个 `run.io_bound` 里，回调落在工作线程上，而跨线程改 NiceGUI
    元素不安全。
    The same shared-dict idiom as `run_production`, for the same cross-thread reason.
    """
    from dna.store import intake_sources

    def _work() -> str:
        result = intake_sources(
            source_ids=source_ids or None,
            limit_per_source=limit_per_source,
            download_images=download_images,
            download_videos=download_videos,
            refetch=refetch,
            max_age_days=max_age_days,
            on_progress=_progress_writer(progress),
        )
        message = result.summary()
        # 源级失败单独报：一个 feed 挂了而其余正常时，总数会显得「今天新闻很少」,
        # 不点出来的话人会以为是天数选窄了。
        # Reported separately: with one dead feed the totals just look like a slow news
        # day, and the user would blame the day-count instead.
        if result.source_failures:
            names = "、".join(sid for sid, _ in result.source_failures[:3])
            message += f"　·　{len(result.source_failures)} 个源失败（{names}）"
        if result.media_warnings:
            # 同上：带上第一条原因，否则「媒体未下载 N」等于没提示
            message += (
                f"　·　媒体未下载 {len(result.media_warnings)}（不影响正文）"
                f"——{result.media_warnings[0][1]}"
            )
        return message

    return await run.io_bound(_work)
