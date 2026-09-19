# 004 · 落盘验证暴露的两个问题：图片防盗链 与 标题被覆盖

| | |
|---|---|
| 发现阶段 | P2+ 落盘与台账（应用户要求提前的 P4 内容），真机验证时 |
| 日期 | 2026-09-02 |
| 状态 | ✅ 全部修复并真机复验 |
| 影响面 | 日报配图（A）、日报标题与落盘目录名（B） |

> 这两个问题**只有把抓取结果落盘之后才可能被发现**——在此之前抽取阶段
> 「拿到 5 张图 URL」看起来是成功的，标题也看起来正常。
> Both problems only became visible once results were persisted: before that, the
> extraction stage reported five image URLs and a plausible title, and looked fine.

---

## A · 图片全部下载失败（HTTP 403 防盗链）

### 现象

`dna add https://www.qbitai.com/2026/09/482337.html` 后，正文 4137 字抓取成功，
但 **配图 0 张**。而抽取阶段明明识别出了 5 张图。

`references.md` 里记着原因：

```
## 未下载的图片 / Skipped images
- https://i.qbitai.com/wp-content/uploads/2026/09/9df0789361daaf07f0ec65feafd8073b.webp
  —— 下载失败：HTTP 403
- （其余 4 张同样 403）
```

### 根因

图床做了**防盗链**（hotlink protection）：检查请求的 `Referer` 头是否来自本站，
不带就返回 403。这是中文站点图床的普遍做法。

### 复现与验证

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe - <<'EOF'
import httpx
img  = "https://i.qbitai.com/wp-content/uploads/2026/09/1486b7a4d8257e25bf15f1fbf3c98b83.jpeg"
page = "https://www.qbitai.com/2026/09/482337.html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
for label, h in [("无 Referer", {"User-Agent": UA}),
                 ("带 Referer", {"User-Agent": UA, "Referer": page})]:
    r = httpx.get(img, headers=h, timeout=25, follow_redirects=True)
    print(f"{label}: HTTP {r.status_code}  {len(r.content)} bytes")
EOF
```

实测结果：

| 请求 | 结果 |
|---|---|
| 无 Referer | **HTTP 403**，489 字节的 XML 错误页 |
| 带 Referer（原文页） | **HTTP 200**，55068 字节 image/jpeg |
| 带 Referer（站点根） | HTTP 200，同上 |

### 修复

1. `sources/http.py::fetch_bytes` 增加 `referer` 参数
2. `store/article_store.py` 下载配图时传入 `asset.source_url` 作为 Referer

这也是 **`MediaAsset.source_url` 必须在抽取时就记下来** 的又一个理由：
它不只是给发布时标注出处用的，下载图片本身就需要它。

### 复验

```bash
dna refetch 222476b2
# ok　正文 4137 字　配图 5 张　（第 2 次抓取）
```

5 张图全部下载成功，且格式按文件头正确识别为 3 个 `.webp` + 2 个 `.jpg`。

### 回归测试

```bash
$PY -m pytest tests/store/test_article_store.py -k referer -v
```

`test_referer_is_sent_when_downloading`

---

## B · 抽取降级时，站点通用标题覆盖了 RSS 的正确标题

### 现象

`dna list` 里 oschina 的两条记录，标题都是
**「OSCHINA - 开源 × AI · 开发者生态社区」**，正文 27 字，状态 degraded。

落盘目录名也跟着变成了 `OSCHINA-开源-×-AI-开发者生态社区__42fdf034`。

### 根因

oschina 是 SPA：服务端只返回一个外壳页，`og:title` 是站点自己的通用标题，正文为空。

原来的 `extract_article()` 无条件优先使用页面标题（`og:title` → `<title>` → `<h1>`），
只在页面完全没有标题时才回退到 RSS 标题。结果是：**抽取降级时，一个毫无信息量的
站点名覆盖掉了 RSS 里本来正确的标题**——抽取反而让数据变差了。

**更糟的是不可逆**：台账里只有一列 `title`，被覆盖后原始标题就丢了。
重抓也救不回来，因为重抓的兜底标题正是那个已被污染的值。

### 修复

三处：

1. `extract/article.py::_choose_title` —— **抽取降级时优先用 fallback 标题**。
   正文抽不出来时页面标题同样不可信，两者失效的原因是同一个。
   抽取成功时仍以页面标题为准（页面标题通常比 RSS 的更完整，RSS 常截断）。
2. `store/db.py` —— schema 升到 **v2**，新增 `feed_title` 列保存源发布的原始标题，
   永不被抽取结果覆盖。老库通过 `_MIGRATE_V2` 自动迁移，不需要删库重来。
3. `store/ledger.py::register` —— **每次采集都以源为准刷新 `feed_title`**。
   源才是标题的权威出处，因此历史上已被污染的行会在下一轮采集时自愈。
   `store/intake.py::refetch_article` 改用 `feed_title` 作为兜底。

### 复验

```bash
dna fetch --source oschina --limit 2 --refetch
dna list --source oschina
```

标题恢复为真实文章标题：

```
130d7362 | 缓存读取价砍 75%：Fable 5.1 真正想卖的不是跑分
42fdf034 | 从 SIP 到大模型：一天构建可本地部署的 AI 智能外呼系统 VibeVoip
```

迁移与自愈也已确认：

```
user_version: 2
has feed_title: True
feed_title 已从被污染的值恢复为真实 RSS 标题
```

### 回归测试

```bash
$PY -m pytest tests/extract/test_extract.py -k title -v
$PY -m pytest tests/store/test_ledger.py -k feed_title -v
```

- `test_degraded_extraction_keeps_rss_title_over_page_title`
- `test_successful_extraction_still_prefers_page_title`
- `test_feed_title_is_preserved_separately`
- `test_feed_title_refreshes_from_source`

---

## 附带确认

oschina 与部分英文源（texasmonthly 返回 403）抽取降级属于**预期行为**：
正文抽不出来时降级为「仅标题 + 链接」，条目仍然可用、仍会进日报。
14 条采集里 4 条降级、0 条失败，其余正常。

## 关联

- `docs/issues/003`（P2 的三个发现，同属抽取层）
- `docs/03_unit_tests.md` P2+ 章节
