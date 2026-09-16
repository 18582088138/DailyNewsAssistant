# 待执行的提交命令

> **这个文件是一次性的。** 每次覆写，只留当前这一组；上一组的内容在 `git log` 里。
>
> **覆写前必须先 `git log` 核对上一组是否已执行。** 本次核对结果（2026-09-11）：
> 上一组「批次 0.1 · 补护栏的洞」已在 `HEAD`（`adb84dc`），`.claude/hooks/` 与
> `tests/hooks/` 逐项确认入库，故安全覆写。
>
> `git add` 一律写明路径，**不用 `-A`**：`.env` 有密钥、`outputs/` 有几百 MB 产物。
> hook 会拦下 `git commit` / `git push` —— 这些命令由人工执行。
>
> ⚠️ **不要提交** `.claude/optimization_brief.md` —— 那是本轮优化的要求原文，
> 全部批次做完会删掉。

分支：`DNA_v0.1` · 本组内容：**批次 1~5 全部**
（1 docs · 2 代码清理与配置 · 3 代码拆分 · 4 skill/memory · 5 规则收尾）

> 一共 15 组提交。**按顺序执行**：③ 之后代码才编译得过（删字段与改消费者必须
> 在同一个提交里），⑩ 的安全网必须早于 ⑪~⑭ 的拆分。
> 每一组之后我都跑过 `python tools/check.py`，全绿。

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant
```

---

## 批次 1 · docs

### ① 文档导览 + 补上 check_docs 的盲区

```bash
git add docs/00_INDEX.md
git add tools/check_docs.py
git add tests/tools/
git commit -m "feat(docs): 加文档导览；check_docs 补上反引号引用的盲区

- 新增 docs/00_INDEX.md：一行一份，写明「什么时候加载它」。CLAUDE.md 第一句
  就在引用它，而这个文件此前并不存在——按需加载一直是句空话
- check_docs 原来的正则只认 [x](y)，反引号里写成纯文本的 docs/*.md 抓不到；
  02_development_plan 就靠这个盲区藏了一条指向 docs/04_architecture.md（单数、
  不存在）的引用
- 判据收紧到 docs 自己的命名约定：产物文件也叫 .md（article.md、summary.zh.md、
  _references.md），一起查只会天天误报，护栏喊狼来了人就不看红灯了
- tests/tools/test_check_docs.py 钉住两侧：该抓的抓到、产物名与占位符不误报"
```

### ② 删腐烂的数字与自相矛盾的章节

```bash
git add docs/00_STAGE_SUMMARY.md
git add docs/03_unit_tests.md
git add docs/04_architecture_src.md
git add docs/04_architecture_frontends.md
git commit -m "docs: 删掉会腐烂的数字与自相矛盾的章节

- 03_unit_tests 从按阶段记流水账改为只写三件不腐烂的事（怎么跑、什么标记、
  怎么写才钉得住）。14 处冻结计数删掉，清单交给 tools/check.py --list 现算；
  「断言意图不断言结果」「边界值只能证明边界挪了」这些换来的写法留下
- 00_STAGE_SUMMARY 删 11 处测试数、删与 05_output_spec 自相矛盾的 §七
  （同一份文档 §七说 topics/ 是已定方案、§四说已取消），修 5 条漏了 issues/
  前缀的断链，顶部加「进行中」记住主线
- 04_architecture_* 删掉全部行数列（45 条声明里 19 条已经错），补上漏掉的
  core/config_edit.py、store/delete.py、tts/console.py"
```

### ③ 消除跨文件的成段重复（子代理查出 18 组）

```bash
git add docs/01_research.md
git add docs/02_development_plan.md
git add docs/05_output_spec.md
git add docs/07_db_schema.md
git add docs/12_digest_guide.md
git add docs/13_workbench_guide.md
git add docs/14_tts_guide.md
git commit -m "docs: 消除跨文件重复，每个概念只留一处权威

- 13_workbench_guide 压掉约三成：提示词规格归 06、落盘归 05、库表归 07、
  前端实现归 04，这份只留「界面上能做什么、会发生什么」
- 05_output_spec 自称落盘唯一权威却漏了整类产物，补上 .wav / .srt / tts/
  （含 seg_<序号>_<角色>.wav 的命名与「可安全删除」）
- 01_research 的选型表把 OpenRouter/DeepSeek 主备写反了
- 02 §4 已取消的 topics/ 与 _history/ 布局删掉（§9.2 §9.3 的裁决记录保留，
  它们是 05 反向引用的唯一权威）；§6.1 的 productions 表改为指向 07，
  并删掉与 §9.3 直接矛盾的「旧产物移入 _history/」
- 02 §11 的文档清单整张删掉——已被 00_INDEX 取代，两份清单必然漂移
- 14_tts_guide 此前零处引用 issues/，把 009、012 接回来
- 修掉 RTF 的自相矛盾：2.5 抄了五份，而 14 已实测 ≈13（界面预估少报五倍）"
```

---

## 批次 2 · 代码清理与配置

> **顺序有讲究**：③ 之后代码才编译得过（删字段与改消费者必须同一个提交）。
> 每一组之后都跑过 `python tools/check.py`，全绿。

### ④ ruff 规则集补两条豁免 + 行宽按语言现实调整

```bash
git add pyproject.toml
git commit -m "chore(lint): typer 写法与测试行宽按实际情况放行

- flake8-bugbear.extend-immutable-calls 放行 typer.Argument/Option：
  \`x: str = typer.Argument(...)\` 是 typer 的标准写法，按 B008 改写只会给
  每个命令加一段样板代码，且与 typer 文档不一致
- tests/ 放行 E501，两个具体理由：每个测试文件第 5 行是**完整复测命令**
  （CLAUDE.md 的要求），必须能整行复制；断言里的中文样本是真实文案片段，
  折行会让「被测的到底是哪段字」变模糊。生产代码仍守 100 列
- tests/ 放行 S105/S106（测试里的「密钥」是 fake-secret 这类假值）
- actions.py / supervisor.py 放行 S603/S606/S607：调系统文件管理器、
  拉起 TTS 服务就是它们的本职"
```

### ⑤ 删死码（169 行，零调用者）

```bash
git add src/dna/produce/__init__.py
git add src/dna/produce/service.py
git add src/dna/tts/__init__.py
git add src/dna/tts/client.py
git add src/dna/tts/supervisor.py
git add frontends/cli/main.py
git commit -m "refactor: 删掉 169 行零调用者的死码

- produce.service.import_audio（86 行）：\"把 TTS 界面精修的音频收回来\"，
  那套交接单的路早已删除，连测试都不调它
- tts.client 的 handoff / handoff_state / download（50 行）：同一条已废弃的路
- tts.supervisor 的 ensure_gui / gui_url（33 行）+ Settings 的 TTS_GUI_PATH /
  TTS_GUI_URL：那个独立 GUI 进程已经不存在，唯一消费者是 dna config 的一行显示
逐一 grep 确认过零调用者（含测试），连同各自的 __all__ 与包级再导出一起清"
```

### ⑥ 修分层违规：core 不再反向依赖 tts

```bash
git add src/dna/tts/doctor.py
git add src/dna/core/doctor.py
git add tests/core/test_doctor.py
git add tests/core/test_layering.py
git add tests/frontends/test_cli.py
git commit -m "refactor(core): TTS 自检搬出 core，铁律改由测试把关

core/doctor.py 里有一行**函数内** \`from dna.tts.client import ...\`，
是全仓唯一的反向依赖。延迟 import 不报错，只是把违规藏起来——藏了小半年。

- check_tts_service 搬到 src/dna/tts/doctor.py（它所属的那一层）
- run_all(extra=[...]) 由前端把上层检查交进来；前端是唯一有权认识所有层的地方
- 新增 tests/core/test_layering.py：按 CLAUDE.md 的铁律逐层断言，
  **连函数内 import 一起查**（那正是上一次违规的藏身之处）"
```

### ⑦ 配置改了不生效：三条真 bug + 18 条假配置

```bash
git add src/dna/core/config.py
git add src/dna/core/config_edit.py
git add src/dna/core/urls.py
git add src/dna/extract/article.py
git add src/dna/extract/__init__.py
git add src/dna/sources/http.py
git add src/dna/store/intake.py
git add src/dna/produce/tasks.py
git add src/dna/produce/service.py
git add src/dna/tts/base.py
git add src/dna/tts/factory.py
git add src/dna/tts/client.py
git add src/dna/tts/doctor.py
git add src/dna/tts/supervisor.py
git add src/dna/tts/__init__.py
git add src/dna/pipeline/clean.py
git add src/dna/pipeline/summarize.py
git add src/dna/pipeline/flow.py
git add src/dna/narration/script_builder.py
git add frontends/cli/main.py
git add frontends/nicegui_app/main.py
git add frontends/nicegui_app/actions.py
git add frontends/nicegui_app/settings_dialog.py
git add frontends/nicegui_app/detail_panel.py
git add frontends/nicegui_app/ledger_table.py
git add frontends/nicegui_app/tts_panel.py
git add config/profile.yaml
git add tests/core/test_config.py
git add tests/core/test_config_effective.py
git add tests/frontends/test_workbench.py
git add tests/tts/test_tts.py
git add tests/narration/test_script_builder.py
git add tests/pipeline/test_summarize.py
git add docs/issues/014-hardcoded-config-and-fake-settings.md
git commit -m "fix(config): 配置改了不生效——三条真 bug 与 18 条假配置

两条最坏的，共同点是**完全静默**：界面显示保存成功，行为一点没变。

- **存图上限**：profile.yaml 里手填的 100 被写死的 10 压掉。同语义常量有四份，
  且 extract_article 不接这个参数——抽取阶段先按 10 砍一刀，后面给多大都补不回。
  修法是让上限在**抓取前**就定下来，并把 CLI --max-images 的默认值从 10 改成
  None（它优先级最前，写死默认值等于每次 dna fetch 都把 profile 压掉）
- **.env 的四个代理项完全不生效**：没有任何地方把它们写进 os.environ，而 httpx
  走 trust_env。GUI 还在提示「NO_PROXY 必须含 localhost」，而那个行为不存在。
  新增 apply_proxy_env()，CLI 与 GUI 入口各调一次，大小写两份都写
  （httpx/requests/urllib 各认一种），**已在系统环境里的一律不覆盖**
- **log_level / DEFAULT_LANGUAGE / summary_max_sentences** 三条已知假配置：
  前两条接上真实消费者，第三条零消费者（真实约束写在提示词里）连字段、控件、
  单测一并删

另外清掉：Profile.languages、Settings.digest_max_entries（被 Profile 遮住）、
FTP_PROXY、钉钉 3 项、IMAP 4 项、inbox_* 4 项——全是零消费者。
tts_start_attempts 是反向问题（代码读它但不在白名单），补进白名单与界面。

同名不同值的常量：
- MIN_BODY_CHARS 80/40 → clean.py 那个 40 从来没被读过，删；抽取侧改名
  MIN_EXTRACTED_CHARS
- MAX_BODY_CHARS 6000/3000 → 改名 SCRIPT_BODY_LIMIT / SUMMARY_BODY_LIMIT，
  并提为 profile 的两个字段（截短的代价见 issues/008：被切掉的尾部含关键局限）
- MAX_REWRITES → 提为 copy_max_rewrites / summary_max_rewrites。
  ⚠️ **行为变更**：文案回炉上限按 profile.yaml 的注释取 2（代码原先是 1），
  即字数不达标时多一次计费调用。不想要就把那一行改成 1
- RTF_ESTIMATE 2.5 → Settings.tts_rtf_estimate，默认按 CPU 实测改成 13
- is_local_url 两套语义 → 统一到 core/urls.py（子串版会把
  http://localhost.example.com 也认成本机）
- mask_secret 两份 → CLI 复用 config_edit 那份

新增 Profile.tuning（进阶旋钮，只在文件里改、不进设置面板）：选题五个权重与
三个饱和点与两个扫描窗口、判重阈值与取样长度、长文案准入与章节数与扩写系数、
趋势门槛。**语速与停顿刻意不放进去**——字数窗口是按当前语速校准的，
动它等于推翻校准（issues/008、009），半接的旋钮就是新的假配置。

验收：tests/core/test_config_effective.py 13 个用例。其中「假配置清零」那条是
遍历模型字段的静态断言，验过有牙（本批删掉的 5 个字段逐个会被判孤儿）；
它第一版被一句提到 profile.languages 的注释骗过去了，所以现在先剥行注释。
Tuning 单独点名——只查 Profile 的话嵌套模型整体算「被引用过」。"
```

### ⑧ ruff 213 → 0

```bash
git add src/ frontends/ tests/
git commit -m "style: ruff 213 项清零

95 项自动修（import 排序、__all__ 排序、无用 noqa）。其余逐项判断：
- B008 typer 写法与 tests 的 E501 走 pyproject 豁免（理由写在配置里）
- 生产代码的 32 处 E501 按语义折行；core/models.py 是跨层数据契约，
  按规矩保留中英双语，所以是折行不是删字
- SIM105/SIM117/RUF046/PIE810/ISC004/FURB162/RUF007 等逐处改写
- pipeline/source.py 的 try-except-continue 补上一行 debug 日志：
  媒体记录坏掉不该毁掉整篇正文，但要留痕，否则「图少了」查不到原因
- tts/factory.py 的 \`language = 'chinese' if lang == 'zh' else 'english'\`
  反过来写：任何没归一化的空值都会静默变成英文音色，中文稿被用英文发音念出来
  而日志里什么都没有"
```

---

## 批次 5 · 规则收尾

### ⑨ 配置铁律与「一个概念一处权威」写进 CLAUDE.md

```bash
git add CLAUDE.md
git commit -m "docs(claude): 加配置铁律；架构铁律按真实依赖改写

- 新增「配置铁律」一节：可调参数一律从 env/config 读；**新增字段必须同时接上
  消费者**（半接的旋钮就是假配置，历史上有 18 个）；兜底常量用 field_default
  指向模型；进阶旋钮放 Profile.tuning 不进面板。点名两条静态测试作为闸门
- 架构铁律原文按不存在的 apps/render/inbox 三层写，改成实测的依赖图，
  并点名唯一违规（已在批次 2 修掉）
- 成本纪律补一条「一个概念只在一处写权威」：实测同一段内容曾被抄进 3~4 份
  文档，改一处等于留下三份矛盾的旧版本
- 第 7 条「计划必须落盘」、第 8 条「没有 CI 兜底」
- TTS 那条删掉「约 6 分钟 / 约 37 分钟」——都是从已被推翻的 RTF 2.5 推出来的"
```

---

## 批次 3 · 代码拆分

> **手法**：按**行区间搬运**，不重新生成代码文本。照抄五千多行到输出里既贵又
> 容易抄错；脚本只负责写新文件的 docstring 与 import 段。
>
> **先补安全网再拆**：两套冒烟测试（见 ⑩）在拆分之前就位，拆的过程中它们抓到了
> 四类真问题（见下面每条的说明）。

### ⑩ 拆分前的两套安全网

```bash
git add tests/frontends/test_cli_smoke.py
git add tests/frontends/test_render_smoke.py
git add tests/conftest.py
git commit -m "test(frontends): 补两套冒烟测试，作为拆分前的安全网

拆 cli/main.py（1332 行、20 个命令）与四个前端大文件之前，这两类错是没有
任何测试能发现的：漏注册一个命令、某个模块 import 环了、控件建了但 slot 不对。

- test_cli_smoke：20 个子命令逐个 --help + 「一个命令都没少」。
  --help 走完「模块导入 → typer 注册 → 参数声明」整条路，零费用零落盘。
  命令清单**写死在测试里**，不从 app 自己身上取 —— 否则命令掉一个、两边一起
  少一个，测试照样绿
- test_render_smoke：每个对话框/面板在无头环境里真的建一遍。
  test_workbench.py 有八十多个用例但**一个都没渲染过**，于是 issues/013 那个
  Invalid value（select 候选里有空串，渲染时才抛）这类错全漏在网外。
  不需要浏览器：NiceGUI 的 Client 能当上下文管理器用，元素建在内存里
- conftest 加 patch_actions_settings 夹具：actions 拆包之后每个子模块各有一份
  get_settings 绑定，只打 facade 是没用的 —— 而报错长得像「测试写错了」"
```

### ⑪ 拆 CLI：1332 行 → 9 个文件

```bash
git add frontends/cli/
git add tests/frontends/test_cli.py
git commit -m "refactor(cli): main.py 1332 行拆成按命令分组的 9 个文件

- app.py 单独放 typer.Typer 实例：各 cmd_* 都要 import 它做装饰，而 main 又要
  import 全部 cmd_* 才能完成注册，留在 main 里就成环
- cmd_env / cmd_articles / cmd_produce / cmd_publish / cmd_admin / cmd_prompt
  按命令分组；render.py 放表格与进度的渲染辅助（不含命令）
- main.py 只剩 48 行：import 各 cmd_*（导入即注册）+ 再导出 app。
  app 必须留在这里 —— pyproject 的 entry point 钉的是 frontends.cli.main:app
- 最大的文件从 1332 降到 346 行"
```

### ⑫ 拆后端：produce / ledger / config

```bash
git add src/dna/produce/
git add src/dna/store/ledger.py src/dna/store/ledger_models.py
git add src/dna/store/production_queries.py
git add src/dna/core/config/
git add tests/core/test_config.py tests/produce/
git commit -m "refactor: 拆 produce.service / store.ledger / core.config

- produce/service.py 990 → 426：results（两个纯数据结构）/ generate（文本产物）
  / audio（音频那条路）/ badges（NEW 判定）各自独立。read_production 跟着
  generate 走 —— 它留在 service 里会让 generate 反过来 import 编排层
- store/ledger.py 728 → 382：ledger_models（行映射与 WHERE 片段，零 SQL 执行）
  + production_queries（productions 表，做成 **mixin** 被 Ledger 继承）。
  mixin 而非独立类：两张表共用同一个连接与事务边界，拆成两个对象就得把连接
  传来传去，或者开两条连接 —— 后者在 SQLite 上会互相锁
- core/config.py 752 → 包（settings 管 .env / profile 管 YAML + facade）。
  facade 是刻意的：全仓四十多处 from dna.core.config import，而「配置」对调用方
  本来就是一个整体概念

⚠️ **拆 config 时 dna doctor 抓到一个真 bug**：PROJECT_ROOT 原先靠
parents[3] 数层级，文件深一层就指到了 src/ —— config/、data/、outputs/ 全找错
地方，而代码一个字都没动。改成**向上找 pyproject.toml**，层级计数被目录结构
的任何改动悄悄改坏，找标记文件不会。"
```

### ⑬ 拆前端：actions / ledger_table / tts_panel / settings

```bash
git add frontends/nicegui_app/
git add tests/frontends/test_workbench.py
git commit -m "refactor(gui): 拆掉四个前端大文件，最大的从 1380 降到 316

- actions.py 1380 → 包（rows / run / intake / tts_actions / paths / batch /
  reveal / env / progress + facade）。facade 是显式边界：界面各处都写
  actions.load_rows(...)，属性式调用就是这个模块对外的形状
- ledger_table.py 1007 → 包（state / shell / cells / run / batch）。
  跨刷新状态单独一个文件：展开哪一格、勾了哪几行、滚回哪一行都必须保留
- tts_panel.py 850 → 包（model / shell / view / edit / synth）。
  **view 与 edit/synth 本来会互相 import**：重画在 view，而每段的编辑与合成
  改完状态都要重画。解法不是懒 import，是在 _Panel 上开一个 refresh 回调槽，
  由 shell 装配时注入 —— 依赖变成单向 shell → view → edit/synth → model
- settings_dialog.py 623 → 拆出 TTS 子页与共用控件（settings_widgets）

测试侧的连带改动都是「打桩要打在名字真正所在的模块上」：facade 的再导出是
绑定的**副本**，打 facade 不影响子模块。这一点很容易被误读成测试写错了，
所以 conftest 里那个夹具的注释专门写了它。

这一组还带着批次 6 里前端那一半的改动（语言哨兵入口归一化 + 设置面板滚动），
见 docs/issues/015 —— 同一批文件，按路径分不开。"
```

### ⑭ 拆剩下四个刚过线的

```bash
git add src/dna/narration/
git add src/dna/store/article_store.py src/dna/store/article_render.py
git add src/dna/store/intake.py src/dna/store/intake_engine.py
git add src/dna/store/intake_models.py src/dna/store/__init__.py
git add tests/store/ tests/narration/
git commit -m "refactor: 拆最后四个超线文件，行数门清零

- script_builder 544 → 429 + script_engine（生成 → 数字数 → 回炉 那台引擎）
- longform 515 → 429 + longform_models（专题与访谈产出同一个结构）
- intake 517 → 377 + intake_engine（四个入口共用的那一段）+ intake_models
  （结果类型，两边都要用，放任何一边都会互相 import）
- article_store 501 → 331 + article_render（写出的两份 Markdown 与读回那一侧；
  写与读必须对得上，分两个文件迟早漂开）

python tools/check.py --max-lines 500 → 通过（此前 12 个文件超线）"
```

---

## 批次 6 · 用户验证时报的三个 bug

**先读这一段再跑上面的 ⑫⑬。** 前两个 bug 改的文件，跟批次 3 拆分改的
**是同一批文件**（`frontends/nicegui_app/`、`src/dna/produce/`、
`tests/produce/`、`tests/frontends/test_render_smoke.py`）。同一个文件里
两拨改动按路径分不开，只能靠 `git add -p` 逐块挑 —— 不值得。
所以**那部分代码会落在 ⑩⑫⑬ 那三个提交里**（⑬ 的信息里已经写明）。
`docs/00_STAGE_SUMMARY.md` 同理，它的 015 那一行会落在批次 1 的 ② 里。
下面 ⑮ 只提交能单独分开的：issue 文档、`src/` 护栏、散文占比那个决定。

改了什么（完整清单，方便你核对覆盖面）：

```
frontends/nicegui_app/  actions/{rows,paths,run,tts_actions}.py
                        detail_panel.py  ledger_table/{run,cells}.py
                        tts_panel/shell.py  theme.py  settings_dialog.py
src/dna/produce/        tasks.py  service.py  generate.py
tests/                  produce/test_lang_sentinel.py（新）
                        frontends/test_render_smoke.py
                        core/test_layering.py（加 src/ 护栏）
tools/check.py          散文占比改成只报不拦，参考线 40%
CLAUDE.md               同上那条规则
```

**另外删掉了两个目录**（未跟踪、被 `.gitignore` 遮着，所以 `git status` 看不见）：
`src/data/logs/dna.log` 与空的 `src/outputs/`。它们是拆 `core/config` 那三十秒里
仓库根指到 `src/` 的残骸，成因早已修掉，详见 issues/015 ③。

验收（都是会从红变绿的节点，不是「我觉得没问题」）：

```bash
PY=/c/Users/test/miniforge3/envs/ov_env_py312/python.exe
$PY -m pytest tests/produce/test_lang_sentinel.py -q     # 哨兵静态规矩
$PY -m pytest tests/frontends/test_render_smoke.py -q    # 两条行为回归
$PY -m pytest tests/core/test_layering.py -q             # src/ 下只有代码
$PY tools/check.py --max-lines 500                       # 全量闸
```

### ⑮ 三个 bug 的记录、`src/` 护栏、散文占比定案

```bash
git add docs/issues/015-language-sentinel-and-dialog-clipping.md
git add tests/core/test_layering.py
git add tools/check.py CLAUDE.md
git commit -m "fix: 语言哨兵漏到下游、设置面板被裁掉、src/ 下落了数据

批次 2 把写死的 DEFAULT_LANGUAGE 换成读配置的 default_language() 之后，
默认参数只能写成哨兵空串（默认值不能在导入时读配置），而只有一部分函数在
入口归一化。三种漏法：当字典键（整张表静默显示「未生成」）、当 LANGUAGE_LABELS
的下标（展开面板 KeyError: ''，于是「文章用不了 LLM 功能」）、跟默认语言比
（中文版被标成「（EN）」）。规矩改成一刀切：lang 默认为空串的函数，入口第一句
必须 normalize_lang()，由 tests/produce/test_lang_sentinel.py 用 AST 把关。

设置面板没滚动条：.wb-dialog 上那句 overflow: auto 永远不触发——Quasar 的
q-panel-parent 是 overflow: hidden，分页比它高就直接裁掉，卡片本身没超高。
改成让分页自己滚（.wb-dialog-body），顺带底栏按钮不再滚走。

src/ 下出现 data/ 与 outputs/：拆 config 包那阵仓库根短暂指到了 src/，成因
（parents[3] 数层级）当时就改成向上找 pyproject.toml 了，这次清掉残骸并加
test_src_下只有代码 盯症状——.gitignore 的 data/ 不带前导斜杠，任何层级都
匹配，所以这类错 git status 永远看不见。

散文占比改成只报不拦，参考线 40%（原计划 25% 是硬门）。理由写在 check.py 的
PROSE_BUDGET：这个项目最贵的 bug 全是「不报错，只是不对」，记着为什么的注释
就是防复发的东西，把比例当硬门第一个被删的就是它们。"
```

### ⑯ 一次性文件本身

```bash
git add docs/git_commands.md
git commit -m "chore(docs): 覆写 git_commands 为批次 1~6"

git status --short
python tools/check.py
```

---

## 不入库、但这几批也改了的东西（批次 4）

**skill 与 memory 都在 `~/.claude/` 下，不随仓库走，无需提交。** 改了这些：

1. **skill `dailynews-dev/SKILL.md`：404 行 → 59 行。** 它原先一触发就把 25KB
   整份塞进上下文，内容是 `docs/04~14` 与 `issues/` 的平铺副本 —— 既违反它服务的
   项目自己定的「按需加载」，又带着冻结的测试数（`813 tests pass`）和只到
   `09_packaging` 的过期 docs 清单，还住在 `~/.claude/skills/` 不随仓库走版本。
   现在只留**怎么工作**（成本纪律、验证分级、提交纪律），项目事实指向
   `CLAUDE.md` 与 `docs/00_INDEX.md`。
2. **`dailynews-project-overview` 记忆里的架构铁律是错的**，照抄的是
   `frontends → apps → narration/render → …`，而那三层根本不存在。
   整份改写为「几条推不出来的选型理由 + 指向仓库」，28 行长行 → 33 行短行。
3. **`config-is-authoritative` 补两条**：兜底常量用 `field_default` 指向模型
   （那一轮外提配置时我自己制造了 6 处「同一个数字写两遍」，正是它要防的）；
   新增字段必须同时接上消费者，并点名那条静态测试。
4. `MEMORY.md` 索引同步。

**验收（批次 4 唯一能换成钱的指标）**：固定上下文（`CLAUDE.md` + skill + memory）
**833 行 → 276 行**，其中每轮都要付费的部分（CLAUDE.md + memory）是 217 行。

## 不入库、但这两批也改了的东西

1. **`config/prompts - Copy/` 已移走**，不是删除：`data/backup/prompts-20260911/`
   （`data/` 已 gitignore）。逐文件核对过——11 份里 9 份只是 CRLF/LF 差异，
   真有差异的 `summarize.md` 与 `shortvideo.zh.md` **方向是正式版更新、Copy 更旧**
   （Copy 还是重构前的散文式要求，正式版已改成「一、硬性约束」结构化写法）。
   没有任何独有价值需要找回，但它未被 gitignore、未跟踪，将来 `git add config/`
   会把它整个带进仓库。
2. 清掉 26 个 `__pycache__` + `.pytest_cache` + `.ruff_cache`。
3. `.claude/optimization_brief.md`：本轮要求原文，临时文件，**不要提交**。

## 验收结果

```
python tools/check.py --max-lines 500   → ruff ✔ · pytest ✔ · doctor ✔ · 行数 ✔
python tools/check_docs.py              → 文档门通过（此前 64 项）
```

| 指标 | 之前 | 现在 |
|---|---|---|
| ruff | 213 项 | **0** |
| 超过 500 行的文件 | 12 个 | **0**（最长 472 行） |
| 固定上下文（CLAUDE.md + skill + memory） | 833 行 | **276 行** |
| `docs/*.md`（不含 `issues/`） | 5325 行 | 约 4400 行 |
| 假配置字段 | 18 个 | **0**（静态测试把关） |
| 反向依赖 | 1 处（`core → tts`） | **0**（静态测试把关） |

| 散文占比 | 计划要求 ≤25%（硬门） | **参考线 40%，只报不拦** ← 已定案 |

## 散文占比：已定案

原计划是「散文（注释 + docstring）占非空行 **≤25%**」当硬门。**改成参考线 40%、
`check.py` 每次都报但不拦**（用户 2026-09-16 定）。

理由落在 `tools/check.py` 的 `PROSE_BUDGET` 注释里，不在这份一次性文件里：
把比例当硬门，第一个被删的就是那些「为什么」，而这个项目最贵的 bug 全是
「不报错，只是不对」（配置被静默忽略、字数预算少要三分之一、重做拿回旧答案），
那些根因记录正是防复发的东西。它也与项目自己那条原则冲突——
**「已经写下的『为什么』，是新增要克制，不是回头删存量」**。

想临时当闸门用还是可以：`tools/check.py --prose-ratio 40`（显式传就会拦）。
当前 38.5%，大户是 `narration/duration.py`(77%)、`core/config/settings.py`(59%)。
