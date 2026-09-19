"""
test_tasks.py —— 产物任务定义单元测试 / Production task definition unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/produce/test_tasks.py -v

对应的人工验证 / Matching manual check:
    dna gui        # 设置面板改字数窗口 → 表格里超长的格子跟着变色
    dna prompt <id> -t shortvideo   # 渲染出的提示词里的字数跟着改

覆盖 / Covers:
    1. 三种按字数验收的产物各自映射到 profile 里对应的字段
    2. 长文案与音频返回 None —— 它们**不按字数验收**
    3. 传字符串 kind 也认（台账里存的是字符串）
    4. 窗口跟着 profile 走，不是写死的常量

为什么要有这个函数 / Why this mapping is a function:
    `service.py` 原来把这三条映射各写了一遍。界面要判断「这一格是不是超长」，
    用的窗口必须和生成时用的是同一个，否则会出现「生成时合格、显示成超长」。
    The mapping used to be spelled out three times in `service.py`. The window the UI
    judges a cell against must be the same one generation used, or a production can be
    generated as acceptable and then displayed as over-length.

预期 / Expected:
    耗时 < 1s；纯函数，不落盘、不联网、不调用 LLM
"""

from __future__ import annotations

import pytest

from dna.core.config import Profile
from dna.produce.tasks import ProductionKind, char_window


@pytest.fixture
def profile() -> Profile:
    return Profile(
        summary_chars=(80, 100),
        shortvideo_chars=(200, 300),
        narration_chars=(400, 800),
    )


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ProductionKind.SUMMARY, (80, 100)),
        (ProductionKind.SHORTVIDEO, (200, 300)),
        (ProductionKind.NARRATION, (400, 800)),
    ],
)
def test_char_windows_come_from_the_profile(
    profile: Profile, kind: ProductionKind, expected: tuple[int, int]
) -> None:
    assert char_window(kind, profile) == expected


@pytest.mark.parametrize(
    "kind",
    [
        ProductionKind.LONGFORM,
        ProductionKind.SHORTVIDEO_AUDIO,
        ProductionKind.NARRATION_AUDIO,
        ProductionKind.LONGFORM_AUDIO,
    ],
)
def test_kinds_without_a_char_window_return_none(profile: Profile, kind: ProductionKind) -> None:
    """
    长文案按**时长**验收（字数由时长推出来，见 issue 007-C）；音频根本不是文本。

    返回 None 的意思是「不按字数判定」，不是「窗口是 0~0」——后者会把每一格都
    标成超长。
    """
    assert char_window(kind, profile) is None


def test_string_kind_is_accepted(profile: Profile) -> None:
    """台账里的 kind 是字符串，界面拿到的就是字符串。"""
    assert char_window("summary", profile) == (80, 100)


def test_window_follows_the_profile() -> None:
    """改了 profile 的窗口，判定跟着变——这正是不把它存进库的理由。"""
    wide = Profile(summary_chars=(60, 200))
    assert char_window(ProductionKind.SUMMARY, wide) == (60, 200)
