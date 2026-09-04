"""
配图与官方视频抽取 / Image and official-video extraction.

图文版每条要配 1~3 张图，视频版要尽量拿到官方视频片段。本模块负责从页面里
找出这些资产，并**强制记录来源**。
The graphic app needs one to three images per entry and the video app wants official
clips where available. This module finds those assets and always records where each
came from.

来源必须记录 / Attribution is mandatory:
    产物要发到公众号、小红书等公开平台，每张图都必须能追溯到原文出处，
    最终写进 `_references.md`。因此 MediaAsset.source_url 是必填字段，
    抽取时一并带上，而不是事后补——事后就补不回来了。
    Output is published to public platforms, so every image must be traceable to the
    article it came from and ends up in `_references.md`. MediaAsset.source_url is
    therefore required and captured at extraction time; it cannot be reconstructed
    later.

纯函数，无网络请求（下载图片是 P4 落盘阶段的事）。
Pure functions with no network access; downloading happens at the P4 output stage.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from dna.core.logging import get_logger
from dna.core.models import MediaAsset, MediaKind
from dna.core.urls import host_of

logger = get_logger("extract.media")

# 每篇最多留几张配图 / images kept per article
# 日报只用 1~3 张，但多存的那些是素材库：做长图、口播配图、视频封面时可以挑，
# 而重抓一遍拿不回当初那些图（站点会换图、删图）。存储成本远低于错过素材。
# The digest itself uses one to three, but the extras are a material library for long
# images, voice-over stills and video covers. Re-fetching later cannot recover them —
# sites swap and delete images — and storage is far cheaper than missing the material.
DEFAULT_MAX_IMAGES = 10

# 尺寸下限：低于此宽高的多半是图标、头像或追踪像素
# Minimum dimensions: anything smaller is usually an icon, avatar or tracking pixel.
MIN_IMAGE_WIDTH = 200
MIN_IMAGE_HEIGHT = 120

# 文件名里出现这些词的基本不是配图 / filename markers that indicate chrome, not content
_NON_CONTENT_MARKERS = (
    "logo", "icon", "avatar", "sprite", "placeholder", "blank", "spacer",
    "pixel", "tracking", "beacon", "button", "badge", "qrcode", "qr-code",
    "footer", "header", "head", "banner", "watermark", "emoji", "loading",
    "default", "thumb-default", "nopic",
    # 赞助方/合作方标识：arXiv 的页脚挂着 funders/simons-foundation.png，
    # 而 arXiv 摘要页本身没有任何配图，于是这个基金会 logo 成了整条目的「配图」。
    # Sponsor and partner marks: arXiv's footer carries funders/simons-foundation.png,
    # and since an arXiv abstract page has no images of its own that logo ended up
    # standing in as the entry's illustration.
    "funder", "funders", "sponsor", "sponsors", "partner", "partners", "affiliate",
)

# 按「词」而非子串匹配 / match markers as words, not bare substrings
#
# 子串匹配会误伤真实配图：`overhead-view.jpg`、`headline-photo.png` 都含 "head"，
# 但它们是内容图。以非字母数字为边界，既能命中 `qbitai-logo-1.png` 与 `head.jpg`，
# 又不会波及上述文件名。
# Substring matching would wrongly reject genuine images: `overhead-view.jpg` and
# `headline-photo.png` both contain "head" yet are content. Anchoring on
# non-alphanumeric boundaries catches `qbitai-logo-1.png` and `head.jpg` while
# leaving those alone.
_MARKER_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:" + "|".join(_NON_CONTENT_MARKERS) + r")(?:[^a-z0-9]|$)",
    re.IGNORECASE,
)

# class / alt / id 里出现即判定为装饰图 / markers that mark chrome in HTML attributes
# 这里用子串匹配而非词边界：class 名本来就是拼接的（`jump_author_avatar`），
# 词边界会一个都匹配不上。
# Substring matching is used here rather than word boundaries: class names are compound
# by nature (`jump_author_avatar`), and word boundaries would match none of them.
_ATTRIBUTE_CHROME_MARKERS = (
    "avatar", "头像", "qrcode", "qr_code", "二维码", "logo",
    "icon", "emoji", "watermark", "水印", "sponsor", "advert",
)

# 视频站点，出现在 iframe 或链接里视为官方视频
# Video hosts; when they appear in an iframe or link the target counts as an official clip.
_VIDEO_HOSTS = (
    "youtube.com", "youtu.be", "vimeo.com", "bilibili.com", "player.bilibili.com",
    "v.qq.com", "youku.com", "ixigua.com", "douyin.com",
)

_IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif|avif)(\?|$)", re.IGNORECASE)


def extract_media(
    html: str,
    url: str,
    *,
    soup: BeautifulSoup | None = None,
    max_images: int = DEFAULT_MAX_IMAGES,
) -> list[MediaAsset]:
    """
    抽出页面的配图与官方视频 / Extract images and official videos from a page.

    图片按优先级排序 / Images are ordered by priority:
        1. og:image / twitter:image —— 站点自己指定的分享图，几乎总是最合适的封面
        2. 正文里的 <img> —— 按出现顺序，过滤掉图标与过小的图

    返回列表中图片在前、视频在后。
    Images come first in the returned list, followed by videos.
    """
    page = soup or BeautifulSoup(html or "", "html.parser")

    images = _dedupe(
        [*_social_images(page, url), *_content_images(page, url)],
        limit=max_images,
    )
    videos = _dedupe(_videos(page, url), limit=3)

    return [*images, *videos]


# ---------------------------------------------------------------------------
# 图片 / images
# ---------------------------------------------------------------------------


def _social_images(soup: BeautifulSoup, page_url: str) -> list[MediaAsset]:
    """
    取站点声明的分享图 / The share image the site declares for itself.

    通常是最可靠的封面来源：站点为了在社交平台上好看，会主动挑一张有代表性的图。
    Usually the most reliable cover source: sites pick a representative image so their
    links look good when shared.

    ⚠️ 但**必须同样过滤图标**：不少站点没有为每篇文章单独配分享图，og:image 直接
    写死成自家 logo（实测量子位就是如此）。不过滤的话，logo 会作为第一张图成为
    日报的封面，而且每条都一样。
    However the chrome filter still applies: many sites do not author a per-article
    share image and hard-code their own logo into og:image (量子位 does exactly this).
    Without filtering, the logo becomes the first image and therefore the digest cover
    — identically for every entry.
    """
    assets: list[MediaAsset] = []
    for prop in ("og:image", "og:image:url", "twitter:image", "twitter:image:src"):
        for tag in soup.find_all("meta", attrs={"property": prop}) + soup.find_all(
            "meta", attrs={"name": prop}
        ):
            src = (tag.get("content") or "").strip()
            if src and not _looks_like_chrome(src):
                assets.append(_image_asset(src, page_url))
    return [a for a in assets if a is not None]  # type: ignore[misc]


def _content_images(soup: BeautifulSoup, page_url: str) -> list[MediaAsset]:
    """
    取正文里的图片 / Images from the article body.

    懒加载的站点把真实地址放在 data-src / data-original 里，`src` 只是占位图，
    因此这些属性要一并检查——否则抓到的全是同一张 loading 占位图。
    Lazy-loading sites keep the real address in data-src or data-original while `src`
    holds a placeholder, so those attributes must be checked too; otherwise every
    image extracted is the same loading placeholder.
    """
    assets: list[MediaAsset] = []

    for img in soup.find_all("img"):
        src = _image_src(img)
        if not src:
            continue
        if _looks_like_chrome(src) or _chrome_by_attributes(img):
            continue
        if _too_small(img):
            continue

        asset = _image_asset(src, page_url, caption=_caption_of(img))
        if asset is not None:
            assets.append(asset)

    return assets


def _image_src(img: Tag) -> str:
    """按优先级取图片地址，兼容懒加载 / Image address, honouring lazy-loading attributes."""
    for attr in ("data-src", "data-original", "data-actualsrc", "data-lazy-src", "src"):
        value = (img.get(attr) or "").strip()
        if value and not value.startswith("data:"):
            return value

    # srcset 形如 "a.jpg 1x, b.jpg 2x"，取第一个候选
    srcset = (img.get("srcset") or "").strip()
    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        if first and not first.startswith("data:"):
            return first

    return ""


def _looks_like_chrome(src: str) -> bool:
    """
    是否是图标 / 装饰图而非内容配图 / Whether this is chrome rather than content.

    只看路径部分，不看域名：图床域名里出现 `img.logo-cdn.com` 之类的词很常见，
    据此判断会把整个图床的图片全部误杀。
    Only the path is examined, never the host: image CDNs are routinely named things
    like `img.logo-cdn.com`, and judging by host would reject every image served from
    such a domain.
    """
    lowered = src.lower()
    if lowered.endswith(".svg"):  # 站点图标几乎都是 svg
        return True

    path = urlsplit(lowered).path or lowered
    return bool(_MARKER_RE.search(path))


def _chrome_by_attributes(img: Tag) -> bool:
    """
    按 class / alt 判断是不是装饰图 / Detect chrome from the class and alt attributes.

    微信、知乎这类站点的图片地址是 CDN 随机串（`mmbiz_png/q4wL2iaHZfGk…`），
    路径上没有任何特征，`_looks_like_chrome` 完全使不上劲。但它们的 HTML 属性
    很老实：作者头像带 `jump_author_avatar` 与 `alt="作者头像"`，二维码带
    `js_qrcode_img`。不看属性的话，每篇微信文章都会白白存进一张作者头像。
    On sites like WeChat and Zhihu the image address is an opaque CDN string carrying no
    signal, leaving `_looks_like_chrome` nothing to work with. Their HTML attributes are
    honest though — an author avatar declares `jump_author_avatar` and `alt="作者头像"`,
    a QR code declares `js_qrcode_img` — and without checking them every WeChat article
    would store the author's avatar as if it were content.
    """
    haystack = " ".join(
        [
            " ".join(img.get("class") or []),
            img.get("alt") or "",
            img.get("id") or "",
        ]
    ).lower()

    return any(marker in haystack for marker in _ATTRIBUTE_CHROME_MARKERS)


def _too_small(img: Tag) -> bool:
    """
    按 width/height 属性判断是否过小 / Whether the declared dimensions are too small.

    只在属性明确给出时判断；缺属性时放行，交给后续下载阶段按真实尺寸再过滤。
    Only applied when the attributes are present; missing dimensions pass through and
    are filtered by real size at download time.
    """
    for attr, minimum in (("width", MIN_IMAGE_WIDTH), ("height", MIN_IMAGE_HEIGHT)):
        raw = (img.get(attr) or "").strip().rstrip("px")
        if raw.isdigit() and int(raw) < minimum:
            return True
    return False


def _caption_of(img: Tag) -> str | None:
    """取图片说明 / The image caption, from alt text or an enclosing <figcaption>."""
    alt = (img.get("alt") or "").strip()
    if alt and not _looks_like_chrome(alt):
        return " ".join(alt.split())[:200]

    figure = img.find_parent("figure")
    if figure:
        caption = figure.find("figcaption")
        if caption:
            return " ".join(caption.get_text(" ", strip=True).split())[:200]
    return None


def _image_asset(src: str, page_url: str, caption: str | None = None) -> MediaAsset | None:
    """
    构造图片资产 / Build an image asset.

    相对地址按原文链接补全；来源与署名一并写入。
    Relative addresses are resolved against the article URL, and attribution is
    recorded at the same time.
    """
    absolute = _absolutise(src, page_url)
    if not absolute:
        return None

    return MediaAsset(
        kind=MediaKind.IMAGE,
        url=absolute,
        source_url=page_url,
        caption=caption,
        credit=host_of(page_url) or None,
    )


# ---------------------------------------------------------------------------
# 视频 / videos
# ---------------------------------------------------------------------------


def _videos(soup: BeautifulSoup, page_url: str) -> list[MediaAsset]:
    """
    取官方视频 / Official video clips.

    只收三类可靠来源，不去猜 / Only three reliable sources are collected:
        1. og:video —— 站点声明的视频
        2. <video><source> —— 页面内嵌的视频文件
        3. 指向已知视频站的 <iframe> —— 官方嵌入的播放器
    """
    assets: list[MediaAsset] = []

    for prop in ("og:video", "og:video:url", "og:video:secure_url"):
        for tag in soup.find_all("meta", attrs={"property": prop}):
            src = (tag.get("content") or "").strip()
            if src:
                assets.append(_video_asset(src, page_url))

    for video in soup.find_all("video"):
        src = (video.get("src") or "").strip()
        if src:
            assets.append(_video_asset(src, page_url))
        for source in video.find_all("source"):
            source_src = (source.get("src") or "").strip()
            if source_src:
                assets.append(_video_asset(source_src, page_url))

    for iframe in soup.find_all("iframe"):
        src = (iframe.get("src") or iframe.get("data-src") or "").strip()
        if src and any(host in src.lower() for host in _VIDEO_HOSTS):
            assets.append(_video_asset(src, page_url))

    return [a for a in assets if a is not None]  # type: ignore[misc]


def _video_asset(src: str, page_url: str) -> MediaAsset | None:
    """构造视频资产 / Build a video asset."""
    absolute = _absolutise(src, page_url)
    if not absolute:
        return None
    return MediaAsset(
        kind=MediaKind.VIDEO,
        url=absolute,
        source_url=page_url,
        credit=host_of(page_url) or None,
    )


# ---------------------------------------------------------------------------
# 共用 / shared
# ---------------------------------------------------------------------------


def _absolutise(src: str, page_url: str) -> str:
    """
    把相对地址补成绝对地址 / Resolve a relative address against the page URL.

    协议相对地址（`//img.example.com/a.jpg`）也要处理——直接用会缺 scheme。
    Protocol-relative addresses must be handled too; used as-is they lack a scheme.
    """
    src = src.strip()
    if not src or src.startswith("data:"):
        return ""
    try:
        return urljoin(page_url, src)
    except ValueError:
        return ""


def _dedupe(assets: list[MediaAsset], *, limit: int) -> list[MediaAsset]:
    """按 URL 去重并截断，保持原有顺序 / De-duplicate by URL, keep order, apply the cap."""
    seen: set[str] = set()
    kept: list[MediaAsset] = []
    for asset in assets:
        if asset.url in seen:
            continue
        seen.add(asset.url)
        kept.append(asset)
        if len(kept) >= limit:
            break
    return kept


def looks_like_image_url(url: str) -> bool:
    """
    URL 是否指向图片文件 / Whether a URL points at an image file.

    供 P4 下载阶段做二次校验。
    Used by the P4 download stage as a secondary check.
    """
    return bool(_IMAGE_EXT_RE.search(url or ""))


__all__ = [
    "DEFAULT_MAX_IMAGES",
    "MIN_IMAGE_HEIGHT",
    "MIN_IMAGE_WIDTH",
    "extract_media",
    "looks_like_image_url",
]
