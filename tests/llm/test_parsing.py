"""
test_parsing.py —— LLM 输出解析单元测试 / LLM output parsing unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/llm/test_parsing.py -v

覆盖 / Covers:
    1. 纯净 JSON 直接返回（对象与数组）
    2. ```json 代码块、``` 无语言标记代码块
    3. 前后夹带解释文字（「好的，结果如下：」这类）
    4. **字符串字面量里含 } 或 { 时不能被截断**（贪婪正则会在这里出错）
    5. 嵌套对象与数组的括号配对
    6. 转义引号 \\" 不能被误判为字符串结束
    7. 空输出 / 无 JSON / 括号未闭合 → ProviderResponseError
    8. strip_think_tags 去除 Qwen3 系列的 <think>…</think>

为什么值得这么多用例 / Why so many cases:
    JSON 解析失败在运行期表现为「摘要偶尔生成不出来」，而且换个模型就复现不了。
    这里一次性把已知的畸形输出形态钉死，后续迁移到本地小模型时不用重新踩。
    A JSON parse failure shows up at runtime as "summaries occasionally fail" and
    stops reproducing when the model changes. Pinning the known malformed shapes
    down here means the local-model migration will not have to rediscover them.

预期 / Expected:
    24 passed；耗时 < 1s；纯字符串处理，无 I/O
"""

from __future__ import annotations

import json

import pytest

from dna.core.errors import ProviderResponseError
from dna.llm.parsing import extract_json_block, strip_think_tags


# --- 正常形态 / well-formed shapes -------------------------------------------


def test_plain_json_object() -> None:
    """整体就是 JSON 对象 / The whole response is a JSON object."""
    assert extract_json_block('{"a": 1}') == '{"a": 1}'


def test_plain_json_array() -> None:
    """整体就是 JSON 数组 / The whole response is a JSON array."""
    assert extract_json_block("[1, 2, 3]") == "[1, 2, 3]"


def test_json_with_surrounding_whitespace() -> None:
    """前后空白应被容忍 / Surrounding whitespace is tolerated."""
    assert extract_json_block('\n\n  {"a": 1}  \n') == '{"a": 1}'


# --- 代码块 / fenced blocks ---------------------------------------------------


def test_fenced_json_block() -> None:
    """```json 代码块 / A ```json fenced block."""
    raw = '```json\n{"title": "标题"}\n```'
    assert json.loads(extract_json_block(raw)) == {"title": "标题"}


def test_fenced_block_without_language() -> None:
    """无语言标记的代码块 / A fence without a language tag."""
    raw = '```\n{"n": 1}\n```'
    assert json.loads(extract_json_block(raw)) == {"n": 1}


def test_fenced_block_with_prose_around() -> None:
    """代码块前后有解释文字 / Prose surrounding the fenced block."""
    raw = '好的，结果如下：\n\n```json\n{"ok": true}\n```\n\n希望对你有帮助。'
    assert json.loads(extract_json_block(raw)) == {"ok": True}


# --- 夹带文字 / prose without fences ------------------------------------------


def test_prefix_prose_without_fence() -> None:
    """前面有一句话，没有代码块 / A leading sentence with no fence."""
    raw = '这是结果：{"a": 1}'
    assert extract_json_block(raw) == '{"a": 1}'


def test_suffix_prose_without_fence() -> None:
    """后面有解释文字 / A trailing explanation."""
    raw = '{"a": 1}\n以上就是全部内容。'
    assert extract_json_block(raw) == '{"a": 1}'


# --- 关键：字符串字面量中的括号 / critical: braces inside strings --------------


def test_brace_inside_string_is_not_a_boundary() -> None:
    """
    字符串里的 } 不能被当成对象结束 —— 贪婪正则在这里必然出错。
    A } inside a string must not end the object; a greedy regex gets this wrong.
    """
    raw = '{"note": "价格 } 上涨", "n": 1}'
    assert json.loads(extract_json_block(raw)) == {"note": "价格 } 上涨", "n": 1}


def test_open_brace_inside_string() -> None:
    """字符串里的 { 不应增加深度 / A { inside a string must not deepen the nesting."""
    raw = '{"note": "模板 {name}", "n": 2}'
    assert json.loads(extract_json_block(raw)) == {"note": "模板 {name}", "n": 2}


def test_escaped_quote_inside_string() -> None:
    """转义引号不应被误判为字符串结束 / An escaped quote must not close the string."""
    raw = '{"quote": "他说\\"你好\\"", "n": 3}'
    assert json.loads(extract_json_block(raw))["n"] == 3


def test_brackets_inside_string_in_array() -> None:
    """数组元素字符串里的 ] / A ] inside a string element of an array."""
    raw = '["a ] b", "c"]'
    assert json.loads(extract_json_block(raw)) == ["a ] b", "c"]


# --- 嵌套 / nesting -----------------------------------------------------------


def test_nested_object() -> None:
    """嵌套对象 / Nested objects."""
    raw = '{"a": {"b": {"c": 1}}}'
    assert json.loads(extract_json_block(raw)) == {"a": {"b": {"c": 1}}}


def test_nested_array_of_objects() -> None:
    """对象数组 —— 摘要结果的典型形态 / An array of objects, the typical digest shape."""
    raw = '前言\n[{"t": "a"}, {"t": "b"}]\n后记'
    assert json.loads(extract_json_block(raw)) == [{"t": "a"}, {"t": "b"}]


def test_chinese_content_preserved() -> None:
    """中文内容不应被破坏 / Chinese content must survive intact."""
    raw = '{"summary": "国产多模态大模型发布，推理成本下降四成。"}'
    assert json.loads(extract_json_block(raw))["summary"].endswith("四成。")


# --- 异常路径 / failure paths -------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   ", "\n\n"])
def test_empty_response_raises(raw: str) -> None:
    """空响应必须报错 / An empty response must raise."""
    with pytest.raises(ProviderResponseError, match="模型返回为空"):
        extract_json_block(raw)


def test_no_json_raises() -> None:
    """完全没有 JSON 时报错 / Raises when there is no JSON at all."""
    with pytest.raises(ProviderResponseError, match="找不到 JSON"):
        extract_json_block("抱歉，我无法完成这个请求。")


def test_unbalanced_braces_raise() -> None:
    """括号未闭合时报错，而不是返回半截内容 / Unbalanced braces raise rather than truncate."""
    with pytest.raises(ProviderResponseError):
        extract_json_block('{"a": 1')


def test_error_message_is_truncated() -> None:
    """超长输出不应灌满日志 / A very long response must not flood the log."""
    with pytest.raises(ProviderResponseError) as excinfo:
        extract_json_block("没有 JSON " * 500)
    assert len(str(excinfo.value)) < 400


# --- 思维链 / chain-of-thought tags -------------------------------------------


def test_strip_think_tags() -> None:
    """去掉 <think> 段 / Strip the <think> block."""
    raw = "<think>让我想想……</think>\n最终答案"
    assert strip_think_tags(raw) == "最终答案"


def test_strip_multiline_think_tags() -> None:
    """跨行的 <think> 段 / A multi-line <think> block."""
    raw = "<think>\n第一步\n第二步\n</think>\n{\"a\": 1}"
    assert strip_think_tags(raw) == '{"a": 1}'


def test_strip_think_tags_noop_when_absent() -> None:
    """没有 <think> 时原样返回 / Unchanged when there is no think block."""
    assert strip_think_tags("普通回复") == "普通回复"


def test_think_tags_then_json_extraction() -> None:
    """
    组合场景：先剥思维链再提 JSON —— 这正是 Ollama 跑 qwen3.5 的真实输出形态。
    Combined: strip the reasoning, then extract JSON — exactly what qwen3.5 on
    Ollama actually returns.
    """
    raw = '<think>用户想要 JSON</think>\n```json\n{"ok": 1}\n```'
    assert json.loads(extract_json_block(strip_think_tags(raw))) == {"ok": 1}
