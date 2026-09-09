# 04 前端架构 / Front-end architecture（`frontends/`）

> 人工查阅与修改用的地图。后端在 [`04_architecture_src.md`](04_architecture_src.md)。
>
> 找不到东西时先看最后一节的 [症状 → 文件](#症状--文件)。

---

## 前端的三条规矩

**1. 前端不写业务逻辑。**
`dna produce <id> -k narration` 与工作台点「重做」**调的是同一个**
`dna.produce.service.produce()`。所以两个前端不可能各坏一套——
一个 bug 只需要修一处，一个护栏（比如「不加 force 不重复计费」）也只需要写一处。
前端只负责：收参数 → 调后端 → 把结果画出来。

判断标准：**这段代码换到另一个前端里要不要重写？** 要，就说明它在错的层。

**2. 依赖单向：`frontends → produce → narration/pipeline → … → core`。**
前端可以 import 任何后端模块；**后端永远不 import 前端**。

**3. GUI 的每一次 LLM / TTS 调用都必须过 `nicegui.run.io_bound`。**
同步调用会冻住整个页面十几秒，看起来跟崩了一模一样。

---

## `frontends/cli/` —— 命令行（1060 行，18 个命令）

### 命令 → 后端 → 费用

| 命令 | 落到后端的哪个函数 | 费用 |
|---|---|---|
| `version` | —— | 免费 |
| `doctor` | `core.doctor.run_all` | 免费 |
| `config` | `core.config.get_settings`（**密钥打码**） | 免费 |
| `sources` | `core.config.load_sources` | 免费 |
| `fetch` | `store.intake_sources` | 免费（网络） |
| `add <urls>` | `store.intake_urls` | 免费（网络） |
| `list` | `store.Ledger.list` | 免费 |
| `show <id>` | `store.Ledger.get` | 免费 |
| `sync <id>` | `store.sync_manual_body` | 免费 |
| `refetch <id>` | `store.refetch_article` | 免费（网络） |
| `probe <url>` | `sources.discover.discover_feeds` | 免费（网络） |
| `stats` | `store.Ledger.count_by_*` | 免费 |
| `issue [date]` | `store.load_issue` / `refresh_references` | 免费 |
| `migrate-layout` | `store.plan_migration` / `migrate` | 免费 |
| `tts` | `tts.get_tts` + `supervisor.ensure_service` | 免费（本地，慢） |
| `gui` | `frontends.nicegui_app.main.run` | —— |
| **`prompt`** | `produce.prompt_lab.render` / `.run` | **默认免费**；`--run` 计费 |
| **`produce`** | `produce.produce` / `produce_all` | **计费**（音频除外） |
| **`digest`** | `pipeline.run_daily` + `store.save_issue` | **计费**（`--dry-run` 免费） |

只有 `produce`、`digest` 和 `prompt --run` 会花钱。`digest --dry-run` 停在费用分界线上：
先看选题，再决定要不要付钱。

### 三个花钱命令的护栏（改动时不要拆掉）

**`produce`**
- 已有产物不加 `--force` 直接复用，一次 LLM 都不调
- `--force` 时**不走 LLM 缓存**：缓存故意不设过期、键是完整提示词，
  照常走的话重做会拿回一模一样的旧答案
- `longform` **不在 `--all` 里**（一篇 5~9 次调用），必须显式 `-k longform`
- `--lang en` 也不在 `--all` 里：多数文章不需要英文版，跟着批量跑等于每篇翻倍
- 三种 `*_audio` 是本地 TTS，一分钱不花但很花时间（RTF ≈ 2.5），同样不进 `--all`
  ——理由是时间不是钱

**`digest`**
- `--dry-run` 在付钱之前把选题打出来
- `--no-cache` 才绕过缓存

**`prompt`**
- **默认不发任何请求**，只把提示词渲染出来
- `--run` 之前打印预估调用次数并交互确认（`--yes` 跳过）
- `--run` **不写文件也不记台账**：调试跑十次不该留十份垃圾。正式产出用 `dna produce`

### 内部 helper

| 函数 | 职责 |
|---|---|
| `_resolve_article(prefix)` | **按 id 前缀查文章**——所有带 `<id>` 的命令都用它，8 位即可 |
| `_status_label(status)` | 状态着色 |
| `_render_intake(result)` | 渲染入库结果（采集/过滤/抓取/失败的分项统计） |
| `_preview_collection()` | `fetch --dry-run` 的预览，不写任何文件 |
| `_slug_id(feed_url)` | `probe` 里由 feed 地址猜一个源 id |

**改这里要注意**：
- **密钥必须打码**（`config` 命令）——用户会截终端图。
- `probe` 打印 YAML 片段时用 `markup=False`：`[tech, cn]` 这种 YAML 列表
  会被 rich 当成标记语法吞掉。`prompt` 打印提示词时同理。

---

## `frontends/nicegui_app/` —— 台账工作台

`dna gui` 启动。**一行一篇文章，一列一种产物，每格都能单独重做。**

### 文件职责

| 文件 | 行数 | 职责 |
|---|---|---|
| `main.py` | 192 | 装配页面：顶栏 + 筛选 + 表格；`run()` 启服务 |
| `ledger_table.py` | 552 | 表格：表头、行、产物格、格子状态与配色、发起生成 |
| `detail_panel.py` | 542 | 展开面板：语言开关、这一版的元信息、修改指令、下载/合成/重做 |
| `actions.py` | 652 | **界面动作**：读数据、跑生成、TTS 交接、打开目录 |
| `import_dialog.py` | 139 | 链接导入对话框 |
| `audio_progress.py` | 129 | 音频合成的进度浮窗 |
| `theme.py` | 303 | 主题与列宽；`short_title()` 截断过长标题 |

### `actions.py` —— 界面与后端之间的唯一一层

界面代码只调这里的函数，不直接调后端。

| 函数 | 说明 |
|---|---|
| `load_rows()` | 读表格数据：一次查一页，**产物矩阵一次查完**（逐格查是几百次往返） |
| `run_production()` | **在后台线程里**跑生成（`run.io_bound`） |
| `audio_estimate_seconds()` | 这一格音频要等多久（RTF ≈ 2.5，长文案约 37 分钟） |
| `last_instructions()` | 上一版是带着什么额外要求生成的 |
| `preview_links()` / `import_links()` | 粘贴一段文本 → 认出链接 → 入库 |
| `open_tts_workbench()` / `collect_tts_handoff()` / `import_tts_handoff()` | 把稿子交给 TTS 图形界面精修，出活了收回来 |
| `production_text()` / `production_file()` / `production_sidecar()` | 读回产物供预览与下载 |
| `article_directory()` / `media_folders()` / `open_in_file_manager()` | 打开素材目录 |
| `longform_estimate()` / `cache_status()` | 长文案时长预估 · LLM 缓存命中率 |

`RowView` 是表格一行的视图模型：`article` + `productions[(kind, lang)]` + `is_new`。

**改这里要注意**：
- **每一次 LLM / TTS 调用都要 `run.io_bound`。** 同步调用冻住整个页面十几秒。
- `open_in_file_manager()` 先看 `_server_is_local()`：服务端不在本机时打开的是
  服务器上的目录，不是用户的——那不是用户要的。

### `ledger_table.py` —— 花钱的按钮都在这里

| 函数 | 说明 |
|---|---|
| `render_table()` / `_render_row()` / `_render_kind_cell()` | 渲染 |
| `_cell_state(record)` | 格子的形状、数值与配色（含**失败**状态） |
| `_cell_tooltip()` | 悬停详情 |
| `_launch()` / `_run()` | 发起一次生成并把结果告诉用户 |
| `_ask_audio()` | 超过 `AUDIO_CONFIRM_SECONDS = 300` 的合成先确认耗时 |
| `_ask_longform()` | **长文案的形式选择与费用确认** |

**改这里要注意**：
- **失败的格子要看得出是失败，不是「未生成」。** 显示「未生成」的话人会以为
  没跑过，再点一次再失败一次，每次都付钱。
- `_ask_longform()` 是长文案唯一的费用闸门（一篇 5~9 次调用）。
- 长音频要 `_ask_audio()`：不花钱但要跑几十分钟，没有确认人会以为界面挂了。

### `detail_panel.py` —— 单格详情与重做

`render()` 画一格的详情；`effective_instructions()` 算出这次真正要发出去的额外要求。

`_watch_handoff()` 盯 TTS 交接单：`_WATCH_INTERVAL = 4.0` 秒一次，
上限 `_WATCH_LIMIT`（90 分钟）——长文案音频真的要跑这么久。

**改这里要注意**：`_render_language_toggle()` 的中/英是**同一产物的两个版本**
（schema v4：语言是产物的一个维度，不是新的 kind），切换语言不是切换产物类型。

---

## 症状 → 文件

| 症状 | 看这里 |
|---|---|
| 点一下界面卡十几秒 | 漏了 `nicegui.run.io_bound`（`actions.py` / `ledger_table.py`） |
| 点重做花了钱 / 没花钱 | 后端 `produce/service.py::produce` 的 `force` 分支 |
| 格子显示「未生成」但确实跑过 | `ledger_table.py::_cell_state` + 后端要记失败行 |
| 长文案点一下就扣了一大笔 | `ledger_table.py::_ask_longform` 的确认没生效 |
| 「打开文件夹」打开的不是我的目录 | `actions.py::_server_is_local` |
| 中/英切换像是切换了产物类型 | `detail_panel.py::_render_language_toggle` |
| TTS 精修的产物收不回来 | `actions.py::collect_tts_handoff` + `_watch_handoff` 的轮询上限 |
| 标题在表格里被截断 | `theme.py::short_title` / `TITLE_MAX_CHARS` |
| YAML 片段在终端里少了半截 | CLI 打印时缺 `markup=False`（rich 吞掉了 `[a, b]`） |
| `dna xxx <id>` 说找不到文章 | `cli/main.py::_resolve_article`，id 前 8 位就够 |
| 改了提示词，界面里没变化 | `dna prompt <id> -t <task>` 看真正发出去的那一份 |

---

## 相关文档

- [`04_architecture_src.md`](04_architecture_src.md) —— 后端每个模块
- [`13_workbench_guide.md`](13_workbench_guide.md) —— 工作台的使用说明
- [`14_tts_guide.md`](14_tts_guide.md) —— TTS 与交接流程
- [`06_prompt_spec.md`](06_prompt_spec.md) —— 提示词（正文在 `config/prompts/`）
