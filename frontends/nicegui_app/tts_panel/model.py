"""操作台的状态模型：一段是什么、整块面板持有什么 / The console's state。

`✔ / ✎ / ○` 三态是这个面板的核心：已生成 / 生成后改过（缓存作废）/ 没生成过。
改文本或换声音配置都会让这一段的缓存作废 —— 否则会拿旧波形去拼新文本。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from dna.produce import ProductionKind
from dna.tts.base import SpeechSegment, VoiceSpec
from dna.tts.segment import DEFAULT_MAX_SEGMENT_CHARS
from frontends.nicegui_app.actions import RowView
from frontends.nicegui_app.voice_controls import VoiceControls, reroll_seed

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
        self.refresh: Callable[[], None] = lambda: None
        """
        重画底栏与三态的回调，由 `shell` 装配时注入。

        **这个槽是为了打破一个真实的环**：重画在 `view` 里，而每段的编辑动作
        （`edit`）与合成（`synth`）改完状态都要重画。让它们 import `view`，
        而 `view` 又要 import 它们才能挂事件 —— 两边互相 import。
        注入一个回调之后依赖是单向的：shell → view → edit/synth → model。
        A callback slot rather than an import: it makes the dependency one-way.
        """

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
