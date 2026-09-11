"""
`guard_bash.py` 的回归测试 —— 三条铁律靠它兑现，此前它自己零测试。

复测 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/hooks -q

覆盖点：
    1. `git commit` / `push` 被拦，**带值的全局 flag 不能成为绕过口**
    2. `git add -A` / `.` 被拦，同样不怕全局 flag
    3. 正常的 `git add <路径>` 不误伤 —— 这是每次提交都要走的路
    4. `pytest -m ''` / `-m live` 被拦，默认的 `-m 'not live and not slow'` 不误伤
"""

from __future__ import annotations

from types import ModuleType

import pytest

# 带值的全局 flag 是这次修复的核心：`(?:-\S+\s+)*` 在 `-c x=y` / `-C /path` 处断链，
# 于是 hook 形同虚设。每一条都是实测能绕过去的真实命令。
BLOCKED_COMMIT = [
    'git commit -m "x"',
    "git push",
    "git push origin DNA_v0.1",
    'git -c commit.gpgsign=false commit -m "x"',
    "git -C /c/repo commit",
    "git -C /c/repo push",
    "git --work-tree=. --git-dir=.git commit",
    "git --no-pager commit",
    'cd /c/repo && git -c user.name=bot commit -m "x"',
]

ALLOWED_COMMIT = [
    "git status --short",
    "git log --oneline -5",
    "git diff .gitignore",
    "git -C /c/repo status",
]

BLOCKED_ADD = [
    "git add -A",
    "git add --all",
    "git add .",
    "git add . ",
    "git -C /c/repo add -A",
    "git -c core.autocrlf=false add --all",
    "git add -v -A",
]

ALLOWED_ADD = [
    "git add tests/",
    "git add docs/issues/",
    "git add .gitignore",
    "git add config/profile.yaml",
    "git add ./tests/hooks",
    "git add src/dna/core/config.py frontends/cli/main.py",
]

BLOCKED_PYTEST = [
    "pytest -m ''",
    'pytest -m ""',
    "pytest -m live",
    'pytest -m "live"',
    "pytest -m live -v tests/llm",
]

ALLOWED_PYTEST = [
    "pytest",
    "pytest -q",
    "pytest tests/core -q",
    "pytest -m 'not live and not slow'",
    'pytest -m "not live and not slow" -v',
]


@pytest.mark.parametrize("command", BLOCKED_COMMIT)
def test_commit_is_blocked_even_behind_a_valued_global_flag(
    guard_bash: ModuleType, command: str
) -> None:
    assert guard_bash.COMMIT.search(command), f"漏网：{command}"


@pytest.mark.parametrize("command", ALLOWED_COMMIT)
def test_read_only_git_commands_pass(guard_bash: ModuleType, command: str) -> None:
    assert not guard_bash.COMMIT.search(command), f"误伤：{command}"


@pytest.mark.parametrize("command", BLOCKED_ADD)
def test_add_all_is_blocked_even_behind_a_valued_global_flag(
    guard_bash: ModuleType, command: str
) -> None:
    assert guard_bash.ADD_ALL.search(command), f"漏网：{command}"


@pytest.mark.parametrize("command", ALLOWED_ADD)
def test_adding_explicit_paths_is_not_blocked(guard_bash: ModuleType, command: str) -> None:
    assert not guard_bash.ADD_ALL.search(command), f"误伤：{command}"


@pytest.mark.parametrize("command", BLOCKED_PYTEST)
def test_billing_pytest_is_blocked(guard_bash: ModuleType, command: str) -> None:
    assert guard_bash.billing_tests(command), f"漏网：{command}"


@pytest.mark.parametrize("command", ALLOWED_PYTEST)
def test_default_pytest_is_not_blocked(guard_bash: ModuleType, command: str) -> None:
    assert not guard_bash.billing_tests(command), f"误伤：{command}"


def test_main_returns_two_and_explains_why(
    guard_bash: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """退出码 2 才会把理由喂回模型；只打印不返回 2 等于没拦。"""
    monkeypatch.setattr(
        guard_bash, "read_event", lambda: {"tool_input": {"command": "git -C . commit -m x"}}
    )
    assert guard_bash.main() == 2
    assert "docs/git_commands.md" in capsys.readouterr().err
