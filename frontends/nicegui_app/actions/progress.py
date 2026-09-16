"""
后台线程往界面回报进度的那一个写入口 / The single progress writer.

`run` 与 `intake` 都要用它：一个报「第几段音频」，一个报「第几篇文章」。
单独一个文件是为了两边都能 import 而不互相依赖。
"""

from __future__ import annotations


def _progress_writer(progress: dict | None):
    """
    把采集回调接到共享字典上 / Wire the intake callback to the shared dict.

    工作线程只写字典，UI 侧用 `ui.timer` 读——**不能在这里碰任何 NiceGUI 元素**。
    The worker only writes the dict; touching NiceGUI elements here is unsafe.
    """
    if progress is None:
        return None

    def _write(done: int, total: int, title: str) -> None:
        progress.update(done=done, total=total, title=title)

    return _write


__all__ = ["_progress_writer"]
