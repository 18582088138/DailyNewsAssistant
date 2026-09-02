"""
LLM 输出解析 / Parsing LLM output.

模型返回的 JSON 常常不是干净的 JSON：外面裹一层 ```json 代码块、前面加一句
「好的，这是结果：」、后面再补一段解释。本模块负责把真正的 JSON 抠出来。
Models rarely return clean JSON: it comes fenced in ```json blocks, prefixed with
"Sure, here you go:", or followed by an explanation. This module digs the actual
JSON out of that.

纯字符串处理，无 I/O，因此可以完全离线测试。
Pure string handling with no I/O, so it is fully testable offline.
"""

from __future__ import annotations

import re

from dna.core.errors import ProviderResponseError

# ```json ... ``` 或 ``` ... ```
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)

_OPEN_TO_CLOSE = {"{": "}", "[": "]"}


def extract_json_block(text: str) -> str:
    """
    从模型输出中提取 JSON 文本 / Extract the JSON payload from a model response.

    按以下顺序尝试 / Strategies, in order:
        1. 整体就是 JSON（最常见的理想情况）
        2. Markdown 代码块内的内容
        3. 从第一个 `{` 或 `[` 开始做括号配对扫描，取出第一个完整的 JSON 值

    第 3 步用配对扫描而不是正则，因为正则无法正确处理嵌套结构，
    而字符串字面量里的花括号（例如 "价格 {{涨}}"）会让贪婪匹配取错边界。
    Step 3 uses bracket matching rather than a regex: a regex cannot handle nesting,
    and braces inside string literals would break greedy matching.

    抛出 / Raises:
        ProviderResponseError: 输出里找不到任何 JSON 结构
    """
    if not text or not text.strip():
        raise ProviderResponseError("模型返回为空 / the model returned an empty response")

    stripped = text.strip()

    # 1) 整体就是 JSON
    if stripped[0] in _OPEN_TO_CLOSE:
        matched = _match_balanced(stripped, 0)
        if matched is not None and matched.strip() == stripped:
            return stripped

    # 2) Markdown 代码块
    for block in _FENCE_RE.findall(text):
        candidate = block.strip()
        if candidate and candidate[0] in _OPEN_TO_CLOSE:
            matched = _match_balanced(candidate, 0)
            if matched is not None:
                return matched

    # 3) 括号配对扫描
    for index, char in enumerate(stripped):
        if char in _OPEN_TO_CLOSE:
            matched = _match_balanced(stripped, index)
            if matched is not None:
                return matched

    raise ProviderResponseError(
        f"模型输出中找不到 JSON / no JSON found in the response: {_preview(text)}"
    )


def _match_balanced(text: str, start: int) -> str | None:
    """
    从 start 处的开括号开始做配对扫描，返回完整的 JSON 值。
    Scan from the opening bracket at `start` and return the balanced JSON value.

    正确跳过字符串字面量与转义字符，因此 `{"note": "a } b"}` 不会被截断。
    String literals and escapes are skipped, so `{"note": "a } b"}` is not truncated.
    """
    opener = text[start]
    closer = _OPEN_TO_CLOSE.get(opener)
    if closer is None:
        return None

    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        char = text[i]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None  # 括号未闭合 / unbalanced


def strip_think_tags(text: str) -> str:
    """
    去掉推理模型的思维链标签 / Strip chain-of-thought tags from reasoning models.

    Qwen3 等模型会在正文前输出 <think>…</think>，直接喂给解析器会失败。
    Models such as Qwen3 emit <think>…</think> before the answer, which would
    otherwise break parsing.
    """
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _preview(text: str, limit: int = 200) -> str:
    """截断预览，避免超长内容灌满日志 / Truncated preview so logs stay readable."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


__all__ = ["extract_json_block", "strip_think_tags"]
