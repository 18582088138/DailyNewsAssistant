"""
一组声音配置控件 / One set of voice-configuration controls.

**统一配置与每段的单独配置用同一份实现。** 操作台顶部那套（整篇统一）和每段展开的
那套（这一段单独）是同一个类的两个实例——各写一份的话，「克隆模式不能同时给音色名」
这类护栏迟早只剩一处，而漏掉它的表现是服务端报参数冲突，看起来像服务坏了。
The panel-wide controls and the per-piece ones are two instances of one class: a second
implementation would eventually drop one of the guards below.

两条硬规则住在 `voice_from()`（纯函数，可单测）/ Two hard rules live in `voice_from()`:
    1. 克隆模式下 `speaker` 放的是**绝对路径**，且**不带音色名**——这是
       `tts/factory.py::_clone_voice` 的写法；服务端把「有 ref_audio 又有音色名」
       当成互相冲突的参数直接报错（它故意不做静默忽略）。
    2. **换了参考音频就清掉 `ref_text`**：参考原话只对录它的那段音频成立，
       带到另一个文件上，上游会按 ICL 对齐一段根本没说过的话，克隆质量明显变差。

下拉框的初值另有一条 / One more rule for the two selects:
    `ui.select` 的初值**必须在选项里**，否则 NiceGUI 在构造时就抛
    `ValueError: Invalid value: …`，**整个面板打不开**。规则 1 的代价正落在这里——
    克隆配置里 `speaker` 是一条 wav 路径，它永远不在音色名列表里；参考音频那格
    则是「选项是相对路径、配置解析完是绝对路径」。两处都由 `voice_choice()` /
    `ref_choice()` 折算，别在画界面的地方就手改（改一处漏一处的事已经发生过一次）。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from nicegui import ui

from dna.tts.base import VoiceSpec
from frontends.nicegui_app import actions

MODE_BUILTIN = "custom_voice"
MODE_DESIGN = "voice_design"
MODE_CLONE = "voice_clone"
MODE_LABELS = {
    MODE_BUILTIN: "内置音色",
    MODE_DESIGN: "音色设计",
    MODE_CLONE: "克隆参考音频",
}
"""三种模式与服务端的 `Mode` 一一对应（`agentic_tts/core/types.py`）。

`mode` 一路是字符串传到 `/tts/synthesize` 的 payload 里，所以加一种模式**不需要动后端**——
但每种模式各有一条服务端会当场拒掉的前置条件，见 `voice_problem()`。
"""

MODE_HINTS = {
    MODE_BUILTIN: "用服务端已有的音色；「语气指令」只调语气。",
    MODE_DESIGN: "没有现成音色时用：把「语气指令」写成音色描述（必填），"
                 "服务端按描述现造一个声音——同一段描述每次生成不完全一样。",
    MODE_CLONE: "克隆参考音频里的那个人；没有参考原话时只能走「只用音色向量」。",
}

BASE_SEED = 20260907
"""重掷种子的基准 / the base for a re-rolled seed.

具体取值不重要（与上游 `configs/config.yaml` 的默认值一致，纯粹为了眼熟）：
要紧的是**重掷出来的种子彼此不同，也不等于服务端的默认那一版**，见 `reroll_seed`。
"""

REROLL_STRIDE = 7919
"""照抄 TTS 图形界面的重掷步长（`gui/app.py`）：素数步长让相邻两次重掷差得够远。"""


def reroll_seed(index: int, reroll: int) -> int | None:
    """
    第 `index` 段第 `reroll` 次重掷该用哪个种子 / The seed for one re-roll.

    **没重掷过就不给种子**（返回 None），让服务端用它自己配的那一个——
    这样「第一次生成」和「服务端自动合成」是同一版音频，不会莫名其妙地不一样。
    Not having re-rolled means sending no seed at all, so the first take matches what
    automatic synthesis would produce.

    重掷之后的种子里带上 `index`：否则同一次重掷会把所有段推到同一个种子上，
    整篇的采样偏好一起变，听起来像换了个人。
    """
    if reroll <= 0:
        return None
    return BASE_SEED + index + REROLL_STRIDE * reroll


def voice_choice(voices: list[str], base: VoiceSpec) -> tuple[list[str], str]:
    """
    音色下拉框的选项与初值 / The options and initial value of the voice select.

    **克隆与音色设计模式下 `base.speaker` 不是音色名**：克隆那边放的是参考音频的
    绝对路径（规则 1），设计那边根本没有音色名。把它当初值塞进 `ui.select` 会得到
    `ValueError: Invalid value: C:\\…\\ref_audio\\xxx.wav`，面板直接打不开。
    In clone mode `speaker` carries a wav path, which is never one of the voice names.
    """
    named = base.speaker if base.mode not in (MODE_CLONE, MODE_DESIGN) else ""
    options = list(voices)
    if named and named not in options:
        options.append(named)   # 配置里写的音色服务端没报，也得让人看见自己配了什么
    return options, (named or (options[0] if options else ""))


def ref_choice(options: list[str], base: VoiceSpec) -> tuple[list[str], str]:
    """
    参考音频下拉框的选项与初值 / The options and initial value of the ref-audio select.

    选项是**相对路径**（`ref_audio/x.wav`，与 `.env` 里写的同一个形式），而
    `base.ref_audio` 已经被 `factory._ref_audio_path` 解析成**绝对路径**。
    两种形式混在一个下拉里同样会撞上 `Invalid value`，所以能对上的折回相对形式；
    真在 `data/ref_audio/` 之外的文件才把绝对路径也列进去。
    """
    choices = list(options)
    current = base.ref_audio or ""
    if not current:
        return choices, ""
    tail = current.replace("\\", "/")
    for choice in choices:
        # 「绝对路径以这条相对路径结尾」比只比文件名严格：同名不同目录不会被悄悄换掉
        if tail.endswith("/" + choice.replace("\\", "/")):
            return choices, choice
    choices.append(current)
    return choices, current


def voice_problem(spec: VoiceSpec) -> str | None:
    """
    这套音色现在还差什么 / What this voice is still missing, if anything.

    服务端对三种模式各有一条硬前置（`SynthRequest._check_consistency`）：缺了就是
    422，而合成一段要等几十秒。**在点下去之前就说清楚**，别让人等完再看报错。
    """
    if spec.mode == MODE_DESIGN and not spec.instruct.strip():
        return "音色设计要先写音色描述（如「沉稳的中年男声，播音腔」）"
    if spec.mode == MODE_CLONE and not spec.ref_audio:
        return "克隆模式要先选一段参考音频"
    if spec.mode == MODE_BUILTIN and not spec.speaker.strip():
        return "内置音色模式要先选一个音色"
    return None


def voice_from(
    base: VoiceSpec,
    *,
    mode: str,
    speaker: str = "",
    instruct: str = "",
    ref_audio: Path | None = None,
    x_vector_only: bool = False,
    seed: int | None = None,
) -> VoiceSpec:
    """
    把控件上的值算成一个 `VoiceSpec` / Turn the control values into one VoiceSpec.

    纯函数：`ref_audio` 收的是**已经解析好的路径**（由 `actions.ref_audio_file`
    按 `data_dir` 解析），所以这条规则不依赖任何设置就能单测。
    """
    if mode == MODE_DESIGN:
        # 音色设计只吃 instruct：服务端要求有描述，且**不能同时带 ref_audio**
        # （`mode=voice_design 不该带 ref_audio`），音色名那边它压根不看。
        return replace(
            base, mode=MODE_DESIGN, speaker="", instruct=instruct,
            ref_audio="", ref_text="", x_vector_only=False, seed=seed,
        )

    if mode != MODE_CLONE:
        # 从克隆/设计切回内置音色时**不能拿 base.speaker 兜底**：那里放的是一条
        # wav 路径，送去合成会被当成不存在的音色名。
        fallback = "" if base.mode in (MODE_CLONE, MODE_DESIGN) else base.speaker
        return replace(
            base, mode=MODE_BUILTIN, speaker=speaker or fallback, instruct=instruct,
            ref_audio="", ref_text="", x_vector_only=False, seed=seed,
        )

    if ref_audio is None:
        # 解析不到就保留原来的音色，别把一个空路径送去合成（服务端会报参考音频不存在，
        # 而真正的原因是这台机器上没有那个文件）。
        return replace(base, instruct=instruct, seed=seed)

    same_clip = str(ref_audio) == base.ref_audio
    transcript = base.ref_text if same_clip else ""
    forced = x_vector_only or not transcript
    return replace(
        base, mode=MODE_CLONE, speaker=str(ref_audio), ref_audio=str(ref_audio),
        ref_text="" if forced else transcript, x_vector_only=forced,
        instruct=instruct, seed=seed,
    )


class VoiceControls:
    """
    画一组声音配置控件，并按控件上的值算出音色 / Draw the controls and compute the voice.

    `uploads=False` 给每段的单独配置用：上传参考音频是整篇共享的事，
    每段一个上传框只会让人不知道该点哪个。
    """

    def __init__(
        self,
        *,
        voices: list[str],
        base: VoiceSpec,
        on_change=None,
        uploads: bool = True,
    ) -> None:
        self.voices = voices
        self.base = base
        self.on_change = on_change
        self.uploads = uploads

        self.mode_box: object | None = None
        self.voice_box: object | None = None
        self.instruct_box: object | None = None
        self.ref_box: object | None = None
        self.xvec_box: object | None = None
        self.mode_hint: object | None = None

    # ---------------------------------------------------------- 画 / rendering

    def render(self) -> None:
        """在当前容器里画出这组控件 / Draw into the current container."""
        mode = self.base.mode if self.base.mode in MODE_LABELS else MODE_BUILTIN
        with ui.row().classes("items-center gap-3 no-wrap"):
            self.mode_box = ui.radio(
                MODE_LABELS, value=mode, on_change=lambda _e: self._changed()
            ).props("inline dense")

            # 音色表取不到时退成自由文本框：服务离线不该让面板用不了
            voice_options, voice_value = voice_choice(self.voices, self.base)
            self.voice_box = (
                ui.select(
                    voice_options,
                    # 空字符串不是合法初值（NiceGUI 只放过 None）
                    value=voice_value or None,
                    new_value_mode="add-unique",
                    on_change=lambda _e: self._changed(),
                )
                .props("outlined dense use-input input-debounce=0")
                .classes("w-[170px]")
            )
            self.instruct_box = (
                ui.input(
                    value=self.base.instruct,
                    placeholder="语气指令，如「沉稳，语速稍慢」",
                    on_change=lambda _e: self._changed(),
                )
                .props("outlined dense")
                .classes("flex-grow")
                .tooltip("自然语言描述语气；音色设计模式下这里就是「音色描述」，必填")
            )

        with ui.row().classes("items-center gap-2 no-wrap w-full"):
            ref_options, ref_value = ref_choice(actions.ref_audio_options(), self.base)
            self.ref_box = (
                ui.select(
                    ref_options, value=ref_value or None,
                    label="参考音频", on_change=lambda _e: self._changed(),
                )
                .props("outlined dense use-input input-debounce=0")
                .classes("w-[320px]")
            )
            self.xvec_box = ui.checkbox(
                "只用音色向量", value=self.base.x_vector_only,
                on_change=lambda _e: self._changed(),
            ).tooltip("没有参考原话时只能这样；有原话时勾上等于放弃 ICL 对齐")
            if self.uploads:
                ui.upload(
                    label="上传参考音频", auto_upload=True, max_files=1,
                    on_upload=self._upload,
                ).props("flat dense accept=.wav,.mp3,.flac,.m4a").classes("w-[240px]")

        # 灰掉的控件说明「这个模式不看它」，这行说明「这个模式看哪个」——
        # 音色设计的必填项藏在「语气指令」那个框里，不说没人找得到。
        self.mode_hint = ui.label("").classes("wb-path")

        self.apply_mode()

    async def _upload(self, event) -> None:
        """
        收下一份参考音频并**立刻选中它** / Adopt the clip and select it right away.

        不选中的话，人上传完还要自己再去下拉框里找一遍——而他刚才那个动作
        本来就已经表达了「用这个」。
        """
        try:
            relative = await actions.upload_ref_audio(event.name, event.content.read())
        except Exception as exc:
            ui.notify(f"参考音频保存失败：{exc}", type="negative", multi_line=True,
                      close_button=True)
            return
        options = actions.ref_audio_options()
        if relative not in options:
            options.append(relative)
        # 选项与初值必须**同一次**换掉：先 `options=` 再 `update()` 的话，NiceGUI 会
        # 在 `_update_options()` 里把当前值悄悄打回 None（它只保留仍在新选项里的值）。
        self.ref_box.set_options(options, value=relative)   # type: ignore[union-attr]
        if self.mode_box is not None:
            self.mode_box.set_value(MODE_CLONE)   # 上传就是为了克隆
        ui.notify(f"已保存并选中 {relative}", type="positive")
        self._changed()

    def apply_mode(self) -> None:
        """
        按模式灰掉无关控件 / Grey out what this mode ignores.

        灰掉而不是隐藏：隐藏会让人以为「这个功能没有」，
        灰掉说的是「这个模式下它不生效」。
        """
        mode = self.mode
        clone = mode == MODE_CLONE
        for box, wanted in ((self.ref_box, clone), (self.xvec_box, clone),
                            (self.voice_box, mode == MODE_BUILTIN)):
            if box is None:
                continue
            if wanted:
                box.enable()
            else:
                box.disable()
        if getattr(self, "mode_hint", None) is not None:
            self.mode_hint.set_text(MODE_HINTS.get(mode, ""))

    def _changed(self) -> None:
        self.apply_mode()
        if self.on_change is not None:
            self.on_change()

    # -------------------------------------------------------- 取值 / the value

    @property
    def mode(self) -> str:
        return str(getattr(self.mode_box, "value", MODE_BUILTIN) or MODE_BUILTIN)

    def spec(self, base: VoiceSpec | None = None, *, seed: int | None = None) -> VoiceSpec:
        """
        这套控件当前描述的音色 / The voice these controls currently describe.

        `base` 给每段自己的原始 `VoiceSpec`（里面带着这一段的语言与参考原话），
        不给就用构造时那份。
        """
        raw = str(getattr(self.ref_box, "value", "") or "")
        return voice_from(
            base if base is not None else self.base,
            mode=self.mode,
            speaker=str(getattr(self.voice_box, "value", "") or ""),
            instruct=str(getattr(self.instruct_box, "value", "") or ""),
            ref_audio=actions.ref_audio_file(raw) if raw else None,
            x_vector_only=bool(getattr(self.xvec_box, "value", False)),
            seed=seed,
        )


__all__ = [
    "BASE_SEED",
    "MODE_BUILTIN",
    "MODE_CLONE",
    "MODE_DESIGN",
    "MODE_HINTS",
    "MODE_LABELS",
    "REROLL_STRIDE",
    "VoiceControls",
    "ref_choice",
    "reroll_seed",
    "voice_choice",
    "voice_from",
    "voice_problem",
]
