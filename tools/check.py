"""
唯一验收入口 / The single acceptance gate.

    python tools/check.py                 # ruff + 全量 pytest + doctor
    python tools/check.py --fast          # ruff + 只跑 git 改过的那些包
    python tools/check.py --max-lines 500   # 散文占比默认只报不拦，见 PROSE_BUDGET
    python tools/check.py --prose-ratio 40  # 显式传才当闸门用
    python tools/check.py --list          # 现算测试清单（文档里不再手抄数字）

**报告完成之前必须跑它。** 「我觉得没问题」不是验收，退出码才是。
每一项都打印耗时，因为「哪一步慢」决定下次该怎么跑（实测：ruff 单文件 0.1s、
分包测试 2.7~15.5s、全量 31s）。
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import time
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CODE_DIRS = ("src", "frontends", "tools")
FAIL_RE = re.compile(r"(\d+)\s*FAIL")

# 散文（注释 + docstring）占非空行的参考线 / the prose-ratio reference line
#
# **只报不拦**：它拦不住真正该拦的东西。这个项目里最贵的 bug 全是「不报错，
# 只是不对」（配置被静默忽略、字数预算少要三分之一、重做拿回旧答案），
# 记着那些「为什么」的注释正是防止它们被重新引入的东西 —— 把比例当硬门，
# 第一个被删的就是它们。40 是按当前存量（约 38%）留一点余量定的：
# 新代码里散文失控会顶上来，而不要求回头删已经写下的根因记录。
PROSE_BUDGET = 40


def python_exe() -> str:
    """跑子进程用哪个解释器：优先 hook 里钉好的那个，否则就是当前这个。"""
    pin = REPO / ".claude" / "hooks" / "python-path.txt"
    if pin.exists():
        line = pin.read_text(encoding="utf-8").strip()
        if line and Path(line).exists():
            return line
    return sys.executable


def run(label: str, args: list[str]) -> tuple[bool, str]:
    start = time.monotonic()
    done = subprocess.run(
        args, cwd=REPO, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    ok = done.returncode == 0
    print(f"{'✔' if ok else '✘'} {label}  {time.monotonic() - start:.1f}s")
    return ok, (done.stdout or "") + (done.stderr or "")


# --- 各项检查 / the checks ----------------------------------------------------


def check_ruff() -> tuple[bool, str]:
    return run("ruff", [python_exe(), "-m", "ruff", "check", *CODE_DIRS])


def check_tests(packages: list[str] | None) -> tuple[bool, str]:
    targets = packages or ["tests"]
    return run(f"pytest {' '.join(targets)}", [python_exe(), "-m", "pytest", *targets, "-q"])


def check_doctor() -> tuple[bool, str]:
    ok, output = run("dna doctor", [python_exe(), "-m", "frontends.cli.main", "doctor"])
    fails = [int(m) for m in FAIL_RE.findall(output)]
    if ok and fails and max(fails) > 0:
        return False, output
    return ok, output


def file_stats(path: Path) -> tuple[int, int, int]:
    """(总行, 非空行, 散文行)。散文 = 注释行 + docstring 行。"""
    source = path.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    non_blank = sum(1 for ln in lines if ln.strip())
    prose: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                prose.add(token.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return len(lines), non_blank, len(prose)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        doc = ast.get_docstring(node, clean=False)
        if doc is None or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and first.end_lineno:
            prose.update(range(first.lineno, first.end_lineno + 1))
    # 模块级的「裸字符串当注释」（本项目大量用在常量下面）也算散文
    return len(lines), non_blank, len(prose)


def code_files() -> list[Path]:
    return sorted(
        p for d in CODE_DIRS for p in (REPO / d).rglob("*.py")
        if "__pycache__" not in p.parts
    )


def check_max_lines(limit: int) -> tuple[bool, str]:
    over = [(p, n) for p in code_files() if (n := len(p.read_text(
        encoding="utf-8", errors="replace").splitlines())) > limit]
    ok = not over
    print(f"{'✔' if ok else '✘'} 行数上限 {limit}")
    detail = "\n".join(
        f"  {p.relative_to(REPO).as_posix()}  {n} 行" for p, n in sorted(
            over, key=lambda x: -x[1]))
    return ok, detail


def check_prose(limit: int, *, enforce: bool = True) -> tuple[bool, str]:
    """
    注释 + docstring 占非空行的比例。

    默认**只报不拦**（见 `PROSE_BUDGET`）。显式传 `--prose-ratio N` 才当闸门用。
    """
    rows = [(p, *file_stats(p)) for p in code_files()]
    non_blank = sum(r[2] for r in rows) or 1
    prose = sum(r[3] for r in rows)
    ratio = prose * 100 / non_blank
    ok = ratio <= limit
    mark = "✔" if ok else ("✘" if enforce else "⚠")
    suffix = "" if enforce else "，只报不拦"
    print(f"{mark} 散文占比 {ratio:.1f}%（参考线 {limit}%{suffix}）")
    worst = sorted(
        ((p, pr * 100 / (nb or 1)) for p, _, nb, pr in rows if nb >= 150),
        key=lambda x: -x[1])[:10]
    detail = "\n".join(
        f"  {p.relative_to(REPO).as_posix()}  {r:.0f}%" for p, r in worst)
    if not ok and not enforce:
        print(detail)
    return ok, detail


def changed_packages() -> list[str]:
    """git 改过的文件落在哪些测试包里 —— `--fast` 用它决定跑哪些测试。"""
    done = subprocess.run(["git", "status", "--porcelain"], cwd=REPO,
                          capture_output=True, text=True)
    packages: set[str] = set()
    for line in done.stdout.splitlines():
        rel = line[3:].strip().strip('"')
        parts = Path(rel).parts
        if not rel.endswith(".py") or not parts:
            continue
        if parts[0] == "tests" and len(parts) > 1:
            candidate = f"tests/{parts[1]}"
        elif parts[:2] == ("src", "dna") and len(parts) > 2:
            candidate = f"tests/{parts[2]}"
        elif parts[0] == "frontends":
            candidate = "tests/frontends"
        else:
            continue
        if (REPO / candidate).is_dir():
            packages.add(candidate)
    return sorted(packages)


def list_tests() -> None:
    """现算测试清单 —— 文档里写死数字就会腐烂（实测 15 处已过期）。"""
    total = 0
    for path in sorted((REPO / "tests").rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        count = sum(
            1 for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")
        )
        total += count
        print(f"{count:>4}  {path.relative_to(REPO).as_posix()}")
    print(f"{total:>4}  合计（{len(list((REPO / 'tests').rglob('test_*.py')))} 个文件）")


# --- 入口 / entry -------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="DNA 验收门")
    parser.add_argument("--fast", action="store_true", help="只跑 git 改过的包")
    parser.add_argument("--all", action="store_true", help="全量（默认行为）")
    parser.add_argument("--no-lint", action="store_true", help="跳过 ruff")
    parser.add_argument("--max-lines", type=int, help="单文件行数上限")
    parser.add_argument("--prose-ratio", type=int,
                        help=f"把散文占比当闸门，上限 %% （默认 {PROSE_BUDGET}%% 只报不拦）")
    parser.add_argument("--list", action="store_true", help="打印测试清单后退出")
    args = parser.parse_args()

    if args.list:
        list_tests()
        return 0

    failures: list[tuple[str, str]] = []

    if not args.no_lint:
        ok, detail = check_ruff()
        if not ok:
            failures.append(("ruff", detail))

    packages = changed_packages() if args.fast else None
    if args.fast and not packages:
        print("· 没有改动的 python 文件，跳过测试")
    else:
        ok, detail = check_tests(packages)
        if not ok:
            failures.append(("pytest", detail))

    ok, detail = check_doctor()
    if not ok:
        failures.append(("doctor", detail))

    if args.max_lines:
        ok, detail = check_max_lines(args.max_lines)
        if not ok:
            failures.append(("行数上限", detail))
    if args.prose_ratio:
        ok, detail = check_prose(args.prose_ratio)
        if not ok:
            failures.append(("散文占比", detail))
    else:
        check_prose(PROSE_BUDGET, enforce=False)

    if not failures:
        print("\n全部通过。")
        return 0
    print(f"\n{len(failures)} 项未通过：")
    for name, detail in failures:
        print(f"\n--- {name} ---")
        print("\n".join(detail.splitlines()[-25:]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
