"""
语言哨兵的静态把关 / The language sentinel, checked statically.

    $PY -m pytest tests/produce/test_lang_sentinel.py -q

`lang: str = ""` 里那个空串是**哨兵**，意思是「按 `.env` 的 `DEFAULT_LANGUAGE`」，
它本身不是一个合法的语言码。库里的 `productions.lang` 只有 `zh` / `en`，
`LANGUAGE_LABELS` 也只有这两个键。于是哨兵漏到下游就会炸或者静默出错：

- 当字典键：`row.production(kind)` 查不到 → **整张表显示成「未生成」**，完全静默
- 当下标：`LANGUAGE_LABELS[lang]` → `KeyError: ''`，展开面板直接打不开
- 跟默认语言比：`lang == default_language()` → 中文版被标成「（EN）」

这三种上一次同时发生过：`DEFAULT_LANGUAGE` 从写死的常量改成读配置的
`default_language()` 时，所有默认参数被换成了 `""`，而**只有一部分函数**
在入口归一化。渲染冒烟测试当时是绿的——它构造的 `RowView` 产物字典是空的，
根本走不到查表那一步。

所以规矩是**一刀切**的，好让它能被这一个测试查出来：
凡是 `lang` 参数默认为 `""` 的函数，入口必须有 `lang = normalize_lang(lang)`。
多归一化一次是幂等的、代价是一次字典查找；漏一次是一个静默的错。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCANNED = (ROOT / "src" / "dna", ROOT / "frontends")

# 哨兵的定义处本身不受这条规矩管：它就是那个归一化函数
EXEMPT = {("tasks.py", "normalize_lang")}


def _functions_with_sentinel() -> list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """找出所有 `lang` 默认为空串的函数 / Every function whose `lang` defaults to ""."""
    found = []
    for base in SCANNED:
        for path in base.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if (path.name, node.name) in EXEMPT:
                    continue
                if _lang_defaults_to_empty(node.args):
                    found.append((path, node))
    return found


def _lang_defaults_to_empty(args: ast.arguments) -> bool:
    """`lang` 这个参数的默认值是不是空串。"""
    pairs = list(
        zip(args.args[len(args.args) - len(args.defaults):], args.defaults, strict=True)
    ) + list(zip(args.kwonlyargs, args.kw_defaults, strict=True))
    for arg, default in pairs:
        if arg.arg != "lang" and not arg.arg.endswith("_lang"):
            continue
        if isinstance(default, ast.Constant) and default.value == "":
            return True
    return False


def _normalises_on_entry(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """函数体里有没有 `lang = normalize_lang(...)` 这种赋值。"""
    for stmt in ast.walk(node):
        if not isinstance(stmt, ast.Assign):
            continue
        targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
        if not any(t == "lang" or t.endswith("_lang") for t in targets):
            continue
        call = stmt.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "normalize_lang"
        ):
            return True
    return False


def test_至少扫到了这些函数() -> None:
    """守住这个测试自己：AST 匹配写错了会「一个都没扫到」而照样绿。"""
    found = _functions_with_sentinel()
    assert len(found) >= 10, f"只扫到 {len(found)} 个，匹配逻辑可能已经失效"


@pytest.mark.parametrize(
    "path,func",
    [(p, n) for p, n in _functions_with_sentinel()],
    ids=lambda x: x.name if isinstance(x, Path) else getattr(x, "name", str(x)),
)
def test_哨兵必须在入口归一化(path: Path, func) -> None:
    assert _normalises_on_entry(func), (
        f"{path.relative_to(ROOT)}::{func.name} 的 lang 默认是哨兵空串，"
        "但函数体里没有 `lang = normalize_lang(lang)`。"
        "空串不是语言码：当字典键查不到、当 LANGUAGE_LABELS 的下标直接 KeyError。"
    )
