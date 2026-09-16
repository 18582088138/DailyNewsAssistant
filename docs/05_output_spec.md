# 05 产物落盘规范 / Output Layout Specification

> **本文件是落盘路径的唯一权威。**代码、文档、脚本如与本文冲突，以本文为准。
> 依据用户 2026-09-03 的决定：「落盘的路径以 P3.5 中的实现为准，所有文件的落盘
> 以及中间产物，要确保一致，不要出现散落在各处的问题」。

---

## 一、两棵树，一句话

```
data/       程序自己用的      —— 台账数据库、LLM 响应缓存。不放产物。
outputs/    人要打开、拷走、发布的 —— 全部产物。
```

判断一个文件该放哪：**你会不会把它拷给别人？** 会 → `outputs/`，不会 → `data/`。

---

## 二、完整目录

```
DailyNewsAssistant/
├── data/
│   ├── dna.db                                    文章台账 + productions 产出矩阵
│   └── llm_cache/                                LLM 响应缓存（命中不计费）
│
└── outputs/
    ├── articles/                                 ← 条目级：单篇文章的全部资产
    │   └── 20260902/
    │       └── DeepSeek-V4-Flash-正式版开源__decef4a4/
    │           ├── article.md                    正文（YAML frontmatter，可直接读）
    │           ├── meta.json                     完整 Article 结构，供程序复用
    │           ├── references.md                 本篇来源 + 下载下来的文件名
    │           ├── images/
    │           │   ├── 01_qbitai.jpg
    │           │   └── 01_qbitai.jpg.json        每张图的来源与署名
    │           ├── videos/
    │           ├── summary.zh.md                 中文总结      ┐
    │           ├── summary.en.md                 英文总结      │ dna.produce 生成，
    │           ├── shortvideo.zh.md              短视频 25~35s │ 每项可单独重做
    │           ├── narration.zh.md               口播 1~2min   │
    │           ├── longform.zh.md                长文案 5~15min│
    │           ├── longform.zh.json              长文案角色轮次（TTS 消费）┘
    │           ├── shortvideo.zh.wav             音频：与文稿同名，换扩展名 ┐
    │           ├── shortvideo.zh.srt             字幕：与音频逐段对齐       │ dna.tts
    │           └── tts/                          逐段中间产物              │ 生成
    │               ├── seg_001_anchor_f.wav …    分段音频（`seg_<序号>_<角色>`）│
    │               ├── merged.wav                                          │
    │               └── merged.srt                                          ┘
    │
    └── 20260902-DailyNews/                       ← 期次级：只放整期产物
        ├── _digest.json                          结构化事实源，可重放
        ├── _references.md                        全期来源汇总
        ├── graphic/                              场景1 整期图文        ← P5
        └── podcast/                              场景3 整期播客        ← P7
```

### 命名规则

| 对象 | 规则 | 例 |
|---|---|---|
| 期次目录 | `YYYYMMDD-DailyNews` | `20260902-DailyNews` |
| 条目日期层 | `YYYYMMDD`（首次落盘那天） | `20260902` |
| 条目目录 | `<标题slug>__<id前8位>` | `DeepSeek-V4-Flash-正式版开源__decef4a4` |
| slug | `core/naming.slugify()`：剔 Windows 非法字符、折叠分隔符、截 40 字、保留中文 | |
| 语言后缀 | 统一 `.zh` / `.en`，放在扩展名之前 | `summary.zh.md` |
| 音频 / 字幕 | **与文稿同名，只换扩展名** | `narration.zh.wav` · `shortvideo.zh.srt` |
| 分段中间产物 | `tts/seg_<序号>_<角色>.wav`，可安全删除（只影响断点续合成） | `tts/seg_001_anchor_f.wav` |

id 是 `url_hash(url)`（规范化 URL 的 sha256 前 16 位，见 `core/urls.py`）。
**台账主键、`NewsItem.id`、`DigestEntry.id` 是同一个值**，所以三者互相查得到。

---

## 三、两级之间只有引用，没有副本

期次目录里**不出现任何条目资产**。`_references.md` 用相对路径指过去：

```markdown
- 条目目录：[`../articles/20260902/DeepSeek-V4-Flash-正式版开源__decef4a4/`](...)
```

**为什么不复制。**一篇文章可以进多期（跨日的后续报道）。复制会产生两份各自漂移的
副本：重做了条目的口播稿，期次里那份还是旧的，而且没有任何提示。按 id 引用只有
一处真相，重做之后期次这边**自动就是新的，不需要重新装配**。

代价是：条目目录被手工删掉时，期次里的链接会指空。这一点**不静默处理**——
`_references.md` 末尾会列出所有未能定位的条目，`dna issue` 里那一列显示「未定位」。

---

## 四、两个 references 文件，各管一件事

| 文件 | 位置 | 记什么 | 谁写 |
|---|---|---|---|
| `references.md` | 条目目录 | **下载下来的文件名** ← 原始地址、署名、跳过的原因 | `store/article_store.py` |
| `_references.md` | 期次目录 | **本期用到了谁**：每条的原文、多源出处、图片与视频原始地址、条目目录在哪 | `store/issue_store.py` |

不互相抄：本地文件名会随重抓变化，原始地址才是署名要引用的那一个。

`_references.md` 是**发布时的合规依据**——图文/视频/播客发出去之前，
每一张图、每一段视频的出处都要在这里查得到。

---

## 五、命令

```bash
dna digest                   # 生成一期：写 _digest.json + _references.md
dna issue --list             # 列出已生成的期次
dna issue                    # 看最新一期：条目、评分、图视数、条目目录是否定位到
dna issue --date 20260902    # 看指定一期
dna issue --refresh          # 只重建 _references.md（零费用，见下）
dna migrate-layout           # 把 data/articles 旧布局搬到 outputs/articles
```

**`--refresh` 什么时候用。**重抓会改标题 → 改 slug → 改目录名，期次里的链接因此
指错。`--refresh` 只重算引用，**不碰 `_digest.json`、不调用 LLM、不花钱**。

---

## 六、已作废的布局（不要再引用）

| 布局 | 状态 | 原因 |
|---|---|---|
| `<期次>/topics/01_<slug>/` | ❌ 作废 | 与 `outputs/articles/` 重复。条目资产只有一处，见 `02_development_plan.md` §9.2 |
| `_history/<时间戳>/` | ❌ 取消 | 旧产物的价值不足以抵消目录复杂度；重做历史记在 `productions` 表的 `redo_of_id` 上，见 §9.3 |
| `data/articles/` | ❌ 已迁移 | 产物不该放在 `data/`。迁移工具 `dna migrate-layout` 保留 |

对应地，`core/naming.py` 里的 `topic_dir_name()` 与 `history_dir_name()` 已随之删除。

---

## 七、相关代码与测试

| 层 | 文件 |
|---|---|
| 命名（纯函数，不做 I/O） | `src/dna/core/naming.py` |
| 条目落盘 | `src/dna/store/article_store.py` |
| 期次落盘与装配 | `src/dna/store/issue_store.py` |
| 旧布局迁移 | `src/dna/store/migrate_layout.py` |
| 测试 | `tests/core/test_naming.py` · `tests/store/test_article_store.py` · `tests/store/test_issue_store.py` · `tests/store/test_migrate_layout.py` |
