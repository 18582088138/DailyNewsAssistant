# 04 后端架构 / Backend architecture（`src/dna/`）

> 人工查阅与修改用的地图。每个模块一节：**它负责什么 · 核心函数 · 关键常量 ·
> 改这里要注意什么**。前端在 [`04_architecture_frontends.md`](04_architecture_frontends.md)。
>
> 找不到东西时先看最后一节的 [症状 → 文件](#症状--文件)。

---

## 唯一的架构规则

> **核心算法与后端共用；前端与应用各自独立。**

依赖严格单向，从不反向：

```
frontends ─→ produce ─→ narration ─┐
                 │                 ├─→ pipeline ─→ sources / extract / llm / store ─→ core
                 └─→ tts ──────────┘
```

`DailyDigest`（落盘为 `_digest.json`）是唯一的真相源。三种发布形态只**读**它，
所以任何单一产物都能单独重做——「补一版英文」「只重做播客」几乎免费就是这么来的。
**永远不要让应用层回写流水线，也不要把业务逻辑放进前端。**

新增一种发布形态 = 一个新的 `apps/*.py` + 模板，核心一行不动。

### 三条费用分界（改动前先确认自己在哪一侧）

| | 免费 | 计费 |
|---|---|---|
| 采集 | `fetch` `add` `refetch` `sync` `probe` | —— |
| 流水线 | clean → dedup → score | summarize → translate → trend |
| 单篇产物 | 已有产物复用（不加 `--force`）· 全部音频合成 | 文案生成 |

`dna digest --dry-run` 停在这条线上：先看选题，再决定要不要付钱。

---

## `core/` —— 配置、模型、命名、日志、异常、自检、提示词

谁都可以依赖它，它不依赖任何人。

### `core/config.py`（552 行）

**职责**：`.env` → `Settings`；`config/*.yaml` → `SourceConfig` / `Profile`。

| 核心 | 说明 |
|---|---|
| `Settings` | 字段名与 `.env` 变量名一一对应；`get_settings()` 进程内只解析一次 |
| `Settings.resolve/output_path/data_path/db_file/llm_cache_path` | **所有路径都从这里派生** |
| `load_sources()` / `load_profile()` / `safe_profile()` | 读 YAML；`safe_*` 出错时退默认值 |
| `PROJECT_ROOT` · `DEFAULT_CONFIG_DIR` · `DEFAULT_ENV_FILE` | 仓库锚点 |

**改这里要注意**：
- **相互依赖的路径不能做成各自独立的设置。** `db_path` 曾经相对仓库根解析，而
  `data_dir` 独立移动，于是改 `DATA_DIR` 会把文章搬走、台账留在原地，
  所有 `store_dir` 指向空处且不报错（issue 006-A）。
- OS 环境变量**覆盖** `.env`；`dna doctor` 显示的是真正生效的那份。

### `core/models.py`（288 行）

**职责**：贯穿全流程的数据模型，按加工阶段递进。

```
RawItem（采集，无正文）→ Article（抽取后）→ NewsItem（清洗归一化）
   → Cluster（同一事件多源合并）→ DigestEntry（成稿单元）→ DailyDigest（一期）
```

`MediaAsset` 必须携带 `source_url` + `credit`；`DigestEntry.title(lang)` /
`summary(lang)` 英文缺失时回退中文。

**改这里要注意**：`MediaAsset.source_url` 是**抽取时**采集的，事后无法重建——
它同时是署名依据和图片下载的 `Referer`（issue 004-A）。

### `core/prompts.py`（新增）

**职责**：读 `config/prompts/*.md`。提示词正文的唯一来源。

| 核心 | 说明 |
|---|---|
| `load_prompt(name, block)` | 读一段；缺文件/缺块抛 `ConfigError` |
| `render_prompt(name, block, **values)` | 填 `{{占位符}}`；**缺值报错，不静默留下** |
| `available_prompts()` · `blocks_of()` · `placeholders_of()` | 给 `dna prompt --list` 与 doctor |

**改这里要注意**：
- 缓存键含 `mtime`，所以 `dna gui` 这种长驻进程改完提示词立刻生效。
- 占位符是 `{{name}}` 而不是 `{name}`：提示词里有 `P(A|B)`、`[pause:400ms]`
  这类字面量，用 `str.format` 就得让改提示词的人记转义规则。
- **缺文件不降级。** 空 system prompt 照样发请求、照样计费。

### `core/naming.py`（106 行）· `core/urls.py`（246 行）

`slugify()` / `issue_dir_name()` / `lang_suffix_name()`：目录名必须在 Windows 上活下来——
剥 `<>:"/\|?*`、避开 `CON`/`PRN`/`COM1`…、结尾不留点和空格。

`canonicalize_url()` / `url_hash()` / `extract_urls()` / `title_from_url()`：
去追踪参数、生成稳定 id。抓取失败的文章标题**由 URL 推导**而不是用固定字符串——
否则每篇失败的都同名同目录，而那些恰恰是最需要人工处理的（issue 005-E）。

### `core/doctor.py`（392 行 + 新增 `check_prompts`）· `core/logging.py` · `core/errors.py`

`run_all()` 按显示顺序跑全部检查，每项返回 `CheckResult` 而不打印，
所以 CLI 能渲染成表格、单测能直接断言。

`check_prompts()` 是**花钱之前的免费自检**：必需的提示词文件与分块是否齐全。
清单硬编码在函数里而不是扫目录——扫目录只能说「有几个文件」，说不出「少了哪一个」。

异常层次：`DNAError` → `ConfigError` / `SourceError` / `ExtractionError` /
`ProviderError`（含 `RateLimitError` / `ProviderTimeoutError` /
`ProviderResponseError` / `AuthError`）/ `RenderError` / `StoreError` / `InboxError`。
`ProviderError.retryable` 决定重试还是直接切备用 provider。

---

## `sources/` —— 外部渠道 → `RawItem`

### `sources/base.py` · `registry.py` · `rss.py` · `rsshub.py` · `user_link.py`

| 模块 | 职责 |
|---|---|
| `base.py` | `SourceAdapter` 抽象：`fetch(limit)` → `list[RawItem]` |
| `registry.py` | `build_adapters()` 装配 · `collect()` 跑一轮，**逐源隔离失败** |
| `rss.py` | 标准 RSS/Atom；`parse_feed()` 是纯函数 |
| `rsshub.py` | 自建 RSSHub 实例的路由拼接 |
| `user_link.py` | 用户粘贴的一段文本 → 抽出全部链接 |

**改这里要注意**：
- **绝不用 feedparser 的 `bozo` 判断 feed 好坏。** HTTP 200 的 HTML 错误页
  `bozo=False`，于是这个源被静默记成「成功、0 条」，日报悄悄变短。
  用 `parsed.version`：真 feed 是 `'rss20'`/`'atom10'`，否则是 `''`/`None`（issue 003-A）。
- `collect()` 的**逐源隔离必须保留**——日报每天无人值守跑，feed 天天挂。

### `sources/filters.py`（144 行）

`build_filter()` 合并全局与源级规则，`apply_filters()` 返回 `kept` + `dropped`（带原因）。

**改这里要注意**：过滤跑在**取正文之前**，所以被过滤的条目不花网络、存储、LLM。
全局 `exclude` 到处生效；`include`（必须命中）**只能按源配**——
一个全局的「必须命中」会丢掉大量正常资讯。

### `sources/discover.py`（249 行）· `sources/http.py`（142 行）

`discover_feeds()` 探测网站的 RSS（先读页面声明，再试常见路径），
`suggest_yaml()` 生成可直接粘进 `config/sources.yaml` 的片段（`dna probe` 用）。

`fetch_text()` / `fetch_bytes()` 共用带 UA 与体积上限的 HTTP；
`is_local_url()` 让本机地址绕过公司代理。

---

## `extract/` —— HTML → 正文 + 媒体

### `extract/article.py`（269 行）

`extract_article(html, url)` → `Article`；`fetch_article(url)` 抓取加抽取。
trafilatura 优先，失败退启发式（`_heuristic_body`）。

**改这里要注意**：抽取失败**降级为「标题 + 链接」，从不抛异常**——
只有链接的条目仍然值得发布。`MIN_BODY_CHARS = 80` 以下算降级。

### `extract/media.py`（407 行）

`extract_media(html, url)` → 配图 + 官方视频，每个都带 `source_url` 与 `credit`。

| 常量 | 值 | 说明 |
|---|---|---|
| `DEFAULT_MAX_IMAGES` | 10 | **另有一个同名常量在 `store/article_store.py`** |
| `MIN_IMAGE_WIDTH/HEIGHT` | 200 / 120 | 声明尺寸过小的丢掉 |

**改这里要注意**：
- **两个 `DEFAULT_MAX_IMAGES`。** 只改 store 那个没有用——抽取这边已经截断了。
- 装饰图过滤要同时看 `class` / `alt` / `id`，**不能只看 URL 路径**。
  微信、知乎的图走不透明 CDN 路径（`mmbiz_png/q4wL2ia…`），路径没东西可匹配，
  而标记是诚实的（`class="jump_author_avatar"`）。属性匹配是**子串**匹配，
  路径匹配是**词边界**匹配——两套规则不能混用（issue 005-C）。
- 站点的 `og:image` 常常只是它的 logo，所以社交图**也要过**装饰图过滤器，
  否则每条都拿到同一张封面（issue 003-B）。

---

## `llm/` —— 统一的 LLM 抽象

### `llm/base.py`（381 行）

`LLMProvider` 是唯一接口。子类只实现 `_complete()` 与 `info`。

| 核心 | 说明 |
|---|---|
| `chat()` | 计时与日志统一在这层 |
| `chat_text()` | 只要文本 |
| `chat_json(messages, schema)` | **提取 → 校验 → 带错误重试**（`max_repair=2`） |
| `system()` / `user()` / `assistant()` | 构造消息 |

**改这里要注意**：
- `chat_json()` 会在 messages 末尾**追加一条带完整 JSON Schema 的指令**。
  所以「业务代码构造的 messages」≠「真正发出去的 messages」——
  这是 `llm/capture.py` 拦在 provider 边界而不是 `build_messages()` 那一层的原因。
- 打分类字段**必须写 `ge`/`le` 与 `description`**。裸 `score: float` 时
  qwen3.5:9b 答 95.0、qwen2.5:3b 答 3.0——它们各自发明了量纲（issue 002）。
  约束通过 `model_json_schema()` 免费进入提示词。
- 小模型会把 Schema 原样抄回来（`_SchemaEchoError` 自动纠正），
  但新提示词仍要明确说「给数据，不要照抄 Schema」。

### `llm/cache.py`（239 行）

`CachedProvider` 给任意 provider 套一层磁盘缓存，**套在最外层**——命中就跳过重试层。

`cache_key()` = provider + model + 完整 messages + 调用参数。
所以换模型、改提示词都会自然 miss，旧模型的答案永远不可能冒充新模型的。
**失败从不入缓存；损坏的条目算 miss，不算错误。**
实测：5 条日报第一次 6 次调用 / 10.7 秒，重跑 **0 次调用 / 0.1 秒**。

### `llm/factory.py`（327 行）

`get_llm()` 是**唯一的入口**——业务代码永远不要 import 具体 provider。
`ResilientProvider` 套重试与降级，`RetryPolicy` 指数退避。
`KNOWN_PROVIDERS = ("deepseek", "openrouter", "vllm", "ollama", "openvino")`，
其中 DeepSeek 是默认且是后续全部工作的基准。

### `llm/openai_compat.py`（283 行）· `ollama_provider.py` · `openvino_provider.py` · `parsing.py`

`_translate_error()` 把 SDK / 网络异常翻译成本项目的异常类型；
`_empty_content_error()` 在正文为空时给出**能指到根因**的报错（推理模型把思维链
放独立字段，`max_tokens` 太小会在思维链中间截断成空正文，issue 001）。

`parsing.py` 的 `extract_json_block()` 做配对扫描而不是正则贪婪匹配。

**本地 provider（Ollama / OpenVINO）已冻结**：保留接口与 P1 代码，
不做功能开发也不做功能测试，直到用户说本地方案可以用了。

### `llm/capture.py`（新增）

给提示词调试台用的两个 provider：

| 类 | 用途 |
|---|---|
| `CapturingProvider` | 记下 messages，返回预置假回复让流程走完（**零费用**） |
| `RecordingProvider` | 套在真 provider 外面，记一份 messages 再透传（真机也能看提示词） |

**改这里要注意**：拦截点必须在 `_complete()`。在 `build_messages()` 那一层看
提示词会漏掉 `chat_json()` 追加的 JSON 指令，也漏掉回炉重写那几轮
（它们的 messages 里带着上一稿）——而那恰恰是最需要检查的部分。

---

## `store/` —— 落盘与台账

### `store/db.py`（211 行）

`connect()` / `open_db()`；`SCHEMA_VERSION = 4`。

**改这里要注意**：迁移走 `PRAGMA user_version`，**加新分支，绝不改 `_SCHEMA_V1`**——
否则已有的数据库缺列。schema v4 把「语言」做成产物的一个维度，不是新的 kind。

### `store/ledger.py`（657 行）

`data/dna.db` 是主表：标题、链接、抓取状态、正文长度、图片数、落盘路径。
它是后续每个功能的选择依据，也是跨天去重发生的地方。

| 核心 | 说明 |
|---|---|
| `register()` / `record_fetch()` / `record_failure()` / `set_body()` | 写入 |
| `list()` / `get()` / `find_by_url()` / `count_by_*()` | 查询 |
| `record_production()` / `latest_production()` / `production_matrix()` / `production_history()` | 产物账 |

**改这里要注意**：
- **`feed_title` 存源自己的标题，每次采集都从源刷新。** 绝不让抽取覆盖它——
  SPA 降级抽取拿到的是站点通用名，单列存标题会不可恢复地毁掉真标题（issue 004-B）。
- 产物**插新行并记 `redo_of_id`**，不原地更新：否则一次重做就抹掉了上一版是
  哪个模型、什么时候写的。`latest_production` 按自增 id 排序而不是 `created_at`——
  一秒内两次重做时间戳相同。
- `production_matrix()` 一次查一整页；逐格查会是几百次往返，界面明显卡。

### `store/article_store.py`（499 行）

`save_article()` 把一篇文章落盘：`article.md` + `meta.json` + `references.md` +
`images/` + `videos/`。`read_body()` / `read_title()` 从 `article.md` 读回。

**改这里要注意**：
- **图片下载必须带文章 URL 作 `Referer`**，否则图床防盗链一律 403（issue 004-A）。
- 图片格式看**文件头**，不看 URL 后缀。每张图配一个 `.json` 记出处，
  文件被拷走署名也还在。
- **重抓前清空 `images/` 与 `videos/`。** 重抓写进同一目录，但 `references.md`
  按本次运行重建，于是新过滤器拒掉的文件留在那里、没有记录，看起来还像这篇的图
  （issue 005-D）。`_remove_stale_dirs()` 处理标题改变导致目录改名后的残留。
- 反爬站点（知乎、小红书）**根本抓不到**——httpx、完整浏览器头、无头
  Chromium、带代理且关掉 `navigator.webdriver` 的 Chromium 全部 403「安全验证」。
  用户选择**降级 + 手工粘贴**：`article.md` 带粘贴标记，`dna sync <id>` 把正文
  **和 `# ` 标题**回写进 `meta.json` 与台账，把这行提升为 `ok`。
  **没有这次回写，这行永远是「0 字 / degraded」，后续每个阶段都当它是空的**（issue 005）。

### `store/intake.py`（453 行）· `issue_store.py`（433 行）· `video_store.py`（246 行）· `migrate_layout.py`（290 行）

| 模块 | 职责 |
|---|---|
| `intake.py` | 采集→过滤→抓取→落盘→登记的完整入库流程；`refetch_article()` / `sync_manual_body()` |
| `issue_store.py` | 期次目录：`_digest.json` + `_references.md` + `graphic/` + `podcast/` |
| `video_store.py` | 视频落盘：直链 HTTP，其余走 yt-dlp |
| `migrate_layout.py` | 旧布局迁移：**先搬文件再改库**，中途失败数据库没动，重跑可续 |

**改这里要注意**：
- 媒体上限的优先级：**命令行 > 源级 > profile > 代码默认**。
  arXiv 摘要没有图，长微信文可能有二十张；一个全局数字要么浪费带宽要么漏素材。
- 视频**按文件头确认**，与图片同理——`.mp4` URL 返回 HTML 错误页会在磁盘上
  留下一堆播不了的文件，而台账报告成功。
- yt-dlp 的 format 串**优先单文件 mp4**：合并分离轨道要 ffmpeg，而它不保证装了。
  能播的低分辨率胜过需要外部工具的高分辨率。yt-dlp **懒加载**，失败记原因不抛异常。
- `video_count` 数的是**下载成功的**，不是抽取到的链接数。

---

## `pipeline/` —— 清洗 → 去重 → 打分 → 摘要 → 翻译 → 趋势 → Digest

```
clean → dedup → score  │  summarize → translate → trend → _digest.json
──── 免费，随便跑 ─────│──── 每次运行都计费 ────
```

`run_daily(dry_run=True)` 停在这条线上。

### `pipeline/clean.py`（214 行）

`clean_text()` = 归一化 + 去样板；`is_publishable()` 判断够不够格进流水线。

**改这里要注意**：**不要对中文做 NFKC 归一化。** 它把 `，` 折成 `,` 却不动 `。`，
产出混合宽度的标点，在每种发布形态里都读着像机器翻译。
只显式折全角字母数字（issue 006-B）。

### `pipeline/dedup.py`（333 行）

三级去重：URL → SimHash（Hamming ≤ 3）→ 句向量（可选）。

**改这里要注意**：
- SimHash 用 **blake2b，绝不用内置 `hash`**——后者按进程加盐，重启后跨天去重
  会静默失效且不报错。
- 相似度签名只取正文**前 500 字**：转载会追加不同的尾巴，否则相同文章被推开。
- 阈值保守（≤3）：漏合并读者看得见，错合并看不见。
- 句向量是**可选**第三级，缺了前两级照常работа，只是合得少一些。

### `pipeline/score.py`（290 行）

规则打分，不用 LLM。权重：关键词 .35 / 多源 .25 / 新鲜度 .20 / 篇幅 .12 / 媒体 .08。

**改这里要注意**：
- **打分是规则不是 LLM**：可解释（知道该调哪个旋钮）、稳定（LLM 分数会漂、
  排序跟着抖）、免费。LLM 的钱花在真正需要理解的摘要上。
- 关键词得分**除以 3，不是除以关键词个数**——按总数归一化会让配了二十个
  关键词之后所有分数趋近于零。
- **缺 `published_at` 记 0.5，不是 0**：很多 feed 不给 pubDate，
  把「未知」当「很旧」会把整个源沉到底部，等于误封。
- `needs_video()` 刻意宽松：误标了在界面上取消勾选即可，漏标的根本不会出现在候选里。

### `pipeline/summarize.py` · `translate.py` · `trend.py`

提示词在 `config/prompts/{summarize,translate,trend}.md`；
`system_prompt()` 读它，`build_messages()` 是**纯函数**（测试重点）。

| 模块 | 调用 | 降级 |
|---|---|---|
| `summarize.py` | 每条 1 次，`MAX_BODY_CHARS = 3000` | **失败用标题当摘要**，不抛异常 |
| `translate.py` | 每 `BATCH_SIZE = 10` 条 1 次 | 失败返回空字典，中文版照常 |
| `trend.py` | 每期 1 次，少于 `MIN_ENTRIES_FOR_TREND = 4` 条直接跳过 | 返回 `(None, [])` |

**改这里要注意**：
- **翻译按 id 对齐，绝不按位置。** 漏译一条就会让后面全部错位，
  给每条挂上错误的英文，而且不报错。缺的条目保持 `None`，不是空串。
- `summarize_all` 是**串行不是并发**：DeepSeek 有速率限制，并发换来的是 429 与重试。
- 每个 LLM 节点都**降级不抛异常**——日报无人值守跑。

### `pipeline/source.py`（147 行）· `pipeline/flow.py`（267 行）

`load_candidates()` 从台账取候选；正文**从 `meta.json` 读回，不从台账读**——
台账负责索引与状态，不是内容库。

`run_daily()` 编排全流程，`PipelineReport.explain()` 逐条解释入选理由。

---

## `narration/` —— 三种文案

| kind | 时长窗口 | 字数 | 调用次数 |
|---|---|---|---|
| shortvideo | 25~35 秒 | 110~160 | 1~3（回炉） |
| narration | 1~2 分钟 | 270~540 | 1~3（回炉） |
| longform | 5~15 分钟，专题（1 角色）/ 访谈（2 角色） | 1350~4050 | 5~9 |

### `narration/duration.py`（269 行）

`estimate_seconds()` 量时长，`prompt_char_budget()` 给提示词的字数预算，
`length_feedback()` 生成回炉反馈。`CHARS_PER_SECOND_ZH = 4.5`、
`WORDS_PER_SECOND_EN = 2.6`、`MIXED_COPY_CHAR_FACTOR = 1.5`。

**改这里要注意**：**长度只用一个单位定义。** 长文案的目标从**时长**推导，
字数上下限由它换算。按字数推导在中英混排的技术稿上失效——
`UD-Q8_K_XL` 这类标识符占字符多、占时长少，「4000 字」的稿子实测 7.4 分钟
而不是 15 分钟（issue 007-C）。

### `narration/script_builder.py`（584 行）

`build_short_video()` / `build_narration()`，中英各一条路径；共用骨架 `_generate()`：
生成 → 量时长 → 超区间**带着具体差值和上一稿**回炉 → 最多 `MAX_REWRITES = 2` 次。

`rules_for(lang)` 读共用写作要求，`instruction_block()` 把人写的额外要求接在**末尾**，
`article_block()` 组织输入块（`MAX_BODY_CHARS = 6000`，`longform.py` 也用这一份）。

**改这里要注意**：
- **文案从正文写，不从摘要写。** 摘要已经把技术细节压掉了（它的任务是 60~120 字
  的快速浏览），拿它写口播，模型手里只剩几句结论可以注水——白话正是这么来的。
- 回炉时**必须把上一稿作为 assistant 消息带回去**。只说「太长了」会让模型从头
  重写一篇完全不同的，上一稿写对的部分一起丢了。
- 差值按**这一稿实测的字符密度**换算，不按预设 4.5 字/秒：技术稿实测能到 8，
  按预设算会少要求删一半，第二稿仍然超时。
- 试到上限仍不达标就**按现状返回并标记**，不失败：38 秒的稿子人手动删两句就能用。
- **英文版原生写，不翻译中文稿。** 中文 30 秒的稿子翻成英文不是 30 秒的稿子，
  而时长正是这三种文案的验收标准。
- `instruction_block("")` 返回**空串**，让提示词与不带这个功能时逐字节相同——
  否则每篇都因为多一个空标题错开 LLM 缓存。

### `narration/longform.py`（519 行）

三步：出提纲（1 次）→ 逐节展开（每节 1 次，带上一节结尾）→ 拼装（不做全文重写）。
`MIN_BODY_FOR_LONGFORM = 800`、`EXPANSION_RATIO = 1.2`、
`MIN/MAX_TARGET_SECONDS = 300/900`、`MIN/MAX_SECTIONS = 4/8`。

两种模式**产出同一种 speaker-turn JSON**（`to_json_dict()`），所以 TTS 侧只有一条路径。

**改这里要注意**：
- `can_build_longform()` **在花钱之前判断**——这是最贵的产物（提纲 1 次 + 每节 1 次）。
- 展开一节要给模型**两样上下文**：上一节结尾（120 字，保接缝）**和完整提纲**
  （标出哪几节已讲、哪几节留给后面）。只给结尾不够——实测 7 节的那篇，
  第 7 节把第 6 节的 9 个实体全部重讲了一遍，因为它只看见前 120 字。
- `language_directive()` **每节都重复一遍**。长文案是十几次独立调用，
  只在第一节说「用英文写」，后面几节会跟着中文原文滑回中文。
- 模型偶尔自创角色名（「专家」「记者」）→ **归到默认角色而不是丢掉**：
  内容是好的，只是标签错了。

---

## `produce/` —— 单篇产物

### `produce/tasks.py`（350 行）

产物注册表。`ProductionKind` 枚举 + `TaskSpec`（文件名、依赖、是否进 `--all`、
预估调用次数、最小正文长度、是否要念）。**不含生成函数**——那在 `service.py`。

**改这里要注意**：`longform` **绝不进 `--all`**：一篇 5~9 次调用，
是其余三项加起来的两倍多，一次误触就是一个数量级的账单。

### `produce/service.py`（922 行）

`produce()` 是唯一入口，CLI 与 GUI 都调它，所以两个前端不会各坏一套。

| 核心 | 说明 |
|---|---|
| `produce()` / `produce_all()` | 生成一种 / 批量 |
| `generate_text()` | **分派到具体生成器**（提示词调试台也调这一个） |
| `load_article()` / `as_cluster()` | 从落盘目录读回 / 包成单成员 Cluster |
| `read_production()` / `speech_segments_for()` / `import_audio()` | 读回 / 分段 / 收外部音频 |

**改这里要注意**：
- **已有产物不加 `force` 就复用，零 LLM 调用。** 按钮就在那里，误触不能花钱——
  这是产物层最重要的护栏。
- **失败也记一行。** 不记的话表格显示「未生成」，人以为没跑过，再点一次再失败一次，
  每次都付钱。
- **前置一律不 force**：重做音频不该顺手把稿子重新调一遍 LLM。
  下游重做免费，上游重做要花钱，让一个动作同时触发两者是危险的默认值。
- `generate_text()` / `load_article()` / `as_cluster()` 是**刻意公开**的，
  为的是让 `prompt_lab.py` 调同一个分派函数。另写一份 if/elif 两边迟早漂移。

### `produce/prompt_lab.py`（新增）

`dna prompt` 的实现。`render()` 零费用看提示词，`run()` 真机跑一次看产物，
两者都走 `service.generate_text()`，只有注入的 provider 不同。

**改这里要注意**：`run()` **不写文件也不记台账**——调试跑十次不该留十份垃圾，
也不该搅乱台账的产物历史（那本账记的是「发布用的那一版是谁写的」）。

### `produce/documents.py`（127 行）

`front_matter()` 抬头、`script_block()` 正文排版、`spoken_text()` 从产物文件里
**只取要念的那部分**（靠 `SPOKEN_MARKER`）、`longform_turns()` 读回 JSON 附件。

---

## `tts/` —— 语音合成（很薄的服务客户端）

**本项目不装任何模型依赖**：没有 torch、没有 openvino、没有 transformers。
波形拼接用标准库 `wave`，所以连 numpy 都不需要——只剩一个 HTTP 客户端。

| 模块 | 职责 |
|---|---|
| `base.py` | `TTSProvider` 协议、`VoiceSpec`、`SpeechSegment`、`AudioClip`、`encode_wav()` |
| `client.py` | HTTP 客户端：`health` / `info` / `speakers` / `synthesize` / `handoff` / `download` |
| `service.py` | 唯一后端：`TTSServiceProvider`，逐段合成并拼接 |
| `factory.py` | `get_tts()` · `voice_for_role()` 按角色取音色 |
| `supervisor.py` | 看门人：服务不在线时自动拉起（`ensure_service` / `ensure_gui`） |
| `preprocess.py` | **朗读友好化**：一次 LLM 调用（型号、公式、多音字、断句） |
| `segment.py` | `clean_for_speech()` 去 Markdown 与网址 · `split_for_speech()` 分段 |
| `subtitle.py` | SRT 导出，与音频同名同目录 |

**改这里要注意**：
- **顺序不能反**：先朗读友好化，再分段。预处理会调整断句与停顿标记，
  先切好再改写，切点就落在改写前的位置上了。
- **TTS service 是纯 TTS，它自己从不调 LLM。** 「什么写法念得顺」是文案层的判断，
  所以那一次调用属于本项目，在交出去之前做完。合成本身仍记 `calls=0`。
- 预处理**失败、超时、返回明显不对时一律退回原文**（长度偏离 0.6~1.8 倍即拒）。
  音频能不能出来，不该取决于一次可选的润色。`TTS_PREPROCESS=false` 可彻底关掉。
- 时长**从波形实测**，不按估算累加——字幕漂移会累积。
- 全程本地、零费用，但很慢（RTF ≈ 2.5：口播约 4 分钟，长文案约 37 分钟）。

---

## 症状 → 文件

| 症状 | 看这里 |
|---|---|
| 某个源「成功、0 条」 | `sources/rss.py` —— 用 `parsed.version` 而不是 `bozo` |
| 每条资讯配图都一样 | `extract/media.py::_social_images` —— `og:image` 是站点 logo |
| 配图抓成了头像 / 二维码 | `extract/media.py::_looks_like_chrome` / `_chrome_by_attributes` |
| 图片全部 403 | `store/article_store.py::_download_images` —— 缺 `Referer` |
| 提高了图片上限却没变多 | 两个 `DEFAULT_MAX_IMAGES`，抽取那个也要改 |
| 手工粘贴的正文没生效 | `dna sync <id>` → `store/intake.py::sync_manual_body` |
| 文章标题变成了站点通用名 | `store/ledger.py` —— `feed_title` 被抽取覆盖了（issue 004-B） |
| 改了 `DATA_DIR` 之后全空 | `core/config.py` —— 路径必须从 `Settings` 统一派生（issue 006-A） |
| 中文标点变成半角 | `pipeline/clean.py` —— 不要 NFKC（issue 006-B） |
| 重启后跨天去重失效 | `pipeline/dedup.py::simhash` —— 必须 blake2b |
| 整个源沉到最底 | `pipeline/score.py::_freshness_signal` —— 缺 pubDate 记 0.5 |
| 英文摘要挂错了条目 | `pipeline/translate.py::build_messages` —— 按 id 对齐 |
| 稿子全是形容词、没有数字 | 提示词：`config/prompts/_shared/professionalism.zh.md` |
| 稿子时长总是不达标 | `narration/duration.py::length_feedback` + `prompt_char_budget` |
| 长文案后面几节重复前面 | `narration/longform.py::_outline_map` —— 提纲要全量可见 |
| 英文长文案中英夹杂 | `narration/longform.py::language_directive` —— 每节都要重复 |
| 点重做花了钱 / 没花钱 | `produce/service.py::produce` 的 `force` 分支 |
| 表格显示「未生成」但确实跑过 | `produce/service.py` —— 失败要记一行 |
| 改了提示词没生效 | `dna prompt <id> -t <task>` 看真正发出去的那一份 |
| 音频念出了网址 | `produce/documents.py::spoken_text` + `tts/segment.py::clean_for_speech` |
| 字幕越往后越偏 | `tts/service.py` —— 时长要从波形实测 |
| 界面点一下卡十秒 | 前端漏了 `nicegui.run.io_bound`（见前端文档） |

---

## 相关文档

- [`04_architecture_frontends.md`](04_architecture_frontends.md) —— CLI 与工作台
- [`06_prompt_spec.md`](06_prompt_spec.md) —— 提示词为什么这么写（正文在 `config/prompts/`）
- [`07_db_schema.md`](07_db_schema.md) —— 台账表结构
- [`05_output_spec.md`](05_output_spec.md) —— 产物目录布局
- `issues/NNN-*` —— 上面每个「注意」背后的根因分析
