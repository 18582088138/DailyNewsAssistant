"""
文档验收门 / The docs gate.

    python tools/check_docs.py

查三件事，每一件都是实测踩过的坑：
    1. **断链** —— `docs/00_STAGE_SUMMARY.md` 曾有 5 条链接漏掉 `issues/` 前缀。
    2. **冻结数字** —— 「813 passed」「1060 行」这类手抄的数字；实测架构文档
       45 条行数声明里 19 条已经错。数字要么现算（`tools/check.py --list`），要么别写。
    3. **导览缺条目** —— 每份 `docs/*.md` 都得在 `00_INDEX.md` 里有一行，
       否则没人知道它什么时候该被加载。

`git_commands.md` 与 `issues/` 不查冻结数字：前者是待执行的命令、后者是历史记录，
里面的实测数字**就是它们的价值**。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
INDEX = DOCS / "00_INDEX.md"

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
FENCE = re.compile(r"^\s*```")
# 反引号里的 `docs/04_architecture.md` 不是 markdown 链接，LINK 抓不到 ——
# 实测正是这种写法藏住了一条指向不存在文件的引用。
TICKED = re.compile(r"`([^`\n]+)`")
# 只认 docs 自己的命名约定（`docs/x.md`、`04_architecture.md`、`issues/007-x.md`）——
# 产物文件也叫 .md（`article.md`、`summary.zh.md`、`_references.md`），不能一起查
DOC_REF = re.compile(r"^(?:docs/)?(?:issues/\d{3}-[\w\-]+|\d{2}_[\w\-]+)\.md$")
# 占位符不是引用：`docs/issues/NNN-*.md` 是在描述命名规则
PLACEHOLDER = re.compile(r"NNN|\*|<|>")
FROZEN = (
    re.compile(r"\d+\s*passed"),
    re.compile(r"\d+\s*行(?!业|为|动|情)"),      # 「N 行」——排除「行业/行为/行动/行情」
    re.compile(r"\d+\s*OK\b"),
    re.compile(r"\d+\s*个命令"),
)
# 冻结数字检查的豁免：命令汇总是一次性的，issues 是历史记录
FROZEN_SKIP = {"git_commands.md"}


def markdown_files() -> list[Path]:
    return sorted(DOCS.glob("*.md")) + sorted(DOCS.glob("issues/*.md"))


def outside_code(text: str):
    """逐行产出 (行号, 内容)，跳过围栏代码块里的行。"""
    inside = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            inside = not inside
            continue
        if not inside:
            yield number, line


def check_links() -> list[str]:
    problems = []
    for path in markdown_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in outside_code(text):
            for target in LINK.findall(line):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                clean = target.split("#")[0].strip()
                if not clean:
                    continue
                if not (path.parent / clean).exists():
                    problems.append(
                        f"断链 {path.relative_to(REPO).as_posix()}:{number} → {target}")
            problems.extend(_ticked_refs(path, number, line))
    return problems


def _ticked_refs(path: Path, number: int, line: str) -> list[str]:
    """反引号里写成纯文本的文档路径，同样要指得到东西。"""
    problems = []
    for ref in TICKED.findall(line):
        ref = ref.strip()
        if not DOC_REF.match(ref) or PLACEHOLDER.search(ref):
            continue
        # `docs/x.md` 从仓库根算，`x.md` 从本文件所在目录算
        base = REPO if ref.startswith("docs/") else path.parent
        if not (base / ref).exists():
            problems.append(
                f"断链（反引号）{path.relative_to(REPO).as_posix()}:{number} → {ref}")
    return problems


def check_frozen() -> list[str]:
    problems = []
    for path in markdown_files():
        if path.name in FROZEN_SKIP or path.parent.name == "issues":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in outside_code(text):
            for pattern in FROZEN:
                if match := pattern.search(line):
                    problems.append(
                        f"冻结数字 {path.relative_to(REPO).as_posix()}:{number} "
                        f"「{match.group(0)}」——改成现算或删掉")
                    break
    return problems


def check_index() -> list[str]:
    if not INDEX.exists():
        return [f"缺导览：{INDEX.relative_to(REPO).as_posix()}"]
    text = INDEX.read_text(encoding="utf-8", errors="replace")
    problems = []
    for path in sorted(DOCS.glob("*.md")):
        if path.name in {INDEX.name, "git_commands.md"}:
            continue
        if path.name not in text:
            problems.append(f"导览里没有 {path.name}")
    return problems


def main() -> int:
    groups = [("链接", check_links()), ("冻结数字", check_frozen()),
              ("导览", check_index())]
    total = 0
    for name, problems in groups:
        print(f"{'✔' if not problems else '✘'} {name}（{len(problems)} 项）")
        for line in problems[:40]:
            print(f"    {line}")
        if len(problems) > 40:
            print(f"    …另有 {len(problems) - 40} 项")
        total += len(problems)
    print("\n文档门通过。" if not total else f"\n共 {total} 项待修。")
    return 0 if not total else 1


if __name__ == "__main__":
    sys.exit(main())
