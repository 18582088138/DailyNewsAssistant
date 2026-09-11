"""
test_console.py —— TTS 操作台后端单元测试 / TTS console backend tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/tts/test_console.py -v

对应的人工验证 / Matching manual check:
    dna gui → 展开口播格 → 「TTS 操作台」   # 服务离线时面板照样打得开
    设置 → TTS 子页                          # 音色下拉、参考音频候选与试听

覆盖 / Covers:
    1. **服务离线时 `available_voices()` 回空表，不抛异常**——设置面板和操作台
       在服务没起来的时候也要能打开，退回自由文本框，而不是整块打不开
    2. 音色表来自服务端，不在前端硬编码
    3. 参考音频候选是**相对路径**（`ref_audio/x.wav`）——绝对路径存进 `.env`
       会把配置绑死在这台机器的目录上；非音频文件不进候选
    4. 相对路径按 `data_dir` 解析（**不是仓库根**），解析规则与
       `tts/factory.py::_ref_audio_path` 是同一份
    5. **克隆模式下试听不带音色名**：服务端把「有 ref_audio 又有音色名」视为
       互相冲突的参数并直接报错，那条护栏只能有一份（provider 里那份）
    6. 试听用固定的产物目录名，不每试一次在服务端留一个时间戳目录
    7. 上传的参考音频落进 `data_dir/ref_audio/` 并**立刻进候选**；文件名过 slugify；
       **重名不覆盖**（覆盖会悄悄换掉别的产物正在用的那把音色）
    8. `resplit()` 的结果与 `split_for_speech` 逐段一致——操作台看到的分段
       必须就是自动合成会切出来的那批

预期 / Expected:
    耗时 < 1s；不联网、不加载模型、不产生音频（客户端是假的）
"""

from __future__ import annotations

import numpy as np
import pytest

from dna.core.config import Settings
from dna.tts import console
from dna.tts.base import SpeechSegment, TTSError, VoiceSpec, encode_wav
from dna.tts.service import TTSServiceProvider

SAMPLE_RATE = 24_000


class _FakeClient:
    """不连网的 TTS 服务 / A TTS service that never leaves the process."""

    def __init__(self) -> None:
        self.seen: list[dict] = []

    def health(self, timeout: float = 3.0) -> bool:
        return True

    def speakers(self) -> list[str]:
        return ["Serena", "Uncle_Fu"]

    def synthesize(self, text: str, **kwargs) -> dict:
        self.seen.append({"text": text, **kwargs})
        return {
            "wav": encode_wav(np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE),
            "seconds": 1.0,
        }


def _provider() -> TTSServiceProvider:
    provider = TTSServiceProvider(Settings(_env_file=None))
    provider.client = _FakeClient()
    return provider


# --- 音色表 / the voice list ---------------------------------------------------


def test_offline_service_yields_an_empty_voice_list(monkeypatch) -> None:
    """
    **服务离线不能让设置面板打不开。**

    音色表取不到是常态（TTS 服务是按需拉起的），不是错误。抛出去的话，
    整个设置对话框会在打开的那一刻炸掉——而人打开它往往正是为了改 TTS 地址。
    An offline service is normal, not an error: raising here would break the very dialog
    used to fix the service address.
    """
    def _dead(_settings=None):  # noqa: ANN001, ANN202
        raise TTSError("服务不可用")

    monkeypatch.setattr(console, "get_tts", _dead)

    assert console.available_voices(Settings(_env_file=None)) == []


def test_voices_come_from_the_service(monkeypatch) -> None:
    """音色表在服务端，前端不硬编码——换权重音色就变了。"""
    provider = _provider()
    monkeypatch.setattr(console, "get_tts", lambda _s=None: provider)

    assert console.available_voices(Settings(_env_file=None)) == ["Serena", "Uncle_Fu"]


# --- 参考音频 / reference audio ------------------------------------------------


def test_candidates_are_relative_paths_and_audio_only(tmp_path) -> None:
    """
    候选给的是相对路径，且只收音频。

    相对路径是 `.env` 里该写的形式：绝对路径存进去，配置就绑死在这台机器上，
    换机器的人拿到的是一条指向不存在目录的设置。
    """
    directory = tmp_path / "ref_audio"
    directory.mkdir()
    (directory / "b.wav").write_bytes(b"RIFF")
    (directory / "a.mp3").write_bytes(b"ID3")
    (directory / "notes.txt").write_text("不是音频", encoding="utf-8")

    choices = console.ref_audio_choices(Settings(_env_file=None, data_dir=tmp_path))

    assert choices == ["ref_audio/a.mp3", "ref_audio/b.wav"]


def test_missing_directory_is_not_an_error(tmp_path) -> None:
    """还没建 ref_audio 目录的机器上，面板照样要能打开。"""
    assert console.ref_audio_choices(Settings(_env_file=None, data_dir=tmp_path)) == []


def test_relative_paths_resolve_against_the_data_dir(tmp_path) -> None:
    """
    **按 `data_dir` 解析，不是仓库根。**

    界面显示的路径与合成时真正读的路径必须是同一个，否则界面上是绿的、
    合成出来却是内置音色（找不到文件时后端只打一条 warning 就降级）。
    """
    directory = tmp_path / "ref_audio"
    directory.mkdir()
    clip = directory / "host.wav"
    clip.write_bytes(b"RIFF")
    settings = Settings(_env_file=None, data_dir=tmp_path)

    assert console.resolve_ref_audio("ref_audio/host.wav", settings) == clip
    assert console.resolve_ref_audio("ref_audio/nope.wav", settings) is None
    assert console.resolve_ref_audio("", settings) is None


def test_uploaded_clip_lands_in_the_choices(tmp_path) -> None:
    """
    上传的参考音频要**立刻出现在候选里**，回的也是相对路径。

    目录不存在时自己建：新机器上第一次上传就是这个场景。
    """
    settings = Settings(_env_file=None, data_dir=tmp_path)

    relative = console.save_ref_audio("我的 录音:1.WAV", b"RIFFfake", settings)

    assert relative.startswith("ref_audio/")
    assert relative.endswith(".wav")          # 后缀统一成小写
    assert relative in console.ref_audio_choices(settings)
    assert console.resolve_ref_audio(relative, settings) is not None


def test_uploading_the_same_name_twice_keeps_both(tmp_path) -> None:
    """
    重名不覆盖 —— 覆盖会**悄悄换掉**别的产物正在用的那把音色，
    而那些产物的台账里记的还是同一个路径。
    """
    settings = Settings(_env_file=None, data_dir=tmp_path)

    first = console.save_ref_audio("host.wav", b"RIFF-1", settings)
    second = console.save_ref_audio("host.wav", b"RIFF-2", settings)

    assert first != second
    assert (tmp_path / first).read_bytes() == b"RIFF-1"


# --- 重新拆条 / re-splitting ---------------------------------------------------


def test_resplit_matches_the_pipeline_splitter() -> None:
    """
    操作台里看到的分段必须**就是**自动合成会切出来的那批。

    另写一套切法（或者去问服务端的 `/tts/split`）迟早给出不同的段数，
    那时人在面板里调好的东西换成自动合成又不一样了。
    """
    from dna.tts.segment import split_for_speech

    text = "第一句话在这里。第二句话稍微长一点，也在这里。第三句收尾。"

    assert console.resplit(text) == split_for_speech(text)
    assert console.resplit("   ") == []


# --- 试听 / audition -----------------------------------------------------------


def test_clone_mode_preview_carries_no_voice_name() -> None:
    """
    **克隆模式下不能同时给音色名。**

    服务端把「有 ref_audio 又有音色名」当成互相冲突的参数并直接报错（它故意不做
    静默忽略）。所以试听必须走 provider 自己那份参数拼装，不另抄一份——
    抄出来的第二份迟早漏掉这条护栏，然后表现成服务端报参数冲突。
    The guard lives in the provider; a copied parameter assembly would eventually drop it.
    """
    provider = _provider()
    voice = VoiceSpec(speaker="/data/ref_audio/host.wav", mode="voice_clone",
                      ref_audio="/data/ref_audio/host.wav", ref_text="原话",
                      language="chinese")

    wav = console.preview_segment("试听一句。", voice, tts=provider)

    assert wav.startswith(b"RIFF")
    sent = provider.client.seen[0]
    assert sent["voice"] is None
    assert sent["ref_audio"] == "/data/ref_audio/host.wav"
    assert sent["mode"] == "voice_clone"


def test_builtin_mode_preview_sends_the_voice_name() -> None:
    """内置音色反过来：必须带名字，否则服务端不知道用谁的嗓子。"""
    provider = _provider()
    voice = VoiceSpec(speaker="serena", language="chinese")

    console.preview_segment("试听一句。", voice, tts=provider)

    sent = provider.client.seen[0]
    # 大小写按服务端的音色表纠正过（provider 自己做的），所以只比小写
    assert (sent["voice"] or "").lower() == "serena"
    assert sent["ref_audio"] is None


def test_preview_uses_one_fixed_run_directory() -> None:
    """
    试听固定用一个产物目录名。

    每试一次留一个时间戳目录，服务端的 outputs/ 会堆到没人愿意清——
    而试听的音频没有任何保留价值。
    """
    provider = _provider()
    console.preview_segment("一句。", VoiceSpec(speaker="serena"), tts=provider)

    assert provider.client.seen[0]["run"] == console.PREVIEW_RUN


def test_preview_writes_no_ledger_row() -> None:
    """
    试听**不写台账**——它免费且无副作用。

    写一行成功/失败记录会污染那一格的历史，让「上次生成的到底是什么」说不清。
    这里用「函数签名里没有 article_id」来断言：拿不到文章就写不了那一行。
    """
    import inspect

    params = inspect.signature(console.preview_segment).parameters
    assert "article_id" not in params
    assert set(params) == {"text", "voice", "role", "settings", "tts"}


def test_empty_text_is_rejected_by_the_provider() -> None:
    """空文本一段都合不出来，报错要在试听时立刻出现。"""
    with pytest.raises(TTSError):
        console.preview_segment("   ", VoiceSpec(speaker="serena"), tts=_provider())


def test_preview_asks_for_exactly_one_segment() -> None:
    """试听只合成这一段，不整篇跑一遍（整篇要几分钟）。"""
    provider = _provider()
    console.preview_segment("只有这一句。", VoiceSpec(speaker="serena"), tts=provider)

    assert len(provider.client.seen) == 1
    assert provider.client.seen[0]["text"] == "只有这一句。"


def test_segment_type_is_what_the_provider_expects() -> None:
    """
    传给 provider 的是 `SpeechSegment`，不是裸字符串。

    provider 的循环读的是 `piece.voice` 与 `piece.role`，给错类型会在
    「逐段合成」那一行才炸，而那已经在工作线程里了。
    """
    provider = _provider()
    seen: list[object] = []
    original = provider.synthesize

    def _spy(segments, **kwargs):  # noqa: ANN001, ANN202
        seen.extend(segments)
        return original(segments, **kwargs)

    provider.synthesize = _spy  # type: ignore[method-assign]
    console.preview_segment("一句。", VoiceSpec(speaker="serena"), role="host",
                            tts=provider)

    assert isinstance(seen[0], SpeechSegment)
    assert seen[0].role == "host"
