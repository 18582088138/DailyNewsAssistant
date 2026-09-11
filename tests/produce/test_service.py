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
   15. `segments=` 外部给定分段（TTS 操作台）：原样送去合成、字数按真正念的那批算；
       `segments=None` 时与 `speech_segments_for()` 切出来的**逐段一致**

预期 / Expected:
    耗时 < 3s；**全部使用假 provider，零 LLM 调用、零费用；不加载 TTS 模型**
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
        _env_file=None,
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "outputs",
        # 朗读友好化是**一次真的 LLM 调用**，开着会让「音频不重新计费」这类断言
        # 数不清账。它本身在 tests/tts/test_tts.py 里单独测。
        # A real LLM call; left on it would muddy the audio billing assertions.
        tts_preprocess=False,
    )


def seed(
    settings: Settings,
    *,
    body: str = "这是原文的技术内容。" * 200,
    via: SourceKind = SourceKind.RSS,
) -> str:
    """往台账里放一篇可用的文章 / Seed one usable article."""
    ledger = Ledger(settings.db_file)
    article_id, _ = ledger.register(
        RawItem(
            source_id="qbitai",
            via=via,
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


# 假回复的长度要落在 profile 配置的字数窗口里，否则每次都触发一次回炉，
# 把 ScriptedProvider 的剧本提前吃光——测的就不是 produce 层的护栏了。
#   summary_chars    = (80, 100)
#   shortvideo_chars = (200, 250)   英文折算约 77~96 词
#   narration_chars  = (400, 800)   英文折算约 154~308 词
_FAKE_SUMMARY = "这是一条摘要，说明了核心事实内容，包含关键数字 82.7 分与激活参数 13B。" * 2


def summary_reply(text: str = _FAKE_SUMMARY) -> str:
    return json.dumps({"summary": text, "tags": ["大模型"]}, ensure_ascii=False)


def translation_reply(article_id: str) -> str:
    return json.dumps(
        {"entries": [{"id": article_id, "title_en": "A Title", "summary_en": "A summary."}]},
        ensure_ascii=False,
    )


def short_reply(*, lang: str = "zh") -> str:
    script = "字" * 220 if lang == "zh" else " ".join(["benchmark"] * 85)
    return json.dumps(
        {"title": "推理成本砍半", "subtitle": "吞吐提升 2.3 倍", "script": script},
        ensure_ascii=False,
    )


def narration_reply(*, lang: str = "zh") -> str:
    """口播稿同样带主副标题——1~2 分钟的视频发布时也要填标题栏。"""
    script = "字" * 500 if lang == "zh" else " ".join(["throughput"] * 200)
    return json.dumps(
        {"title": "推理引擎开源", "subtitle": "吞吐提升 2.3 倍", "script": script},
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

    first = produce(article_id, ProductionKind.SUMMARY, settings=settings, llm=llm)
    second = produce(article_id, ProductionKind.SUMMARY, settings=settings, llm=llm)

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
    llm = ScriptedProvider("fake", [summary_reply("第一版" + _FAKE_SUMMARY[3:]),
                                    summary_reply("第二版" + _FAKE_SUMMARY[3:])])

    produce(article_id, ProductionKind.SUMMARY, settings=settings, llm=llm)
    produce(article_id, ProductionKind.SUMMARY, settings=settings, llm=llm, force=True)

    assert llm.call_count == 2
    history = Ledger(settings.db_file).production_history(article_id, "summary")
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

    result = produce(
        article_id, ProductionKind.SUMMARY, lang="en", settings=settings, llm=llm
    )

    assert result.ok
    assert llm.call_count == 2, "应先补中文总结再翻译"
    ledger = Ledger(settings.db_file)
    assert ledger.latest_production(article_id, "summary") is not None


def test_prerequisite_failure_is_reported_clearly(settings: Settings) -> None:
    """前置产物失败时，错误信息要说清是哪一步挂了。"""
    article_id = seed(settings)
    llm = ScriptedProvider("fake", ["不是 JSON"] * 6)

    result = produce(
        article_id, ProductionKind.SUMMARY, lang="en", settings=settings, llm=llm
    )

    assert not result.ok
    assert "前置产物" in result.error and "总结" in result.error


# --- 落盘 / files on disk -------------------------------------------------------


def test_output_lands_in_the_article_directory(settings: Settings) -> None:
    """产物写进文章目录，文件名与 TaskSpec 一致。"""
    article_id = seed(settings)
    llm = ScriptedProvider("fake", [summary_reply()])

    result = produce(article_id, ProductionKind.SUMMARY, settings=settings, llm=llm)

    assert result.path is not None
    assert result.path.name == spec(ProductionKind.SUMMARY).filename_for("zh")
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

    result = produce(article_id, ProductionKind.SUMMARY, settings=settings, llm=llm)
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

    result = produce(article_id, ProductionKind.SUMMARY, settings=settings, llm=llm)

    assert not result.ok
    record = Ledger(settings.db_file).latest_production(article_id, "summary")
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
    result = produce("不存在的id", ProductionKind.SUMMARY, settings=settings, llm=llm)

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

    result = produce(article_id, ProductionKind.SUMMARY, settings=settings, llm=llm)

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

    三项彼此独立，因为一项翻车就放弃整批是不合理的。
    """
    article_id = seed(settings)
    llm = ScriptedProvider(
        "fake",
        ["坏 JSON", "坏 JSON", "坏 JSON",           # 总结失败（3 次重试）
         short_reply(),                              # 短视频成功
         narration_reply()],                        # 口播成功
    )

    results = produce_all(article_id, settings=settings, llm=llm)
    by_kind = {str(r.kind): r for r in results}

    assert not by_kind["summary"].ok
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


def test_recorded_chars_count_the_script_not_the_whole_file(settings: Settings) -> None:
    """
    台账里的字数是**正文的字数**，不含抬头与主副标题。

    抬头带着文章标题和整条 URL：按整个文件长度记，一篇 400 字的口播稿会显示成
    六百多字，和同一格里的预估秒数、以及文件里那行「约 N 秒 · M 字」三个数字
    互相打架——实测报上来的就是这个。
    """
    article_id = seed(settings)
    llm = ScriptedProvider("fake", [narration_reply()])

    result = produce(article_id, ProductionKind.NARRATION, settings=settings, llm=llm)
    text = result.path.read_text(encoding="utf-8")

    assert result.chars == 500
    assert result.chars < len(text)
    # 文件自己写的字数与台账记的必须是同一个数
    assert f"· {result.chars} 字）" in text


# --- 「新导入」标识 / the "new import" flag ------------------------------------


def test_fresh_import_with_nothing_produced_is_new(settings: Settings) -> None:
    """人工粘进来、一条产物都没有 → 是新的 / Pasted and untouched is new."""
    from dna.produce import is_new_article

    article_id = seed(settings, via=SourceKind.GUI)
    record = Ledger(settings.db_file).get(article_id)

    assert is_new_article(record, {})


def test_any_production_clears_the_flag(settings: Settings) -> None:
    """
    生成了任意一项产物之后就不再是「新的」——这是用户对这个标识的定义。
    """
    from dna.produce import is_new_article

    article_id = seed(settings)
    llm = ScriptedProvider("fake", [summary_reply()])
    produce(article_id, ProductionKind.SUMMARY, settings=settings, llm=llm)

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
    result = produce(article_id, ProductionKind.SUMMARY, settings=settings, llm=llm)
    assert not result.ok

    ledger = Ledger(settings.db_file)
    record = ledger.get(article_id)
    matrix = ledger.production_matrix([article_id])

    assert matrix[article_id], "失败也应该记了一行"
    assert not is_new_article(record, matrix[article_id])


def test_rss_backlog_is_not_marked_new(settings: Settings) -> None:
    """
    **RSS 存量文章不算新的**，哪怕它一条产物都没有。

    实测台账 37 篇里有 32 篇从来没生成过任何产物（RSS 存量大多如此）。
    只看「有没有产物」的话，NEW 会挂在 32 行上——标识就失去意义了。
    Marking every production-less row would put the badge on 32 of 37 articles.
    """
    import dataclasses
    from datetime import timedelta

    from dna.produce import is_new_article

    article_id = seed(settings, via=SourceKind.RSS)
    record = Ledger(settings.db_file).get(article_id)
    stale = dataclasses.replace(
        record, first_seen_at=record.first_seen_at - timedelta(hours=25)
    )

    assert not is_new_article(stale, {})


def test_a_fresh_subscription_import_is_marked_new(settings: Settings) -> None:
    """
    **刚从订阅导进来的那批要挂标识。**

    否则一次导入几十条进来，界面上和存量文章长得一模一样，人分不出
    这次新到的是哪些——用户实测时把这个现象描述成「订阅导入的文章都没有 new」。
    Without it a fresh batch is indistinguishable from the backlog.
    """
    from dna.produce import is_new_article

    article_id = seed(settings, via=SourceKind.RSS)
    record = Ledger(settings.db_file).get(article_id)

    assert is_new_article(record, {})


def test_badge_survives_any_amount_of_time(settings: Settings) -> None:
    """
    **标识不因为过了一夜就消失。**

    先前的实现用**纯时间窗**判定，于是「昨天粘进来、今天还没处理」的链接
    第二天就失去标识——而那恰恰是最需要标识的一条。实测 `fe3b7d3c` 导入 47 小时、
    零产物，正是这样丢掉的。现在时间窗只是「或」的右边：它能加标识、拿不掉。
    这条测试守的就是这一点。
    A pure time window used to strip the badge from exactly the row that still needed it.
    The window is now only an OR branch, so it can add a badge but never remove one.
    """
    from dna.produce import is_new_article

    import dataclasses

    article_id = seed(settings, via=SourceKind.GUI)
    record = Ledger(settings.db_file).get(article_id)
    # 把入库时间推到一年前：判定里不该再有任何时间成分
    ancient = dataclasses.replace(
        record, first_seen_at=record.first_seen_at.replace(year=record.first_seen_at.year - 1)
    )

    assert is_new_article(ancient, {})


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

    def synthesize(self, segments, *, on_progress=None, run=None, rendered=None):  # noqa: ANN001, ANN201
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
    (directory / spec(kind).filename_for("zh")).write_text(
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


# --- 重做必须绕开缓存 / a redo must bypass the response cache ------------------


def test_redo_builds_an_uncached_provider(settings, monkeypatch) -> None:
    """
    **`force=True` 时 provider 不能带缓存。**

    LLM 响应缓存故意不设过期，键是「provider + 模型 + 完整提示词」——同样的输入
    永远给同样的输出，这正是它平时的价值。但「重做」的字面意思就是要一个不一样的：
    照常走缓存的话，模型确实被调了、文件确实被重写了，内容却一字未变，
    看起来就像**「生成了但没保存」**。这是 2026-09-06 实测报上来的现象。
    Served from cache, a redo calls the model, rewrites the file, and changes nothing —
    which reads as "it generated but did not save".

    断言的是**传给 `get_llm` 的参数**，不是调用结果：缓存行为本身在 `tests/llm`
    里已经验证过，这里要钉住的是「重做这条路径有没有把缓存关掉」。
    The assertion targets the argument rather than the outcome: cache behaviour is
    covered elsewhere, and what needs pinning here is whether this path disables it.
    """
    seen: list[bool | None] = []
    real_llm = ScriptedProvider("fake", [summary_reply(), summary_reply()])

    def fake_get_llm(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        seen.append(kwargs.get("cache"))
        return real_llm

    monkeypatch.setattr("dna.produce.service.get_llm", fake_get_llm)
    article_id = seed(settings)

    produce(article_id, ProductionKind.SUMMARY, settings=settings)
    produce(article_id, ProductionKind.SUMMARY, settings=settings, force=True)

    assert seen == [True, False], "首次生成走缓存，重做必须绕开"


# --- 修改指令 / the extra instructions ----------------------------------------


def test_instructions_reach_the_prompt(settings) -> None:
    """
    额外要求要真的进到提示词里 / The extra requirements really reach the prompt.

    不进提示词的话，这个输入框就是个安慰剂——人写了「加长到 40 秒」，
    界面收下了，模型没看见，出来的稿子一如既往。
    Otherwise the box is a placebo: the request is accepted, never delivered, and the
    output is unchanged.
    """
    article_id = seed(settings)
    llm = ScriptedProvider("fake", [summary_reply()])

    produce(
        article_id,
        ProductionKind.SUMMARY,
        settings=settings,
        llm=llm,
        instructions="多引用原文的具体数字",
    )

    system_prompt = llm.messages[0][0].content
    assert "多引用原文的具体数字" in system_prompt
    assert "额外要求" in system_prompt


def test_instructions_are_recorded(settings) -> None:
    """
    指令记进台账 / The instructions are recorded.

    调稿子是渐进的：下次重做要在上次的基础上再加一条，而不是从零回忆。
    界面用它预填输入框。
    Tuning is incremental, and the workbench pre-fills the box from this.
    """
    article_id = seed(settings)
    llm = ScriptedProvider("fake", [summary_reply()])

    produce(
        article_id,
        ProductionKind.SUMMARY,
        settings=settings,
        llm=llm,
        instructions="用词再专业一点",
    )

    record = Ledger(settings.db_file).latest_production(article_id, "summary")
    assert record.instructions == "用词再专业一点"


def test_no_instructions_leaves_the_prompt_byte_identical(settings) -> None:
    """
    没写指令时提示词**逐字不变** / An empty instruction changes nothing.

    否则每篇都会因为多一个空标题而错开 LLM 缓存，白花一轮钱——
    一个「本次没有额外要求」的空段落，代价是整批重新计费。
    Otherwise every call would miss the response cache over an empty heading, re-billing
    a whole batch for nothing.
    """
    from dna.narration.script_builder import instruction_block

    assert instruction_block("") == ""
    assert instruction_block("   ") == ""


# --- 语言维度 / the language dimension ----------------------------------------


def test_two_languages_are_two_files_and_two_records(settings) -> None:
    """
    **中英两版是两个文件、两条记录，互不覆盖。**

    语言要是漏出查询条件，生成过英文版之后再查中文版会拿到英文那一行——
    「已存在就跳过」于是拿英文版冒充中文版，一次都不会报错。
    If language fell out of the key, the skip-if-exists guard would pass off one edition
    as the other in silence.
    """
    article_id = seed(settings)
    llm = ScriptedProvider(
        "fake", [summary_reply(), translation_reply(article_id)]
    )

    zh = produce(article_id, ProductionKind.SUMMARY, settings=settings, llm=llm)
    en = produce(
        article_id, ProductionKind.SUMMARY, lang="en", settings=settings, llm=llm
    )

    assert zh.path.name == "summary.zh.md"
    assert en.path.name == "summary.en.md"
    assert zh.path.exists() and en.path.exists()

    ledger = Ledger(settings.db_file)
    assert ledger.latest_production(article_id, "summary", "zh") is not None
    assert ledger.latest_production(article_id, "summary", "en") is not None


def test_english_summary_translates_the_chinese_one(settings) -> None:
    """
    英文总结**翻译已写好的中文**，前置缺了会自动补。

    比用英文重写一遍便宜，而且中英两版保证说的是同一件事。
    Cheaper than rewriting, and both editions are guaranteed to say the same thing.
    """
    article_id = seed(settings)
    llm = ScriptedProvider("fake", [summary_reply(), translation_reply(article_id)])

    result = produce(
        article_id, ProductionKind.SUMMARY, lang="en", settings=settings, llm=llm
    )

    assert result.ok
    assert llm.call_count == 2, "一次中文总结 + 一次翻译"
    assert Ledger(settings.db_file).latest_production(article_id, "summary", "zh").ok


def test_english_scripts_are_written_natively(settings) -> None:
    """
    **英文文稿原生写，不翻译。**

    中文 30 秒的稿子翻成英文不是 30 秒的稿子，而时长正是这三种文案的验收标准。
    走翻译会把刚校准过的时长（issue 008）交给译文长度去决定。
    A thirty-second Chinese script is not a thirty-second English one, and duration is
    exactly what these kinds are accepted on.
    """
    article_id = seed(settings)
    llm = ScriptedProvider("fake", [short_reply(lang="en")])

    produce(
        article_id, ProductionKind.SHORTVIDEO, lang="en", settings=settings, llm=llm
    )

    assert llm.call_count == 1, "原生生成只要一次调用，没有翻译那一步"
    system_prompt = llm.messages[0][0].content
    assert "Writing rules" in system_prompt, "英文稿要用英文的写作要求"
    assert "words" in system_prompt, "英文按词数给预算，不是字数"


def test_given_segments_replace_the_automatic_split(settings: Settings) -> None:
    """
    **外部给的分段原样用，一个字不改。**

    TTS 操作台里逐段挑过音色之后，那批分段必须原样送去合成：重切一次会把
    挑好的音色丢掉，还会再做一次朗读友好化（预处理在切分之前，
    对已改写过的文本再改写一遍，切点也可能和试听过的那批不一样）。
    The console's pieces go through untouched: re-splitting would discard the chosen
    voices and re-run preprocessing on text that was already rewritten once.
    """
    from dna.tts.base import SpeechSegment, VoiceSpec

    article_id = seed(settings)
    _write_script(settings, article_id, ProductionKind.NARRATION, "第一句。第二句。")
    tts = FakeTTS()
    picked = VoiceSpec(speaker="uncle_fu", language="chinese")

    result = produce(
        article_id,
        ProductionKind.NARRATION_AUDIO,
        settings=settings,
        tts=tts,
        segments=[SpeechSegment(text="只念这一句。", voice=picked)],
    )

    assert result.ok
    assert [s.text for s in tts.segments] == ["只念这一句。"]
    assert [s.voice for s in tts.segments] == [picked]
    # 字数按真正念出去的那批算，否则台账里的字数和音频对不上
    assert result.chars == len("只念这一句。")


def test_no_segments_means_the_old_behaviour(settings: Settings) -> None:
    """
    `segments=None` 时与从前一字不差 / The default path is untouched.

    自动合成用的仍是 `_build_segments`，也就是 `speech_segments_for()`
    在操作台里展示的那一批——两个入口切得不一样的话，
    「面板里看到的」和「自动跑出来的」就是两回事。
    """
    from dna.produce.service import speech_segments_for

    article_id = seed(settings)
    _write_script(settings, article_id, ProductionKind.NARRATION, "第一句。第二句。")
    auto = speech_segments_for(article_id, ProductionKind.NARRATION_AUDIO, settings=settings)
    tts = FakeTTS()

    result = produce(
        article_id, ProductionKind.NARRATION_AUDIO, settings=settings, tts=tts,
        segments=None,
    )

    assert result.ok
    assert [(s.text, s.voice, s.role) for s in tts.segments] == [
        (s.text, s.voice, s.role) for s in auto
    ]
    assert result.chars == sum(len(s.text) for s in auto)


def test_rendered_pieces_reach_the_backend(settings: Settings) -> None:
    """
    已试听过的段以 `rendered` 透传到后端 / Pre-rendered pieces travel through.

    产物层只负责把它交下去（复用逻辑在 `tts/service.py`，那里有自己的测试）——
    这条钉的是这条参数不会在中间被吞掉：吞掉的表现是「操作台说复用 3 段，
    却又慢慢地全合成了一遍」。
    """
    from dna.tts.base import SpeechSegment, VoiceSpec

    article_id = seed(settings)
    _write_script(settings, article_id, ProductionKind.NARRATION, "第一句。第二句。")

    class _Spy(FakeTTS):
        rendered_seen: dict | None = None

        def synthesize(self, segments, *, on_progress=None, run=None, rendered=None):  # noqa: ANN001, ANN201
            self.rendered_seen = rendered
            return super().synthesize(segments, on_progress=on_progress, run=run)

    tts = _Spy()
    voice = VoiceSpec(speaker="serena", language="chinese")
    produce(
        article_id,
        ProductionKind.NARRATION_AUDIO,
        settings=settings,
        tts=tts,
        segments=[SpeechSegment(text="第一句。", voice=voice),
                  SpeechSegment(text="第二句。", voice=voice)],
        rendered={1: b"RIFFfake"},
    )

    assert tts.rendered_seen == {1: b"RIFFfake"}


# --- 人工校对写回 / the proof-read write-back ----------------------------------


def test_proofread_script_is_written_back_and_recorded(settings: Settings) -> None:
    """
    操作台里改过的文本要**同时**落到稿子文件和台账。

    只改文件不记台账，「这一版是谁改的、什么时候」就没了；只记台账不改文件，
    下一次自动合成又会念回 LLM 那一版。
    """
    from dna.produce.documents import spoken_text
    from dna.produce.service import save_script_text

    article_id = seed(settings)
    _write_script(settings, article_id, ProductionKind.NARRATION, "原来的一句。")
    ledger = Ledger(settings.db_file)
    before = ledger.latest_production(article_id, str(ProductionKind.NARRATION))

    row_id = save_script_text(
        article_id, ProductionKind.NARRATION_AUDIO,   # 操作台手里拿的是音频那一格
        "改过的一句，念起来顺一点。", settings=settings,
    )

    path = settings.output_path / ledger.get(article_id).store_dir
    document = (path / spec(ProductionKind.NARRATION).filename_for("zh")).read_text(
        encoding="utf-8"
    )
    assert spoken_text(document) == "改过的一句，念起来顺一点。"
    assert "https://e.com/1" in document, "抬头里的来源不能在写回时丢掉"

    record = ledger.latest_production(article_id, str(ProductionKind.NARRATION))
    assert record.id == row_id
    assert record.calls == 0, "人改的稿子不花钱"
    assert not record.llm_model, "没有模型写过这一版，这个字段就是它的标记"
    assert record.instructions == "人工校对（TTS 操作台）"
    assert record.redo_of_id == before.id, "历史仍由 redo_of_id 链承担"


def test_longform_script_cannot_be_flattened(settings: Settings) -> None:
    """
    长文案的稿子按发言人分轮存在 `.json` 附件里，一段纯文本写不回去 ——
    静默写坏比报错糟得多：访谈会变成一个人从头念到尾。
    """
    from dna.produce.service import save_script_text

    article_id = seed(settings)

    with pytest.raises(ValueError, match="按发言人分轮"):
        save_script_text(article_id, ProductionKind.LONGFORM, "一段纯文本。",
                         settings=settings)


def test_subtitles_follow_the_console_split(settings: Settings) -> None:
    """
    **字幕按操作台的拆分出**：一段一条，文本逐字就是那一段的文本。

    操作台把分段交给 `produce(segments=...)`，字幕在 `tts/service.py` 里按
    **每段的波形长度**算时间轴（不是服务端报的秒数——那个误差是累积的），
    再由产物层写到音频旁边。这条链断在哪一环，表现都是「字幕跟我拆的不一样」。
    """
    from dna.tts.base import SpeechSegment, VoiceSpec
    from dna.tts.subtitle import build_cues

    article_id = seed(settings)
    _write_script(settings, article_id, ProductionKind.NARRATION, "自动切出来的原稿。")

    class _WithCues(FakeTTS):
        """按真实服务的做法补上 cues（`FakeTTS` 本身不出字幕）。"""

        def synthesize(self, segments, *, on_progress=None, run=None, rendered=None):  # noqa: ANN001, ANN201
            clip = super().synthesize(segments, on_progress=on_progress, run=run)
            spoken = [(1.0, seg.text) for seg in segments]
            clip.cues = build_cues(spoken, [0.0] * len(segments))
            return clip

    voice = VoiceSpec(speaker="serena", language="chinese")
    texts = ["操作台里改过的第一段。", "手工加的第二段。", "第三段。"]
    result = produce(
        article_id,
        ProductionKind.NARRATION_AUDIO,
        settings=settings,
        tts=_WithCues(),
        segments=[SpeechSegment(text=t, voice=voice) for t in texts],
    )

    assert result.ok
    srt = result.path.with_suffix(".srt")
    assert srt.exists(), "字幕文件没写出来"
    body = srt.read_text(encoding="utf-8-sig")
    assert [line for line in body.splitlines() if line and "-->" not in line
            and not line.isdigit()] == texts
