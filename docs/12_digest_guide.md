# 12 生成日报指南 / Building the Daily Digest

> 面向使用者的操作手册。⚠️ **本命令会调用 LLM 并产生费用**，请先读第一节。

---

## 一、先花零元看清单

```bash
dna digest --dry-run
```

这条命令**一次 LLM 都不调用、不产生任何费用、不写任何文件**。它跑完
「取数 → 清洗 → 去重 → 打分」，把会选出哪些条目、按什么顺序、为什么，
全部打印出来：

```
21 条 → 20 个事件，近重复合并 1

1. [0.583] The efficient frontier of LLM inference
     0.583 = 关键词 0.83 + 多源 0.00 + 新鲜度 0.62 + 篇幅 1.00 + 配图 0.60（命中：LLM、agent、inference）
2. [0.479] 企业级Agent落地样板间！百融硅基员工批量上岗，按结果领工资
     0.479 = 关键词 0.50 + 多源 0.00 + 新鲜度 0.68 + 篇幅 1.00 + 配图 0.60（命中：大模型、agent）
3. [0.460] 英特尔为MiniMax H3提供Day 0支持…  📹
     ...
```

**先看清单再付费。** 选题不对时改配置重跑，仍然是零成本；反过来的话，
选错了也是付完钱才发现。

`📹` 表示这条被标记为适合做短视频。

### 排序不对怎么调

评分明细直接告诉你是哪一项的问题：

| 现象 | 该改哪 |
|---|---|
| 不相关的内容排在前面 | `config/profile.yaml` 的 `focus_keywords` 加词 |
| 想要的内容排不上去 | 同上，或看它的「新鲜度」是不是太低（超过 48 小时就归零） |
| 某类内容根本不该出现 | `profile.yaml` 的 `exclude_keywords`，或源级 `filters`（见 [10](10_sources_guide.md)） |
| 视频标记太少/太多 | `profile.yaml` 的 `video_keywords` |

五项信号的权重：关键词 0.35、多源 0.25、新鲜度 0.20、篇幅 0.12、配图 0.08。

---

## 二、真的生成

```bash
dna digest                       # 按 profile.yaml 的条数
dna digest --limit 5             # 只做 5 条（**先用小数量试**）
dna digest --bilingual           # 同时产出英文版（额外计费）
dna digest --since-days 3        # 回看 3 天（默认 2 天）
dna digest --source qbitai       # 只用某个来源的文章
```

产物是 **`outputs/YYYYMMDD-DailyNews/_digest.json`**。

### 人工挑几篇做一期

```bash
dna list                                    # 先在总表里看
dna digest -a 65960cc3 -a decef4a4 -a 5c4430ab
```

`-a` 指定的文章**不受时间窗与来源过滤限制**——你点名要的就是想要的。
这就是「在总表里勾选几篇做日报」。

---

## 三、费用与缓存

流水线分两半：

```
clean → dedup → score  │  summarize → translate → trend
──── 免费，随便跑 ────  │  ──── 每次运行都计费 ────
```

`--dry-run` 停在分界线上。

**相同的请求会命中本地磁盘缓存，不重复计费。** 实测：

| 操作 | 调用次数 | 耗时 |
|---|---|---|
| 5 条日报，首次 | 6 次 | 10.7 秒 |
| 同样输入再跑一次 | **0 次** | 0.1 秒 |
| 加 `--bilingual` | 2 次（摘要走缓存） | 6.7 秒 |

命令结尾会打印命中率：

```
LLM 缓存：命中 6 / 6（省下 6 次计费调用）
```

缓存键包含 **provider + 模型 + 完整提示词 + 调用参数**，所以换模型或改提示词
会自然错开，不会拿旧模型的答案冒充新的。缓存在 `data/llm_cache/`，
删掉即可全部重来；不想用缓存时加 `--no-cache`。

---

## 四、`_digest.json` 是什么

它是**唯一的事实源**，三个发布应用（图文 / 视频 / 播客）都只读它：

```json
{
  "date": "2026-09-02",
  "entries": [
    {
      "rank": 3,
      "title_zh": "英特尔为MiniMax H3提供Day 0支持…",
      "title_en": "Intel Provides Day 0 Support for MiniMax H3…",
      "summary_zh": "英特尔锐炫Pro B70为开源视频生成模型…",
      "summary_en": "Intel Arc Pro B70 provides Day 0 support…",
      "score": 0.4601,
      "tags": ["芯片", "开源", "大模型", "视频生成"],
      "need_video": true,
      "images": [...],
      "videos": [...],
      "refs": ["https://..."]
    }
  ],
  "trend_note_zh": "今日多条新闻共同指向AI落地的效率与成本优化…",
  "trend_note_en": "Today's news collectively points to…",
  "stats": {"raw_count": 21, "entry_count": 5, "llm_provider": "deepseek", ...}
}
```

**它一旦生成，重做任何一种输出都不需要重新采集、也不需要再调 LLM。**
「补个英文版」「只重做播客」之所以几乎免费，就是因为有它。

`refs` 取的是**整个聚类的**全部来源链接，不只是被选中那一家——多源报道时
读者应该看到全部出处。

---

## 五、双语

```bash
dna digest --bilingual
```

**双语是渲染期参数，不是采集期参数。** 已有的 `_digest.json` 随时可以补出英文版，
不用重跑流水线。翻译的输入是已写好的中文摘要而不是原始正文——这样中英两版
一定说的是同一件事，成本也低得多。

漏译的条目 `title_en` / `summary_en` 保持 `null`，明确表示「这条没有英文版」，
可以单独补译；不会填空串糊弄过去。

---

## 六、常见情况

**「台账里没有可用的文章」** —— 先 `dna fetch` 或 `dna add`。

**摘要降级** —— 输出里会写「其中 N 条摘要降级」，表示这几条 LLM 调用失败、
退回用标题当摘要。日报仍然完整，只是这几条不如别的精炼。重跑一次通常就好了
（其余条目走缓存，只有失败的那几条重新调用）。

**没有主线综述** —— 条目少于 4 条时不生成。两三条谈不上「趋势」，
硬写只会得到一段把标题重述一遍的废话，还要花钱。

**去重合并错了** —— `--dry-run` 里能看到「近重复合并 N」。阈值取的是保守值
（汉明距离 ≤3）：宁可漏合并（你自己看得出两条相似），也不要错合并
（两件事混成一条，读者无从察觉）。

---

## 七、相关

- 提示词与调优记录：[06_prompt_spec.md](06_prompt_spec.md)
- 添加与过滤信息源：[10_sources_guide.md](10_sources_guide.md)
- 文章总表与落盘：[11_article_store_guide.md](11_article_store_guide.md)
- 实现：`src/dna/pipeline/`（`clean` · `dedup` · `score` · `summarize` · `translate` · `trend` · `flow`）
