"""
打包后的入口 / The frozen entry point.

PyInstaller 需要一个脚本作为入口，而 `frontends/cli/main.py` 是 Typer 应用——
打包它会得到一个「必须带子命令」的 exe，双击直接打印用法然后退出。
这个文件只做一件事：把 GUI 起起来。
PyInstaller needs a script, and the Typer app would produce an exe that prints usage and
exits when double-clicked. This does one thing: start the GUI.

**不要在这里写业务逻辑。** 它和 `dna gui` 走的是同一个 `run()`。
"""

from __future__ import annotations

import multiprocessing
import sys


def main() -> None:
    # 冻结后的进程必须先调这一句：Windows 上子进程是**重新启动 exe**来创建的,
    # 不调的话 NiceGUI / 线程池派生出的子进程会各自再开一个完整的界面，无限套娃。
    # Required in a frozen app: on Windows a child process is created by re-launching the
    # exe, so without this every spawned child opens another full UI, recursively.
    multiprocessing.freeze_support()

    from frontends.nicegui_app.main import run

    run(host="127.0.0.1", port=8080, show=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 双击启动时没有终端可看回溯
        # 打包后的 windowed 进程崩了是**静默**的：窗口一闪就没了，什么都不留。
        # 至少把原因写到 exe 旁边，否则用户唯一能报的现象是「双击没反应」。
        # A windowed frozen process dies silently, so the reason is written next to the
        # exe: otherwise the only symptom the user can report is "nothing happens".
        from pathlib import Path

        # 文件名用 ASCII：这份日志要在陌生机器上被找到、被念出来、被贴进聊天窗口,
        # 而中文文件名在别的语言环境下可能显示成乱码。
        log = Path(sys.executable).with_name("startup_error.log")
        log.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        raise
