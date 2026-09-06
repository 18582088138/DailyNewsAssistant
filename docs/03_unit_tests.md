# 03 单元测试说明 / Unit Test Reference

> 每个基础功能都必须配单元测试；测试文件**头部注释**写完整复测命令与说明，本文件做汇总索引。
> 规矩：**某模块测试不通过，不进入下一模块。**

---

## 通用命令

```bash
# 环境
conda activate ov_env_py312
# 或直接用绝对路径（bash 下推荐，conda run 会吞 -c 参数）
PY=/c/Users/test/miniforge3/envs/ov_env_py312/python.exe

cd /c/Users/test/Downloads/xkd/DailyNewsAssistant

$PY -m pytest                    # 默认：只跑离线快测
$PY -m pytest -v                 # 带用例名
$PY -m pytest -m ""              # 全量，含 live / slow
$PY -m pytest -m live            # 仅需联网 / 真实 LLM / 真实飞书
$PY -m pytest -m slow            # 仅耗时项（TTS 合成、E2E）
$PY -m pytest --cov=dna          # 覆盖率（需 pytest-cov）
```

### 标记约定

| 标记 | 含义 | 默认 |
|---|---|---|
| 无标记 | 纯离线，不联网不落真实盘 | ✅ 跑 |
| `@pytest.mark.live` | 真实调用外部服务，**产生实际费用** | ⬜ 跳过 |
| `@pytest.mark.slow` | 耗时长（TTS 合成、端到端） | ⬜ 跳过 |

默认跳过规则写在 `pyproject.toml` 的 `addopts = "-q -m 'not live and not slow'"`。

### 💰 LLM 测试纪律（必须遵守）

真实 LLM 调用**要花钱**，所以：

1. **日常开发一律用假响应**——`tests/llm/fakes.py` 提供 `FakeOpenAIClient`（冒充 SDK）
   与 `ScriptedProvider`（冒充 provider）。新增涉及 LLM 的功能时，单测断言的应该是
   **提示词构造是否正确**与**返回解析是否正确**，而不是去问真实模型
2. **`-m live` 只在阶段验收时手动跑一次**，不要放进日常循环
3. `pytest`（默认）不会产生费用；⚠️ **`pytest -m ""` 会把 live 跑起来**，慎用
4. **本地 LLM 测试已冻结**：`test_live_ollama` 标记为 skip。P1 已验证路径可用，
   但本地方案当前不成熟，接口保留、测试冻结

---

## P0 脚手架

**最近一次全量结果：95 passed in 0.49s（0 failed）**

### `tests/core/test_config.py` — 配置加载

```bash
$PY -m pytest tests/core/test_config.py -v
```

| 覆盖点 | 说明 |
|---|---|
| Settings 默认值 | 不依赖本机 `.env`，构造时传 `_env_file=None` 隔离 |
| `.env` 覆盖 | 文件中的值应覆盖代码默认值 |
| 路径解析 | 相对路径按仓库根解析为绝对路径（`output_path` / `data_path` / `db_file`） |
| **飞书白名单** | 逗号分隔、去空格、去重；**空值必须得到空列表 = 拒收全部** |
| `api_key_for()` | 云端 provider 返回 key，本地 provider 返回空串 |
| **代理配置** | `localhost_bypasses_proxy()` 能识别 NO_PROXY 漏放行 localhost |
| `load_sources()` | 只返回 enabled；id 重复报 `ConfigError`；文件缺失报 `ConfigError` |
| `load_profile()` | 字段类型正确（`languages` 转 `Language` 枚举、时长转元组） |
| 仓库自带配置 | `config/sources.yaml` 与 `profile.yaml` 必须真实可加载（防手写笔误） |

**预期**：20 passed，< 2s，不联网、不碰真实 `outputs/` 与 `data/`。

---

### `tests/core/test_models.py` — 核心数据模型

```bash
$PY -m pytest tests/core/test_models.py -v
```

| 覆盖点 | 说明 |
|---|---|
| `NewsItem` 校验 | 空标题报错；未知字段报错（`extra="forbid"`，防拼写错误静默生效） |
| `MediaAsset` | 必须携带 `source_url`，保证 references 能标注出处 |
| `Cluster` | `canonical` 取首个成员；`refs` 去重且保序 |
| 双语回退 | 缺英文时 `title(EN)`/`summary(EN)` 回退中文，渲染层永远拿不到 `None` |
| `has_language()` | 判断某语言是否已成稿——**补译功能的判定依据** |
| `video_entries()` | 只返回 `need_video=True` 的条目 |
| `entry_by_id()` | 命中与未命中——**重做功能的定位依据** |
| **JSON 往返无损** | `_digest.json` 是可重放事实源，重做功能完全建立在这一点上 |

**预期**：13 passed，< 2s。

---

### `tests/core/test_naming.py` — 命名与路径规则

```bash
$PY -m pytest tests/core/test_naming.py -v
```

| 覆盖点 | 说明 |
|---|---|
| 期次目录 | 必须是 `YYYYMMDD-DailyNews`（用户指定格式，不可改） |
| slug 基础 | 中英混排、空白与标点折叠成单个 `-`、超长截断 |
| **Windows 非法字符** | `<>:"/\|?*` 逐个参数化验证必须被剔除，否则建目录直接 `OSError` |
| **Windows 保留名** | `CON`/`PRN`/`NUL`/`COM1`…必须回退为 `untitled` |
| 结尾点与空格 | Windows 不允许，必须剥掉 |
| 空标题回退 | 空串、纯标点 → `untitled` |
| `topic_dir_name()` | 两位补零序号 + slug；序号 < 1 报错 |
| 端到端路径 | 用真实中文标题拼完整相对路径，逐段验证 Windows 可用 |

**预期**：26 passed，< 1s，纯字符串运算无 I/O。

> 这组测试参数化程度高（37 个用例），因为目录名一旦出错是**运行期**才炸，且在 Windows 上错得很隐蔽。

---

### `tests/core/test_doctor.py` — 环境自检

```bash
$PY -m pytest tests/core/test_doctor.py -v
# 对应的人工验证
$PY -m frontends.cli.main doctor -v
```

| 覆盖点 | 说明 |
|---|---|
| Python 版本 | 当前环境必须 ≥ 3.12 |
| 依赖分级 | P0 缺失 = FAIL；后续阶段缺失只能 WARN（依赖按阶段增量装） |
| LLM 配置 | 云端缺 key → FAIL；本地 provider 无需 key → OK；**输出必须脱敏** |
| **代理** | NO_PROXY 漏放行 localhost → FAIL（否则本地 Ollama/RSSHub 被代理拦截） |
| **远程投递** | 未启用 → SKIP（部署在私人电脑）；启用缺凭证 → FAIL；**启用但白名单为空 → FAIL** |
| TTS 模型 | 空/不存在/无 IR → WARN；有 `*.xml` → OK |
| 目录可写 | 自动创建并写临时文件验证 |
| 汇总 | `run_all` / `summarize` / `has_failure` |

**预期**：19 passed，< 3s，只在 `tmp_path` 下建目录。

**人工验证预期输出**（本机 2026-09-01）：

```
合计：15 OK · 2 WARN · 0 FAIL · 1 SKIP
环境就绪。
```

两个 WARN 分别是 P2 的 `feedparser`/`trafilatura` 和 P9 的 `lark_oapi`——到对应阶段再装，属预期。
SKIP 是飞书投递（部署在私人电脑，开发机不启用）。

---

### `tests/frontends/test_cli.py` — CLI 前端

```bash
$PY -m pytest tests/frontends/test_cli.py -v
```

| 覆盖点 | 说明 |
|---|---|
| `dna version` | 输出版本号 |
| `dna doctor` | 可运行；无阻塞项退出码 0，有阻塞项退出码 1（脚本/CI 门禁） |
| **`dna config`** | **不得泄露完整密钥**，只显示前 7 位（用户可能截图分享终端） |
| `dna sources` | 列出仓库自带订阅源 |
| 无参数 | 打印帮助而非报错 |

**预期**：7 passed，< 5s。

---

## P1 LLM 抽象层

**最近一次全量结果：189 passed in 1.51s（0 failed，2 个 live 用例默认跳过）**

### `tests/llm/test_parsing.py` — LLM 输出解析

```bash
$PY -m pytest tests/llm/test_parsing.py -v
```

| 覆盖点 | 说明 |
|---|---|
| 纯净 JSON | 对象与数组直接返回 |
| 代码块 | ` ```json ` 与无语言标记的 ` ``` ` |
| 夹带解释文字 | 「好的，结果如下：」这类前后缀 |
| **字符串里的括号** | `{"note": "价格 } 上涨"}` 不能被截断——贪婪正则必然在此出错 |
| 转义引号 | `\"` 不能被误判为字符串结束 |
| 嵌套 | 嵌套对象、对象数组 |
| 异常 | 空输出 / 无 JSON / 括号未闭合 → `ProviderResponseError`，且报错被截断不灌日志 |
| 思维链 | `strip_think_tags` 去 `<think>…</think>`，含与 JSON 提取的组合场景 |

**预期**：24 passed，< 1s，纯字符串处理无 I/O。

### `tests/llm/test_provider.py` — Provider 实现

```bash
$PY -m pytest tests/llm/test_provider.py -v
# 真实调用（需联网 / 需 Ollama 在跑）
$PY -m pytest tests/llm/test_provider.py -m live -v
```

| 覆盖点 | 说明 |
|---|---|
| 正常返回 | 文本、token 用量、耗时 |
| 参数传递 | model / temperature / max_tokens / messages 原样送达；未设 max_tokens 时不发该字段 |
| 思维链 | 内联 `<think>` 剥离；**独立 `reasoning` 字段单独保存** |
| **截断诊断** | `finish_reason=length` + 空正文 → 报错点明「被 max_tokens 截断、预算被思维链耗尽」，且**标记为不可重试**（见 issue 001） |
| **错误分类** | 429→`RateLimitError`(可重试)、401→`AuthError`(**不可重试**)、timeout→`ProviderTimeoutError`、其它→可重试 |
| chat_json | 纯净/代码块解析；校验失败带错误重试并修复；重试用尽抛错且次数 = `max_repair+1` |
| **Schema 回声** | 模型抄回 Schema 时显式识别并给出针对性纠正（见 issue 002）；四种特征字段参数化 |
| **服务端 JSON 模式** | `chat_json` 发 `response_format`；普通 chat 不发；端点不支持时自动降级重试 |
| Ollama | base_url 归一化（4 种写法）、标记为本地、复用同一套解析 |
| health_check | 成功与失败路径 |

**预期**：离线 31 passed；`-m live` 时 DeepSeek 用例真实调用通过，Ollama 未运行则跳过。

### `tests/llm/test_factory.py` — 工厂与容错

```bash
$PY -m pytest tests/llm/test_factory.py -v
```

| 覆盖点 | 说明 |
|---|---|
| build_provider | 5 种 provider；大小写不敏感；OpenVINO 占位调用时给出「P10 实现」的明确指引 |
| 配置校验 | 缺 key / 缺 model → `ConfigError` 且指名要设哪个环境变量；未知名列出可选值 |
| **退避策略** | 指数增长 1→2→4→8，被 `max_delay` 截断 |
| **重试** | 可重试错误退避后成功，`attempts` 计数正确；**退避通过注入的假 sleep 完成，测试不真的等待** |
| **不重试** | 鉴权失败一次即放弃，退避列表为空 |
| **降级** | 主 provider 用尽 → 切备用；鉴权失败 → 立即切备用不浪费退避；主备都挂 → 抛最后一个错 |
| get_llm 组装 | 备用与主相同 / 备用未配置 → 不挂备用（坏备用比没备用更糟） |
| 类型契约 | `ResilientProvider` 本身是 `LLMProvider`，`chat_json` 可穿透使用 |

**预期**：25 passed，< 2s。

> 降级逻辑测得重是有原因的：OpenRouter 免费层限流已经在 doc_analyzer 上踩过（issue 002）。
> 一期日报要跑十几次 LLM 调用，中途被限流就整期作废，而这类 bug 表现为「偶尔失败」，极难复现。

### P1 真机验证结论

| 项 | 结果 |
|---|---|
| DeepSeek（云端，默认） | ✅ 通过，`-m live` 自动化覆盖（**验收时才跑**） |
| Ollama `qwen2.5:3b` | ✅ chat + chat_json 均一次通过 |
| Ollama `qwen3.5:9b`（推理模型） | ✅ 通过，但回答一个字耗 **2244 tokens / 314 秒** |

> ⏸ **本地 LLM 已于 P1 结束后冻结**：上述 Ollama 验证结果保留作为记录，
> `test_live_ollama` 已标记 skip，后续阶段不再对本地 LLM 做功能开发与测试。

真机验证暴露并修复了两个问题，均已归档：
[issue 001](issues/001-ollama-reasoning-token-budget.md)（推理 token 预算）、
[issue 002](issues/002-small-model-echoes-json-schema.md)（小模型抄回 Schema）。

---

## P2 信息源与抽取层

**最近一次全量结果：316 passed in 2.31s（0 failed）** —— 本阶段**完全不涉及 LLM**，无费用。

### `tests/core/test_urls.py` — URL 规范化

```bash
$PY -m pytest tests/core/test_urls.py -v
```

| 覆盖点 | 说明 |
|---|---|
| 大小写 | scheme/host 转小写，**path 保持大小写敏感**（bilibili 的 BV 号等 id 大小写有意义） |
| 端口 | 省略 http:80 / https:443，非默认端口保留 |
| **剔除追踪参数** | `utm_*` 前缀 + `fbclid`/`spm`/`from`/`share_source` 等具名参数，10 个参数化用例 |
| **保留内容参数** | `?id=123`、微信的 `__biz/mid/idx/sn`——清空会把不同文章合并成一条 |
| 参数排序 | 仅顺序不同的 URL 规范化为同一个 |
| fragment / 斜杠 | 去 `#comments`、去末尾斜杠（根路径除外） |
| `url_hash` | 稳定、跨追踪参数一致、不同文章不同、定长 16 位 |
| `extract_urls` | 中文文本提取、去重保序、**剥掉结尾中英文标点**（手机转发几乎必带） |

**预期**：41 passed，< 1s。

> 规则太松会让同一篇文章在日报里出现两次；太严会把不同文章合并、丢内容。
> 两种错误都只在生产数据上才显形，所以这里测得细。

### `tests/sources/test_rss.py` — RSS 解析与源装配

```bash
$PY -m pytest tests/sources/test_rss.py -v
```

| 覆盖点 | 说明 |
|---|---|
| 正常解析 | 标题、链接、摘要、发布时间 |
| 标题空白 | 换行与连续空格必须压平（否则污染目录 slug 与排版） |
| **缺 pubDate → None** | 不能回填「现在」，否则这条永远排最前且跨日去重失真 |
| **缺 link/title → 跳过** | 没有链接就无法抽正文，留着变成空条目 |
| 瑕疵容忍 | bozo 但有条目的 feed 继续可用（真实 feed 十有八九带瑕疵） |
| **HTML 错误页必须报错** | 站点改版后返回 200 + HTML 404 页；**feedparser 对此 bozo=False**，靠 bozo 判断会静默记成「成功 0 条」（issue 003-A） |
| **合法空 feed 不算错** | 低频源今天没新内容是正常的 |
| RSSHub 路由 | 5 种写法的拼接容错 |
| **registry 错误隔离** | 一个源抛异常（含未预期类型）不中断整轮；per-source 计数；summary 体现失败数 |

**预期**：25 passed，< 2s，全程离线（用 `tests/fixtures/sample_feed.xml`）。

### `tests/sources/test_user_link.py` — 用户投递

```bash
$PY -m pytest tests/sources/test_user_link.py -v
```

| 覆盖点 | 说明 |
|---|---|
| 文本→条目 | 中文句子里提链接、多链接保序、标题留空待抽取层补 |
| **按规范化 URL 去重** | 微信分享（`from=groupmessage`）与浏览器复制（`utm_source`）指向同一文章时只留一条 |
| via 标记 | GUI 手动粘贴 vs 飞书投递 |
| UserLinkSource | 累积、**跨批次去重**、fetch 上限、clear |

**预期**：16 passed，< 1s。

### `tests/extract/test_extract.py` — 正文与媒体抽取

```bash
$PY -m pytest tests/extract/test_extract.py -v
```

| 覆盖点 | 说明 |
|---|---|
| 正文抽取 | 抽出正文且**不含导航/侧栏/页脚**（去模板是本层核心价值） |
| 标题 | og:title 优先于 `<title>`（后者常带「_站点名」后缀） |
| **失败降级** | 抽不到正文时降级为「仅标题+链接」而非抛异常——链接本身仍有价值 |
| 畸形 HTML | 4 种坏 HTML 不崩 |
| 图片优先级 | og:image 优先，但**必须先过图标过滤** |
| **懒加载** | 取 `data-src` 而非占位 `src`，否则每条配图都是同一张 loading 图 |
| **logo 过滤** | og:image 是站点 logo 时过滤（实测量子位如此），否则每条日报封面都一样（issue 003-B） |
| **按词匹配** | `overhead-view.jpg`/`iconic-moment.jpg` 等真实配图不能被误杀；只看 path 不看域名 |
| 追踪像素 | 1x1 gif 过滤 |
| 地址补全 | 相对路径与协议相对（`//cdn...`）地址 |
| 视频 | bilibili iframe 识别为官方视频；广告 iframe 不误判 |
| **出处可追溯** | 每个资产必带 `source_url` 与 `credit`——发布合规要求，事后补不回来 |

**预期**：26 passed，< 3s，全程离线（用 `tests/fixtures/sample_article.html`）。

### P2 真机验证

```bash
$PY -m frontends.cli.main fetch --limit 3            # 采集预览
$PY -m frontends.cli.main fetch --source qbitai --limit 1 --extract   # 含正文抽取
```

**不调用 LLM，无费用**，可放心重复执行。

验证结果：4 个源可用（量子位 / InfoQ / HackerNews / arXiv），发现并处理了 3 个失效源；
正文抽取 4137 字、结构干净；配图修复后 5 张全部为真实文章图。
过程中发现的三个问题见 [issue 003](issues/003-p2-live-verification-findings.md)。

---

## P2+ 落盘与台账（应用户要求从 P4 提前）

**最近一次全量结果：402 passed in 4.67s（0 failed）** —— 本阶段**完全不涉及 LLM**，无费用。

### `tests/sources/test_filters.py` — 条目过滤

```bash
$PY -m pytest tests/sources/test_filters.py -v
```

| 覆盖点 | 说明 |
|---|---|
| exclude | 命中即丢，**优先级高于 include**（同时命中也丢） |
| include | 非空时一条未命中即丢；留空 = 不限制 |
| 匹配范围 | **标题 + 源自带摘要**，大小写不敏感（软文标题常看不出来，摘要才露馅） |
| min_title_length | 滤掉「快讯」这类空标题 |
| max_age_days | 滤旧条目；**没有发布时间的条目不受此限**（很多 feed 不给 pubDate，误判会整源误杀） |
| 规则合并 | 全局排除词对所有源生效并去重；**include 只取源级不从全局继承** |
| 快路径 | 无规则时全量保留，不逐条判断 |
| 丢弃原因统计 | 用户要能知道「今天为什么只有 N 条」 |

**预期**：22 passed，< 1s，纯函数无 I/O。

### `tests/store/test_ledger.py` — 文章台账（总表）

```bash
$PY -m pytest tests/store/test_ledger.py -v
```

| 覆盖点 | 说明 |
|---|---|
| 建库 | 首次使用自动建库建表；重复打开不重置数据 |
| **跨日去重** | 二次登记返回 is_new=False；**带不同追踪参数的同一篇算一篇** |
| **feed_title** | 源自带标题单独保存、每次采集从源刷新——防止降级抽取污染后不可恢复（issue 004-B） |
| 抓取结果 | ok / **degraded（不是 failed）** / failed；fetch_count 累加；重抓成功清除旧错误 |
| 错误截断 | 超长错误信息截断，避免撑爆数据库 |
| 查询 | 按状态 / 来源 / 关键词筛选，按首次出现时间倒序 |
| 统计 | count_by_status / count_by_source / total / pending_ids |

**预期**：23 passed，< 2s，只在 tmp_path 下建库。

### `tests/store/test_article_store.py` — 文章落盘

```bash
$PY -m pytest tests/store/test_article_store.py -v
```

| 覆盖点 | 说明 |
|---|---|
| 目录命名 | 日期 + 标题 slug + id 后缀；同标题不同文章不冲突 |
| article.md | frontmatter 可 YAML 解析；**标题里的冒号转义**（否则解析直接失败） |
| 降级标记 | 降级抽取在文件里明确警示，避免人工核对时误以为抓成功 |
| meta.json | 可反序列化回 Article——重做输出时不必重抓 |
| **Referer** | 下载配图必须带原文地址，否则图床防盗链全部 403（issue 004-A） |
| **文件头判格式** | 不信 URL 后缀（`.php` 返 JPEG、`.jpg` 返 HTML 都很常见） |
| 过小文件 | 下载后按字节数剔除图标（抽取阶段无法判断，很多站点不写宽高） |
| 出处 sidecar | 每张图配同名 .json，图片被单独拷走时信息不丢 |
| **失败隔离** | 单张图失败不影响正文落盘，原因记入 skipped_images |

**预期**：20 passed，< 2s，图片下载全部 monkeypatch 拦截，不联网。

### P2+ 真机验证

```bash
dna add <文章链接>          # 抓指定链接
dna show <id>               # 核对抓取结果
dna list                    # 总表
dna fetch --limit 2         # 全量采集入库
dna stats                   # 统计
```

验证结果：14 条采集、4 条降级、0 条失败；指定链接抓取正文 4137 字 + 配图 5 张。
过程中发现并修复两个问题，见 [issue 004](issues/004-image-hotlink-and-title-overwrite.md)。

---

## P2++ 视频落盘与人工补正文（445 passed）

| 测试文件 | 覆盖 | 条数 |
|---|---|---|
| `tests/store/test_video_store.py` | 直链判定 · 文件头校验 · 防盗链 Referer · 失败隔离 · 限量 · yt-dlp 分发 | 18 |
| `tests/store/test_article_store.py` | 新增：粘贴标记 · `read_body` / `read_title` 回读 · 旧目录清理 · 重抓清空旧图 · 视频清单 | +10 |
| `tests/store/test_ledger.py` | 新增：`set_body` 升级状态与标题 · 视频数记下载成功数 | +4 |
| `tests/core/test_urls.py` | 新增：`title_from_url` 七种 URL 形态 + 两篇失败文章不同名 | +8 |
| `tests/extract/test_extract.py` | 新增：按 class/alt 过滤作者头像与二维码，且不误伤正文图 | +2 |

复测：

```bash
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/store/test_video_store.py -v
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest   # 全量 445 passed
```

**yt-dlp 与网络全部被 monkeypatch 拦截**，不联网、不下载、不产生费用。

### P2++ 真机验证

用户提供的三个真实链接：

```bash
dna add "https://mp.weixin.qq.com/s/XhU4W02gvLxm77el13cpIQ?scene=1&click_id=338847213"         "https://zhuanlan.zhihu.com/p/2067333343717355528"         "https://mp.weixin.qq.com/s/VgO-WiLWNRzuSGSweHrY7g"
```

| 链接 | 结果 |
|---|---|
| 微信 MiniMax H3 | ✅ 正文 3151 字，配图 2 张（原文只有 1 张正文图，头像已正确过滤） |
| 微信 DeepSeek-V4-Flash | ✅ 正文 4069 字，配图 **10 张**（上限提升前是 5 张） |
| 知乎专栏 | ❌ 403 强反爬，降级保存；手工补正文后 `dna sync` 回写为 `ok / 47 字`，标题同步更新 |

发现并修复 6 个问题，见 [issue 005](issues/005-wechat-zhihu-live-verification.md)。
视频下载路径三个链接均未涉及（都没有嵌入视频），仅由单元测试覆盖。

---

## P3 Pipeline 核心（610 passed）

| 测试文件 | 覆盖 | 条数 |
|---|---|---|
| `tests/pipeline/test_clean.py` | NFKC 折半角但保中文标点 · 样板整行剥离 · 成稿门槛 · 脏数据返回 None | 35 |
| `tests/pipeline/test_dedup.py` | SimHash 跨进程稳定 · 词序敏感 · 三级合并 · **不同事件不误合并** · 向量层降级 | 22 |
| `tests/pipeline/test_score.py` | 权重合计 1.0 · 五个信号 · need_video 三条件 · 先排序后截断 | 23 |
| `tests/pipeline/test_summarize.py` | 提示词构建 · 截断 · 多源说明 · 禁止编造 · **失败降级为标题** | 13 |
| `tests/pipeline/test_translate.py` | 按 id 对齐 · 未知 id 丢弃 · 漏译不填空串 · 分批隔离 | 13 |
| `tests/pipeline/test_trend.py` | 编号输入 · 禁止逐条复述 · 条目不足不调用 · 失败返回 None | 11 |
| `tests/pipeline/test_flow.py` | **dry_run 零调用** · 字段齐全 · refs 取整簇 · 双语回填 · 统计 | 17 |
| `tests/pipeline/test_source.py` | 正文从 meta.json 读回 · 状态筛选 · 目录缺失降级 · 人工指定 id | 12 |
| `tests/llm/test_cache.py` | **第二次不调用底层 provider** · 键包含模型与参数 · 损坏当未命中 · 失败不缓存 | 16 |
| `tests/core/test_config.py` | 新增：台账跟随 `DATA_DIR`（issue 006-A 回归） | +3 |

复测：

```bash
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/pipeline/ -v
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest   # 全量 610 passed
```

**全部使用假 provider（`tests/llm/fakes.py`），零 LLM 调用、零费用、不联网、
不加载向量模型。**

### P3 真机验证（一次，DeepSeek）

```bash
dna digest --dry-run --limit 8    # 免费：确认选题与排序
dna digest --limit 5              # 计费：6 次调用 / 10.7 秒
dna digest --limit 5              # 复跑：0 次调用 / 0.1 秒（缓存 6/6 命中）
dna digest --limit 5 --bilingual  # 2 次调用（摘要走缓存）
```

结果：`outputs/20260902-DailyNews/_digest.json`，5 条中英双语条目 + 主线综述。

- 主线综述正确提炼出「效率与成本优化」这条跨条目共性，未逐条复述标题
- 技术术语翻译原样保留：`2TP×4USP`、`XPU Kernel`、`Layer-wise Offloading`
- **总计 8 次 LLM 调用**——缓存让复跑与调试不再计费

开发过程中发现并修复 2 个问题，见 [issue 006](issues/006-p3-config-and-normalisation.md)。

---

## P3.5 台账工作台（705 passed）

| 测试文件 | 覆盖 | 条数 |
|---|---|---|
| `tests/narration/test_duration.py` | 语速与字数换算 · **不发音内容不计时长** · 回炉反馈给具体差值 | 25 |
| `tests/narration/test_script_builder.py` | 专业性约束在提示词里 · **达标不回炉** · 回炉带上一稿 · 上限后返回而非报错 | 16 |
| `tests/narration/test_longform.py` | 短文拒绝且零调用 · 分段调用计数 · 带上一节结尾 · 双角色 · 两模式同一 JSON 结构 | 19 |
| `tests/produce/test_service.py` | **已有产物零调用** · force 插新版 · 前置自动补 · 失败也记账 · **长文案不进 --all** | 14 |
| `tests/store/test_migrate_layout.py` | dry-run 零改动 · 幂等 · 冲突不猜 · **孤儿只报告不删除** | 11 |
| `tests/store/test_ledger.py` | 新增：产物版本链 · 一次查完整页矩阵 · 失败入账 · `set_store_dir` 不误改其它字段 | +8 |
| `tests/extract/test_extract.py` | 新增：arXiv 基金会 logo 被拦 · 赞助方标记不误伤 `partnership-diagram` | +2 |

复测：

```bash
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/narration/ tests/produce/ -v
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest   # 全量 705 passed
```

**全部使用假 provider，零 LLM 调用、零费用、不联网。**

### P3.5 真机验证（DeepSeek，共 11 次调用）

```bash
dna migrate-layout            # 21 个目录迁到 outputs/，台账同步更新
dna produce decef4a4 --all    # 4 次调用（短视频回炉 1 次，共 5 次）
dna produce decef4a4 --kind longform --variant interview   # 7 次调用，29 秒
dna gui                       # HTTP 200，表格五列全绿
```

| 产物 | 结果 |
|---|---|
| 总结 | 235 字 |
| 英文总结 | 562 字 |
| 短视频文案 | 359 字，**26 秒**（落在 25~35s；首稿超长，回炉一次） |
| 口播文案 | 964 字，**99 秒**（落在 60~120s） |
| 长文案（访谈） | 3900 字，7.4 分钟，29 轮，主持人 14 / 嘉宾 15 |

**数值保真度核验**：生成内容里出现的 `304B`、`167GB`、`164.6`、`0.029`、`0.0102`、
`UD-Q8_K_XL`、`MXFP4`、`96%` **全部能在原文中找到，零编造**。

发现并处理 3 个问题，见 [issue 007](issues/007-p35-workbench.md)。

---

## P3.5 bis 文案质量返工（722 passed）

用户给了一份人工撰写的参考稿，指出「信息量不够，同时没有把文章讲清楚」。
根因不在提示词措辞，在**字数换算的单位**——见
[issue 008](issues/008-copy-information-density.md)。

| 测试文件 | 新增覆盖 | 条数 |
|---|---|---|
| `tests/narration/test_duration.py` | **`prompt_char_budget` 大于 `target_chars`**（两者是不同的量）· 实测密度反映中英比例 · 退化输入不除零 · **回炉差值按上一稿密度换算** | 25 → 31 |
| `tests/narration/test_script_builder.py` | **信息密度是第一要求** · 逐条点名禁止的填充句式 · 短视频**覆盖主干**而非单点 · CTA 由调用方传入 · **字数预算断言 168~235 而非 112~157** · 口播也返回主副标题 | 16 → 21 |
| `tests/narration/test_longform.py` | 时长窗口可由 profile 覆盖 · **profile 默认值与代码常量一致** · **每节看得见完整提纲并标出边界** · 提纲禁止「其他」类兜底节 | 19 → 25 |
| `tests/pipeline/test_summarize.py` | 信息完整性 · **指标取舍优先级** | 13 → 15 |
| `tests/produce/test_service.py` | **profile 的时长区间与 CTA 真的传到构建器**（此前是死配置）· 产物文件写入主副标题 | 14 → 16 |

**为什么字数预算要断言具体数值**：它是「信息量不够」的机械原因，不是风格问题。
按纯中文 4.5 字/秒换算，30 秒只要 135 字，而实测技术稿装得下约 200 字——
模型照少的写，**时长照样达标、回炉不触发，错误完全静默**。所以必须锁在测试里。

### 真机复验（DeepSeek，同一篇，4 次调用）

```bash
dna produce decef4a4 --all --force
```

| 产物 | 改前 | 改后 |
|---|---|---|
| 短视频 | 185 字 / 26 秒，只讲量化一个点 | **289 字 / 31 秒**，8 个事实 |
| 口播 | 约 550 字 / 99 秒，含第一人称与形容词结尾 | **840 字 / 94 秒**，无第一人称 |
| 总结 | 167GB + 量化一致 | 167GB + 13 档 + KLD 0.0102 + IQ3_XXS 103GB |

**数值保真度复核**：`304B`、`8-bit`、`167GB`、`13 档`、`UD-Q8_K_XL`、`MXFP4`、
`0.0102`、`0.0747`、`8 倍`、`Hopper`、`Blackwell`、`384K` 全部能在原文中找到，
零编造。

**长文案实跑两篇**（用户要求），发现分节重复缺陷并修复，见 issue 008-G：

| 文章 | 模式 | 改前 | 改后 |
|---|---|---|---|
| CES 2026（17714 字） | 专题 | 4078 字 / 628 秒，7 节，**第 7 节 9 个实体全部重复第 6 节、零新信息** | **5524 字 / 902 秒**，8 节含局限节，最严重重复是局限节点评前文（预期行为） |
| MiniMax H3（3151 字） | 访谈 | 3639 字 / 539 秒，主持人 15 / 嘉宾 15，含局限节 | 未复跑 |

根因：`previous_tail` 只给上一节最后 120 字，模型不知道前面几节覆盖了什么。
修复是在每节提示词里放完整提纲并标注「已讲过 / 现在写这节 / 留给后面」，
**不增加调用次数**。

---

## P3.5 ter 工作台改版 + 链接导入 + NEW 标识（752 passed）

| 测试文件 | 覆盖 | 条数 |
|---|---|---|
| `tests/frontends/test_workbench.py` | 标题按**字符数**截断（中英一视同仁）· 空标题回退 · **列宽由产物种类数算出**（加一列不会让表头与数据错位）· `spoken` 标注正确 · **链接导入**：整段中文里认链接、剥中文标点、去重 · `production_file`/`media_folders` 文件不在时返回 None（按钮据此禁用）· `open_in_file_manager` 返回原因而非抛异常 · 非本机绑定时拒绝 · 时长预估走核心公式 · **NEW 用第四种颜色**（四色互不相同）· 减少动效时只关动画、标识仍在 | 22 |
| `tests/produce/test_service.py` | 新增：**NEW 标识判定**——刚导入且无产物为新 · 任意产物清掉标识 · **失败的尝试同样清掉** · **存量文章不算新的**（32/37 篇无产物，只看产物会让标识失效）· 窗口设 0 关闭时间检查 | +5 |

**为什么只测这些**：布局与观感**测不出来**，靠 `dna gui` 人工看。
这里锁的是纯函数与文件系统契约——它们出错时界面会**安静地显示错误的东西**：
该禁用的按钮没禁用、点下去拿到 404、或者表头与数据列错开一格。

### 人工验证（Playwright 截图，零费用）

| 项 | 结果 |
|---|---|
| 1600px 宽 | 列对齐，无重叠 |
| **900px 宽** | **无重叠**——出横向滚动条，标题省略号截断 |
| 滚动 600px | **表头冻结**，行从下面滑过 |
| 点格子 | 展开该产物全文，格子高亮，重做/下载/打开文件夹在面板里 |
| 导入对话框 | 粘贴含 3 条链接的中文段落，全部识别，**结尾中文逗号句号已剥掉** |
| NEW 标识 | 37 篇里**只有 1 行**挂 NEW——同一分钟导入的另两篇已有产物，正确不挂；「只看新导入」筛出 1 行 |

---

## 清理与干净检出验证（753 passed）

| 测试文件 | 变化 |
|---|---|
| `tests/core/test_doctor.py` | `run_all` 现在注入临时 `.env`；新增「`.env` 缺失必须是阻塞项」的反向测试 | +1 |

### 为什么之前干净检出会挂

`.env` 按约定不入库，克隆里没有它，`check_env_file` 因此 FAIL。
而 `run_all` 把路径写死，测试读的是**开发机上那个未跟踪的文件**——
于是测试「在我机器上过、在干净克隆里挂」，挂的原因和被测代码毫无关系。

**这类问题只有干净检出能发现**：工作区里文件都在，本地全绿。
复现与验证：

```bash
git clone -q --no-hardlinks . /c/tmp/verify
PYTHONPATH=/c/tmp/verify/src:/c/tmp/verify $PY -m pytest -p no:cacheprovider
# 修复前：1 failed, 751 passed   修复后：753 passed
rm -rf /c/tmp/verify
```

---

## P4 期次落盘与装配（766 passed）

| 测试文件 | 覆盖 | 数量 |
|---|---|---|
| `tests/store/test_issue_store.py` | 期次目录只有两个文件 · **`graphic/`/`podcast/` 不预建**（空目录会被读成「已生成」）· `_digest.json` 往返无损 · 来源汇总列出图片与视频**原始地址** · 条目**按 id 引用不复制** · **查不到的条目不静默跳过**（文件里写 ⚠️，返回值里也有）· **目录被删同样算未定位** · 代表条目换过时按 refs 兜底 · 落盘幂等零费用 · `--refresh` 不碰 digest · 空目录不算一期 · 损坏文件返回 None | 15 |
| `tests/core/test_naming.py` | 删去 `topic_dir_name` / `history_dir_name` 两组测试（两套布局已取消）；端到端路径改成条目级 | 34 |
| `tests/extract/test_extract.py` | 新增：**复数装饰目录**（`/icons/`、`/logos/`、`/banners/`）必须被拦 | +1 |

### 装配层的两条要害

**1. 未能定位的条目必须可见。** 期次按 id 引用条目目录，目录被手工删掉时链接就
指空。少写一行链接是最坏的处理方式——产物看起来完整，实际缺了出处，而发布时
`_references.md` 正是合规依据。因此三处同时报：文件末尾列清单、`SavedIssue.unresolved`
带结构化结果、`dna issue` 那一列显示「未定位」。

**2. `--refresh` 不能碰 `_digest.json`。** 修链接是零成本操作，而重跑流水线要花钱。
两件事写在一个命令里，早晚有一次误触把钱花掉。测试直接断言 digest 字节不变。

### P4 真机验证（零 LLM 调用）

```bash
dna issue --list        # 已生成 1 期：20260902-DailyNews，来源汇总「缺失」
dna issue --refresh     # 5 条全部 ✅ 定位到条目目录，写出 104 行 _references.md
```

顺带暴露一个真实缺陷：arXiv 那条的 5 张「配图」全是页脚装饰，其中 4 张走
`/images/icons/social/...`。词表里有 `icon`，但边界规则要求其后是非字母数字，
`icons` 因此整个漏过去。**把每张图的原始地址平铺出来才看得见**——
这正是 `_references.md` 存在的意义。修法是在词边界正则末尾加 `s?`，
同时删掉 `funders`/`sponsors`/`partners` 三个手写复数（已被 `s?` 覆盖）。

---

## P4.5 语音合成层（802 passed）

| 测试文件 | 覆盖 | 数量 |
|---|---|---|
| `tests/tts/test_tts.py` | **URL 必须去掉**（不去掉模型会把网址逐字念出来）· HTML 注释里的提纲不能念 · 链接锚文本保留地址丢掉 · **只在句子边界切**且标点跟着前一句 · 空输入返回空列表而非 `[""]` · WAV 往返 · **越界样点归一化不硬裁剪** · **一段失败不毁整篇，全失败才抛** · 换发言人的停顿更长 · **实例按（后端·模型·设备·精度）缓存** · **两个角色音色相同时自动岔开** · torch 后端**加载前**就校验 CUDA/精度/设备 · **写与读不漂移** | 29 |
| `tests/produce/test_service.py` | 新增音频：**一次 LLM 都不调** · 按字节写盘且台账记真实时长 · **只念正文不念抬头里的网址** · 访谈两个角色两把嗓子（读 JSON turns）· **重做音频不会把稿子重新计费** · 音频不进 `--all` | +6 |
| `tests/core/test_doctor.py` | TTS 检查**只看当前配的那个后端**——Intel 机器上没有 CUDA 权重是正常的，报成问题只会让人学会忽略 doctor | +1 |

### 这一层的三条要害

**1. 送进模型之前必须把字清干净。** 产物抬头里有 `> 口播文案　·　来源：https://…`。
整篇照念的话，模型会把网址一个字符一个字符读出来——**不是音质变差，是整段废掉**。
而且这条失效**不会报错**：文件生成了、时长也有，只有听的人才发现。
所以渲染（`script_block`）与解析（`spoken_text`）放进同一个文件 `produce/documents.py`，
并由一条往返测试钉住它们。

**2. 一段失败不能毁整篇。** 长文案有几十段，逐段合成跑到第 40 段崩掉，
就把前面 39 段的半小时一起赔进去。缺段的音频照样落盘，
但 `complete` 为 False 且明确报出缺了几段——**残缺的音频被当成成品发出去才是最坏的结果**。

**3. 重做音频不能把稿子重新计费。** 下游重做是免费的，上游重做是要花钱的。
`produce` 原先给前置传的是 `force=force`，也就是说点一次「重新合成」会连带把
口播稿重新调一遍 LLM。改成前置一律 `force=False`：**缺了才补，不会替人做花钱的决定**。

### P4.5 真机验证（本地合成，零费用）

```bash
dna tts                                   # 9 个音色，后端就绪
dna tts --say "…46 字…"                   # 8.4 秒音频，耗时 22.3 秒，RTF 2.67
dna produce decef4a4 --kind narration_audio
#   ● 口播音频：827 字，约 153 秒　→ narration.zh.wav 7.02 MB
```

同时验证 `TTS_DEVICE` 真的生效（上游把它写死成 GPU）：

```bash
$PY -c "from dna.core.config import Settings; from dna.tts import get_tts
s = Settings(_env_file='.env', tts_device='CPU'); m = get_tts(s, fresh=True)._model_ready()
print(m.talker.device, m.speech_tokenizer.device)"
# cpu CPU        ← 配 CPU 就真的在 CPU 上
```

顺带量出一个偏差：口播稿估算 93.9 秒，实际合成 153.3 秒，**差 63%**。
本阶段不改，理由见 [issues/009](issues/009-tts-speaking-rate.md)。

---

## P3.5 quater 实测返工（813 passed）

用户实测报了六条，两条是无报错的失效，四条是功能调整。

| 测试文件 | 覆盖 | 数量 |
|---|---|---|
| `tests/produce/test_service.py` | **重做必须构造不带缓存的 provider**（断言传给 `get_llm` 的参数）· 额外指令真的进到提示词并记进台账 · **空指令不改变提示词一个字节**（否则每篇都错开缓存白花钱）· 中英两版是两个文件两条记录 · 英文总结翻译中文版 · **英文文稿原生生成**（一次调用、英文写作要求、按词给预算）· NEW 按来源判定 · **标识不因过了一年而消失** | 34 |
| `tests/narration/test_duration.py` | **英文预算不乘混排系数**（乘了就超长 50%，正是「英文文本量太大」的来源）· 单位随语言走 · **回炉反馈用稿子自己的语言写** | 34 |
| `tests/store/test_ledger.py` | 矩阵按 `(kind, lang)` 索引 · **两个语言版本是两格，互不覆盖** | 37 |
| `tests/frontends/test_workbench.py` | 去掉已合并的 `SUMMARY_EN` 断言 | 22 |

### 两条无报错失效，测试怎么钉

**重做拿回旧答案**（[issues/010](issues/010-redo-cache-and-new-badge.md) A）。
断言的不是「两次结果不同」——假 provider 本来就每次给不同答案，那样测不出任何东西。
钉的是**意图**：`force=True` 时传给 `get_llm` 的 `cache` 参数必须是 `False`。

```python
assert seen == [True, False], "首次生成走缓存，重做必须绕开"
```

**NEW 过夜消失**（同 issue 的 B）。判定里不该再有任何时间成分，
所以测试把入库时间推到**一年前**，断言标识还在。写「25 小时前」这种边界值
只能证明窗口变长了，证明不了窗口没了。

### 真机复验（1 次调用，约 0.001 元）

```bash
dna produce decef4a4 --kind summary --force
# 重做前：…304B参数，8-bit精度，模型文件167GB。Unsloth发布13档GGUF量化版本…
# 重做后：…304B参数，8-bit精度，文件167GB，后训练超越V4-Pro预览版。Unsloth发布13档…
```

内容确实变了，新版还多带出一个原文里的事实。
台账里的历史版本也留着这个 bug 的痕迹：此前两次重做的字数都是 258，一模一样。

界面走 Playwright 截图验证（零费用）：表头加粗放大、总结列合并为一列、
格子上的 `EN` 标记、展开面板里的中文/English 开关与「修改指令」开关。

---

## 待补（随阶段推进填写）

| 阶段 | 测试文件 | 状态 |
|---|---|---|
| P3 | 见上 | ✅ |
| P4 | `tests/store/test_issue_store.py`（台账测试在 P2+/P3.5 已交付；`test_history.py` 随 `_history/` 取消） | ✅ |
| P5 | `tests/apps/test_graphic_daily.py` | ⬜ |
| P4.5 | `tests/tts/test_tts.py` + `tests/produce/test_service.py` 的音频部分 | ✅ |
| P6 | `tests/apps/test_video_brief.py`（narration 与 tts 已在 P3.5/P4.5 交付） | ⬜ |
| P7 | `tests/apps/test_podcast_script.py` | ⬜ |
| P8 | `tests/frontends/test_redo.py` | ⬜ |
| P9 | `tests/inbox/test_inbox_parse.py` `test_inbox_whitelist.py` `test_inbox_commands.py` | ⬜ |
| P10 | `tests/test_e2e.py`(slow) | ⬜ |
