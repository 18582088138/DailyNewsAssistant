"""
正在执行的任务 / Jobs in flight: 登记表 + 页面级任务坞。

为什么要有任务坞 / Why a page-level dock:
    任务的计时器和进度浮窗原先建在表格行里。任意一个任务结束都会 `on_change()`
    重画表格，把**别的**还在跑的任务的计时器和浮窗一起删掉——并行跑两篇就会
    互相打断。任务坞建在表格之外，重画表格碰不到它；TTS 操作台也挂在这里，
    关掉对话框（点别处）任务照样在后台跑。

登记表按 (文章, 产物, 语言) 去重：同一格重复点「生成」是第二次计费，不是加速。
"""

from __future__ import annotations

import time
from contextlib import nullcontext
from dataclasses import dataclass, field

from nicegui import context, ui

from dna.produce import ProductionKind, spec


@dataclass
class Job:
    article_id: str
    kind: str
    lang: str
    label: str
    audio: bool
    started: float = field(default_factory=time.perf_counter)


# 单人本地工具：和 `_SELECTED` 一样放模块级，多开一个标签页也能看到同一份
_JOBS: dict[tuple[str, str, str], Job] = {}
_DOCKS: dict[str, ui.element] = {}


def begin(article_id: str, kind: ProductionKind | str, lang: str, label: str) -> Job | None:
    """登记一个任务；同一格已经在跑时返回 None / Register, or None if already running。"""
    key = (article_id, str(kind), lang)
    if key in _JOBS:
        return None
    job = Job(article_id, str(kind), lang, label, audio=spec(kind).audio_of is not None)
    _JOBS[key] = job
    return job


def finish(job: Job) -> None:
    _JOBS.pop((job.article_id, job.kind, job.lang), None)


def running_in_cell(article_id: str, kind: ProductionKind | str) -> Job | None:
    """
    这一格上有没有任务在跑 / The job running on a table cell, if any。

    音频没有自己的列，它显示在稿子那一格上（口播音频 → 口播格）。
    """
    for job in _JOBS.values():
        if job.article_id != article_id:
            continue
        task = spec(job.kind)
        if job.kind == str(kind) or (task.audio_of is not None and str(task.audio_of) == str(kind)):
            return job
    return None


def active() -> list[Job]:
    return list(_JOBS.values())


def mount_dock() -> ui.element:
    """在当前页面建任务坞（右下角，进度浮窗一张张往上叠）/ Mount the dock on this page。"""
    dock = ui.column().classes("wb-dock gap-2")
    _DOCKS[context.client.id] = dock
    context.client.on_delete(lambda cid=context.client.id: _DOCKS.pop(cid, None))
    return dock


def anchor():
    """
    任务相关元素的挂载点 / Where job elements are created。

    没有任务坞（离屏测试）时退回当前上下文。
    """
    dock = _DOCKS.get(context.client.id)
    return dock if dock is not None and not dock.is_deleted else nullcontext()


__all__ = ["Job", "active", "anchor", "begin", "finish", "mount_dock", "running_in_cell"]
