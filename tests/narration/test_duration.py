"""
test_duration.py —— 口播时长估算单元测试 / Narration duration unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/narration/test_duration.py -v

对应的人工验证 / Matching manual check:
    dna produce <id> --kind shortvideo    # 输出会打印「约 N 秒」，与实际念一遍对比

覆盖 / Covers:
    1. 中文按 4.5 字/秒——135 字 ≈ 30 秒（短视频的目标点）
    2. 中英混排分别计算再相加，不把英文当中文字数算
    3. **角色前缀不计入**（`主持人：`）——它不会被念出来
    4. **Markdown 标记不计入**——`**加粗**` 的星号不发音
    5. 空文本、纯标点返回 0，不崩
    6. target_chars 与 estimate_seconds 互为逆运算
    7. **prompt_char_budget 比 target_chars 大**——两者是不同的量，见下
    8. observed_chars_per_second 反映实际中英比例，退化输入不除零
    9. within 区间判定含边界
   10. char_feedback：区间内返回 None；超出时给**确切差多少字**而非「请缩短」
   11. **给了字数时按这一稿的实测密度换算差值**，不用预设常数

两个字数函数为什么必须分开 / Why the two character functions are separate:
    `target_chars` 是物理量：`estimate_seconds` 的逆运算，纯中文下严格自洽。
    `prompt_char_budget` 是「要告诉模型写多少字」，按中英混排的实测密度放大 1.5 倍。
    混用的后果是**静默的**：模型照纯中文换算的字数写完，时长恰好落在区间下沿，
    验收通过、回炉不触发，只是信息量少掉三分之一。
    `target_chars` is physical — the strict inverse of `estimate_seconds` for all-Chinese
    text. `prompt_char_budget` is what the model is told to write, scaled to the measured
    density of mixed copy. Conflating them fails silently: the draft lands at the bottom
    of the window, passes acceptance, never triggers a rewrite, and carries a third less
    information.

为什么必须扣掉不发音的内容 / Why non-spoken content must be excluded:
    角色名和 Markdown 标记不进 TTS。把它们算进时长会让估算系统性偏长，
    于是达标的稿子被误判超时、白白回炉重写——而每次回炉都是一次计费调用。
    Counting them inflates every estimate, wrongly flags compliant scripts as overlong
    and triggers rewrites — and every rewrite is a billed call.

预期 / Expected:
    耗时 < 1s；纯函数，无网络、无 LLM、零费用
"""

from __future__ import annotations

import pytest

from dna.narration.duration import (
    CHARS_PER_SECOND_ZH,
    MIXED_COPY_CHAR_FACTOR,
    char_feedback,
    count_units,
    estimate_seconds,
    observed_chars_per_second,
    prompt_char_budget,
    spoken_text,
    target_chars,
    within,
)

# --- 基本估算 / basic estimation -----------------------------------------------


def test_chinese_uses_the_documented_rate() -> None:
    """中文 4.5 字/秒——这是常规播报语速，三种文案的字数都由它推导。"""
    assert estimate_seconds("字" * 135) == pytest.approx(30.0, abs=0.2)
    assert CHARS_PER_SECOND_ZH == 4.5


@pytest.mark.parametrize(
    ("seconds", "expected_chars"),
    [
        (25, 112),   # 短视频下限
        (35, 157),   # 短视频上限
        (60, 270),   # 口播下限
        (120, 540),  # 口播上限
        (600, 2700), # 长文案下限
        (900, 4050), # 长文案上限
    ],
)
def test_target_chars_matches_the_documented_windows(seconds: int, expected_chars: int) -> None:
    """
    时长区间与字数区间必须对得上。

    提示词里写的是字数（模型对字数的遵守程度远好于秒数），而验收看的是秒数。
    两者一旦对不上，就会出现「模型照字数写了却总被判超时」的死循环。
    """
    assert target_chars(seconds) == expected_chars


def test_target_chars_and_estimate_are_inverses() -> None:
    """两个方向必须自洽，否则回炉重写的差值会算错。"""
    for seconds in (30, 90, 700):
        assert estimate_seconds("字" * target_chars(seconds)) == pytest.approx(seconds, abs=1)


def test_prompt_budget_is_larger_than_the_raw_conversion() -> None:
    """
    **给提示词的字数预算比纯中文换算大**，这是两个不同的量。

    `target_chars` 是物理量（`estimate_seconds` 的逆运算），`prompt_char_budget`
    是「要告诉模型写多少字」。混用会让稿子时长达标而信息量不足：
    实测人工撰写的 30 秒参考稿有 199 字符，而按 4.5 字/秒只会要到 135 字。
    """
    assert prompt_char_budget(30) > target_chars(30)
    assert prompt_char_budget(30) == int(target_chars(30) * MIXED_COPY_CHAR_FACTOR)

    # 与人工撰写的参考稿对得上：199 字符 / 27.8 秒
    assert prompt_char_budget(25) <= 199 <= prompt_char_budget(35)


def test_observed_density_reflects_the_actual_mix() -> None:
    """
    实测密度随中英比例变化，所以回炉差值不能用常数换算。

    纯中文稿约 4.5 字符/秒，塞满英文标识符的技术稿能到两倍以上。
    用常数换算，高密度稿会被少要求删一半，第二稿仍然超时——白花一次调用。
    """
    chinese = "这是一段纯中文的口播稿件内容" * 10
    english = "Terminal Bench throughput benchmark result " * 10

    zh_rate = observed_chars_per_second(len(chinese), estimate_seconds(chinese))
    en_rate = observed_chars_per_second(len(english), estimate_seconds(english))

    assert zh_rate == pytest.approx(CHARS_PER_SECOND_ZH, abs=0.6)
    assert en_rate > zh_rate * 2


@pytest.mark.parametrize(("chars", "seconds"), [(0, 10.0), (100, 0.0), (-5, -5.0)])
def test_observed_density_falls_back_on_degenerate_input(chars: int, seconds: float) -> None:
    """
    零字数或零时长时退回预设值，**不能除零崩掉**。

    生成失败会传进来空串，而回炉环节挂掉意味着整篇产物作废、
    已经花掉的调用一起浪费。
    """
    assert observed_chars_per_second(chars, seconds) > 0


def test_feedback_delta_is_exact() -> None:
    """
    差值是**减法**，不是按密度估的。

    提示词要求的单位和这里检查的单位现在是同一个（都是字数），所以「超了多少」
    是确定的。先前提示词说字数、验收看秒数，差值只能按密度换算，而密度随中英
    比例在 4.5~9 字/秒之间浮动——算错一半，第二稿照样不达标。
    """
    feedback = char_feedback(280, 200, 250)

    assert feedback is not None
    assert "删掉 30 字" in feedback


def test_mixed_language_counts_each_separately() -> None:
    """
    中英混排分别按各自语速计算。

    技术稿里「DeepSeek-V4-Flash 在 Terminal Bench 上达到 82.7」这种句子极常见，
    把英文词当成中文字数算会明显低估——一个 8 字母的模型名念起来不止 8/4.5 秒。
    """
    chinese_only = estimate_seconds("模型在测试上达到分数")
    mixed = estimate_seconds("模型在 Terminal Bench 上达到 82.7 分")

    assert mixed > chinese_only


# --- 不发音的内容 / non-spoken content -----------------------------------------


def test_speaker_prefix_is_not_counted() -> None:
    """
    角色名不计入时长——它不会被念出来，只是给 TTS 分配音色的标记。

    访谈稿里每一轮都有前缀，二十轮就是几十个字的系统性高估。
    """
    with_prefix = estimate_seconds("**主持人：** 今天我们聊聊这个新模型的架构")
    without = estimate_seconds("今天我们聊聊这个新模型的架构")

    assert with_prefix == pytest.approx(without, abs=0.3)


def test_markdown_markup_is_not_counted() -> None:
    """加粗的星号、列表符号都不发音。"""
    assert estimate_seconds("**重点内容**") == pytest.approx(
        estimate_seconds("重点内容"), abs=0.2
    )


def test_spoken_text_strips_prefix_and_markup() -> None:
    """`spoken_text` 是可单独检查的中间结果，便于排查估算偏差。"""
    result = spoken_text("**嘉宾：** 这里有 *重点*")

    assert "嘉宾" not in result
    assert "*" not in result
    assert "这里有" in result


@pytest.mark.parametrize("text", ["", "   ", "。，、！", "\n\n"])
def test_degenerate_input_returns_zero(text: str) -> None:
    """空文本与纯标点返回 0，不崩——生成失败时会传进来空串。"""
    assert estimate_seconds(text) == 0.0


# --- 区间判定 / window checks --------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(24.9, False), (25.0, True), (30.0, True), (35.0, True), (35.1, False)],
)
def test_within_includes_the_boundaries(seconds: float, expected: bool) -> None:
    """边界值算达标——卡在 35.0 秒的稿子没有理由回炉重写。"""
    assert within(seconds, 25, 35) is expected


def test_feedback_is_none_inside_the_window() -> None:
    """达标时不返回反馈，调用方据此结束循环。"""
    assert char_feedback(220, 200, 250) is None


def test_feedback_states_the_concrete_delta_when_too_long() -> None:
    """
    超长时给出**具体删多少字**，而不是「请缩短」。

    模糊的指令换来模糊的修改：模型要么砍掉一半，要么几乎没动，
    两种都要再花一次调用。给数字它就照着删。
    """
    feedback = char_feedback(300, 200, 250)

    assert feedback is not None
    assert "300" in feedback   # 上一稿的字数
    assert "250" in feedback   # 上限
    assert "删掉 50 字" in feedback  # 确切差值


def test_feedback_states_the_delta_when_too_short() -> None:
    """不足时同样给具体字数，并**明确禁止用套话凑长度**。"""
    feedback = char_feedback(150, 200, 250)

    assert feedback is not None
    assert "补充" in feedback
    assert "不要用背景介绍和套话凑长度" in feedback


def test_feedback_tells_the_model_what_to_cut_first() -> None:
    """
    超长反馈要指明先删什么。

    不说的话模型往往先删数字和方法名——那正是稿子里最有价值的部分，
    删完就成了白话。
    """
    feedback = char_feedback(320, 200, 250)

    assert feedback is not None
    assert "背景铺垫" in feedback
    assert "保留具体数字" in feedback


def test_english_budget_is_not_scaled_up() -> None:
    """
    **英文预算不乘混排系数。**

    1.5 这个系数量的是「中文字数 vs 中英混排稿的实际字符数」——英文稿的单位
    本来就是词，不存在这个折算。照样乘 1.5 会让英文稿超长 50%，
    而这正是实测反馈「英文输出的文本量太大」的来源。
    The factor measures Chinese characters against mixed copy; English is counted in
    words to begin with, so scaling it simply makes every English script half again too
    long — which is what the measured complaint was about.
    """
    assert prompt_char_budget(30, lang="en") == target_chars(30, lang="en")
    assert prompt_char_budget(30, lang="zh") > target_chars(30, lang="zh")


def test_units_follow_the_language() -> None:
    """
    中文数字符、英文数词 / Characters for Chinese, words for English.

    回炉反馈的差值必须和提示词里用的单位一致，否则会出现
    「上一稿 900 字符 → 请删掉 300 words」这种自相矛盾的指令。
    """
    text = "This draft has exactly seven words here."

    assert count_units(text, lang="en") == 7
    assert count_units(text, lang="zh") == len(text)


def test_english_feedback_is_written_in_english() -> None:
    """
    **给英文稿的回炉指令用英文写。**

    给英文稿发中文指令，模型有相当概率改回中文输出——这一层的输入输出语言
    应当一致，否则回炉反而把稿子毁了。
    A Chinese instruction attached to an English draft stands a real chance of flipping
    the output back to Chinese, damaging the draft rather than fixing it.
    """
    feedback = char_feedback(130, 77, 96, lang="en")

    assert feedback is not None
    assert "words" in feedback
    assert "字" not in feedback
