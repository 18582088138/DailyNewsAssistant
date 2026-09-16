"""
`tools/check_docs.py` 的反引号断链检查。

    $PY -m pytest tests/tools/test_check_docs.py -q

由来：`02_development_plan.md` 曾用反引号纯文本引用 `docs/04_architecture.md`（单数，
不存在）。原来的 LINK 正则只认 `[x](y)`，这条断链藏了很久没人发现。
反过来，产物文件也叫 `.md`（`article.md`、`summary.zh.md`、`_references.md`），
它们不在 docs/ 里，查它们只会天天误报 —— 护栏一喊狼来了，人就不看红灯了。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import check_docs


def _flag(ref: str) -> bool:
    """这条反引号引用会不会被报成断链。"""
    doc = check_docs.DOCS / "00_INDEX.md"
    return bool(check_docs._ticked_refs(doc, 1, f"见 `{ref}` 那一份"))


def test_不存在的文档引用会被抓到():
    assert _flag("docs/04_architecture.md")
    assert _flag("04_architecture.md")
    assert _flag("issues/999-not-here.md")


def test_存在的文档引用不报():
    assert not _flag("docs/05_output_spec.md")
    assert not _flag("issues/007-p35-workbench.md")


def test_产物文件名不是文档引用():
    for name in ("article.md", "summary.zh.md", "_references.md", "CLAUDE.md"):
        assert not _flag(name), name


def test_占位符不是引用():
    assert not _flag("docs/issues/NNN-*.md")
