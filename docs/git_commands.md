# 待执行的提交命令

> **这个文件是一次性的。** 每次覆写，只留当前这一组；上一组的内容在 `git log` 里。
>
> **覆写前必须先 `git log` 核对上一组是否已执行。** 本次核对结果（2026-10-04）：
> 上一组「GUI 易用性优化（8 个 commit）」**已全部执行**——`8455eae` 是第 ⑧ 个，
> 且已通过 `96bba1d Merge pull request #2` 合进 `main`。故安全覆写。
>
> `git add` 一律写明路径，**不用 `-A`**：`.env` 有密钥、`outputs/` 有几百 MB 产物。
> hook 会拦下 `git commit` / `git push` —— 这些命令由人工执行。
>
> ⚠️ **不要提交** `config/profile.yaml`：工作区里那两行改动是人工调的，不属于这一组。
> ⚠️ **不要提交** `start.bat - 快捷方式.lnk`：那是本机快捷方式，不是源码。

分支：`main` · 本组内容：**字幕改为段内切分，并显示原文案（5 个 commit）**

背景与实测数据见 `docs/issues/016-subtitles-used-the-rewritten-copy.md`；
用法与调参见 `docs/14_tts_guide.md` 的「字幕的文本与切分」。

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
  当成「整段没有停顿」，而实际是读不了

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## ② 切分与配对：条数配对，不按字数比例

```bash
git add src/dna/tts/cue_split.py
git commit -m "feat(tts): 字幕段内切分与条数配对

- 朗读稿决定条数与时间（音频是按它合成的），原文决定显示什么
- **不按字数比例映射**：改写前后长度允许 0.6~1.8 倍浮动（preprocess 的
  _MIN_RATIO/_MAX_RATIO），比例映射必然错位。改为按条数配对
- 理论边界吸附到最近的真实停顿，每处停顿只用一次且保持严格递增
  （吸到同一点会切出零长度字幕，某些播放器整份都不加载）
- 文本优先的取舍写进模块 docstring：偶尔偏几十毫秒，比显示错字轻

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## ③ 数据流：让原文活下来，并接进合成

```bash
git add src/dna/tts/preprocess.py src/dna/tts/base.py src/dna/produce/audio.py \
  src/dna/tts/service.py src/dna/tts/subtitle.py
git commit -m "feat(tts): 字幕改用原文案，并逐段落一份 srt

- PreparedSpeech 增加 source（clean_for_speech 的输出，与作者写的逐字对应）；
  此前它只带出改写稿，原文算完就丢了，所以字幕只能显示同音字替换后的版本
- SpeechSegment.source + AudioClip.piece_cues
- fetch_artifacts 顺带写 seg_001_narrator.srt：音频的逐段副本早就落了盘，
  字幕却只有整条那份，想换一句话时有音频没字幕
- 逐段字幕时间从 0 起算（描述这一段自己），整条那份才是累积时间轴
- subtitle.py 删掉 build_cues 与重复的 strip_markup（切分职责已归 cue_split）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## ④ 测试：三个新文件 + 端到端落盘

```bash
git add tests/tts/test_vad.py tests/tts/test_cue_split.py tests/tts/test_subtitle.py \
  tests/produce/test_service.py
git commit -m "test: 字幕切分的回归

- vad：停顿检出、分位数抗爆音、立体声混音、位深与损坏文件
- cue_split：条数必须精确等于 n、切分不增删字符、停顿每处只用一次、
  显示原文而非朗读稿、时间轴单调且有界
- subtitle：逐段字幕从 0 起算、整条算进段间静音、文件名与音频同名、SRT 带 BOM
- produce：逐段 srt 与逐段 wav 同目录落盘，且内容是原文

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## ⑤ 提示词措辞修正 + 探针 + 文档

```bash
git add config/prompts/shortvideo.zh.md tools/probe_subtitles.py \
  docs/issues/016-subtitles-used-the-rewritten-copy.md \
  docs/14_tts_guide.md docs/00_STAGE_SUMMARY.md docs/git_commands.md
git commit -m "fix(prompts) + docs: 字数区间措辞对齐、字幕切分说明与探针

- shortvideo.zh.md 是唯一写成「字数在 X~Y 之间」的模板，其余三处
  （narration.zh.md / summarize.md / prompts/README.md）与全部测试断言都用
  「X~Y 字」—— 对齐后 tests/narration/test_script_builder.py 转绿
- tools/probe_subtitles.py：拿真实产物看切分形状，调 SplitParams 前先跑它
- issues/016 记「字幕误用改写稿 + 一段一条太长」的现象/判定/修法/实测

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 核对

```bash
git status --short
# 应只剩：config/profile.yaml（人工改的）、start.bat - 快捷方式.lnk（本机文件）
git log --oneline -6
```
