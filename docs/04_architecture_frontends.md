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
| `main.py` | 327 | 装配页面：顶栏（含 TTS 状态芯片）+ 筛选 + 表格 + 分页；`run()` 启服务 |
| `ledger_table.py` | 1005 | 表格：勾选列、表头、行、产物格、格子状态与配色、批量条、发起生成、操作后回到原位 |
| `detail_panel.py` | 411 | 展开面板：语言开关、这一版的元信息、修改指令、下载/合成/操作台/重做 |
| `actions.py` | 1335 | **界面动作**：读数据、跑生成、批量重抓/删除、配置读写、TTS 状态/分段/生成/写回稿子、打开文件 |
| `tts_panel.py` | 696 | **TTS 操作台**：可编辑分段、三态与缓存、逐段生成/重掷/插事件、整体合成（走 `produce(segments=..., rendered=...)`） |
| `voice_controls.py` | 362 | 一组声音配置控件（**三种模式**/音色/语气或音色描述/参考音频/上传）；**统一配置与每段的单独配置是同一个类的两个实例** |
| `import_dialog.py` | 137 | 链接导入对话框 |
| `source_dialog.py` | 156 | **从订阅导入**：选源 + 最近几天 + 每源上限，不调 LLM |
| `settings_dialog.py` | 612 | **设置面板**：四个子页（内容偏好 / 文案 → `profile.yaml`；TTS / 运行设置 → `.env`） |
| `audio_progress.py` | 129 | 音频合成的进度浮窗 |
| `intake_progress.py` | 59 | 批量采集的进度提示条（工作线程写字典、UI 轮询） |
| `theme.py` | 410 | 主题与列宽；`short_title()` / `short_url()` 截断 |

操作台的后端能力在 `src/dna/tts/console.py`（118 行）：音色表、参考音频候选与解析、
单段试听。**这三件都不能在前端自己算** —— 音色表在服务端，参考音频的相对路径按
`data_dir` 解析（不是仓库根），前端另写一套的结果是「界面上看着有、合成时找不到」。

### `actions.py` —— 界面与后端之间的唯一一层

界面代码只调这里的函数，不直接调后端。

| 函数 | 说明 |
|---|---|
| `load_rows()` | 读表格数据：一次查一页，**产物矩阵一次查完**（逐格查是几百次往返） |
| `run_production()` | **在后台线程里**跑生成（`run.io_bound`） |
| `audio_estimate_seconds()` | 这一格音频要等多久（RTF ≈ 2.5，长文案约 37 分钟） |
| `last_instructions()` | 上一版是带着什么额外要求生成的 |
| `preview_links()` / `import_links()` | 粘贴一段文本 → 认出链接 → 入库 |
| `production_text()` / `production_file()` / `production_sidecar()` | 读回产物供预览与下载 |
| `article_directory()` / `media_folders()` / `open_in_file_manager()` | 打开素材目录**或文件**（`article.md` 用默认编辑器打开） |
| `body_file()` / `media_target()` | 表格里正文格 / 媒体格点开的目标；没东西可开时返回 `None` |
| `batch_refetch()` | 逐篇重抓，**每篇一次 `io_bound`**，进度回调报 `i/N` |
| `plan_batch_delete()` / `batch_delete()` | 先算再删：确认框显示的数字就是 `plan_delete()` 算出来的 |
| `target_window()` / `over_target()` | 字数窗口 · **渲染时现算**是否超长（改了 profile，历史产物跟着重判） |
| `source_options()` / `import_from_sources()` | 从 `load_sources()` 取启用的源 · 采集（不调 LLM） |
| `profile_values()` / `save_settings()` | 设置面板的读与写；写走 `core/config_edit.py` |
| `ENV_FIELDS` / `env_groups()` / `env_display()` / `env_shadowed()` | `.env` 白名单的呈现：分组、打码、环境变量遮盖判定 |
| `longform_estimate()` / `cache_status()` | 长文案时长预估 · LLM 缓存命中率 |

`RowView` 是表格一行的视图模型：`article` + `productions[(kind, lang)]` + `is_new`。

**改这里要注意**：
- **每一次 LLM / TTS 调用都要 `run.io_bound`。** 同步调用冻住整个页面十几秒。
- `open_in_file_manager()` 先看 `_server_is_local()`：服务端不在本机时打开的是
  服务器上的目录，不是用户的——那不是用户要的。
- **`ENV_FIELDS` 只描述「怎么画」，能不能写由 `core/config_edit.ENV_ALLOWLIST` 说了算。**
  两边错开是静默的：少一项 → 界面上根本改不了，而白名单声称可写；多一项 → `save_env`
  只记一条日志就把它丢掉，界面显示「已保存」。所以有一条双向相等的测试锁着。

### `ledger_table.py` —— 花钱的按钮都在这里

| 函数 | 说明 |
|---|---|
| `render_table()` / `_render_row()` / `_render_kind_cell()` | 渲染 |
| `_cell_state(record)` | 格子的形状、数值与配色（含**失败**状态） |
| `_cell_tooltip()` | 悬停详情 |
| `_launch()` / `_run()` | 发起一次生成并把结果告诉用户 |
| `_ask_audio()` | 超过 `AUDIO_CONFIRM_SECONDS = 300` 的合成先确认耗时 |
| `_ask_longform()` | **长文案的形式选择与费用确认** |
| `_SELECTED` / `selected_ids()` / `clear_selection()` | 勾选状态**跨刷新保留**（与 `_OPEN` 同一个做法）；`dict[str, None]` 当有序集合用，确认框里的标题顺序就是勾的顺序 |
| `render_batch_bar()` / `_ask_batch_refetch()` / `_ask_batch_delete()` | 选中 > 0 时出现的批量条与两个确认框 |
| `_render_body_cell()` / `_render_media_cell()` / `_reveal()` | 正文格开 `article.md`，媒体格开 `images/`（没有则 `videos/`） |

**改这里要注意**：
- **失败的格子要看得出是失败，不是「未生成」。** 显示「未生成」的话人会以为
  没跑过，再点一次再失败一次，每次都付钱。
- `_ask_longform()` 是长文案唯一的费用闸门（一篇 5~9 次调用）。
- 长音频要 `_ask_audio()`：不花钱但要跑几十分钟，没有确认人会以为界面挂了。
- **勾选框与标题下的原文链接都要 `click.stop`**（`js_handler`，浏览器端拦住，不走一趟服务端）。
  漏了就是勾一下顺带把行展开、点链接开新标签页的同时也展开——这一行的注释曾经描述过
  这个行为，但代码把 click 挂在整个标题格上，实现从来没有对上过。
- **批量重抓是逐篇 `io_bound`，不是丢一个批量函数进线程**：进度要真的在动，
  一个不动的转圈和卡死看起来一样（`audio_progress.py` 存在的同一个理由）。

### `detail_panel.py` —— 单格详情与重做

`render()` 画一格的详情；`effective_instructions()` 算出这次真正要发出去的额外要求。

「TTS 操作台」按钮在这里挂上 `tts_panel.open_panel()`——**这是唯一的 TTS 界面**，
从前那条交给 TTS 自带图形界面的交接单路（`_open_tts_workbench` / `_watch_handoff`）
已删除，见 `issues/012`。

**改这里要注意**：`_render_language_toggle()` 的中/英是**同一产物的两个版本**
（schema v4：语言是产物的一个维度，不是新的 kind），切换语言不是切换产物类型。

### 三个对话框

| 文件 | 入口 | 花不花钱 |
|---|---|---|
| `import_dialog.py` | 顶栏「导入链接」 | 不调 LLM |
| `source_dialog.py` | 顶栏「从订阅导入」 | 不调 LLM，与 `dna fetch` 是同一个 `intake_sources` |
| `settings_dialog.py` | 顶栏齿轮 | 只写配置文件 |

**改这里要注意**：

- 对话框只管画，**批量循环、配置落盘、采集一律在 `actions.py` / `src/dna/` 里**——
  判据是「这段代码换到另一个前端里要不要重写」。
- `source_dialog` 的「最近几天」是**这一次的意图**，不写回 `sources.yaml`
  （那里的按源配置是长期偏好）。一个源都没有时只画一句说明，不画那些开关：
  点了也没东西可抓，摆着只会让人以为程序坏了。
- `settings_dialog` **不逐项即时保存**：一个窗口是一对数，改到一半上下限是反的。
  另外只提交**改过**的 profile 项——`patch_yaml_values` 会重写它收到的每一个 key，
  全量提交会把 `git diff config/profile.yaml` 塞满没变的行，而那个文件是靠读 diff 审的。
- 被环境变量遮盖的字段**禁用且不提交**，否则 `.env` 里会多出一个与实际行为矛盾的值。

---

## 症状 → 文件

| 症状 | 看这里 |
|---|---|
| 点一下界面卡十几秒 | 漏了 `nicegui.run.io_bound`（`actions.py` / `ledger_table.py`） |
| 点重做花了钱 / 没花钱 | 后端 `produce/service.py::produce` 的 `force` 分支 |
| 格子显示「未生成」但确实跑过 | `ledger_table.py::_cell_state` + 后端要记失败行 |
| 长文案点一下就扣了一大笔 | `ledger_table.py::_ask_longform` 的确认没生效 |
| 「打开文件夹」打开的不是我的目录 | `actions.py::_server_is_local` |
| 点标题下的原文链接，连带把这一行展开了 | `ledger_table.py::_render_title_cell` 的 `click.stop`（`js_handler`） |
| 勾选丢了 / 重线跑到「媒体」列左边 | `theme.py` 的 `COL_PICK` 与 `.wb-grid > div:nth-child(5)`——加列时这两处要一起改 |
| 改了 `.env` 完全没反应 | 被系统环境变量遮盖了，看 `config_edit.py::shadowed_by_env`（设置面板会标红并禁用） |
| 设置里改了字数窗口，超长标记没变 | `actions.py::over_target` 是渲染时现算的，回调必须是 `refresh` 而不是关掉对话框了事 |
| 选了「最近 3 天」却抓回一堆旧文章 | `sources/filters.py::should_keep`——`published_at is None` 的条目不受天数限制，这是有意的 |
| 中/英切换像是切换了产物类型 | `detail_panel.py::_render_language_toggle` |
| 设置面板点开就 `ValueError: Invalid value:`（冒号后是空的） | `settings_dialog.py` 三处 `ui.select` 必须写 `value=value or None`——空字符串不是合法初值（`issues/013`） |
| 弹窗底部的按钮看不见、只能拉整页滑条 | 那个 `ui.card()` 少了 `wb-dialog` 类（`theme.py`：`max-height: 88vh` + 可拉大） |
| 微信文章的视频一个都没抓到 | `extract/media.py::_wechat_videos`——直链在 JS 里且 `&` 被转义（`issues/013`） |
| 打开操作台就 `ValueError: Invalid value: …` | `voice_controls.py::voice_choice` / `ref_choice`——`ui.select` 的初值必须在选项里（`issues/012`） |
| 音色设计合成报 422 | 描述填在「语气指令」框里，`voice_controls.py::voice_problem` 本该在点之前就提示 |
| 点「TTS 操作台」直接开始合成了 | `detail_panel.py::_render_audio_button`——那里必须是按钮不是开关，且与「合成音频」隔一条竖线 |
| 操作台里挑的音色没生效 | `tts_panel.py::_Panel.voice_for` → `actions.run_production(segments=...)` → `produce/service.py::_generate_audio` 的 `if segments is None` |
| **5 段全部合成失败** | 先看 `data/logs/tts_service.log`：引擎在服务端那侧加载失败（torch/torchaudio ABI 不匹配就长这样，见 `issues/012`）。`/health` 探不出来，唯一的探针是 `dna tts --say` |
| 操作台里改了字，落盘的音频还是旧的 | `tts_panel.py::_Panel.state` 的缓存作废判断 → `plan()` 的 `rendered` 筛选；改过字或换过音色的段不能进 `rendered` |
| 试听报「参数冲突」 | 克隆模式下不能同时给音色名；护栏只有一份，在 `tts/service.py::TTSServiceProvider.synthesize` |
| 操作台音色下拉是空的 / 参考音频标红 | 服务离线（`tts/console.py::available_voices` 静默回空表），或路径解析不到（`tts/factory.py::_ref_audio_path`，按 `data_dir` 不是仓库根） |
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
