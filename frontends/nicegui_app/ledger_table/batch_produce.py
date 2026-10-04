"""
批量生成：选几种产物 → 先算后跑 → 串行逐条执行 / Batch generation from the batch bar。

**串行，不是 N×4 个并发**：一次把几十个 LLM 请求同时打出去只会撞限流，
而且失败了不好说清是哪一条。逐条跑、逐条登记，格子按顺序亮起，
批量之外的单格任务照样可以并行。
"""

from __future__ import annotations

from nicegui import ui

from dna.produce import DISPLAY_ORDER, ProductionKind, spec
from dna.produce.tasks import default_language
from frontends.nicegui_app import actions, jobs
from frontends.nicegui_app.audio_progress import AudioProgress

# 勾选状态跨重画保留（批量条每勾一篇文章都会重画）；长视频默认不勾：一篇 5~9 次调用
_PICKED: set[ProductionKind] = {
    ProductionKind.SUMMARY, ProductionKind.SHORTVIDEO, ProductionKind.NARRATION,
}


def render_produce_row(ids: list[str], *, on_done) -> None:
    """批量条第二行：产物多选 + 生成按钮 / The second row of the batch bar。"""
    with ui.row().classes("items-center gap-1 no-wrap"):
        ui.label("批量生成").classes("text-xs").style("color: var(--wb-dim)")
        for kind in DISPLAY_ORDER:
            box = ui.checkbox(spec(kind).label, value=kind in _PICKED).props("dense")
            box.on_value_change(lambda e, k=kind: _PICKED.add(k) if e.value else _PICKED.discard(k))
        ui.button(
            "生成", icon="auto_awesome", on_click=lambda: _ask(list(ids), on_done=on_done)
        ).props("flat dense no-caps").classes("wb-btn-cost").tooltip(
            "已生成的直接复用，只补缺口；确认框里先报调用次数"
        )


def _ask(ids: list[str], *, on_done) -> None:
    kinds = [k for k in DISPLAY_ORDER if k in _PICKED]
    if not kinds:
        ui.notify("先勾选要生成的产物", type="warning")
        return
    plan = actions.plan_batch_produce(ids, kinds)

    with ui.dialog() as dialog, ui.card().classes("w-[30rem] wb-dialog").style(
        "background: var(--wb-panel); border: 1px solid var(--wb-line-strong)"
    ):
        ui.label(f"批量生成 · {len(ids)} 篇").classes("text-lg font-medium").style(
            "color: var(--wb-accent)"
        )
        for kind in kinds:
            new = sum(1 for _, k in plan.todo if k == kind)
            parts = [f"新生成 {new} 篇"]
            if plan.reused.get(kind):
                parts.append(f"已有 {plan.reused[kind]} 篇复用")
            if plan.not_applicable.get(kind):
                parts.append(f"{plan.not_applicable[kind]} 篇正文太短跳过")
            ui.label(f"{spec(kind).label}：{'　·　'.join(parts)}").classes("wb-path")

        mode = None
        if any(k == ProductionKind.LONGFORM for _, k in plan.todo):
            mode = ui.radio(
                {"feature": "专题（单角色讲述）", "interview": "访谈（主持人 + 嘉宾）"},
                value="feature",
            ).props("inline dense")
            ui.label("⚠️ 长视频一篇 5~9 次 LLM 调用，是其余三种加起来的数倍").classes(
                "text-xs"
            ).style("color: var(--wb-warn)")

        ui.label(f"预估 {plan.calls} 次 LLM 调用，逐条串行执行").classes("text-sm")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("取消", on_click=dialog.close).props("flat no-caps")
            start = ui.button(
                "确认生成",
                on_click=lambda: (
                    dialog.close(),
                    _start(plan.todo, variant=mode.value if mode else None, on_done=on_done),
                ),
            ).props("no-caps").classes("wb-btn-cost")
            if not plan.todo:
                start.disable()
    dialog.open()


def _start(todo: list[tuple[str, ProductionKind]], *, variant, on_done) -> None:
    lang = default_language()
    total = len(todo)

    async def _go() -> None:
        ok = failed = busy = 0
        try:
            for index, (article_id, kind) in enumerate(todo, start=1):
                label = spec(kind).label
                card.progress.update(done=index - 1, total=total)
                job = jobs.begin(article_id, kind, lang, label)
                if job is None:
                    # 同一格单独点过、正在跑：不重复计费
                    busy += 1
                    continue
                on_done()
                try:
                    result = await actions.run_production(
                        article_id, kind, lang=lang, force=False,
                        variant=variant if kind == ProductionKind.LONGFORM else None,
                    )
                finally:
                    jobs.finish(job)
                if result.ok:
                    ok += 1
                else:
                    failed += 1
            card.progress.update(done=total, total=total)
        finally:
            card.close()
        summary = f"批量生成完成：成功 {ok}，失败 {failed}"
        if busy:
            summary += f"，{busy} 条已在执行中未重复提交"
        ui.notify(summary, type="positive" if not failed else "warning")
        on_done()

    with jobs.anchor():
        card = AudioProgress("批量", action="生成中", detail=f"共 {total} 条，逐条执行", unit="条",
                             hint="点别处不影响，任务在后台继续")
        ui.timer(0.01, _go, once=True)


__all__ = ["render_produce_row"]
