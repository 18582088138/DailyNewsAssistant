# issue 005 — 微信 / 知乎真机验证发现的问题

> 触发：用户提供三个真实链接要求验证「给链接 → 存文章 + 图片/视频」这条主路径
> 日期：2026-09-02　状态：4 项已修，1 项确认为外部限制

## 验证用的链接

| 链接 | 结果 |
|---|---|
| `mp.weixin.qq.com/s/XhU4W02g…`（MiniMax H3） | ✅ 正文 3151 字 |
| `mp.weixin.qq.com/s/VgO-WiLW…`（DeepSeek-V4-Flash） | ✅ 正文 4069 字 |
| `zhuanlan.zhihu.com/p/2067333343717355528` | ❌ HTTP 403 |

---

## A. 知乎 403 —— 外部限制，不修

未登录访问知乎专栏返回 403，页面标题是「安全验证 - 知乎」。逐级验证过：

| 方式 | 结果 |
|---|---|
| httpx + 浏览器 UA | 403，584 字节 |
| httpx + 完整浏览器请求头（Accept / Referer / Sec-Fetch-*） | 403，584 字节 |
| Playwright headless Chromium | 403，「安全验证」页 |
| Playwright + 公司代理 + 关闭 `navigator.webdriver` | 403，「安全验证」页 27502 字节 |

**结论**：这不是 UA 或请求头能绕过的，知乎对非登录态做了拦截。要拿到正文只能复用
浏览器里的登录 cookie。

**决策（用户选择）**：不做 cookie 复用，采用「降级保存 + 手动补正文」。
配套做了 D 项，让手工补的正文能真正进入后续流程。

---

## B. 配图上限 5 张偏少 —— 已改为 10

用户反馈「3 张感觉有点少，可以扩展到 3~10 张，多积累素材」。

原先 `extract/media.py` 与 `store/article_store.py` 各有一个 `DEFAULT_MAX_IMAGES = 5`，
**两个都得改**——只改落盘那个不起作用，抽取阶段已经先截断了。

改为 10 后实测 DeepSeek 那篇由 5 张变为 10 张。CLI 加了 `--max-images` 可按次调整。

理由记在代码注释里：日报本身只用 1~3 张，但多存的是素材库（长图、口播配图、
视频封面都要挑图），而**重抓拿不回当初那些图**——站点会换图删图。存储成本远低于
错过素材。

MiniMax 那篇仍是 2 张，与上限无关：原文只有 1 张正文配图，其余是二维码和作者头像。

---

## C. 作者头像被当成正文配图 —— 已修

MiniMax 那篇存下来的第 3 张图是**作者头像**，caption 明确写着「作者头像」。

根因：`_looks_like_chrome()` 只匹配 URL 路径上的关键词，而微信图片地址是 CDN
随机串（`mmbiz_png/q4wL2iaHZfGkGQhHZ7wGA1e5ASg2ib9UyTOuic4…`），**路径上没有任何特征**，
路径匹配完全使不上劲。

但 HTML 属性很老实：

```html
<img class="jump_author_avatar" src="…" alt="作者头像">
<img class="jump_wx_qrcode_img js_qrcode_img" alt="跳转二维码">
```

修复：新增 `_chrome_by_attributes()`，检查 `class` / `alt` / `id`。这里用**子串匹配**
而不是路径过滤那种词边界匹配——class 名本来就是拼接的（`jump_author_avatar`），
词边界一个都匹配不上。

不这么做的话，**每篇微信文章都会白白存进一张作者头像**当素材。

---

## D. 重抓时旧图片没清 —— 已修

修好 C 之后重抓 MiniMax，`references.md` 只列 2 张图，但 `images/` 目录里有 3 个文件——
被新过滤规则剔掉的那张头像留在盘上，成了孤儿。

根因：重抓写进同一个目录（标题没变），但 `references.md` 是按本轮结果重新生成的。
两者不同步，多出来的文件**没有出处可查**，做素材时还会被误当成本篇配图用出去。

修复：`_clear_media_dir()` 在重新下载前清空 `images/` 与 `videos/`。只在确实要重新
下载时清（`--no-images` 时不动已有文件）。

同类问题还有一个：标题变化时会换一个新目录，旧目录留在盘上，同一个 id 出现两份。
实测盘上确有 `OSCHINA-…__130d7362` 与 `缓存读取价砍-75-…__130d7362` 两份，是 issue 004
修复后重抓留下的。修复：`_remove_stale_dirs()` 落盘前清掉同 id 的其他目录。

---

## E. 抓取失败的文章全叫「(抓取失败)」—— 已修

知乎那篇落盘目录叫 `抓取失败__cf06c31a`。写死一个常量意味着**所有失败的文章同名**，
目录里根本认不出哪篇是哪篇——而这些正是最需要人工处理的文章。

修复：新增 `core/urls.py::title_from_url()`，从 URL 推一个可读标题。

| URL | 推出的标题 |
|---|---|
| `example.com/2026/09/gpt-5-released` | `gpt 5 released` |
| `zhuanlan.zhihu.com/p/2067333343717355528` | `zhuanlan.zhihu.com p 2067333343717355528` |
| `mp.weixin.qq.com/s/VgO-WiLWNRzuSGSweHrY7g` | `mp.weixin.qq.com s VgO WiLWNRzuSGSweHrY7g` |

末段是有意义的词就直接用；**没有字母**（知乎的纯数字）或**又长又带大写**
（微信的随机串）则判定为无意义 id，补上主机名与前段。真正的标题 slug 几乎总是
全小写的短词，这两个信号足够区分。

---

## F. 视频只记链接不下文件 —— 已实现下载

用户明确要「有图片或者视频保存下来」，原先只在 `references.md` 里记地址。

新增 `store/video_store.py`，两条路径：

1. **直链**（`<video src="….mp4">`）→ HTTP 下载，带 Referer 防盗链（同 issue 004-A）
2. **播放器 / iframe**（腾讯视频、B站、YouTube）→ yt-dlp

几个刻意的选择：

- **按文件头校验是不是视频**。和图片同理，`.mp4` 的 URL 返回 HTML 错误页很常见，
  只信后缀会在盘上留一堆打不开的 mp4，而台账还显示「视频 1 个」。
- **yt-dlp 优先选单文件 mp4**（`best[height<=1080][ext=mp4]/…`）。合并音视频轨需要
  ffmpeg，而 ffmpeg 不保证装了。宁可清晰度略低但一定能播。
- **yt-dlp 延迟导入**，缺依赖时给出可执行的提示，不拖慢 CLI 启动。
- **失败只记原因不抛异常**。视频下载失败远比图片频繁（地域限制、会员墙、平台封禁），
  一个视频下不下来不该让整篇文章白抓。
- **台账的 `video_count` 记实际下载成功数**，不是抽到的链接数——抽到 3 个但一个都
  没下下来时写 3 会让人以为盘上有三个文件。

三个测试链接都没有嵌入视频，因此视频下载路径由单元测试覆盖（`tests/store/test_video_store.py`
18 项，含文件头校验、防盗链 Referer、失败隔离、限量），未做真机验收。

---

## G. 配套：人工补正文的回写链路（对应 A 的决策）

只让人把正文粘进 `article.md` 是不够的——台账里这篇仍是「0 字 / degraded」，
后续挑文章做日报时会被当成空文章跳过，**人辛苦补的正文等于白补**。

补齐的链路：

1. 降级文章的 `article.md` 里放一个明确的粘贴标记 `<!-- 正文粘贴区 -->`，
   并直接写出该执行的命令
2. `dna sync <id>` 从标记之后读回正文，**同时读回 `# ` 标题**（从 URL 推的标题
   不能直接当日报标题用，补正文的人通常顺手改对了）
3. 回写 `meta.json`（后续流程实际读的是它）+ 台账，状态升到 `ok`

状态直接升 `ok` 而不是停在 degraded：正文来自人而不是抽取器，比任何自动抽取都可靠。

实测：粘贴 → `dna sync cf06c31a` → 台账 `ok / 47 字`、标题同步更新、`meta.json`
的 `extraction_ok` 变为 `true`。

---

## 相关

- 代码：`store/video_store.py`、`store/article_store.py`、`extract/media.py`、`core/urls.py`
- 测试：`tests/store/test_video_store.py`、`tests/store/test_article_store.py`、
  `tests/extract/test_extract.py`、`tests/core/test_urls.py`
- 前序问题：[issues/004](004-image-hotlink-and-title-overwrite.md)（防盗链、标题覆盖）
