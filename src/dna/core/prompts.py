"""
提示词加载 / Prompt loading —— 提示词正文的唯一来源是 `config/prompts/`。

为什么把提示词搬出 .py / Why the prompts live outside the code:
    调提示词是**内容工作，不是编程工作**。写在 Python 字符串里就得跟转义、缩进、
    f-string 的花括号打交道，而且改一句要重启进程才生效。搬到 `config/prompts/`
    之后，改提示词就是改一个 Markdown 文件，`dna prompt` 立刻能看到效果。
    Tuning a prompt is content work, not programming. Keeping them in Python strings
    means fighting escapes, indentation and f-string braces, and a change needs a
    restart to take effect.

三个约定 / Three conventions:
    1. **占位符是 `{{name}}`，不是 `{name}`。** 提示词里有 `P(A|B)`、`[pause:400ms]`
       这类字面量，用 `str.format` 就得让改提示词的人记住转义规则。
    2. **一个文件可以分块**：整行 `## @key` 起一个新块，第一个标记之前的内容是
       `main`。长文案的 专题/访谈 两种变体因此不必各存一份完整文件。
    3. **换行是有意义的。** 文件内容逐字节发给模型，不要为了排版折行。
       Newlines are significant: the file goes to the model verbatim.

**缺文件、缺块、缺占位符一律抛 ConfigError，不降级。** 空的 system prompt 会照样
发出请求、照样计费，只是产出是垃圾——这是花钱路径上的护栏。
Missing files, blocks and placeholders all raise: an empty system prompt still issues a
billed request, it just returns rubbish.
"""

from __future__ import annotations

import re
from pathlib import Path

from dna.core.config import DEFAULT_CONFIG_DIR
from dna.core.errors import ConfigError

PROMPTS_DIR = DEFAULT_CONFIG_DIR / "prompts"

MAIN_BLOCK = "main"

# 分块标记：整行 `## @key`，key 允许字母数字、下划线与点（`role.interview`）
_BLOCK_RE = re.compile(r"^##[ \t]+@([A-Za-z0-9_.]+)[ \t]*$", re.MULTILINE)

# 占位符：`{{name}}`
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")

# 解析结果缓存，键含 mtime / parsed blocks, keyed with the file's mtime
#
# 为什么带 mtime / Why the mtime is part of the key:
#     `dna gui` 是长驻进程。改完提示词点「重做」要立刻用新的那一版，否则
#     「方便调试」就不成立——人会以为自己改的没生效，然后去改别的地方。
#     The workbench is a long-running process: a prompt edited on disk must take effect
#     on the next click, or the user concludes the edit did nothing and changes something
#     else instead.
_cache: dict[tuple[Path, int], dict[str, str]] = {}


def prompt_path(name: str) -> Path:
    """
    提示词文件的路径 / The path of one prompt file.

    `name` 不带 `.md`，可以带目录：`summarize`、`shortvideo.zh`、
    `_shared/professionalism.zh`。
    """
    return PROMPTS_DIR / f"{name}.md"


def load_prompt(name: str, block: str = MAIN_BLOCK) -> str:
    """
    读一段提示词 / Read one prompt block.

    参数 / Args:
        name:  文件名，不含 `.md`
        block: 块名；`main` 是第一个 `## @` 标记之前的内容

    抛出 / Raises:
        ConfigError: 文件不存在、读不出来，或没有这个块
    """
    blocks = _blocks_of(name)
    if block not in blocks:
        available = "、".join(sorted(blocks)) or "（无）"
        raise ConfigError(
            f"提示词 {prompt_path(name)} 里没有 `## @{block}` 这一块，"
            f"现有的块：{available} / no such block"
        )
    return blocks[block]


def render_prompt(name: str, block: str = MAIN_BLOCK, **values: object) -> str:
    """
    读一段提示词并填占位符 / Read one block and fill its placeholders.

    模板里出现、但调用方没给的占位符**报错**，不静默留下 `{{cta}}`——
    那一串会原样发给模型并计费。多给的值忽略，方便一次 render 供多个块使用。
    A placeholder present in the template but missing from the call raises rather than
    being left in place, since the literal `{{cta}}` would be sent to the model and
    billed. Extra values are ignored.

    抛出 / Raises:
        ConfigError: 文件/块不存在，或有占位符没给值
    """
    template = load_prompt(name, block)
    missing = sorted(
        {key for key in _PLACEHOLDER_RE.findall(template) if key not in values}
    )
    if missing:
        raise ConfigError(
            f"提示词 {prompt_path(name)}（块 {block}）缺少占位符取值："
            f"{'、'.join(missing)} / missing placeholder values"
        )
    return _PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]), template)


def placeholders_of(name: str, block: str = MAIN_BLOCK) -> list[str]:
    """这一块用到哪些占位符 / Which placeholders one block uses，供 `dna prompt --list`。"""
    return sorted(set(_PLACEHOLDER_RE.findall(load_prompt(name, block))))


def blocks_of(name: str) -> list[str]:
    """这个文件有哪些块 / Which blocks one file defines."""
    return sorted(_blocks_of(name))


def available_prompts() -> list[str]:
    """
    有哪些提示词文件 / Every prompt file present，含 `_shared/` 下的片段。

    给 `dna doctor` 与 `dna prompt --list` 用；返回的是不带 `.md` 的 name。
    """
    if not PROMPTS_DIR.is_dir():
        return []
    return sorted(
        path.relative_to(PROMPTS_DIR).with_suffix("").as_posix()
        for path in PROMPTS_DIR.rglob("*.md")
        if path.name != "README.md"
    )


def clear_cache() -> None:
    """丢掉解析缓存 / Drop the parse cache（测试用；正常路径靠 mtime 自动失效）。"""
    _cache.clear()


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _blocks_of(name: str) -> dict[str, str]:
    """读文件并切块，按 (路径, mtime) 缓存 / Read and split, cached on path and mtime."""
    path = prompt_path(name)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError as exc:
        raise ConfigError(
            f"提示词文件不存在或读不到：{path} —— 提示词正文放在 config/prompts/，"
            f"缺文件时不能降级运行（空提示词照样计费）/ prompt file unavailable: {exc}"
        ) from exc

    cached = _cache.get((path, mtime))
    if cached is not None:
        return cached

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"提示词文件读取失败：{path} —— {exc}") from exc

    blocks = _split_blocks(text)
    _cache[(path, mtime)] = blocks
    return blocks


def _split_blocks(text: str) -> dict[str, str]:
    """
    按 `## @key` 切块 / Split on the `## @key` markers.

    每块都 `strip()`：文件末尾的换行、块之间的空行都是排版，不是提示词内容。
    提示词**内部**的换行原样保留。
    Every block is stripped: the trailing newline and the blank lines between blocks are
    layout rather than content. Newlines *inside* a block are kept verbatim.
    """
    matches = list(_BLOCK_RE.finditer(text))
    blocks: dict[str, str] = {}

    head = text[: matches[0].start()] if matches else text
    if head.strip():
        blocks[MAIN_BLOCK] = head.strip()

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks[match.group(1)] = text[match.end() : end].strip()

    return blocks


__all__ = [
    "MAIN_BLOCK",
    "PROMPTS_DIR",
    "available_prompts",
    "blocks_of",
    "clear_cache",
    "load_prompt",
    "placeholders_of",
    "prompt_path",
    "render_prompt",
]
