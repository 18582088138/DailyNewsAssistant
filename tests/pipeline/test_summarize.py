"""
test_summarize.py —— 摘要节点单元测试 / Summarisation node unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/pipeline/test_summarize.py -v

对应的人工验证 / Matching manual check:
    dna digest --limit 3        # 真机跑三条，人工看摘要质量（会计费）

覆盖 / Covers:
    1. 提示词里带上标题与正文（**测试重点在这里**）
    2. 正文超长时被截到 MAX_BODY_CHARS，不把整篇塞进去烧 token
    3. 多源报道时提示词里说明「另有 N 家媒体报道同一事件」
    4. 正文为空（抽取降级）时提示词明确要求「不要编造细节」
    5. **提示词要求信息完整**：原文讲了几件事就都要覆盖到
    6. **提示词给出指标取舍的优先级**：榜单与架构参数优先于文件体积这类次要细节
    7. 正常响应被解析成 summary + tags
    8. **LLM 失败时降级为标题，不抛异常**，且 degraded 标记为 True
    9. 模型返回空摘要时也退回标题
   10. tags 去空白、丢空串
   11. 批量：一条失败不影响其余条目
   12. 批量是串行的（DeepSeek 有速率限制，并发换来的是 429）

为什么测提示词而不是测模型输出 / Why the prompt, not the model output:
    断言「我们问了什么」可靠且免费；断言「模型答了什么」既要花钱又每次都飘。
    真实模型的输出质量在阶段验收时人工看一次，不放进单元测试。
    Asserting what we asked is reliable and free; asserting what the model answered
    costs money and drifts. Real output quality is reviewed by hand at sign-off.

预期 / Expected:
    耗时 < 1s；**全部使用假 provider，零 LLM 调用、零费用**
"""

from __future__ import annotations

import json

from dna.core.errors import RateLimitError
from dna.core.models import Cluster, NewsItem, SourceKind
from dna.pipeline.summarize import (
    MAX_BODY_CHARS,
    build_messages,
    summarize_all,
    summarize_cluster,
)
from tests.llm.fakes import ScriptedProvider


def make_item(title: str, text: str = "", *, source: str = "s1", url: str = "https://e.com/1") -> NewsItem:
    return NewsItem(
        id=url,
        source_id=source,
        via=SourceKind.RSS,
        url=url,
        canonical_url=url,
        title=title,
        text=text,
    )


def make_cluster(*items: NewsItem) -> Cluster:
    return Cluster(id=items[0].id, members=list(items), canonical_url=items[0].canonical_url)


def reply(summary: str = "这是一条摘要，说明了核心事实。", tags: list[str] | None = None) -> str:
    """构造一条合法的模型响应 / Build a well-formed model response."""
    return json.dumps({"summary": summary, "tags": tags or ["大模型"]}, ensure_ascii=False)


# --- 提示词构建 / prompt construction ------------------------------------------


def test_prompt_carries_title_and_body() -> None:
    """提示词必须带上标题与正文——这是摘要节点的全部输入。"""
    cluster = make_cluster(make_item("OpenAI 发布新一代模型", "正文的具体内容在这里。"))
    text = "\n".join(m.content for m in build_messages(cluster))

    assert "OpenAI 发布新一代模型" in text
    assert "正文的具体内容在这里。" in text


def test_long_body_is_truncated() -> None:
    """
    正文超长时截断。

    3000 字足够写出准确摘要，再多只是线性增加 input token 费用——
    一期日报几十条，不截断的成本差别很可观。
    """
    cluster = make_cluster(make_item("某标题", "正" * 10_000))
    user_message = build_messages(cluster)[-1].content

    assert len(user_message) < MAX_BODY_CHARS + 500
    assert "正" * 100 in user_message


def test_multi_source_is_stated_in_the_prompt() -> None:
    """
    多源报道时告诉模型，让它写共同的核心事实而不是某一家的角度。
    """
    cluster = make_cluster(
        make_item("甲媒体的标题", "正文", url="https://a.com/1"),
        make_item("乙媒体的标题", "正文", url="https://b.com/2"),
        make_item("丙媒体的标题", "正文", url="https://c.com/3"),
    )
    text = build_messages(cluster)[-1].content

    assert "另有 2 家媒体报道同一事件" in text
    assert "乙媒体的标题" in text


def test_missing_body_prompt_forbids_fabrication() -> None:
    """
    正文抓取失败时，提示词必须明确禁止编造细节。

    不说的话模型会自信地补出一段看似合理、实则凭空捏造的内容——
    而这会原样印进日报，读者无从分辨。
    """
    cluster = make_cluster(make_item("知乎那篇被反爬拦下的文章", ""))
    text = build_messages(cluster)[-1].content

    assert "不要编造" in text
    assert "只有标题可用" in text


def test_system_prompt_is_present() -> None:
    """system 消息定义角色与格式要求，必须存在。"""
    messages = build_messages(make_cluster(make_item("某标题", "正文")))
    assert messages[0].role == "system"
    assert "日报" in messages[0].content


def test_prompt_demands_information_completeness() -> None:
    """
    摘要必须**覆盖原文的全部主干**，不能只写前一半。

    实测问题：一篇既讲模型发布、又讲第三方量化方案的文章，模型只写了发布部分，
    量化方案整段丢失。1~2 句确实装不下所有细节，但「有几件事」这个层级的信息
    必须完整——读者靠它判断这条值不值得点开。
    """
    prompt = build_messages(make_cluster(make_item("某标题", "正文")))[0].content

    assert "原文讲了几件事就都要覆盖到" in prompt
    assert "两件事各占一句，不要只写前一半" in prompt


def test_prompt_ranks_which_metrics_to_keep() -> None:
    """
    提示词必须给出**指标的取舍优先级**，而不是只说「保留关键数字」。

    「1~2 句」是硬约束，装不下所有数字。不给优先级，模型就按原文出现顺序取，
    而技术文章的开头往往是文件体积、依赖版本这类次要细节。
    实测对照：模型自选写了「模型文件 167GB」，人工撰写的参考摘要在同一位置
    写的是「激活参数 13B」——后者才是决定这条资讯价值的数字。
    """
    prompt = build_messages(make_cluster(make_item("某标题", "正文")))[0].content

    assert "按这个优先级舍弃低的" in prompt
    assert "榜单排名与得分" in prompt  # 优先级最低的那一档
    assert "激活参数" in prompt
    assert "次要细节" in prompt


# --- 响应解析 / response parsing -----------------------------------------------


def test_normal_response_is_parsed() -> None:
    """正常响应解析成 summary 与 tags。"""
    llm = ScriptedProvider("fake", [reply("模型写出来的摘要内容，说明了这条资讯的核心事实。", ["大模型", "开源"])])
    result = summarize_cluster(make_cluster(make_item("某标题", "正文")), llm)

    assert result.summary == "模型写出来的摘要内容，说明了这条资讯的核心事实。"
    assert result.tags == ["大模型", "开源"]
    assert result.degraded is False


def test_tags_are_stripped_and_emptied_entries_dropped() -> None:
    """标签去空白、丢空串——模型偶尔会返回带空格或空的标签。"""
    llm = ScriptedProvider("fake", [reply("一条长度合规的摘要内容，说明核心事实。", ["  大模型  ", "", "开源"])])
    result = summarize_cluster(make_cluster(make_item("某标题", "正文")), llm)

    assert result.tags == ["大模型", "开源"]


def test_empty_summary_falls_back_to_the_title() -> None:
    """模型返回空摘要时退回标题——日报里不能出现空条目。"""
    llm = ScriptedProvider("fake", [json.dumps({"summary": "          ", "tags": []})])
    result = summarize_cluster(make_cluster(make_item("这是一条新闻的标题", "正文")), llm)

    assert result.summary == "这是一条新闻的标题"


# --- 失败降级 / degradation ----------------------------------------------------


def test_llm_failure_degrades_to_the_title() -> None:
    """
    LLM 失败时用标题当摘要，**不抛异常**。

    标题本身就是一句话摘要，足以让读者判断要不要点开。因为一次 API 抖动
    就把一条真实资讯从日报里删掉，代价大得多。
    """
    llm = ScriptedProvider("fake", [RateLimitError("429 限流")])
    result = summarize_cluster(make_cluster(make_item("这是一条新闻的标题", "正文")), llm)

    assert result.summary == "这是一条新闻的标题"
    assert result.degraded is True


def test_malformed_json_degrades_rather_than_raises() -> None:
    """模型返回的不是合法 JSON 时同样降级，不中断整期日报。"""
    llm = ScriptedProvider("fake", ["这不是 JSON", "还是不是 JSON", "仍然不是"])
    result = summarize_cluster(make_cluster(make_item("这是一条新闻的标题", "正文")), llm)

    assert result.degraded is True
    assert result.summary == "这是一条新闻的标题"


# --- 批量 / batch --------------------------------------------------------------


def test_batch_isolates_failures() -> None:
    """
    一条失败不影响其余条目。

    日报是无人值守跑的，中间一条翻车不该让整期作废。
    """
    clusters = [
        make_cluster(make_item("第一条新闻标题", "正文", url="https://e.com/1")),
        make_cluster(make_item("第二条新闻标题", "正文", url="https://e.com/2")),
        make_cluster(make_item("第三条新闻标题", "正文", url="https://e.com/3")),
    ]
    llm = ScriptedProvider("fake", [reply("第一条的摘要内容，说明核心事实。"), RateLimitError("429"), reply("第三条的摘要内容，说明核心事实。")])

    results = summarize_all(clusters, llm)

    assert [r.summary for r in results] == [
        "第一条的摘要内容，说明核心事实。",
        "第二条新闻标题",
        "第三条的摘要内容，说明核心事实。",
    ]
    assert [r.degraded for r in results] == [False, True, False]


def test_batch_calls_once_per_cluster_in_order() -> None:
    """
    每个 cluster 一次调用，按顺序串行。

    并发打给 DeepSeek 换来的是 429 与重试，实际并不更快，还让费用与日志难以追踪。
    """
    clusters = [
        make_cluster(make_item("第一条新闻标题", "正文A", url="https://e.com/1")),
        make_cluster(make_item("第二条新闻标题", "正文B", url="https://e.com/2")),
    ]
    llm = ScriptedProvider("fake", [reply("第一条的摘要，说明核心事实。"), reply("第二条的摘要，说明核心事实。")])

    summarize_all(clusters, llm)

    assert llm.call_count == 2
    # chat_json 会在末尾追加一条 Schema 指令消息，所以在整组消息里找正文
    first = " ".join(m.content for m in llm.messages[0])
    second = " ".join(m.content for m in llm.messages[1])
    assert "正文A" in first and "正文B" not in first
    assert "正文B" in second


def test_empty_batch() -> None:
    """空输入不调用 LLM。"""
    llm = ScriptedProvider("fake", [])
    assert summarize_all([], llm) == []
    assert llm.call_count == 0
