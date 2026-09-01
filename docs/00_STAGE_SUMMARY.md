# 00 阶段总览 / Stage Summary — DailyNewsAssistant

> 本文件是项目的**唯一进度看板**，每个阶段结束必须更新。

---

## 一、开发基础要求（已敲定，2026-09-01）

| # | 要求 | 落实 |
|---|---|---|
| 1 | 开发环境 | conda `ov_env_py312`（Python 3.12.13）。bash 调用 `/c/Users/test/miniforge3/envs/ov_env_py312/python.exe`；**不用 `conda run ... -c`**（会吞参数） |
| 2 | 参考 skills | `xkd/best-skills/skills/`，本项目取用 `dev-workflow`、`project-docs`、`codegen-doc`、`codegen-diagram`、`skill-create` |
| 3 | 参考 prompts | `xkd/best-prompts/prompts/`，按阶段取 `dev-requirement` → `dev-design` → `dev-implementation` → `dev-review`/`code-review-excellence` → `dev-bug-fix` |
| 4 | 专属资产 | Skill：`~/.claude/skills/dailynews-dev/SKILL.md`；Memory：`~/.claude/projects/c--Users-test-Downloads-xkd/memory/`（一事一文件 + MEMORY.md 索引）。随开发持续更新 |
| 5 | 流程 | 调研 → 构建方案 → 方案调整 → 开发 → 测试 → 人工审阅 → debug&修改 → **git 命令汇总（人工手动提交，AI 不执行提交）** |
| 6 | 文档与测试 | 每阶段留档于 `docs/`；每个基础功能配单元测试，**测试文件头部写完整复测命令**，同步 `docs/03_unit_tests.md`。测试不过不进下一模块 |
| 7 | 代码规范 | 分层单向依赖、功能独立封装、核心函数中英双语注释 |
| 8 | AI 推理 | `LLMProvider` 抽象：API（OpenRouter/DeepSeek）与 Local（Ollama → OpenVINO）可互换。先用 API 验证功能，后续迁 Local。TTS 同构抽象 |

---

## 二、项目定位

每日 AI 资讯采集 → 结构化 `DailyDigest` → 派生三种发布形态（图文版 / 播客版 / 视频版）。
**架构第一约束：核心算法与后端公用，前端与应用相互独立可增删。**

详见 [01_research.md](01_research.md) 与 [02_development_plan.md](02_development_plan.md)。

---

## 三、技术栈定稿

| 环节 | 选型 |
|---|---|
| 信息源 | RSS(`feedparser`) + **自建 RSSHub**(公众号/知乎/微博/X) + **用户投链接接口** |
| 远程投递 | **飞书自建应用机器人 + 长连接**（`lark-oapi` ws.Client，出网即可，无需公网 IP）；白名单 open_id + hashtag 指令 + 即时回执/交互卡片。钉钉 Stream、邮箱 IMAP 留作 adapter |
| 正文/媒体抽取 | `trafilatura` + `bs4` 兜底；og:image / 正文图 / 官方视频，强制记录来源 |
| 处理架构 | **单 Pipeline + LLM 节点**（不用 Multi-Agent） |
| 去重聚类 | URL 规范化 + SimHash（一级）→ `bge-m3` 向量层次聚类（二级） |
| 云 LLM | **DeepSeek（默认）** / OpenRouter（备，待充值问题解决） |
| 本地 LLM | Ollama `qwen3.5:9b` → 后续 OpenVINO |
| TTS | **Qwen3-TTS OpenVINO**（`Qwen3-TTS-CustomVoice-0.6B-OV`，Intel CPU/GPU，中英双语，本地已验证） |
| 长图 | Jinja2 HTML + **Playwright 截图** |
| 前端 | CLI + **NiceGUI**（含后台台账控制台） |
| 存储 | 文件系统（期次目录 `YYYYMMDD-DailyNews` / `topics` 标题目录）+ **SQLite 资产台账** |

---

## 四、阶段进度

| 阶段 | 内容 | 状态 | 测试 | 文档 |
|---|---|---|---|---|
| 调研 | 环境/依赖/本地资产核查、选型 | ✅ 完成 | — | `01_research.md` |
| 构建方案 | 架构、目录、数据模型、分阶段计划 | ✅ 完成 | — | `02_development_plan.md` v1 |
| 方案调整 | 并入用户 7 点反馈 → 方案 v2 | ✅ 完成 | — | `02_development_plan.md` v2 |
| P0 脚手架 | 配置/模型/命名/日志/环境自检 + CLI | ✅ **完成** | 95 passed (0.49s) | `03_unit_tests.md` · `README.md` |
| P1 LLM 抽象层 | DeepSeek 默认，3 provider 可互换 | ⬜ | — | — |
| P2 信息源 + 抽取 | RSS / user_link / article / media | ⬜ | — | — |
| P3 Pipeline 核心 | 去重聚类→打分→摘要→**双语**→趋势 | ⬜ | — | — |
| P4 落盘 + 台账 DB | 目录规则 / references / SQLite ledger | ⬜ | — | — |
| P5 场景1 图文版 | md/html 双语 + 长图 | ⬜ | — | 🔍 人工审阅 |
| P6 场景2 视频版 | narration 公用层 + 口播稿 + 素材 + 音频 | ⬜ | — | 🔍 人工审阅 |
| P7 场景3 播客版 | 复用 narration：单/双人脚本 + 整期音频 | ⬜ | — | 🔍 人工审阅 |
| P8 前端 | CLI + NiceGUI + **后台台账控制台/重做** | ⬜ | — | 🔍 人工审阅 |
| P9 远程投递 | 飞书机器人长连接 + 白名单 + hashtag + 回执卡片 + 定时调度（离线单测；联调在私人电脑） | ⬜ | — | ✅ 部署文档已交付 |
| P10 集成 | RSSHub/OV LLM/E2E/打包 | ⬜ | — | — |

---

## 五、环境注意事项（踩坑前置）

1. `conda run -n ov_env_py312 python -c "..."` 会吞掉 `-c` 参数 → 直接调 `python.exe`
2. 公司代理 `child-prc.sh.intel.com:913`：**Python 走代理，`curl` 不走** → 联网验证用 Python。
   **`NO_PROXY` 必须含 `localhost`**，否则本地 Ollama(11434) 与 RSSHub(1200) 会被代理拦截（`dna doctor` 已内置该检查）。
   注意**系统环境变量优先级高于 `.env`**，实际生效值以 `dna doctor` 显示为准
3. OpenRouter 免费层会限流（doc_analyzer issue 002）→ 批量任务默认走 DeepSeek
4. Playwright 需先 `playwright install chromium`，P0 的 `dna doctor` 会检查
5. `outputs/`、`data/`、`.env`、模型缓存一律 gitignore
6. 飞书 `ws.Client.start()` **阻塞主线程**，必须单起线程，否则会挡住 NiceGUI 与调度器
7. **飞书投递功能不在公司电脑运行**（部署在私人电脑）→ 公司电脑上相关测试全部用构造事件离线跑，真连接测试标 `@live` 默认跳过

---

## 六、问题台账

| # | 问题 | 状态 | 记录 |
|---|---|---|---|
| — | 暂无 | — | — |

---

## 六 bis、P0 交付清单（2026-09-01）

| 类别 | 内容 |
|---|---|
| 打包 | `pyproject.toml`（src 布局 + 前端独立包；依赖按阶段拆 extras；pytest 标记与默认跳过规则） |
| 核心层 | `core/config.py`（.env + YAML 两层，路径解析、白名单、代理校验）· `core/models.py`（8 个 Pydantic 模型 + 双语回退）· `core/naming.py`（Windows 安全的目录命名）· `core/logging.py` · `core/errors.py` · `core/doctor.py`（11 类检查） |
| 前端 | `frontends/cli/main.py`：`dna doctor` / `config` / `sources` / `version` |
| 配置 | `config/sources.yaml`（7 源）· `config/profile.yaml`（关注词/排除词/视频词/篇幅/时长） |
| 测试 | 5 个测试文件，**95 passed / 0.49s**，全部离线 |
| 文档 | `README.md` · `03_unit_tests.md` |
| 专属资产 | Skill `~/.claude/skills/dailynews-dev/SKILL.md` |

`dna doctor` 实测：**15 OK · 2 WARN · 0 FAIL · 1 SKIP**。
两个 WARN 是 P2 的 `feedparser`/`trafilatura` 与 P9 的 `lark_oapi`，到对应阶段再装，属预期。

---

## 七、方案调整决议（2026-09-01 已确认，原待确认假设全部回收）

| # | 议题 | 决议 |
|---|---|---|
| 1 | 落盘目录 | 保留「期次级 + 条目级」混合结构，但**去掉中间层**：期次目录直接命名 **`YYYYMMDD-DailyNews`**（如 `20260901-DailyNews`），整期产物按形态平铺为 `graphic/`、`podcast/`，单条新闻资产在 `topics/<序号>_<标题slug>/`，重做旧产物入 `_history/<时间戳>/` |
| 2 | `need_video` 标签 | LLM 打分 + 关键词规则自动判定，**GUI 可人工勾选覆盖** |
| 3 | 双语 | **三个场景全覆盖**，文案与音频双语；`narration` 层的 script_builder 与 voiceover 由视频版与播客版**公用**。双语是渲染期参数，已有 Digest 可随时补英文版 |
| 4 | 场景顺序 | **P5 图文 → P6 视频 → P7 播客**（narration 公用层在 P6 建，P7 直接复用） |
| 5 | LLM 默认 | **DeepSeek 设为默认 API**；OpenRouter 充值受阻，暂作备用 |
| 6 | 远程投递 | 新增 `inbox/` 模块，**飞书自建应用机器人 + 长连接**为默认实现（用户要求聊天式投递；飞书/钉钉均支持长连接，出网即可，无需公网回调）。白名单 open_id（空=拒收全部）+ `#video`/`#skip`/`#en` 指令 + 即时回执与交互卡片。钉钉/邮箱仅留预留接口 → **P9** |
| 6b | 部署环境 | **飞书功能不在公司电脑运行**，部署在用户私人电脑，联调由用户自行完成。公司电脑只交付代码与离线单测 → 代理 WSS 风险关闭、spike 取消；`feishu_bot.py` 须与 SDK 解耦以支持离线单测；**部署文档已提前交付**：[08_feishu_bot_deployment.md](08_feishu_bot_deployment.md) |
| 7 | 资产台账 | 评估结论：**难度不大，纳入正式范围**。SQLite `productions` 表记录「哪篇文章 × 哪种形态 × 哪种语言」的产出矩阵；NiceGUI 后台控制台支持筛选、统计与**指定重做**（旧产物移入 `_history/` 不覆盖）→ 建表 **P4**、控制台 **P8** |
