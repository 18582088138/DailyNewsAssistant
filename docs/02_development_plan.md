# 02 开发方案与计划 / Design & Development Plan

> 阶段：**构建方案 → 方案调整（v2，已并入用户反馈）**
> 日期：2026-09-01
> 修订记录：
> - v1 初稿
> - **v2**：双语扩展到播客与视频且文案/音频层公用；场景顺序改为 图文→视频→播客；DeepSeek 设为默认；新增「远程投递接口」与「资产台账数据库 + 后台控制台」两大模块
> - **v3**：产物落盘去掉 `issue/`（曾拟改名 `daily/`）中间层，期次目录直接命名为 `YYYYMMDD-DailyNews`
> - **v4**：远程投递由「邮箱 IMAP」改为「**飞书机器人 + 长连接**」——核实到飞书/钉钉均已支持长连接模式，无需公网回调，v3 的排除理由已失效
> - **v5**：明确**飞书功能不在公司电脑运行**，部署在用户私人电脑。取消代理 WSS spike；邮箱兜底降为预留不实现；`feishu_bot.py` 与 SDK 解耦以支持离线单测；**部署文档 `08_feishu_bot_deployment.md` 提前交付**
> - **v6**：**本地 LLM（Ollama / OpenVINO）功能冻结**——只保留接口与 P1 已完成的代码，不做进一步开发与功能测试，待本地方案成熟后再启动；后续开发一律以 API LLM（DeepSeek）为主；**LLM 真实调用产生费用，不得频繁测试**，日常单测一律用假响应

---

## 1. 设计第一原则

> **核心算法与后端公用；前端与应用相互独立、可增删。**

三层硬隔离，单向依赖：

```
      ┌──────────────── 前端层 frontends/ ────────────────┐
      │   CLI          NiceGUI GUI（含后台台账控制台）      │   ← 可独立替换
      └──────────────────────┬───────────────────────────┘
                             │ 只调用 AppProducer / Pipeline 的公开 API
      ┌──────────────── 应用层 src/dna/apps/ ─────────────┐
      │  图文版      视频版       播客版      (未来: N)    │   ← 可独立增删
      │           └── 共用 narration（文案+TTS音频）──┘    │
      └──────────────────────┬───────────────────────────┘
                             │ 只消费 DailyDigest（结构化事实源）
      ┌──── 核心层 core / inbox / sources / pipeline / llm / tts / store ────┐
      │  投递 → 采集 → 抽取 → 去重聚类 → 打分 → 摘要 → 双语 → 趋势 → Digest  │   ← 公用、稳定
      └──────────────────────────────────────────────────────────────────────┘
```

**契约**：应用层**只读** `DailyDigest`，不反向依赖前端；前端**不含**业务逻辑。
新增一个发布形态 = 新增一个 `apps/xxx.py` + 一套模板，核心层零改动。

---

## 2. 目录结构

```
DailyNewsAssistant/
├── src/dna/
│   ├── core/
│   │   ├── config.py            # .env + YAML 分层配置
│   │   ├── models.py            # Pydantic: NewsItem/Cluster/DigestEntry/DailyDigest/Asset
│   │   ├── logging.py
│   │   └── errors.py
│   ├── inbox/                   # 【远程投递接口】不在电脑前也能聊天式投链接
│   │   ├── base.py              # InboxAdapter ABC: run(on_message) / send_reply()
│   │   ├── feishu_bot.py        # 飞书机器人 + 长连接（默认实现，lark-oapi ws.Client）
│   │   │                        #   只做协议适配，与 SDK 解耦，便于离线单测
│   │   ├── dingtalk_bot.py      # 预留接口：钉钉 Stream 模式（能力对等，暂不实现）
│   │   ├── email_imap.py        # 预留接口：邮箱 IMAP 轮询（暂不实现）
│   │   └── service.py           # 消息 → 抽链接 → 校验白名单 → 入库 → 回执
│   ├── sources/                 # 【可插拔信息源】
│   │   ├── base.py              # SourceAdapter ABC: fetch() -> list[RawItem]
│   │   ├── rss.py               # 标准 RSS/Atom
│   │   ├── rsshub.py            # 自建 RSSHub 路由（公众号/知乎/微博/X）
│   │   ├── user_link.py         # 用户投递链接入库（被 inbox 与 GUI 共同调用）
│   │   └── registry.py          # 按 config/sources.yaml 装配
│   ├── extract/
│   │   ├── article.py           # trafilatura + bs4 兜底 → 正文/标题/时间/作者
│   │   └── media.py             # og:image / 正文图 / 官方视频 → 下载 + 来源记录
│   ├── pipeline/                # 【核心算法，全公用】
│   │   ├── clean.py
│   │   ├── dedup.py             # URL规范化+SimHash（一级）→ bge-m3向量聚类（二级）
│   │   ├── score.py             # 重要性打分 + 关键词加权 + need_video 判定
│   │   ├── summarize.py         # LLM：每条 1~2 句总结
│   │   ├── translate.py         # LLM：中英双语（zh/en 双份文本）
│   │   ├── trend.py             # LLM：当日主线/趋势/观点
│   │   └── flow.py              # 编排入口 run_daily() -> DailyDigest
│   ├── llm/                     # 【API/Local 兼容层】
│   │   ├── base.py              # LLMProvider ABC
│   │   ├── openai_compat.py     # DeepSeek / OpenRouter / vLLM（统一 OpenAI SDK）
│   │   ├── ollama_provider.py
│   │   ├── openvino_provider.py # 预留（P10）
│   │   └── factory.py           # 构造 + 退避重试 + 限流降级
│   ├── tts/                     # 【TTS 抽象层，同构于 llm/】
│   │   ├── base.py              # TTSProvider ABC
│   │   ├── qwen3_ov.py          # 封装 OVQwen3TTSModel（Intel CPU/GPU，中英双语）
│   │   ├── edge_provider.py     # 备选
│   │   └── factory.py
│   ├── narration/               # 【文案+音频公用层 · 新增】视频与播客共享
│   │   ├── script_builder.py    # DigestEntry → 口播/播客脚本（单人/双人/短视频）
│   │   ├── duration.py          # 中英文语速估算，校验 20~25s 约束
│   │   └── voiceover.py         # 脚本 → 分段合成 → 拼接 → wav（zh/en 各一份）
│   ├── apps/                    # 【三个发布应用，互不依赖】
│   │   ├── base.py              # AppProducer ABC: produce(digest, lang, variant) -> ProduceResult
│   │   ├── graphic_daily.py     # 场景1 图文版
│   │   ├── video_brief.py       # 场景2 视频版
│   │   └── podcast_daily.py     # 场景3 播客版
│   ├── render/
│   │   ├── markdown_render.py
│   │   ├── html_render.py       # Jinja2
│   │   ├── longimage.py         # Playwright 截图 → 长图 / 小红书竖图
│   │   └── audio_render.py      # 音频拼接、静音间隔、导出
│   ├── store/
│   │   ├── output.py            # 【落盘目录规则】唯一出口
│   │   ├── db.py                # SQLite 引擎与迁移
│   │   ├── ledger.py            # 【资产台账 · 新增】做过什么、产出什么、可重做
│   │   └── stats.py             # 统计查询（供后台控制台）
│   └── scheduler/runner.py      # APScheduler：每日流水线 + inbox 轮询
├── frontends/
│   ├── cli/main.py              # dna run / add-link / produce / redo / stats / doctor
│   └── nicegui_app/
│       ├── app.py               # 主界面：源管理｜投链接｜跑流水线｜预览｜导出
│       └── console.py           # 【后台台账控制台 · 新增】已处理文章、产出矩阵、重做
├── templates/
│   ├── graphic/  wechat_long.html · xhs_vertical.html · plain.html
│   ├── video/    brief.md.j2
│   └── podcast/  script_solo.md.j2 · script_duo.md.j2
├── config/
│   ├── sources.yaml             # 订阅源清单
│   ├── profile.yaml             # 关注领域/关键词/篇幅/语言偏好
│   └── prompts/                 # 各节点提示词（外置，改词不改代码）
├── tests/                       # 与 src 对称
├── docs/
├── data/                        # SQLite 台账（gitignore）
├── outputs/                     # 产物（gitignore）
└── .env / .env.example / .gitignore / requirements.txt / pyproject.toml
```

---

## 3. 核心数据模型

```python
RawItem      # 采集原件：source_id, url, title, published_at, raw_html, via(rss|rsshub|inbox|gui)
Article      # 抽取后：正文、作者、媒体资产列表
MediaAsset   # kind(image|video), url, local_path, source_url, credit
Cluster      # 同一事件的多源聚合：members[], canonical_url, refs[]
DigestEntry  # 成稿单元：
             #   title_zh / title_en, summary_zh / summary_en (各1~2句),
             #   images[], refs[], score, tags[], need_video: bool
DailyDigest  # date, entries[], trend_note_zh/en, stats  ← 三个应用唯一输入
```

`DailyDigest` 序列化为 `_digest.json`。**任何应用、任何语言、任何变体都能脱离流水线单独重跑**——这是「重做指定输出」功能的地基（见 §6）。

---

## 4. 产物落盘规则（v3 修订）

**期次目录命名：`YYYYMMDD-DailyNews`**（如 `20260901-DailyNews`）。
去掉中间的 `daily/` 层——期次级产物按形态直接平铺，单条新闻资产收在 `topics/`。

```
outputs/20260901-DailyNews/
├── _digest.json                     # 结构化事实源，可重放
├── _references.md                   # 【参考链接汇总 · 独立文件】全部来源 + 图片/视频出处
├── graphic/                         # 场景1 · 整期图文
│   ├── daily.zh.md    daily.en.md
│   ├── daily.zh.html  daily.en.html
│   ├── longimage_wechat_zh.png    longimage_wechat_en.png
│   └── longimage_xhs_zh_1..n.png
├── podcast/                         # 场景3 · 整期播客
│   ├── script_solo.zh.md   script_solo.en.md   （或 script_duo.*）
│   └── podcast.zh.wav      podcast.en.wav
├── topics/01_<标题slug>/            # 条目级 = 单条新闻的全部资产
│   ├── article.md                   # 抽取正文
│   ├── summary.zh.md  summary.en.md # 1~2 句总结
│   ├── references.md                # 本条来源链接
│   ├── images/  01_<src-host>.jpg + 同名 .json（来源 URL / 版权信息）
│   ├── video/   official_*.mp4 / video_links.txt
│   └── brief/                       # 场景2 · 仅 need_video=True
│       ├── brief.zh.md  brief.en.md # 主副标题 + 20~25s 口播稿 + 1~2句短介绍
│       └── narration.zh.wav  narration.en.wav
└── _history/<时间戳>/               # 重做时旧产物移入此处，不覆盖
```

**命名规则**：
- 期次目录 `YYYYMMDD-DailyNews`；`topics` 子目录 `<两位序号>_<标题slug>`
- 语言后缀统一 `.zh` / `.en`
- 重做产生的旧文件按原相对路径移入 `_history/<时间戳>/`，可追溯

---

## 5. 双语策略（v2 修订）

覆盖**全部三个场景**，且文案与音频两层公用：

| 层 | 双语实现 |
|---|---|
| Pipeline | `translate.py` 一次产出 `title_zh/en` + `summary_zh/en` + `trend_note_zh/en`，写进同一份 Digest |
| 文案 | `narration/script_builder.py` 按 `lang` 出脚本；视频（20~25s 短稿）与播客（长稿）**共用同一构建器**，只是模板与长度约束不同 |
| 音频 | `narration/voiceover.py` 统一 `script → 分段 → TTS → 拼接`；Qwen3-TTS 原生支持中英，`language` 参数切换。**视频口播与播客音频走同一条代码路径** |
| 长度校验 | `narration/duration.py`：中文约 4.5 字/秒、英文约 2.6 词/秒，视频稿超出 20~25s 自动回炉重写（最多 2 次） |

默认只产 `zh`；`--lang zh,en` 或 GUI 勾选时产双份。**双语是渲染期参数，不是采集期参数**——已有 Digest 可随时补出英文版，不用重跑流水线。

---

## 6. 资产台账数据库 + 后台控制台（新增，用户第 7 点）

> 结论：**难度不大，顺带做**。因为 `_digest.json` 已是可重放事实源、应用层本就互相独立，「重做指定输出」几乎是白送的——只需记录「谁在什么时候用什么参数产出了什么」。

### 6.1 SQLite 表设计（`data/dna.db`）

| 表 | 关键字段 | 作用 |
|---|---|---|
| `sources` | id, name, type(rss/rsshub/inbox), url, enabled | 源清单 |
| `items` | id, url_hash(唯一), canonical_url, title, source_id, published_at, first_seen_at | **跨日去重**：见过就不再重复入日报 |
| `clusters` | id, date, canonical_item_id, title, score, tags, need_video | 事件聚合 |
| `digests` | date(主键), path, entry_count, created_at | 每期日报 |
| `digest_entries` | id, digest_date, cluster_id, rank, title_zh/en, summary_zh/en | 成稿条目 |
| `productions` | id, digest_date, entry_id(NULL=期次级), **app**(graphic/video/podcast), **variant**(wechat_long/xhs/solo/duo), **lang**(zh/en), status, output_path, llm_provider, model, tokens, duration_ms, created_at, **redo_of_id** | **核心：做过哪些输出** |

`productions` 回答了你要的两个问题——「哪些文章已经做过了」「都做了哪些方面的输出」。
重做 = 新插一行、`redo_of_id` 指向旧行，旧产物移入 `_history/`，**历史可追溯不丢失**。

### 6.2 后台控制台（NiceGUI 页面 `console.py`）

- **文章台账表**：日期 / 标题 / 来源 / 评分 / 产出矩阵徽章（图文·视频·播客 × zh·en，已产=实心，未产=空心）/ 最后生成时间
- **筛选**：按日期区间、来源、标签、`need_video`、「有缺口的」（某形态未产出）
- **重做**：勾选任意条目 + 选择目标形态与语言 → 触发 `AppProducer.produce()`，进度条 + 结果链接
- **统计**：每日条目数趋势、各形态产出数、token 消耗与耗时、来源贡献 Top N、去重命中率

CLI 对等命令：`dna stats`、`dna redo --date 2026-09-01 --entry 3 --app video --lang en`

---

## 7. 远程投递接口（用户第 6 点，v4 改为飞书机器人）

需求：不在电脑前时，用**聊天的方式**把看到的链接送进应用。

**选型：飞书自建应用机器人 + 长连接（WebSocket）事件订阅**

> v3 曾因「需公网回调」排除 IM 方案，该判断已过时。飞书与钉钉均已提供**长连接模式**：
> 客户端主动向平台建立 WebSocket 拉取事件，**只需本机能出网，不需要公网 IP 或域名**。
> 飞书官方还说明长连接仅在建连时鉴权，后续事件为明文，**免去解密与验签**。

| 方案 | 需公网回调 | 聊天式交互 | 富媒体/交互卡片 | 即时回执 | 结论 |
|---|---|---|---|---|---|
| **飞书 长连接** | ❌ 出网即可 | ✅ | ✅ | ✅ 秒级 | ✅ **选定** |
| 钉钉 Stream 模式 | ❌ 出网即可 | ✅ | ✅ | ✅ | 能力对等，留 adapter |
| 邮箱 IMAP | ❌ | ❌ 体验割裂 | ❌ | ⚠️ 分钟级轮询 | **降级为兜底 adapter** |

### 技术要点

- SDK：`pip install lark-oapi`
- 长连接：`lark.ws.Client(APP_ID, APP_SECRET, event_handler=...)` → `cli.start()`
  - `start()` **阻塞主线程**，必须单独起线程，否则会挡住 NiceGUI / 调度器
- 事件：`im.message.receive_v1`（P2ImMessageReceiveV1）
- 后台配置：开发者后台建企业自建应用 → 开启机器人能力 → 申请 `im:message.receive_v1` 与发消息权限 → 事件订阅方式选「**使用长连接接收事件**」→ 发布应用

### 工作方式

1. 手机上把链接**转发/粘贴给机器人单聊**（或拉进群 @机器人）
2. `inbox/feishu_bot.py` 经长连接收到 `im.message.receive_v1`
3. **发送者白名单校验**（`FEISHU_ALLOWED_USERS`，open_id 列表；**空 = 拒收全部**）
4. 从消息文本提取全部 URL → 交 `sources/user_link` 抽正文入库
5. 文本中的 hashtag 作指令：`#video` 强制 `need_video=True`、`#skip` 忽略、`#en` 要英文版
6. **即时回执**：回一条消息「已入库 N 条：《标题》…；失败 M 条：原因…」
7. **交互卡片**（P9 进阶，成本低）：回执卡片带按钮，直接勾「做视频 / 跳过 / 要英文版」，
   回调同样走长连接，无需额外服务

### 开发环境与部署环境分离（v5）

> **本功能不在公司电脑运行。** 公司电脑只做开发与**离线单元测试**；真机联调与常驻部署在用户私人电脑上完成。

因此：

- 公司代理下的 WSS 连通性**不再是风险项**，v4 计划的 `scripts/spike_feishu_ws.py` 取消
- `email_imap.py` 兜底的理由（代理阻断）随之消失 → **降为预留接口，不实现**，需要时再补
- **可测试性成为硬要求**：`feishu_bot.py` 只做协议适配（飞书事件 → 内部 `InboxMessage` → 回执），
  业务逻辑全在 `service.py`，与 SDK 解耦；单测用**构造的事件对象**驱动，零网络依赖
- 真实长连接相关测试标 `@pytest.mark.live`，公司电脑默认跳过
- **交付物含部署文档**：[08_feishu_bot_deployment.md](08_feishu_bot_deployment.md)（已完成，含后台配置、
  open_id 获取、Windows 常驻、8 条验证清单、排错对照表）

`.env` 项：`INBOX_PROVIDER=feishu` / `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_ALLOWED_USERS` / `INBOX_SEND_RECEIPT`。

---

## 8. LLM / TTS 兼容层（要求 8）

```python
class LLMProvider(ABC):
    def chat(self, messages, **kw) -> str: ...
    def chat_json(self, messages, schema, **kw) -> dict: ...   # 强制结构化 + 重试
    @property
    def info(self) -> ProviderInfo: ...                        # name/model/是否本地
```

| 实现 | 覆盖 | 状态 |
|---|---|---|
| `OpenAICompatProvider` | **DeepSeek（默认，后续开发主力）** / OpenRouter（备）/ vLLM | ✅ P1 完成 |
| `OllamaProvider` | 本地 Ollama | ✅ 代码完成，⏸ **功能冻结** |
| `OpenVINOProvider` | 本地 OV IR | ⏸ 接口占位，**冻结** |

### ⏸ 本地 LLM 冻结说明（v6）

用户决定：**本地 LLM 方案当前不够成熟，只保留接口，不做进一步功能开发与功能测试**，
待本地方案状态核实后再启动。P1 已完成的 `OllamaProvider` 代码保留不动，
其 live 测试标记为 skip（`tests/llm/test_provider.py::test_live_ollama`）。

依据（P1 真机实测）：`qwen3.5:9b` 回答一个字耗 2244 tokens / 314 秒，
按每期 15 条估算纯摘要环节即需 1~2 小时，尚不具备实用性。详见 `issues/001`。

**对后续阶段的影响**：P3–P9 全部基于 DeepSeek 开发验证；原计划 P10 的
「OpenVINO LLM provider」改为**待定**，解冻后再排期。抽象层已经就位，
解冻时业务代码零改动。

### 💰 LLM 测试纪律（v6）

真实调用**产生实际费用**，因此：

- 日常开发与单元测试**一律使用 `tests/llm/fakes.py` 的测试替身**，断言的是
  「提示词构造是否正确」与「返回解析是否正确」，而不是去问真实模型
- `@pytest.mark.live` 用例**只在阶段验收时手动跑一次**
- 默认的 `pytest` 命令已排除 live，不会产生费用；`pytest -m ""` 会跑 live，慎用
- 新增涉及 LLM 的功能时，**先用固定假响应把逻辑测透**，最后才做一次真机验收

`factory.py` 统一处理超时、指数退避、限流自动降级、token 计数（计数写入 `productions` 表）。
业务代码只见 `LLMProvider`——**换模型零改动**，这是「先 API 验证、后 Local 迁移」的落点。

`TTSProvider` 同构：`synthesize(text, voice, language) -> (np.ndarray, sr)`；
`Qwen3OVProvider` 封装 `OVQwen3TTSModel`，`TTS_DEVICE=CPU|GPU` 控制设备，中英双语由 `language` 参数切换。

---

## 9. 分阶段计划 v2（每阶段：代码 + 单元测试 + 文档；测试不过不进下一阶段）

| 阶段 | 内容 | 关键交付 | 单元测试 |
|---|---|---|---|
| **P0** | 脚手架：目录、`pyproject`、配置加载、数据模型、日志、`dna doctor`（含 `playwright install chromium` 检查） | 可 `import dna`，doctor 全绿 | `test_config` `test_models` |
| **P1** | LLM 抽象层：base / openai_compat(DeepSeek默认) / ollama / factory + 重试降级 | 三 provider 互换跑通同一 prompt | `test_llm_provider`（mock）+ `@live` |
| **P2** | 信息源 + 抽取：`rss` / `user_link` / `extract.article` / `extract.media` | RSS 与链接各拿到结构化 `Article` | `test_rss` `test_user_link` `test_extract`（本地 fixture） |
| **P3** | Pipeline 核心：clean → dedup → score → summarize → **translate(双语)** → trend → `run_daily()` | 产出 `_digest.json`（含 zh/en） | 每节点一份；聚类用构造样本验证合并 |
| **P4** | 落盘 + **台账 DB**：`store/output` 目录规则、`_references.md`、`db`/`ledger`/`stats` | 完整 outputs 树 + `data/dna.db` | `test_output_layout` `test_ledger` `test_history` |
| **P5** | **场景1 图文版**：md/html 中英双语 + Playwright 长图（公众号长图 / 小红书竖图） | 可发布图文稿 + PNG | `test_graphic_daily`（尺寸与关键文本快照） |
| **P6** | **场景2 视频版** + **narration 公用层**：need_video 判定、主副标题、20~25s 口播稿（时长校验）、素材归集、短介绍、`voiceover` 中英音频 | `brief.zh/en.md` + `narration.*.wav` + 素材目录 | `test_duration`（时长落在 20~25s）`test_video_brief` `test_tts`(`@slow`) |
| **P7** | **场景3 播客版**（复用 P6 的 script_builder/voiceover）：单人/双人脚本 + 整期音频 | `script.*.md` + `podcast.*.wav` | `test_podcast_script`（离线）+ `@slow` 音频 |
| **P8** | 前端：CLI 全命令 + NiceGUI 主界面 + **后台台账控制台（产出矩阵/筛选/重做/统计）** | 可交互使用 | `test_cli` `test_redo`；GUI 走人工审阅 |
| **P9** | **远程投递接口**（飞书机器人长连接 + 白名单 + hashtag 指令 + 即时回执 + 交互卡片）+ APScheduler 定时。**公司电脑只交付代码与离线测试**，联调在私人电脑 | 可运行的 `dna.inbox.service` + 部署文档 | `test_inbox_parse` `test_inbox_whitelist` `test_inbox_commands`（构造事件，零网络）；真连接测试标 `@live` 默认跳过 |
| **P10** | 自建 RSSHub 接入 + E2E + 打包（~~OpenVINO LLM~~ 随本地 LLM 一并冻结，解冻后另行排期） | 端到端一键日报 | `test_e2e`（`@slow`） |

**人工审阅节点**：P5 / P6 / P7 各自完成后停下来给你审内容质量；P8 后整体审阅一次。

### 9.1 实际执行与原计划的偏差（截至 2026-09-03）

原计划的阶段边界在执行中动过，**查进度以 `00_STAGE_SUMMARY.md` §四 的表为准**，
这张表是原始规划、不反映后来的调整：

| 偏差 | 说明 |
|---|---|
| **P4 的一半提前到了 P2+** | 应用户要求先做了「文章总表 + 条目级落盘」：`db`/`ledger`/`stats` 与 `outputs/articles/<日期>/<slug>__<id8>/` 都已交付 |
| **新增 P3.5 台账工作台** | 原计划把台账控制台放在 P8，但它是用户最频繁使用的入口，且工作量独立，因此单独成阶段 |
| **新增 P3.5 bis 文案质量返工** | 按人工撰写的参考稿重写三种文案的提示词，见 [issue 008](issues/008-copy-information-density.md) |
| **P6 的 narration 层提前到 P3.5** | 三种文案（短视频/口播/长文案）与时长校验已完成，P6 只剩 TTS 与素材归集 |

### 9.2 落盘路径的唯一权威（2026-09-03 用户决定）

> 「落盘的路径以 P3.5 中的实现为准，所有文件的落盘以及中间产物，要确保一致，
> 不要出现散落在各处的问题」

**§4 里 `topics/01_<slug>/` 那套布局作废。** 条目级的一切——正文、配图、视频、
五种文案——只存在一个地方：

```
outputs/articles/<YYYYMMDD>/<slug>__<id8>/        ← 条目级，唯一权威
outputs/<YYYYMMDD>-DailyNews/                      ← 期次级，只放整期产物
├── _digest.json        结构化事实源
├── _references.md      整期来源汇总
├── graphic/            场景1 整期图文
└── podcast/            场景3 整期播客
```

**期次目录不再复制条目内容**，`_digest.json` 里存 `article_id`，需要正文和配图时
按 id 去 `articles/` 取。代价是期次目录不能单独打包带走——这个代价换来的是
**同一份内容不会有两个副本各自演化**，而后者是必然会咬人的（P3.5 迁移时就发现了
两个因标题改名而残留的重复目录，内容相同、标题一新一旧）。

**`data/` 只放程序自己的东西**，不放任何产物：

| 路径 | 内容 | 为什么在 data/ |
|---|---|---|
| `data/dna.db` | 台账数据库 | 程序的索引，不是给人看的 |
| `data/llm_cache/` | LLM 响应磁盘缓存 | 按「provider+模型+消息+参数」的 sha256 存 API 响应。**不是产物的副本**——它缓存的是模型回复的原始 JSON，作用是调提示词时相同请求不重复计费（实测复跑一期日报：6 次调用 → 0 次） |

已核查全仓所有写文件的位置（22 处 `write_text`/`write_bytes`/`mkdir`），
条目内容一律走 `settings.output_path / record.store_dir`，
`data_path` 只被 `dna.db`、`llm_cache` 和 `migrate_layout`（读旧位置）用到。
**目前没有散落**。

### 9.3 `_history/` 取消（2026-09-03 用户决定）

原 §4 规划了 `_history/<时间戳>/`：重做产生的旧产物移到这里而不是覆盖。
**这一项取消。**

`productions` 表已经记录了每次重做的版本链（`redo_of_id` 指向上一版，
带生成时间、provider、模型、调用次数）。`_history/` 要额外解决的只有一件事：
**读回旧版稿子的正文**。而实际使用中重做的原因就是上一版不好，不需要留着。

至于表里为什么留 `llm_provider` / `llm_model` 两列：它们**不是为了追溯责任**，
而是为了**换模型时能筛出该重做哪些**。真实场景是——换了模型或改了提示词之后，
库里一半产物是旧口径的，另一半是新的，肉眼分不出来；有这两列就能
`WHERE llm_model = '旧模型'` 直接列出待重做的，没有就只能全部重跑一遍
（长文案一篇 9 次调用）。**这两列是省钱用的，不是审计用的。**

它们已经在表里且零成本（写入时顺手带上），保留。

---

## 10. 测试规范（要求 6）

- 位置：`tests/` 与 `src/dna/` 对称
- **每个测试文件头部必须写全复测指令**：

```python
"""
test_dedup.py — 去重与聚类单元测试 / Dedup & clustering unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/pipeline/test_dedup.py -v

覆盖 / Covers:
    1. URL 规范化去重（utm 参数、末尾斜杠）
    2. SimHash 近重复判定阈值
    3. bge-m3 向量层次聚类：3 源同一事件应合并为 1 个 Cluster

预期 / Expected:
    5 passed；聚类数 == 1；耗时 < 10s（首次加载 embedding 模型除外）
"""
```

- 标记：`@pytest.mark.live`（需联网/LLM）、`@pytest.mark.slow`（TTS/E2E），默认离线跑
- 每阶段同步更新 `docs/03_unit_tests.md`（用途 / 命令 / 预期输出）

---

## 11. 文档留存清单（要求 5、6）

| 文件 | 内容 | 更新时机 |
|---|---|---|
| `docs/00_STAGE_SUMMARY.md` | 原则、环境、进度表、问题台账 | 每阶段 |
| `docs/01_research.md` | 调研报告 | 已完成 |
| `docs/02_development_plan.md` | 本文档 | 方案变更时 |
| `docs/03_unit_tests.md` | 每个单元测试的用途/命令/预期 | 每模块 |
| `docs/04_architecture.md` | 架构图（Mermaid）+ 业务流 | P3 后 |
| `docs/05_output_spec.md` | 产物目录与文件格式规范 | P4 |
| `docs/06_prompt_spec.md` | 各节点提示词与调优记录 | P3 起持续 |
| `docs/07_db_schema.md` | 台账表结构与统计口径 | P4 |
| `docs/08_feishu_bot_deployment.md` | 飞书机器人 + 长连接**部署指南**：后台配置、权限、发布、open_id 获取、私人电脑部署、Windows 常驻、验证清单、排错表 | ✅ **已完成** |
| `docs/09_packaging.md` | 部署/打包 | P10 |
| `docs/10_sources_guide.md` | **添加信息源指南**：`dna probe` 用法、验证步骤、找不到 feed 的出路、当前源清单状态 | ✅ **已完成** |
| `docs/11_article_store_guide.md` | **文章总表与落盘指南**：`dna list/show/add/refetch/stats`、状态含义、落盘目录结构、后续功能如何取用 | ✅ **已完成** |
| `docs/issues/NNN-*.md` | 现象 + 复现脚本 + 精确命令 + 结论 | 随时 |
| `docs/git_commands.md` | 各阶段 git 命令汇总（**你手动执行**） | 每阶段 |

项目专属资产（要求 4）：Skill `~/.claude/skills/dailynews-dev/SKILL.md`（P0 建）；
Memory `~/.claude/projects/c--Users-test-Downloads-xkd/memory/`。

---

## 12. 编码规范（要求 7）

- 严格单向依赖 `frontends → apps → narration/render → pipeline → sources/inbox/llm/tts/store → core`，**禁止反向 import**
- 每模块对外只暴露少量函数/类，内部私有化
- 核心函数中英双语 docstring：

```python
def cluster_by_embedding(items: list[NewsItem], threshold: float = 0.82) -> list[Cluster]:
    """
    按语义向量对多源新闻做层次聚类，把同一事件的不同报道合并为一个 Cluster。
    Cluster multi-source news by semantic embedding so that different reports of the
    same event are merged into a single Cluster.
    """
```

- 类型注解齐全；算法函数纯函数化，I/O 分离，便于单测
- 密钥只从 `.env` 读取，代码内零硬编码
