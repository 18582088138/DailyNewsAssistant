"""
hook 共用的环境解析 / Shared environment resolution for the hooks.

为什么要解析解释器：PATH 上的 `python` 是 miniforge base（3.13），
项目依赖装在 `ov_env_py312`（3.12）里 —— 用错解释器，pytest 一上来就 import 失败，
而失败信息看起来像「代码坏了」。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
REPO = HOOK_DIR.parent.parent


def repo_root() -> Path:
    """仓库根。优先用 Claude Code 给的环境变量，拿不到就按本文件位置推。"""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env).resolve() if env else REPO


def project_python() -> str:
    """
    跑测试用哪个解释器 / Which interpreter runs the tests.

    顺序：`DNA_PYTHON` 环境变量 → `.claude/hooks/python-path.txt`（本机一行路径，
    不入库）→ PATH 上的 python。**换机器只需写那个 txt**，settings.json 不必改。
    """
    if env := os.environ.get("DNA_PYTHON"):
        return env
    pin = HOOK_DIR / "python-path.txt"
    if pin.exists():
        line = pin.read_text(encoding="utf-8").strip()
        if line and Path(line).exists():
            return line
    return shutil.which("python") or sys.executable


def read_event() -> dict:
    """读 hook 的 stdin JSON；读不出来就当空事件（hook 不该把会话弄挂）。"""
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, OSError):
        return {}


def package_for(path: str) -> str | None:
    """
    改了这个文件，该跑哪个测试包 / Which test package covers this file.

    `src/dna/tts/*` → `tests/tts`；`frontends/*` → `tests/frontends`；
    对不上任何测试目录就返回 None（例如 `tools/`、`docs/`）。
    """
    try:
        rel = Path(path).resolve().relative_to(repo_root())
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if not parts or rel.suffix != ".py":
        return None
    if parts[0] == "tests" and len(parts) > 1:
        candidate = f"tests/{parts[1]}"
    elif parts[:2] == ("src", "dna") and len(parts) > 2:
        candidate = f"tests/{parts[2]}"
    elif parts[0] == "frontends":
        candidate = "tests/frontends"
    else:
        return None
    return candidate if (repo_root() / candidate).is_dir() else None


def run(args: list[str], timeout: float = 300.0) -> tuple[int, str]:
    """跑一个子进程，返回 (退出码, 合并后的输出)。"""
    try:
        done = subprocess.run(
            args, cwd=repo_root(), capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return 1, f"超时（{timeout:.0f}s）：{' '.join(args)}"
    except OSError as exc:
        return 1, f"启动失败：{exc}"
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def tail(text: str, lines: int = 25) -> str:
    """只回最后几行 —— hook 的输出会进模型上下文，全量贴等于白花钱。"""
    kept = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])
