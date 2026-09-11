# 003 · P2 真机验证发现的三个问题

| | |
|---|---|
| 发现阶段 | P2 信息源与抽取层，`dna fetch` 真机验证时 |
| 日期 | 2026-09-01 |
| 状态 | ✅ 全部处理完毕 |
| 影响面 | 采集可靠性（A）、日报配图质量（B、C） |

---

## A · feed 失效被静默记成「成功，0 条」

### 现象

单元测试先暴露出来：断言「无法解析的内容应抛错」失败了，实际没抛。

### 根因

原判据是 `parsed.bozo and not parsed.entries`。实测 feedparser 的行为：

| 输入 | version | bozo | entries |
|---|---|---|---|
| 空字符串 | `None` | `False` | 0 |
| 纯文本 | `''` | `1` | 0 |
| **HTML 错误页** | `''` | **`False`** | 0 |
| 合法但空的 feed | `'rss20'` | `False` | 0 |
| 合法有条目 | `'rss20'` | `False` | 1 |

**`bozo` 不可靠**——HTML 错误页的 `bozo` 竟然是 `False`。

这意味着：站点改版后旧 feed 地址返回 HTTP 200 + HTML 404 页时，该源会被**静默记成
「成功，0 条」**，日报悄悄变短，日志里却没有任何失败记录。这类问题极难被发现。

### 复现

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe - <<'EOF'
import feedparser
for name, content in {
    "HTML错误页": "<html><body>404 Not Found</body></html>",
    "合法但空的feed": '<?xml version="1.0"?><rss version="2.0"><channel><title>T</title></channel></rss>',
}.items():
    p = feedparser.parse(content)
    print(f"{name}: version={p.get('version')!r} bozo={p.bozo} entries={len(p.entries)}")
EOF
```

### 修复

`src/dna/sources/rss.py::parse_feed` 改用 **`version` 作为判据**：feedparser 认出
feed 格式时给出 `'rss20'` / `'atom10'`，认不出时是 `''` 或 `None`。

- 不是 feed → 抛 `SourceError`（会被 registry 记进 failures 并显示给用户）
- 合法但当前没有新条目 → 返回空列表，**不算错误**（低频更新的源今天没内容是正常的）

### 回归测试

```bash
$PY -m pytest tests/sources/test_rss.py -k "html_error or valid_but_empty or non_feed" -v
```

- `test_html_error_page_raises_not_silently_empty`
- `test_valid_but_empty_feed_returns_empty_not_error`
- `test_non_feed_content_raises`

---

## B · og:image 是站点 logo 时被当成封面

### 现象

`dna fetch --source qbitai --extract` 抽出的 5 张配图里，**第一张是
`qbitai-logo-1.png`**，第二张是 `head.jpg`（站点模板头图）。

第一张图会成为日报的封面。也就是说，**每一条量子位的新闻，封面都会是量子位的 logo**。

### 根因

两个独立疏漏：

1. `_social_images()` 没有调用 `_looks_like_chrome()`。当初的假设是「og:image 是站点
   自己挑的分享图，必然合适」——**这个假设不成立**：不少站点没有为每篇文章单独配图，
   直接把自家 logo 写死进 og:image。
2. `head.jpg` 没被命中。标记表里有 `header` 却没有 `head`，而当时用的是**子串匹配**，
   `"header" in "head.jpg"` 为假。

### 修复

`src/dna/extract/media.py`：

1. `_social_images()` 同样应用图标过滤；og:image 被过滤后，真实配图自动顶上来当封面
2. 标记表补 `head` / `banner` / `default` / `nopic`
3. 子串匹配改为**按词匹配**（`_MARKER_RE`，以非字母数字为边界）

### 为什么必须改成按词匹配

补了 `head` 之后，子串匹配会误伤真实配图：`overhead-view.jpg`、`headline-photo.png`
都含 `head`，`iconic-moment.jpg` 含 `icon`。这些是内容图，被丢掉是无声的质量损失。
按词匹配同时满足两边：命中 `qbitai-logo-1.png`、`head.jpg`，放过上述文件名。

同时限定**只看 URL 的 path、不看域名**——图床域名形如 `img.logo-cdn.com` 很常见，
按域名判断会把整个图床的图片全部误杀。

### 复验

```bash
$PY -m frontends.cli.main fetch --source qbitai --limit 1 --extract
```

修复后 5 张图全部来自 `i.qbitai.com/wp-content/uploads/2026/09/`，均为真实文章配图。

### 回归测试

```bash
$PY -m pytest tests/extract/test_extract.py -k "logo or header or marker" -v
```

- `test_og_image_is_filtered_when_it_is_a_logo`
- `test_site_header_image_filtered`
- `test_marker_matching_does_not_over_reject`（4 个参数化用例）
- `test_marker_matching_ignores_host`

---

## C · 三个订阅源实测失效

`dna fetch` 跑通后逐源核对，发现默认配置里有三个源不可用：

| 源 | 现象 | 处置 |
|---|---|---|
| 机器之心 `jiqizhixin.com/rss` | 返回 HTML 页面而非 feed，站点疑似改版 | `enabled: false` |
| 36氪 `36kr.com/feed` | 同上，返回 HTML | `enabled: false` |
| TechCrunch AI | 公司网络下不可达 | `enabled: false` |

TechCrunch 的报错是 `SSL: CERTIFICATE_VERIFY_FAILED`，看起来像证书配置问题，
但**跳过证书验证后返回 HTTP 504**——是公司代理层面不可达，装 CA 证书没有用。
判定方法：

```bash
$PY -c "
import httpx
r = httpx.get('https://techcrunch.com/category/artificial-intelligence/feed/',
              verify=False, timeout=25, follow_redirects=True,
              headers={'User-Agent':'Mozilla/5.0'})
print(r.status_code)"   # 504 → 代理不可达；200 → 才是证书问题
```

### 替补源（已实测可用）

| 源 | 结果 |
|---|---|
| 量子位 `qbitai.com/feed` | ✅ |
| InfoQ 中文 `infoq.cn/feed` | ✅ 20 条（替补机器之心） |
| Hacker News `hnrss.org/frontpage` | ✅ |
| arXiv cs.AI `rss.arxiv.org/rss/cs.AI` | ✅ 302 条 → **必须限量**，已设 `max_items: 15` |

失效的源保留在 `config/sources.yaml` 里并标 `enabled: false` + 失效原因注释，
避免以后重复踩坑。

### 2026-09-10 补记：这些源已从配置里删除，记录只留在这里

留在 `sources.yaml` 里的坏源没有起到「避免重复踩坑」的作用——它们在
「从订阅导入」的源列表里照样占位（只是勾不上），每次打开都要重新确认一遍
「这几个是不是忘了开」。用户实测后要求删干净。配置里只留一行指回本文件。

**下面这五个不要再加回 `config/sources.yaml`：**

| id | 地址 | 失效原因 | 复核日期 |
|---|---|---|---|
| `jiqizhixin` | `https://www.jiqizhixin.com/rss` | 返回 HTML 页面而非 feed，站点改版 | 2026-09-01 |
| `36kr` | `https://36kr.com/feed` | 同上，返回 HTML | 2026-09-01 |
| `techcrunch-ai` | `https://techcrunch.com/category/artificial-intelligence/feed/` | 公司网络下代理返回 504（不是证书问题，判定方法见上） | 2026-09-01 |
| `zhihu-hot` | RSSHub 路由 `/zhihu/hotlist` | 知乎 403「安全验证」，issue 005 已实测四种抓取方式全部失败 | 2026-09-01 |
| —（无 id） | huxiu · feedburner/阮一峰 · MIT Tech Review · Google Research · HuggingFace Blog | 公司网络下被代理阻断（SSL / 超时），**换网络环境可能可用** | 2026-09-01 |

`enabled: false` 但**实测可用**的那批（sspai / arstechnica / theverge /
hackernews-best / arxiv-cs-cl / arxiv-cs-lg）全部保留在配置里——
它们关掉是为了控制篇幅，不是源有问题。

---

## 附带确认：错误隔离按设计工作

这一轮验证里同时有 2 个源失败、2 个源成功，采集照常返回 6 条并明确列出失败源。
这正是 `registry.collect()` 逐源隔离要达到的效果——日报每天无人值守跑一次，
任何一个源出问题都不该导致当天没有日报。

## 关联

- `docs/02_development_plan.md` §2 目录结构（sources / extract 层）
- `docs/03_unit_tests.md` P2 章节
