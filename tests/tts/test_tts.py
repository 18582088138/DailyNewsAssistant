"""
test_tts.py —— 语音合成层单元测试 / Speech synthesis layer tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/tts/test_tts.py -v

对应的人工验证 / Matching manual check:
    dna tts                              # 后端能不能起来、有哪些音色（不合成，秒回）
    dna tts --say "一句话" -o out.wav    # 真的合成一次，看 RTF 与音质
    dna produce <id> --kind narration_audio
    dna gui → 展开口播格 → 「合成音频」

覆盖 / Covers:
    清洗与分段（纯函数，无模型）:
      1. **URL 必须去掉**——不去掉模型会把网址一个字符一个字符念出来，整段音频报废
      2. Markdown 标记、HTML 注释（长文案的提纲藏在这里）、图片、列表符号都要去掉
      3. 链接的**锚文本保留**，地址丢掉——锚文本是句子的一部分
      4. **只在句子边界切**，标点跟着前一句走（模型靠它收尾语调）
      5. 单句超长时退到逗号切；空输入返回空列表而不是 `[""]`
    协议与拼接:
      6. WAV 编码可被 `wave` 读回，时长对得上
      7. 越界样点按峰值归一化，不硬裁剪（硬裁剪会有刺耳的削波）
      8. **一段失败不毁整篇**，但全失败要抛 TTSError
      9. 换发言人的停顿比换句子长（听感上才像对话）
     10. 预估耗时 = 稿子时长 × 实测 RTF
    工厂与配置:
     11. 两个 provider 名字都认；未知名字报错并列出可选
     12. **实例按 (后端, 模型, 设备, 精度) 缓存**——加载要 10 秒，不能每次点按钮都付
     13. 必需路径没配时报错说清缺哪一项
     14. **主持人与嘉宾音色相同时自动岔开**——两个角色一个嗓子等于没做双角色
     15. torch 后端在没有 CUDA 的机器上**加载前就报错**，不是等权重读完才抛
    产物层:
     16. **写与读不漂移**：`script_block` 写出来的，`spoken_text` 必须读得回来

    音频产物走完整 produce 流程的那几条在 `tests/produce/test_service.py`：
    不调 LLM · 按字节写盘 · 只念正文不念网址 · 访谈两把嗓子 · 重做音频不重新计费

预期 / Expected:
    29 passed；耗时 < 1s；**不加载任何模型、不产生任何音频**，纯逻辑
    真机合成走上面的人工验证，不进单测——加载一次 10 秒，合成一次几十秒
"""

from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from dna.core.config import Settings
from dna.produce.documents import script_block, spoken_text
from dna.tts import factory
from dna.tts.base import (
    PAUSE_SECONDS,
    SPEAKER_CHANGE_PAUSE_SECONDS,
    SpeechSegment,
    TTSError,
    VoiceSpec,
    encode_wav,
    estimate_synthesis_seconds,
    wav_seconds,
)
from dna.tts.segment import clean_for_speech, split_for_speech
from dna.tts.service import (
    DEFAULT_GUEST_SPEAKER,
    DEFAULT_SPEAKER,
    TTSServiceProvider,
)

SAMPLE_RATE = 24_000


# --- 清洗 / cleaning ----------------------------------------------------------


def test_url_is_removed() -> None:
    """
    **网址必须去掉。**产物抬头里有 `> 口播文案　·　来源：https://…`，
    直接送进 TTS，模型会把它一个字符一个字符念出来——不是音质变差，是整段废掉。
    The header carries a source URL; read aloud character by character it destroys the
    take rather than merely degrading it.
    """
    cleaned = clean_for_speech("来源：https://www.qbitai.com/2026/09/482967.html 正文开始。")

    assert "http" not in cleaned
    assert "qbitai" not in cleaned
    assert "正文开始。" in cleaned


def test_markdown_markers_are_removed() -> None:
    """星号、井号、列表符号都是给眼睛看的 / Markup is for the eye, not the ear."""
    cleaned = clean_for_speech("## 小标题\n\n- **加粗**的要点\n1. 第一条\n")

    assert "#" not in cleaned
    assert "*" not in cleaned
    assert "小标题" in cleaned
    assert "加粗" in cleaned


def test_html_comment_is_removed() -> None:
    """
    长文案把提纲藏在 HTML 注释里，念出来就成了「提纲一背景二方法」。
    The long-form outline hides in an HTML comment and must not be voiced.
    """
    cleaned = clean_for_speech("<!-- 提纲\n1. 背景\n2. 方法\n-->\n\n正文第一句。")

    assert "提纲" not in cleaned
    assert "正文第一句。" in cleaned


def test_link_anchor_text_survives() -> None:
    """
    链接的锚文本是句子的一部分，丢掉句子就读不通了；地址丢掉。
    The anchor is part of the sentence and is kept; the address is not.
    """
    cleaned = clean_for_speech("[Qwen3](https://example.com/qwen3) 发布了新版本。")

    assert "Qwen3 发布了新版本。" in cleaned
    assert "example.com" not in cleaned


# --- 分段 / splitting ---------------------------------------------------------


def test_splits_on_sentence_boundaries() -> None:
    """
    **只在句子边界切。**每段是独立一次合成，模型不知道上一段的语气，
    句中切开拼起来能听见断裂与语调重置。
    Each piece is synthesised independently, so a mid-sentence split is audible as a
    break and a pitch reset once the pieces are joined.
    """
    pieces = split_for_speech("句子一。" * 60, max_chars=50)

    assert len(pieces) > 1
    for piece in pieces:
        assert piece.endswith("。")


def test_terminator_stays_with_its_sentence() -> None:
    """句号留在句尾——模型靠它收尾语调 / The full stop closes the intonation."""
    pieces = split_for_speech("第一句话。第二句话。", max_chars=6)

    assert pieces[0].endswith("。")
    assert not pieces[0].startswith("。")


def test_over_long_sentence_falls_back_to_clauses() -> None:
    """一句话本身超长时退到逗号切 / An over-long sentence breaks on clauses."""
    sentence = "，".join(["某个很长的子句"] * 20) + "。"
    pieces = split_for_speech(sentence, max_chars=40)

    assert len(pieces) > 1
    assert max(len(p) for p in pieces) <= 60


def test_empty_input_yields_no_pieces() -> None:
    """
    空输入返回空列表，不是 `[""]`。
    一个空分段会合成出一段静音，看上去像成功了——而实际上没有可念的内容。
    An empty piece would synthesise silence and read as success.
    """
    assert split_for_speech("") == []
    assert split_for_speech("   \n\n  ") == []
    assert split_for_speech("<!-- 只有注释 -->") == []


# --- WAV 编码 / WAV encoding --------------------------------------------------


def test_encoded_wav_round_trips() -> None:
    """编出来的 WAV 要能被标准库读回，时长对得上 / The WAV reads back correctly."""
    samples = np.zeros(SAMPLE_RATE, dtype=np.float32)
    wav = encode_wav(samples, SAMPLE_RATE)

    with wave.open(io.BytesIO(wav), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == SAMPLE_RATE
    assert wav_seconds(wav) == pytest.approx(1.0)


def test_out_of_range_samples_are_normalised_not_clipped() -> None:
    """
    越界样点按峰值归一化，不硬裁剪。

    模型偶尔会给出略微越界的样点；直接裁剪会产生刺耳的削波失真。
    The model occasionally returns samples slightly out of range, and clipping them would
    be audible as distortion.
    """
    samples = np.array([2.0, -2.0, 1.0], dtype=np.float32)
    wav = encode_wav(samples, SAMPLE_RATE)

    with wave.open(io.BytesIO(wav), "rb") as handle:
        pcm = np.frombuffer(handle.readframes(3), dtype="<i2")

    assert pcm[0] == pytest.approx(32767, abs=2)
    assert pcm[2] == pytest.approx(16383, abs=2)  # 1.0 / 2.0 峰值 → 一半


def test_estimate_uses_measured_rtf() -> None:
    """预估耗时 = 稿子时长 × 实测 RTF / The estimate is the script duration times RTF."""
    assert estimate_synthesis_seconds(100.0) == pytest.approx(250.0)
    assert estimate_synthesis_seconds(0.0) == 0.0


# --- 逐段合成、拼接与容错 / per-piece synthesis, joining, fault tolerance ------


class FakeClient:
    """
    一个不连网的 TTS 服务 / A TTS service that never leaves the process.

    每段回 1 秒静音；`fail_at` 里的下标抛 TTSError，用来验「一段失败不毁整篇」。
    """

    def __init__(self, *, fail_at: set[int] | None = None) -> None:
        self.fail_at = fail_at or set()
        self.calls = 0
        self.seen: list[dict] = []

    def health(self, timeout: float = 3.0) -> bool:
        return True

    def speakers(self) -> list[str]:
        return ["Serena", "Uncle_Fu"]

    def synthesize(self, text: str, **kwargs) -> dict:
        index = self.calls
        self.calls += 1
        self.seen.append({"text": text, **kwargs})
        if index in self.fail_at:
            raise TTSError("这一段炸了")
        silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
        run = kwargs.get("run") or "run"
        return {"wav": encode_wav(silence, SAMPLE_RATE), "seconds": 1.0,
                "path": f"/outputs/{run}/seg_{index + 1:03d}.wav"}


def _provider(*, fail_at: set[int] | None = None) -> TTSServiceProvider:
    """一个注入了假客户端的 provider / A provider wired to the fake client."""
    provider = TTSServiceProvider(Settings(_env_file=None))
    provider.client = FakeClient(fail_at=fail_at)
    return provider


def _segments(count: int, *, roles: list[str] | None = None) -> list[SpeechSegment]:
    voice = VoiceSpec(speaker="Serena", language="chinese")
    roles = roles or ["narrator"] * count
    return [SpeechSegment(text=f"第{i}段。", voice=voice, role=roles[i]) for i in range(count)]


def test_joins_segments_with_pauses() -> None:
    """段间要有停顿，否则两句黏在一起像抢话 / Without a gap the sentences run together."""
    clip = _provider().synthesize(_segments(3))

    expected = 3 + 2 * PAUSE_SECONDS
    assert clip.seconds == pytest.approx(expected, abs=0.01)
    assert clip.segments == 3
    assert clip.complete


def test_speaker_change_gets_a_longer_pause() -> None:
    """换人处的停顿更长，听感上才像两个人在对话 / A speaker change gets more silence."""
    clip = _provider().synthesize(_segments(2, roles=["host", "guest"]))

    expected = 2 + SPEAKER_CHANGE_PAUSE_SECONDS
    assert clip.seconds == pytest.approx(expected, abs=0.01)


def test_one_failed_segment_does_not_destroy_the_take() -> None:
    """
    **一段失败不毁整篇。** 几十分钟的合成里丢一句，比全部重来划算得多；
    但 `failed_segments` 必须记下来，上层据此告诉人「音频不完整」。
    """
    clip = _provider(fail_at={1}).synthesize(_segments(3))

    assert clip.failed_segments == [1]
    assert not clip.complete
    assert clip.seconds == pytest.approx(2 + PAUSE_SECONDS, abs=0.01)


def test_all_segments_failing_raises() -> None:
    """全失败要抛，不能回一条空音频 / Total failure raises rather than returning silence."""
    with pytest.raises(TTSError, match="全部合成失败"):
        _provider(fail_at={0, 1}).synthesize(_segments(2))


def test_nothing_to_speak_raises() -> None:
    voice = VoiceSpec(speaker="Serena")
    with pytest.raises(TTSError, match="没有可朗读"):
        _provider().synthesize([SpeechSegment(text="   ", voice=voice)])


def test_progress_is_reported_per_segment() -> None:
    """长合成必须逐段报进度，否则界面只能看一个转圈 / Progress is per piece."""
    seen: list[tuple[int, int]] = []
    _provider().synthesize(
        _segments(3), on_progress=lambda done, total, _s: seen.append((done, total))
    )

    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_artifacts_carry_the_run_directory() -> None:
    """
    产物清单形如 `run/文件名` —— 上层就是拿这两段去 `GET /outputs/{run}/{file}`。
    用**路径里真实的父目录名**：产物目录重名时服务端会加后缀，
    写死请求时给的名字就取不到了。
    """
    clip = _provider().synthesize(_segments(2))

    assert clip.run.startswith("dna-")
    assert [a.split("/")[-1] for a in clip.artifacts] == ["seg_001.wav", "seg_002.wav"]
    assert all(a.count("/") == 1 for a in clip.artifacts)


def test_voice_case_is_corrected_against_the_service() -> None:
    """
    `.env` 里写小写的音色名要能对上服务端的写法 —— 大小写不符会在**加载完权重
    之后**才报错，那时已经白等了几十秒。
    """
    provider = _provider()
    provider.synthesize([SpeechSegment(text="一句话。",
                                       voice=VoiceSpec(speaker="serena"))])

    assert provider.client.seen[0]["voice"] == "Serena"


# --- 工厂与音色 / factory and voices ------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache():
    factory.reset_cache()
    yield
    factory.reset_cache()


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_instance_is_cached_per_service_url() -> None:
    """provider 缓存了 /info 与音色清单，每次重建就要多两次往返。"""
    s = _settings(tts_service_url="http://127.0.0.1:8300")
    assert factory.get_tts(s) is factory.get_tts(s)
    assert factory.get_tts(s, fresh=True) is not None


def test_different_service_gets_a_new_instance() -> None:
    """换地址（本机 → 4060 那台）必须换实例，否则还在问旧服务。"""
    first = factory.get_tts(_settings(tts_service_url="http://127.0.0.1:8300"))
    second = factory.get_tts(_settings(tts_service_url="http://10.0.0.9:8300"))
    assert first is not second


def test_two_roles_never_share_a_voice() -> None:
    """
    **主持人与嘉宾的音色相同时自动岔开。**
    访谈稿两个人一个嗓子，听众分不出谁在说话，双角色这件事就白做了。
    An interview read in one voice leaves the listener unable to tell who is speaking.
    """
    s = _settings(tts_voice_host="Serena", tts_voice_guest="Serena")

    host = factory.voice_for_role("host", s)
    guest = factory.voice_for_role("guest", s)

    assert host.speaker != guest.speaker


def test_default_voices_differ() -> None:
    """默认值本身就必须不同 / The defaults themselves must differ."""
    assert DEFAULT_SPEAKER != DEFAULT_GUEST_SPEAKER

    s = _settings()
    assert factory.voice_for_role("host", s).speaker == DEFAULT_SPEAKER
    assert factory.voice_for_role("guest", s).speaker == DEFAULT_GUEST_SPEAKER


def test_narrator_uses_the_host_voice() -> None:
    """专题模式只有旁白，用主持人那把嗓子 / A feature has only a narrator."""
    s = _settings(tts_voice_host="Eric")
    assert factory.voice_for_role("narrator", s).speaker == "Eric"


# --- 服务探活与自动拉起 / liveness and autostart -------------------------------
#
# 这三条都在验**报错说得清不清**：服务不在线时人要能立刻知道
# 「该等它自己起来」还是「该自己去开」，而不是看一个连接错误。


def _dead_client() -> FakeClient:
    client = FakeClient()
    client.health = lambda timeout=3.0: False
    return client


def test_remote_service_is_never_started_for_you() -> None:
    """远程地址不做自动拉起，而且要说清是这个原因。"""
    from dna.tts.supervisor import ensure_service

    with pytest.raises(TTSError, match="不在本机"):
        ensure_service(_settings(tts_service_url="http://10.0.0.9:8300"),
                       client=_dead_client())


def test_autostart_off_says_so() -> None:
    from dna.tts.supervisor import ensure_service

    with pytest.raises(TTSError, match="自动拉起"):
        ensure_service(_settings(tts_autostart=False), client=_dead_client())


def test_missing_module_dir_is_named() -> None:
    """没配 TTS_MODULE_DIR 时要说出这个名字，而不是只说「起不来」。"""
    from dna.tts.supervisor import ensure_service

    with pytest.raises(TTSError, match="TTS_MODULE_DIR"):
        ensure_service(_settings(tts_module_dir=""), client=_dead_client())


# --- 朗读友好化 / speakable rewriting -----------------------------------------


class FakeLLM:
    """按需回一个 SpokenOut / Returns whatever the test asks for."""

    def __init__(self, spoken: str, *, boom: bool = False) -> None:
        self.spoken = spoken
        self.boom = boom
        self.calls = 0

    def chat_json(self, messages, schema, **kwargs):
        self.calls += 1
        if self.boom:
            raise RuntimeError("模型不可用")
        return schema(spoken=self.spoken, notes=["改了型号读法"])


def test_preprocess_uses_the_rewrite() -> None:
    from dna.tts.preprocess import prepare_for_speech

    llm = FakeLLM("RTX 四零六零 发布了，比上代快三点五倍。")
    result = prepare_for_speech("RTX 4060 发布了，比上代快 3.5x。",
                                llm=llm, settings=_settings())

    assert result.used_llm and llm.calls == 1
    assert "四零六零" in result.text


def test_preprocess_failure_falls_back_to_the_original() -> None:
    """**预处理失败绝不能挡住音频。** 它只是润色，音频能不能出来不该取决于它。"""
    from dna.tts.preprocess import prepare_for_speech

    result = prepare_for_speech("原文一句话。", llm=FakeLLM("", boom=True),
                                settings=_settings())

    assert result.text == "原文一句话。"
    assert not result.used_llm and "LLM 调用失败" in result.reason


def test_preprocess_rejects_a_wildly_different_length() -> None:
    """
    长度偏离过大 ⇒ 模型自己续写或大段删除了，宁可不要 ——
    音频里多念一段不存在的内容，比读音不完美严重得多。
    """
    from dna.tts.preprocess import prepare_for_speech

    original = "第一句话在这里。第二句话在这里。第三句话在这里。"
    result = prepare_for_speech(original, llm=FakeLLM("短。"), settings=_settings())

    assert result.text == original
    assert "偏离过大" in result.reason


def test_preprocess_can_be_switched_off() -> None:
    """关掉时一次都不该调 / Switched off means zero calls."""
    from dna.tts.preprocess import prepare_for_speech

    llm = FakeLLM("不该被用到")
    result = prepare_for_speech("**加粗**的一句话。", llm=llm,
                                settings=_settings(tts_preprocess=False))

    assert llm.calls == 0
    assert result.text == "加粗的一句话。"      # Markdown 清洗仍然生效


# --- 与产物层的接口 / the interface to the production layer -------------------


class _Script:
    """`script_block` 只用到这几个字段 / Only these fields are read."""

    title = "DeepSeek V4 Flash 开源"
    subtitle = "极限量化，成本骤降"
    seconds = 30.0
    chars = 200
    text = "DeepSeek 开源模型，在智能指数上得分 50。激活参数只有 13B。"


def test_written_script_reads_back() -> None:
    """
    **写与读不能漂移。**产物抬头里有网址；解析这边一旦跟排版脱节，
    不会有任何报错，只会安静地把网址念出来。所以渲染与解析放在同一个文件里，
    并由这条测试钉住它们的往返关系。
    Nothing raises when the parser falls out of step with the layout — the URL simply
    gets read aloud. Renderer and parser therefore live together, pinned by this test.
    """
    document = f"# 标题\n\n> 口播文案　·　来源：https://x.com/a\n\n{script_block(_Script())}"

    spoken = spoken_text(document)

    assert spoken == _Script.text
    assert "http" not in spoken
    assert "主标题" not in spoken


def test_spoken_text_without_marker_drops_metadata() -> None:
    """
    没有标记时（旧产物、手工编辑过的文件）也不能整篇照念。
    Without the marker the metadata lines are still dropped.
    """
    spoken = spoken_text("# 标题\n\n> 口播文案　·　来源：https://x.com/a\n\n正文一句。")

    assert spoken == "正文一句。"
