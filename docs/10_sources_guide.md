# 10 添加信息源指南 / Adding News Sources

> 面向使用者的操作手册。**全程不调用 LLM，不产生费用**，可放心反复试。

---

## 一、最快的办法：`dna probe`

```bash
dna probe <网站首页或 feed 地址>
```

它会依次尝试三件事，并**当场验证**能不能真的解析出条目：

1. 你给的地址本身是不是 feed
2. 页面 HTML 里声明的 feed（`<link rel="alternate" type="application/rss+xml">`）
3. 常见路径猜测（`/feed`、`/rss`、`/rss.xml`、`/atom.xml` …共 11 个）

> 第 3 步不是多余的：实测 **InfoQ 的 feed 完全可用，但首页并没有声明它**。
> 只靠声明发现会漏掉这类站点，而它们并不少见。

验证通过后直接给出可粘贴的 YAML 片段：

```bash
$ dna probe https://www.infoq.cn/ --lang zh --tags tech,cn

推荐：https://www.infoq.cn/feed
最新一条：VoidZero 发布 Vite+ Beta 版：一体化 Web 工具链

粘进 config/sources.yaml 的 sources: 下面即可：

  - id: infoq
    name: InfoQ - 促进软件开发领域知识与创新的传播
    kind: rss
    url: https://www.infoq.cn/feed
    lang: zh
    tags: [tech, cn]
    enabled: true
```

常用参数：

| 参数 | 作用 |
|---|---|
| `--id` | 指定源 id（不指定则从域名猜一个） |
| `--name` | 指定显示名（不指定则用 feed 自带的标题，通常偏长，建议改短） |
| `--lang` | `zh` / `en`，决定是否需要翻译 |
| `--tags` | 逗号分隔，如 `ai,cn`，用于筛选与统计 |

**片段里的 `name` 记得改短**——feed 自带的标题常常是一整句站点简介。

---

## 二、加完之后必须验证

```bash
dna sources                      # 确认新源出现在启用列表里
dna fetch --source <你的id> -n 3 # 实际抓一次
```

`dna fetch` 会明确列出失败的源和原因。**采集是逐源隔离的**——一个源挂掉不影响其余源，
所以加错了不会导致当天没有日报，但也因此**不主动验证就不会发现问题**。

---

## 三、找不到 feed 怎么办

`dna probe` 报「没有找到可用的 feed」通常意味着该站真的不再提供 RSS。
实测机器之心、36氪 都是如此——SPA 改版后所有常见路径要么 404 要么返回 HTML。

三条出路：

| 办法 | 适用 | 状态 |
|---|---|---|
| **自建 RSSHub** | 微信公众号、知乎、微博、X 等无 RSS 的平台，含机器之心/36氪 | P10 阶段接入 |
| **用户投递接口** | 你随手看到的单篇文章 | 飞书机器人，P9 阶段接入 |
| 找第三方镜像 feed | 部分站点有社区维护的镜像 | 稳定性不保证 |

第一条是根治办法：RSSHub 有上千条路由，覆盖绝大多数中文平台。
接入后在 `sources.yaml` 里写路由即可（`kind: rsshub`，`url` 只填 `/zhihu/hotlist` 这样的路由部分，
实例地址取 `.env` 的 `RSSHUB_BASE_URL`）。

---

## 三 bis、过滤掉不想要的文章

每个源可以在 `config/sources.yaml` 里配自己的过滤规则。**过滤在抓正文之前执行**——
被滤掉的条目不发 HTTP 请求、不下载图片、不占存储，后续也不进 LLM。省下的是全链路成本。

```yaml
  - id: hackernews-front
    name: Hacker News Front Page
    url: https://hnrss.org/frontpage
    filters:
      exclude: [Is Hiring, "Who is hiring", Ask HN, Show HN]
      min_title_length: 10

  - id: arxiv-cs-ai
    name: arXiv cs.AI
    url: https://rss.arxiv.org/rss/cs.AI
    max_items: 15
    filters:
      include: [LLM, language model, multimodal, agent, reasoning, inference, benchmark]
```

| 规则 | 含义 |
|---|---|
| `include` | 命中任一才保留；**留空 = 不限制**。适合噪声大的源（如 arXiv） |
| `exclude` | 命中任一即丢弃，**优先级最高**（同时命中 include 也丢） |
| `min_title_length` | 标题最少字数，滤掉「快讯」这类空标题 |
| `max_age_days` | 只要最近 N 天的。**没有发布时间的条目不受此限**——很多 feed 不给 pubDate，把「没时间」当「很旧」会把整个源误杀 |

匹配范围是**标题 + 源自带的摘要**，大小写不敏感。只匹配标题会漏掉那些标题含蓄、
摘要里才点明主题的条目。

**全局排除词**写在 `config/profile.yaml` 的 `exclude_keywords`，对所有源生效；
源级 `exclude` 在其之上追加。注意 `focus_keywords` **不是过滤条件**——它是给 P3
打分加权用的，全局强制「必须命中某关键词」会误杀大量正常资讯。

验证过滤效果：

```bash
dna fetch --limit 10 --dry-run   # 看采集到什么
dna fetch --limit 10             # 实际入库，会打印「过滤明细」
```

---

## 四、几条实践经验

**条目多的源必须限量。** 实测 arXiv cs.AI 单次返回 **302 条**、OpenAI News 返回 **1159 条**
（全量存档）。不加 `max_items` 会让这一个源淹掉整期日报，并成倍放大 LLM 调用量。
`dna probe` 对超过 50 条的源会自动在片段里加上 `max_items: 15`。

**失效的源不要直接删。** 保留条目并标 `enabled: false` + 写明失效原因，
避免以后自己或别人又把它加回来。`config/sources.yaml` 里已按这个约定组织。

**SSL 报错不一定是证书问题。** 公司网络下若看到 `CERTIFICATE_VERIFY_FAILED`，
先确认到底是证书还是被代理阻断：

```bash
python -c "
import httpx
r = httpx.get('<feed地址>', verify=False, timeout=25, follow_redirects=True,
              headers={'User-Agent':'Mozilla/5.0'})
print(r.status_code)"
```

返回 **504 → 代理层面不可达**，装 CA 证书没用（TechCrunch 就是这种）；
返回 **200 → 才是证书配置问题**。

**源不是越多越好。** 每多一个源，日报条目变多、LLM 调用量和费用同步上升。
`config/sources.yaml` 里已实测可用但默认关闭的源，按需打开即可。

---

## 五、当前源清单状态（2026-09-01 实测）

### 已启用（7 个）

| id | 名称 | 语言 | 备注 |
|---|---|---|---|
| `qbitai` | 量子位 | zh | AI 资讯 |
| `infoq-cn` | InfoQ 中文 | zh | 技术 |
| `oschina` | 开源中国 | zh | 限量 15 |
| `hackernews-front` | Hacker News | en | 技术 |
| `arxiv-cs-ai` | arXiv cs.AI | en | 限量 15（原始 302 条） |
| `openai-news` | OpenAI News | en | 限量 10（原始 1159 条） |
| `venturebeat-ai` | VentureBeat AI | en | AI 资讯 |

### 已验证可用、默认关闭（按需开启）

少数派 · Ars Technica · The Verge · Hacker News Best · arXiv cs.CL · arXiv cs.LG

### 已失效（保留记录）

| 源 | 原因 |
|---|---|
| 机器之心 | 不再提供 RSS，所有常见路径 404 或返回 HTML |
| 36氪 | 同上 |
| TechCrunch | 公司代理不可达（504），非证书问题 |
| 虎嗅 · 阮一峰 · MIT Tech Review · Google Research · HuggingFace Blog | 公司网络下被代理阻断，换网络环境可能可用 |

复核方式：`dna fetch --limit 3`，失败的源会明确列出。

---

## 六、相关

- 探测实现：`src/dna/sources/discover.py`
- 失效源的排查记录：[issues/003](issues/003-p2-live-verification-findings.md)
- RSSHub 接入计划：[02_development_plan.md](02_development_plan.md) P10
