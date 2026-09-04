"""
test_longform.py —— 长文案单元测试 / Long-form script unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/narration/test_longform.py -v

对应的人工验证 / Matching manual check:
    dna produce <id> --kind longform --variant interview
    # ⚠️ 单篇 5~9 次调用，是最贵的产物，**不要频繁跑**

覆盖 / Covers:
    1. **正文太短时拒绝生成**，且在花钱之前就拒绝（can_build_longform）
    2. 目标字数跟正文体量走，不硬凑 10~15 分钟；有上下限夹逼
    2b. **时长窗口可由 profile 覆盖**，且 profile 默认值与代码常量一致
        （两处不一致时不会报错，只会让改配置的人以为生效了）
    2c. 字数预算走 `prompt_char_budget`（中英混排密度），不是纯中文语速
    3. 提纲调用 1 次 + 每节 1 次，calls 计数正确（费用可见）
    4. 逐节展开时**带上一节的结尾**——分段生成最容易在接缝处断裂
    5. 提纲提示词要求「必须有一节讲局限」
    6. 访谈提示词要求主持人不讲技术内容、不互相吹捧
    7. 访谈产出 host/guest 两种角色；专题产出单一 narrator
    8. **模型自创角色名时归到默认角色而不是丢掉**（丢掉会让这节出现空洞）
    9. 两种模式产出**同一种 JSON 结构**，TTS 侧不必分两条路径
   10. Markdown 版带角色前缀，JSON 版带 turns

为什么分段生成 / Why sectioned generation:
    2700~4000 字远超单次输出的可靠范围。一次生成的话模型会在中途丢失结构、
    重复论述、越写越水——而「不要白话」正是这批文案的核心要求。
    One call cannot hold four thousand characters together: the model loses structure
    part-way and thins out, which is exactly what this format cannot afford.

预期 / Expected:
    22 passed；耗时 < 1s；**全部使用假 provider，零 LLM 调用、零费用**
"""

from __future__ import annotations

import json

import pytest

from dna.core.models import Article
from dna.narration.duration import prompt_char_budget
from dna.narration.longform import (
    MAX_TARGET_SECONDS,
    MIN_BODY_FOR_LONGFORM,
    MIN_TARGET_SECONDS,
    LongformMode,
    build_longform,
    can_build_longform,
    plan_target_chars,
    plan_target_seconds,
)
from tests.llm.fakes import ScriptedProvider


def article(chars: int = 3000) -> Article:
    return Article(
        url="https://e.com/1",
        title="某公司发布新一代推理引擎",
        text="这是原文的技术内容。" * (chars // 10),
        extraction_ok=True,
    )


def outline_reply(n: int = 3) -> str:
    return json.dumps(
        {
            "sections": [
                {
                    "title": f"第{i}节标题",
                    "points": [f"要点{i}"],
                    "target_chars": 400,
                }
                for i in range(1, n + 1)
            ]
        },
        ensure_ascii=False,
    )


def section_reply(*turns: tuple[str, str]) -> str:
    return json.dumps(
        {"turns": [{"speaker": s, "text": t} for s, t in turns]}, ensure_ascii=False
    )


def joined(messages) -> str:
    return " ".join(m.content for m in messages)


# --- 体量门槛 / the length gate ------------------------------------------------


def test_short_article_is_rejected_before_spending_anything() -> None:
    """
    正文太短时**在花钱之前**就拒绝。

    长文案是最贵的产物（提纲 1 次 + 每节 1 次）。不够料的文章做出来一定是注水稿，
    先判断再决定要不要花这个钱。
    """
    ok, reason = can_build_longform(article(400))

    assert not ok
    assert "不足" in reason
    assert "注水" in reason


def test_rejection_happens_without_any_llm_call() -> None:
    """拒绝路径一次调用都不能发。"""
    llm = ScriptedProvider("fake", [outline_reply()])

    with pytest.raises(ValueError, match="不足"):
        build_longform(article(400), llm)

    assert llm.call_count == 0


def test_threshold_is_the_documented_one() -> None:
    """门槛值本身要有测试，调整时会立刻暴露。"""
    assert MIN_BODY_FOR_LONGFORM == 800
    assert can_build_longform(article(MIN_BODY_FOR_LONGFORM))[0]
    assert not can_build_longform(article(MIN_BODY_FOR_LONGFORM - 100))[0]


# --- 目标字数 / target length --------------------------------------------------


def test_target_scales_with_the_source() -> None:
    """
    时长跟文章体量走，不硬凑 15 分钟。

    宁可产出 8 分钟也不注水凑长度——注水恰恰是「白话」的来源，
    模型会用背景铺垫和重复论述填满剩下的时间。
    """
    assert plan_target_chars(article(1000)) < plan_target_chars(article(3000))


def test_profile_default_window_agrees_with_the_code_floor() -> None:
    """
    `profile.yaml` 的默认窗口必须与代码里的常量一致。

    两处写不同的值就会出现「配置说 10 分钟起、代码按 5 分钟起」这种谁也说不清的
    行为：改配置的人以为生效了，读代码的人以为常量是权威——而不一致时不会报错。
    先前 profile 默认是 (600, 900) 而代码下限是 300，正是这种漂移。
    """
    from dna.core.config import Profile

    assert Profile().longform_duration_seconds == (
        int(MIN_TARGET_SECONDS),
        int(MAX_TARGET_SECONDS),
    )


def test_window_can_be_overridden_by_the_caller() -> None:
    """
    时长窗口可以由调用方（即 profile）覆盖，不是写死的常量。

    否则 `longform_duration_seconds` 就是死配置——改了没反应，
    比不提供这个配置更糟。
    """
    wide = plan_target_seconds(article(50_000), low=300, high=1800)
    narrow = plan_target_seconds(article(50_000), low=300, high=600)

    assert wide == 1800
    assert narrow == 600


def test_target_is_clamped_at_both_ends() -> None:
    """
    上限防止把一篇长文拉成半小时；下限保证短文也有基本结构。

    **只在时长上夹逼**，字数由它换算——同一件事的两种单位各设一套上下限，
    迟早会漂移到互相矛盾（先前就出现过 300 秒下限换算出 1350 字、
    却又另设 1200 字下限的情况）。
    """
    assert plan_target_seconds(article(50_000)) == MAX_TARGET_SECONDS
    assert plan_target_seconds(article(MIN_BODY_FOR_LONGFORM)) == MIN_TARGET_SECONDS
    # 字数始终由时长换算而来，且走中英混排密度——按纯中文 4.5 字/秒折算，
    # 「15 分钟」只会要到 4050 字，而实测 3900 字的技术稿只有 7.4 分钟。
    assert plan_target_chars(article(50_000)) == prompt_char_budget(MAX_TARGET_SECONDS)
    assert plan_target_chars(article(50_000)) > int(MAX_TARGET_SECONDS * 4.5)


def test_target_is_derived_in_duration_space_not_character_space() -> None:
    """
    目标由**口播时长**推导，不是字符数。

    两者不是一回事：技术稿里 `UD-Q8_K_XL` 这类标识符一个占十几个字符，
    念出来的时间却远不成比例。按字符数推导会让「4000 字原文」与
    「4000 字成稿」看起来对等，实际口播时长差出一倍——真机验证时正是这样
    才发现的（目标 15 分钟、实测 7.4 分钟）。
    """
    plain = Article(url="https://e.com/1", title="T", text="这是中文技术内容。" * 200, extraction_ok=True)
    # 同样字符数，但一半是英文标识符——念出来短得多
    identifier_heavy = Article(
        url="https://e.com/2",
        title="T",
        text="这是内容 DeepSeek-V4-Flash-0731 UD-Q8_K_XL 说明。" * 100,
        extraction_ok=True,
    )

    assert plan_target_seconds(plain) > plan_target_seconds(identifier_heavy), (
        "字符数相近但英文标识符多的文章，目标时长应更短"
    )


def test_target_seconds_never_exceeds_the_window() -> None:
    """无论原文多长，目标都不超过 15 分钟——用户给的上限。"""
    assert plan_target_seconds(article(50_000)) == MAX_TARGET_SECONDS


# --- 分段生成 / sectioned generation -------------------------------------------


def test_calls_are_outline_plus_one_per_section() -> None:
    """
    调用次数 = 1 次提纲 + 每节 1 次。

    calls 要如实反映，GUI 才能在按钮上标出预估成本。
    """
    llm = ScriptedProvider(
        "fake",
        [outline_reply(3), section_reply(("narrator", "第一节内容")),
         section_reply(("narrator", "第二节内容")), section_reply(("narrator", "第三节内容"))],
    )
    result = build_longform(article(), llm, mode=LongformMode.FEATURE)

    assert llm.call_count == 4
    assert result.calls == 4
    assert len(result.sections) == 3


def test_each_section_carries_the_previous_tail() -> None:
    """
    展开每一节时带上一节的结尾。

    分开生成的稿子最容易在接缝处断裂：上一节刚说完某个数字，
    这一节又从头介绍一遍同一件事。给它看见上文就能自然接上。
    """
    llm = ScriptedProvider(
        "fake",
        [outline_reply(2), section_reply(("narrator", "第一节讲了量化方案的细节")),
         section_reply(("narrator", "第二节内容"))],
    )
    build_longform(article(), llm, mode=LongformMode.FEATURE)

    # messages[0] 是提纲，[1] 是第一节，[2] 是第二节
    second_section = joined(llm.messages[2])
    assert "量化方案的细节" in second_section
    assert "自然衔接" in second_section
    assert "不要重复" in second_section


def test_first_section_has_no_previous_context() -> None:
    """第一节没有上文，不该凭空塞一句「上一节的结尾是」。"""
    llm = ScriptedProvider(
        "fake", [outline_reply(1), section_reply(("narrator", "内容"))]
    )
    build_longform(article(), llm, mode=LongformMode.FEATURE)

    assert "上一节的结尾是" not in joined(llm.messages[1])


def test_each_section_sees_the_whole_outline_with_boundaries_marked() -> None:
    """
    每节的提示词里要有**完整提纲**，并标出哪几节已讲过、哪几节留给后面。

    只给上一节的结尾是不够的。真机实测（CES 那篇，7 节）第 7 节把第 6 节的产品
    几乎逐个重讲了一遍——9 个实体全部重复。因为写第 7 节时模型只看见第 6 节最后
    120 字，不知道前面六节各自覆盖了什么，于是把「其他创新硬件」理解成
    「再说一遍我知道的硬件」。
    """
    llm = ScriptedProvider(
        "fake",
        [outline_reply(3), section_reply(("narrator", "第一节")),
         section_reply(("narrator", "第二节")), section_reply(("narrator", "第三节"))],
    )
    build_longform(article(), llm, mode=LongformMode.FEATURE)

    # messages[2] 是第二节：前一节已讲过，后一节留给后面
    second = joined(llm.messages[2])
    assert "第1节标题（已讲过）" in second
    assert "第2节标题　← 现在写这节" in second
    assert "第3节标题（留给后面）" in second
    assert "一个产品名、一个数字都不要重复" in second
    assert "不要去写别节的内容凑长度" in second


def test_outline_map_marks_every_position() -> None:
    """
    提纲地图是纯函数，三种标记都要正确——它是防重复的全部依据。
    """
    from dna.narration.longform import SectionPlan, _outline_map

    sections = [
        SectionPlan(title=f"第{i}节", points=["要点"], target_chars=300)
        for i in range(1, 4)
    ]
    text = _outline_map(sections, 2)

    assert text.splitlines()[0].endswith("（已讲过）")
    assert "← 现在写这节" in text.splitlines()[1]
    assert text.splitlines()[2].endswith("（留给后面）")


def test_outline_forbids_overlapping_and_catch_all_sections() -> None:
    """
    提纲提示词必须要求**各节互斥**，并禁止「其他」这类兜底节名。

    CES 那篇的提纲里同时有「智能眼镜与 AR 赛道」和「其他创新硬件与生态」——
    边界模糊的两节必然各自把同一批产品讲一遍。兜底节没有明确边界，
    写的时候只能去重复前面的内容。
    """
    llm = ScriptedProvider("fake", [outline_reply(1), section_reply(("narrator", "内容"))])
    build_longform(article(), llm, mode=LongformMode.FEATURE)

    outline_prompt = joined(llm.messages[0])
    assert "各节内容必须互斥" in outline_prompt
    assert "不要用「其他」「其余」「补充」命名任何一节" in outline_prompt


# --- 提示词 / prompts -----------------------------------------------------------


def test_outline_demands_a_limitations_section() -> None:
    """
    提纲必须要求有一节讲局限。

    只讲好处的长稿没有可信度——听众听十分钟不是为了听广告。
    """
    llm = ScriptedProvider("fake", [outline_reply(1), section_reply(("narrator", "内容"))])
    build_longform(article(), llm, mode=LongformMode.FEATURE)

    outline_prompt = joined(llm.messages[0])
    assert "必须有一节讲局限" in outline_prompt
    assert "听十分钟不是为了听广告" in outline_prompt


def test_outline_forbids_a_recap_section() -> None:
    """禁止规划「总结回顾」——把前面重说一遍就是注水。"""
    llm = ScriptedProvider("fake", [outline_reply(1), section_reply(("narrator", "内容"))])
    build_longform(article(), llm, mode=LongformMode.FEATURE)

    assert "那是注水" in joined(llm.messages[0])


def test_interview_prompt_separates_the_two_roles() -> None:
    """
    访谈里主持人只提问、不讲技术内容。

    不约束的话两个角色会说得一模一样，双角色就失去了意义——
    也让 TTS 分配的两个音色显得莫名其妙。
    """
    llm = ScriptedProvider(
        "fake", [outline_reply(1), section_reply(("host", "问题"), ("guest", "回答"))]
    )
    build_longform(article(), llm, mode=LongformMode.INTERVIEW)

    section_prompt = joined(llm.messages[1])
    assert "主持人只提问、追问、转场，不讲技术内容" in section_prompt


def test_interview_prompt_forbids_mutual_flattery() -> None:
    """
    禁止互相吹捧。

    「您说得太好了」这类话占时长、没信息，而且一听就假——
    十五分钟的稿子经不起这种消耗。
    """
    llm = ScriptedProvider(
        "fake", [outline_reply(1), section_reply(("host", "问"), ("guest", "答"))]
    )
    build_longform(article(), llm, mode=LongformMode.INTERVIEW)

    assert "不要互相吹捧" in joined(llm.messages[1])


def test_professionalism_rules_apply_to_longform_too() -> None:
    """专业性约束对长文案同样生效。"""
    llm = ScriptedProvider("fake", [outline_reply(1), section_reply(("narrator", "内容"))])
    build_longform(article(), llm, mode=LongformMode.FEATURE)

    assert "准确的技术术语" in joined(llm.messages[0])


# --- 角色与输出格式 / speakers and output format --------------------------------


def test_interview_produces_two_roles() -> None:
    """访谈产出 host 与 guest 两种角色，供 TTS 分配两个音色。"""
    llm = ScriptedProvider(
        "fake",
        [outline_reply(1), section_reply(("host", "这个方案的核心是什么"), ("guest", "核心是极限量化"))],
    )
    result = build_longform(article(), llm, mode=LongformMode.INTERVIEW)

    assert {t.speaker for t in result.turns} == {"host", "guest"}


def test_feature_produces_a_single_role() -> None:
    """专题只有 narrator 一个角色。"""
    llm = ScriptedProvider("fake", [outline_reply(1), section_reply(("narrator", "内容"))])
    result = build_longform(article(), llm, mode=LongformMode.FEATURE)

    assert {t.speaker for t in result.turns} == {"narrator"}


def test_invented_speaker_is_reassigned_not_dropped() -> None:
    """
    模型自创角色名时归到默认角色，**不丢掉这段内容**。

    内容是好的，只是标签错了。丢掉会让这一节凭空少一段，
    而分段生成本来就难保证节与节之间连贯。
    """
    llm = ScriptedProvider(
        "fake",
        [outline_reply(1), section_reply(("专家", "这段技术内容是有价值的"), ("guest", "补充说明"))],
    )
    result = build_longform(article(), llm, mode=LongformMode.INTERVIEW)

    assert len(result.turns) == 2
    assert "这段技术内容是有价值的" in result.text
    assert all(t.speaker in ("host", "guest") for t in result.turns)


def test_markdown_carries_speaker_prefixes() -> None:
    """人读的版本要带角色名，否则对谈读起来分不清谁在说。"""
    llm = ScriptedProvider(
        "fake", [outline_reply(1), section_reply(("host", "提问内容"), ("guest", "回答内容"))]
    )
    result = build_longform(article(), llm, mode=LongformMode.INTERVIEW)

    assert "**主持人：**" in result.text
    assert "**嘉宾：**" in result.text


def test_both_modes_emit_the_same_json_shape() -> None:
    """
    两种模式产出同一种 JSON 结构。

    TTS 侧照着 speaker 分配音色即可，不必知道这篇是专题还是访谈——
    否则 P6/P7 就要写两条代码路径。
    """
    feature_llm = ScriptedProvider(
        "fake", [outline_reply(1), section_reply(("narrator", "内容"))]
    )
    interview_llm = ScriptedProvider(
        "fake", [outline_reply(1), section_reply(("host", "问"), ("guest", "答"))]
    )

    feature = build_longform(article(), feature_llm, mode=LongformMode.FEATURE).to_json_dict()
    interview = build_longform(article(), interview_llm, mode=LongformMode.INTERVIEW).to_json_dict()

    assert feature.keys() == interview.keys()
    assert set(feature) == {"mode", "speakers", "est_seconds", "turns"}
    assert feature["mode"] == "feature"
    assert interview["mode"] == "interview"
    assert all("speaker" in t and "text" in t for t in feature["turns"])
