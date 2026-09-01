# 01 调研报告 / Research Report

> 阶段：**调研**（流程第 1 步）
> 日期：2026-09-01
> 状态：已完成，等待「方案调整」阶段确认

---

## 1. 项目定位

DailyNewsAssistant（DNA）：**每日 AI 资讯采集 → 结构化处理 → 多形态内容生产** 的本地应用。

一份「当日结构化摘要」（digest）为唯一事实源，向下派生三种可独立演进的发布形态：

| 场景 | 名称 | 产物 | 目标平台 |
|---|---|---|---|
| 1 | AI 日报 · 图文版 | 每条 1~2 句总结 + 1~3 张配图 + 源链接；文字稿 + 长图 | 微信群/公众号/小红书/X/知乎/微博 |
| 2 | AI 日报 · 播客版 | 播客文案（单人播报 / 双人访谈）+ 音频 | 喜马拉雅/播客平台 |
| 3 | AI 时事播报 · 视频版 | 主/副标题 + 20~25s 口播稿 + 配图/官方视频片段 + 1~2 句短介绍 | 抖音/小红书/视频号 |

场景会随发布反馈调整，因此架构的第一约束是：**核心算法与后端公用，前端与应用相互独立、可增删。**

---

## 2. 开发环境核查结果

### 2.1 Python 环境

- conda env `ov_env_py312`，Python **3.12.13**（conda-forge, MSC v.1944 64bit）
- 调用方式（bash）：`/c/Users/test/miniforge3/envs/ov_env_py312/python.exe`
  - ⚠️ 不要用 `conda run -n ... python -c`，`-c` 参数会被吞掉（doc_analyzer 项目已踩过）

### 2.2 依赖盘点

| 已安装（可直接用） | 未安装（需 pip 补） |
|---|---|
| `nicegui` `playwright` `PIL` `jinja2` `markdown` `markdown_it` | `feedparser`（RSS 解析） |
| `openai` `crewai` `transformers` `openvino` `optimum` | `trafilatura`（正文抽取） |
| `sklearn` `numpy` `sentence_transformers` | `apscheduler`（定时调度） |
| `requests` `httpx` `bs4` `pydantic` `yaml` `dotenv` `pytest` | `soundfile` / `pydub`（音频拼接，待确认） |

> `crewai` 虽已装但本项目**不使用**——已定架构为单 Pipeline。

### 2.3 本地 LLM 资产（Ollama）

| 模型 | 大小 | 用途预判 |
|---|---|---|
| `qwen3.5:9b` | 6.6 GB | 后续 Local LLM 迁移主力 |
| `qwen2.5:3b` | 1.9 GB | 快速冒烟测试 |
| `gurubot/Qwen3.5-35B-A3B-GGUF-unsloth-nothink:UD-Q4_K_XL` | 22 GB | 高质量离线批处理 |

### 2.4 本地 TTS 资产（已验证）

路径：`openvino_notebooks/notebooks/qwen3-tts/`

- 模型：`Qwen3-TTS-CustomVoice-0.6B-OV`（**OpenVINO IR 已转换完成，本地就绪**）
  - talker language model / text embedding / text projection / code predictor / speech tokenizer 全套 `.xml + .bin`
- 封装类：`qwen_3_tts_helper.py::OVQwen3TTSModel`
  - `from_pretrained(model_dir, device)` — 支持 Intel **CPU / GPU**
  - `generate_custom_voice(text, language, speaker, instruct)` → `(wavs, sample_rate)`
  - `generate_voice_clone(...)` — 音色克隆（可做固定主播音色）
  - `generate_voice_design(...)` — 文字描述音色（可做双人访谈的两种音色）
  - `get_supported_speakers()` / `get_supported_languages()`
- **结论**：播客音频可全本地生成，零 API 成本；双人访谈用两个 speaker 或 voice_design 区分。

### 2.5 网络

- 公司代理：`HTTPS_PROXY=http://proxy-dmz.intel.com:912`
- ⚠️ Python 走代理正常，**`curl` 不走**——联网验证一律用 Python，不要用 curl（doc_analyzer 已踩过）

---

## 3. 技术选型与理由

| 环节 | 选型 | 理由 | 备选/后路 |
|---|---|---|---|
| 标准信息源 | `feedparser` + RSS | 无 key、格式稳定、合规、配置文件即可扩展 | — |
| 平台源（公众号/知乎/微博/X） | **自建 RSSHub（Docker）** | 千余路由、不限流、稳定 | 公共 rsshub.app 实例（限流） |
| 用户投递 | 自研 `user_link` adapter | 用户随手发链接 → 抽正文入库，是刚需入口 | — |
| 正文抽取 | `trafilatura` 主 + `bs4` 兜底 | 抽取质量最好，自带去模板/去广告 | readability-lxml |
| 一级去重 | URL 规范化 + SimHash | 零成本干掉完全重复 | — |
| 二级聚类 | `bge-m3` 向量 + 层次聚类（sklearn） | 多源同一事件合并；embedding 全本地、无 token 成本 | LLM 判重（贵，不用） |
| 摘要/打分/趋势 | LLM（Pipeline 节点） | 只在必要节点调用，省 token，便于 Local 迁移 | — |
| 云 LLM | OpenRouter（主）/ DeepSeek（备） | 已有 key；DeepSeek 中文摘要质量好、稳定不限流 | — |
| 本地 LLM | Ollama → 后续 OpenVINO | 与你的迁移目标一致 | vLLM(Linux) |
| TTS | **Qwen3-TTS OpenVINO**（本地） | 已本地验证，支持 Intel CPU/GPU，无外网依赖 | edge-tts |
| 图文长图 | Jinja2 HTML + Playwright 截图 | 排版自由度最高，HTML 稿可直接复用；`playwright` 已装 | PIL 绘制 |
| GUI | NiceGUI | 与你现有 doc_analyzer / K12 项目栈一致 | — |
| 数据落盘 | 文件系统（按日期/标题）+ SQLite 历史库 | 产物可直接取用；历史库仅做「见过没」判定 | — |

---

## 4. 关键风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| OpenRouter 免费层限流（doc_analyzer issue 002 复现过） | 批量摘要跑不完 | 默认 DeepSeek 做批量；OpenRouter 做验证；Provider 层做退避重试 + 自动切备用 |
| RSSHub 自建部署成本 | 平台源延后 | Source 层做成可插拔 adapter，P2 先交付 RSS + user_link，RSSHub 放 P9 |
| 配图版权 | 发布合规 | 只抓原文 `og:image` / 正文图，**每张图强制记录来源 URL**，写入 `references.md`；不做二次分发担保 |
| Playwright 浏览器内核未下载 | 长图生成失败 | P0 增加 `playwright install chromium` 前置检查脚本 |
| Qwen3-TTS 长文本合成慢 | 播客生成耗时 | 按段落切分 → 分段合成 → 拼接；GPU 优先、CPU 兜底；给进度回调 |
| 中英双语 token 翻倍 | 成本/耗时 | 双语作为可选开关，默认中文；英文源先译中，需要时再回译 |

---

## 5. 待确认假设 → 已在「方案调整」阶段全部确认

见 [00_STAGE_SUMMARY.md](00_STAGE_SUMMARY.md) §七「方案调整决议」。

---

## 5b. 补充调研（v4 修订）：远程投递接口选型

需求：用户不在电脑前时，用**聊天的方式**把随手看到的链接送进应用。

### v2/v3 的错误判断与更正

v2 曾以「IM 机器人需公网回调地址」为由排除飞书/钉钉，选定邮箱 IMAP。**该判断已过时**：
飞书与钉钉均已推出**长连接模式**，由客户端主动向平台建立 WebSocket 拉取事件推送，
**只需本机具备出网能力，不需要公网 IP 或域名**，内网机器即可开发与运行。

| 方案 | 需公网回调 | 需平台审批 | 聊天式交互 | 富媒体/交互卡片 | 回执时延 | 结论 |
|---|---|---|---|---|---|---|
| **飞书 自建应用 + 长连接** | ❌ 出网即可 | 需建应用（免费团队即可） | ✅ | ✅ | 秒级 | ✅ **选定** |
| 钉钉 Stream 模式 | ❌ 出网即可 | 需建应用 | ✅ | ✅ | 秒级 | 能力对等，留 adapter |
| 邮箱 IMAP 轮询 | ❌ | ❌ | ❌ 体验割裂 | ❌ | 分钟级 | **降级为兜底** |
| 企业微信 | ✅ 仍需回调 | ✅ | ✅ | ✅ | — | 不做 |

**决定性优势**：IM 方案能做**即时回执**与**交互卡片**——收到链接后立刻回一张卡片，
按钮直接勾「做视频 / 跳过 / 要英文版」，回调同样走长连接。邮箱做不到这种闭环。

### 技术验证要点

- 飞书：`pip install lark-oapi`（PyPI 现版本 1.4.19）
  - `lark.ws.Client(APP_ID, APP_SECRET, event_handler=...)` + `cli.start()`
  - 事件 `im.message.receive_v1` → `P2ImMessageReceiveV1`
  - ⚠️ `start()` **阻塞主线程**，须单起线程，否则挡住 NiceGUI 与调度器
  - 长连接仅建连时鉴权，**后续事件明文，免解密与验签**
  - 后台：建企业自建应用 → 开机器人能力 → 申请 `im:message.receive_v1` 等权限 → 事件订阅选「使用长连接接收事件」→ 发布应用
- 钉钉（备选）：`pip install dingtalk-stream`，继承 `ChatbotHandler` 实现 `process`，`start_forever()` 自动重连

### 安全设计

- `FEISHU_ALLOWED_USERS` 发送者 open_id 白名单，**空值 = 拒收全部**（安全默认）
- hashtag 指令：`#video`（强制进视频版）/ `#skip` / `#en`（要英文版）
- App Secret 只入 `.env`，不进代码不进库

### 部署环境（v5 更新）：不在公司电脑运行

用户明确本功能部署在**私人电脑**，公司电脑只做开发与离线单元测试。因此：

- ~~公司代理下 WSS 连通性风险~~ → **不适用，风险关闭**，v4 计划的连通性 spike 取消
- 邮箱 IMAP 兜底的理由（代理阻断）随之消失 → 降为预留接口不实现
- 换来一条新的硬要求：**`feishu_bot.py` 必须与 SDK 解耦**（只做协议适配，业务逻辑在 `service.py`），
  否则公司电脑上无法离线单测
- 部署步骤见 [08_feishu_bot_deployment.md](08_feishu_bot_deployment.md)

参考：
[飞书 SDK 仓库](https://github.com/larksuite/oapi-sdk-python) ·
[飞书「使用长连接接收事件」](https://feishu.apifox.cn/doc-7518429) ·
[lark-oapi PyPI](https://pypi.org/project/lark-oapi/) ·
[钉钉 Stream Python SDK](https://github.com/open-dingtalk/dingtalk-stream-sdk-python) ·
[钉钉 Stream 模式教程](https://open-dingtalk.github.io/developerpedia/docs/explore/tutorials/stream/overview/)

---

## 5c. 补充调研（v2）：资产台账可行性

用户诉求：查「哪些文章做过了、都做了哪些输出」，并可对指定文档**重做**指定形态。

**评估结论：实现成本低，纳入正式范围。** 原因是方案本身已经具备两个前提：

1. `_digest.json` 是可重放的结构化事实源——重做不需要重新采集和重跑 LLM 摘要
2. 应用层（图文/视频/播客）本就互相独立、只读 Digest——「重做某一种输出」= 单独调一次对应的 `AppProducer`

因此只需再补一张 `productions` 表记录「digest_date × entry × app × variant × lang → 产物路径/状态/耗时/token」，
即可同时得到：产出矩阵视图、统计报表、重做入口。旧产物移入 `_history/<时间戳>/` 不覆盖，历史可追溯。

---

## 6. 结论

技术上无阻塞项。全链路所需的关键能力（RSS 解析、正文抽取、向量聚类、LLM 抽象、TTS、无头浏览器截图、GUI）在本机均有现成资产或成熟库支撑，可直接进入方案设计。
