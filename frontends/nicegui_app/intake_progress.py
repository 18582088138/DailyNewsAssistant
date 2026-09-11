"""
批量采集的进度提示 / The batch-intake progress notice.

为什么不是一句静态的「采集中…」/ Why not a static message:
    一批十几二十篇，每篇要抓正文、下图、可能还要下视频，整趟几分钟起步。
    原来的提示条从头到尾一个字不变，于是「在跑」和「卡住了」长得一模一样，
    而且看不出这一批还剩多少。
    A batch runs for minutes; an unchanging spinner cannot distinguish progress from
    a hang, nor say how much is left.

跨线程的规矩（和 `audio_progress.py` 同一条）/ The threading rule:
    采集跑在 `run.io_bound` 的工作线程里，**不能从那里碰 UI 元素**。
    工作线程只往共享 dict 里写，这里用 `ui.timer` 读。
    The worker writes to a shared dict; this polls it.
"""

from __future__ import annotations

from nicegui import ui

from frontends.nicegui_app import theme


class IntakeProgress:
    """
    一趟采集的进度提示 / One intake run's progress notice.

    用法 / Usage:
        notice = IntakeProgress("从 3 个源采集最近 3 天…")
        ...  # 把 notice.progress 传给 actions.import_*
        notice.close()
    """

    def __init__(self, headline: str, *, interval: float = 0.4) -> None:
        self.progress: dict = {}
        """工作线程写、这里读 / written by the worker, read here."""

        self._headline = headline
        self._notification = ui.notification(headline, spinner=True, timeout=None)
        self._timer = ui.timer(interval, self._tick)

    def _tick(self) -> None:
        total = int(self.progress.get("total") or 0)
        if not total:
            return
        done = int(self.progress.get("done") or 0)
        # 标题只留前几个字：提示条一行放不下整条标题，而人要的只是
        # 「现在轮到哪一篇」——截断反而比换行挤走进度数字好。
        # A few characters is all that fits, and all that is being asked.
        title = theme.short_title(str(self.progress.get("title") or ""), 16)
        self._notification.message = f"{self._headline}　{done}/{total}　·　{title}"

    def close(self) -> None:
        """收工 / Tear down —— 定时器必须停，否则页面开着它就一直在跑。"""
        self._timer.deactivate()
        self._notification.dismiss()


__all__ = ["IntakeProgress"]
