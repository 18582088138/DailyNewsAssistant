"""
TTS 服务的看门人 / Keeping the TTS service alive.

一件事：**用之前先确认服务在线，不在线就把它拉起来，拉不起来就明说不可用。**
One job: make sure the service is up before use, start it if it is not, and say plainly
when it cannot be started.

为什么要自动拉起 / Why it starts the service itself:
    TTS 是一个独立进程（`agentic_tts.cli serve`）。让人「先去开另一个窗口起服务」
    是一条迟早会被忘掉的步骤，而忘掉之后的报错是一个连接错误——看不出该去做什么。
    Requiring a separate terminal is a step that gets forgotten, and what surfaces then
    is a connection error that says nothing about the missing step.

为什么最多三次 / Why three attempts:
    第一次可能是**冷启动慢**（权重加载几十秒）；第二次能排除偶发的端口竞争；
    第三次还不行就不是运气问题，而是环境不对（缺依赖、端口被别的程序占了、
    权重路径错），继续重试只是把一个确定的失败拖长。
    Attempt one may simply be a slow cold start and attempt two rules out a port race;
    a third failure is an environment problem, and retrying only delays the verdict.

⚠️ **只拉本机的服务。** 配了远程地址（4060 那台）时这里不做任何事——
拉起别人机器上的进程既做不到，也不该由这个进程决定。
Only local services are started: a remote address is somebody else's process.

⚠️ **图形界面不是第二个进程。** 它挂在服务的 `/gui` 上，与服务共用一份权重。
先前按两个应用来拉（8300 + 8301），实测各加载一份 1.7B —— 8 GB 卡上顶满，
而且每次点「高级配置」都要等第二个进程冷启动。
The GUI is mounted on the service rather than started separately.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dna.core.config import Settings, get_settings
from dna.core.logging import get_logger
from dna.core.urls import is_local_url
from dna.tts.base import TTSError
from dna.tts.client import TTSServiceClient

logger = get_logger("tts.supervisor")

# 拉起后多久探一次 / how often to probe a starting service
_PROBE_INTERVAL = 1.5

# 已经由本进程拉起来的子进程 / children this process started, keyed by role.
#
# **不在退出时杀掉**：TTS 服务是共用的，工作台关掉之后命令行、别的项目可能还在用它。
# Deliberately not killed at exit: the service is shared with the CLI and other callers.
_STARTED: dict[str, subprocess.Popen] = {}


@dataclass(frozen=True)
class ServiceStatus:
    """
    服务当前的状态 / The service's current state.

    `started_by_us` 要报出来：界面上「刚帮你把服务拉起来了」和「服务本来就在」
    是两句不同的话，后者出现在第一次点合成时会让人以为自己漏了步骤。
    """

    online: bool
    url: str
    started_by_us: bool = False
    attempts: int = 0
    detail: str = ""

    def summary(self) -> str:
        if not self.online:
            return f"TTS 服务不可用（{self.url}）：{self.detail}"
        if self.started_by_us:
            return f"TTS 服务已拉起（{self.url}，第 {self.attempts} 次尝试）"
        return f"TTS 服务在线（{self.url}）"


def ensure_service(
    settings: Settings | None = None, *, client: TTSServiceClient | None = None
) -> ServiceStatus:
    """
    确保 TTS 服务可用 / Ensure the TTS service is usable.

    抛出 / Raises:
        TTSError: 不在线且（不允许自动拉起 / 是远程地址 / 拉起 N 次都失败）

    参数 / Args:
        client: 注入一个客户端（测试用；生产走配置里的地址）
    """
    s = settings or get_settings()
    api = client or TTSServiceClient(s.tts_service_url, timeout=s.tts_request_timeout)

    if api.health():
        return ServiceStatus(True, s.tts_service_url)

    reason = _cannot_start(s)
    if reason:
        raise TTSError(f"TTS 服务不在线（{s.tts_service_url}）：{reason}")

    attempts = max(1, int(s.tts_start_attempts))
    last = ""
    for attempt in range(1, attempts + 1):
        logger.info("拉起 TTS 服务（第 %d/%d 次）…", attempt, attempts)
        try:
            process = _spawn(s, "service", ["serve"], s.tts_service_url)
        except OSError as exc:
            last = f"起不了进程：{exc}"
            logger.warning("拉起失败：%s", last)
            continue

        if _wait_until(api.health, timeout=s.tts_start_timeout, process=process):
            logger.info("TTS 服务已就绪（第 %d 次尝试）", attempt)
            return ServiceStatus(True, s.tts_service_url,
                                 started_by_us=True, attempts=attempt)

        # 起来了但没就绪（或直接退了）：**必须收尸**，否则下一次尝试会撞上占住端口的僵尸
        # Reap it: otherwise the next attempt collides with a zombie holding the port.
        last = _describe_exit(process, s)
        _terminate(process)
        _STARTED.pop("service", None)
        logger.warning("第 %d 次拉起未就绪：%s", attempt, last)

    raise TTSError(
        f"TTS 服务不可用：{s.tts_service_url} 连不上，且自动拉起 {attempts} 次都失败。"
        f"最后一次：{last}　"
        f"手动排查：cd {s.tts_module_dir} && python -m agentic_tts.cli doctor"
    )


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _cannot_start(s: Settings) -> str:
    """能不能自动拉起；不能就回一句给人看的原因 / Why autostart is unavailable."""
    if not s.tts_autostart:
        return "已关闭自动拉起（TTS_AUTOSTART=false），请手动启动服务"
    if not is_local_url(s.tts_service_url):
        return "地址不在本机，无法代为启动；请到那台机器上启动 TTS 服务"
    directory = (s.tts_module_dir or "").strip()
    if not directory:
        return "未配置 TTS_MODULE_DIR（Agent_TTS_Module 的目录），无法代为启动"
    root = Path(directory)
    if not (root / "agentic_tts").is_dir():
        return f"TTS_MODULE_DIR 下没有 agentic_tts/ 子目录：{root}"
    return ""


def _spawn(s: Settings, role: str, args: list[str], url: str) -> subprocess.Popen:
    """
    起一个 TTS 子进程 / Launch one TTS child process.

    日志重定向到文件而不是丢弃：拉起失败时**唯一**能说明原因的东西就在那里
    （缺依赖、端口占用、权重路径错都只在服务自己的输出里）。
    The child's output goes to a file: when a start fails, that is the only place the
    reason exists.
    """
    existing = _STARTED.get(role)
    if existing is not None and existing.poll() is None:
        return existing                     # 已经有一个在起了，别起第二个

    port = urlparse(url).port
    command = [
        (s.tts_python or "").strip() or sys.executable,
        "-m", "agentic_tts.cli", *args,
        "--host", urlparse(url).hostname or "127.0.0.1",
    ]
    if port:
        command += ["--port", str(port)]

    log_dir = s.data_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"tts_{role}.log"
    handle = log_file.open("a", encoding="utf-8", errors="replace")
    handle.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(command)} ===\n")
    handle.flush()

    # Windows 上必须脱离本进程的控制台组：否则工作台被 Ctrl+C 时，
    # 这个共用的 TTS 服务会跟着一起死。
    # On Windows the child must leave this console group, or Ctrl+C here kills the
    # shared service too.
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0
    # 命令由配置组装，非用户输入（S603/S607 在 pyproject 里按文件放行）
    process = subprocess.Popen(
        command,
        cwd=s.tts_module_dir,
        stdout=handle,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    _STARTED[role] = process
    logger.info("已启动 %s（pid %s），日志：%s", role, process.pid, log_file)
    return process


def _wait_until(probe, *, timeout: float, process: subprocess.Popen) -> bool:
    """
    等到探活通过 / Wait until the probe succeeds.

    子进程**先退掉**时立刻返回失败，不等满超时 —— 缺依赖的情况下它一秒就退了，
    再干等两分钟毫无意义。
    A child that has already exited fails fast: waiting out the timeout adds nothing.
    """
    deadline = time.monotonic() + max(5.0, timeout)
    while time.monotonic() < deadline:
        if probe():
            return True
        if process.poll() is not None:
            return False
        time.sleep(_PROBE_INTERVAL)
    return probe()


def _describe_exit(process: subprocess.Popen, s: Settings) -> str:
    """拉起失败时给人一句能查下去的话 / A message that leads somewhere."""
    code = process.poll()
    log_file = s.data_path / "logs"
    if code is None:
        return f"{s.tts_start_timeout:.0f} 秒内没有就绪（进程还活着，可能在加载权重）"
    return f"进程已退出（退出码 {code}），看日志：{log_file}"


def _terminate(process: subprocess.Popen) -> None:
    """结束一个子进程 / End a child process，先礼后兵。"""
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        with contextlib.suppress(OSError):
            process.kill()


__all__ = ["ServiceStatus", "ensure_service"]
