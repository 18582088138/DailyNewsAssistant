"""合成与落盘：逐段生成、全部生成、拼接保存 / Synthesis and saving。"""

from __future__ import annotations

import base64

from nicegui import ui

from dna.produce import spec
from dna.tts.base import VoiceSpec
from frontends.nicegui_app import actions
from frontends.nicegui_app.audio_progress import AudioProgress
from frontends.nicegui_app.tts_panel.model import (
    STATE_DONE,
    _Panel,
    _Piece,
)
from frontends.nicegui_app.voice_controls import voice_problem


async def _generate(panel: _Panel, piece: _Piece) -> None:
    """
    生成本段 / Render this one piece.

    只合成这一段，几秒钟就回来；wav 直接以 data URL 塞给播放器——落一个临时文件
    就要管清理。这份字节**同时就是「全部合成并保存」要复用的那份**：
    另存一次的话，两处迟早不是同一段音频。
    The bytes kept here are exactly what the full run reuses.
    """
    text = piece.text()
    if not text:
        ui.notify("这一段是空的", type="warning")
        return
    voice = panel.voice_for(piece)
    problem = voice_problem(voice)
    if problem:
        # 等几十秒再看服务端 422 是纯浪费
        ui.notify(problem, type="warning", multi_line=True, close_button=True)
        return
    note = ui.notification(f"生成第 {piece.number} 段…", spinner=True, timeout=None)
    try:
        wav = await actions.preview_voice(text, voice, role=piece.role)
    except Exception as exc:
        ui.notify(f"第 {piece.number} 段生成失败：{exc}", type="negative", timeout=12000,
                  multi_line=True, close_button=True)
        return
    finally:
        note.dismiss()

    _adopt(panel, piece, wav, text, voice)
    panel.refresh()


def _adopt(panel: _Panel, piece: _Piece, wav: bytes, text: str, voice: VoiceSpec) -> None:
    """
    收下这一段的波形并就地能播 / Keep the bytes and make them playable in place.

    wav 以 data URL 交给播放器：落一个临时文件就要管清理，而这份字节
    **同时就是「合成并保存」要复用的那份**——另存一次的话两处迟早不是同一段音频。
    """
    piece.wav = wav
    piece.rendered_text = text
    piece.rendered_voice = voice
    encoded = base64.b64encode(wav).decode("ascii")
    piece.player.set_source(f"data:audio/wav;base64,{encoded}")   # type: ignore[union-attr]
    piece.player.set_visibility(True)                             # type: ignore[union-attr]


async def _generate_all(panel: _Panel) -> None:
    """
    逐段生成还没生成好的段 / Render every piece that is not ready yet.

    只跑 `○` 与 `✎`：`✔` 的段已经有波形，重跑一遍只是花时间，而且**换出来的
    未必是同一版**（种子不同就是另一个念法）。跑完全都变 `✔`，
    底栏那个按钮就只剩拼接。
    Only the stale and never-rendered pieces run; re-rendering a ready piece would cost
    minutes and could come back sounding different.

    进度用一条会更新的通知，不是每段弹一个：十几段各弹一次会把屏幕糊满。
    """
    todo = [
        piece for piece in panel.pieces
        if piece.text() and panel.state(piece) != STATE_DONE
    ]
    if not todo:
        ui.notify("每一段都已生成（✔），不用再跑一遍", type="info")
        return

    problem = next(
        (p for p in (voice_problem(panel.voice_for(piece)) for piece in todo) if p), None
    )
    if problem:
        ui.notify(problem, type="warning", multi_line=True, close_button=True)
        return

    note = ui.notification(f"逐段生成 0/{len(todo)}…", spinner=True, timeout=None)
    done = 0
    failed: list[tuple[int, str]] = []
    try:
        for piece in todo:
            note.message = f"逐段生成 {done + 1}/{len(todo)}（第 {piece.number} 段）…"
            text = piece.text()
            voice = panel.voice_for(piece)
            try:
                wav = await actions.preview_voice(text, voice, role=piece.role)
            except Exception as exc:
                failed.append((piece.number, str(exc)))
                continue
            _adopt(panel, piece, wav, text, voice)
            done += 1
    finally:
        note.dismiss()
        panel.refresh()

    if failed:
        detail = "；".join(f"第 {number} 段：{reason}" for number, reason in failed[:3])
        ui.notify(f"{done} 段已生成，{len(failed)} 段失败——{detail}",
                  type="warning", timeout=15000, multi_line=True, close_button=True)
    else:
        ui.notify(f"{done} 段已生成，全部段现在都是 ✔——底栏那个按钮只做拼接",
                  type="positive", multi_line=True)


async def _reroll(panel: _Panel, piece: _Piece) -> None:
    """换个种子再念一遍这一段 / Re-roll the seed and render again."""
    piece.reroll += 1
    await _generate(panel, piece)


# --- 落盘 / saving -------------------------------------------------------------


def _synthesise(panel: _Panel, dialog, on_change) -> None:
    """
    整篇合成并落盘 / Synthesise everything and save it.

    顺序 / The order matters:
        1. 文本改过 ⇒ **先写回稿子**（`calls=0` 的「人工校对」行）
        2. 再走 `run_production(..., segments=..., rendered=...)`
        反过来的话，音频那一行的前置稿子还是 LLM 那一版。
    """
    segments, rendered = panel.plan()
    if not segments:
        ui.notify("没有可合成的文本", type="warning")
        return
    for segment in segments:
        problem = voice_problem(segment.voice)
        if problem:
            # 整篇要跑几分钟，缺个必填项就等到最后一段才报是最贵的失败方式
            ui.notify(problem, type="warning", multi_line=True, close_button=True)
            return

    label = spec(panel.kind).label
    edited = panel.script_text() != panel.loaded_text
    progress = AudioProgress(
        label,
        expected_seconds=actions.audio_estimate_seconds(
            panel.row.article, panel.kind, panel.lang
        ),
        hint=f"复用 {len(rendered)} 段，本次合成 {len(segments) - len(rendered)} 段"
        if rendered else "音色按操作台里选的那套",
    )

    async def _go() -> None:
        try:
            if edited and panel.editable:
                try:
                    await actions.save_script(
                        panel.row.article.id, panel.kind, panel.script_text(),
                        lang=panel.lang,
                    )
                except Exception as exc:
                    # 稿子没写回就不该继续：那样音频念的是新文本、台账指的是旧稿子。
                    # 浮窗由 finally 关掉，这里关会关两次。
                    ui.notify(f"稿子写回失败，已停下：{exc}", type="negative",
                              timeout=12000, multi_line=True, close_button=True)
                    return
            result = await actions.run_production(
                panel.row.article.id, panel.kind, lang=panel.lang, force=True,
                progress=progress.progress, segments=segments, rendered=rendered,
            )
        finally:
            progress.close()

        dialog.close()
        if not result.ok:
            ui.notify(f"{label} 合成失败：{result.error}", type="negative",
                      timeout=12000, multi_line=True, close_button=True)
        else:
            warning = "" if result.within_target else "，⚠️ 部分段落合成失败，音频不完整"
            saved = "，稿子已按人工校对写回" if edited and panel.editable else ""
            # 字幕按这次的分段出，一段一条——说出来，否则人以为没出
            subs = f"，字幕 {len(result.cues)} 条（.srt 在音频旁边）" if result.cues else ""
            ui.notify(
                f"{label} 完成：{result.seconds or 0:.0f} 秒音频"
                f"（{result.chars} 字）{subs}{saved}{warning}",
                type="positive" if result.within_target else "warning",
            )
        if on_change is not None:
            on_change()

    ui.timer(0.01, _go, once=True)
