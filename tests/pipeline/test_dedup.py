"""
test_dedup.py —— 去重与聚类单元测试 / De-duplication and clustering unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/pipeline/test_dedup.py -v

对应的人工验证 / Matching manual check:
    dna digest --dry-run     # 输出里的「N 条 → M 个事件」以及合并明细

覆盖 / Covers:
    1. SimHash **跨进程稳定**——不能用内置 hash（它每次启动都加盐）
    2. SimHash 对词序敏感（2-gram 特征，不是词袋）
    3. 空文本、单字等边界输入不崩
    4. 第一级：规范 URL 相同 → 合并（带不同 utm 的分享链接）
    5. 第二级：正文近似 → 合并（同一篇稿子被多个号转发）
    6. **不同事件不被误合并**——错合并比漏合并危险得多
    7. 代表条目取正文最长的那篇（多源报道时内容最全的最适合做摘要）
    8. cluster.refs 汇总全部成员链接且去重保序
    9. 计数对得上账：raw_count / merged_by_* / len(clusters)
   10. 语义聚类不可用时**降级而非报错**（第三级是锦上添花）

阈值取向 / The threshold's bias:
    汉明距离 ≤3 是保守值：宁可漏合并（读者自己看得出两条相似），
    也不要错合并（两件不同的事被混成一条，读者无从察觉）。
    Conservative on purpose — a missed merge is visible to the reader, a wrong merge
    is not.

预期 / Expected:
    22 passed；耗时 < 1s；纯计算，无网络、无 LLM、不加载向量模型、零费用
"""

from __future__ import annotations

import importlib
from datetime import datetime

import pytest

from dna.core.models import NewsItem, SourceKind
from dna.core.urls import canonicalize_url, url_hash
from dna.pipeline.dedup import (
    DEFAULT_HAMMING_THRESHOLD,
    dedup,
    hamming,
    is_near_duplicate,
    simhash,
    tokenize,
)


def item(
    url: str, title: str, text: str = "", source: str = "s1", *, when: datetime | None = None
) -> NewsItem:
    """构造一条测试条目 / Build a test item."""
    return NewsItem(
        id=url_hash(url),
        source_id=source,
        via=SourceKind.RSS,
        url=url,
        canonical_url=canonicalize_url(url),
        title=title,
        text=text,
        published_at=when,
    )


# --- SimHash 本身 / SimHash itself ---------------------------------------------


def test_simhash_is_stable() -> None:
    """同样的输入必须得到同样的指纹。"""
    text = "OpenAI 发布新一代多模态大模型"
    assert simhash(text) == simhash(text)


def test_simhash_is_stable_across_processes() -> None:
    """
    指纹必须跨进程稳定——这是「不能用内置 hash」的回归测试。

    内置 hash 对字符串每次进程启动都加盐，用它做指纹会让跨日去重在重启后
    全部失效，而且**不会报错**，只是默默失去作用。这里对写死的输入断言写死的值。
    """
    assert simhash("test") == 10857399923672245971


def test_simhash_is_word_order_sensitive() -> None:
    """
    词序不同的两句话应有不同指纹。

    用单 token 做特征会让「AI 发布模型」和「模型发布 AI」得到相同指纹，
    词序完全丢失；因此实现用的是 2-gram。
    """
    assert simhash("AI 发布 模型") != simhash("模型 发布 AI")


@pytest.mark.parametrize("text", ["", " ", "。", "a"])
def test_simhash_handles_degenerate_input(text: str) -> None:
    """边界输入不能崩——真实 feed 里什么都有。"""
    assert isinstance(simhash(text), int)


def test_empty_text_hashes_to_zero() -> None:
    """空文本约定为 0，便于调用方识别。"""
    assert simhash("") == 0


def test_hamming_distance() -> None:
    """汉明距离就是异或后 1 的个数。"""
    assert hamming(0b1010, 0b1010) == 0
    assert hamming(0b1010, 0b1011) == 1
    assert hamming(0b0000, 0b1111) == 4


def test_tokenize_splits_cjk_by_character_and_latin_by_word() -> None:
    """中文按字、英文按词——SimHash 靠大量特征叠加，单字粒度已足够稳定。"""
    assert tokenize("AI 模型 GPT4") == ["ai", "模", "型", "gpt4"]


def test_near_duplicate_detection() -> None:
    """只差标点/空格的两段文本是近重复；讲不同事情的不是。"""
    a = "OpenAI 发布了新一代多模态大模型，支持视频理解能力"
    b = "OpenAI 发布了新一代多模态大模型 支持视频理解能力"
    c = "苹果公司公布最新季度财报，营收超出市场预期"

    assert is_near_duplicate(a, b)
    assert not is_near_duplicate(a, c)


def test_threshold_is_the_documented_conservative_value() -> None:
    """阈值本身要有测试，调整时会立刻暴露。"""
    assert DEFAULT_HAMMING_THRESHOLD == 3


# --- 第一级：URL / tier one ----------------------------------------------------


def test_same_canonical_url_merges() -> None:
    """
    带不同追踪参数的分享链接是同一篇文章。

    不合并的话，同一篇稿子从微博和微信各来一次，日报里就会出现两条一模一样的。
    """
    result = dedup(
        [
            item("https://e.com/p?utm_source=weibo", "一条足够长的新闻标题", "正文" * 50),
            item("https://e.com/p?utm_source=wechat", "一条足够长的新闻标题", "正文" * 50),
        ]
    )

    assert len(result.clusters) == 1
    assert result.merged_by_url == 1
    assert len(result.clusters[0].members) == 2


# --- 第二级：SimHash / tier two ------------------------------------------------


def test_reposted_article_merges_by_simhash() -> None:
    """同一篇稿子被两个号转发，链接不同但正文几乎一致，应合并。"""
    body = "OpenAI 今天正式发布了新一代多模态大模型，该模型支持视频理解。" * 8
    result = dedup(
        [
            item("https://a.com/1", "OpenAI 发布新一代多模态大模型", body, source="a"),
            item("https://b.com/2", "OpenAI 发布新一代多模态大模型", body + "。", source="b"),
        ]
    )

    assert len(result.clusters) == 1
    assert result.merged_by_simhash == 1


def test_distinct_events_are_not_merged() -> None:
    """
    不同事件绝不能被合并。

    错合并比漏合并危险得多：漏合并只是日报里出现两条相似新闻，读者看得出来；
    错合并是把两件事混成一条，读者被误导且无从察觉。
    """
    result = dedup(
        [
            item("https://a.com/1", "OpenAI 发布新一代多模态大模型", "OpenAI 的模型。" * 30),
            item("https://b.com/2", "苹果公布季度财报营收超预期", "苹果的财报。" * 30),
            item("https://c.com/3", "英伟达新一代显卡开始量产出货", "英伟达的显卡。" * 30),
        ]
    )

    assert len(result.clusters) == 3
    assert result.merged_total == 0


# --- 代表条目与来源 / representative and references -----------------------------


def test_representative_is_the_longest_body() -> None:
    """
    多源报道同一件事时，代表条目取正文最长的那篇——内容最全的最适合做摘要。

    这里也顺带验证了「签名只取正文前 500 字」的设计：两个版本开头完全相同、
    只是其中一个在后面多了内容，仍应被判为同一件事。用全文比对的话，
    转载版常见的追加内容会把本来相同的两篇硬生生拉开。
    """
    head = "同一件事的报道内容。" * 60  # 600 字，超过 500 字的签名窗口
    result = dedup(
        [
            item("https://a.com/1", "同一个事件的标题", head, source="a"),
            item("https://b.com/2", "同一个事件的标题", head + "另外还补充了后续进展。" * 20, source="b"),
        ]
    )

    assert len(result.clusters) == 1, "开头相同的转载版应被判为同一件事"
    assert result.clusters[0].canonical.url == "https://b.com/2"


def test_refs_collect_every_member_link() -> None:
    """
    来源链接汇总全部成员，不只是被选中的那家。

    多源报道时读者应该看到全部出处——这也是 references.md 的要求。
    """
    body = "同一件事的报道。" * 20
    result = dedup(
        [
            item("https://a.com/1", "同一个事件的标题", body, source="a"),
            item("https://b.com/2", "同一个事件的标题", body, source="b"),
        ]
    )

    refs = result.clusters[0].refs
    assert set(refs) == {"https://a.com/1", "https://b.com/2"}
    assert len(refs) == len(set(refs)), "refs 必须去重"


# --- 计数与边界 / accounting and edges ------------------------------------------


def test_counts_reconcile() -> None:
    """计数要对得上账，人看到「3 条变 2 个」时要知道另一条去哪了。"""
    body = "重复的正文内容。" * 20
    items = [
        item("https://e.com/p?utm_source=a", "第一个事件的标题", body),
        item("https://e.com/p?utm_source=b", "第一个事件的标题", body),
        item("https://x.com/1", "完全不同的第二个事件", "另一件事。" * 30),
    ]

    result = dedup(items)

    assert result.raw_count == 3
    assert len(result.clusters) == 2
    assert result.merged_total == 1
    assert "3 条 → 2 个事件" in result.summary()


def test_empty_input() -> None:
    """空输入返回空结果，不崩。"""
    result = dedup([])
    assert result.clusters == []
    assert result.raw_count == 0


def test_single_item() -> None:
    """单条输入就是单个 cluster。"""
    result = dedup([item("https://e.com/1", "只有一条新闻的标题", "正文" * 30)])
    assert len(result.clusters) == 1
    assert result.merged_total == 0


# --- 第三级：语义向量 / tier three ----------------------------------------------


def test_embedding_failure_degrades_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    语义聚类不可用时降级，不报错。

    第三级要加载 GB 级模型，公司网络下可能下载不到。它是锦上添花——
    为它中断整期日报是本末倒置，前两级照常工作只是合并得少一些。
    """
    # 注意：dna.pipeline 把 dedup 函数重导出了，属性查找会拿到函数而不是模块，
    # 因此这里必须用 import_module 明确要模块对象。
    dedup_module = importlib.import_module("dna.pipeline.dedup")

    def boom(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("模型下载失败")

    monkeypatch.setattr(dedup_module, "_encode", boom)

    result = dedup(
        [
            item("https://a.com/1", "第一个事件的标题", "正文A。" * 30),
            item("https://b.com/2", "第二个事件的标题", "正文B。" * 30),
        ],
        use_embeddings=True,
    )

    assert len(result.clusters) == 2, "降级后前两级仍应正常工作"
    assert result.merged_by_vector == 0


def test_embedding_tier_merges_semantically_similar_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    第三级：用词不同但说的是同一件事，靠向量合并。

    注入假向量，避免测试依赖 GB 级模型与网络。
    """
    # 注意：dna.pipeline 把 dedup 函数重导出了，属性查找会拿到函数而不是模块，
    # 因此这里必须用 import_module 明确要模块对象。
    dedup_module = importlib.import_module("dna.pipeline.dedup")

    # 前两条向量几乎相同（同一件事），第三条正交（另一件事）
    fake = [[1.0, 0.0], [0.99, 0.14], [0.0, 1.0]]
    monkeypatch.setattr(dedup_module, "_encode", lambda texts: fake[: len(texts)])

    result = dedup(
        [
            item("https://a.com/1", "国内厂商发布千亿参数模型", "甲的报道。" * 30),
            item("https://b.com/2", "某公司推出超大规模语言模型", "乙的报道。" * 30),
            item("https://c.com/3", "显卡价格本季度大幅下调", "丙的报道。" * 30),
        ],
        use_embeddings=True,
    )

    assert len(result.clusters) == 2
    assert result.merged_by_vector == 1
