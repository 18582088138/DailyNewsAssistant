# 待执行的提交命令

> **这个文件是一次性的。** 每次覆写，只留当前这一组；上一组的内容在 `git log` 里。
>
> **覆写前必须先 `git log` 核对上一组是否已执行。** 本次核对结果（2026-10-05）：
> 上一组「GUI 易用性优化（8 个 commit）」已执行（`8455eae`），`custom_article`
> 那批也已随 `b825546 Merge pull request #3` 合进 `main`；本地已快进到 `origin/main`。
> 故安全覆写。
>
> `git add` 一律写明路径，**不用 `-A`**：`.env` 有密钥、`outputs/` 有几百 MB 产物。
> hook 会拦下 `git commit` / `git push` —— 这些命令由人工执行。
>
> ⚠️ **不要提交** `config/profile.yaml`：工作区里那两行（`summary_chars`、`cta_line`）
> 是人工调的，不属于这一组。
> ⚠️ **不要提交** `start.bat - 快捷方式.lnk`：本机快捷方式，不是源码。

分支：`main` · 本组内容：**字幕改为段内切分、显示原文案、旋钮可配（7 个 commit）**

背景与实测见 `docs/issues/017-subtitles-used-the-rewritten-copy.md`；
用法见 `docs/14_tts_guide.md` 的「字幕的文本与切分」。
提交前跑一次 `python tools/check.py` 确认闸口全绿（ruff + 全量测试 + doctor）。

```bash
cd "F:/2026年/DailyNewsAssistant"
```

## ① 停顿检测：能量法 VAD（零新依赖）

```bash
git add src/dna/tts/vad.py
git commit -m "feat(tts): 能量法停顿检测，给字幕切分提供真实停顿

- 纯标准库 wave + 分帧 RMS：tts 那组依赖只有 httpx，不为几十秒的干净音频
  引入 onnxruntime。输入是刚合成的音频，没有背景噪声，静音段真的接近零
- 语音电平取高分位而不是峰值：一个爆破音就能把峰值抬高一个数量级，
  用峰值当基准会把整段判成静音
- 位深不认识时抛 UnsupportedWav，不静默返回空样点 —— 返回空会被下游
  当成「整段没有停顿」，而实际是读不了"
```

## ② 切分与配对：要显示的文本说了算

```bash
git add src/dna/tts/cue_split.py
git commit -m "feat(tts): 字幕段内切分，条数由要显示的文本决定

- **标点绝对优先**：字数只是软上限，到点后继续走到下一个标点；硬上限只用来
  回退到标点，只有长到两倍上限仍无标点才就地切。否则会切出「函 / 数」这种
  半个词的字幕（实测在真实产物里见过）
- **不按字数比例映射**：改写前后长度允许 0.6~1.8 倍浮动，比例映射必然错位
- 时间边界吸附到真实停顿，每处只用一次且严格递增；过短的条目并入相邻条目
  （吸附后可能出现几十毫秒的条，一闪而过）
- 修正 split_to_n 的边界：单元数正好等于目标条数时应当一一对应，
  原来写成 <= 会掉进按字符硬切"
```

## ③ 数据流：让原文活下来，并接进合成

```bash
git add src/dna/tts/preprocess.py src/dna/tts/base.py src/dna/produce/audio.py  src/dna/tts/service.py src/dna/tts/subtitle.py
git commit -m "feat(tts): 字幕改用原文案，并逐段落一份 srt

- PreparedSpeech 增加 source（clean_for_speech 的输出，与作者写的逐字对应）；
  此前它只带出改写稿，原文算完就丢了，于是字幕只能显示同音字替换后的版本
  与中文数字（实测 2.1.287 被显示成「二点一点二八七」）
- SpeechSegment.source + AudioClip.piece_cues
- fetch_artifacts 顺带写 seg_001_narrator.srt：音频的逐段副本早就落了盘，
  字幕却只有整条那份，想换一句话时有音频没字幕
- 逐段字幕时间从 0 起算（描述这一段自己），整条那份才是累积时间轴
- subtitle.py 删掉 build_cues 与重复的 strip_markup（切分职责已归 cue_split）"
```

## ④ 切分旋钮可配（配置铁律）

```bash
git add src/dna/core/config/profile.py
git commit -m "feat(config): 字幕切分旋钮进 profile.tuning

CLAUDE.md 的配置铁律：可调参数不许写死在代码里。判据是「改一下会不会影响
产物质量，而用户现在改不动它」—— subtitle_max_chars 正是这种值。

- Profile.tuning 增加 subtitle_max_chars / subtitle_min_chars /
  subtitle_merge_below_seconds / subtitle_snap_window，并校验下限不超上限
- 与上面那批语速常量**刻意不同**：语速变了会推翻按它校准的字数窗口，
  而字幕参数只影响观感，与验收窗口不耦合，所以可以开放
- SplitParams.from_tuning 负责映射；TTSServiceProvider 构造时读一次，
  profile 读不到就退回默认值（字幕不该因为偏好文件有问题就整个不出）"
```

## ⑤ 测试：三个新文件 + 落盘 + 配置生效

```bash
git add tests/tts/test_vad.py tests/tts/test_cue_split.py tests/tts/test_subtitle.py  tests/produce/test_service.py tests/narration/test_script_builder.py
git commit -m "test: 字幕切分的回归

- vad：停顿检出、分位数抗爆音、立体声混音、位深与损坏文件
- cue_split：切口必须落在标点上、软上限等下一个标点、单元数等于条数时
  一一对应、合并一闪而过的条目、显示原文而非朗读稿且保留阿拉伯数字、
  时间轴单调有界、**旋钮从 profile 读且真的改变输出**
- subtitle：逐段字幕从 0 起算、整条算进段间静音、文件名与音频同名、SRT 带 BOM
- produce：逐段 srt 与逐段 wav 同目录落盘，且内容是原文
- narration：两处断言改为跟着模板的写法（模板由用户调，不回改）"
```

## ⑥ 修复：操作台把原文案丢了

```bash
git add frontends/nicegui_app/tts_panel/model.py frontends/nicegui_app/tts_panel/view.py  tests/frontends/test_workbench.py
git commit -m "fix(gui): TTS 操作台重建 SpeechSegment 时透传 source

取段有两条路，只有一条带了原文案：

- 命令行走 _build_segments，它一直把 clean_for_speech 的输出挂在
  SpeechSegment.source 上
- 操作台走外部传入的 segments —— 它在 plan() 里**重建** SpeechSegment
  （因为人在界面上改过文本与音色），却漏了 source，于是 _generate_audio
  拿到的是空 source，字幕退回显示朗读稿
- 表现：短视频稿里是 30项 / 50.64 / 39.56 / 80.25，字幕却出现
  「三十项」「五十点六四」「三十九点五六」「八十点二五」
- 修法：_Piece 带上 source，构造时从 segment 复制，plan() 再带出去
- 补一条用例：操作台的 plan() 必须把 source 带出来"
```

## ⑦ 文档与探针

```bash
git add tools/probe_subtitles.py docs/issues/017-subtitles-used-the-rewritten-copy.md  docs/14_tts_guide.md docs/00_STAGE_SUMMARY.md docs/git_commands.md
git commit -m "docs: 字幕切分说明、探针与 issue 017

- 017 记「字幕误用改写稿 + 一段一条太长」，含真实产物上的实测（每段 9~13 秒
  切成 3~6 条、切口全在标点）与三次自我修正的过程（含查错方向的那一次）
- 14_tts_guide 补「字幕的文本与切分」：两个文本的分工、条数配对、停顿从哪来
- tools/probe_subtitles.py：拿真实产物看切分形状，调旋钮前先跑它"
```

---

## 核对

```bash
git status --short
# 应只剩：config/profile.yaml（人工改的）、start.bat - 快捷方式.lnk（本机文件）
git log --oneline -9
```
