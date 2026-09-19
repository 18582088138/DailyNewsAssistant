"""
test_prompt_lab.py —— 提示词调试台单元测试 / Prompt workbench tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/produce/test_prompt_lab.py -v

对应的人工验证 / Matching manual check:
    dna prompt --list
    dna prompt <id> -t narration                    # 免费，看提示词
    dna prompt <id> -t longform --variant interview  # 提纲 + 每节各一条
    dna prompt <id> -t narration --run               # **会计费**，阶段验收时跑一次

覆盖 / Covers:
    1. **干跑零 LLM 调用**：`render()` 不碰真 provider，也不写任何文件
    2. 干跑拿到的提示词**含 chat_json 追加的那条 JSON 指令**——
       这是拦在 provider 边界上而不是 build_messages 那一层的全部意义
    3. 长文案干跑拿到「提纲 + 每节」多条提示词，不是只有第一条
    4. `instructions` 出现在提示词末尾（应用里也是接在末尾）
    5. 语言维度：`--lang en` 用英文写作要求
    6. 干跑不返回产物字数/时长——那些是占位回复的属性，不是模型的
    7. 前置检查与 `produce()` 一致：台账没这篇 / 没落盘 / 正文太短都给明确错误
    8. `run()` 注入假 provider 时**不写文件、不记台账**（调试跑十次不留垃圾）
    9. `save()` 把每条提示词、产物与 meta.json 落盘，供前后对比
   10. **调用路径与应用同源**：`render()` 与 `produce()` 对同一篇文章生成的
       提示词逐字节相同

第 10 条是这个模块存在的前提 / Why case 10 matters most:
    「在调试台里验证过的提示词，在应用里生效」这句话必须是可验证的事实，
    而不是承诺。断言的方式是把两条路各自发出的 messages 抓下来对比。

预期 / Expected:
    耗时 < 3s；**全部使用假 provider，零 LLM 调用、零费用**
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from dna.core.config import Settings
from dna.core.models import Article, RawItem, SourceKind
from dna.llm.capture import RecordingProvider
from dna.produce import prompt_lab
from dna.produce.tasks import ProductionKind
from dna.store.ledger import Ledger
from tests.llm.fakes import ScriptedProvider


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "outputs",
        tts_preprocess=False,
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


# ---------------------------------------------------------------------------
# 干跑 / the dry run
# ---------------------------------------------------------------------------


def test_render_costs_nothing_and_writes_nothing(settings: Settings) -> None:
    """
    **干跑零调用零落盘。** 不加 --run 时一次 LLM 都不该发出去。
    """
    article_id = seed(settings)
    directory = settings.output_path / f"articles/20260903/{article_id[:8]}"
    before = sorted(p.name for p in directory.iterdir())

    result = prompt_lab.render(article_id, ProductionKind.NARRATION, settings=settings)

    assert result.ok, result.error
    assert result.billed is False
    assert result.prompts
    assert sorted(p.name for p in directory.iterdir()) == before
    assert Ledger(settings.db_file).latest_production(article_id, "narration", "zh") is None


def test_prompt_includes_the_json_instruction(settings: Settings) -> None:
    """
    提示词里必须带上 `chat_json()` 追加的那条 JSON 指令。

    业务代码构造的 messages **不等于**真正发出去的 messages：基类会在末尾
    再加一条带完整 JSON Schema 的指令。在 `build_messages()` 那一层看提示词
    会漏掉它，而它恰恰是小模型最容易出问题的地方。
    """
    article_id = seed(settings)
    result = prompt_lab.render(article_id, ProductionKind.NARRATION, settings=settings)

    joined = "\n".join(result.prompts)
    assert "Schema" in joined
    assert "只输出 JSON" in joined
    assert "NarrationOut" in joined


def test_longform_renders_one_prompt_per_section(settings: Settings) -> None:
    """长文案是十几次调用拼起来的，每一节的提示词都不一样，要全部拿到。"""
    article_id = seed(settings)
    result = prompt_lab.render(
        article_id, ProductionKind.LONGFORM, variant="interview", settings=settings
    )

    assert result.ok, result.error
    assert len(result.prompts) >= 2, "至少要有提纲 + 一节"
    assert "规划一篇" in result.prompts[0]  # 第一条是提纲
    assert "现在写这节" in result.prompts[1]  # 之后是分节展开
    assert "主持人只提问" in result.prompts[1]  # 访谈模式的角色规则生效


def test_instructions_land_at_the_end(settings: Settings) -> None:
    """额外要求接在提示词末尾——后出现的指令权重更高，应用里也是这么拼的。"""
    article_id = seed(settings)
    result = prompt_lab.render(
        article_id,
        ProductionKind.SHORTVIDEO,
        instructions="加长到 40 秒",
        settings=settings,
    )

    prompt = result.prompts[0]
    assert "加长到 40 秒" in prompt
    assert prompt.index("本次的额外要求") > prompt.index("信息密度优先")


def test_english_uses_the_english_rules(settings: Settings) -> None:
    article_id = seed(settings)
    result = prompt_lab.render(
        article_id, ProductionKind.NARRATION, lang="en", settings=settings
    )

    prompt = result.prompts[0]
    assert "Information density comes first" in prompt
    assert "信息密度优先" not in prompt


def test_dry_run_reports_no_output_metrics(settings: Settings) -> None:
    """
    干跑不报字数与时长：那是占位回复的属性，留着会被当成模型的结果读。
    """
    article_id = seed(settings)
    result = prompt_lab.render(article_id, ProductionKind.NARRATION, settings=settings)

    assert result.output == ""
    assert result.chars == 0
    assert result.seconds is None
    assert result.within_target is None
    assert "干跑" in result.summary()


# ---------------------------------------------------------------------------
# 前置检查，与 produce() 一致 / the same pre-checks produce() runs
# ---------------------------------------------------------------------------


def test_unknown_article_reports_clearly(settings: Settings) -> None:
    result = prompt_lab.render("nope", ProductionKind.NARRATION, settings=settings)
    assert not result.ok
    assert "台账里没有" in result.error


def test_article_without_store_dir_reports_clearly(settings: Settings) -> None:
    ledger = Ledger(settings.db_file)
    article_id, _ = ledger.register(
        RawItem(source_id="s", via=SourceKind.RSS, url="https://e.com/2", title="没落盘")
    )
    result = prompt_lab.render(article_id, ProductionKind.NARRATION, settings=settings)
    assert not result.ok
    assert "落盘目录" in result.error


def test_short_body_refuses_longform_without_calling(settings: Settings) -> None:
    """正文不够的文章，应用会拒绝；调试台也必须拒绝，否则会渲染出一份用不上的提示词。"""
    article_id = seed(settings, body="太短了。")
    result = prompt_lab.render(article_id, ProductionKind.LONGFORM, settings=settings)
    assert not result.ok
    assert result.prompts == []


def test_missing_prerequisite_reports_instead_of_rendering_an_empty_prompt(
    settings: Settings,
) -> None:
    """
    英文总结的输入是**已写好的中文总结**。它还没生成时必须报错。

    静默渲染的话会得到一份「摘要：」后面空着的提示词，看着像提示词写坏了；
    而代为生成又会让一个默认免费的命令悄悄发起一次计费调用。
    """
    article_id = seed(settings)
    result = prompt_lab.render(
        article_id, ProductionKind.SUMMARY, lang="en", settings=settings
    )
    assert not result.ok
    assert result.prompts == []
    assert "还没生成" in result.error
    assert "dna produce" in result.error


# ---------------------------------------------------------------------------
# 真跑（注入假 provider）与落盘 / the live path, with a fake provider
# ---------------------------------------------------------------------------


def test_run_returns_output_but_persists_nothing(settings: Settings) -> None:
    """
    `run()` 带回产物，但**不写文件、不记台账**。

    调试台跑十次不该在产物目录里留十份垃圾，也不该搅乱台账的产物历史——
    那本账记的是「发布用的那一版是谁写的」。
    """
    article_id = seed(settings)
    directory = settings.output_path / f"articles/20260903/{article_id[:8]}"
    before = sorted(p.name for p in directory.iterdir())

    reply = json.dumps(
        {"title": "推理引擎开源", "subtitle": "吞吐提升 2.3 倍", "script": "字" * 400},
        ensure_ascii=False,
    )
    llm = ScriptedProvider("fake", [reply])

    result = prompt_lab.run(
        article_id, ProductionKind.NARRATION, settings=settings, llm=llm
    )

    assert result.ok, result.error
    assert "推理引擎开源" in result.output
    assert result.chars > 0
    assert result.billed is False  # 注入了 provider，不算真机计费
    assert result.prompts, "真跑也要能看到这次发出去的提示词"
    assert sorted(p.name for p in directory.iterdir()) == before
    assert Ledger(settings.db_file).latest_production(article_id, "narration", "zh") is None


def test_save_writes_prompts_output_and_meta(settings: Settings, tmp_path: Path) -> None:
    article_id = seed(settings)
    result = prompt_lab.render(
        article_id, ProductionKind.LONGFORM, variant="feature", settings=settings
    )

    root = prompt_lab.save(result, tmp_path / "lab")

    assert (root / "prompt_01.txt").read_text(encoding="utf-8")
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    assert meta["task"] == "longform"
    assert meta["article_id"] == article_id
    assert meta["prompt_files"]
    assert meta["billed"] is False


def test_prompt_files_are_listed_for_every_task() -> None:
    """`dna prompt --list` 要能回答「这个任务读哪个文件」。"""
    for kind in prompt_lab.TASKS:
        assert prompt_lab.prompt_files_for(kind), f"{kind} 没登记提示词文件"


# ---------------------------------------------------------------------------
# 与应用同源 / identical to what the app sends
# ---------------------------------------------------------------------------


def test_render_matches_what_produce_sends(settings: Settings) -> None:
    """
    **调试台的提示词与 `produce()` 发出的逐字节相同。**

    这是整个模块的前提。两条路都调 `service.generate_text()`，差别只在注入的
    provider；如果哪天有人另写一份分派逻辑，这一条会立刻红。
    """
    from dna.produce import produce

    article_id = seed(settings)
    reply = json.dumps(
        {"title": "推理引擎开源", "subtitle": "吞吐提升 2.3 倍", "script": "字" * 400},
        ensure_ascii=False,
    )

    lab = prompt_lab.render(
        article_id, ProductionKind.NARRATION, instructions="多讲局限", settings=settings
    )

    recorder = RecordingProvider(ScriptedProvider("fake", [reply]))
    produced = produce(
        article_id,
        ProductionKind.NARRATION,
        settings=settings,
        llm=recorder,
        instructions="多讲局限",
    )
    assert produced.ok, produced.error

    from dna.llm.capture import render_messages

    via_produce = [render_messages(batch) for batch in recorder.captured]
    assert lab.prompts == via_produce
