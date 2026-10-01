# 待执行的提交命令

> **这个文件是一次性的。** 每次覆写，只留当前这一组；上一组的内容在 `git log` 里。
>
> **覆写前必须先 `git log` 核对上一组是否已执行。** 本次核对结果（2026-10-01）：
> 上一组「MiniMax 克隆音色模板」已提交（`d9de49a`），故安全覆写。
>
> `git add` 一律写明路径，**不用 `-A`**：`.env` 有密钥、`outputs/` 有几百 MB 产物。
> hook 会拦下 `git commit` / `git push` —— 这些命令由人工执行。
>
> ⚠️ **不要提交** `config/profile.yaml`：工作区里那两行改动是人工调的，不属于这一组。

分支：`opt_gui` · 本组内容：**GUI 易用性优化，按功能拆成 8 个 commit**

**几个文件里混着多个功能的改动**（`actions/__init__.py`、`ledger_table/cells.py`、
`theme.py`、`main.py`、`tests/frontends/test_gui_opt.py`），这些按 hunk 拆成了
`c:/tmp/dna_commits/03~07.patch`，用 `git apply --cached` 只进 index、不动工作区。
**必须按顺序执行**：每个 patch 都以前一步的 index 为基准。
整套命令已在临时 index 上演练过，每一步的快照都跑过
`tests/frontends` 和 `tests/produce/test_service.py`，全部通过。

```bash
cd c:/Users/test/Downloads/xkd/DailyNewsAssistant
F=frontends/nicegui_app
P=c:/tmp/dna_commits
```

## ① 开发守则：测试分级 + 忽略经验文件

```bash
git add .gitignore CLAUDE.md
git commit -m "chore: 守则第 3 条改为平时只跑相关测试包、收尾跑一次 check.py；忽略 optimization_brief

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## ② 后端：人工编辑写回（工作台与 TTS 操作台共用）

```bash
git add src/dna/produce/editing.py src/dna/produce/__init__.py src/dna/produce/service.py  tests/produce/test_service.py
git commit -m "feat(produce): save_production_text 统一人工编辑写回，字数与估算时长重算

- 口播类按 spoken_text 数字数、估时长并刷新「口播（约 N 秒 · M 字）」行；
  总结只数抬头外正文、不补口播标记；长文案（json 旁车）拒绝
- save_script_text 改为委托它。修：TTS 操作台校对写回时不记 est_seconds，
  格子上的时长会丢

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## ③ 产出格「XX字 (约XXs)」+ 媒体格开两个文件夹

```bash
git add $F/actions/paths.py tests/frontends/test_workbench.py
git apply --cached $P/03.patch
git commit -m "feat(gui): 产出格统一「字数 (约时长)」；媒体格图、视频都有就打开两个文件夹

- cell_seconds：台账无 est_seconds（总结、旧产物）时按字数现算
- ≥1.5 分钟显示 min；产物列加宽到 124px
- media_target → media_targets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## ④ 展开面板双击编辑文案

```bash
git add $F/actions/editing.py $F/text_editor.py $F/detail_panel.py
git apply --cached $P/04.patch
git commit -m "feat(gui): 展开面板双击文案编辑、失焦自动保存；长文案只读并提示去 TTS 操作台

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## ⑤ 任务登记表 + 任务坞：运行动效与并行任务

```bash
git add $F/jobs.py $F/audio_progress.py $F/ledger_table/run.py  $F/tts_panel/shell.py $F/tts_panel/synth.py
git apply --cached $P/05.patch
git commit -m "feat(gui): 任务登记表与页面级任务坞，支持并行任务与格子运行动效

- 同一格（文章, 产物, 语言）重复点生成被挡住，不重复计费
- 计时器、进度浮窗、TTS 操作台挂在任务坞上：修了「一个任务结束重画表格，
  把别的任务的计时器/浮窗/操作台一起删掉」
- 关掉 TTS 操作台（点别处）合成在后台继续；浮窗在右下角往上叠

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## ⑥ 批量生成四种产物

```bash
git add $F/actions/batch.py $F/ledger_table/batch.py $F/ledger_table/batch_produce.py
git apply --cached $P/06.patch
git commit -m "feat(gui): 批量条支持批量生成总结/短视频/口播/长文案

- 先算后跑：确认框报新生成/复用/跳过篇数与预估调用次数；已生成的复用不重做
- 长文案单独选专题/访谈并警告费用；逐条串行执行，格子依次亮起
- 批量条单独占一行，图例与翻页移到下一行

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## ⑦ TTS 操作台布局

```bash
git add $F/voice_controls.py $F/tts_panel/view.py
git apply --cached $P/07.patch
git commit -m "feat(gui): TTS 操作台全文与分段同列对齐；三种声音模式只显示对应配置

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## ⑧ 文档

```bash
git add docs/13_workbench_guide.md docs/14_tts_guide.md docs/00_STAGE_SUMMARY.md  docs/git_commands.md
git commit -m "docs: 同步工作台与 TTS 指南；阶段看板记 opt_gui 进度

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git status --short   # 应只剩 config/profile.yaml 与 .claude/ 下的本机文件
git push -u origin opt_gui
```
