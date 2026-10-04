"""opt_gui 分支的界面改动：格子标签、文案编辑、任务登记、批量生成。"""

from __future__ import annotations

import pytest

from dna.store.ledger import ProductionRecord


def _prod(chars: int, *, est: float | None = None, kind: str = "summary", lang: str = "zh"):
    return ProductionRecord(
        id=1, article_id="a", kind=kind, lang=lang, variant=None, instructions=None,
        status="ok", output_path=None, chars=chars, est_seconds=est,
        llm_provider=None, llm_model=None, tokens=0, calls=1, duration_ms=0,
        error=None, created_at=None, redo_of_id=None,
    )


@pytest.mark.parametrize(
    ("record", "kind", "expected"),
    [
        (_prod(279, est=44.0, kind="shortvideo"), "shortvideo", "279字 (约44s)"),
        (_prod(4000, est=900.0, kind="longform"), "longform", "4000字 (约15min)"),
        # 总结台账里没有时长：按字数现算，格子仍是「字数 (约时长)」
        (_prod(90), "summary", "90字 (约"),
    ],
)
def test_cell_label_chars_and_seconds(record, kind, expected) -> None:
    from frontends.nicegui_app.ledger_table.cells import _cell_state

    glyph, value, _ = _cell_state(record, kind)
    assert glyph == "●"
    assert value.startswith(expected)
    assert value.endswith(")")


@pytest.fixture
def offscreen():
    from nicegui.client import Client
    from nicegui.page import page as page_decorator

    client = Client(page_decorator("/test"), request=None)
    with client:
        yield client


def _texts(element) -> list[str]:
    from nicegui import ui

    found = []
    for child in element.descendants():
        if isinstance(child, ui.label):
            found.append(child.text)
    return found


def test_body_is_editable_except_longform(offscreen) -> None:
    """总结 / 中视频类可双击编辑；长视频只读并提示去 TTS 操作台。"""
    from nicegui import ui

    from dna.produce import ProductionKind
    from frontends.nicegui_app.text_editor import LONGFORM_HINT, render_body

    for kind, editable in ((ProductionKind.SUMMARY, True), (ProductionKind.SHORTVIDEO, True),
                           (ProductionKind.LONGFORM, False)):
        container = ui.column()
        with container:
            render_body("a", kind, "zh", "# 标题\n\n正文", on_saved=lambda: None)
        box = container.default_slot.children[0]
        assert ("editable" in box.classes) is editable
        assert (LONGFORM_HINT in _texts(container)) is (not editable)


# --- 任务登记表 / the job registry ------------------------------------------------


def test_job_registry_blocks_duplicates_and_maps_audio_to_its_script_cell() -> None:
    """
    同一格重复点「生成」被挡住（第二次是计费，不是加速）；不同格、不同文章可并行。
    音频没有自己的列：中视频音频在跑时，亮的是中视频那一格。
    """
    from dna.produce import ProductionKind as K
    from frontends.nicegui_app import jobs

    first = jobs.begin("a", K.NARRATION_AUDIO, "zh", "中视频音频")
    try:
        assert first is not None and first.audio
        assert jobs.begin("a", K.NARRATION_AUDIO, "zh", "中视频音频") is None
        other = jobs.begin("b", K.SUMMARY, "zh", "总结")
        assert other is not None and not other.audio

        assert jobs.running_in_cell("a", K.NARRATION) is first
        assert jobs.running_in_cell("a", K.SUMMARY) is None
        assert jobs.running_in_cell("b", K.SUMMARY) is other
        jobs.finish(other)
    finally:
        jobs.finish(first)
    assert jobs.active() == []
    assert jobs.begin("a", K.NARRATION_AUDIO, "zh", "中视频音频") is not None
    jobs.finish(jobs.running_in_cell("a", K.NARRATION))


# --- 批量生成的预算 / batch generation planning -----------------------------------


def test_batch_plan_reuses_existing_and_skips_too_short(settings, monkeypatch, patch_actions_settings) -> None:
    """
    批量是补缺口：已生成（台账有、文件在）的复用、不计费；正文撑不起的剔掉。
    反向：台账说有但文件被删了，要重新生成，不能算成「已有」。
    """
    from dna.core import config as config_module
    from dna.produce import ProductionKind as K
    from dna.produce import spec
    from dna.store.ledger import Ledger
    from frontends.nicegui_app import actions
    from tests.frontends.test_workbench import _seed

    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    patch_actions_settings(settings)
    article_id, directory = _seed(settings)
    ledger = Ledger(settings.db_file)
    ledger.record_production(article_id, "summary", status="ok", lang="zh", chars=90)
    ledger.record_production(article_id, "shortvideo", status="ok", lang="zh", chars=200)
    (directory / spec(K.SUMMARY).filename_for("zh")).write_text("x", encoding="utf-8")

    kinds = [K.SUMMARY, K.SHORTVIDEO, K.NARRATION, K.LONGFORM]
    plan = actions.plan_batch_produce([article_id], kinds)

    assert plan.reused == {K.SUMMARY: 1}
    assert plan.not_applicable == {K.LONGFORM: 1}, "正文 200 字撑不起长视频"
    assert plan.todo == [(article_id, K.SHORTVIDEO), (article_id, K.NARRATION)]
    assert plan.calls == 2


def test_batch_bar_has_a_generation_row(offscreen, monkeypatch) -> None:
    from nicegui import ui

    from frontends.nicegui_app.ledger_table import batch, state

    monkeypatch.setitem(state._SELECTED, "a", None)
    container = ui.row()
    batch.render_batch_bar(container, on_done=lambda: None)
    boxes = [c.text for c in container.descendants() if isinstance(c, ui.checkbox)]
    assert len(boxes) == 4, "四种产物都能批量选"


# --- TTS 操作台：按模式只显示对应配置 / per-mode controls ---------------------------


def test_voice_controls_show_only_the_selected_mode(offscreen, monkeypatch) -> None:
    from dna.tts.base import VoiceSpec
    from frontends.nicegui_app import actions
    from frontends.nicegui_app.voice_controls import (
        MODE_BUILTIN,
        MODE_CLONE,
        MODE_DESIGN,
        VoiceControls,
    )

    # 直接给 `list` 而不是 `lambda: []`：本例只关心「一个都取不到」这个状态
    monkeypatch.setattr(actions, "ref_audio_options", list)
    controls = VoiceControls(voices=["serena"], base=VoiceSpec(speaker="serena"), uploads=False)
    controls.render()

    expected = {
        MODE_BUILTIN: (True, False, "语气指令"),
        MODE_DESIGN: (False, False, "音色描述"),
        MODE_CLONE: (False, True, "语气指令"),
    }
    for mode, (builtin, clone, label) in expected.items():
        controls.mode_box.set_value(mode)
        controls.apply_mode()
        assert controls.builtin_row.visible is builtin, mode
        assert controls.clone_row.visible is clone, mode
        assert controls.instruct_box.visible, "指令框三种模式都用"
        assert controls.instruct_box.props["label"].startswith(label), mode
