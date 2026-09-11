"""
`_env.py` 的回归测试 —— 两条都是实测咬过人的。

复测 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/hooks -q

1. `relative_to_repo`：ruff 的 per-file-ignores 按相对路径匹配。喂绝对路径时
   `tests/**/*.py = ["S101"]` 整条失效，测试里每个 assert 都被报成红灯 ——
   护栏天天喊狼来了，人就不看红灯了。
2. `package_for`：改了 `.claude/hooks/*` 要能触发 `tests/hooks`，否则护栏改坏了没人发现。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

HOOK_DIR = Path(__file__).resolve().parents[2] / ".claude" / "hooks"


@pytest.fixture(scope="module")
def env() -> ModuleType:
    if str(HOOK_DIR) not in sys.path:
        sys.path.insert(0, str(HOOK_DIR))
    spec = importlib.util.spec_from_file_location("hook_env", HOOK_DIR / "_env.py")
    module = importlib.util.module_from_spec(spec)   # type: ignore[arg-type]
    spec.loader.exec_module(module)                  # type: ignore[union-attr]
    return module


def test_absolute_paths_are_made_relative(env: ModuleType) -> None:
    absolute = str(HOOK_DIR.parent.parent / "tests" / "core" / "test_config.py")
    assert not Path(env.relative_to_repo(absolute)).is_absolute()
    assert env.relative_to_repo(absolute).replace("\\", "/") == "tests/core/test_config.py"


def test_paths_outside_the_repo_are_left_alone(env: ModuleType) -> None:
    """相对化失败时原样返回，别让 hook 因为一个奇怪路径就崩掉整轮。"""
    outside = str(Path(sys.executable).resolve())
    assert env.relative_to_repo(outside) == outside


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/dna/tts/client.py", "tests/tts"),
        ("src/dna/core/config.py", "tests/core"),
        ("frontends/cli/main.py", "tests/frontends"),
        ("frontends/nicegui_app/actions.py", "tests/frontends"),
        (".claude/hooks/guard_bash.py", "tests/hooks"),
        ("tests/store/test_ledger.py", "tests/store"),
        ("tools/check.py", None),
        ("docs/00_INDEX.md", None),
        ("pyproject.toml", None),
    ],
)
def test_edited_file_maps_to_its_test_package(
    env: ModuleType, path: str, expected: str | None
) -> None:
    target = env.repo_root() / path
    assert env.package_for(str(target)) == expected
