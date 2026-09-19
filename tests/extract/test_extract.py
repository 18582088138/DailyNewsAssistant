"""
test_extract.py —— 正文与媒体抽取单元测试 / Article and media extraction tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/extract/test_extract.py -v

样例 / Sample:
    tests/fixtures/sample_article.html —— 刻意包含真实世界的干扰项：
    导航/侧栏/页脚、懒加载图片、logo 与追踪像素、相对与协议相对地址、bilibili iframe

覆盖 / Covers:
    正文:
      1. 抽出正文且**不含导航、侧栏、页脚**（去模板是抽取的核心价值）
      2. og:title 优先于 <title>（<title> 常带「_站点名」后缀）
      3. 作者与 ISO 8601 发布时间
      4. **抽取失败时降级为「仅标题+链接」而非抛异常**——标题+链接本身仍有价值
      5. fallback_title 兜底（通常来自 RSS）
      6. 畸形 HTML / 空 HTML 不崩
    媒体:
      7. og:image 排在最前（站点自选的分享图，最适合当封面）
      8. **懒加载图片取 data-src 而非占位 src**——否则抓到的全是同一张 loading 图
      9. **过滤 logo / 图标 / 1x1 追踪像素**
     10. 相对地址与协议相对地址（`//cdn...`）补全为绝对地址
     11. figcaption 作为图片说明
     12. bilibili iframe 识别为官方视频
     13. **每个资产都带 source_url**（发布合规要求，必须能追溯出处）
     14. 数量上限与去重

预期 / Expected:
    耗时 < 3s；全程离线，只读本地样例文件
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from dna.core.models import MediaKind
from dna.extract.article import extract_article
from dna.extract.media import extract_media, looks_like_image_url

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PAGE_URL = "https://news.example.com/posts/1"


@pytest.fixture
def html() -> str:
    """样例文章 HTML / The sample article HTML."""
    return (FIXTURES / "sample_article.html").read_text(encoding="utf-8")


# --- 正文 / article body ------------------------------------------------------


def test_extracts_body_text(html: str) -> None:
    """抽出正文 / The body text is extracted."""
    article = extract_article(html, PAGE_URL)

    assert article.extraction_ok is True
    assert "多模态大模型" in article.text
    assert "推理成本" in article.text


def test_body_excludes_chrome(html: str) -> None:
    """
    正文里**不应包含导航、侧栏、页脚**。
    Navigation, sidebar and footer must not leak into the body.

    去模板是抽取层的核心价值：这些噪声会污染后续的摘要提示词，
    让模型把「相关推荐」当成新闻内容。
    Boilerplate removal is the point of this layer: the noise would pollute the
    summarisation prompt and lead the model to treat "related articles" as content.
    """
    text = extract_article(html, PAGE_URL).text

    assert "相关推荐" not in text
    assert "版权所有" not in text
    assert "关于我们" not in text


def test_og_title_preferred_over_title_tag(html: str) -> None:
    """
    og:title 优先于 <title> / og:title wins over <title>.

    <title> 是「某公司发布新一代多模态大模型_科技前线」，带站点名后缀；
    og:title 是站点为分享场景准备的干净标题。
    """
    article = extract_article(html, PAGE_URL)

    assert article.title == "某公司发布新一代多模态大模型"
    assert "_科技前线" not in article.title


def test_author_extracted(html: str) -> None:
    """抽出作者 / The author is extracted."""
    assert extract_article(html, PAGE_URL).author == "张三"


def test_published_time_extracted(html: str) -> None:
    """抽出 ISO 8601 发布时间 / The ISO 8601 publication time is extracted."""
    assert extract_article(html, PAGE_URL).published_at == datetime(2026, 9, 1, 9, 30)


def test_url_is_preserved(html: str) -> None:
    """原文链接原样保留 / The source URL is preserved."""
    assert extract_article(html, PAGE_URL).url == PAGE_URL


# --- 降级 / graceful degradation ----------------------------------------------


def test_degrades_instead_of_raising() -> None:
    """
    **抽不到正文时降级，而不是抛异常。**
    A body that cannot be extracted degrades instead of raising.

    每条日报都对应一个链接；抽不到正文时摘要会变弱，但标题+链接本身仍有价值——
    读者点进去就能看。因为一个技术问题丢掉一条真实资讯是不划算的。
    Every entry corresponds to a link. Without a body the summary is weaker, but the
    title and link remain useful, so discarding real news over a technical failure
    would be the wrong trade.
    """
    article = extract_article("<html><body><p>短</p></body></html>", PAGE_URL)

    assert article.extraction_ok is False
    assert article.url == PAGE_URL  # 链接仍在


def test_fallback_title_used_when_page_has_none() -> None:
    """页面无标题时用 RSS 的标题兜底 / The RSS title is used when the page has none."""
    article = extract_article("<html><body>x</body></html>", PAGE_URL, fallback_title="来自RSS的标题")
    assert article.title == "来自RSS的标题"


def test_degraded_extraction_keeps_rss_title_over_page_title() -> None:
    """
    **抽取降级时保留 RSS 标题，不采信页面标题。**
    On a degraded extraction the RSS title wins over the page title.

    真机实测（oschina）：SPA 站点服务端只返回外壳页，`og:title` 是站点通用名
    「OSCHINA - 开源 × AI · 开发者生态社区」，正文为空。若采信页面标题，就会用
    一个毫无信息量的站点名**覆盖掉 RSS 里本来正确的标题**——抽取反而让数据变差，
    而且这个标题还会成为落盘目录名。
    Verified live on oschina: the SPA serves only a shell page whose og:title is the
    site's generic name, with no body. Trusting it replaces a perfectly good RSS title
    with a meaningless site name, and that title also becomes the storage folder name.

    见 docs/issues/004
    """
    shell_page = """<html><head>
        <meta property="og:title" content="OSCHINA - 开源 × AI · 开发者生态社区">
      </head><body><div id="app"></div></body></html>"""

    article = extract_article(shell_page, PAGE_URL, fallback_title="某公司开源了新的推理框架")

    assert article.extraction_ok is False
    assert article.title == "某公司开源了新的推理框架"
    assert "OSCHINA" not in article.title


def test_successful_extraction_still_prefers_page_title(html: str) -> None:
    """
    抽取成功时仍以页面标题为准 / A successful extraction still prefers the page title.

    页面标题通常比 RSS 标题更完整（RSS 常截断），只有在降级时才不可信。
    Page titles are usually more complete than RSS ones, which are often truncated;
    they are only untrustworthy when extraction degrades.
    """
    article = extract_article(html, PAGE_URL, fallback_title="RSS 里的简短标题")

    assert article.extraction_ok is True
    assert article.title == "某公司发布新一代多模态大模型"


def test_no_title_anywhere() -> None:
    """标题彻底缺失时给占位值，不留空 / A placeholder is used when no title exists."""
    assert extract_article("<html><body>x</body></html>", PAGE_URL).title == "(无标题)"


@pytest.mark.parametrize(
    "bad_html",
    ["", "<html><body>", "<<<>>>", "<html><p>未闭合的标签<div></html>"],
)
def test_malformed_html_does_not_crash(bad_html: str) -> None:
    """
    畸形 HTML 不能让流水线崩 / Malformed HTML must not crash the pipeline.

    真实网页什么样的都有，抓到一个坏页面不应中断当天的日报。
    """
    article = extract_article(bad_html, PAGE_URL)
    assert article.url == PAGE_URL


# --- 媒体：图片 / media: images -----------------------------------------------


def test_og_image_comes_first(html: str) -> None:
    """
    og:image 排在最前 / og:image comes first.

    它是站点自己挑的分享图，几乎总是最合适的封面。
    """
    images = [a for a in extract_media(html, PAGE_URL) if a.kind is MediaKind.IMAGE]
    assert images[0].url == "https://cdn.example.com/cover.jpg"


def test_lazy_loaded_image_uses_data_src(html: str) -> None:
    """
    **懒加载图片必须取 data-src，而不是占位的 src。**
    Lazy-loaded images must use data-src, not the placeholder in src.

    不处理的话，抓到的全是同一张 loading 占位图，日报里每条配图都一样。
    Otherwise every extracted image is the same loading placeholder and every entry
    in the digest carries an identical picture.
    """
    urls = [a.url for a in extract_media(html, PAGE_URL)]

    assert "https://cdn.example.com/figure1.jpg" in urls
    assert not any("placeholder" in u for u in urls)


def test_protocol_relative_url_absolutised(html: str) -> None:
    """协议相对地址（//cdn...）应补全 / Protocol-relative addresses are resolved."""
    urls = [a.url for a in extract_media(html, PAGE_URL)]
    assert "https://cdn.example.com/chart.png" in urls


def test_logo_and_icons_filtered(html: str) -> None:
    """
    logo 与图标不应被当作配图 / Logos and icons must not become content images.

    它们出现在每一页上，配进日报既难看又无意义。
    """
    urls = [a.url for a in extract_media(html, PAGE_URL)]

    assert not any("logo" in u.lower() for u in urls)
    assert not any("share-icon" in u.lower() for u in urls)


def test_og_image_is_filtered_when_it_is_a_logo() -> None:
    """
    **og:image 是站点 logo 时必须过滤掉。**
    An og:image that is really the site logo must be filtered out.

    实测发现（量子位）：不少站点没有为每篇文章单独配分享图，og:image 直接写死成
    自家 logo。不过滤的话，logo 会作为第一张图成为日报封面，而且**每条都一样**。
    Found on real data (量子位): many sites do not author a per-article share image and
    hard-code their logo into og:image. Unfiltered, that logo becomes the cover image
    for every single entry in the digest.

    见 docs/issues/003-p2-live-verification-findings.md
    """
    html = """
      <meta property="og:image" content="https://www.qbitai.com/uploads/qbitai-logo-1.png">
      <img src="https://i.qbitai.com/uploads/2026/09/real-photo.webp" width="800" height="600">
    """
    urls = [a.url for a in extract_media(html, PAGE_URL)]

    assert not any("logo" in u for u in urls)
    assert urls[0].endswith("real-photo.webp")  # 真实配图顶上来当封面


def test_site_header_image_filtered() -> None:
    """
    站点头图（`head.jpg`）必须过滤 / A site header image must be filtered.

    实测发现：量子位的模板头图路径是 `/imagesnew/head.jpg`。
    这类文件名要靠**按词匹配**才能命中——纯子串匹配的标记表里只有 "header"，会漏掉。
    """
    html = '<img src="http://www.qbitai.com/themes/imagesnew/head.jpg" width="800" height="300">'
    assert extract_media(html, PAGE_URL) == []


@pytest.mark.parametrize(
    "filename",
    ["overhead-view.jpg", "headline-photo.png", "iconic-moment.jpg", "logout-flow.png"],
)
def test_marker_matching_does_not_over_reject(filename: str) -> None:
    """
    **按词匹配不能误伤真实配图。**
    Word matching must not reject genuine content images.

    `overhead-view.jpg` 含 "head"、`iconic-moment.jpg` 含 "icon"，
    但它们都是内容图。若用纯子串匹配，这些图会被无声丢掉。
    These filenames contain "head" / "icon" / "logo" as substrings yet are genuine
    content images; bare substring matching would silently discard them.
    """
    html = f'<img src="https://cdn.example.com/{filename}" width="800" height="600">'
    assert len(extract_media(html, PAGE_URL)) == 1


def test_marker_matching_ignores_host() -> None:
    """
    只看路径不看域名 / Only the path is examined, never the host.

    图床域名里含 "logo" 之类的词很常见，据此判断会把整个图床的图全部误杀。
    """
    html = '<img src="https://img.logo-cdn.com/2026/photo.jpg" width="800" height="600">'
    assert len(extract_media(html, PAGE_URL)) == 1


def test_tracking_pixel_filtered(html: str) -> None:
    """
    1x1 追踪像素必须过滤 / 1x1 tracking pixels must be filtered.

    它们是图片标签，但显然不是配图。
    """
    urls = [a.url for a in extract_media(html, PAGE_URL)]
    assert not any("pixel" in u.lower() for u in urls)


def test_figcaption_becomes_caption(html: str) -> None:
    """figcaption 作为图片说明 / A figcaption becomes the image caption."""
    images = [a for a in extract_media(html, PAGE_URL) if a.kind is MediaKind.IMAGE]
    captions = [a.caption for a in images if a.caption]
    assert "模型架构示意图" in captions


def test_alt_text_becomes_caption(html: str) -> None:
    """alt 文本作为说明 / Alt text becomes the caption."""
    images = [a for a in extract_media(html, PAGE_URL) if a.kind is MediaKind.IMAGE]
    chart = next(a for a in images if "chart.png" in a.url)
    assert chart.caption == "性能对比图"


def test_max_images_respected(html: str) -> None:
    """图片数量上限生效 / The image cap is applied."""
    images = [a for a in extract_media(html, PAGE_URL, max_images=1) if a.kind is MediaKind.IMAGE]
    assert len(images) == 1


def test_images_deduplicated() -> None:
    """同一地址只保留一次 / The same address appears once."""
    html = '<img src="https://c.com/a.jpg" width="800"><img src="https://c.com/a.jpg" width="800">'
    images = [a for a in extract_media(html, PAGE_URL) if a.kind is MediaKind.IMAGE]
    assert len(images) == 1


def test_data_uri_images_skipped() -> None:
    """内联 data: 图片跳过 / Inline data: images are skipped."""
    html = '<img src="data:image/png;base64,iVBORw0KG" width="800" height="600">'
    assert extract_media(html, PAGE_URL) == []


# --- 媒体：视频 / media: videos -----------------------------------------------


def test_bilibili_iframe_recognised_as_video(html: str) -> None:
    """
    已知视频站的 iframe 识别为官方视频 / An iframe from a known video host is a video.

    视频版场景要尽量拿到官方片段，这是最可靠的来源之一。
    """
    videos = [a for a in extract_media(html, PAGE_URL) if a.kind is MediaKind.VIDEO]

    assert len(videos) == 1
    assert "bilibili.com" in videos[0].url


def test_unknown_iframe_not_treated_as_video() -> None:
    """
    非视频站的 iframe 不应误判 / An iframe from an unknown host is not a video.

    广告位和评论组件也是 iframe，收进来会污染视频素材。
    """
    html = '<iframe src="https://ads.example.com/banner"></iframe>'
    assert [a for a in extract_media(html, PAGE_URL) if a.kind is MediaKind.VIDEO] == []


def test_video_tag_source_extracted() -> None:
    """<video><source> 应被识别 / A <video><source> element is recognised."""
    html = '<video><source src="https://c.com/clip.mp4" type="video/mp4"></video>'
    videos = [a for a in extract_media(html, PAGE_URL) if a.kind is MediaKind.VIDEO]
    assert videos[0].url == "https://c.com/clip.mp4"


# --- 出处必须可追溯 / attribution is mandatory --------------------------------


def test_every_asset_carries_source_url(html: str) -> None:
    """
    **每个资产都必须带来源** / Every asset must carry its source.

    产物要发到公众号、小红书等公开平台，每张图都要能追溯出处并写进
    `_references.md`。抽取时不带上，事后就补不回来了。
    Output goes to public platforms, so every image must be traceable and land in
    `_references.md`. If it is not captured at extraction time it cannot be
    reconstructed later.
    """
    assets = extract_media(html, PAGE_URL)

    assert assets  # 确保样例确实产出了资产
    assert all(a.source_url == PAGE_URL for a in assets)
    assert all(a.credit == "news.example.com" for a in assets)


def test_article_carries_media(html: str) -> None:
    """抽取的文章应带上媒体资产 / The extracted article carries its media."""
    article = extract_article(html, PAGE_URL)
    assert len(article.media) > 0
    assert all(a.source_url == PAGE_URL for a in article.media)


# --- 辅助 / helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://c.com/a.jpg", True),
        ("https://c.com/a.PNG", True),
        ("https://c.com/a.webp?v=2", True),
        ("https://c.com/a.mp4", False),
        ("https://c.com/page", False),
    ],
)
def test_looks_like_image_url(url: str, expected: bool) -> None:
    """URL 是否指向图片，供下载阶段二次校验 / Image URL check for the download stage."""
    assert looks_like_image_url(url) is expected


# --- 按属性过滤装饰图 / chrome detected from HTML attributes -------------------


def test_wechat_author_avatar_is_filtered_by_attributes() -> None:
    """
    微信的作者头像必须被剔掉。

    它的地址是 CDN 随机串（`mmbiz_png/q4wL2iaHZfGk…`），路径上没有任何特征，
    只看路径的过滤器完全使不上劲。但 class 与 alt 很老实。
    不过滤的话每篇微信文章都会白白存进一张作者头像当素材。
    """
    html = """
    <html><body><article>
      <img class="rich_pages wxw-img" data-src="https://mmbiz.qpic.cn/sz_mmbiz_png/AAAA/640" />
      <img class="jump_author_avatar" src="http://mmbiz.qpic.cn/mmbiz_png/BBBB/0" alt="作者头像" />
      <img class="jump_wx_qrcode_img js_qrcode_img" src="http://mmbiz.qpic.cn/CCCC/0" alt="跳转二维码" />
    </article></body></html>
    """
    urls = [a.url for a in extract_media(html, "https://mp.weixin.qq.com/s/XXXX")]

    assert any("AAAA" in u for u in urls), "正文配图不该被误杀"
    assert not any("BBBB" in u for u in urls), "作者头像应被过滤"
    assert not any("CCCC" in u for u in urls), "二维码应被过滤"


def test_attribute_filter_does_not_kill_normal_images() -> None:
    """
    属性过滤不能误伤正常配图。

    子串匹配比路径上的词边界匹配宽松，必须确认它不会因为 class 里含 `icon` 之类的
    子串就把正文大图丢掉——所以标记只挑那些不会出现在内容图 class 里的词。
    """
    html = """
    <html><body><article>
      <img class="article-image lazyload" data-src="https://cdn.example.com/photo.jpg" />
      <img class="content-figure" src="https://cdn.example.com/chart.png" alt="性能对比图" />
    </article></body></html>
    """
    urls = [a.url for a in extract_media(html, "https://example.com/post")]

    assert any("photo.jpg" in u for u in urls)
    assert any("chart.png" in u for u in urls)


def test_arxiv_funder_logo_is_filtered() -> None:
    """
    arXiv 页脚的基金会 logo 不该成为文章配图。

    实测抓 arXiv 论文时，落盘的唯一「配图」是
    `arxiv.org/static/base/1.0.1/images/funders/simons-foundation.png`——
    因为 arXiv 摘要页本身没有任何配图，这个赞助方标识就顶上了。
    """
    html = """
    <html><body><article>
      <img src="https://arxiv.org/static/base/1.0.1/images/funders/simons-foundation.png" />
      <img src="https://arxiv.org/static/sponsors/acme-corp.png" />
    </article></body></html>
    """
    assert extract_media(html, "https://arxiv.org/abs/2509.00001") == []


def test_plural_chrome_directories_are_filtered() -> None:
    """
    装饰性资源多半挂在**复数目录**下，词边界规则必须认复数形式。

    实测 20260902 那一期的 arXiv 条目，5 张「配图」全是页脚装饰，其中 4 张走的是
    `/images/icons/social/...`——`icon` 在词表里，但边界规则要求其后是非字母数字，
    于是 `icons` 整个漏过去了。这一条由 P4 的 `_references.md` 暴露出来：
    把每张图的原始地址平铺出来之后，垃圾资源一眼可见。
    Chrome assets usually sit under plural directory names, so the word-boundary rule
    must accept them. Surfaced by P4's reference roll-up, which lists every image URL.
    """
    html = """
    <html><body><article>
      <img src="https://arxiv.org/static/browse/0.3.4/images/icons/social/reddit.png" />
      <img src="https://arxiv.org/static/browse/0.3.4/images/icons/social/bluesky.png" />
      <img src="https://cdn.example.com/assets/logos/brand.png" />
      <img src="https://cdn.example.com/img/banners/top.jpg" />
    </article></body></html>
    """
    assert extract_media(html, "https://arxiv.org/abs/2509.00001") == []


def test_sponsor_markers_do_not_hit_normal_filenames() -> None:
    """
    新增的赞助方标记不能误伤正常配图。

    `partnership-diagram.png` 是内容图——词边界匹配保证 `partner` 不会命中
    `partnership`，这正是当初选词边界而非子串的理由。
    """
    html = """
    <html><body><article>
      <img src="https://cdn.example.com/partnership-diagram.png" />
      <img src="https://cdn.example.com/sponsorship-model-chart.jpg" />
    </article></body></html>
    """
    urls = [a.url for a in extract_media(html, "https://example.com/post")]

    assert any("partnership-diagram" in u for u in urls)
    assert any("sponsorship-model-chart" in u for u in urls)


def test_wechat_article_videos_are_found() -> None:
    """
    **微信公众号的视频要认出来。**

    微信不给 og:video、没有 `<video>`，iframe 的 `data-src` 指向一个 yt-dlp 解析不了
    的模板页；真正的 mp4 直链藏在页面里的一段 JS 里，`&` 还被转义成 `\\x26amp;`。
    实测那篇 MiniMax H3 的文章：正文和配图都抓到了，视频一个没有，
    而「一个都没识别到」和「这篇没有视频」在界面上长得完全一样。

    同一条视频有多种码率，只留一条，且**优先 H.264**（f10004/f10002）——
    HEVC 在剪辑软件里的支持差得多，而这些素材是要拿去剪片子的。
    """
    from dna.core.models import MediaKind
    from dna.extract.media import extract_media

    clip = "0bc3dacgsaaeueaiy43pvvvfiggdnemai2ia"
    html = (
        '<html><body><div id="js_content">'
        '<iframe class="video_iframe" data-mpvid="wxv_4628226333980491779"'
        ' data-src="https://mp.weixin.qq.com/mp/readtemplate?t=pages/video_player_tmpl'
        '&amp;action=mpvideo&amp;vid=wxv_4628226333980491779"></iframe>'
        "</div><script>window.__videoinfo = {"
        f'"url": "http://mpvideo.qpic.cn/{clip}.f10002.mp4?dis_k=aaa\\x26amp;auth_info=S1",'
        f'"hd": "http://mpvideo.qpic.cn/{clip}.f10004.mp4?dis_k=bbb\\x26amp;auth_info=S2",'
        f'"hevc": "http://mpvideo.qpic.cn/{clip}.f10104.mp4?dis_k=ccc\\x26amp;auth_info=S3"'
        "};</script></body></html>"
    )

    videos = [
        a for a in extract_media(html, "https://mp.weixin.qq.com/s/XhU4W02")
        if a.kind is MediaKind.VIDEO
    ]

    assert len(videos) == 1, "同一条视频的多种码率只该留一条"
    assert ".f10004.mp4" in videos[0].url, "该优先 H.264 高清那一路"
    # 反转义必须发生在匹配之前：签名丢了的话下载回来是一个错误页
    assert "auth_info=S2" in videos[0].url
    assert "\\x26" not in videos[0].url
    assert videos[0].source_url.startswith("https://mp.weixin.qq.com/s/")


def test_a_wechat_video_url_counts_as_a_direct_download() -> None:
    """带签名查询串的 mp4 仍然是直链——走 HTTP 下载，不必启动 yt-dlp。"""
    from dna.store.video_store import is_direct_video_url

    assert is_direct_video_url(
        "http://mpvideo.qpic.cn/abc.f10004.mp4?dis_k=x&auth_info=y"
    )
