"""
test_script_builder.py —— 短视频与口播文案单元测试 / Short-video and voice-over tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/narration/test_script_builder.py -v

对应的人工验证 / Matching manual check:
    dna produce <id> --kind shortvideo    # 人工看主副标题与技术准确性（会计费）
    dna produce <id> --kind narration

覆盖 / Covers:
    1. 提示词包含专业性约束（术语准确、数值与原文一致、不确定的不写）
    2. **信息密度是第一要求**，且逐条点名了禁止出现的填充句式
    3. 提示词包含正文，且超长时截断到 SCRIPT_BODY_LIMIT
    4. 短视频要求**覆盖文章主干**（第一句给事件、每句换一个新事实）
    5. 口播提示词要求技术深度，并要求提到局限
    6. 结尾引导语由调用方传入，不写死在提示词里
    7. **字数预算按中英混排密度放大**（168~235 字而非 112~157 字）
    8. 正文为空时提示词禁止编造
    9. **达标时不回炉**——一次调用就结束
   10. **超长时回炉，并把上一稿带回去**（不带的话模型会从头重写，丢掉写对的部分）
   11. **回炉差值按上一稿实测的字符密度换算**，不是预设的 4.5 字/秒
   12. 回炉上限 2 次，仍不达标时**返回结果而不是报错**
   13. calls 计数正确反映实际调用次数（费用可见）
   14. 两种文案都返回主副标题

为什么测提示词而不是模型输出 / Why the prompt, not the output:
    断言「我们要求了什么」可靠且免费；断言「模型写得好不好」既贵又每次都飘。
    文案质量在阶段验收时人工审阅。

但**字数预算必须断言具体数值** / The character budget is asserted numerically:
    它是「信息量不够」的机械原因，不是风格问题。按纯中文语速 4.5 字/秒换算，
    30 秒只要 135 字，而实测技术稿装得下约 200 字——模型照少的写，时长照样达标，
    回炉环节也不触发，于是错误完全静默。所以这条必须锁死在测试里。
    It is the mechanical cause of thin copy rather than a matter of style. Converting at
    4.5 characters per second asks for 135 where 200 fit; the draft still meets the
    duration, the rewrite loop never fires, and the defect is entirely silent. Hence the
    numeric assertion.

预期 / Expected:
    耗时 < 1s；**全部使用假 provider，零 LLM 调用、零费用**
"""

from __future__ import annotations

import json

from dna.core.models import Article
from dna.narration.script_builder import (
    DEFAULT_MAX_REWRITES,
    SCRIPT_BODY_LIMIT,
    build_narration,
    build_short_video,
)
from tests.llm.fakes import ScriptedProvider


def article(text: str = "正文内容。" * 60, title: str = "某公司发布新一代推理引擎") -> Article:
    return Article(url="https://e.com/1", title=title, text=text, extraction_ok=bool(text))


def short_reply(script: str, *, title: str = "推理成本砍半", subtitle: str = "吞吐提升 2.3 倍") -> str:
    return json.dumps(
        {"title": title, "subtitle": subtitle, "script": script}, ensure_ascii=False
    )


def narration_reply(
    script: str, *, title: str = "推理引擎开源", subtitle: str = "吞吐提升 2.3 倍"
) -> str:
    return json.dumps(
        {"title": title, "subtitle": subtitle, "script": script}, ensure_ascii=False
    )


def joined(messages) -> str:
    """把一次调用的全部消息拼起来 / Join one call's messages."""
    return " ".join(m.content for m in messages)


# --- 提示词 / prompts -----------------------------------------------------------


def test_prompt_carries_the_professionalism_rules() -> None:
    """
    专业性约束必须在提示词里。

    不写的话模型会输出「性能大幅提升，效果非常惊艳」——这正是用户明确不要的白话。
    """
    llm = ScriptedProvider("fake", [short_reply("字" * 200)])
    build_short_video(article(), llm)

    text = joined(llm.messages[0])
    assert "准确的技术术语" in text
    assert "必须与原文完全一致" in text
    assert "宁可不写" in text


def test_prompt_carries_the_body() -> None:
    """输入是正文而不是摘要——摘要已经把技术细节压掉了。"""
    llm = ScriptedProvider("fake", [short_reply("字" * 200)])
    build_short_video(article(text="这里是原文的具体技术细节。" * 20), llm)

    assert "这里是原文的具体技术细节。" in joined(llm.messages[0])


def test_long_body_is_truncated() -> None:
    """正文截到上限，不把整篇塞进去线性烧 input token。"""
    llm = ScriptedProvider("fake", [short_reply("字" * 200)])
    build_short_video(article(text="正" * 10_000), llm)

    user_message = llm.messages[0][1].content
    assert len(user_message) < SCRIPT_BODY_LIMIT + 300


def test_prompt_demands_information_density() -> None:
    """
    **信息密度是第一要求**，必须出现在提示词里。

    时长是固定的（平台限制，发布时还会加速播放），所以稿子的成败在于同样的秒数里
    装进多少事实。不写这条，模型会用形容词把时长填满——那才是「白话」的来源，
    而不是用词不够文雅。
    """
    llm = ScriptedProvider("fake", [short_reply("字" * 200)])
    build_short_video(article(), llm)

    text = joined(llm.messages[0])
    assert "信息密度优先" in text
    assert "每一句都必须携带至少一个具体信息点" in text


def test_prompt_bans_the_known_filler_patterns() -> None:
    """
    明确列出禁止出现的内容，而不是笼统地说「不要白话」。

    这几条都是从真机产出里挑出来的实际问题：模板句「这意味着什么」被原样写进稿子、
    第一人称「我最关注的是」、以及用形容词堆出来的结尾。
    笼统的禁令模型无法执行，逐条点名它才知道要避开什么。
    """
    llm = ScriptedProvider("fake", [short_reply("字" * 200)])
    build_short_video(article(), llm)

    text = joined(llm.messages[0])
    assert "第一人称主观表达" in text
    assert "让我们一起来看看" in text
    assert "用形容词堆出来的结尾" in text


def test_short_video_demands_the_whole_fact_chain() -> None:
    """
    短视频提示词要求**覆盖文章主干**，而不是只讲一个点。

    先前的版本要求「一条只讲一个点」，产出技术上正确却没把文章讲清楚：
    观众听完知道某个量化档位的困惑度没变，却不知道这是哪个模型、发布了没有。
    人工撰写的参考稿在 30 秒里放了六个事实，每句一个。
    """
    llm = ScriptedProvider("fake", [short_reply("字" * 200)])
    build_short_video(article(), llm)

    text = joined(llm.messages[0])
    assert "第一句必须是事件本身" in text
    assert "覆盖文章的主干，不要只挑一个点深挖" in text
    assert "每句换一个新事实" in text


def test_video_scripts_end_with_the_configured_sign_off() -> None:
    """
    结尾引导语由调用方传入，不写死在提示词里。

    这是账号品牌，换栏目就要换；而提示词是「怎么写好文案」的规则，
    两者变更频率完全不同，混在一起改一个就得动另一个。
    """
    llm = ScriptedProvider("fake", [short_reply("字" * 200)])
    build_short_video(article(), llm, cta="订阅频道，每天一条 AI 快讯")

    assert "订阅频道，每天一条 AI 快讯" in joined(llm.messages[0])


def test_narration_demands_technical_depth_and_limitations() -> None:
    """
    口播提示词必须要求技术深度，并要求提到局限。

    有深度是它区别于短视频稿的全部理由；只讲好处的稿子没有可信度。
    """
    llm = ScriptedProvider("fake", [narration_reply("字" * 500)])
    build_narration(article(), llm)

    text = joined(llm.messages[0])
    assert "必须有技术深度" in text
    assert "局限" in text
    assert "只讲好处的稿子没有可信度" in text


def test_missing_body_forbids_fabrication() -> None:
    """
    正文抓不到时禁止编造。

    不说的话模型会自信地补出一段看似合理的技术细节和数字，
    而它会被念进视频里，观众无从分辨。
    """
    llm = ScriptedProvider("fake", [short_reply("字" * 200)])
    build_short_video(article(text=""), llm)

    text = joined(llm.messages[0])
    assert "不要编造" in text


def test_target_length_uses_the_mixed_copy_budget_not_the_raw_rate() -> None:
    """
    提示词里给的字数按**中英混排的实际密度**放大，不是纯中文的 4.5 字/秒。

    这是「信息量不够」的机械原因：按 4.5 换算，25~35 秒是 112~157 字，
    而实测技术稿 30 秒装得下约 200 字。模型照 112~157 写完，时长恰好落在区间下沿
    ——**验收通过，信息量少掉三分之一**，回炉环节也不会触发。
    放大后是 168~235 字，与人工撰写的参考稿（199 字 / 27.8 秒）对得上。
    """
    llm = ScriptedProvider("fake", [short_reply("字" * 200)])
    build_short_video(article(), llm, low=25, high=35)

    text = joined(llm.messages[0])
    assert "168~235 字" in text
    assert "112~157 字" not in text, "按纯中文语速换算会系统性少要"


def test_config_char_window_goes_straight_into_the_prompt() -> None:
    """
    `profile.yaml` 里的字数区间**原样进提示词，也原样用于验收**。

    这是「字数为准」的全部含义：配置说 200~250，提示词就写 200~250，
    程序就按 200~250 检查。先前配置说秒数、提示词说字数、验收又看秒数，
    三处各说一套——200 字的稿子按混排是 27 秒（通过）、按纯中文是 44 秒（回炉），
    同一份稿子的命运取决于它里面有多少英文。
    """
    llm = ScriptedProvider("fake", [short_reply("字" * 220)])
    result = build_short_video(article(), llm, low=25, high=35, chars=(200, 250))

    assert "200~250 字" in joined(llm.messages[0])
    assert llm.call_count == 1, "落在配置的区间内就不该回炉"
    assert result.within_target


def test_english_window_is_converted_to_words_not_characters() -> None:
    """
    英文版把中文字数折算成**词数**，不是字符数。

    一个英文词的口播时长约等于两个半汉字。按字符 1:1 折算，英文稿会长出一倍多，
    而时长才是平台的硬约束。200~250 字 → 约 77~96 词。
    """
    reply = short_reply(" ".join(["benchmark"] * 85))
    llm = ScriptedProvider("fake", [reply])
    build_short_video(article(), llm, low=25, high=35, chars=(200, 250), lang="en")

    text = joined(llm.messages[0])
    assert "77-96 words" in text
    assert "200" not in text.split("script")[1][:80], "英文不该出现中文字数"


# --- 回炉重写 / the rewrite loop ------------------------------------------------


def test_in_range_result_needs_only_one_call() -> None:
    """达标就结束，不多花一次调用。"""
    llm = ScriptedProvider("fake", [short_reply("字" * 200)])  # 200 字，落在 168~235 的窗口内
    result = build_short_video(article(), llm, low=25, high=35)

    assert llm.call_count == 1
    assert result.calls == 1
    assert result.within_target
    assert 168 <= result.chars <= 235


def test_overlong_result_triggers_a_rewrite() -> None:
    """超长时回炉，第二稿达标就结束。"""
    llm = ScriptedProvider("fake", [short_reply("字" * 300), short_reply("字" * 200)])
    result = build_short_video(article(), llm, low=25, high=35)

    assert llm.call_count == 2
    assert result.calls == 2
    assert result.within_target


def test_rewrite_carries_the_previous_draft_back() -> None:
    """
    回炉时必须把上一稿带回去。

    只发一句「太长了」而不给上一稿，模型会从头重写一篇完全不同的稿子——
    上一稿里写对的技术细节也一起丢了，等于白花一次调用。
    """
    llm = ScriptedProvider("fake", [short_reply("超" * 300), short_reply("字" * 200)])
    build_short_video(article(), llm, low=25, high=35)

    second_call = llm.messages[1]
    roles = [m.role for m in second_call]
    assert "assistant" in roles, "上一稿必须作为 assistant 消息带回去"
    assert "超" * 300 in joined(second_call)


def test_rewrite_feedback_states_the_delta() -> None:
    """回炉的反馈里要有具体差多少字，不是「请缩短」。"""
    llm = ScriptedProvider("fake", [short_reply("超" * 300), short_reply("字" * 200)])
    build_short_video(article(), llm, low=25, high=35)

    # chat_json 会在末尾追加 Schema 指令，所以在整组消息里找反馈
    second_call = joined(llm.messages[1])
    assert "删掉 65 字" in second_call
    assert "保留具体数字" in second_call


def test_rewrites_are_capped() -> None:
    """
    回炉次数有上限。

    每次都是一次计费调用。两次之后仍不达标说明模型对这篇的长度判断已经稳定，
    再试下去是在烧钱换几个字。
    """
    llm = ScriptedProvider("fake", [short_reply("超" * 300)] * 10)
    result = build_short_video(article(), llm, low=25, high=35)

    assert llm.call_count == DEFAULT_MAX_REWRITES + 1
    assert result.calls == DEFAULT_MAX_REWRITES + 1


def test_still_off_target_returns_the_draft_rather_than_failing() -> None:
    """
    试到上限仍不达标时返回结果，**不抛异常**。

    一篇 45 秒的稿子仍然可用，人手动删两句就行；
    为此让整篇产物失败、把已经花掉的三次调用作废，是不划算的。
    """
    llm = ScriptedProvider("fake", [short_reply("超" * 300)] * 5)
    result = build_short_video(article(), llm, low=25, high=35)

    assert result.text  # 有内容
    assert result.within_target is False  # 但如实标记不达标
    assert result.chars > 235


def test_short_video_returns_title_and_subtitle() -> None:
    """主副标题要单独返回，供发布时直接用。"""
    llm = ScriptedProvider(
        "fake", [short_reply("字" * 200, title="推理成本砍半", subtitle="吞吐提升 2.3 倍")]
    )
    result = build_short_video(article(), llm)

    assert result.title == "推理成本砍半"
    assert result.subtitle == "吞吐提升 2.3 倍"


def test_narration_targets_the_longer_window() -> None:
    """口播稿按 1~2 分钟判定，不能套用短视频的区间。"""
    llm = ScriptedProvider("fake", [narration_reply("字" * 500)])  # 500 字，落在 405~810 的窗口内
    result = build_narration(article(), llm, low=60, high=120)

    assert llm.call_count == 1
    assert result.within_target
    assert 405 <= result.chars <= 810


def test_narration_also_returns_a_title_pair() -> None:
    """
    口播稿同样带主副标题。

    1~2 分钟的稿子也是发到平台上的视频，标题栏一样要填。产物里带上就不用
    发布时再想一遍，也保证标题与文案出自同一次生成、口径一致。
    """
    llm = ScriptedProvider(
        "fake", [narration_reply("字" * 500, title="推理引擎开源", subtitle="吞吐提升 2.3 倍")]
    )
    result = build_narration(article(), llm)

    assert result.title == "推理引擎开源"
    assert result.subtitle == "吞吐提升 2.3 倍"


def test_rewrite_delta_is_exact_and_in_the_config_unit() -> None:
    """
    回炉的差值是**减法**，而且用的是配置里那个单位。

    先前提示词说字数、验收看秒数，差值只能按上一稿的字符密度估——而密度随中英
    比例在 4.5~9 字/秒之间浮动，估错一半，第二稿照样不达标。现在两边同一个单位，
    「超了多少字」是确定的。
    """
    llm = ScriptedProvider("fake", [short_reply("超" * 400), short_reply("字" * 200)])
    build_short_video(article(), llm, low=25, high=35, chars=(200, 250))

    feedback = joined(llm.messages[1])
    assert "上一稿 400 字" in feedback
    assert "删掉 150 字" in feedback   # 400 - 250，确切值
    assert "200~250 字" in feedback   # 并重申目标区间
