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
| 云 LLM | **DeepSeek（默认，后续开发一律以 API LLM 为主）** / OpenRouter（备，待充值问题解决） |
| 本地 LLM | ⏸ **已冻结**：Ollama / OpenVINO **只保留接口，不做功能开发与测试**，待本地方案成熟后再启动 |
| TTS | **Qwen3-TTS OpenVINO**（`Qwen3-TTS-CustomVoice-0.6B-OV`，Intel CPU/GPU，中英双语，本地已验证） |
| 长图 | Jinja2 HTML + **Playwright 截图** |
| 前端 | CLI + **NiceGUI**（含后台台账控制台） |
| 存储 | 文件系统：条目级 `outputs/articles/<日期>/<slug>__<id8>/`（**唯一权威**，正文·配图·视频·五种文案都在这）+ 期次级 `outputs/<日期>-DailyNews/`（只放整期产物，条目按 id 引用）+ **SQLite 资产台账**（`data/` 只放 `dna.db` 与 `llm_cache/`，不放产物） |

---

## 四、阶段进度

| 阶段 | 内容 | 状态 | 测试 | 文档 |
|---|---|---|---|---|
| 调研 | 环境/依赖/本地资产核查、选型 | ✅ 完成 | — | `01_research.md` |
| 构建方案 | 架构、目录、数据模型、分阶段计划 | ✅ 完成 | — | `02_development_plan.md` v1 |
| 方案调整 | 并入用户 7 点反馈 → 方案 v2 | ✅ 完成 | — | `02_development_plan.md` v2 |
| P0 脚手架 | 配置/模型/命名/日志/环境自检 + CLI | ✅ **完成** | 95 passed (0.49s) | `03_unit_tests.md` · `README.md` |
| P1 LLM 抽象层 | DeepSeek 默认，5 provider 可互换 + 重试降级 | ✅ **完成** | 189 passed (1.5s) + 真机验证 | `03_unit_tests.md` · `issues/001` `issues/002` |
| P2 信息源 + 抽取 | RSS / RSSHub / user_link / article / media | ✅ **完成** | 316 passed (2.3s) + 真机验证 | `03_unit_tests.md` · `issues/003` |
| P2+ 落盘与台账 | 文章总表(SQLite) / 正文与图片落盘 / 按源过滤 / dna add·list·show·refetch·stats | ✅ **完成**（应用户要求从 P4 提前） | 402 passed (4.7s) + 真机验证 | `11_article_store_guide.md` · `issues/004` |
| P2++ 视频落盘与人工补正文 | 视频下载(直链 + yt-dlp) / 配图上限 10 / 属性级装饰图过滤 / `dna sync` 回写人工正文 | ✅ **完成** | 445 passed (5.6s) + 真机验证（微信 ×2、知乎 ×1） | `11_article_store_guide.md` · `issues/005` |
| P3 Pipeline 核心 | clean → dedup → score → summarize → translate → trend → `_digest.json`；LLM 响应缓存；`dna digest` | ✅ **完成** | 610 passed (6.9s) + 真机验证（DeepSeek，8 次调用） | `06_prompt_spec.md` · `12_digest_guide.md` · `issues/006` |
| P3.5 台账工作台 | NiceGUI 产出矩阵（5 列逐格重做）/ 单篇产物层 / 三种文案（25~35s·1~2min·5~15min 专题·访谈）/ 目录迁到 outputs / 媒体上限可配置 | ✅ **完成** | 705 passed (12.3s) + 真机验证（DeepSeek，11 次调用） | `07_db_schema.md` · `13_workbench_guide.md` · `issues/007` |
| P3.5 bis 文案质量返工 | 信息密度列为第一要求 / 字数预算按中英混排密度换算 / 短视频改为覆盖主干 / 摘要加信息完整性与指标优先级 / profile 时长区间与 CTA 接通 | ✅ **完成** | 722 passed (12.9s) + 真机复验（DeepSeek，4 次调用） | `06_prompt_spec.md` · `13_workbench_guide.md` · `issues/008` |
| P4 落盘 + 台账 DB | **期次级**目录规则 / `_references.md` / 期次装配 / `dna issue`。⚠️ DB 与条目级落盘已在 P2+ 与 P3.5 提前完成；条目布局以 P3.5 的 `outputs/articles/` 为唯一权威，`topics/` 与 `_history/` 均已取消（`02_development_plan.md` §9.2 §9.3） | ✅ **完成** | 766 passed (16.7s) + 真机验证（20260902 期，零 LLM 调用） | `05_output_spec.md`（落盘唯一权威） |
| P4.5 语音合成层 | `dna/tts/` 两个后端（OpenVINO / PyTorch CUDA）同一协议 / 清洗·分段·拼接·逐段容错 / 三种条目级音频进产出矩阵 / GUI 合成按钮与进度 / `dna tts` `dna produce --kind *_audio` | ✅ **完成** | 802 passed (19.0s) + 真机验证（本地合成，零费用） | `14_tts_guide.md` · `issues/009` |
| P5 场景1 图文版 | md/html 双语 + 长图 | ⬜ | — | 🔍 人工审阅 |
| P6 场景2 视频版 | 素材归集 + 视频合成（narration 层在 P3.5、TTS 在 P4.5 已交付） | ⬜ | — | 🔍 人工审阅 |
| P7 场景3 播客版 | 复用 narration：单/双人脚本 + 整期音频 | ⬜ | — | 🔍 人工审阅 |
| P8 前端 | CLI + NiceGUI + **后台台账控制台/重做** | ⬜ | — | 🔍 人工审阅 |
| P9 远程投递 | 飞书机器人长连接 + 白名单 + hashtag + 回执卡片 + 定时调度（离线单测；联调在私人电脑） | ⬜ | — | ✅ 部署文档已交付 |
| P10 集成 | RSSHub/OV LLM/E2E/打包 | ⬜ | — | — |

---

## 四 bis、LLM 使用与测试纪律（2026-09-01 确立，**后续阶段必须遵守**）

| 约束 | 说明 |
|---|---|
| **本地 LLM 冻结** | Ollama / OpenVINO **只保留接口与已有代码，不做进一步功能开发，不做功能测试**。P1 已验证路径可用（见 issues/001、002），但本地方案当前不成熟（qwen3.5:9b 回答一个字要 2244 tokens / 314 秒）。待用户核实本地方案状态后再启动 |
| **以 API LLM 为主** | 后续所有功能开发与验证均基于 DeepSeek（云端 API） |
| **不频繁测试 LLM** | 真实调用**产生实际费用**。日常开发一律用 `tests/llm/fakes.py` 的测试替身；`@pytest.mark.live` 用例**只在阶段验收时手动跑一次** |
| 默认命令是安全的 | `pytest` 默认排除 `live`，不会产生费用。⚠️ `pytest -m ""` 会把 live 跑起来，需要时再用 |
| 新阶段的写法 | 涉及 LLM 的新功能，单元测试一律用固定的假响应（fixture），断言**提示词构造与结果解析**，而不是去问真实模型 |

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
| 001 | 推理模型（qwen3.5:9b）的 token 预算被思维链耗尽，`max_tokens` 偏小时正文为空；原报错指不到根因 | ✅ 已修复 + ⚠️ 遗留性能结论 | [issues/001](issues/001-ollama-reasoning-token-budget.md) |
| 002 | 小模型（qwen2.5:3b）把 JSON Schema 原样抄回，且修复重试的反馈无效导致三次全废 | ✅ 已修复并真机复验 | [issues/002](issues/002-small-model-echoes-json-schema.md) |
| 004 | 落盘验证暴露两个问题：①图床防盗链导致配图全部 403 ②抽取降级时站点通用标题覆盖了 RSS 正确标题且不可恢复 | ✅ 已修复并真机复验 | [issues/004](issues/004-image-hotlink-and-title-overwrite.md) |
| 008 | 文案信息量不够：①**字数预算按纯中文语速折算，系统性少要三分之一**，而稿子时长照样达标、回炉不触发，错误完全静默 ②短视频「只讲一个点」导致技术正确却没把文章讲清楚 ③笼统的「不要白话」模型执行不了 ④正文截到 3000 字，最有价值的那条局限模型没看到 ⑤摘要选了文件体积而非激活参数 | ✅ 全部已修并真机复验 | [issues/008](008-copy-information-density.md) |
| 009 | P4.5 首次真机合成：口播稿估算 93.9 秒，实际合成 153.3 秒，**差 63%**；Qwen3-TTS 实测中文语速 5.4 字/秒 | ⚠️ 已量出，本阶段不改（发布加速倍率未定，且改语速会连带推翻 issue 008 刚校准过的字数） | [issues/009](009-tts-speaking-rate.md) |
| 007 | P3.5：①arXiv 抓到的「配图」是页脚基金会 logo ②长文案目标按字符数推导，与按秒数验收对不上（目标 15 分钟实测 7.4 分钟）③**图片水印实测无法在抓取时绕开**（发布方烧进像素） | ✅ ①②已修，③确认为外部限制、按用户决定不处理 | [issues/007](007-p35-workbench.md) |
| 006 | P3 开发中发现：①`db_path` 不跟随 `DATA_DIR`，改数据目录会让台账与落盘静默失联（测试因此写进了真实数据库）②NFKC 归一化把中文逗号折成半角，中文稿读起来像机翻 | ✅ 已修复 | [issues/006](006-p3-config-and-normalisation.md) |
| 005 | 微信/知乎真机验证：①知乎强反爬 403（外部限制，改为人工补正文 + `dna sync` 回写）②配图上限 5 张偏少→10 ③微信作者头像被当成正文配图 ④重抓时旧图片残留成孤儿文件 ⑤失败文章全叫「(抓取失败)」⑥视频只记链接不下文件 | ✅ 已修复并真机复验 | [issues/005](issues/005-wechat-zhihu-live-verification.md) |
| 003 | P2 三个发现：①feed 失效被静默记成「成功 0 条」（feedparser 的 bozo 不可靠）②og:image 是站点 logo 时成为日报封面 ③三个订阅源实测失效 | ✅ 全部处理完毕 | [issues/003](issues/003-p2-live-verification-findings.md) |

**issue 001 对后续阶段的约束**（重要）：`qwen3.5:9b` 回答一个字耗 2244 tokens / 314 秒，
按每期 15 条估算纯摘要就要 1~2 小时。因此：P3 起本地模型**不要设小的 `max_tokens`**
（控长度靠提示词约束句数）；Local 迁移优先选非推理模型（本机已有 `...nothink` 变体）；
P3–P7 功能验证走云端 DeepSeek，本地性能评估放到 P10。

**issue 002 对 P3 的约束**：所有打分类字段必须在 Pydantic 模型上写清 `ge`/`le` 与
`description`——实测两个模型对无约束的 `score: float` 分别脑补了 0–100 和 0–10 两种量纲。

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
