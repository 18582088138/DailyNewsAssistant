# 013 · 微信公众号视频一个都抓不到 · 设置面板打不开 · 弹窗被切掉

日期：2026-09-11 · 来源：用户实测反馈六条（另外三条是 TTS 操作台的功能补全）

## ① 设置面板点开直接报错（回归）

```
ValueError: Invalid value:            ← 注意冒号后面是空的
  settings_dialog.py:391 → nicegui/elements/choice_element.py:22
```

和 `issues/012` 里操作台那条**是同一个坑**：`ui.select` 的初值必须在选项里，
而**空字符串不是合法初值**（NiceGUI 只放过 `None`）。触发条件是两件事同时成立：

- `.env` 里 `TTS_VOICE_GUEST` 没配（很常见，双人访谈才用得上），于是 `value == ""`；
- **TTS 服务在线**，`tts_voice_options()` 拿到了音色表，于是走的是下拉框那条分支
  （服务离线时 `if voices` 为假，退成文本框，反而不报错）。

所以它是「修好了 TTS 服务之后才出现」的回归：012 那轮把 torchaudio 装对了，
服务真的在线了，这条分支第一次被走到。

修法：三处 `ui.select` 统一写 `value=value or None`
（音色 · 参考音频 · 运行设置里的枚举项）。**这类下拉框以后只写这一种写法。**

## ② 微信公众号文章的视频一个都识别不到

实测那篇 MiniMax H3（`mp.weixin.qq.com/s/XhU4W02…`）：正文 4000 余字、配图 2 张都
抓到了，`videos: null`。**而「一个都没识别到」和「这篇没有视频」在界面上长得一模一样**
——这是「失败了也没有提示」的真实来源。

微信的视频markup 三条都不落在原来的抽取规则上：

| 原来只认 | 微信实际给的 |
|---|---|
| `og:video` | 没有 |
| `<video>` / `<source>` | 没有 |
| 指向已知视频站的 `<iframe>` | `<iframe class="video_iframe" data-mpvid="wxv_…" data-src="…/mp/readtemplate?t=pages/video_player_tmpl…">`，那个模板页 **yt-dlp 解析不了** |

真正的 mp4 直链藏在页面里的一段 JS 里：

```
http://mpvideo.qpic.cn/<clip>.f10004.mp4?dis_k=…&dis_t=…&auth_info=…
```

三个必须记住的细节：

1. **`&` 在那段 JS 里被转义成 `\x26amp;`。** 不先反转义就上正则，会在反斜杠处截断，
   得到一条丢了 `auth_info` 的 URL——下载回来是错误页。（`video_store` 会按文件头
   拦下来，但那时表现成「视频下载失败」，根因完全看不出来。）
2. **同一条视频给四种码率**（`f10002`/`f10004`/`f10102`/`f10104`），每条只留一个，
   **优先 H.264**（`f10004` > `f10002`）——`f101xx` 是 HEVC，剪辑软件和浏览器的支持
   差得多，而这些素材是要拿去剪片子的。
3. 抽取阶段的视频上限从写死的 `3` 改成 `MAX_VIDEO_CANDIDATES = 10`：
   **抽取记候选，下载几个由 `max_videos_per_article` 决定**。写死 3 会把配置里
   调大的值悄悄压回去，和图片那边 `DEFAULT_MAX_IMAGES = 10` 是同一个道理。

### 反爬的边界（实测）

| 请求 | 结果 |
|---|---|
| 文章页 · 裸 UA | `环境异常，完成验证后即可继续访问`（3.7 万字节的验证页） |
| 文章页 · 项目自己的 `fetch_text()`（Chrome UA + `Accept-Language`） | **200，3.3 MB 真页面** |
| mp4 直链 · 带 Referer | **206 `video/mp4`，`ftypisom`**，实测下载成功 |

所以 CDN 这一层没有反爬，**视频是能下的**；此前抓不到纯粹是没认出 markup。
文章页那一层已经被现有的浏览器请求头绕过了——这也是为什么正文和配图一直都正常。

> 微信随时会改这段 JS 的形状。改版后的表现仍然是「视频 0 个」，
> 查的时候先把页面存下来 grep `mpvideo.qpic.cn`：有直链就是正则该更新，没有就是
> 请求头被降级到了验证页。

## ③ 「媒体未下载 N」不带原因

`skipped_videos` 的原因一直记在文章目录的 `references.md` 里，但界面上只报个数字。
现在两处提示都带上第一条原因（地域限制 / 封禁下载 / 拿回来是错误页决定了
要不要人工去弄），完整清单仍在 `references.md`。

## ④ 弹窗被切掉，只能拉整页的滑条

`ui.dialog` 里的卡片没有高度上限，面板一长就顶出屏幕。新增一个 `.wb-dialog` 类
（`theme.py`），六个弹窗共用：

```css
.wb-dialog { resize: both; overflow: auto; max-height: 88vh; max-width: 96vw; }
```

`resize: both` **只在 `overflow != visible` 时生效**，这两条必须成对写。
右下角可以拖着拉大。

## 相关文件

- `frontends/nicegui_app/settings_dialog.py` —— 三处 `value or None`
- `src/dna/extract/media.py` —— `_wechat_videos()` / `_WX_VIDEO_RE` / `_WX_FORMAT_ORDER`
- `frontends/nicegui_app/actions.py` —— 提示带原因；`subtitle_file()`
- `frontends/nicegui_app/theme.py` —— `.wb-dialog`
- `frontends/nicegui_app/tts_panel.py` —— 整篇文案框 · 全部生成 · 拼接不重合成
