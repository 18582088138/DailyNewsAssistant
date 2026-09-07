"""
音频合成的进度浮窗 / The synthesis progress panel.

为什么值得单独一个组件 / Why this is not just a notification:
    合成一条口播音频在 CPU 上要几分钟，长文案要几十分钟。这段时间里界面必须
    **一直有东西在动**，而且要能回答三个问题：还在跑吗、跑到哪了、大概还要多久。
    一个静止的转圈图标只能回答第一个，而且回答得并不可信 —— 卡死的界面上转圈
    照样在转（那是 CSS 动画，不是程序还活着的证据）。
    A spinner answers only the first question, and answers it unreliably: the animation
    keeps turning on a frozen page because it is CSS, not evidence of life.

所以这里显示的是 / What it shows instead:
    · **秒表**：每 0.5 秒自己走一格 —— 数字在变就说明事件循环还活着
    · **进度条**：按「已完成段 / 总段」推进，这是真实进展
    · **已产出秒数**：音频在长出来，比百分比更有说服力
    A stopwatch proves the event loop is alive; the bar shows real progress.

跨线程的规矩 / The threading rule:
    合成跑在 `run.io_bound` 的工作线程里，**不能从那里碰 UI 元素**。
    工作线程只往一个共享 dict 里写，这个浮窗用 `ui.timer` 读。
    The worker writes to a shared dict; this panel polls it. Touching UI elements from
    the worker thread is unsafe.
"""

from __future__ import annotations

import time

from nicegui import ui


class AudioProgress:
    """
    一次合成的进度浮窗 / One synthesis run's progress panel.

    用法 / Usage:
        panel = AudioProgress("口播音频", expected_seconds=240)
        ...  # 工作线程往 panel.progress 里写 done/total/seconds
        panel.close()
    """

    def __init__(self, label: str, *, expected_seconds: float = 0.0,
                 hint: str = "", on_cancel=None, action: str = "合成中") -> None:
        self.progress: dict = {}
        """工作线程写、这里读 / written by the worker, read here."""

        self._label = label
        self._expected = max(0.0, expected_seconds)
        self._started = time.perf_counter()

        # 右下角固定，不挡表格 / pinned bottom-right, clear of the table
        self._card = ui.card().style(
            "position: fixed; right: 18px; bottom: 18px; z-index: 3000; width: 320px;"
            "background: var(--wb-panel); border: 1px solid var(--wb-line-strong);"
            "box-shadow: 0 8px 28px rgba(0,0,0,.45)"
        ).classes("p-3 gap-2")

        with self._card:
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.spinner("audio", size="sm", color="green")
                ui.label(f"{label} {action}").classes("text-sm").style(
                    "color: var(--wb-accent)"
                )
                ui.space()
                self._clock = ui.label("0:00").classes("wb-path")
                # 取消只在**等别人干活**时给：自己这条合成已经在跑了，
                # 半路掐掉只会留下一段废音频。
                # Cancelling is offered only while waiting on someone else.
                if on_cancel is not None:
                    ui.button(icon="close", on_click=on_cancel) \
                        .props("flat dense round size=sm").tooltip("停止等待")

            self._bar = ui.linear_progress(value=0.0, show_value=False, size="6px")
            self._bar.props("color=green track-color=grey-9")

            self._detail = ui.label("正在连接 TTS 服务…").classes("wb-path")
            if not hint:
                hint = "服务不在线会自动拉起（首次要等权重加载）"
                if self._expected >= 60:
                    hint = f"预计 {self._expected / 60:.0f} 分钟左右；" + hint
            ui.label(hint).classes("wb-path").style("color: var(--wb-faint)")

        self._timer = ui.timer(0.5, self._tick)

    # ------------------------------------------------------------------ 内部

    def _tick(self) -> None:
        """
        刷一次界面 / Repaint once.

        进度**只有在服务报了段数之后**才画成确定进度；在那之前保持
        不确定态（`indeterminate`），而不是画一个 0% —— 0% 会被读成「卡在开头」。
        Indeterminate until the service reports piece counts: a 0 % bar reads as stuck.
        """
        elapsed = time.perf_counter() - self._started
        self._clock.set_text(f"{int(elapsed) // 60}:{int(elapsed) % 60:02d}")

        done, total = self.progress.get("done"), self.progress.get("total")
        if not total:
            self._bar.props("indeterminate")
            return

        self._bar.props(remove="indeterminate")
        self._bar.set_value(min(1.0, done / total))
        seconds = self.progress.get("seconds", 0.0)
        self._detail.set_text(
            f"第 {done}/{total} 段　已产出 {seconds:.0f} 秒音频"
            + (f"　剩约 {self._remaining(done, total, elapsed) / 60:.0f} 分钟"
               if done else "")
        )

    def _remaining(self, done: int, total: int, elapsed: float) -> float:
        """
        按**实测速度**推剩余时间 / Extrapolate from measured speed.

        不用常量 RTF：那个值是别的机器上量的，而这里手上已经有本次的真实速度了。
        Not from the constant: the real rate for this run is already in hand.
        """
        return max(0.0, elapsed / max(1, done) * (total - done))

    # ------------------------------------------------------------------ 对外

    def close(self) -> None:
        """收掉浮窗 / Remove the panel（`finally` 里调，异常路径也要收）。"""
        self._timer.deactivate()
        self._card.delete()


__all__ = ["AudioProgress"]
