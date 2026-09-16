"""画面：整块布局与每一段的控件 / The layout and per-piece widgets。"""

from __future__ import annotations

from nicegui import ui

from dna.tts.base import SpeechSegment
from dna.tts.segment import DEFAULT_MAX_SEGMENT_CHARS
from frontends.nicegui_app import actions
from frontends.nicegui_app.tts_panel.edit import _insert, _remove
from frontends.nicegui_app.tts_panel.model import (
    EVENT_MARKERS,
    STATE_HINTS,
    STATE_NEW,
    _Panel,
    _Piece,
)
from frontends.nicegui_app.tts_panel.synth import (
    _generate,
    _generate_all,
    _reroll,
    _synthesise,
)
from frontends.nicegui_app.voice_controls import VoiceControls

# --- 画面 / the layout ---------------------------------------------------------


def _render(
    panel: _Panel,
    segments: list[SpeechSegment],
    body,
    footer,
    dialog,
    *,
    on_change,
) -> None:
    """声音配置 + 整段工具条 + 分段列表 + 底栏。"""
    panel.loaded_text = ("" if panel.lang == "zh" else " ").join(s.text for s in segments)

    body.clear()
    with body:
        panel.shared = VoiceControls(
            voices=panel.voices, base=segments[0].voice, on_change=lambda: _refresh(panel)
        )
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            panel.uniform_box = ui.checkbox(
                "统一声音配置", value=True, on_change=lambda _e: _apply_uniform(panel)
            ).tooltip("取消勾选后，每段可以在「单独配置」里各自选模式与音色")
            with ui.column().classes("flex-grow gap-1"):
                panel.shared.render()

        if not panel.voices:
            ui.label(
                "服务离线，音色表取不到——音色框按名字手填（打错要等合成跑完才知道）"
            ).classes("wb-path").style("color: var(--wb-danger)")
        if not panel.editable:
            ui.label(
                "长文案的稿子按发言人分轮保存，这里改的文本**不会写回稿子**"
                "（音频仍按改后的文本合成）"
            ).classes("wb-path").style("color: var(--wb-warn, var(--wb-danger))")

        ui.separator().style("background: var(--wb-line)")

        # 整篇文案 / the whole script
        #
        # **先有整篇，再有分段。** 只给分段列表的话，改一句话要在几个小框里来回找，
        # 而人手上的原始素材本来就是一整篇文案。这个框是拆分的输入，
        # 分段列表才是真正拿去合成的东西。
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.label("整篇文案").classes("wb-path")
            panel.max_chars_box = (
                ui.number(label="每段字数上限", value=DEFAULT_MAX_SEGMENT_CHARS,
                          min=20, max=600, step=10)
                .props("outlined dense").classes("w-[140px]")
                .tooltip(f"默认 {DEFAULT_MAX_SEGMENT_CHARS} 字，与自动合成一致；"
                         "服务端还会按模型上限再切一刀")
            )
            ui.button("按此文案拆分", icon="content_cut",
                      on_click=lambda: _split_script(panel)).props(
                "dense no-caps size=sm outline"
            ).tooltip("用上面这个框里的文案重新拆成分段；文本没变的段保留已生成的音频")
            ui.button("从分段回填", icon="vertical_align_top",
                      on_click=lambda: _fill_script(panel)).props(
                "flat dense no-caps size=sm"
            ).tooltip("把下面各段拼起来写回这个框（在分段里改完、想整篇再读一遍时用）")
        panel.script_box = (
            ui.textarea(value=panel.loaded_text)
            .props("outlined dense autogrow")
            .classes("w-full")
            .style("font-size: 12.5px; max-height: 22vh; overflow-y: auto")
        )

        ui.separator().style("background: var(--wb-line)")

        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.label("分段").classes("wb-path")
            ui.button("重新拆条", icon="content_cut",
                      on_click=lambda: _resplit(panel)).props(
                "flat dense no-caps size=sm"
            ).tooltip(
                "把所有段拼起来按自动合成的切法重切；文本没变的段保留已生成的音频"
            )
            ui.button("加一段", icon="add",
                      on_click=lambda: _add_piece(panel)).props(
                "flat dense no-caps size=sm"
            )
            ui.button("清空", icon="clear_all",
                      on_click=lambda: _clear(panel)).props(
                "flat dense no-caps size=sm"
            ).tooltip("清掉所有段（稿子文件不动，没点「全部合成并保存」就什么都没改）")
            ui.space()
            ui.button("全部生成", icon="playlist_play",
                      on_click=lambda: _generate_all(panel)).props(
                "dense no-caps size=sm outline"
            ).tooltip(
                "逐段生成还没生成过（○）和改过字（✎）的段，已生成的（✔）跳过。"
                "全部变成 ✔ 之后，下面那个按钮就只是拼接，不再重新合成"
            )

        panel.list_box = ui.column().classes("w-full gap-1")
        with panel.list_box:
            for segment in segments:
                _add_piece(panel, segment=segment, refresh=False)

    _apply_uniform(panel)

    footer.clear()
    with footer:
        panel.footer_label = ui.label("").classes("wb-path")
        with ui.row().classes("items-center gap-2"):
            ui.button("关闭", on_click=dialog.close).props("flat no-caps")
            panel.footer_button = ui.button(
                "全部合成并保存到这篇文章",
                icon="graphic_eq",
                on_click=lambda: _synthesise(panel, dialog, on_change),
            ).props("no-caps").style("color: var(--wb-accent)").tooltip(
                "走的是和自动合成完全同一条落盘路径（台账、时长、字幕）；已有音频会被覆盖。"
                "全部段都是 ✔ 时它不重新合成，只拼接"
            )
    _refresh(panel)


def _add_piece(
    panel: _Panel,
    *,
    segment: SpeechSegment | None = None,
    refresh: bool = True,
) -> None:
    """
    追加一段 / Append one piece.

    新段沿用第一段的音色与角色：空的 `VoiceSpec` 会把语言与参考原话都丢掉，
    而人加一段的意思从来不是「这一段用另一把嗓子」。
    """
    template = segment.voice if segment is not None else (
        panel.pieces[0].base if panel.pieces else panel.template
    )
    if template is None:
        return
    panel.template = panel.template or template
    piece = _Piece(
        base=template,
        role=segment.role if segment is not None else "narrator",
        pause_ms=segment.pause_ms if segment is not None else None,
    )
    panel.pieces.append(piece)
    with panel.list_box:
        _render_piece(panel, piece, text=segment.text if segment is not None else "")
    if refresh:
        _apply_uniform(panel)
        _refresh(panel)


def _render_piece(panel: _Panel, piece: _Piece, *, text: str) -> None:
    """一段：状态 + 可编辑正文 + 字数 + 生成/重掷/删除 + 单独配置。"""
    with ui.column().classes("w-full gap-0").style(
        "border-top: 1px solid var(--wb-line)"
    ) as container:
        piece.container = container
        with ui.row().classes("w-full items-start gap-2 no-wrap"):
            with ui.column().classes("items-center gap-0").style("width: 34px"):
                piece.number_label = ui.label("").classes("wb-path")
                piece.icon = ui.label(STATE_NEW).classes("wb-path")
            piece.text_box = (
                ui.textarea(value=text, on_change=lambda _e: _refresh(panel))
                .props("outlined dense autogrow")
                .classes("flex-grow")
                .style("font-size: 12.5px")
            )
            with ui.column().classes("items-end gap-0"):
                piece.counter = ui.label("").classes("wb-path")
                with ui.row().classes("items-center gap-0 no-wrap"):
                    ui.button(icon="play_arrow",
                              on_click=lambda p=piece: _generate(panel, p)).props(
                        "flat dense round size=sm"
                    ).tooltip("生成本段（免费，不写台账）——这份音频会被「全部合成」复用")
                    ui.button(icon="casino",
                              on_click=lambda p=piece: _reroll(panel, p)).props(
                        "flat dense round size=sm"
                    ).tooltip(
                        "换种子重掷：文本和音色都对、只是这一版念得不好听时用它"
                    )
                    ui.button(icon="close",
                              on_click=lambda p=piece: _remove(panel, p)).props(
                        "flat dense round size=sm"
                    ).tooltip("删掉这一段")

        with ui.row().classes("w-full items-center gap-1 no-wrap").style(
            "padding-left: 34px"
        ):
            for label, marker, hint in EVENT_MARKERS:
                ui.button(label, on_click=lambda m=marker, p=piece: _insert(panel, p, m)) \
                    .props("flat dense size=sm").classes("text-xs").tooltip(
                        f"{hint}（插在这一段末尾；要插在中间就直接在文本里手打）"
                    )
            if piece.role and piece.role != "narrator":
                ui.label(piece.role).classes("wb-path")
            piece.player = ui.audio("").props("controls").classes("w-[240px]")
            piece.player.set_visibility(False)

        piece.own_box = ui.expansion("单独配置", icon="tune").props("dense").classes(
            "w-full"
        ).style("padding-left: 34px")
        with piece.own_box:
            piece.own = VoiceControls(
                voices=panel.voices, base=piece.base, uploads=False,
                on_change=lambda: _refresh(panel),
            )
            piece.own.render()


def _apply_uniform(panel: _Panel) -> None:
    """
    统一配置时收起并灰掉每段的单独配置 / Uniform mode greys the per-piece controls.

    灰掉而不是隐藏：隐藏会让人以为「这个面板没有单独配置」。
    """
    uniform = panel.uniform
    for piece in panel.pieces:
        if piece.own_box is None:
            continue
        piece.own_box.set_visibility(not uniform)
        if uniform:
            piece.own_box.close()
    _refresh(panel)


def _refresh(panel: _Panel) -> None:
    """重算序号、字数、三态与底栏 / Recompute the numbers, counts, states and footer."""
    for number, piece in enumerate(panel.pieces, start=1):
        piece.number = number
        if piece.number_label is not None:
            piece.number_label.set_text(f"{number}")
        text = piece.text()
        if piece.counter is not None:
            piece.counter.set_text(f"{len(text)} 字")
            # 超长不是错误：服务端 `segment: true` 会自己再切一刀。
            # 但要说出来——否则人以为这一段就是一刀，试听听到的却是两句。
            over = len(text) > DEFAULT_MAX_SEGMENT_CHARS
            piece.counter.style(
                f"color: {'var(--wb-danger)' if over else 'inherit'}"
            )
            piece.counter.tooltip(
                f"超过 {DEFAULT_MAX_SEGMENT_CHARS} 字，服务端会再切一刀" if over else ""
            )
        state = panel.state(piece)
        if piece.icon is not None:
            piece.icon.set_text(state)
            piece.icon.tooltip(STATE_HINTS[state])

    segments, rendered = panel.plan()
    todo = len(segments) - len(rendered)
    if panel.footer_label is not None:
        reuse = f"（本次只需合成 {todo} 段，复用 {len(rendered)} 段）" if rendered else ""
        panel.footer_label.set_text(
            f"共 {len(segments)} 段 · {len(rendered)} 段已生成{reuse} · 生成本段不写台账"
        )
    if panel.footer_button is not None:
        # 全是 ✔ 时那个按钮只做拼接（后端一段都不会重发），按钮上要说出来——
        # 否则人以为点下去又是几分钟，于是不敢点。
        panel.footer_button.set_text(
            "拼接并保存到这篇文章（无需重新合成）" if segments and not todo
            else "全部合成并保存到这篇文章"
        )

def _rebuild(panel: _Panel, source: str) -> None:
    """
    用一段文本重建分段列表 / Rebuild the piece list from one blob of text.

    **文本没变的段保留已生成的音频**：只在中间加了一个断点时，其余段没有任何
    理由重合成一遍（一段几十秒）。按文本原样匹配，不做模糊比较——
    模糊匹配一旦认错，人会拿到一段听着差不多但不是这句话的音频。
    Pieces whose text is byte-identical keep their audio; matching is exact on purpose.
    """
    if not source:
        ui.notify("没有可拆分的文案", type="warning")
        return
    cache = {
        piece.rendered_text: (piece.wav, piece.rendered_voice)
        for piece in panel.pieces
        if piece.wav is not None and piece.rendered_text
    }
    template = panel.pieces[0].base if panel.pieces else panel.template
    role = panel.pieces[0].role if panel.pieces else "narrator"
    if template is None:
        return
    panel.template = panel.template or template

    texts = actions.resplit_text(source, max_chars=panel.max_chars)
    panel.pieces.clear()
    panel.list_box.clear()      # type: ignore[union-attr]
    with panel.list_box:
        for text in texts:
            piece = _Piece(base=template, role=role)
            panel.pieces.append(piece)
            _render_piece(panel, piece, text=text)
            kept = cache.get(text)
            if kept is not None:
                piece.wav, piece.rendered_voice = kept
                piece.rendered_text = text
    _apply_uniform(panel)
    kept_count = sum(1 for piece in panel.pieces if piece.wav is not None)
    ui.notify(f"拆成 {len(texts)} 段（{kept_count} 段沿用已生成的音频）", type="info")

def _resplit(panel: _Panel) -> None:
    """
    按自动合成的切法重新拆条 / Re-split the way automatic synthesis would.

    输入是**下面各段拼起来的文本**（不是上面那个整篇框）：在分段里改完字、
    只想换个断点时用这个。
    """
    _rebuild(panel, panel.script_text())

def _split_script(panel: _Panel) -> None:
    """
    按上面那个「整篇文案」框拆分 / Split the whole-script box into pieces.

    这是操作台的主路径：人手上的素材是一整篇文案，先粘进来、再按字数上限切。
    """
    _rebuild(panel, str(getattr(panel.script_box, "value", "") or "").strip())

def _fill_script(panel: _Panel) -> None:
    """把各段拼回整篇框 / Join the pieces back into the whole-script box。"""
    if panel.script_box is not None:
        panel.script_box.set_value(panel.script_text())   # type: ignore[union-attr]

def _clear(panel: _Panel) -> None:
    """
    清掉所有段 / Drop every piece.

    音色模板留在 `panel.template` 上：清空之后「加一段」还得有个模板，
    否则新段的语言与参考原话全丢，表现是「加一段」点了没反应。
    """
    panel.pieces.clear()
    panel.list_box.clear()      # type: ignore[union-attr]
    panel.refresh()
