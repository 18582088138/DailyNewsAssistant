"""字幕段内切分与文本配对 / Cue splitting and text pairing.

复测 / rerun:
    C:\\Users\\75203\\miniforge3\\envs\\ov_env_py312\\python.exe -m pytest tests/tts/test_cue_split.py -q

覆盖 / covers: 条数配对、原文优先、时间吸附、单调性、条数不匹配时的退回。
不含 / excludes: 真实音频的端到端（见 tests/tts/test_vad.py 与 produce 侧的用例）。
说明文档 / docs: docs/14_tts_guide.md
"""

from __future__ import annotations

from itertools import pairwise

from dna.tts.cue_split import (
    CLAUSE_END,
    SENTENCE_END,
    SplitParams,
    align,
    even_boundaries,
    sentences,
    snap_to_pauses,
    split_by_rules,
    split_to_n,
    strip_markup,
    weight,
)

P = SplitParams(max_chars=20, min_chars=12)


def test_strip_markup_drops_synth_directives() -> None:
    assert strip_markup("你好[pause:400ms]世界") == "你好 世界"


def test_weight_counts_english_by_words() -> None:
    """英文按词折算，否则一行英文的字面长度会比一行中文长出一大截。"""
    assert weight("中文五个字啊") == 6
    assert weight("hello world foo bar") > 12


def test_sentences_keep_the_terminator() -> None:
    assert sentences("第一句。第二句！") == ["第一句。", "第二句！"]


def test_split_to_n_yields_exactly_n() -> None:
    """**条数必须精确** —— 时间轴切了几条，文本就得给几条。"""
    text = "第一句话在这里。第二句话也在。第三句比较长一些内容。第四句收尾。"
    for n in (1, 2, 3, 4):
        chunks = split_to_n(text, n, P)
        assert len(chunks) == n, f"n={n} 时给了 {len(chunks)} 条：{chunks}"
        assert "".join(chunks).replace(" ", "") == text.replace(" ", ""), \
            "切分不得增删字符"


def test_split_to_n_when_units_are_fewer() -> None:
    """单元比目标条数少时也要凑够条数（硬分），不能少给。"""
    chunks = split_to_n("短句。", 3, P)
    assert len(chunks) == 3


def test_cuts_land_on_punctuation_never_mid_word() -> None:
    """**切口必须落在标点上** —— 这是本模块最要紧的一条。

    中文没有词边界，一旦按字数无条件切，就会切出「函 / 数」「基 / 本」这种
    半个词的字幕（实测在真实产物里见过）。所以规则是：字数只是**软上限**，
    到了之后继续往前走到下一个标点；实在没有标点可退时，宁可这一条长一点。
    """
    text = "这是一句相当长的话用来验证切分规则会不会把它切短一些。" * 2
    chunks = split_by_rules(text, SplitParams(max_chars=15, min_chars=6))
    assert len(chunks) > 1
    # 除最后一条外（原文可能不以标点收尾），每条都该以标点结尾
    assert all(c[-1] in SENTENCE_END + CLAUSE_END for c in chunks[:-1]), \
        f"切口没落在标点上：{chunks}"


def test_soft_ceiling_waits_for_the_next_punctuation() -> None:
    """到软上限后继续走到下一个标点，而不是就地切。"""
    text = "Anthropic 工程师 Lydia Hallie 称它基本是 Claude Code 的中间件，支持观察、改写。"
    chunks = split_by_rules(text, SplitParams(max_chars=20, min_chars=12))
    assert chunks
    assert chunks[0].endswith("，"), f"第一条应当收在逗号上：{chunks[0]!r}"
    assert "的中间件" in chunks[0], f"不该在「的」前面断开：{chunks}"


def test_split_to_n_matches_units_one_to_one() -> None:
    """原子单元数**正好**等于目标条数时一一对应，不走按字符硬切。

    这里曾经写成 `len(units) <= n`，于是 6 个单元要切成 6 条也会走硬切 ——
    实测切出了「Claude Code 推出 Mods 功 / 能，从 2.1.287…」。
    """
    text = "第一句。第二句。第三句。"
    chunks = split_to_n(text, 3, P)
    assert chunks == ["第一句。", "第二句。", "第三句。"], f"没有一一对应：{chunks}"


def test_snap_consumes_each_pause_once() -> None:
    """两个边界不得吸到同一个停顿上 —— 那会切出零长度字幕。"""
    bounds = [0.0, 2.0, 2.1, 6.0]
    pauses = [2.0, 2.1]
    snapped = snap_to_pauses(bounds, pauses, window=0.5)
    assert snapped == sorted(snapped)
    assert all(b - a > 0 for a, b in pairwise(snapped))


def test_snap_keeps_endpoints() -> None:
    """首尾是音频的事实，不是估计，不许被吸附改动。"""
    snapped = snap_to_pauses([0.0, 5.0, 10.0], [4.9, 10.1], window=0.5)
    assert snapped[0] == 0.0
    assert snapped[-1] == 10.0


def test_even_boundaries_cover_the_duration() -> None:
    bounds = even_boundaries(["甲" * 10, "乙" * 20, "丙" * 10], 12.0, P)
    assert bounds[0] == 0.0
    assert bounds[-1] == 12.0
    assert bounds[1] < bounds[2]


def test_align_uses_the_source_text() -> None:
    """**核心断言**：显示原文，而不是朗读稿。

    朗读稿里有多音字换成的同音字与合成指令，显示出来就是错字与噪音。
    """
    spoken = "开放时间早上九点至下午五点。欢迎光临本店[pause:400ms]谢谢。"
    source = "开放时间9:00至17:00。欢迎光临本店，谢谢。"
    cues = align(spoken, source, 8.0, [], P)
    assert cues, "应当切出字幕"
    shown = "".join(cue.text for cue in cues)
    assert "9:00" in shown, f"显示的不是原文：{shown}"
    assert "pause" not in shown, f"合成指令漏进了字幕：{shown}"


def test_align_snaps_to_pauses() -> None:
    """有真实停顿时，边界要落在停顿上，而不是按字数估的位置。"""
    spoken = "第一句话说得比较长一些。第二句话也不短的样子在这里。"
    cues = align(spoken, spoken, 10.0, [4.6], P)
    assert len(cues) == 2
    assert abs(cues[0].end - 4.6) < 0.01, f"没有吸附到停顿：{cues}"


def test_align_timeline_is_monotonic_and_bounded() -> None:
    spoken = "话" * 60
    cues = align(spoken, "原" * 60, 9.0, [3.0, 6.0], P)
    assert cues
    assert cues[0].start == 0.0
    assert cues[-1].end <= 9.0 + 0.01
    for earlier, later in pairwise(cues):
        assert later.start >= earlier.start
        assert later.start < later.end


def test_align_single_chunk_covers_the_whole_piece() -> None:
    """只有一条时覆盖整段 —— 一条字幕只挂半秒钟会被当成没出。"""
    cues = align("很短。", "很短。", 3.0, [], P)
    assert len(cues) == 1
    assert cues[0].start == 0.0
    assert abs(cues[0].end - 3.0) < 0.01


def test_align_falls_back_to_spoken_when_source_is_empty() -> None:
    """没有原文时退回朗读稿，而不是留一条空字幕。"""
    cues = align("只有朗读稿。", "", 2.0, [], P)
    assert cues
    assert cues[0].text


def test_align_trims_leading_punctuation_only() -> None:
    """去开头的悬挂标点，**保留句末标点**。"""
    cues = align("，开头有逗号。", "，开头有逗号。", 2.0, [], P)
    assert cues[0].text == "开头有逗号。"


def test_align_merges_cues_that_would_flash_past() -> None:
    """停顿吸附可能切出几十毫秒的条，必须并进相邻条。

    实测在真实产物上见过 0.05 秒的一条 —— 观众只看到一次闪烁，还会以为播放器卡了。
    合并后文本会变长，这是刻意的取舍。
    """
    spoken = "第一句在这里。第二句也在。第三句收尾。"
    # 两个停顿挨得极近，吸附后中间那条只剩 50 毫秒
    cues = align(spoken, spoken, 6.0, [2.0, 2.05], P)
    assert cues
    assert all(cue.duration >= P.merge_below_seconds * 0.5 for cue in cues), \
        f"仍有一闪而过的条目：{[(c.text, round(c.duration, 3)) for c in cues]}"


def test_align_keeps_arabic_numerals_from_the_source() -> None:
    """原文里的阿拉伯数字必须原样显示。

    TTS 的改写稿会把 `2.1.287` 写成「二点一点二八七」（那是给耳朵的），
    字幕显示成那样就成了错字。
    """
    spoken = "从 二点一点二八七 起默认开启，约 八十 行代码。"
    source = "从 2.1.287 起默认开启，约 80 行代码。"
    cues = align(spoken, source, 5.0, [], P)
    shown = "".join(cue.text for cue in cues)
    assert "2.1.287" in shown, f"阿拉伯数字没保留：{shown}"
    assert "80" in shown
    assert "二点一点二八七" not in shown, "显示成了给耳朵的写法"
    assert "八十" not in shown


def test_align_count_follows_the_displayed_text() -> None:
    """**条数由要显示的文本决定**，不是由朗读稿决定。

    曾经的写法是「朗读稿切几条、原文跟着切几条」，两边对不上就整段退回朗读稿，
    于是数字又变回中文写法。现在能显示的文本是硬约束。
    """
    spoken = "第一句读法写得比较长一些。第二句读法也不短的样子。第三句读法同样很长。"
    source = "第一句原文写得比较长一些。第二句原文也不短的样子。"
    cues = align(spoken, source, 4.0, [], P)
    assert len(cues) == len(split_by_rules(source, P)), \
        f"条数应当跟着原文走：{[c.text for c in cues]}"
    assert len(cues) == 2, f"这段原文应当切成两条：{[c.text for c in cues]}"
    assert all("原文" in cue.text for cue in cues)
    assert not any("读法" in cue.text for cue in cues), "退回了朗读稿"


def test_markup_only_text_yields_nothing() -> None:
    assert split_by_rules("[pause:400ms]", P) == []


def test_split_params_come_from_the_profile() -> None:
    """切分旋钮从 `profile.yaml` 的 `tuning` 读，改配置就生效。

    `CLAUDE.md` 的配置铁律：可调参数不许写死在代码里。判据是「改一下会不会
    影响产物质量，而用户现在改不动它」——`subtitle_max_chars` 正是这种值。
    这条用例是那个契约的守卫。
    """
    from dna.core.config.profile import Tuning

    tuning = Tuning(
        subtitle_max_chars=30,
        subtitle_min_chars=10,
        subtitle_merge_below_seconds=0.5,
        subtitle_snap_window=0.2,
    )
    params = SplitParams.from_tuning(tuning)
    assert params.max_chars == 30
    assert params.min_chars == 10
    assert params.merge_below_seconds == 0.5
    assert params.snap_window == 0.2


def test_split_params_tolerate_a_profile_without_the_fields() -> None:
    """旧 profile 没有这些字段时退回默认值，不让字幕整个挂掉。"""
    assert SplitParams.from_tuning(None) == SplitParams()
    assert SplitParams.from_tuning(object()) == SplitParams()


def test_configured_max_chars_actually_changes_the_output() -> None:
    """改了旋钮，切出来的条数要真的变 —— 「假配置」是这个项目的历史教训（issues/014）。"""
    text = "第一句比较长一些的内容在这里，第二句也不短的样子，第三句同样有点长。"
    few = split_by_rules(text, SplitParams(max_chars=60, min_chars=4))
    many = split_by_rules(text, SplitParams(max_chars=10, min_chars=4))
    assert len(many) > len(few), f"调小 max_chars 没有切得更碎：{len(few)} vs {len(many)}"
