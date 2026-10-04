# 待执行的提交命令

> **一次性文件。** 只留当前这一组；上一组的内容在 `git log` 里。
>
> **覆写前已 `git log` 核对（2026-10-04）**：先前各节均已由人工执行——
> `f7f21eb` 改名 · `b8b87f0` 后端人工创作的文章 · `1db67e3` 素材合规化 ·
> `77353d7` 骨架 · `3df1010` 文档与准则同步 · 测试隔离加固。这些文件在
> `git status` 里已消失，故只留下面 3 组。
>
> `git commit` / `git push` 由人工执行；`git add` 一律写明路径，**不用 `-A`**。
>
> ⚠️ **不要提交** `config/profile.yaml`：那一行改动是人工调的，不属于这一组。
>
> 分支 `custom_article` · 剩余 **15 个文件 / 3 组**，按 ①~③ 顺序执行。

```bash
cd c:/Users/75203/Downloads/xkd/DailyNewsAssistant
```

## ① 前端：新建按钮、文章面板、媒体格合规化

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

## ② 命令行对等

```bash
git add frontends/cli/cmd_articles.py tests/frontends/test_cli_smoke.py
git commit -m "feat(cli): dna new / dna media（与界面同一批后端函数）

- dna new：新建空白文章，--title / --body 可直接给内容
- dna media：整理素材文件夹并逐条报告（不删除、不覆盖已有出处）
- test_cli_smoke 的命令清单同步登记两个新命令"
```

## ③ 清单自身

```bash
git add docs/git_commands.md
git commit -m "docs: 提交清单推进到剩余两组（前端与 CLI）"
git status --short   # 应只剩 config/profile.yaml 与 .claude/ 下的本机文件
```

## 收尾

```bash
$env:PYTHONIOENCODING = "utf-8"    # 本机控制台是 GBK，不加会在打印结果时崩
python tools/check.py               # ruff + 全量 pytest + dna doctor；唯一的闸
git push -u origin custom_article
```
