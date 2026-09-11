# issue 007 — P3.5 开发与验证中发现的问题

> 日期：2026-09-03　状态：①②已修，③确认为外部限制

---

## A. 图片水印无法在抓取时绕开 —— 确认为外部限制，不处理

### 用户的问题

> 「有一些图片抓取下来后会有水印，这个水印是否能在抓取时避免」

### 验证过程

**看图确认**：量子位的图右下角有「公众号：量子位」+ logo，微信的图右下角有
淡色账号水印。**都在源文件的像素里**，不是叠加层。

**探测无水印变体**（`i.qbitai.com/wp-content/uploads/2026/09/d2067ef…`）：

| 尝试的地址 | 结果 |
|---|---|
| `.webp`（原地址） | 200，14976 字节 |
| `.png` | 404 |
| `-scaled.webp` | 404 |
| `_original.webp` | 404 |

**只存在一个版本，就是带水印的那个。** 水印由发布方在上传时烧进去，
我们下载到的文件就是它。

### 结论

抓取时无法绕开。可选的补救各有代价：

| 办法 | 代价 |
|---|---|
| 裁掉右下角 | 实测量子位那张表格图会连最后一行数据一起裁掉 |
| 算法去除（inpainting） | 对文字和表格效果不可靠；且**去除发布方水印后再发布，与本项目一贯强调的可追溯署名相抵触** |
| 检测并标记，多源时优先选无水印的 | 可行但收益有限 |

**用户决定：不处理。** 原样保存，发布时自行取舍。

---

## B. arXiv 文章抓到的「配图」是基金会 logo —— 已修

### 现象

翻查落盘目录时发现，两篇 arXiv 论文的唯一配图都是：

```
https://arxiv.org/static/base/1.0.1/images/funders/simons-foundation.png
```

——页脚的赞助方标识。因为 arXiv 摘要页本身没有任何配图，这个 logo 就顶上了，
成了这两条目在日报里的封面。

### 根因

`_NON_CONTENT_MARKERS`（`src/dna/extract/media.py:50`）里有 `logo`、`icon`、
`banner` 等，但**没有 `funder` / `sponsor` / `partner`**。路径里的
`funders/simons-foundation` 一个都没命中。

### 修复

补上 `funder`、`funders`、`sponsor`、`sponsors`、`partner`、`partners`、`affiliate`。

沿用既有的**词边界匹配**而非子串匹配，因此 `partnership-diagram.png`、
`sponsorship-model-chart.jpg` 这类真实配图不受影响——这正是当初选词边界的理由
（[issue 003-B](003-p2-live-verification-findings.md)）。回归测试两条都覆盖了。

修复后 arXiv 条目的配图数变为 0，这是**正确**结果：它本来就没有配图。

---

## C. 长文案的目标与验收用了两种单位 —— 已修

### 现象

真机跑一篇访谈长文案，日志说「目标 4000 字（约 15 分钟）」，
产出 3900 字，但实测口播时长只有 **7.4 分钟**——差了一倍。

### 根因

两处用了不同的单位：

- `plan_target_chars()` 按**原文字符数** × 1.2 推导目标
- `estimate_seconds()` 按**汉字数 / 4.5 + 英文词数 / 2.6** 计算时长

对纯中文稿这两者大致等价，但这是一篇技术稿。实测成稿的构成：

| | 数量 |
|---|---|
| 总字符 | 3319 |
| 汉字 | 1693 |
| 中文标点 | 198 |
| 空格 | 321 |
| 英文/数字字符 | 1107（205 个词） |

`DeepSeek-V4-Flash-0731`、`UD-Q8_K_XL` 这类标识符**一个就占十几个字符，
但念出来的时间远不成比例**。于是「4000 字」在中英混排的技术稿里只值 7.4 分钟。

### 修复

把目标推导搬进**时长空间**：

```python
def plan_target_seconds(article):
    source_seconds = estimate_seconds(article.text)     # 原文念出来多久
    return clamp(source_seconds * 1.2, 300, 900)        # 成稿目标时长

def plan_target_chars(article):
    return target_chars(plan_target_seconds(article))   # 换算成提示词能用的字数
```

同时**删掉了 `MIN_TARGET_CHARS` / `MAX_TARGET_CHARS` 两个独立常量**：
它们和时长上下限是同一件事的两种单位，各写一份迟早会漂移——写这个修复时就
已经出现了「300 秒下限换算出 1350 字、却又另设 1200 字下限」的矛盾。
现在长度**只在一处定义**（时长窗口），字数由它换算。

### 遗留

中英混排稿的实测时长仍会低于按字数推算的值——要精确校准得建立
「英文标识符字符数 → 口播秒数」的经验系数。按用户要求，
**本阶段只求能按要求输出，时长精细化校准留到后续**，已在代码与文档里注明。

### 教训

**同一个量不要有两种单位的定义。** 一旦有两个，它们就会各自演化，
而且不一致的时候不会报错——只会让产出悄悄偏离预期。

---

## 相关

- 代码：`src/dna/extract/media.py`、`src/dna/narration/longform.py`
- 测试：`tests/extract/test_extract.py`、`tests/narration/test_longform.py`
- 前序：[issue 003-B](003-p2-live-verification-findings.md)（词边界匹配的由来）、
  [issue 005](005-wechat-zhihu-live-verification.md)（属性级装饰图过滤）
