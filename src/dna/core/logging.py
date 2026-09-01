"""
日志配置 / Logging setup.

控制台用 rich 彩色输出便于开发；同时落一份纯文本到 logs/ 供事后排查
（尤其是飞书投递服务在私人电脑上常驻运行时，日志是唯一的排错依据）。
Console output uses rich for readability during development, while a plain-text
copy goes to logs/ — the log file is the only diagnostic available when the
Feishu inbox service runs unattended on the user's personal machine.
"""

from __future__ import annotations

import logging
from pathlib import Path

from rich.logging import RichHandler

_CONFIGURED = False
_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(
    level: str = "INFO",
    log_dir: Path | None = None,
    log_file: str = "dna.log",
    force: bool = False,
) -> logging.Logger:
    """
    初始化全局日志 / Initialise global logging.

    参数 / Args:
        level:    日志级别名，如 "DEBUG" / level name
        log_dir:  日志目录；为 None 则只输出到控制台 / log directory, console-only when None
        log_file: 日志文件名 / log file name
        force:    是否强制重新配置（测试用）/ force reconfiguration, used by tests

    返回 / Returns:
        根 logger / the root "dna" logger
    """
    global _CONFIGURED

    root = logging.getLogger("dna")
    if _CONFIGURED and not force:
        return root

    root.handlers.clear()
    root.setLevel(level.upper())
    root.propagate = False

    console = RichHandler(rich_tracebacks=True, show_path=False, markup=False)
    console.setLevel(level.upper())
    console.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
    root.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
        root.addHandler(file_handler)

    _CONFIGURED = True
    return root


def get_logger(name: str) -> logging.Logger:
    """
    取一个模块级 logger / Get a module-scoped logger.

    统一挂在 "dna" 命名空间下，便于一次性调整全局级别。
    All loggers live under the "dna" namespace so the level can be changed in one place.
    """
    return logging.getLogger(name if name.startswith("dna") else f"dna.{name}")


__all__ = ["get_logger", "setup_logging"]
