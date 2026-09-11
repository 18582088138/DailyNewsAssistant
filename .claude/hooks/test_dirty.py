"""
Stop：这一轮改过哪些包，就只跑那些包的测试。

一轮一次、只跑受影响的包（2.7~15.5s），而不是每次编辑跑一遍，也不是每次都全量 31s。
测试挂了就用退出码 2 顶回去 —— 让模型在**说完成之前**看到红灯，
这是「工具替模型做验证」最省钱的一环。

`stop_hook_active` 为真说明上一次已经顶回去过：这次只报告不拦，否则会卡成死循环。
"""

from __future__ import annotations

import sys

from _env import project_python, read_event, repo_root, run, tail

DIRTY = ".claude/.dirty-packages"


def main() -> int:
    event = read_event()
    marker = repo_root() / DIRTY
    if not marker.exists():
        return 0
    packages = sorted(set(marker.read_text(encoding="utf-8").split()))
    marker.unlink()                      # 先清，避免失败后每轮重复跑同一批
    if not packages:
        return 0

    code, output = run(
        [project_python(), "-m", "pytest", *packages, "-q"], timeout=600
    )
    if code == 0:
        print(f"✔ {' '.join(packages)} 通过", file=sys.stderr)
        return 0

    if event.get("stop_hook_active"):    # 已经顶过一次，不再拦
        print(f"⚠️ {' '.join(packages)} 仍未通过：\n{tail(output)}", file=sys.stderr)
        return 0
    print(f"{' '.join(packages)} 测试未通过，先修它：\n{tail(output)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
