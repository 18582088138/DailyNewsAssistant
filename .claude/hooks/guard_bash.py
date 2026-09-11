"""
PreToolUse(Bash)：拦住三类不可逆或会花钱的命令。

这三条都是项目铁律，写在文档里靠自觉，写在这里靠退出码：
    1. `git commit` / `git push` —— AI 不提交，命令汇总交给人执行
    2. `pytest -m ''` / `-m live` —— 真调云端 API，**真花钱**
    3. `git add -A` / `git add .` —— `.env`（密钥）与 `outputs/`（328MB）就在旁边

注意 `-m "not live and not slow"` 是默认值，**不能拦**；所以只看 `-m` 的取值里
有没有「不带 not 的 live」。
"""

from __future__ import annotations

import re
import sys

from _env import read_event

# git 的全局 flag 分两种：自封闭的（`--no-pager`）和**值是独立 token** 的（`-c x=y`、
# `-C /path`）。只写 `(?:-\S+\s+)*` 会在后者处断链 —— `git -C /path commit` 就溜过去了，
# 而铁律的整个可信度就挂在这条正则上。带值的必须显式列出来，且要排在通用分支前面。
_GLOBAL_FLAG = (
    r"(?:"
    r"-[cC]\s+\S+"
    r"|--(?:git-dir|work-tree|namespace|super-prefix|config-env|exec-path)[ =]\S+"
    r"|-\S+"
    r")\s+"
)
COMMIT = re.compile(rf"\bgit\s+(?:{_GLOBAL_FLAG})*(commit|push)\b")
# `(?:-\S+\s+)*?` 非贪婪：允许 `git add -v -A`，但不跨过路径参数去够后面命令里的点号。
ADD_ALL = re.compile(
    rf"\bgit\s+(?:{_GLOBAL_FLAG})*add\s+(?:-\S+\s+)*?(?:-A\b|--all\b|\.(?:\s|$))"
)
DASH_M = re.compile(r"-m\s*(?:'([^']*)'|\"([^\"]*)\"|(\S+))")


def billing_tests(command: str) -> bool:
    """这条 pytest 会不会真调 API。"""
    if "pytest" not in command:
        return False
    for match in DASH_M.finditer(command):
        value = next((g for g in match.groups() if g is not None), "")
        if not value.strip():
            return True                                  # -m '' 把 live 也放出来
        if re.search(r"(?<!not )\blive\b", value):
            return True
    return False


def main() -> int:
    command = ((read_event().get("tool_input") or {}).get("command") or "")
    if COMMIT.search(command):
        print("本项目 AI 不执行 git commit / push：把命令写进 docs/git_commands.md，"
              "由人工执行（覆写前先 git log 核对上一组已执行）。", file=sys.stderr)
        return 2
    if ADD_ALL.search(command):
        print("禁止 git add -A / git add .：.env 有密钥、outputs/ 有几百 MB 产物。"
              "请逐个写明路径。", file=sys.stderr)
        return 2
    if billing_tests(command):
        print("这条 pytest 会跑 live 用例，真调云端 API 并产生费用。"
              "默认的 `-m 'not live and not slow'` 已在 pyproject 里配好，直接 `pytest` 即可。",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
