"""
加载 `.claude/hooks/` 下的脚本供测试用。

那个目录不是包、也不在 `sys.path` 上（它是给 Claude Code 直接 `python xxx.py` 跑的），
所以按文件路径加载。`test_dirty.py` 要换个模块名装进来，否则 pytest 会把它当成测试文件。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

HOOK_DIR = Path(__file__).resolve().parents[2] / ".claude" / "hooks"


def _load(filename: str, module_name: str) -> ModuleType:
    if str(HOOK_DIR) not in sys.path:            # hook 之间靠 `from _env import ...` 互相引用
        sys.path.insert(0, str(HOOK_DIR))
    spec = importlib.util.spec_from_file_location(module_name, HOOK_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def guard_bash() -> ModuleType:
    return _load("guard_bash.py", "hook_guard_bash")


@pytest.fixture(scope="session")
def test_dirty() -> ModuleType:
    return _load("test_dirty.py", "hook_test_dirty")
