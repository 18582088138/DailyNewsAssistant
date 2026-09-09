# DailyNewsAssistant

每日 AI 资讯采集 → 结构化摘要 → 多形态内容生产。
Daily AI news collection → structured digest → multi-format content production.

一份「当日结构化摘要」（`DailyDigest`）为唯一事实源，向下派生三种可独立演进的发布形态：

| 场景 | 产物 | 目标平台 |
|---|---|---|
| **图文版** | 每条 1~2 句总结 + 1~3 张配图 + 源链接；文字稿 + 长图 | 微信群/公众号/小红书/X/知乎/微博 |
| **视频版** | 主副标题 + 20~25s 口播稿 + 配图/官方视频 + 短介绍 | 抖音/小红书/视频号 |
| **播客版** | 单人播报或双人访谈脚本 + 音频 | 喜马拉雅/播客平台 |

---

## 架构

```
frontends (CLI / NiceGUI)              ← 零业务逻辑，可独立替换
   ↓
apps (图文 / 视频 / 播客)               ← 只读 DailyDigest，互不依赖，可增删
   ↓
narration / render                     ← 文案与音频公用层（视频与播客共享）
   ↓
pipeline                               ← 清洗→去重聚类→打分→摘要→双语→趋势
   ↓
sources / inbox / llm / tts / store    ← 可插拔的外部接入层（tts 是一层薄的服务客户端）
   ↓
core                                   ← 配置、数据模型、命名规则、日志
```

**依赖严格单向，禁止反向 import。** 新增一个发布形态 = 新增一个 `apps/*.py` + 模板，核心层零改动。

`DailyDigest` 序列化为 `_digest.json`，任何应用、任何语言都能脱离流水线单独重跑——
「补出英文版」「重做某一种输出」都不需要重新采集或重跑 LLM 摘要。

---

## 快速开始

```bash
conda activate ov_env_py312
pip install -e .

cp .env.example .env      # 填入 API key
dna doctor                # 环境自检
```

常用命令：

```bash
dna doctor          # 环境自检（有阻塞项时退出码 1）
dna config          # 查看生效配置（密钥自动脱敏）
dna sources         # 列出已启用的订阅源
dna probe <url>     # 探测网站的 RSS 地址，给出可粘贴的配置片段

dna fetch           # 采集入库：采集→过滤→抓正文→落盘→记台账
dna fetch --dry-run # 只预览采集到什么，不写文件
dna add <url>...    # 直接抓取指定链接
dna list            # 文章总表（后续选文做日报/口播/视频的依据）
dna show <id>       # 核对某篇的抓取结果与落盘位置
dna refetch <id>    # 重新抓取
dna stats           # 台账统计
dna version

dna prompt --list           # 各任务读哪些提示词文件
dna prompt <id> -t narration  # 看某个任务真正发出去的提示词（加 --run 才计费）

# 以上命令全部不调用 LLM，不产生费用
```

提示词正文在 `config/prompts/`，一个任务一个 Markdown 文件，
改完用 `dna prompt` 验证——看到的与应用真正发出去的逐字节相同。
详见 `config/prompts/README.md`。

---

## 开发

```bash
python -m pytest              # 离线快测（默认）
python -m pytest -m ""        # 全量，含 live / slow
python -m pytest -m live      # 仅需要联网/真实服务的测试
```

约定：

- 每个模块配单元测试，**测试文件头部写完整复测命令**，并登记到 `docs/03_unit_tests.md`
- 测试不过不进入下一模块
- 核心函数中英双语注释
- 密钥只从 `.env` 读取，代码内零硬编码
- **git 提交由人工执行**，各阶段命令汇总在 `docs/git_commands.md`

---

## 文档

| 文件 | 内容 |
|---|---|
| [docs/00_STAGE_SUMMARY.md](docs/00_STAGE_SUMMARY.md) | 进度看板、基础要求、技术栈、问题台账 |
| [docs/01_research.md](docs/01_research.md) | 调研报告与技术选型 |
| [docs/02_development_plan.md](docs/02_development_plan.md) | 架构方案与 P0–P10 计划 |
| [docs/03_unit_tests.md](docs/03_unit_tests.md) | 每个单元测试的用途、命令、预期 |
| [docs/08_feishu_bot_deployment.md](docs/08_feishu_bot_deployment.md) | 飞书机器人部署指南 |
| [docs/10_sources_guide.md](docs/10_sources_guide.md) | **怎么添加信息源**：`dna probe` 探测、按源过滤、当前源清单 |
| [docs/11_article_store_guide.md](docs/11_article_store_guide.md) | **文章总表与落盘**：`dna list/show/add/refetch`、状态含义、落盘结构 |
| [docs/git_commands.md](docs/git_commands.md) | 各阶段 git 命令汇总 |

---

## 环境

- Python 3.12（conda env `ov_env_py312`）
- 云 LLM：DeepSeek（默认）/ OpenRouter（备用）
- 本地 LLM：Ollama `qwen3.5:9b` → 后续 OpenVINO
- TTS：**独立服务** `Agent_TTS_Module`（Qwen3-TTS torch，本项目零模型依赖）
- 长图：Jinja2 + Playwright 截图
