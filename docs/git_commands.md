# 待执行的提交命令

> **这个文件是一次性的。** 每次覆写，只留当前这一组；上一组的内容在 `git log` 里。
>
> **覆写前已 `git log` 核对（2026-10-04）**：本文件**上一组的 ①② 已由人工执行**——
> `f7f21eb`（改名）与 `b8b87f0`（后端人工创作的文章，含 `issues/016` 与 `_relocate` 修复），
> 工作区里那些文件已不再出现，故删掉这两节、其余重编号。
> 更早的「opt_gui」整组已合入 `96bba1d`。
>
> `git add` 一律写明路径，**不用 `-A`**：一堆临时脚本不该被卷进来。
> **护栏会拦 `git commit` / `git push` / `git add -A` / `pytest -m live`**
> （DSH 插件 `dsh-plugin-dna-guard`，见 CLAUDE.md 文末），所以这些命令必须由人工执行。
>
> ⚠️ **不要提交** `config/profile.yaml`：工作区里那一行改动是人工调的，不属于这一组。
>
> 分支：`custom_article` · 剩余内容：**素材合规化 / 骨架 / 界面 / CLI / 测试隔离 / 文档**，
> 按 6 个 commit 拆。①~⑥ 顺序执行。

```bash
cd c:/Users/75203/Downloads/xkd/DailyNewsAssistant
```

## ① 后端：素材合规化

```bash
git add src/dna/store/media_normalize.py tests/store/test_media_normalize.py
git commit -m "feat(store): 素材合规化 normalize_media（用户拷进来的图与视频）

- 按**文件头**纠正扩展名、改名成 NN_<来源>.<ext>（本地文件用 custom）、
  缺出处的补写同名 .json（origin: manual + original_name）、回写台账计数
- 三条尺度：**不删除任何文件**（认不出的只点名）、**不覆盖已有边车**
  （那是下载时记下的真出处，重写等于伪造来源）、**不编造出处**
  （credit 写『人工放入（出处未记录）』）
- 判定只看文件头不看目录名：视频被拷进 images/ 要搬到 videos/，
  按目录名判定会让它作为一张『图』永远留在那里
- 幂等：已合规的文件不动、已有边车不覆盖，所以每次点开媒体格都能跑
- 文件头表借用 article_store / video_store 的私有常量与函数，
  不另抄一份：下载路径与整理路径的判定必须完全一致
- **抓取来的文章整趟跳过**（MediaReport.skipped_reason）：它的 images/ videos/
  是抓取当时的历史档案，改名就是改写历史。连名字不规范的文件也不动。
  界面上的媒体格与 dna media 都走这一条，所以守卫只需要一处"
```

## ② 后端：未生成产物的骨架 + 抬头来源

```bash
git add src/dna/produce/documents.py src/dna/produce/generate.py \
  src/dna/produce/__init__.py src/dna/produce/skeleton.py \
  tests/produce/test_skeleton.py tests/produce/test_service.py
git commit -m "feat(produce): 未生成时给出可手写的空稿骨架

- 需求：四格产物也『双击后可编辑』。**不预写占位文件**——produce 默认复用
  已有产物，先写一份占位稿会让『生成』变成空操作，产出一个看起来成功、
  内容却是模板的稿子。骨架只显示在编辑器里，保存那一刻才落盘
- 排版与生成路径共用 documents.spoken_block：不一致的后果很安静——
  文件看着正常、格子显示已生成，只有合成音频时才发现念出来是空的
- 音频与长视频没有骨架（二进制 / 发言人分轮在 JSON 边车里）
- generate.py 抬头改用 display_source，人工创作的文章不再把伪 URL 印进产物"
```

## ③ 前端：新建按钮、文章面板、媒体格合规化

```bash
git add frontends/nicegui_app/main.py frontends/nicegui_app/theme.py \
  frontends/nicegui_app/ledger_table/cells.py frontends/nicegui_app/ledger_table/state.py \
  frontends/nicegui_app/text_editor.py frontends/nicegui_app/detail_panel.py \
  frontends/nicegui_app/article_panel.py frontends/nicegui_app/actions/__init__.py \
  frontends/nicegui_app/actions/custom.py frontends/nicegui_app/actions/editing.py \
  frontends/nicegui_app/actions/paths.py tests/frontends/test_custom_article_gui.py
git commit -m "feat(gui): 右上角「新建」+ 文章面板 + 媒体格顺手整理素材

- 新建：建完回到第 1 页并清掉搜索框，新文章是最新的所以一定看得见；
  **不静默清掉来源/状态筛选**（那是用户自己设的，替他清掉比被筛掉更困惑）
- 点标题格展开『文章』面板：标题 / 正文双击编辑、媒体一节说明素材放哪。
  新增这个面板而不是塞进 detail_panel——那个面板每件事都围绕**一份稿子**，
  在『总结』格里放一个改标题的框没有道理
- **抓取来的文章在面板里只读**，并写明该走 dna refetch / dna sync，
  不摆一个必然会失败的编辑框
- 媒体格：可点即打开文件夹，并顺手跑合规化再报告（含『认不出哪些』）。
  静默改名会让人在发布时才发现文件名变了
- 媒体格对人工创作的文章**始终可点**（空目录是『把素材拷进来』唯一的入口）；
  抓来的文章保持『空目录不返回』的老规矩
- text_editor 抽出 render_field：『双击→文本框→失焦保存』只有一份实现，
  产物正文与文章字段共用
- 展开面板的键改成字符串（产物格用 ProductionKind 的值，标题格用 ARTICLE_CELL），
  刷新后才能按原样重开"
```

## ④ 命令行对等

```bash
git add frontends/cli/cmd_articles.py tests/frontends/test_cli_smoke.py
git commit -m "feat(cli): dna new / dna media（与界面同一批后端函数）

- dna new：新建空白文章，--title / --body 可直接给内容
- dna media：整理素材文件夹并逐条报告（不删除、不覆盖已有出处）
- test_cli_smoke 的命令清单同步登记两个新命令"
```

## ⑤ 测试隔离加固 + ruff

```bash
git add tests/conftest.py tests/frontends/test_gui_opt.py
git commit -m "fix(test): 隔离夹具扩到 dna.* 模块；修 ruff PIE807

- patch_actions_settings 原先只替 frontends.nicegui_app.actions.* 里的
  get_settings，而 dna.store.custom_article 这类模块**各持一份绑定**。
  后果不是测试失败，而是**往用户真实的 data/dna.db 里插文章**且全程不报错
  ——2026-10-04 真的这样污染过一次（9 行 + 9 个目录，已手工清掉）。
  现在凡是持有 get_settings 的 dna.* / actions.* 模块一起替
- test_gui_opt：lambda: [] → list（ruff PIE807）"
```

## ⑥ 文档 + 准则同步

```bash
git add CLAUDE.md docs/00_STAGE_SUMMARY.md docs/03_unit_tests.md \
  docs/04_architecture_frontends.md docs/04_architecture_src.md \
  docs/05_output_spec.md docs/06_prompt_spec.md docs/07_db_schema.md \
  docs/11_article_store_guide.md docs/13_workbench_guide.md docs/14_tts_guide.md \
  docs/git_commands.md
git commit -m "docs: 新建自己的文章 / 素材合规化 / 中视频改名；准则与护栏改为实际状态

- 13 增『新建自己的文章』与骨架一节，媒体格那张表写清四件事与两条硬规矩
- 11 增 dna new / dna media 两节（为什么占位标题必须够长）
- 05 记人工创作的文章与常规布局的四处差异（伪 URL、粘贴区、空素材目录、
  NN_custom 命名）
- 00_STAGE_SUMMARY §〇 记当前分支与**基线里那 2 个既有失败**：
  tests/narration/test_script_builder.py 断言提示词含『200~250 字』，
  而 config/prompts/shortvideo.zh.md 写的是『正文字数在 … 之间』。
  两者取其一，但提示词是用户手工调的，不擅自回改
- 00_STAGE_SUMMARY §〇 顺手记：两份计划正文（opt-gui / sequential-fluttering-wren）
  已从 ~/.claude/plans/ 丢失，指针变断链 → 计划正文优先写进仓库
- CLAUDE.md 环境节：解释器路径改成本机的（原为原作者机器），补
  `PYTHONIOENCODING=utf-8`（否则 check.py 在 GBK 控制台上自己崩）
- CLAUDE.md 自动反馈节：**改掉一张假话表**——.claude/settings.json 是 Claude Code 的
  hook 协议，DSH 不读；实测一轮改了 80+ 文件，ruff 自动检查与测试包提示一次都没出现。
  现状写成 DSH 插件 dsh-plugin-dna-guard（挂 ctx.tools.guard，拦 git commit/push、
  git add -A、pytest -m live），并写明「哪些仍然只能靠自觉」
- .claude/hooks/python-path.txt 已补上（不入库，.gitignore 第 44 行已忽略）"
git status --short   # 应只剩 config/profile.yaml 与 .claude/ 下的本机文件
```

## 收尾

```bash
$env:PYTHONIOENCODING = "utf-8"    # 本机控制台是 GBK，不加会在打印结果时崩
python tools/check.py               # ruff + 全量 pytest + dna doctor；唯一的闸
git push -u origin custom_article
```
