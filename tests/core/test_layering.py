"""
分层铁律的静态断言 / The layering rule, asserted statically.

    $PY -m pytest tests/core/test_layering.py -q

`CLAUDE.md` 的铁律：

    frontends → produce → pipeline → narration → store → extract → sources → llm · tts → core

依赖严格单向。这条规则以前只写在文档里，于是 `core/doctor.py` 靠一行**函数内**
`from dna.tts.client import ...` 反着依赖了上层近半年没人发现 —— 延迟 import
不会报错，只是把违规藏起来。文档管不住的事情交给测试。

这里连函数内 import 一起查：它正是上一次违规的藏身之处。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "dna"

# 每一层**允许**依赖谁（不含自己与标准库/第三方）
# Which layers each layer may import from.
ALLOWED: dict[str, set[str]] = {
    "core": set(),
    "llm": {"core"},
    "tts": {"core", "llm"},
    "sources": {"core"},
    "extract": {"core", "sources"},
    "store": {"core", "sources", "extract"},
    "narration": {"core", "llm"},
    "pipeline": {"core", "llm", "narration", "store"},
    "produce": {"core", "llm", "tts", "narration", "pipeline", "store"},
}

IMPORT = re.compile(r"^\s*(?:from|import)\s+dna\.([a-z_]+)", re.MULTILINE)


def _layers() -> list[str]:
    return sorted(ALLOWED)


@pytest.mark.parametrize("layer", _layers())
def test_这一层没有反向依赖(layer: str) -> None:
    violations: list[str] = []
    for path in (SRC / layer).rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for other in IMPORT.findall(text):
            if other in (layer, "core") and layer != "core":
                continue
            if other == layer or other in ALLOWED[layer]:
                continue
            if other not in ALLOWED:      # dna.xxx 里的非分层模块（如 dna.__version__）
                continue
            violations.append(f"{path.relative_to(SRC.parent.parent).as_posix()} → dna.{other}")

    assert not violations, (
        f"`{layer}/` 反向依赖了上层：{violations}。"
        "铁律是单向的；需要上层能力时由前端（组装根）把它交进来，"
        "参考 core/doctor.py 的 run_all(extra=...) 与 tts/doctor.py。"
    )


def test_core_不依赖任何上层() -> None:
    """
    单独再钉一遍 core：它是「谁都可以依赖它，它不依赖任何人」的那一层，
    也是唯一真的破过例的一层。
    """
    offenders: list[str] = []
    for path in (SRC / "core").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for other in IMPORT.findall(text):
            if other != "core":
                offenders.append(f"{path.name} → dna.{other}")

    assert not offenders, f"core/ 不许依赖上层，实际有：{offenders}"


# `src/` 里允许出现的非 .py 文件名（打包标记之类）
SRC_ALLOWED_FILES = {"py.typed"}


def test_src_下只有代码() -> None:
    """
    `src/` 是包根，不是工作目录：任何数据、日志、产物出现在这里都是路径算错了。

    实际发生过：仓库根原先靠数目录层级得出，`config.py` 拆成包之后深了一层，
    根就指到了 `src/`，于是 `src/data/logs/` 与 `src/outputs/` 被建了出来。
    `.gitignore` 里的 `data/` 与 `outputs/` 不带前导斜杠，**任何层级都匹配**，
    所以 `git status` 从头到尾一片干净 —— 只有人拿眼睛看目录才会发现。

    这条测试盯的是症状而不是某一种成因：不管是仓库根算错、相对路径按 cwd 落盘，
    还是将来谁把缓存写进包里，都会在这里变红。
    """
    src = SRC.parent
    strays: list[str] = []

    for path in src.iterdir():
        if path.is_dir() and not (path / "__init__.py").is_file():
            strays.append(f"{path.name}/（不是包：没有 __init__.py）")
        elif path.is_file():
            strays.append(path.name)

    for path in src.rglob("*"):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        if path.suffix != ".py" and path.name not in SRC_ALLOWED_FILES:
            strays.append(path.relative_to(src).as_posix())

    assert not strays, (
        f"`src/` 下出现了非代码内容：{sorted(set(strays))}。"
        "数据、日志、产物一律走 settings.data_path / output_path（它们按仓库根"
        "解析绝对路径），不要用相对路径直接落盘 —— 那会跟着 cwd 跑。"
    )
