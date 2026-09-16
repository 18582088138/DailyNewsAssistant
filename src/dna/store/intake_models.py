"""
入库的结果类型与进度回调 / The intake result and progress callback.

`intake`（四个公开入口）与 `intake_engine`（共用的那一段）都要用它们，
所以单独一个文件 —— 放在任何一边都会让两个模块互相 import。

计数必须能对上账：`collected = filtered_out + skipped_existing + processed`。
用户看到「今天只入了 6 条」时，应当能立刻知道其余的去哪了。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# 采集进度回调 `(第几篇, 共几篇, 这一篇的标题)` / progress callback
#
# 一批抓下来要几分钟，而界面上原来只有一个不动的转圈——「在跑」和「卡死了」
# 长得一模一样。回调在**每篇抓取之前**触发，报的是正在处理的那一篇。
# A batch takes minutes and a static spinner cannot distinguish running from hung.
IntakeProgressFn = Callable[[int, int, str], None]


@dataclass
class IntakeResult:
    """
    一轮入库的结果 / The outcome of one intake run.

    每一类计数都要能对上账：collected = filtered + skipped_existing + processed。
    用户看到「今天只入了 6 条」时，应当能立刻知道其余的去哪了。
    The counts must reconcile: collected = filtered + skipped_existing + processed.
    When only six items are ingested the user should immediately see where the rest went.
    """

    collected: int = 0
    filtered_out: int = 0
    skipped_existing: int = 0
    fetched_ok: int = 0
    fetched_degraded: int = 0
    failed: int = 0
    article_ids: list[str] = field(default_factory=list)
    filter_reasons: dict[str, int] = field(default_factory=dict)
    source_failures: list[tuple[str, str]] = field(default_factory=list)
    media_warnings: list[tuple[str, str]] = field(default_factory=list)
    """
    抓不下来的媒体文件 /media that could not be downloaded：`(article_id, 一行原因)`。

    **不算失败**：正文已经入库，少一个视频不影响这篇文章可用。单独拎出来是因为
    它以前只写进了 `references.md`，界面上完全看不见——人无从知道该去手工补哪一个。
    Not counted as a failure: the article is in and usable without the clip. It is
    surfaced separately because it previously only reached `references.md`, leaving the
    user no way to know which one needs fetching by hand.
    """

    @property
    def processed(self) -> int:
        """本轮实际抓取的条数 / Items actually fetched this run."""
        return self.fetched_ok + self.fetched_degraded + self.failed

    def summary(self) -> str:
        """一行摘要 / A one-line summary."""
        parts = [f"采集 {self.collected}"]
        if self.filtered_out:
            parts.append(f"过滤 {self.filtered_out}")
        if self.skipped_existing:
            parts.append(f"已存在跳过 {self.skipped_existing}")
        parts.append(f"新入库 {self.fetched_ok + self.fetched_degraded}")
        if self.fetched_degraded:
            parts.append(f"（其中降级 {self.fetched_degraded}）")
        if self.failed:
            parts.append(f"失败 {self.failed}")
        if self.media_warnings:
            parts.append(f"媒体未下载 {len(self.media_warnings)}")
        return "，".join(parts)


__all__ = ["IntakeProgressFn", "IntakeResult"]
