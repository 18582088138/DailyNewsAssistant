"""
test_clean.py —— 清洗归一化单元测试 / Cleaning and normalisation unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/pipeline/test_clean.py -v

对应的人工验证 / Matching manual check:
    dna digest --dry-run        # 看清洗后进入流水线的条目数与标题

覆盖 / Covers:
    1. NFKC 归一化：全角字母数字与半角统一（不统一会导致去重漏判）
    2. 空白归一：连续空格/空行压缩，行首尾去空
    3. 样板行剥离：关注引导、二维码、阅读原文、版权声明、英文 advertisement 等
    4. **只删整行命中的**，绝不切半行——宁可留噪声也不能切坏正文
    5. 正文里正常出现的「关注」不被误删（子串 vs 整行的区别）
    6. 标题过短的条目被丢弃
    7. **正文过短的条目不被丢弃**——抽取降级的文章仍是真实资讯
    8. to_news_item 返回 None 而不是抛异常（一条脏数据不该中断整期）
    9. canonical_url 与 id 由 URL 推导，带 utm 的链接归一到同一篇

预期 / Expected:
    耗时 < 1s；纯函数，无网络、无 LLM、零费用
"""

from __future__ import annotations

from datetime import datetime

import pytest

from dna.core.models import SourceKind
from dna.pipeline.clean import (
    MIN_TITLE_CHARS,
    clean_all,
    clean_text,
    is_publishable,
    normalize_text,
    strip_boilerplate,
    to_news_item,
)


# --- 归一化 / normalisation ----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 全角字母数字必须转半角，否则「ＧＰＴ」和「GPT」会被当成两件事
        ("ＧＰＴ－４ 发布", "GPT-4 发布"),
        ("２０２６年", "2026年"),
        # 连续空白压成一个
        ("多   模态    模型", "多 模态 模型"),
        ("行一\n\n\n\n行二", "行一\n\n行二"),
        # 行首尾空白清掉
        ("  前后有空格  ", "前后有空格"),
        ("\t制表符\t", "制表符"),
        ("", ""),
    ],
)
def test_normalize_text(raw: str, expected: str) -> None:
    """全角与空白归一，让后续相似度比较不被格式差异干扰。"""
    assert normalize_text(raw) == expected


def test_normalize_preserves_chinese_punctuation() -> None:
    """
    中文标点是内容不是格式，不能被归一化掉。

    把「，」变成「,」会让摘要读起来像机翻。
    """
    assert normalize_text("模型发布了，性能很好。") == "模型发布了，性能很好。"


# --- 样板剥离 / boilerplate removal --------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "点击上方「量子位」关注",
        "长按识别下方二维码",
        "扫码加入交流群",
        "阅读原文",
        "点击阅读原文",
        "原文链接：",
        "本文转载自机器之心",
        "版权声明：本文为原创文章",
        "免责声明：本文仅供参考",
        "未经授权禁止转载",
        "欢迎关注我们的公众号",
        "--------",
        "Advertisement",
        "Sponsored Content",
        "Subscribe to our newsletter for more",
    ],
)
def test_boilerplate_lines_are_removed(line: str) -> None:
    """整行的推广/声明内容被剥掉——它们在多个转载版本里各不相同，会干扰去重。"""
    text = f"正文第一段。\n{line}\n正文第二段。"
    assert strip_boilerplate(text) == "正文第一段。\n正文第二段。"


def test_the_word_follow_inside_prose_survives() -> None:
    """
    正文里正常出现的「关注」不能被删。

    这正是「整行匹配 vs 子串匹配」的分野：子串匹配会把这句话整行删掉，
    而它是实实在在的正文内容。
    """
    text = "值得关注的是，该模型在推理任务上提升明显。"
    assert strip_boilerplate(text) == text


def test_boilerplate_never_cuts_into_a_line() -> None:
    """
    绝不删半行。宁可留下一点噪声，也不能把正文切坏——
    切坏的正文会一路带进摘要和发布稿，而且没人会发现。
    """
    text = "研究团队表示，欢迎关注后续进展，他们将继续优化。"
    assert strip_boilerplate(text) == text


def test_clean_text_combines_both_steps() -> None:
    """clean_text = 归一化 + 去样板，一步到位。"""
    raw = "点击关注\n\n\n  ＡＩ  模型发布  \n阅读原文"
    assert clean_text(raw) == "AI 模型发布"


# --- 成稿门槛 / publishability -------------------------------------------------


def test_short_title_is_rejected() -> None:
    """标题太短的条目没有成稿价值。"""
    assert not is_publishable("短", "正文" * 100)
    assert is_publishable("这是一个足够长的标题", "")


def test_missing_body_is_still_publishable() -> None:
    """
    正文抽不出来**不构成丢弃理由**。

    抽取降级的文章只有标题和链接，但它仍是一条真实资讯，读者点链接就能看。
    为一个技术问题丢掉真新闻不划算——这是全项目一以贯之的取舍。
    """
    assert is_publishable("知乎那篇被反爬拦下的文章标题", "")


def test_title_threshold_is_the_documented_one() -> None:
    """门槛值本身也要有测试，改动时会立刻暴露。"""
    assert is_publishable("x" * MIN_TITLE_CHARS, "")
    assert not is_publishable("x" * (MIN_TITLE_CHARS - 1), "")


# --- 构造 NewsItem / building NewsItem -----------------------------------------


def test_to_news_item_builds_a_complete_item() -> None:
    """正常路径：字段齐全，canonical_url 已去除追踪参数。"""
    item = to_news_item(
        url="https://example.com/post?utm_source=weibo",
        title="OpenAI 发布新模型",
        text="正文内容。" * 20,
        source_id="qbitai",
        via=SourceKind.RSS,
        published_at=datetime(2026, 9, 2, 10, 0),
    )

    assert item is not None
    assert item.canonical_url == "https://example.com/post"
    assert item.source_id == "qbitai"
    assert item.title == "OpenAI 发布新模型"
    assert item.id


def test_to_news_item_returns_none_instead_of_raising() -> None:
    """
    不合格时返回 None 而不是抛异常。

    一条脏数据不该中断整期日报——采集是无人值守跑的，抛异常等于当天没有日报。
    """
    assert to_news_item(url="https://e.com/1", title="", text="正文") is None
    assert to_news_item(url="https://e.com/1", title="短", text="正文") is None


def test_same_article_with_different_tracking_params_shares_canonical_url() -> None:
    """带不同追踪参数的分享链接必须归一到同一个 canonical_url，否则去重白做。"""
    a = to_news_item(url="https://e.com/p?utm_source=a", title="足够长的标题在这里")
    b = to_news_item(url="https://e.com/p?utm_source=b&fbclid=xx", title="足够长的标题在这里")

    assert a is not None and b is not None
    assert a.canonical_url == b.canonical_url


def test_clean_all_drops_bad_records_and_keeps_the_rest() -> None:
    """批量清洗：坏记录被跳过，好记录照常通过，计数对得上。"""
    records = [
        {"url": "https://e.com/1", "title": "第一条足够长的标题", "text": "正文" * 30},
        {"url": "https://e.com/2", "title": "短", "text": "正文"},          # 标题太短
        {"url": "https://e.com/3", "title": "第三条足够长的标题", "text": ""},  # 正文空，但保留
    ]

    items = clean_all(records)

    assert len(items) == 2
    assert [i.title for i in items] == ["第一条足够长的标题", "第三条足够长的标题"]


def test_clean_all_strips_boilerplate_from_bodies() -> None:
    """批量清洗时正文里的样板行也被剥掉。"""
    records = [
        {
            "url": "https://e.com/1",
            "title": "一条足够长的新闻标题",
            "text": "点击关注\n真正的正文内容。\n阅读原文",
        }
    ]

    assert clean_all(records)[0].text == "真正的正文内容。"


def test_fullwidth_alphanumerics_fold_but_chinese_punctuation_does_not() -> None:
    """
    这条是 NFKC 踩过的坑的回归测试。

    全量 NFKC 会把中文逗号「，」折成 ASCII「,」——产物是要发到公众号和小红书的
    中文稿，混着半角逗号读起来就像机翻。只折「本来就是 ASCII、只是被打成全角」的字符。
    """
    assert normalize_text("ＧＰＴ－４ 发布了，性能提升２０％。") == "GPT-4 发布了，性能提升20％。"
    # 中文标点全部原样保留
    for punctuation in "，。；：！？（）、「」《》—…":
        assert normalize_text(f"文本{punctuation}文本") == f"文本{punctuation}文本"
