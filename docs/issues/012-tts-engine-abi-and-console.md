# 012 · 「5 段全部合成失败」——引擎在服务端加载不起来，而 DNA 说不出原因

日期：2026-09-10 · 表象：短视频音频失败，`5 段全部合成失败，没有可用音频（看服务端日志）`

## 症状

工作台点「合成音频」，几秒钟就回来一句「5 段全部合成失败，没有可用音频（看服务端日志）」。
顶栏 `● 在线`，`dna doctor` 17 OK / 0 FAIL。所有模式都失败，不是克隆专有；
换文章、换音色、重启界面都一样。

## 根因（不在 DNA，在环境）

`data/logs/tts_service.log` 里 5 段倒在同一处：

```
qwen3.py::_load → import qwen_tts → speech_vq.py → import torchaudio.compliance.kaldi
→ OSError: Could not load this library: …\ov_env_py312\…\torchaudio\lib\libtorchaudio.pyd
→ OSError: [WinError 127] The specified procedure could not be found
```

环境里是 **torch 2.10.0+cpu 配 torchaudio 2.8.0+cpu**。`libtorchaudio.pyd` 是按 torch 2.8
的 ABI 编的，装在 2.10 上找不到符号。`import qwen_tts` 在**包顶层**就要 torchaudio，
所以引擎根本加载不起来 —— 每一段都在同一行失败。

修复：

```bash
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pip install \
  --index-url https://download.pytorch.org/whl/cpu "torchaudio==2.10.0+cpu"
```

**装完必须杀掉现有 uvicorn 进程**：服务端不重启就还是那份加载失败的模块状态，
再点一次仍然全失败，看起来像没修好。之后 `dna tts --say "测试一句话"` 出声
（实测 14 字 / 3.1 秒 / RTF 9.05）。

> 环境是共用的，torch 再被人升一次这件事就会原样复发。表象仍然是「N 段全部失败」。

## 为什么排查花了一整轮 —— DNA 自己的三个缺陷

| # | 缺陷 | 代价 | 已修 |
|---|---|---|---|
| 1 | `tts/service.py` 抛的是「看服务端日志」，而每段的真实原因就在同一个函数里（`logger.warning`） | 用户拿到一句无信息量的报错，得去翻另一个进程的日志 | 抛出的 `TTSError` 带上**第一段的真实原因** |
| 2 | `setup_logging()` 全仓库**只有定义没有调用者**，`data/logs/` 下只有 TTS 服务自己的日志 | 那 5 条带原因的 WARNING 直接蒸发 | CLI 与 GUI 启动时各调一次，落到 `data/logs/dna.log` |
| 3 | 服务端 `/health` 只回 `{"status":"ok"}`，**从不碰引擎**；引擎要到第一次合成才加载模型 | 顶栏「● 在线」与 doctor「TTS 服务 在线」都没说谎，但对这类故障完全无效 | doctor 那一行补上「只探到进程；引擎能不能出声要跑 `dna tts --say`」 |

**这类故障唯一有效的探针是 `dna tts --say`。** doctor 里不真合成 —— 模型冷启动要几分钟，
而 doctor 必须是秒级的；能做的是把探针指出来，而不是让人以为绿灯代表能合成。

## 同一轮的第二件事：操作台从「只读一行」变成常用面板

用户同时提出：操作台是**每天都用**的功能（要调文本、校对生成内容），
不是罕见功能，要照 TTS 图形界面在主界面复刻一套；LLM 生成的文本默认导入进去，
拆成多段，每段都能单独生成。

原来的面板每段只有「正文摘头 42 字（只读）+ 音色下拉 + 试听」——
**文本其实早就导进来了**（`actions.speech_segments` 走的就是自动合成那份
`_build_segments`），只是被截断成只读的一行，所以看起来像「没导入」。

改造后（`tts_panel.py` + 新的 `voice_controls.py`）有三处决定值得记住：

1. **正文可编辑，改完写回稿子。** 从前拒绝可编辑的理由是「音频内容会和台账里记的
   稿子对不上」。这个理由成立，所以解决办法不是不给编辑，而是**先写回稿子文件、
   并插一行 `calls=0` 的「人工校对」，再写音频那一行** —— 「这一版到底念了什么」
   仍然只有一个答案。长文案例外：它的稿子按发言人分轮存在 JSON 边车里，
   拉平成一段纯文本会把一场访谈变成一个人念完全部，所以 `save_script_text` 直接拒绝。
2. **逐段生成的波形被整篇合成复用**（`rendered={段号: wav}`）。RTF≈2.5，
   口播整篇约 6 分钟、长文案约 37 分钟；不复用的话逐段校对完全白做。
   `rendered` 的下标按**滤掉空段之后**的位置算，和服务端的编号一致 ——
   错一位的表现是某一段的音频跑到别的段上，每段单独听都好，整篇像乱序，极难查。
3. **改文本或换音色就丢掉这一段的缓存**（`✔ / ✎ / ○` 三态）。不作废的话会拿旧波形
   去拼新文本，而这件事人只有整篇听完才会发现。

以及一条抄上游的细节：**第一次生成不发种子**（`reroll_seed` 返回 `None`），
只有点了「换种子重掷」才带 `seed = BASE_SEED + 段号 + 7919 * 次数`。
第一版和自动合成出来的必须是同一版，人此时并没有要求换一版。

## 同一轮的第三件事：操作台成为唯一的 TTS 界面

用户实测后指出两件事：

> 1. 我需要的是在 DailyNewsAssistant 中复刻一个 TTS 界面，由 DailyNewsAssistant 管理，
>    现在点击打开完整 TTS 界面，还是会去调 TTS GUI，之后 TTS GUI 的界面打开会失败。
> 2. 声音克隆的 ref_audio 加载会失败，报错
>    `ValueError: Invalid value: C:\…\data\ref_audio\qwen3-tts-cpu.wav`

### ① `ValueError: Invalid value:` —— 是**下拉框的初值**，不是参考音频加载失败

报错文本里是一条 wav 路径，看起来像「这个音频读不进来」；实际上项目里没有任何一处
抛这句话，唯一的出处是 `nicegui/elements/choice_element.py`：

```python
if not isinstance(value, list) and value is not None and value not in self._values:
    raise ValueError(f'Invalid value: {value}')
```

**`ui.select` 的初值必须在选项里。** 而克隆模式下 `VoiceSpec.speaker` 放的正是参考
音频的绝对路径（`tts/factory.py::_clone_voice` 的写法，为的是不给服务端同时送
「ref_audio + 音色名」这对冲突参数）。于是：

```python
ui.select(self.voices,            # ["Serena", "Ethan", …] 音色名
          value=self.base.speaker)  # C:\…\ref_audio\qwen3-tts-cpu.wav
```

——**构造时就抛，整个操作台打不开**。`.env` 里配了克隆音色的机器上，
「TTS 操作台」这个按钮从头到尾没能用过一次。

参考音频那一格是同一个坑的另一种形式：选项来自 `console.ref_audio_choices()`，
是**相对路径**（`ref_audio/x.wav`，与 `.env` 里写的同一形式），而
`base.ref_audio` 已被 `factory._ref_audio_path` 解析成**绝对路径**。

修法是把「算初值」从画界面的代码里拿出来，做成两个纯函数（可单测，无需事件循环）：

| 函数 | 规则 |
|---|---|
| `voice_choice(voices, base)` | 克隆/设计模式下 `speaker` 不是音色名，一律不当初值 |
| `ref_choice(options, base)` | 绝对路径**以某条相对选项结尾**就折回那一条；真在别处的原样列出 |

两条附带的坑：**空字符串不是合法初值**（NiceGUI 只放过 `None`），服务离线、
音色表为空时会撞上；上传完刷新选项要用 `set_options(options, value=…)` 一次换掉，
先 `options=` 再 `update()` 的话 `_update_options()` 会把当前值悄悄打回 `None`。

### ② 交接给 TTS 自带界面的那条路已删除

从前操作台右上角是「打开完整 TTS 界面（音色设计等罕用功能）」，它 POST
`/gui/handoff` 拿 token → 新标签页打开 TTS 模块自带的 NiceGUI 界面 → 这边每 4 秒
轮询收产物。删掉它的三个理由：

1. **那个界面开不起来**：`data/logs/tts_gui.log` 里是
   `forrtl: error (200): program aborting due to window-CLOSE event` ——
   它是另一个进程，关一次窗就整体退出。
2. **它写盘的位置不受 DNA 台账管**，两个界面各管一半，「这一版念了什么」
   又变成两个答案。
3. 它唯一还独占的功能是**音色设计**，而那只是 `mode` 的第三个取值。
   DNA 的 `mode` 一路是字符串传到 `/tts/synthesize`，加这个模式**后端一行没改**。

删除范围：`actions.py` 的 `TTSHandoff` / `open_tts_workbench` /
`collect_tts_handoff` / `import_tts_handoff`，`detail_panel.py` 的
`_open_tts_workbench` / `_watch_handoff` / `_HANDOFF_NOTES`（共约 300 行）。
`tts/client.py` 的 `handoff()` / `handoff_state()` 留着——它是服务端 API 的镜像。

### ③ 音色设计的三条前置条件在点之前就要说

服务端 `SynthRequest._check_consistency` 对三种模式各有一条硬要求（缺了就是 422），
而合成一段要等几十秒、整篇要几分钟。`voice_controls.voice_problem()` 把
「缺音色描述 / 缺参考音频 / 缺音色名」在**按钮点下去之前**说出来 ——
等几十秒再收一个 422 是最贵的失败方式。

另外 `voice_design` **不能带 `ref_audio`**（服务端明确报错），从克隆切回内置音色时
**不能拿 `base.speaker` 兜底**（那是一条 wav 路径）——两条都在 `voice_from()` 里。

## 相关文件

- `src/dna/tts/service.py` —— 报错带原因；`rendered` 复用
- `src/dna/tts/{base,client}.py` —— `VoiceSpec.seed` / `SpeechSegment.pause_ms`
- `src/dna/tts/console.py` —— `resplit()` / `save_ref_audio()`
- `src/dna/produce/{documents,service}.py` —— `replace_spoken()` / `save_script_text()`
- `frontends/nicegui_app/{tts_panel,voice_controls,actions,detail_panel}.py`
- `src/dna/core/doctor.py` —— 「在线」是什么意思
- `frontends/{cli/main.py,nicegui_app/main.py}` —— 终于调用 `setup_logging()`
