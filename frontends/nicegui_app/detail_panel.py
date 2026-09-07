"""
展开面板 / The expanded detail panel.

点某个产物格 → 这块展开，显示该产物的全文，并把**这一格的操作**收在里面：
切语言、写修改指令、重做、下载、合成音频、打开素材文件夹。
Clicking a production cell opens this panel with that production's full text and every
action for it.

为什么重做按钮在这里而不在格子上 / Why redo lives here rather than on the cell:
    格子挤在表格里，按钮只有十几像素宽，**点开内容和点重做挨在一起**——
    而这两件事的代价完全不对等：前者免费，后者是一次计费调用。
    把重做放进展开面板，等于要求「先看见内容，再决定要不要重做」，
    这正是重做之前本来就该走的一步。
    On the cell a button is barely a dozen pixels from the one that merely opens the
    content, and the two are not equivalent: one is free, the other is a billed call.
    Moving redo into the panel forces the sequence that should happen anyway — look at
    what is there, then decide whether to pay for another one.

语言开关也在这里 / The language switch lives here too:
    英文版曾经是表格里单独一列。可它和中文版是**同一份东西的两个语言版本**，
    不是两种产物——占两列既挤，又没法扩展到短视频和长文案（那样要六列）。
    收进面板之后，一列对应一种产物，语言是这一格内部的一个开关。
    The English edition used to be its own column, but it is one more language of the
    same thing rather than a different kind. As a column it could not extend to the
    scripts without doubling the table.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from dna.produce import ProductionKind, spec
from dna.produce.tasks import DEFAULT_LANGUAGE, LANGUAGE_LABELS, LANGUAGES
from frontends.nicegui_app import actions
from frontends.nicegui_app.actions import RowView
from frontends.nicegui_app.audio_progress import AudioProgress


def render(
    container: ui.element,
    row: RowView,
    kind: ProductionKind,
    *,
    on_change,
    on_redo,
    lang: str = DEFAULT_LANGUAGE,
) -> None:
    """
    把某个产物的详情画进容器 / Draw one production's detail into the container.

    参数 / Args:
        on_redo: 重做的发起函数，由表格层提供（长文案要先弹形式选择框）
        lang:    当前显示哪个语言版本
    """
    container.clear()
    record = row.production(kind, lang)
    task = spec(kind)

    # 「修改指令」的开关与内容 / the extra-instruction toggle and its text
    #
    # 预填上一版用过的指令：调稿子是渐进的，上次写了「用词再专业一点」，
    # 这次多半在此基础上再加一条。每次给一个空框等于每次都要人回忆上次改了什么。
    # Pre-filled from the last version, because tuning is incremental.
    #
    # `use` 是开关本身，和输入框里的文字分开存 / kept apart from the text on purpose:
    #     关掉开关的意思是「这次不要这句要求」，而不是「把它删了」——文字要留着，
    #     以便再打开时还在。两者合成一个字段的话，关掉开关也照样把预填的指令发出去，
    #     稿子会一直按上一次的要求走，而界面上看不出任何原因。
    #     Switching off means "not this time", not "discard it": the text stays so it is
    #     there when reopened. Folding the two together sends the pre-filled instruction
    #     even when the switch reads off, with nothing on screen to explain the result.
    prefill = actions.last_instructions(row.article, kind, lang)
    state = {"instructions": prefill, "use": bool(prefill)}

    def switch_lang(new_lang: str) -> None:
        """切语言就地重画这一块 / Redraw this panel in the other language."""
        render(container, row, kind, on_change=on_change, on_redo=on_redo, lang=new_lang)

    with container:
        with ui.row().classes("w-full items-center justify-between gap-3 no-wrap"):
            _render_summary(row, kind, lang)
            _render_language_toggle(row, kind, lang, on_switch=switch_lang)
            _render_actions(
                row, kind, lang, state=state, on_change=on_change, on_redo=on_redo
            )

        _render_instructions(state)

        if record is None or not record.ok:
            _render_empty(record, task.label, lang)
            return

        # 内容是**展开时才读**的。表格一页 50 行 × 4 种产物 × 2 种语言，进页面就
        # 全读一遍是 400 次磁盘 I/O，而其中绝大多数没人会看。
        # Read on open: loading every cell up front would be hundreds of disk reads for
        # content nobody has asked to see.
        text = actions.production_text(row.article.id, kind, lang)
        with ui.element("div").classes("wb-body w-full"):
            ui.markdown(text or "_（文件读不到，可能被移动或删除）_")


def _render_language_toggle(row: RowView, kind: ProductionKind, lang: str, *, on_switch) -> None:
    """
    中文 / English 开关 / The language switch.

    **切换本身不花钱**——它只是换一个语言版本来看。那个语言还没生成时，
    切过去看到的是「尚未生成」加一个生成按钮，和第一次做中文版走的是同一条路。
    Switching costs nothing: it only changes which edition is shown. When that edition
    does not exist yet the panel offers to generate it, exactly as it does for Chinese.
    """
    with ui.row().classes("items-center gap-1 no-wrap"):
        for code in LANGUAGES:
            done = row.has_language(kind, code)
            button = ui.button(
                LANGUAGE_LABELS[code],
                on_click=lambda c=code: on_switch(c),
            ).props("flat dense no-caps size=sm")
            if code == lang:
                button.classes("wb-lang-active")
            button.tooltip(
                f"{LANGUAGE_LABELS[code]}版：{'已生成' if done else '尚未生成'}"
                "　·　切换不产生费用"
            )


def _render_summary(row: RowView, kind: ProductionKind, lang: str) -> None:
    """左侧：这一版是什么时候、用哪个模型生成的 / When and with what it was made."""
    record = row.production(kind, lang)
    task = spec(kind)

    with ui.column().classes("gap-1 min-w-0"):
        ui.label(f"{task.label}　{LANGUAGE_LABELS[lang]}").classes(
            "text-sm font-medium"
        ).style("color: var(--wb-accent)")

        if record is None:
            ui.label("尚未生成").classes("wb-path")
            return

        parts = [f"{record.chars} 字"]
        if record.est_seconds:
            parts.append(f"约 {record.est_seconds:.0f} 秒")
        if record.variant:
            parts.append("专题" if record.variant == "feature" else "访谈")
        if record.llm_model:
            parts.append(record.llm_model)
        if record.created_at:
            parts.append(record.created_at.strftime("%m-%d %H:%M"))
        if record.calls > 1:
            parts.append(f"{record.calls} 次调用")
        ui.label("　·　".join(parts)).classes("wb-path")

        # 这一版是带着什么要求做出来的——内容不对时，第一个要看的就是它
        # What this version was asked for: the first thing to check when it reads wrong.
        if record.instructions:
            ui.label(f"指令：{record.instructions}").classes("wb-path").style(
                "color: var(--wb-warn)"
            )


def effective_instructions(state: dict) -> str:
    """
    这次生成真正要发出去的额外要求 / What actually goes into this run's prompt.

    单独拎出来是因为它是**花钱路径上的判断**：读错一次，稿子按上一次的要求生成，
    还会因为提示词不同而绕开缓存，多花的钱和多出的回炉调用都看不出原因。
    Pulled out because it is a decision on a billed path: read it wrong and the draft
    follows the previous brief, misses the cache, and nothing on screen says why.
    """
    return state["instructions"] if state.get("use") else ""


def _render_instructions(state: dict) -> None:
    """
    修改指令 / The extra-instruction box.

    开关默认关着 / Off by default:
        大多数重做不需要改要求，直接重做就行。默认展开一个输入框会让每次重做
        都像是「必须先想一句话」，而那是多余的摩擦。
        Most redos need no change of brief, and an always-open box would make every one
        feel like it demands a sentence first.

    为什么它同时也是「重做能拿到不一样结果」的保障 / Why it also fixes redo:
        LLM 响应缓存的键是完整提示词，同样的输入永远给同样的输出。
        写下一句额外要求，提示词就变了，缓存自然错开——即使不算它对内容的引导作用，
        它也让「再来一次」这件事本身变得有意义。
        The cache keys on the full prompt, so an added requirement changes it and misses
        the cache — quite apart from steering the content.
    """
    with ui.row().classes("w-full items-start gap-2 no-wrap").style("margin-top: 6px"):
        toggle = ui.switch(
            "修改指令",
            value=state["use"],
            on_change=lambda e: state.update(use=bool(e.value)),
        ).props("dense").classes("wb-path")
        toggle.tooltip(
            "给这次生成加一句额外要求，例如「加长到 40 秒」「用词再专业一点」"
            "「多讲局限」。它会接在提示词末尾，优先级高于默认要求"
        )

        box = ui.textarea(
            placeholder="例：加长到 40 秒；多引用原文的具体数字；用词再专业一点",
            value=state["instructions"],
            on_change=lambda e: state.update(instructions=e.value or ""),
        ).props("dense outlined autogrow").classes("flex-1")
        # 上一版用过指令时默认打开（`state["use"]` 已按此初始化）：
        # 那说明这一格正处在「调稿子」的过程中
        # Open by default when the last version used one: this cell is being tuned.
        box.bind_visibility_from(toggle, "value")


def _render_actions(
    row: RowView, kind: ProductionKind, lang: str, *, state: dict, on_change, on_redo
) -> None:
    """右侧：下载 / 合成音频 / 打开文件夹 / 重做 / Download, synthesise, reveal, redo."""
    record = row.production(kind, lang)
    task = spec(kind)
    done = bool(record and record.ok)

    with ui.row().classes("items-center gap-1 no-wrap"):
        _render_downloads(row, kind, lang, done=done)
        _render_folder_buttons(row)

        ui.separator().props("vertical").classes("mx-1")

        _render_audio_button(
            row, kind, lang, done=done, on_change=on_change, on_redo=on_redo
        )

        # 重做与生成用词不同，且重做是琥珀色——**颜色本身就是「这会花钱」的提示**
        # The wording differs and redo is amber: the colour itself is the cost warning.
        label = f"重做（{task.approx_calls} 次调用）" if done else "生成"
        button = ui.button(
            label,
            icon="refresh" if done else "play_arrow",
            on_click=lambda: on_redo(
                row,
                kind,
                force=done,
                on_change=on_change,
                lang=lang,
                instructions=effective_instructions(state),
            ),
        ).props("flat dense no-caps")
        if done:
            button.classes("wb-btn-cost").tooltip(
                "重新调用 LLM，会产生费用。**这一次不会走缓存**——"
                "重做的意思就是要一份不一样的"
            )
        else:
            hint = f"调用 LLM 生成{task.label}，约 {task.approx_calls} 次调用"
            if lang != DEFAULT_LANGUAGE:
                hint += f"（{LANGUAGE_LABELS[lang]}版，单独计费）"
            button.tooltip(hint)


def _render_audio_button(
    row: RowView, kind: ProductionKind, lang: str, *, done: bool, on_change, on_redo
) -> None:
    """
    合成音频 / Synthesise the audio.

    **不花钱，但很花时间**，所以它不是琥珀色（那个颜色专门表示「这会计费」），
    而是普通样式配一个把预估等待写进去的 tooltip。把两种代价用同一个颜色标出来，
    等于让「花钱」这个信号贬值。
    Free but slow, so it does not take the amber styling reserved for billed actions;
    reusing that colour for a different kind of cost would devalue the money signal.

    稿子还没生成时按钮禁用——先有稿子才有音频，这个顺序不该由一次失败来教会用户。
    Disabled until the script exists: the order is inherent, and a failed run is a poor
    way to teach it.
    """
    audio_kind = actions.audio_for(kind)
    if audio_kind is None:
        return

    record = row.production(audio_kind, lang)
    has_audio = bool(record and record.ok)
    wait = actions.audio_estimate_seconds(row.article, audio_kind, lang) if done else 0.0

    if not done:
        ui.button("合成音频", icon="graphic_eq").props(
            "flat dense no-caps disable"
        ).tooltip(f"先生成{LANGUAGE_LABELS[lang]}版的{spec(kind).label}，才能合成音频")
        return

    hint = f"约需 {wait / 60:.0f} 分钟" if wait >= 60 else f"约需 {wait:.0f} 秒"
    ui.button(
        "重新合成" if has_audio else "合成音频",
        icon="graphic_eq",
        on_click=lambda: on_redo(
            row, audio_kind, force=has_audio, on_change=on_change, lang=lang
        ),
    ).props("flat dense no-caps").tooltip(
        f"默认走 TTS 服务的单段合成（{LANGUAGE_LABELS[lang]}），{hint}"
        + ("（已有音频，会覆盖）" if has_audio else "")
    )

    # 高级配置：把稿子推到 TTS 图形界面里精修 / hand the script to the TTS workbench
    #
    # 默认那条路是**一键到底**（自动分段、统一音色、直接落盘）。想换音色、克隆
    # 某个人的声音、或者只重做第 7 句时，那些控件在 TTS 界面里已经有了 ——
    # 在这里再画一套是重复实现，而且两套迟早不一致。
    # The default path is one click. Everything else already exists in the TTS
    # workbench, and building a second copy of it here would guarantee divergence.
    note = _HANDOFF_NOTES.get((row.article.id, str(audio_kind), lang))
    if note:
        ui.label(note).classes("wb-path").style("color: var(--wb-accent)")

    switch = ui.switch("高级配置").props("dense").classes("text-xs")
    switch.tooltip(
        "打开 TTS 图形界面，稿子会自动填进多段模式的文本框："
        "可逐段换音色/克隆/音色设计、单句重生成、导出字幕。"
        "在那边生成完，产物会自动收回到这篇文章的目录里"
    )
    switch.on_value_change(
        lambda event: _open_tts_workbench(row, audio_kind, lang, on_change, switch)
        if event.value else None
    )


def _open_tts_workbench(
    row: RowView, kind: ProductionKind, lang: str, on_change, switch
) -> None:
    """
    把稿子交给 TTS 图形界面 / Hand the script over to the TTS workbench.

    开关是**当一次性按钮用的**：点亮之后立刻弹回去。否则它看起来像一个持久
    生效的模式，而实际上每次点亮只做一次交接。
    The switch acts as a momentary trigger and springs back, because it does not
    describe a persistent mode.
    """
    switch.set_value(False)

    async def _go() -> None:
        notification = ui.notification("正在准备 TTS 界面（服务不在线会自动拉起）…",
                                       spinner=True, timeout=None)
        # 本页地址从浏览器拿，而不是从配置拼：工作台可能绑在任意 host/port 上，
        # 而人是通过哪个地址访问到这里的，就该跳回哪个地址。
        # Read from the browser: whichever address the user came in on is the one to
        # return to.
        try:
            back = await ui.run_javascript("window.location.href", timeout=3.0)
        except Exception:  # noqa: BLE001 - 拿不到就不跳回，功能照常
            back = ""
        try:
            handoff = await actions.open_tts_workbench(
                row.article.id, kind, lang=lang, title=row.article.title,
                return_url=str(back or ""),
            )
        except Exception as exc:  # noqa: BLE001 - 原因原样显示给人看
            ui.notify(f"打不开 TTS 界面：{exc}", type="negative", timeout=12000,
                      multi_line=True, close_button=True)
            return
        finally:
            notification.dismiss()

        ui.navigate.to(handoff.gui_url, new_tab=True)
        ui.notify(f"已把 {handoff.segments} 段稿子导入 TTS 界面（新标签页）；"
                  "在那边生成完，这边会自动收下产物",
                  type="positive", multi_line=True, close_button=True)
        _watch_handoff(handoff, kind, on_change)

    ui.timer(0.01, _go, once=True)


# 「高级配置」那趟的结果留言 / the note left by an advanced-config round
#
# 键是 `(文章 id, 产物, 语言)`。一条一闪而过的 notify 不足以回答
# 「刚才那趟到底成了没有」—— 人可能正好切到别的窗口去了。
# A transient notification cannot answer "did that round work"; the note persists.
_HANDOFF_NOTES: dict[tuple[str, str, str], str] = {}


# 等 TTS 界面出活最多等多久 / how long to keep watching the hand-off
#
# 人在界面里调音色、逐句重掷，半小时是常事；但**不能永远挂着**——
# 页面开一天就会累积一堆定时器。停了也不丢：产物在 TTS 那边还在。
_WATCH_INTERVAL = 4.0
_WATCH_LIMIT = int(90 * 60 / _WATCH_INTERVAL)


def _watch_handoff(handoff, kind: ProductionKind, on_change) -> None:
    """
    盯着交接单，出活了就收下 / Watch the hand-off and adopt the result.

    轮询而不是等 TTS 推过来：那边可能在另一台机器上，也不该知道工作台的地址。
    Polling, because the service should not need this application's address.

    等待动效**和自己合成时用的是同一个组件**（`AudioProgress`）：两种等待对人来说
    是同一件事（"音频在长出来"），换一套样式只会让人以为这是另一种状态。
    进度条靠 TTS 界面写回交接单的 `done/total` 推进 —— 那边生成到第几段，
    这边的条就走到第几段。
    The same widget as local synthesis: to the user both are "audio is being made".
    """
    state: dict = {"ticks": 0, "timer": None, "busy": False}

    def _stop(message: str = "") -> None:
        if state["timer"] is not None:
            state["timer"].deactivate()
        panel.close()
        if message:
            ui.notify(message, type="info")

    panel = AudioProgress(
        f"{spec(kind).label}（TTS 界面）",
        action="生成中",
        hint=f"在那个标签页里生成/合并完成即会自动回传　token {handoff.token[-6:]}",
        on_cancel=lambda: _stop("已停止等待（产物仍在 TTS 那边）"),
    )
    panel.progress.update(done=0, total=handoff.segments)

    async def _poll() -> None:
        if state["busy"]:
            return                     # 上一次还没回来，别叠着发
        state["ticks"] += 1
        if state["ticks"] > _WATCH_LIMIT:
            _stop("等了 90 分钟，已停止等待；产物仍在 TTS 那边，可再点一次高级配置取回")
            return

        state["busy"] = True
        try:
            record = await actions.collect_tts_handoff(handoff)
            if record is None:
                return
            # TTS 界面每生成一段就把 done/total 写回交接单，进度条据此推进
            panel.progress.update(done=int(record.get("done") or 0),
                                  total=int(record.get("total") or handoff.segments))
            if record.get("status") != "done" or not record.get("files"):
                return
            result = await actions.import_tts_handoff(handoff, record)
        finally:
            state["busy"] = False

        _stop()
        if result.ok:
            note = (f"✅ 已由 TTS 界面生成并收下：{result.seconds or 0:.0f} 秒音频，"
                    f"{len(record.get('files', []))} 个文件已拷进文章目录")
            # 记在这里，重建表格后那一格下面会把这句话显示出来 ——
            # 一条一闪而过的提示不足以回答「刚才那趟到底成了没有」。
            # Remembered so the cell can state it after the refresh.
            _HANDOFF_NOTES[(handoff.article_id, str(kind), handoff.lang)] = note
            ui.notify(note, type="positive", multi_line=True, close_button=True)
        else:
            ui.notify(f"产物收取失败：{result.error}", type="negative",
                      multi_line=True, close_button=True)
        on_change()

    state["timer"] = ui.timer(_WATCH_INTERVAL, _poll)


def _render_downloads(row: RowView, kind: ProductionKind, lang: str, *, done: bool) -> None:
    """
    下载按钮 / The download buttons.

    文案与音频随时可下；成片按钮先摆出来但禁用，tooltip 写明要等哪个阶段——
    不摆的话没人知道这个功能将来会有，摆了却能点则是欺骗。
    Scripts and audio download today. The video button is shown but disabled with a
    tooltip naming the stage that will fill it.
    """
    script = actions.production_file(row.article, kind, lang) if done else None
    ui.button(
        icon="description",
        on_click=lambda p=script: ui.download.file(p, p.name),
    ).props(f"flat dense round {'' if script else 'disable'}").tooltip(
        f"下载文案 {script.name}" if script else "还没有文案可下载"
    )

    sidecar = actions.production_sidecar(row.article, kind, lang) if done else None
    if sidecar is not None:
        ui.button(
            icon="data_object",
            on_click=lambda p=sidecar: ui.download.file(p, p.name),
        ).props("flat dense round").tooltip(
            f"下载 {sidecar.name}——按发言人切好的轮次，供 TTS 分配音色"
        )

    # 要念出来的产物有音频；成片还没有 / spoken kinds have audio, video is still pending
    if spec(kind).spoken:
        audio_kind = actions.audio_for(kind)
        audio = actions.production_file(row.article, audio_kind, lang) if audio_kind else None
        ui.button(
            icon="graphic_eq",
            on_click=lambda p=audio: ui.download.file(p, p.name),
        ).props(f"flat dense round {'' if audio else 'disable'}").tooltip(
            f"下载音频 {audio.name}" if audio else "还没有音频。用右侧「合成音频」生成"
        )
        ui.button(icon="movie").props("flat dense round disable").tooltip(
            "下载成片：P6 视频合成后可用"
        )


def _render_folder_buttons(row: RowView) -> None:
    """
    打开素材文件夹 / Reveal the material folders.

    直接开到 `images/` / `videos/`，不是只开文章根目录——**要拷的是素材本身**，
    多点一层在每天重复几十次的操作里是实打实的摩擦。
    Opens straight into `images/` or `videos/` rather than the article root: the material
    is what gets copied out, and one extra click is real friction in a task repeated
    dozens of times a day.
    """
    directory = actions.article_directory(row.article)
    if not directory:
        return

    ui.button(
        icon="folder_open", on_click=lambda: _reveal(directory)
    ).props("flat dense round").tooltip(f"打开产物目录\n{directory}")

    for label, path in actions.media_folders(row.article).items():
        icon = "image" if label == "配图" else "video_library"
        ui.button(
            icon=icon, on_click=lambda p=path: _reveal(p)
        ).props("flat dense round").tooltip(f"打开{label}文件夹\n{path}")


def _reveal(path: str | Path) -> None:
    """打开目录，失败时说清原因 / Reveal a folder, reporting why when it fails."""
    error = actions.open_in_file_manager(path)
    if error:
        ui.notify(error, type="warning")


def _render_empty(record, label: str, lang: str) -> None:  # noqa: ANN001
    """还没有产物时显示什么 / What to show before anything exists."""
    if record is not None and not record.ok:
        # 失败原因**原样显示**：人要据此判断是重试、改配置，还是这篇本来就不该做
        with ui.element("div").classes("wb-body w-full"):
            ui.label(f"上次生成失败：{record.error or '未知原因'}").style(
                "color: var(--wb-danger)"
            )
        return

    ui.label(
        f"{LANGUAGE_LABELS[lang]}版的{label}还没有生成。点右侧「生成」开始（会调用 LLM）。"
    ).classes("wb-path").style("padding: 8px 2px")


__all__ = ["render"]
