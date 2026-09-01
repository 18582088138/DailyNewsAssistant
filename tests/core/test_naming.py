"""
test_naming.py —— 命名与路径规则单元测试 / Naming and path rule unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/core/test_naming.py -v

覆盖 / Covers:
    1. issue_dir_name()：期次目录必须是 YYYYMMDD-DailyNews（用户明确指定的格式）
    2. slugify()：中英文混排、空白与标点折叠、超长截断
    3. slugify()：**Windows 非法字符 <>:"/\\|?* 必须被剔除**，否则建目录直接失败
    4. slugify()：Windows 保留设备名（CON/PRN/COM1…）必须回退为 untitled
    5. slugify()：结尾的点与空格必须去掉（Windows 不允许）
    6. slugify()：空标题 / 纯标点标题回退为 untitled
    7. topic_dir_name()：两位补零序号 + slug，序号 < 1 报错
    8. history_dir_name() 与 lang_suffix_name() 格式
    9. 端到端：用真实的中文新闻标题生成完整相对路径，可安全用于 Windows

预期 / Expected:
    26 passed；耗时 < 1s；纯字符串运算，不做任何 I/O
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from dna.core.naming import (
    history_dir_name,
    issue_dir_name,
    lang_suffix_name,
    slugify,
    topic_dir_name,
)

# Windows 不允许出现在文件名中的字符 / characters Windows forbids in filenames
WINDOWS_ILLEGAL = '<>:"/\\|?*'


# --- 期次目录 / issue directory ----------------------------------------------


def test_issue_dir_name_format() -> None:
    """期次目录格式由用户指定，不可随意改动 / The format is user-specified; do not change."""
    assert issue_dir_name(date(2026, 9, 1)) == "20260901-DailyNews"
    assert issue_dir_name(date(2026, 12, 31)) == "20261231-DailyNews"


def test_issue_dir_name_accepts_datetime() -> None:
    """传 datetime 也应正确取日期部分 / A datetime is accepted and truncated to its date."""
    assert issue_dir_name(datetime(2026, 9, 1, 23, 59, 59)) == "20260901-DailyNews"


# --- slugify 基础 / slugify basics -------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("OpenAI 发布新模型", "OpenAI-发布新模型"),
        ("  前后空格  ", "前后空格"),
        ("多个   空格", "多个-空格"),
        ("中文、顿号，逗号。句号", "中文-顿号-逗号-句号"),
        ("a_b-c", "a-b-c"),
        ("Model 3.5 released!", "Model-3-5-released"),
    ],
)
def test_slugify_basic(raw: str, expected: str) -> None:
    """中英文混排与分隔符折叠 / Mixed scripts and separator collapsing."""
    assert slugify(raw) == expected


def test_slugify_keeps_chinese_readable() -> None:
    """中文保留原字，不做拼音转写——目录名要人眼可读 / Chinese is kept, not romanised."""
    assert "多模态" in slugify("国产多模态大模型发布")


def test_slugify_truncates_long_title() -> None:
    """超长标题截断，避免超出 Windows 路径长度上限 / Long titles are truncated."""
    result = slugify("很长的标题" * 30, max_len=20)
    assert len(result) <= 20
    assert not result.endswith("-")


# --- Windows 安全性（关键）/ Windows safety (critical) ------------------------


@pytest.mark.parametrize("ch", list(WINDOWS_ILLEGAL))
def test_slugify_strips_windows_illegal_chars(ch: str) -> None:
    """
    Windows 非法字符必须被剔除，否则建目录会直接抛 OSError。
    Characters Windows forbids must be stripped, or directory creation throws.
    """
    result = slugify(f"标题{ch}后缀")
    assert ch not in result
    assert not (set(result) & set(WINDOWS_ILLEGAL))


def test_slugify_strips_control_chars() -> None:
    """控制字符必须剔除 / Control characters must be stripped."""
    assert "\n" not in slugify("标题\n换行")
    assert "\t" not in slugify("标题\t制表")


@pytest.mark.parametrize("name", ["CON", "con", "PRN", "NUL", "COM1", "LPT9"])
def test_slugify_avoids_windows_reserved_names(name: str) -> None:
    """
    Windows 保留设备名不能作为目录名，必须回退。
    Windows reserved device names cannot be used as directory names.
    """
    assert slugify(name) == "untitled"


def test_slugify_strips_trailing_dot_and_space() -> None:
    """Windows 不允许目录名以点或空格结尾 / Windows forbids trailing dots and spaces."""
    result = slugify("标题...")
    assert not result.endswith(".")
    assert not result.endswith(" ")


@pytest.mark.parametrize("raw", ["", "   ", "。。。", "!!!", "///"])
def test_slugify_falls_back_to_untitled(raw: str) -> None:
    """空标题或纯标点标题回退 / Empty or punctuation-only titles fall back."""
    assert slugify(raw) == "untitled"


# --- 条目目录 / topic directory ----------------------------------------------


def test_topic_dir_name_pads_rank() -> None:
    """序号两位补零，保证目录按日报顺序排列 / Rank is zero-padded so folders sort correctly."""
    assert topic_dir_name(1, "OpenAI 发布新模型") == "01_OpenAI-发布新模型"
    assert topic_dir_name(12, "第十二条") == "12_第十二条"


def test_topic_dir_name_rejects_bad_rank() -> None:
    """序号从 1 开始 / Ranks are 1-based."""
    with pytest.raises(ValueError, match="rank starts at 1"):
        topic_dir_name(0, "标题")


# --- 其它命名 / other names --------------------------------------------------


def test_history_dir_name() -> None:
    """重做归档目录名 / Archive directory name used when redoing an output."""
    assert history_dir_name(datetime(2026, 9, 1, 14, 30, 5)) == "20260901-143005"


def test_lang_suffix_name() -> None:
    """语言后缀统一为 .zh / .en / Language suffix convention."""
    assert lang_suffix_name("daily", "zh", "md") == "daily.zh.md"
    assert lang_suffix_name("podcast", "en", ".wav") == "podcast.en.wav"


# --- 端到端 / end-to-end -----------------------------------------------------


def test_full_relative_path_is_windows_safe() -> None:
    """
    用真实的中文标题拼出完整相对路径，确认整条路径在 Windows 上可用。
    Build a full relative path from a realistic Chinese headline and confirm every
    segment is usable on Windows.
    """
    title = '某公司发布 "新一代" 多模态大模型：推理成本降低 40%？'
    path = f"{issue_dir_name(date(2026, 9, 1))}/topics/{topic_dir_name(3, title)}/brief/brief.zh.md"

    assert path.startswith("20260901-DailyNews/topics/03_")
    # 逐段检查非法字符（跳过路径分隔符本身）
    for segment in path.split("/"):
        assert not (set(segment) & set('<>:"\\|?*'))
        assert not segment.endswith((".", " ")) or segment.endswith(".md")
