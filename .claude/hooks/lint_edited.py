"""
PostToolUse(Edit|Write)：刚改过的 `.py` 立刻过一遍 ruff，并记下要跑哪个测试包。

为什么只跑 ruff 不跑测试：ruff 单文件 112ms，分包测试 2.7~15.5s。
一轮里会改好几个文件，每次都等测试等于把开发节奏拖垮 —— 测试留到 `Stop`（见 test_dirty.py）。
"""

from __future__ import annotations

import sys

from _env import (
    package_for,
    project_python,
    read_event,
    relative_to_repo,
    repo_root,
    run,
    tail,
)

DIRTY = ".claude/.dirty-packages"


def main() -> int:
    event = read_event()
    path = (event.get("tool_input") or {}).get("file_path") or ""
    if not path.endswith(".py"):
        return 0

    if pkg := package_for(path):
        marker = repo_root() / DIRTY
        seen = set(marker.read_text(encoding="utf-8").split()) if marker.exists() else set()
        if pkg not in seen:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("\n".join(sorted(seen | {pkg})), encoding="utf-8")

    # `--no-cache`：hook 和人手动跑的 `ruff check` 共用 `.ruff_cache`，实测会读到过期条目，
    # 把 per-file-ignores 已经豁免的规则（tests 的 S101、hooks 的 S603）报成红灯，
    # 复查又是绿的。单文件全量分析只要 ~150ms，会误报的护栏比没有护栏更糟。
    # `--no-cache`：hook 与人手动跑的 `ruff check` 共用 `.ruff_cache`，读到过期条目会误报。
    # 单文件全量分析 ~150ms，会喊狼来了的护栏比没有护栏更糟。
    target = relative_to_repo(path)
    code, output = run(
        [project_python(), "-m", "ruff", "check", "--no-cache", target], timeout=60
    )
    if code == 0:
        return 0
    # 退出码 2 = 把这段话喂回模型。只回失败行，别贴 ruff 的完整报告。
    print(f"ruff 不过（{target}）：\n{tail(output, 20)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
