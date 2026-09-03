# 11 文章总表与落盘 / Article Ledger and Storage

> 面向使用者的操作手册。**全程不调用 LLM，不产生费用。**

---

## 一、总表：`dna list`

台账记录每一篇采集到的文章，是后续所有功能的选取依据。

```bash
dna list                        # 最近 30 条
dna list --status degraded      # 只看抽取降级的
dna list --status failed        # 只看失败的
dna list --source qbitai        # 只看某个来源
dna list -q 多模态              # 按标题或链接搜索
dna list -n 100                 # 显示更多
```

输出示例：

```
┌──────────┬──────────┬─────────┬──────────────────────────────┬──────┬────┐
│ id       │   状态   │ 来源    │ 标题                         │ 正文 │ 图 │
├──────────┼──────────┼─────────┼──────────────────────────────┼──────┼────┤
│ 5c4430ab │    ok    │ qbitai  │ Claude最强Fable 5.1发布！…   │ 3558 │  5 │
│ 42fdf034 │ degraded │ oschina │ 从 SIP 到大模型：一天构建…   │   27 │  - │
└──────────┴──────────┴─────────┴──────────────────────────────┴──────┴────┘
```

### 状态含义

| 状态 | 含义 | 还能用吗 |
|---|---|---|
| `ok` | 正文抓取成功 | ✅ |
| `degraded` | **只拿到标题 + 链接**，正文抽不出来（站点是 SPA、需登录、或有反爬） | ✅ 仍可进日报；也可手工补正文后 `dna sync` 升为 ok |
| `failed` | 抓取失败（网络错误、403 等），`dna show` 里能看到具体原因 | ❌ 可 `dna refetch` 重试 |
| `pending` | 已登记但还没抓正文 | — |

`degraded` **不是错误**，是刻意的降级：为一个技术问题丢掉一条真实资讯不划算。

---

## 二、核对抓取质量：`dna show`

```bash
dna show 5c4430ab       # id 前 8 位即可，不用敲全
```

显示落盘位置、正文预览、配图清单。**落盘目录可以直接打开逐一核对**：

```
data/articles/20260902/Claude最强Fable-5-1发布__5c4430ab/
├── article.md      正文（带 YAML frontmatter，可直接阅读）
├── meta.json       完整结构化数据，供程序复用
├── references.md   原文链接 + 每张图/视频的出处 + 未下载的原因
├── images/
│   ├── 01_i-qbitai-com.webp
│   └── 01_i-qbitai-com.webp.json   ← 该图的来源、署名、说明
└── videos/                          ← 有官方视频时才出现
    ├── 01_v-qq-com.mp4
    └── 01_v-qq-com.mp4.json
```

**每张图旁边都有一个同名 `.json` 记录出处。** 写在图片旁边而不是只写在数据库里，
是因为图片一定会被单独拷来拷去，信息不能跟着丢——发到公众号、小红书时每张图
都要能追溯来源。

如果某张图没下下来，`references.md` 里会写明原因（403 防盗链、文件过小、格式无法识别等）。

**每篇默认存 10 张图**，比日报实际要用的 1~3 张多得多——多存的是素材库：
做长图、口播配图、视频封面时都要挑图，而**重抓拿不回当初那些图**（站点会换图删图）。
想改数量：`dna add <链接> --max-images 20`。

---

## 二 bis、视频

有官方视频的文章会把视频也下下来，存进 `videos/`：

| 情况 | 怎么处理 |
|---|---|
| 直链视频文件（`<video src="….mp4">`） | 直接下载，带 Referer 过防盗链 |
| 播放器 / iframe（腾讯视频、B站、YouTube、抖音） | 交给 yt-dlp 解析后下载 |
| 平台封禁下载 / 地域限制 / 会员墙 | 下不了，但**地址仍会写进 references.md**，你能点开看 |

视频比图片慢一个数量级。只想快速验证正文时：

```bash
dna add <链接> --no-videos      # 只关视频，图片照下
dna add <链接> --no-images      # 只关图片
```

yt-dlp 跟着平台改版走，某个平台突然下不了时先升级它：

```bash
pip install -U yt-dlp
```

台账里的「视频数」是**实际下载成功**的数量，不是页面上有几个视频链接——
显示 0 但 references.md 里有地址，就说明抽到了但没下下来。

---

## 三、抓取指定链接：`dna add`

```bash
dna add https://example.com/article
dna add https://a.com/1 https://b.com/2                 # 一次多个
dna add "这篇不错 https://example.com/article 值得一看"   # 直接粘一段话
```

会自动提取文字里的所有链接。**手动指定的链接不经过订阅源的过滤规则**——
你点名要的就是想要的。

---

## 三 bis、抓不到正文时：手动补 + `dna sync`

知乎、小红书这类站点有强反爬，**未登录一律 403**，换 UA、换请求头、用无头浏览器
都过不去（实测记录见 [issues/005](issues/005-wechat-zhihu-live-verification.md)）。
这类文章会降级保存，正文由你手工补。

三步：

```bash
dna show <id>                  # 显示落盘位置
```

打开该目录的 `article.md`，会看到：

```markdown
# zhuanlan.zhihu.com p 2067333343717355528

> ⚠️ **正文抽取降级**：只拿到标题与链接。

<!-- 正文粘贴区 / paste the article body below this line -->

_把原文正文粘贴到这一行下面，保存后执行 `dna sync cf06c31a` 回写台账。_
```

把原文正文粘到那行下面，**顺手把第一行的 `# 标题` 也改对**（抓不到正文时标题是从
URL 推的，不能直接当日报标题用），保存，然后：

```bash
dna sync cf06c31a
```

```
ok　已回写正文 3120 字：知乎那篇文章的真实标题
```

**这一步不能省。** 不回写的话台账里这篇永远是「0 字 / degraded」，后续挑文章做日报时
会被当成空文章跳过——你辛苦补的正文等于白补。回写后状态直接升到 `ok`：正文来自人而
不是抽取器，比任何自动抽取都可靠。

---

## 四、重新抓取：`dna refetch`

```bash
dna refetch 42fdf034              # 重抓一篇
dna refetch 42fdf034 --no-images  # 只重抓正文，不下图
```

适用场景：抓取失败后重试、站点内容更新后刷新、修复了抽取逻辑后复验。

批量重抓某个源：

```bash
dna fetch --source oschina --limit 10 --refetch
```

---

## 五、统计：`dna stats`

```bash
dna stats
```

按状态和来源两个维度统计。用来快速判断「哪个源的抓取质量差」——
某个源如果 degraded 占比很高，说明它的文章页需要特殊处理或干脆关掉。

---

## 六、采集入库：`dna fetch`

```bash
dna fetch                        # 全部启用的源，每源 5 条
dna fetch --limit 10             # 每源 10 条
dna fetch --source qbitai        # 只跑一个源
dna fetch --dry-run              # 只看采集到什么，不写任何文件
dna fetch --no-images            # 不下图，快速验证正文抽取
dna fetch --no-videos            # 不下视频（视频慢一个数量级）
dna fetch --max-images 20        # 每篇多存点素材（默认 10）
dna fetch --refetch              # 已抓过的也重抓
```

完整流程：**采集 → 过滤 → 抓正文 → 落盘 → 记台账**。

输出会告诉你条目去向，能对上账：

```
采集 14，过滤 2，已存在跳过 3，新入库 9，（其中降级 4）

过滤明细：
  命中排除词 × 2
```

「已存在跳过」是**跨日去重**：同一篇文章会连续几天出现在 feed 里，
台账记得它，所以不会天天重抓、天天进日报。判重按规范化 URL，
因此带不同追踪参数的分享链接算同一篇。

---

## 七、后续功能会怎么用这张表

台账是 P3 之后所有功能的选取依据：

| 后续功能 | 怎么用 |
|---|---|
| AI 日报（P5） | 从表里选若干篇 → 去重聚类 → 摘要 → 图文稿 + 长图 |
| 短视频口播（P6） | 选一篇 → 主副标题 + 20~25s 口播稿 + 配图/官方视频 |
| 播客（P7） | 选若干篇 → 单人播报或双人访谈脚本 + 音频 |
| 后台控制台（P8） | 表格化展示产出矩阵，勾选重做 |

素材（图片、视频）已经落盘，做长图和视频时直接从 `images/` `videos/` 里挑，
不用回头再抓一次——那时候站点很可能已经换图了。

因为正文与配图都已落盘、`meta.json` 可反序列化回结构化对象，
**重做某一种输出时不需要重新抓取**，直接复用已有的抽取结果。

---

## 八、数据位置

| 路径 | 内容 | 是否入库 |
|---|---|---|
| `data/dna.db` | SQLite 台账 | ❌ gitignore |
| `data/articles/<日期>/<标题>__<id>/` | 正文、图片、视频、来源清单 | ❌ gitignore |

数据库有版本管理（`PRAGMA user_version`），升级时自动迁移，**不需要删库重来**——
台账里积累的抓取历史是有价值的。

---

## 九、相关

- 添加与过滤信息源：[10_sources_guide.md](10_sources_guide.md)
- 真机验证发现的问题：[issues/003](issues/003-p2-live-verification-findings.md)、
  [issues/004](issues/004-image-hotlink-and-title-overwrite.md)、
  [issues/005](issues/005-wechat-zhihu-live-verification.md)
- 实现：`src/dna/store/`（`ledger.py` 台账 · `article_store.py` 落盘 ·
  `video_store.py` 视频下载 · `intake.py` 入库流程）
