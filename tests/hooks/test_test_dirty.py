"""
`test_dirty.py` 的 marker 生命周期回归测试。

复测 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/hooks -q

被修的 bug：marker 在**跑测试之前**就被删掉。于是第一次被顶回去之后，只要不再改 `.py`，
第二次 Stop 会因为「marker 不存在」直接静默返回 0 —— 红着的测试无声无息地变成「完成」。
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

MARKER = ".claude/.dirty-packages"


@pytest.fixture
def fake_repo(
    tmp_path: Path, test_dirty: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> Path:
    monkeypatch.setattr(test_dirty, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(test_dirty, "project_python", lambda: "python")
    marker = tmp_path / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("tests/core\ntests/tts", encoding="utf-8")
    return tmp_path


def _arrange(
    test_dirty: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    exit_code: int,
    stop_hook_active: bool = False,
) -> list[list[str]]:
    """装上假的 pytest 执行器，返回它收到的 argv 列表（用来断言测试到底跑没跑）。"""
    calls: list[list[str]] = []

    def fake_run(args: list[str], timeout: float = 300.0) -> tuple[int, str]:
        calls.append(args)
        return exit_code, "E   assert 1 == 2\n1 failed" if exit_code else "2 passed"

    monkeypatch.setattr(test_dirty, "run", fake_run)
    monkeypatch.setattr(
        test_dirty, "read_event", lambda: {"stop_hook_active": stop_hook_active}
    )
    return calls


def test_passing_tests_clear_the_marker(
    fake_repo: Path, test_dirty: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _arrange(test_dirty, monkeypatch, exit_code=0)
    assert test_dirty.main() == 0
    assert not (fake_repo / MARKER).exists()
    assert calls[0][2:] == ["pytest", "tests/core", "tests/tts", "-q"]


def test_failing_tests_block_and_keep_the_marker(
    fake_repo: Path,
    test_dirty: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """退出码 2 把红灯喂回模型；marker 留着，否则下一次 Stop 就查不出来了。"""
    _arrange(test_dirty, monkeypatch, exit_code=1)
    assert test_dirty.main() == 2
    assert (fake_repo / MARKER).exists()
    assert "先修它" in capsys.readouterr().err


def test_second_stop_still_runs_the_tests_and_reports(
    fake_repo: Path,
    test_dirty: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    这是那个 bug 的现场：被顶回去一次后不改代码再次 Stop。
    放行可以（不然修不好的用例会把会话锁死），但必须**真的跑**并且**说出来**。
    """
    calls = _arrange(test_dirty, monkeypatch, exit_code=1, stop_hook_active=True)
    assert test_dirty.main() == 0
    assert calls, "第二次 Stop 根本没跑测试 —— 红灯被静默放行了"
    assert "仍未通过" in capsys.readouterr().err
    assert not (fake_repo / MARKER).exists()


def test_no_marker_means_nothing_was_touched(
    tmp_path: Path, test_dirty: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(test_dirty, "repo_root", lambda: tmp_path)
    calls = _arrange(test_dirty, monkeypatch, exit_code=1)
    assert test_dirty.main() == 0
    assert not calls


def test_empty_marker_is_cleaned_up(
    fake_repo: Path, test_dirty: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    (fake_repo / MARKER).write_text("  \n", encoding="utf-8")
    calls = _arrange(test_dirty, monkeypatch, exit_code=1)
    assert test_dirty.main() == 0
    assert not calls
    assert not (fake_repo / MARKER).exists()
