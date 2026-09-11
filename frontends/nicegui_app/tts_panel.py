"""
主界面里的 TTS 操作台 / The native TTS console.

**为什么它必须在主界面里** / Why this is not a jump-out:
    从前这一格只能「跳到 TTS 图形界面」——那是另一个进程、另一个端口、另一套
    交接单轮询。稿子在这边、音色在那边，产物还要靠轮询收回来，
    任何一环断了都表现成「点了没反应」。而后端其实早就够了：
    `RemoteTTSProvider.synthesize()` 本来就是逐段合成，每段一个独立的 `VoiceSpec`。
    跳转存在的唯一原因是没人在这边画这块面板。
    The backend already synthesises piece by piece with a per-piece voice; the hand-off
    existed only because nobody had drawn this panel.

两条铁律 / Two rules this module obeys:
    1. **分段来自 `speech_segments_for()`**（经 `actions.speech_segments`），
       与自动合成同一份 `_build_segments`。面板里看到的就是真会合成的那批。
    2. **合成走 `run_production(..., segments=...)`**，与自动合成同一条落盘路径
       （写台账、算真实时长、出字幕）。另开一条捷径的话，两条路的产物迟早不一致。

正文可编辑，改完写回稿子 / The text is editable and written back:
    操作台是常用功能——调文本、校对生成内容都在这里做（2026-09-10 用户要求）。
    改过的文本在「全部合成并保存」时**先写回稿子文件**，并在台账里插一行
    `calls=0` 的「人工校对」，然后才写音频那一行。顺序反了的话，音频那一格的
    前置稿子还是 LLM 那一版，「这一版到底念了什么」就没有答案了。
    Editing is the point of this panel; the edited text is written back to the script
    file before the audio row is recorded, so the ledger keeps one single answer to
    "what did this version actually say".

三态与复用 / The three states and the reuse:
    `✔ / ✎ / ○` = 已生成 / 生成后改过（缓存作废）/ 没生成过。
    「全部合成并保存」只把 `✔` 的那些段的波形交给后端复用（`rendered=`），
    改过字或换过声音配置的段重新合成。RTF≈2.5，整篇重跑要几分钟，
    不复用等于逐段校对白做。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from nicegui import ui

from dna.produce import ProductionKind, spec
from dna.produce.tasks import DEFAULT_LANGUAGE
from dna.tts.base import SpeechSegment, VoiceSpec
from dna.tts.segment import DEFAULT_MAX_SEGMENT_CHARS
from frontends.nicegui_app import actions, theme
from frontends.nicegui_app.actions import RowView
from frontends.nicegui_app.audio_progress import AudioProgress
from frontends.nicegui_app.voice_controls import (
    VoiceControls,
    reroll_seed,
    voice_problem,
)

STATE_DONE = "✔"
STATE_STALE = "✎"
STATE_NEW = "○"
STATE_HINTS = {
    STATE_DONE: "已生成，「全部合成并保存」会直接复用这一段",
    STATE_STALE: "生成之后改过——这一段会重新合成",
    STATE_NEW: "还没生成过",
}

EVENT_MARKERS = (
    ("[pause]", "[pause:400ms]", "切段并插入确定长度的静音——与引擎无关，一定生效"),
    ("[laugh]", "[laugh]", "带着笑意说；服务端不支持原生事件时会转成语气提示"),
    ("[sigh]", "[sigh]", "先叹一口气，语气低落"),
    ("[breath]", "[breath]", "开口前有一次明显的吸气"),
)
"""能插的事件标记 / the event markers this panel offers.

服务端 `events: true` 是默认值，这些标记**今天就已经被解析**。完整的事件表在
服务端（`text/events.py`），这里只放最常用的几个——跨项目引用源码是禁止的，
而一份会过期的完整拷贝比一份短清单更糟。
"""


@dataclass
class _Piece:
    """
    面板里的一段 / One piece in the panel.

    `base` 是 `speech_segments_for()` 给的原始音色：里面带着这一段的语言、
    参考音频与**参考原话**。控件只在它之上做修改（见 `voice_controls.voice_from`），
    不另起一个空的 `VoiceSpec`——那样会把原话丢掉，克隆质量明显变差。
    """

    base: VoiceSpec
    role: str
    container: object = None
    number_label: object = None
    text_box: object = None
    counter: object = None
    icon: object = None
    player: object = None
    own: VoiceControls | None = None
    own_box: object = None

    wav: bytes | None = None
    rendered_text: str = ""
    rendered_voice: VoiceSpec | None = None
    reroll: int = 0
    number: int = 0
    pause_ms: int | None = None

    def text(self) -> str:
        return str(getattr(self.text_box, "value", "") or "").strip()


class _Panel:
    """
    一次打开的操作台 / One open console.

    做成对象是因为「统一/单独配置」「每段的缓存」「底栏的复用统计」三者互相牵制：
    换一次音色要让一批段的缓存作废，而底栏那句「本次只需合成 N 段」必须跟着变。
    """

    def __init__(self, row: RowView, kind: ProductionKind, lang: str) -> None:
        self.row = row
        self.kind = kind
        self.lang = lang
        self.voices: list[str] = []
        self.pieces: list[_Piece] = []
        self.shared: VoiceControls | None = None
        self.uniform_box: object | None = None
        self.list_box: object | None = None
        self.footer_label: object | None = None
        self.footer_button: object | None = None
        self.script_box: object | None = None
        """顶部那个装整篇文案的框——拆分的输入，不是合成的输入。"""
        self.max_chars_box: object | None = None
        self.loaded_text = ""
        """载入时的稿子正文，用来判断人到底改没改过——没改就不必写回台账。"""
        self.editable = True
        """稿子能不能写回（长文案按发言人分轮存，一段纯文本写不回去）。"""
        self.template: VoiceSpec | None = None
        """载入时第一段的音色，「清空」之后「加一段」还要靠它当模板。"""

    # ---------------------------------------------------------- 取值 / values

    @property
    def uniform(self) -> bool:
        return bool(getattr(self.uniform_box, "value", True))

    @property
    def max_chars(self) -> int:
        """拆分用的字数上限；框被清空时退回自动合成那个默认值。"""
        raw = getattr(self.max_chars_box, "value", None)
        try:
            return int(raw) if raw else DEFAULT_MAX_SEGMENT_CHARS
        except (TypeError, ValueError):
            return DEFAULT_MAX_SEGMENT_CHARS

    def voice_for(self, piece: _Piece) -> VoiceSpec:
        """这一段最终用哪个音色 / The voice this piece will actually use."""
        controls = self.shared if self.uniform or piece.own is None else piece.own
        if controls is None:
            return piece.base
        return controls.spec(piece.base, seed=reroll_seed(piece.number, piece.reroll))

    def state(self, piece: _Piece) -> str:
        """
        这一段现在是什么状态 / Which of the three states this piece is in.

        **文本或声音配置一变，缓存就作废**——不作废的话，「全部合成并保存」会
        拿旧波形去拼新文本，而落盘的音频与稿子对不上这件事，人只有听完才会发现。
        """
        if piece.wav is None:
            return STATE_NEW
        if piece.rendered_text != piece.text():
            return STATE_STALE
        if piece.rendered_voice != self.voice_for(piece):
            return STATE_STALE
        return STATE_DONE

    def plan(self) -> tuple[list[SpeechSegment], dict[int, bytes]]:
        """
        这次要合成什么 / What this run will synthesise：分段 + 可复用的波形。

        空段直接丢掉，**并且 `rendered` 的下标按丢掉之后的位置算**——
        后端也是先滤空段再逐段编号（见 `tts/service.py`），两边错一位的表现是
        「某一段的音频跑到了别的段上」，听起来像整篇乱序。
        Blank pieces are dropped and the reuse indices follow the surviving order, which
        is what the backend numbers as well.
        """
        segments: list[SpeechSegment] = []
        rendered: dict[int, bytes] = {}
        for piece in self.pieces:
            text = piece.text()
            if not text:
                continue
            voice = self.voice_for(piece)
            index = len(segments)
            segments.append(
                SpeechSegment(text=text, voice=voice, role=piece.role,
                              pause_ms=piece.pause_ms)
            )
            if self.state(piece) == STATE_DONE and piece.wav is not None:
                rendered[index] = piece.wav
        return segments, rendered

    def script_text(self) -> str:
        """面板里的文本拼回一篇稿子 / Join the pieces back into one script."""
        joiner = "" if self.lang == "zh" else " "
        return joiner.join(piece.text() for piece in self.pieces if piece.text())


def open_panel(
    row: RowView,
    kind: ProductionKind,
    *,
    lang: str = DEFAULT_LANGUAGE,
    on_change=None,
) -> None:
    """
    打开 TTS 操作台 / Open the console for one audio cell.

    参数 / Args:
        kind:      音频产物（`*_audio`），不是稿子
        on_change: 合成落盘后刷新表格

    **这是唯一的 TTS 界面**：内置音色 / 音色设计 / 克隆三种模式、逐段校对、
    逐段生成、换种子、插事件、上传参考音频都在这里，不再往外跳。
    从前右上角那个「打开完整 TTS 界面」按钮已经删掉——它拉起的是 TTS 模块自带的
    另一个 NiceGUI 进程，那个进程一关窗就整体退出（`forrtl: error (200)`），
    而且它写盘的位置不受 DNA 台账管。同一台机器上两个界面各管一半，是这轮返工的根因。
    This is now the only TTS surface; the hand-off to the upstream GUI process is gone.

    **打开这个面板不会开始合成**（用户实测踩过的就是这个），但会做一次
    朗读友好化——`TTS_PREPROCESS=true` 时那是一次很便宜的 LLM 调用。
    """
    panel = _Panel(row, kind, lang)
    script_kind = spec(kind).audio_of
    script_label = spec(script_kind).label if script_kind else spec(kind).label
    panel.editable = actions.script_is_editable(kind, lang=lang)

    with ui.dialog() as dialog, ui.card().classes("w-[1080px] wb-dialog").style(
        "background: var(--wb-panel); border: 1px solid var(--wb-line-strong)"
    ):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label(f"{script_label} · TTS 操作台").classes("text-lg font-medium").style(
                "color: var(--wb-accent)"
            )
        ui.label(theme.short_title(row.article.title, 70)).classes("wb-path")

        status_row = ui.row().classes("items-center gap-2 no-wrap")
        body = ui.column().classes("w-full gap-1")
        footer = ui.row().classes("w-full items-center justify-between no-wrap")

    async def _refresh_status() -> None:
        status = await actions.tts_status()
        status_row.clear()
        with status_row:
            colour = "var(--wb-accent)" if status.online else "var(--wb-danger)"
            ui.label(("● " if status.online else "○ ") + status.detail).classes(
                "wb-path"
            ).style(f"color: {colour}")
            if not status.online:
                ui.button("启动服务", icon="play_arrow", on_click=_start).props(
                    "flat dense no-caps size=sm"
                )

    async def _start() -> None:
        note = ui.notification("正在启动 TTS 服务（模型冷启动要等一会）…",
                               spinner=True, timeout=None)
        try:
            await actions.tts_start()
        except Exception as exc:  # noqa: BLE001 - 原因原样显示，那句话里写了怎么排查
            ui.notify(f"启动失败：{exc}", type="negative", timeout=12000,
                      multi_line=True, close_button=True)
        finally:
            note.dismiss()
        await _refresh_status()

    async def _load() -> None:
        with body:
            spinner = ui.row().classes("items-center gap-2")
            with spinner:
                ui.spinner(size="sm")
                ui.label("正在切分稿子（会做一次朗读友好化）…").classes("wb-path")
        await _refresh_status()
        try:
            segments = await actions.speech_segments(row.article.id, kind, lang=lang)
            panel.voices = await actions.tts_voices()
        except Exception as exc:  # noqa: BLE001
            spinner.delete()
            with body:
                ui.label(f"取不到分段：{exc}").style("color: var(--wb-danger)")
            return
        spinner.delete()
        if not segments:
            with body:
                ui.label("这一格还没有可朗读的稿子——先生成稿子").style(
                    "color: var(--wb-danger)"
                )
            return
        _render(panel, segments, body, footer, dialog, on_change=on_change)

    dialog.open()
    ui.timer(0.01, _load, once=True)


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


# --- 每段的动作 / the per-piece actions ----------------------------------------


def _insert(panel: _Panel, piece: _Piece, marker: str) -> None:
    """
    往这一段末尾插一个事件标记 / Append one event marker to this piece.

    和 TTS 图形界面同一个做法（那边也是追加到末尾）：光标位置在浏览器里，
    为了插在中间去写一段 JS 取 `selectionStart`，坏起来是「点了没反应」——
    而文本框本来就能手打，这条路更值得信。
    """
    box = piece.text_box
    box.set_value(f"{box.value or ''}{marker}")   # type: ignore[union-attr]
    _refresh(panel)


def _remove(panel: _Panel, piece: _Piece) -> None:
    """删掉一段 / Drop one piece（连它已生成的波形一起）。"""
    if piece.container is not None:
        piece.container.delete()
    if piece in panel.pieces:
        panel.pieces.remove(piece)
    _refresh(panel)


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


def _clear(panel: _Panel) -> None:
    """
    清掉所有段 / Drop every piece.

    音色模板留在 `panel.template` 上：清空之后「加一段」还得有个模板，
    否则新段的语言与参考原话全丢，表现是「加一段」点了没反应。
    """
    panel.pieces.clear()
    panel.list_box.clear()      # type: ignore[union-attr]
    _refresh(panel)


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
    except Exception as exc:  # noqa: BLE001 - 失败要立刻说清原因
        ui.notify(f"第 {piece.number} 段生成失败：{exc}", type="negative", timeout=12000,
                  multi_line=True, close_button=True)
        return
    finally:
        note.dismiss()

    _adopt(panel, piece, wav, text, voice)
    _refresh(panel)


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
            except Exception as exc:  # noqa: BLE001 - 一段失败不该毁掉整趟
                failed.append((piece.number, str(exc)))
                continue
            _adopt(panel, piece, wav, text, voice)
            done += 1
    finally:
        note.dismiss()
        _refresh(panel)

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
                except Exception as exc:  # noqa: BLE001
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


__all__ = ["open_panel"]
