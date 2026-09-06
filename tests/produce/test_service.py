"""
test_service.py —— 单篇产物生成单元测试 / Per-article production service tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/produce/ -v

对应的人工验证 / Matching manual check:
    dna produce <id> --all          # 常规四项
    dna gui                         # 表格里逐格重做

覆盖 / Covers:
    1. **已有产物且未 force 时直接返回，一次 LLM 都不调**——防 GUI 误触计费
    2. force=True 时才重做，并在 productions 表里插新行、redo_of_id 指向上一版
    3. **前置缺失时自动补**（英文总结依赖中文总结），不报错让人手动跑
    4. 产物写进文章目录，文件名与 TaskSpec 一致
    5. 长文案额外产出 `.json`（供 TTS 分配音色）
    6. **失败也记一行**，状态 failed 且带原因——不记的话表格显示「未生成」，
       人会以为没跑过，再点一次再失败一次
    7. 正文太短时拒绝长文案，且不调 LLM
    8. `produce_all` **不含长文案**（它一篇 5~9 次调用）
    9. 台账里没有的文章、没落盘的文章都返回明确错误而不是崩
   10. 产物文件带抬头（标题 + 来源链接），单独拷走仍可追溯
   11. **profile.yaml 的时长区间与结尾引导语真的传到构建器**——
       在此之前那三行时长配置是死配置，改了没反应
   12. 短视频稿与口播稿的产物文件里都写入主副标题
   13. **NEW 标识判定**：刚导入且无产物为新 · 任意产物（含失败）清掉标识 ·
       **存量文章不算新的** · 窗口设 0 关闭时间检查
   14. 音频产物（P4.5）：**一次 LLM 都不调** · 按字节写盘且台账记真实时长 ·
       **只念正文不念抬头里的网址** · 访谈两个角色两把嗓子 ·
       **重做音频不会把稿子重新计费** · 音频不进 `--all`

预期 / Expected:
    27 passed；耗时 < 3s；**全部使用假 provider，零 LLM 调用、零费用；不加载 TTS 模型**
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from dna.core.config import Settings
from dna.core.models import Article, RawItem, SourceKind
from dna.produce import ProductionKind, produce, produce_all
from dna.produce.tasks import batch_kinds, spec
from dna.store.ledger import Ledger
from tests.llm.fakes import ScriptedProvider


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """指向临时目录的配置 / Settings pointing at a temp directory."""
    return Settings(
        _env_file=None, data_dir=tmp_path / "data", output_dir=tmp_path / "outputs"
    )


def seed(settings: Settings, *, body: str = "这是原文的技术内容。" * 200) -> str:
    """往台账里放一篇可用的文章 / Seed one usable article."""
    ledger = Ledger(settings.db_file)
    article_id, _ = ledger.register(
        RawItem(
            source_id="qbitai",
            via=SourceKind.RSS,
            url="https://e.com/1",
            title="某公司发布新一代推理引擎",
        )
    )

    article = Article(
        url="https://e.com/1",
        title="某公司发布新一代推理引擎",
        text=body,
        extraction_ok=True,
        published_at=datetime.now(),
    )
    store_dir = f"articles/20260903/{article_id[:8]}"
    directory = settings.output_path / store_dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "meta.json").write_text(article.model_dump_json(indent=2), encoding="utf-8")
    ledger.record_fetch(article_id, article, store_dir=store_dir)
    return article_id


def summary_reply(text: str = "这是一条摘要，说明了核心事实内容。") -> str:
    return json.dumps({"summary": text, "tags": ["大模型"]}, ensure_ascii=False)


def translation_reply(article_id: str) -> str:
    return json.dumps(
        {"entries": [{"id": article_id, "title_en": "A Title", "summary_en": "A summary."}]},
        ensure_ascii=False,
    )


def short_reply() -> str:
    return json.dumps(
        {"title": "推理成本砍半", "subtitle": "吞吐提升 2.3 倍", "script": "字" * 130},
        ensure_ascii=False,
    )


def narration_reply() -> str:
    """口播稿同样带主副标题——1~2 分钟的视频发布时也要填标题栏。"""
    return json.dumps(
        {"title": "推理引擎开源", "subtitle": "吞吐提升 2.3 倍", "script": "字" * 400},
        ensure_ascii=False,
    )


# --- 防重复计费 / not paying twice ---------------------------------------------


def test_existing_production_is_reused_without_calling_the_llm(settings: Settings) -> None:
    """
    已有产物且未 force 时**一次 LLM 都不调**。

    GUI 里按钮就在手边，误触一次不该等于一次计费。这是整个 produce 层最重要的保护。
    """
    article_id = seed(settings)
    llm = ScriptedProvider("fake", [summary_reply()])

    first = produce(article_id, ProductionKind.SUMMARY_ZH, settings=settings, llm=llm)
    second = produce(article_id, ProductionKind.SUMMARY_ZH, settings=settings, llm=llm)

    assert llm.call_count == 1, "第二次不该调用 LLM"
    assert first.ok and not first.skipped
    assert second.ok and second.skipped


def test_force_regenerates_and_records_a_new_version(settings: Settings) -> None:
    """
    force 才重做，并在台账里插新行、指向上一版。

    原地更新会把上一版连同它的模型和时间一起抹掉，而内容出问题时
    「这段稿子是哪天用哪个模型写的」必须能回答。
    """
    article_id = seed(settings)
    llm = ScriptedProvider("fake", [summary_reply("第一版摘要内容，说明核心事实。"),
                                    summary_reply("第二版摘要内容，说明核心事实。")])

    produce(article_id, ProductionKind.SUMMARY_ZH, settings=settings, llm=llm)
    produce(article_id, ProductionKind.SUMMARY_ZH, settings=settings, llm=llm, force=True)

    assert llm.call_count == 2
    history = Ledger(settings.db_file).production_history(article_id, "summary_zh")
    assert len(history) == 2
    assert history[0].redo_of_id == history[1].id


# --- 前置依赖 / prerequisites ---------------------------------------------------


def test_missing_prerequisite_is_produced_automatically(settings: Settings) -> None:
    """
    英文总结依赖中文总结，缺了就先补。

    报错让人手动先跑一遍中文总结是没必要的摩擦——GUI 里点「英文总结」的人
    想要的就是英文总结，不关心它内部依赖什么。
    """
    article_id = seed(settings)
    llm = ScriptedProvider("fake", [summary_reply(), translation_reply(article_id)])

    result = produce(article_id, ProductionKind.SUMMARY_EN, settings=settings, llm=llm)

    assert result.ok
    assert llm.call_count == 2, "应先补中文总结再翻译"
    ledger = Ledger(settings.db_file)
    assert ledger.latest_production(article_id, "summary_zh") is not None


def test_prerequisite_failure_is_reported_clearly(settings: Settings) -> None:
    """前置产物失败时，错误信息要说清是哪一步挂了。"""
    article_id = seed(settings)
    llm = ScriptedProvider("fake", ["不是 JSON"] * 6)

    result = produce(article_id, ProductionKind.SUMMARY_EN, settings=settings, llm=llm)

    assert not result.ok
    assert "前置产物" in result.error and "总结" in result.error


# --- 落盘 / files on disk -------------------------------------------------------


def test_output_lands_in_the_article_directory(settings: Settings) -> None:
    """产物写进文章目录，文件名与 TaskSpec 一致。"""
    article_id = seed(settings)
    llm = ScriptedProvider("fake", [summary_reply()])

    result = produce(article_id, ProductionKind.SUMMARY_ZH, settings=settings, llm=llm)

    assert result.path is not None
    assert result.path.name == spec(ProductionKind.SUMMARY_ZH).filename
    assert result.path.exists()
    assert "articles" in result.path.parts


def test_output_carries_a_traceable_header(settings: Settings) -> None:
    """
    产物文件带标题与来源链接。

    这些文件会被单独拷去发布，脱离目录之后仍要能追溯来源——
    和图片旁边放 .json 是同一个道理。
    """
    article_id = seed(settings)
    llm = ScriptedProvider("fake", [summary_reply()])

    result = produce(article_id, ProductionKind.SUMMARY_ZH, settings=settings, llm=llm)
    text = result.path.read_text(encoding="utf-8")

    assert "某公司发布新一代推理引擎" in text
    assert "https://e.com/1" in text


def test_longform_also_writes_a_json_sidecar(settings: Settings) -> None:
    """
    长文案额外产出 .json —— P6/P7 的 TTS 按 speaker 分配音色要用它。
    """
    article_id = seed(settings)
    outline = json.dumps(
        {"sections": [{"title": "第一节", "points": ["要点"], "target_chars": 400}]},
        ensure_ascii=False,
    )
    section = json.dumps(
        {"turns": [{"speaker": "host", "text": "提问"}, {"speaker": "guest", "text": "回答"}]},
        ensure_ascii=False,
    )
    llm = ScriptedProvider("fake", [outline, section])

    result = produce(
        article_id, ProductionKind.LONGFORM, variant="interview", settings=settings, llm=llm
    )

    assert result.ok
    sidecar = result.path.with_name("longform.zh.json")
    assert sidecar.exists()

    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["mode"] == "interview"
    assert {t["speaker"] for t in data["turns"]} == {"host", "guest"}


# --- 失败处理 / failures --------------------------------------------------------


def test_failure_is_recorded_in_the_ledger(settings: Settings) -> None:
    """
    失败也要记一行。

    不记的话表格里显示「未生成」，人会以为没跑过，于是再点一次、再失败一次——
    每次都在花钱。记下来才能看见「这篇试过了，失败原因是 X」。
    """
    article_id = seed(settings)
    llm = ScriptedProvider("fake", ["不是 JSON"] * 4)

    result = produce(article_id, ProductionKind.SUMMARY_ZH, settings=settings, llm=llm)

    assert not result.ok
    record = Ledger(settings.db_file).latest_production(article_id, "summary_zh")
    assert record is not None
    assert record.status == "failed"
    assert record.error


def test_short_article_rejects_longform_without_calling_the_llm(settings: Settings) -> None:
    """
    正文太短时拒绝长文案，**且不发请求**。

    长文案是最贵的产物，不够料的文章做出来一定是注水稿——先判断再决定花不花钱。
    """
    article_id = seed(settings, body="很短的正文。")
    llm = ScriptedProvider("fake", ["不该被用到"])

    result = produce(article_id, ProductionKind.LONGFORM, settings=settings, llm=llm)

    assert not result.ok
    assert "不足" in result.error
    assert llm.call_count == 0


def test_unknown_article_returns_a_clear_error(settings: Settings) -> None:
    """台账里没有这篇时给明确错误，不崩。"""
    llm = ScriptedProvider("fake", [])
    result = produce("不存在的id", ProductionKind.SUMMARY_ZH, settings=settings, llm=llm)

    assert not result.ok
    assert "没有这篇文章" in result.error
    assert llm.call_count == 0


def test_article_without_store_dir_returns_a_clear_error(settings: Settings) -> None:
    """还没落盘的文章不能生成产物——没有地方写。"""
    ledger = Ledger(settings.db_file)
    article_id, _ = ledger.register(
        RawItem(source_id="s", via=SourceKind.RSS, url="https://e.com/x", title="还没抓的那条")
    )
    llm = ScriptedProvider("fake", [])

    result = produce(article_id, ProductionKind.SUMMARY_ZH, settings=settings, llm=llm)

    assert not result.ok
    assert "落盘" in result.error
    assert llm.call_count == 0


# --- 批量 / the batch -----------------------------------------------------------


def test_batch_excludes_longform(settings: Settings) -> None:
    """
    `--all` **不含长文案**。

    它一篇 5~9 次调用，是其余四项加起来的两倍多。跟着批量跑的话，
    一次误操作就是十几倍的账单。
    """
    assert ProductionKind.LONGFORM not in batch_kinds()

    article_id = seed(settings)
    llm = ScriptedProvider(
        "fake",
        [summary_reply(), translation_reply(article_id), short_reply(),
         narration_reply()],
    )

    results = produce_all(article_id, settings=settings, llm=llm)

    assert {str(r.kind) for r in results} == {str(k) for k in batch_kinds()}
    assert all(r.ok for r in results)
    assert Ledger(settings.db_file).latest_production(article_id, "longform") is None


def test_batch_continues_after_one_failure(settings: Settings) -> None:
    """
    一项失败不阻断其余项。

    四项彼此独立，因为一项翻车就放弃整批是不合理的。
    """
    article_id = seed(settings)
    llm = ScriptedProvider(
        "fake",
        ["坏 JSON", "坏 JSON", "坏 JSON",           # 中文总结失败（3 次重试）
         "坏 JSON", "坏 JSON", "坏 JSON",           # 英文总结的前置也失败
         short_reply(),                              # 短视频成功
         narration_reply()],                        # 口播成功
    )

    results = produce_all(article_id, settings=settings, llm=llm)
    by_kind = {str(r.kind): r for r in results}

    assert not by_kind["summary_zh"].ok
    assert by_kind["shortvideo"].ok
    assert by_kind["narration"].ok


def test_profile_windows_and_sign_off_reach_the_builder(settings: Settings) -> None:
    """
    `profile.yaml` 里的时长区间与结尾引导语必须真的传到构建器。

    在此之前这三行时长配置是**死配置**：构建器用的是自己的默认参数，
    改了 profile.yaml 完全没反应——比不提供这个配置更糟，因为人会以为改生效了。
    """
    from dna.core.config import Profile

    article_id = seed(settings)
    profile = Profile(
        video_duration_seconds=(40, 50), cta_line="订阅频道，每天一条 AI 快讯"
    )
    llm = ScriptedProvider("fake", [short_reply(), short_reply()])

    produce(
        article_id,
        ProductionKind.SHORTVIDEO,
        settings=settings,
        profile=profile,
        llm=llm,
    )

    prompt = " ".join(m.content for m in llm.messages[0])
    assert "40~50 秒" in prompt
    assert "订阅频道，每天一条 AI 快讯" in prompt


def test_video_scripts_write_the_title_pair_into_the_file(settings: Settings) -> None:
    """
    短视频稿与口播稿的产物文件里都要有主副标题。

    发布时标题栏要填，写在产物里就不用再想一遍，也保证标题与文案出自
    同一次生成、口径一致。
    """
    article_id = seed(settings)
    llm = ScriptedProvider("fake", [narration_reply()])

    result = produce(article_id, ProductionKind.NARRATION, settings=settings, llm=llm)
    text = result.path.read_text(encoding="utf-8")

    assert "**主标题：** 推理引擎开源" in text
    assert "**副标题：** 吞吐提升 2.3 倍" in text


# --- 「新导入」标识 / the "new import" flag ------------------------------------


def test_fresh_import_with_nothing_produced_is_new(settings: Settings) -> None:
    """刚导入、一条产物都没有 → 是新的。"""
    from dna.produce import is_new_article

    article_id = seed(settings)
    record = Ledger(settings.db_file).get(article_id)

    assert is_new_article(record, {})


def test_any_production_clears_the_flag(settings: Settings) -> None:
    """
    生成了任意一项产物之后就不再是「新的」——这是用户对这个标识的定义。
    """
    from dna.produce import is_new_article

    article_id = seed(settings)
    llm = ScriptedProvider("fake", [summary_reply()])
    produce(article_id, ProductionKind.SUMMARY_ZH, settings=settings, llm=llm)

    ledger = Ledger(settings.db_file)
    record = ledger.get(article_id)
    matrix = ledger.production_matrix([article_id])

    assert not is_new_article(record, matrix[article_id])


def test_a_failed_attempt_also_clears_the_flag(settings: Settings) -> None:
    """
    **失败的尝试同样清掉标识**。

    失败记录说明 LLM 已经调过了、钱已经花了，这篇不再是「没动过」。
    而且失败的格子本身显示 ▲，和 NEW 摆在一起是自相矛盾的信号：
    一个说「还没开始」，一个说「试过而且出错了」。
    """
    from dna.produce import is_new_article

    article_id = seed(settings)
    llm = ScriptedProvider("fake", ["不是 JSON"] * 3)
    result = produce(article_id, ProductionKind.SUMMARY_ZH, settings=settings, llm=llm)
    assert not result.ok

    ledger = Ledger(settings.db_file)
    record = ledger.get(article_id)
    matrix = ledger.production_matrix([article_id])

    assert matrix[article_id], "失败也应该记了一行"
    assert not is_new_article(record, matrix[article_id])


def test_old_untouched_articles_are_not_new(settings: Settings) -> None:
    """
    **存量文章不算新的**，哪怕它一条产物都没有。

    实测台账 37 篇里有 32 篇从来没生成过任何产物（RSS 存量大多如此）。
    只看「有没有产物」的话，NEW 会挂在 32 行上——而这个标识的全部意义
    就是从几十行里找出刚粘进去的那几条。
    """
    from datetime import datetime, timedelta

    from dna.produce import is_new_article

    article_id = seed(settings)
    record = Ledger(settings.db_file).get(article_id)
    much_later = datetime.now() + timedelta(hours=30)

    assert not is_new_article(record, {}, now=much_later, within_hours=24)
    assert is_new_article(record, {}, now=much_later, within_hours=48)


def test_zero_window_disables_the_time_check(settings: Settings) -> None:
    """
    窗口设 0 表示不限时间，只看有没有产物——给想要「所有没动过的都标出来」的人。
    """
    from datetime import datetime, timedelta

    from dna.produce import is_new_article

    article_id = seed(settings)
    record = Ledger(settings.db_file).get(article_id)
    much_later = datetime.now() + timedelta(days=365)

    assert is_new_article(record, {}, now=much_later, within_hours=0)


def test_calls_are_reported_for_cost_visibility(settings: Settings) -> None:
    """
    调用次数要如实返回。

    GUI 与 CLI 都靠它把成本摆在人眼前——看不见的成本最容易失控。
    """
    article_id = seed(settings)
    llm = ScriptedProvider("fake", [short_reply()])

    result = produce(article_id, ProductionKind.SHORTVIDEO, settings=settings, llm=llm)

    assert result.calls == 1
    assert result.seconds is not None
    assert "次调用" in result.summary()


# --- 音频产物 / audio productions ---------------------------------------------
#
# 音频走的是**同一条路**：同样的「已存在不重跑」、同样的失败入账、同样的逐格重做。
# 这几条验证的是那条路对二进制载荷同样成立，以及它**不碰 LLM**。
# Audio takes the same path; these pin that the path works for a binary payload and that
# it never touches the LLM.


class FakeTTS:
    """
    不加载模型的假 TTS / A TTS backend that loads nothing.

    记下收到的分段，返回一段固定的 WAV。真机合成在 `dna tts --say` 里做——
    加载一次 10 秒、合成一次几十秒，不该进单测。
    Records the segments it receives and returns a fixed WAV. Real synthesis is a manual
    check: loading takes ten seconds and synthesis tens more.
    """

    def __init__(self) -> None:
        self.segments: list = []

    @property
    def info(self):  # noqa: ANN201
        from dna.tts.base import TTSInfo

        return TTSInfo(name="fake_tts", model="fake", device="CPU")

    def available_speakers(self) -> list[str]:
        return ["serena", "uncle_fu"]

    def synthesize(self, segments, *, on_progress=None):  # noqa: ANN001, ANN201
        import numpy as np

        from dna.tts.base import AudioClip, encode_wav

        self.segments = list(segments)
        samples = np.zeros(24_000 * len(self.segments), dtype=np.float32)
        return AudioClip(
            wav=encode_wav(samples, 24_000),
            sample_rate=24_000,
            seconds=float(len(self.segments)),
            segments=len(self.segments),
        )


def _write_script(settings: Settings, article_id: str, kind: ProductionKind, body: str) -> None:
    """直接放一份稿子，跳过 LLM / Drop a script in place, bypassing the LLM."""
    from dna.produce.documents import front_matter

    ledger = Ledger(settings.db_file)
    directory = settings.output_path / ledger.get(article_id).store_dir
    directory.mkdir(parents=True, exist_ok=True)

    article = Article(url="https://e.com/1", title="某公司发布新一代推理引擎")
    (directory / spec(kind).filename).write_text(
        front_matter(article, spec(kind).label) + f"**口播（约 30 秒 · 100 字）：**\n\n{body}",
        encoding="utf-8",
    )
    ledger.record_production(article_id, str(kind), status="ok", chars=len(body))


def test_audio_never_calls_the_llm(settings: Settings) -> None:
    """
    **合成音频一次 LLM 都不调。**

    它一分钱不花，却会因为无条件的 `get_llm()` 而要求 API key ——
    在没配 key 的机器上，本来能跑的合成会先在这一步失败。
    Audio spends nothing, yet an unconditional `get_llm()` would demand an API key and
    fail on a machine where synthesis would otherwise work.
    """
    article_id = seed(settings)
    _write_script(settings, article_id, ProductionKind.NARRATION, "第一句。第二句。")
    llm = ScriptedProvider("fake", [])
    tts = FakeTTS()

    result = produce(
        article_id,
        ProductionKind.NARRATION_AUDIO,
        settings=settings,
        llm=llm,
        tts=tts,
    )

    assert result.ok
    assert llm.call_count == 0
    assert tts.segments


def test_audio_is_written_as_bytes_and_recorded(settings: Settings) -> None:
    """音频按字节写盘，台账记的是**真实时长** / Audio is written as bytes with a real duration."""
    article_id = seed(settings)
    _write_script(settings, article_id, ProductionKind.NARRATION, "第一句。第二句。")

    result = produce(
        article_id, ProductionKind.NARRATION_AUDIO, settings=settings, tts=FakeTTS()
    )

    assert result.path.name == "narration.zh.wav"
    assert result.path.read_bytes().startswith(b"RIFF")
    record = Ledger(settings.db_file).latest_production(
        article_id, str(ProductionKind.NARRATION_AUDIO)
    )
    assert record.ok
    assert record.est_seconds == result.seconds
    assert record.llm_provider == "fake_tts"  # 记的是 TTS 后端，不是 LLM


def test_audio_speaks_only_the_script_body(settings: Settings) -> None:
    """
    **抬头里的网址不能被念出来。**产物文件带 `> 口播文案　·　来源：https://…`，
    整篇照念的话，模型会把网址一个字符一个字符读出来，整段音频报废。
    The header carries a source URL; reading the whole file aloud would destroy the take.
    """
    article_id = seed(settings)
    _write_script(settings, article_id, ProductionKind.NARRATION, "第一句。第二句。")
    tts = FakeTTS()

    produce(article_id, ProductionKind.NARRATION_AUDIO, settings=settings, tts=tts)

    spoken = "".join(s.text for s in tts.segments)
    assert "http" not in spoken
    assert "第一句。" in spoken


def test_interview_audio_uses_two_voices(settings: Settings) -> None:
    """
    访谈的两个角色落在两把不同的嗓子上 / An interview's two roles get two voices.

    长文案的 `.json` 附件已经按发言人切好了轮次，音频这边照着分配音色即可——
    有结构化数据就用结构化数据，不回头解析 Markdown。
    The sidecar is already split into speaker turns, so the audio assigns voices from it.
    """
    article_id = seed(settings)
    _write_script(settings, article_id, ProductionKind.LONGFORM, "占位正文。")

    ledger = Ledger(settings.db_file)
    directory = settings.output_path / ledger.get(article_id).store_dir
    (directory / "longform.zh.json").write_text(
        json.dumps(
            {
                "mode": "interview",
                "turns": [
                    {"speaker": "host", "text": "第一个问题是什么？"},
                    {"speaker": "guest", "text": "第一个答案是这样。"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tts = FakeTTS()

    produce(article_id, ProductionKind.LONGFORM_AUDIO, settings=settings, tts=tts)

    voices = {s.role: s.voice.speaker for s in tts.segments}
    assert set(voices) == {"host", "guest"}
    assert voices["host"] != voices["guest"]


def test_redoing_audio_does_not_rebill_the_script(settings: Settings) -> None:
    """
    **重做音频不该顺手把稿子也重新调一遍 LLM。**

    下游重做是免费的，上游重做是要花钱的。让一个动作同时触发两者，
    等于把「免费」按钮偷偷接上账单。
    The downstream redo is free while the upstream one costs money; letting one click
    trigger both wires a bill onto a button labelled free.
    """
    article_id = seed(settings)
    _write_script(settings, article_id, ProductionKind.NARRATION, "第一句。第二句。")
    llm = ScriptedProvider("fake", [])

    produce(
        article_id,
        ProductionKind.NARRATION_AUDIO,
        settings=settings,
        llm=llm,
        tts=FakeTTS(),
        force=True,
    )

    assert llm.call_count == 0


def test_audio_is_not_in_the_batch() -> None:
    """
    **音频不进 `--all`。**它不花钱，但口播要 4 分钟、长文案要 37 分钟；
    批量里混进一个半小时的任务等同于把机器按死。
    Free but slow: a batch carrying a half-hour task is a frozen machine.
    """
    for kind in (
        ProductionKind.SHORTVIDEO_AUDIO,
        ProductionKind.NARRATION_AUDIO,
        ProductionKind.LONGFORM_AUDIO,
    ):
        assert kind not in batch_kinds()
        assert spec(kind).approx_calls == 0
