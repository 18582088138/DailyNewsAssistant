# 014 写死的参数与假配置：配置改了不生效

**发现**：2026-09-11，`DNA_v0.1` 批次 2 的全仓审计（子代理）
**状态**：✅ 第一节两条、A 类、C 类全部修完；B 类按判据外提了该外提的那些（见 §四）

> ⚠️ **有一条行为变更要你确认**：`copy_max_rewrites` 现在是 **2**（跟 `profile.yaml`
> 的注释一致），而代码此前是 1。也就是说文案字数不达标时会多回炉一次，
> **多一次计费调用**。不想要就把 `config/profile.yaml` 里那一行改成 1。
**约束来源**：用户要求的置信度优先级 **env > config > docs > source code** ——
凡是可调参数都必须从 `Settings`（env）或 `Profile`（config）读，不许写死在代码里。

---

## 一、最坏的两条：配置写了，代码不听

### 1. 存图/存视频上限被静默压掉

`config/profile.yaml` 里是用户手工填的 `max_images_per_article: 100`、
`max_videos_per_article: 10`，而代码里同语义的常量各有三四份：

| 位置 | 值 |
|---|---|
| `config/profile.yaml:56` | `max_images_per_article: 100` ← 用户的意图 |
| `core/config.py:465` | `max_images_per_article = 10`（代码默认） |
| `extract/media.py:42` | `DEFAULT_MAX_IMAGES = 10` |
| `store/article_store.py:58` | `DEFAULT_MAX_IMAGES = 10` |

`extract/article.py:59` 调 `extract_media()` **不传 `max_images`**，所以抽取阶段
先按 10 砍一刀。后面无论 profile 写多少，候选池里已经只剩 10 张。
视频同理（`video_store.py:39` 的 2 对 profile 的 10）。

**这条完全静默**：界面上看不出来，日志里也没有，只有人去数图才发现。

### 2. `.env` 里的四个代理项完全不生效

`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` / `FTP_PROXY` 在 `Settings` 里有字段、
在 GUI 白名单里、`dna doctor` 会显示它们，**但全仓没有任何地方把它们写回
`os.environ`**，而 `httpx` 一律走 `trust_env`（读的是 `os.environ`）。

于是：真正生效的只有**系统环境变量**；`.env` 里改代理等于没改。
而 GUI 的帮助文案还在强调「`NO_PROXY` 必须含 `localhost`」——
那句话描述的行为根本不存在。

> 本机之所以一直能用，是因为公司代理本来就配在系统环境变量里
> （`CLAUDE.md` 里「系统环境变量优先级高于 `.env`」这句是对的，
> 但真实原因不是"优先级"，而是 `.env` 那一路完全没接上）。

---

## 二、A 类：同一个参数在多处各写死一份（14 组）

同名不同值的最危险，因为两处都"看起来对"：

| 参数 | 各处取值 |
|---|---|
| `MIN_BODY_CHARS` | `extract/article.py:39` = 80 · `pipeline/clean.py:63` = 40 |
| `MAX_BODY_CHARS` | `narration/script_builder.py:73` = 6000 · `pipeline/summarize.py:40` = 3000 |
| `MAX_REWRITES` | 两处都是 1，而 `config/profile.yaml:60` 的注释写「最多 2 次」 |
| `digest_max_entries` | `config.py:231`（Settings）与 `config.py:457`（Profile）各一份，只有 Profile 那份被 `flow.py:117` 读 |
| `shortvideo_chars` | `config.py:480` = (200, 250) · `profile.yaml:74` = [200, 300] |
| 每源条数 | `config.py:230` = 30 · `sources/rss.py:47` 的 `limit=30`（纯函数路径写死） |
| 默认语言 | `produce/tasks.py:68` · `Settings` · `Profile.languages` · `settings_dialog.py:181` 四份 |
| `cta_line` | `script_builder.py:79` · `config.py:501` · `profile.yaml:93` 三份 |
| `summary_chars` | `config.py:479` · `summarize.py:68` · `profile.yaml:73` 三份 |
| 长文案时长 | `longform.py:69-70` · `config.py:492` · `profile.yaml:86` 三份 |
| TTS 产物目录名 | `config.py:156` + `service.py:648` 与 `:1034` 各写一遍 `or "tts"` |
| HTTP 超时 | `llm/base.py:38` = 120 · `sources/http.py:26` = 20 · `tts/client.py` 里 3 / 30 / 120 混用 |
| 存图上限 | 见上文，四份 |
| 存视频上限 | 见上文，四份 |

**MAX_REWRITES 那条要单独说**：代码是 1（即"只回炉一次"），而 `profile.yaml`
的注释、`13_workbench_guide` 曾经的写法都说"最多 2 次"。文档与代码不一致时，
按优先级 **config > docs > code**，但这个值本身就该是配置项。

---

## 三、C 类：假配置——有字段、有界面、代码不读（18 条）

比魔数更坏：用户改了，界面显示保存成功，行为一点没变。

| 字段 | 情况 |
|---|---|
| `log_level` | 零消费者。`cli/main.py:53` 写死 `WARNING`、`nicegui_app/main.py:331` 写死 `INFO`。GUI 能改、在白名单里，界面还提示「重启才生效」 |
| `default_language` | 唯一消费者是 `cli/main.py:128` 的展示；真正生效的是 `produce/tasks.py:68` 的同名硬编码 |
| `summary_max_sentences` | 零消费者。有字段、有控件、有单测，`summarize.py` 全文不引用；真实句数约束写在 `config/prompts/summarize.md` 的提示词里 |
| `Profile.languages` | 零消费者。全仓无 `profile.languages` |
| `Settings.digest_max_entries` | 被 `Profile` 同名字段遮住，`.env` 里设它无效 |
| `http_proxy` / `https_proxy` / `no_proxy` / `ftp_proxy` | 见第一节，四项全不生效 |
| `inbox_*`（7 项）· `dingtalk_*`（3 项）· `imap_*`（4 项） | 零消费者；`src/dna/` 下没有 inbox 模块。`feishu_allowed_users` 的派生属性也零消费者——注释声明的「空 = 拒收全部」安全默认**从未被执行** |
| `tts_start_attempts` | **反向问题**：代码读了（`supervisor.py:102`），但不在 `ENV_ALLOWLIST` 里，GUI 给了 `TTS_START_TIMEOUT` 却漏了它 |
| `NEW_WINDOW_HOURS` | `service.py:98` 仍在用（`:428` 的默认参数），而 `config.py:530` 与 `profile.yaml:101` 都声明此项「已删除」 |

---

## 四、B 类：该可调却写死的魔数（57 条）

完整清单在本批的工作记录里。**不打算 57 条全搬进配置** ——
用户明确说过「优化是目标不是手段，不要为了优化而优化导致代码更复杂」。
判据是：**这个值改一下会不会影响产物质量、费用或等待时间，而用户现在无法在不改代码的情况下改它**。

按这个判据要外提的几组（其余留在代码里，但必须**只有一份定义**）：

| 组 | 内容 | 归哪 |
|---|---|---|
| 选题 | `score.py` 五项权重、多源饱和点、正文饱和点、新鲜度窗口、两个扫描窗口 | `Profile` |
| 判重 | `dedup.py` 汉明距离阈值、指纹取样长度 | `Profile` |
| 长文案 | 准入门槛、扩写系数、章节数上下限 | `Profile` |
| 语速与时长 | `duration.py` 中英语速、混排系数；`tts/base.py` 的 `RTF_ESTIMATE` 与两个停顿 | `Profile` |
| LLM | 重试次数与退避、各节点 temperature、翻译批大小 | `Settings` |
| 网络 | 各处 HTTP 超时、单页体积上限 | `Settings` |
| 分页 | 台账分页、扫描上限 | `Settings` |

`RTF_ESTIMATE = 2.5` 要特别点名：注释自承 CPU 实测 ≈13，**界面预估少报五倍**
（见 [009](009-tts-speaking-rate.md)）。它必须从配置读，且注释要说明这是数量级而非承诺。

---

## 四 bis、实际修法

| 位置 | 怎么修的 |
|---|---|
| 存图上限 | `extract_article` / `fetch_article` 新增 `max_images` 参数，`intake` 在**抓取前**就把上限定下来（`_resolve_max_images`），抽取阶段不再先砍一刀。另外 CLI 的 `--max-images` 默认值从 `10` 改成 `None`——它排在优先级最前，写死默认值等于每次 `dna fetch` 都把 profile 压掉 |
| 代理四项 | 新增 `core.config.apply_proxy_env()`，把 `.env` 的值写进 `os.environ`（大小写两份，因为 httpx/requests/urllib 各认一种），CLI 与 GUI 两个入口各调一次。**已经在系统环境里的一律不覆盖**。`FTP_PROXY` 零消费者，删 |
| `log_level` | 默认值改成空串 = 「各前端用自己的默认」（CLI 的 WARNING 与 GUI 的 INFO 都是有理由的，不能统一）；填了就两边都听它 |
| `DEFAULT_LANGUAGE` | `produce/tasks.py` 的同名硬编码改为 `default_language()` 现读配置；`normalize_lang("")` 走它。十几处 `lang: str = DEFAULT_LANGUAGE` 默认参数改成 `""` |
| `summary_max_sentences` · `Profile.languages` | 零消费者，字段 + 界面控件 + 单测 + `profile.yaml` 里的键一并删（`extra="forbid"`，不删键会加载失败） |
| `Settings.digest_max_entries` | 被 `Profile` 同名字段完全遮住，删 |
| inbox / 钉钉 / IMAP | 钉钉 3 项、IMAP 4 项、`inbox_*` 4 项零消费者，删；`inbox_provider` / `inbox_enabled` / `feishu_*` 保留但注明「P9 未实现，只用于显示」 |
| `tts_start_attempts` | 反向问题：代码读它但不在 `ENV_ALLOWLIST`。补进白名单 + 界面字段 |
| `MIN_BODY_CHARS` 80/40 | `clean.py` 那个 40 **从来没被读过**，删；抽取那侧改名 `MIN_EXTRACTED_CHARS` |
| `MAX_BODY_CHARS` 6000/3000 | 改名 `SCRIPT_BODY_LIMIT` / `SUMMARY_BODY_LIMIT`，并提为 `Profile.script_body_chars` / `summary_body_chars` |
| `MAX_REWRITES` | 提为 `Profile.copy_max_rewrites` / `summary_max_rewrites`，取值按 `profile.yaml` 注释（2 / 1） |
| `RTF_ESTIMATE = 2.5` | 提为 `Settings.tts_rtf_estimate`，默认值按 CPU 实测改成 13（此前界面预估少报五倍） |
| `is_local_url` 两套 | 统一到 `core/urls.py` 一份（保留 `urlparse().hostname` 那版；子串版会把 `http://localhost.example.com` 也认成本机） |
| `mask_secret` 两份 | CLI 改为复用 `config_edit.mask_secret` |
| 选题 / 判重 / 长文案 / 趋势 | 新增 `Profile.tuning`（`Tuning` 模型）：五个权重、三个饱和点、两个扫描窗口、判重阈值与取样长度、长文案准入与章节数与扩写系数、趋势门槛。**只在 `profile.yaml` 里改，不进设置面板**——它们要懂后果才该动 |
| 分层违规 | `check_tts_service` 从 `core/doctor.py` 搬到 `tts/doctor.py`，`run_all(extra=[...])` 由前端把它交进去。`core` 不再认识任何上层 |

**没有外提的（刻意的，不是漏了）**：`duration.py` 的中英语速与混排系数、
`tts/base.py` 的两个停顿。上面那些字数窗口是按当前语速人工校准出来的（见 008），
动语速等于同时推翻它们；issues/009 里的决定也是「已量出，本阶段不改」。
真要开放得连着重做一轮校准 —— **半接的旋钮就是新的假配置**，所以宁可不加。

## 五、验收

每一条修复都要有一个会红转绿的断言，不靠"我改了"：

全部落在 `tests/core/test_config_effective.py` 与 `tests/core/test_layering.py`：

| 钉住什么 | 用例 |
|---|---|
| `.env` 的代理真的进 `os.environ`，且系统环境变量优先 | `test_代理设置会写进环境变量` · `test_系统环境变量优先于_env` |
| 配图上限一路传到抽取阶段（参数化 3/12/40） | `test_配图上限一路传到抽取阶段` |
| 默认语言来自配置，不是写死的常量 | `test_默认语言来自配置而不是写死的常量` |
| CLI 真的把配置里的日志级别交给 `setup_logging` | `test_cli_把配置里的日志级别传给_setup_logging` |
| **假配置清零**：遍历 `Settings` / `Profile` / `Tuning` 每个字段，要求全仓有真实消费者 | `test_没有配了不生效的字段` |
| **分层单向**：`core` 不依赖任何上层，连函数内 import 一起查 | `test_core_不依赖任何上层` · `test_这一层没有反向依赖` |

两条护栏都做过「有没有牙」的验证，不是摆设：

- 假配置那条：本批删掉的 5 个字段（`summary_max_sentences`、`languages`、`ftp_proxy`、
  `inbox_poll_interval`、`dingtalk_client_id`）逐个验过会被判成孤儿。
  它第一版**被我自己写的一句注释骗过去了**（注释里提到 `profile.languages` 就算引用），
  所以现在查之前先剥掉行注释。
- `Tuning` 必须单独点名：只查 `Profile` 的话嵌套模型整体算「被引用过」，
  里面每一项是死的都查不出来 —— 本批加 `Tuning` 时差点因此放进去 8 个新的假配置。

另外：`python tools/check.py` 退出码 0（ruff 从 213 项降到 0）。
