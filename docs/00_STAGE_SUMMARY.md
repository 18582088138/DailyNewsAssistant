# 00 阶段总览 / Stage Summary — DailyNewsAssistant

> 项目**唯一进度看板**，阶段收尾时更新。开发规则见 [../CLAUDE.md](../CLAUDE.md)，
> 文档该什么时候读见 [00_INDEX.md](00_INDEX.md)。

---

## 〇、进行中

**当前分支 `DNA_v0.1`：全面优化（减少后续开发成本 + 去冗余）。**
计划正文在 `~/.claude/plans/sequential-fluttering-wren.md`（不随仓库走，
新会话先读它再动手 —— 这一行存在的理由就是上次 `/clear` 后主线找不回来）。

| 批次 | 内容 | 状态 |
|---|---|---|
| 0 | 护栏与验收设施（hooks / tools / CLAUDE.md / ruff） | ✅ 已提交 |
| 0.1 | 补护栏的三个静默失效 + 记忆纠偏 | ✅ 已提交 |
| 1 | docs：导览 + 删腐烂 | ✅ 待人工提交 |
| 2 | 代码清理：死码 / 假配置 / 重复常量 / 分层违规 / ruff 清零 | ✅ 待人工提交 |
| 3 | 代码拆分 | ✅ 行数门清零（12 个超线 → 0）；**散文占比未做，待用户决定** |
| 4 | skill / memory / 杂物 | ✅ 固定上下文压掉三分之二 |
| 5 | 把新流程写进 `CLAUDE.md` | ✅ 待人工提交 |
| 6 | 用户验证时报的三个 bug | ✅ 待人工提交 |

批次 2 的结论与验收手段记在
[issues/014](issues/014-hardcoded-config-and-fake-settings.md)，
批次 6 记在 [issues/015](issues/015-language-sentinel-and-dialog-clipping.md)
（三条里两条是批次 2 / 批次 3 自己带出来的回归）。
散文占比那个决定已定：**参考线 40%，`check.py` 每次都报但不拦**，
理由与判据写在 `tools/check.py` 的 `PROSE_BUDGET` 注释里。

---

## 一、项目定位

每日 AI 资讯采集 → 结构化 `DailyDigest` → 派生三种发布形态（图文版 / 播客版 / 视频版）。
**架构第一约束：核心算法与后端公用，前端与应用相互独立可增删。**

详见 [01_research.md](01_research.md) 与 [02_development_plan.md](02_development_plan.md)。

---

## 二、技术栈定稿

| 环节 | 选型 |
|---|---|
| 信息源 | RSS(`feedparser`) + 自建 RSSHub(公众号/知乎/微博/X) + 用户投链接接口 |
| 远程投递 | 飞书自建应用机器人 + 长连接（`lark-oapi` ws.Client，出网即可）；白名单 open_id + hashtag 指令。钉钉 Stream、邮箱 IMAP 留作 adapter |
| 正文/媒体抽取 | `trafilatura` + `bs4` 兜底；og:image / 正文图 / 官方视频，强制记录来源 |
| 处理架构 | 单 Pipeline + LLM 节点（不用 Multi-Agent） |
| 去重聚类 | URL 规范化 + SimHash（一级）→ `bge-m3` 向量层次聚类（二级） |
| 云 LLM | **DeepSeek（默认）** / OpenRouter（备，待充值问题解决） |
| 本地 LLM | ⏸ **已冻结**：Ollama / OpenVINO 只保留接口，不做功能开发与测试 |
| TTS | Qwen3-TTS OpenVINO（`Qwen3-TTS-CustomVoice-0.6B-OV`，Intel CPU/GPU，中英双语） |
| 长图 | Jinja2 HTML + Playwright 截图 |
| 前端 | CLI + NiceGUI（含后台台账控制台） |
| 存储 | 条目级 `outputs/articles/<日期>/<slug>__<id8>/`（**唯一权威**）+ 期次级 `outputs/<日期>-DailyNews/`（只放整期产物）+ SQLite 台账。落盘细节以 [05_output_spec.md](05_output_spec.md) 为准 |

---

## 三、阶段进度

测试清单与数量现算：`python tools/check.py --list`。

| 阶段 | 内容 | 状态 | 文档 |
|---|---|---|---|
| P0 脚手架 | 配置/模型/命名/日志/环境自检 + CLI | ✅ | `README.md` |
| P1 LLM 抽象层 | DeepSeek 默认，5 provider 可互换 + 重试降级 | ✅ | [issues/001](issues/001-ollama-reasoning-token-budget.md) · [issues/002](issues/002-small-model-echoes-json-schema.md) |
| P2 信息源 + 抽取 | RSS / RSSHub / user_link / article / media | ✅ | [10_sources_guide.md](10_sources_guide.md) · [issues/003](issues/003-p2-live-verification-findings.md) |
| P2+ 落盘与台账 | 文章总表(SQLite) / 正文与图片落盘 / 按源过滤 | ✅ | [11_article_store_guide.md](11_article_store_guide.md) · [issues/004](issues/004-image-hotlink-and-title-overwrite.md) |
| P2++ 视频与人工补正文 | 视频下载 / 装饰图过滤 / `dna sync` 回写 | ✅ | [11_article_store_guide.md](11_article_store_guide.md) · [issues/005](issues/005-wechat-zhihu-live-verification.md) |
| P3 Pipeline 核心 | clean → dedup → score → summarize → translate → trend；LLM 响应缓存 | ✅ | [12_digest_guide.md](12_digest_guide.md) · [issues/006](issues/006-p3-config-and-normalisation.md) |
| P3.5 台账工作台 | NiceGUI 产出矩阵（逐格重做）/ 单篇产物层 / 三种文案 | ✅ | [07_db_schema.md](07_db_schema.md) · [13_workbench_guide.md](13_workbench_guide.md) · [issues/007](issues/007-p35-workbench.md) |
| P3.5 bis 文案返工 | 信息密度列为第一要求 / 字数预算按中英混排密度换算 | ✅ | [06_prompt_spec.md](06_prompt_spec.md) · [issues/008](issues/008-copy-information-density.md) |
| P3.5 quater 实测返工 | 重做绕开 LLM 缓存 / NEW 改按来源判定 / **语言成为产物的一个维度** / 修改指令扩展提示词 | ✅ | [issues/010](issues/010-redo-cache-and-new-badge.md) · [issues/011](issues/011-instructions-leak-and-char-count.md) |
| P4 期次落盘 | 期次目录规则 / `_references.md` / 期次装配 / `dna issue` | ✅ | [05_output_spec.md](05_output_spec.md)（落盘唯一权威） |
| P4.5 语音合成层 | `dna/tts/` 两个后端同一协议 / 清洗·分段·拼接·逐段容错 / GUI 合成 | ✅ | [14_tts_guide.md](14_tts_guide.md) · [issues/009](issues/009-tts-speaking-rate.md) · [issues/012](issues/012-tts-engine-abi-and-console.md) |
| P5 图文版 | md/html 双语 + 长图 | ⬜ | — |
| P6 视频版 | 素材归集 + 视频合成（narration 在 P3.5、TTS 在 P4.5 已交付） | ⬜ | — |
| P7 播客版 | 复用 narration：单/双人脚本 + 整期音频 | ⬜ | — |
| P8 前端 | CLI + NiceGUI + 后台台账控制台/重做 | ✅ 随 P3.5 交付 | [13_workbench_guide.md](13_workbench_guide.md) |
| P9 远程投递 | 飞书机器人长连接 + 白名单 + hashtag + 定时调度 | ⬜ 代码未实现 | [08_feishu_bot_deployment.md](08_feishu_bot_deployment.md)（部署文档已交付） |
| P10 集成 | RSSHub / OV LLM / E2E / 打包 | ⬜ | [09_packaging.md](09_packaging.md) |

---

## 四、仍然有效的方案决议

| 议题 | 决议 |
|---|---|
| 双语 | 三个场景**全覆盖**，文案与音频双语；语言是产物的一个维度，已有 Digest 可随时补英文版 |
| 场景顺序 | P5 图文 → P6 视频 → P7 播客（narration 公用层在 P6 建，P7 直接复用） |
| 飞书部署 | **不在公司电脑运行**，部署在用户私人电脑，联调由用户自行完成；公司电脑只交付代码与离线单测 |
| 图片水印 | 发布方烧进像素，抓取时**无法绕开**（外部限制，按用户决定不处理） |

---

## 五、环境注意事项（踩坑前置）

1. `conda run -n ov_env_py312 python -c "..."` 会吞掉 `-c` 参数 → 直接调 `python.exe`
2. 公司代理 `child-prc.sh.intel.com:913`：**Python 走代理，`curl` 不走** → 联网验证用 Python。
   **`NO_PROXY` 必须含 `localhost`**，否则本地 Ollama(11434) 与 RSSHub(1200) 会被代理拦截。
   **系统环境变量优先级高于 `.env`**，实际生效值以 `dna doctor` 为准
3. OpenRouter 免费层会限流 → 批量任务默认走 DeepSeek
4. Playwright 需先 `playwright install chromium`，`dna doctor` 会检查
5. `outputs/`、`data/`、`.env`、模型缓存一律 gitignore
6. 飞书 `ws.Client.start()` **阻塞主线程**，必须单起线程，否则会挡住 NiceGUI 与调度器

---

## 六、问题台账

遇到似曾相识的报错按编号查一份，**不要通读**。

| # | 一句话 | 状态 |
|---|---|---|
| [001](issues/001-ollama-reasoning-token-budget.md) | 推理模型思维链耗尽 token 预算，正文为空且报错指不到根因 | ✅ 修复 + ⚠️ 遗留性能结论 |
| [002](issues/002-small-model-echoes-json-schema.md) | 小模型把 JSON Schema 原样抄回，修复重试的反馈无效 | ✅ |
| [003](issues/003-p2-live-verification-findings.md) | feed 失效被静默记成「成功 0 条」；og:image 是站点 logo | ✅ |
| [004](issues/004-image-hotlink-and-title-overwrite.md) | 图床防盗链 403；抽取降级时站点通用标题覆盖 RSS 正确标题 | ✅ |
| [005](issues/005-wechat-zhihu-live-verification.md) | 知乎强反爬 403（改人工补正文 + `dna sync`）；配图上限偏少；孤儿图片 | ✅ |
| [006](issues/006-p3-config-and-normalisation.md) | `db_path` 不跟随 `DATA_DIR`；NFKC 把中文逗号折成半角 | ✅ |
| [007](issues/007-p35-workbench.md) | arXiv「配图」是页脚 logo；长文案按字符数推导与按秒数验收对不上 | ✅ |
| [008](issues/008-copy-information-density.md) | **字数预算按纯中文语速折算，系统性少要三分之一**，且错误完全静默 | ✅ |
| [009](issues/009-tts-speaking-rate.md) | 时长估算与实际合成差六成；Qwen3-TTS 中文语速实测值 | ⚠️ 已量出，本阶段不改 |
| [010](issues/010-redo-cache-and-new-badge.md) | **重做调了 LLM 但内容一字未变**（缓存键是提示词）；NEW 标识过夜消失 | ✅ |
| [011](issues/011-instructions-leak-and-char-count.md) | 修改指令泄漏进正文；字数统计口径不一致 | ✅ |
| [012](issues/012-tts-engine-abi-and-console.md) | TTS 引擎 ABI 不匹配；操作台交互问题 | ✅ |
| [013](issues/013-wechat-videos-and-empty-select-values.md) | 微信视频抓取；NiceGUI 空 select 值触发 `Invalid value` 崩溃 | ✅ |
| [014](issues/014-hardcoded-config-and-fake-settings.md) | **配置改了不生效**：存图上限被写死的 10 静默压掉；`.env` 的四个代理项完全不接线；18 条假配置 | 🔄 批次 2 |
| [015](issues/015-language-sentinel-and-dialog-clipping.md) | 语言哨兵空串漏到下游：整张表静默变「未生成」+ 展开面板 `KeyError: ''`；设置面板被 Quasar 裁掉没滚动条；`src/` 下落了 `data/` 与 `outputs/` | ✅ |

**issue 001 的长期约束**：本地推理模型回答一个字耗数千 token / 数百秒。因此本地模型
**不要设小的 `max_tokens`**（控长度靠提示词约束句数）；Local 迁移优先选非推理模型；
功能验证一律走云端 DeepSeek，本地性能评估留到 P10。

**issue 002 的长期约束**：所有打分类字段必须在 Pydantic 模型上写清 `ge`/`le` 与
`description` —— 实测两个模型对无约束的 `score: float` 分别脑补了 0–100 和 0–10 两种量纲。
