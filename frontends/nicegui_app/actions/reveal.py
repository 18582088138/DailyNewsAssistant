"""在系统文件管理器里打开目录 / Revealing a folder in the OS file manager。

纯 OS 集成，和业务无关；单独一个文件是因为它带着一堆 Windows 前台窗口的
琐碎处置（`ShowWindow` / `SetForegroundWindow` / 让权），混在动作里很吵。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from nicegui import app

from dna.core.logging import get_logger

logger = get_logger("gui.actions")

def open_in_file_manager(path: str | Path) -> str:
    """
    在系统文件管理器里打开文件或目录 / Reveal a file or directory in the OS file manager.

    返回空串表示成功，否则返回给人看的失败原因。
    Returns an empty string on success, or a human-readable reason.

    **接受文件，不只是目录。** Windows 下 `os.startfile` 对 `article.md` 会用默认
    编辑器打开它——「点正文栏打开对应的文件」要的正是这个行为。先前这里写着
    `is_dir()`，于是正文格只能开目录、开不了文件。
    Files are accepted, not only directories: `os.startfile` opens `article.md` in the
    default editor, which is exactly what clicking the body cell should do.

    **只在服务端与浏览器同机时有意义。** 工作台默认绑 127.0.0.1，两者本来就是同一台
    机器；一旦有人把它绑到 0.0.0.0 给别人访问，这个按钮会在**服务器**上弹出窗口，
    点的人什么也看不到。所以这里显式检查绑定地址，不是本机就拒绝并说明原因。
    Only meaningful when the server and browser are the same machine. The workbench binds
    to 127.0.0.1 by default, where they are; if someone rebinds it to 0.0.0.0 for others
    to reach, this would pop a window on the *server* and the clicker would see nothing.
    The bind address is therefore checked explicitly.
    """
    target = Path(path)
    if not target.exists():
        return f"路径不存在：{target}"

    if not _server_is_local():
        return "工作台没有绑定在本机，无法打开你这边的文件管理器"

    try:
        if sys.platform == "win32":
            _open_on_windows(target)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as exc:
        return f"打不开：{exc}"
    return ""


def _server_is_local() -> bool:
    """服务端是否绑在本机 / Whether the server is bound to loopback."""
    host = str((getattr(app, "config", None) and getattr(app.config, "host", "")) or "")
    return host in {"", "127.0.0.1", "localhost", "::1"}


def _open_on_windows(target: Path) -> None:
    """
    Windows 下打开并尽量顶到最前 / Open on Windows and try to raise it to the front.

    为什么要额外做一步 / Why the extra step:
        抢到前台权的是浏览器，我们这个 Python 进程没有——Windows 于是拒绝激活
        资源管理器，只让它在任务栏闪一下，看起来就是「被浏览器盖住了」。
        `AllowSetForegroundWindow(ASFW_ANY)` 把这一次的前台权让出去，
        再由后面那个线程把窗口顶上来。
        The browser owns the foreground right, not this process, so Windows refuses to
        activate Explorer and only flashes it in the taskbar.

    **任何一步失败都退回原来的行为**：窗口照样开，只是可能在后面。
    提示「打不开」会是假的。
    """
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception as exc:
        logger.debug("放行前台权失败，窗口可能开在后面：%s", exc)

    if not target.is_dir():
        # 文件仍走 startfile：它按扩展名挑默认程序，
        # 「点正文格用编辑器打开 article.md」这条行为不能变成「打开所在目录」。
        # 路径来自本地台账，不是用户输入 / from the local ledger, not user input
        os.startfile(target)
        return

    subprocess.Popen(["explorer", os.path.normpath(str(target))])
    # 开窗要时间，不能同步等：这个函数跑在界面的事件循环上，等两秒就是卡两秒。
    threading.Thread(target=_raise_explorer, args=(target,), daemon=True).start()


def _raise_explorer(target: Path, *, timeout: float = 2.0) -> None:
    """把刚开的资源管理器窗口顶到最前 / Bring the new Explorer window to the front."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        wanted = {target.name.lower(), os.path.normpath(str(target)).lower()}
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _visit(hwnd, _lparam):  # pragma: no cover - 要真的有窗口才会进来
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value not in {"CabinetWClass", "ExploreWClass"}:
                return True
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            if title.value.lower() in wanted:
                found.append(hwnd)
                return False
            return True

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not found:
            user32.EnumWindows(_visit, 0)
            if found:
                break
            time.sleep(0.1)

        if not found:
            return
        hwnd = found[0]
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE，窗口可能是最小化状态
        if user32.SetForegroundWindow(hwnd):
            return
        # 还是不给：把自己的线程挂到前台线程的输入队列上再试一次，这是
        # Windows 唯一还认的一条路（前台窗口所属线程可以替别人做主）。
        target_tid = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        own_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if user32.AttachThreadInput(own_tid, target_tid, True):
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(own_tid, target_tid, False)
    except Exception:
        logger.debug("raise explorer failed", exc_info=True)
