# 07 台账数据库结构 / Ledger Database Schema

> `data/dna.db`（SQLite，无 ORM）。当前 `user_version = 3`。

---

## 一、两张表

```
articles ──1:N──> productions
  抓到了什么          为它生成了什么
```

`articles` 是「总表」：抓了哪些文章、状态如何、落在哪。
`productions` 是「产出矩阵」：每篇做过哪些形态的内容、什么时候、用哪个模型。

工作台（`dna gui`）的那张大表就是这两张表 join 出来的。

---

## 二、`articles`

| 列 | 说明 |
|---|---|
| `id` | 主键，`url_hash`，**跨次运行稳定**（用 sha256 不用内置 hash） |
| `url` / `canonical_url` | 原始链接 / 去掉追踪参数后的规范链接，判重按后者 |
| `title` | 显示标题，可能被抽取覆盖，也可能被人工改过 |
| `feed_title` | **源发布时的原始标题，每次采集都从源刷新** |
| `source_id` / `via` | 来源 id / 投递方式（rss·rsshub·inbox·gui） |
| `author` / `published_at` | 作者 / 发布时间（源未提供则空） |
| `first_seen_at` / `fetched_at` | 首次出现 / 最近一次成功抓正文 |
| `status` | `pending` \| `ok` \| `degraded` \| `failed` |
| `text_len` / `image_count` / `video_count` | 正文字数 / 实际下载成功的图与视频数 |
| `store_dir` | **相对 `outputs/` 的落盘目录** |
| `error` / `tags` / `fetch_count` | 最近错误 / 标签 / 抓取次数（含重抓） |

### 为什么 `feed_title` 单独一列

抽取降级时（SPA 页面、需登录）拿到的往往是站点通用名，会覆盖掉 RSS 里正确的标题，
**且覆盖后不可恢复**——重抓的兜底标题正是那个已被污染的值。
单独存一列源标题，并在每次采集时刷新，被污染的历史行会自愈。（[issue 004](issues/004-image-hotlink-and-title-overwrite.md)）

### 为什么正文不进数据库

`text_len` 存长度，正文本身在 `outputs/articles/<日期>/<slug>__<id8>/meta.json`。
几百篇全文塞进 SQLite 会让这张每天都要查询和统计的总表又大又慢，
而它的职责是**索引与状态**，不是内容仓库。

---

## 三、`productions`

| 列 | 说明 |
|---|---|
| `id` | 自增主键 |
| `article_id` | 指向 `articles.id` |
| `kind` | `summary_zh` \| `summary_en` \| `shortvideo` \| `narration` \| `longform` |
| `variant` | 长文案专用：`feature`（专题）\| `interview`（访谈） |
| `status` | `ok` \| `failed` |
| `output_path` | 相对 `outputs/` 的产物文件 |
| `chars` / `est_seconds` | 字数 / 估算口播秒数 |
| `llm_provider` / `llm_model` | 哪个 provider、哪个模型写的 |
| `tokens` / `calls` / `duration_ms` | 用量 / 调用次数（长文案是分段的，会有多次） / 耗时 |
| `error` | 失败原因 |
| `created_at` | 生成时间 |
| `redo_of_id` | **指向被替换的上一版** |

### 为什么每次插新行而不是原地更新

原地更新会把上一版连同它的模型与时间一起抹掉。而内容出问题时，
「这段稿子是哪天用哪个模型写的」是必须能回答的问题——换了模型之后旧产物质量
参差不齐，没有这两列就只能全部重做。

`redo_of_id` 串起版本链，`latest_production()` 取最新一版。
**按自增 id 倒序取而不是按 `created_at`**：同一秒内重做两次时时间戳相同，
按时间排序会拿到不确定的那一版。

### 为什么失败也记一行

不记的话工作台里显示「未生成」，人会以为没跑过，于是再点一次、再失败一次——
**每次都在花钱**。记下来才能看见「这篇试过了，失败原因是 X」。

### 一次查完整页

```python
ledger.production_matrix([r.id for r in records])   # {article_id: {kind: record}}
```

工作台一页几十条 × 5 种产物，逐格查库是几百次往返，界面会肉眼可见地卡。
子查询用 `MAX(id) GROUP BY article_id, kind` 只取每种产物的最新版。

---

## 四、版本迁移

`PRAGMA user_version` 逐级迁移，在 `src/dna/store/db.py::_migrate()` 里追加分支。

| 版本 | 内容 |
|---|---|
| v1 | `articles` 建表 |
| v2 | 加 `feed_title` 并用现有标题回填（[issue 004](issues/004-image-hotlink-and-title-overwrite.md)） |
| v3 | 建 `productions` 表与索引（P3.5） |

**不要直接改 `_SCHEMA_V1`**，否则已存在的库不会得到新列——只有全新建库才对，
而老库会在运行时报 `no such column`。

---

## 五、路径约定

| 内容 | 位置 |
|---|---|
| 台账数据库 | `data/dna.db` |
| LLM 响应缓存 | `data/llm_cache/` |
| **文章与全部产物** | `outputs/articles/<YYYYMMDD>/<slug>__<id8>/` |
| 期次日报 | `outputs/<YYYYMMDD>-DailyNews/` |

`store_dir` 与 `output_path` 相对。台账的相对路径按**数据目录**解析而不是仓库根——
两者必须待在一起，否则改了 `DATA_DIR` 会让台账与它索引的内容静默失联
（[issue 006](issues/006-p3-config-and-normalisation.md)）。

历史上文章存在 `data/articles/`，用 `dna migrate-layout` 迁移。

---

## 六、相关

- 实现：`src/dna/store/`（`db.py` 建表迁移 · `ledger.py` 读写 · `migrate_layout.py` 迁移）
- 操作手册：[11_article_store_guide.md](11_article_store_guide.md) · [13_workbench_guide.md](13_workbench_guide.md)

---

## productions v4：语言与指令

台账 schema 升到 **v4**，`productions` 加两列：

| 列 | 类型 | 说明 |
|---|---|---|
| `lang` | TEXT NOT NULL DEFAULT 'zh' | 输出语言，`zh` / `en` |
| `instructions` | TEXT | 生成这一版时附带的额外要求 |

### 为什么语言是列，不是新的 kind

`summary_zh` 与 `summary_en` 原本是两种产物类型，于是工作台里占两列——
可它们是**同一份东西的两个语言版本**。要给短视频、口播、长文案都加英文版时，
照原样得再造三个 kind 加它们各自的音频，枚举翻倍而语义没变清楚一点。

v4 之后 **`(article_id, kind, lang)` 才是一份产物的完整标识**：

```sql
UPDATE productions SET kind='summary', lang='zh' WHERE kind='summary_zh';
UPDATE productions SET kind='summary', lang='en' WHERE kind='summary_en';
CREATE INDEX idx_productions_kind ON productions(article_id, kind, lang);
```

**查询必须带上语言。**漏掉的话，生成过英文版之后再查中文版会拿到英文那一行——
「已存在就跳过」于是拿英文版冒充中文版，一次都不会报错。
`latest_production(article_id, kind, lang)` 与 `production_matrix()`
（返回 `{article_id: {(kind, lang): record}}`）都按三元组走。

### `instructions` 记什么

人在界面上写的「加长到 40 秒」「用词再专业一点」。记下来有两个用处：

1. **界面预填**——调稿子是渐进的，下次重做在上次基础上再加一条，
   而不是从零回忆上次改了什么
2. **内容出问题时第一个要看的**——这一版是带着什么要求做出来的
