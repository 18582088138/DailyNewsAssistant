"""
Stop：这一轮改过哪些包，就只跑那些包的测试。

一轮一次、只跑受影响的包（2.7~15.5s），而不是每次编辑跑一遍，也不是每次都全量 31s。
测试挂了就用退出码 2 顶回去 —— 让模型在**说完成之前**看到红灯，
这是「工具替模型做验证」最省钱的一环。

marker **只在测试通过后才清**。早清一步的代价很贵：被顶回去一次之后，只要不再改 `.py`，
第二次 Stop 就会因为「marker 不存在」直接静默放行 —— 红着的测试无声无息地变成了「完成」。

`stop_hook_active` 为真说明上一次已经顶回去过：这次仍然跑、仍然报告，但不再拦，
否则修不好的用例会把会话锁死在无限重试里。
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
    if not packages:
        marker.unlink()
        return 0

    code, output = run(
        [project_python(), "-m", "pytest", *packages, "-q"], timeout=600
    )
    if code == 0:
        marker.unlink()
        print(f"✔ {' '.join(packages)} 通过", file=sys.stderr)
        return 0

    if event.get("stop_hook_active"):    # 已经顶过一次：照样报告，但放行
        marker.unlink()
        print(f"⚠️ {' '.join(packages)} 仍未通过：\n{tail(output)}", file=sys.stderr)
        return 0
    print(f"{' '.join(packages)} 测试未通过，先修它：\n{tail(output)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
