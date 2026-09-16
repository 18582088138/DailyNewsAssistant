"""
正文抽取 / Article body extraction.

主用 trafilatura（去模板效果最好），失败时退回 BeautifulSoup 的启发式抽取，
再失败则**降级为「仅标题 + 链接」**而不是抛异常。
trafilatura does the heavy lifting (best-in-class boilerplate removal), with a
BeautifulSoup heuristic as fallback, and a final degradation to "title and link
only" rather than raising.

为什么失败要降级而不是报错 / Why degrade instead of failing:
    日报的每一条都对应一个链接。抽不到正文时，摘要质量会下降，但**标题 + 链接
    本身仍然是有价值的**——读者点进去就能看。直接丢弃等于因为一个技术问题
    砍掉一条真实资讯。
    Every digest entry corresponds to a link. When the body cannot be extracted the
    summary gets weaker, but the title and link remain useful — the reader can just
    click through. Dropping the item would discard real news over a technical issue.

抓取与解析分离：`extract_article()` 只吃 HTML 字符串，可用本地样例离线测试。
Fetching and parsing are separate: `extract_article()` takes an HTML string and is
testable offline against local samples.
"""

from __future__ import annotations

from datetime import datetime

import trafilatura
from bs4 import BeautifulSoup

from dna.core.logging import get_logger
from dna.core.models import Article
from dna.core.urls import title_from_url
from dna.extract.media import extract_media
from dna.sources.http import fetch_text, is_local_url

logger = get_logger("extract.article")

# 正文短于此长度视为抽取失败 / a body shorter than this counts as a failed extraction
MIN_EXTRACTED_CHARS = 80


def extract_article(
    html: str, url: str, *, fallback_title: str = "", max_images: int | None = None
) -> Article:
    """
    从 HTML 抽出文章 / Extract an article from HTML.

    纯函数，不发网络请求 / A pure function; performs no network access.

    参数 / Args:
        html:           页面 HTML
        url:            原文链接，用于补全相对图片地址与来源署名
        fallback_title: 抽不到标题时用的兜底（通常来自 RSS 的标题）
        max_images:     配图上限。**必须一路传到这里**：不传的话抽取阶段先按
            `media.DEFAULT_MAX_IMAGES` 砍一刀，后面无论 `profile.yaml` 写多少，
            候选池里已经只剩那么几张了 —— 配置写了 100 却只存到 10，
            而且完全静默（见 issues/014）。

    返回 / Returns:
        Article；`extraction_ok=False` 表示降级为「仅标题 + 链接」
    """
    soup = _soup(html)

    text = _extract_body(html, soup)
    media = (
        extract_media(html, url, soup=soup)
        if max_images is None
        else extract_media(html, url, soup=soup, max_images=max_images)
    )

    ok = len(text) >= MIN_EXTRACTED_CHARS
    if not ok:
        logger.debug("正文抽取降级（%d 字符）：%s", len(text), url)

    title = _choose_title(soup, fallback_title, extraction_ok=ok)

    return Article(
        url=url,
        title=title or "(无标题)",
        text=text,
        author=_extract_author(soup),
        published_at=_extract_published(soup),
        media=media,
        extraction_ok=ok,
    )


def fetch_article(
    url: str,
    *,
    fallback_title: str = "",
    timeout: float = 20.0,
    max_images: int | None = None,
) -> Article:
    """
    抓取并抽取一篇文章 / Fetch a URL and extract the article.

    网络失败时同样降级返回，而不是抛异常——理由见模块文档。
    Network failures also degrade rather than raise; see the module docstring.

    `max_images` 要一路传下去，否则配置里的上限会在抽取阶段就被砍掉（issues/014）。
    """
    try:
        html = fetch_text(url, timeout=timeout, local=is_local_url(url))
    except Exception as exc:
        logger.warning("抓取失败，降级为仅标题+链接：%s —— %s", url, exc)
        return Article(
            url=url,
            # 没有源标题时用 URL 推一个：失败的文章还要落盘、还要人工补正文，
            # 全都叫「(抓取失败)」的话目录里认不出哪篇是哪篇。
            # With no feed title, derive one from the URL: failed articles still get
            # written to disk and filled in by hand, and identical names make the
            # directories indistinguishable.
            title=fallback_title or title_from_url(url),
            text="",
            extraction_ok=False,
        )

    return extract_article(html, url, fallback_title=fallback_title, max_images=max_images)


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _soup(html: str) -> BeautifulSoup:
    """解析 HTML / Parse HTML into a soup."""
    return BeautifulSoup(html or "", "html.parser")


def _extract_body(html: str, soup: BeautifulSoup) -> str:
    """
    抽正文 / Extract the body text.

    两级策略 / Two tiers:
        1. trafilatura —— 去导航、广告、推荐位，效果最好
        2. 启发式 —— 取 <article> 或段落最多的容器，去掉脚本与样式
    """
    try:
        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_precision=True,
        )
        if extracted and len(extracted.strip()) >= MIN_EXTRACTED_CHARS:
            return _normalise(extracted)
    except Exception as exc:
        logger.debug("trafilatura 抽取异常，改用启发式：%s", exc)

    return _normalise(_heuristic_body(soup))


def _heuristic_body(soup: BeautifulSoup) -> str:
    """
    启发式抽正文 / Heuristic body extraction.

    选段落数最多的容器：正文所在的节点通常 <p> 最密集，而导航和侧栏没有。
    Picks the container with the most paragraphs: the body node is where <p> tags
    cluster, while navigation and sidebars have none.
    """
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    best_text = ""
    for container in soup.find_all(["article", "main", "div", "section"]):
        paragraphs = container.find_all("p", recursive=True)
        if not paragraphs:
            continue
        text = "\n".join(p.get_text(" ", strip=True) for p in paragraphs)
        if len(text) > len(best_text):
            best_text = text

    return best_text or soup.get_text(" ", strip=True)


def _choose_title(soup: BeautifulSoup, fallback_title: str, *, extraction_ok: bool) -> str:
    """
    决定最终标题 / Decide which title to keep.

    **抽取降级时优先用 fallback（通常来自 RSS），而不是页面标题。**
    When extraction degrades, the fallback title — normally from RSS — wins over the
    page title.

    原因（真机实测）/ Why, from live verification:
        SPA 站点（如 oschina）在服务端只返回一个外壳页，`og:title` 是站点自己的
        通用标题「OSCHINA - 开源 × AI · 开发者生态社区」，正文为空。此时若采信
        页面标题，就会用一个毫无信息量的站点名**覆盖掉 RSS 里本来正确的标题**——
        抽取反而让数据变差了，而且该标题还会成为落盘目录名。
        SPA sites such as oschina serve only a shell page: `og:title` is the site's
        generic name and there is no body. Trusting the page title there replaces a
        perfectly good RSS title with a meaningless site name — extraction making the
        data worse — and that title then becomes the storage directory name too.

        正文抽不出来时，页面标题同样不可信：两者失效的原因是同一个。
        When the body cannot be extracted the page title is equally suspect: both fail
        for the same reason.
    """
    page_title = _extract_title(soup)

    if not extraction_ok and fallback_title:
        return fallback_title

    return page_title or fallback_title


def _extract_title(soup: BeautifulSoup) -> str:
    """
    抽标题 / Extract the title.

    优先 og:title —— 它是站点主动给分享场景准备的，通常比 <title> 干净
    （<title> 常带「_ 站点名」这类后缀）。
    og:title comes first: sites author it for sharing, so it is usually cleaner than
    <title>, which tends to carry a "— Site Name" suffix.
    """
    for prop in ("og:title", "twitter:title"):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find(
            "meta", attrs={"name": prop}
        )
        if tag and tag.get("content", "").strip():
            return _normalise(tag["content"])

    if soup.title and soup.title.string:
        return _normalise(soup.title.string)

    h1 = soup.find("h1")
    return _normalise(h1.get_text(" ", strip=True)) if h1 else ""


def _extract_author(soup: BeautifulSoup) -> str | None:
    """抽作者 / Extract the author, if declared."""
    for attrs in (
        {"name": "author"},
        {"property": "article:author"},
        {"name": "byl"},
        {"itemprop": "author"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content", "").strip():
            return _normalise(tag["content"])[:100]
    return None


def _extract_published(soup: BeautifulSoup) -> datetime | None:
    """
    抽发布时间 / Extract the publication time.

    只认 ISO 8601 形式的 meta：站点的可见日期文本格式千奇百怪，猜错比没有更糟
    （会让排序和跨日去重出错）。取不到就交给 RSS 的时间。
    Only ISO 8601 meta tags are trusted: visible date text comes in endless formats,
    and guessing wrong is worse than having nothing, since it corrupts ordering and
    cross-day de-duplication. Otherwise the RSS timestamp is used.
    """
    for attrs in (
        {"property": "article:published_time"},
        {"name": "pubdate"},
        {"itemprop": "datePublished"},
        {"name": "date"},
    ):
        tag = soup.find("meta", attrs=attrs)
        content = tag.get("content", "").strip() if tag else ""
        if not content:
            continue
        try:
            return datetime.fromisoformat(content).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _normalise(text: str | None) -> str:
    """
    压平空白但保留段落 / Collapse whitespace while keeping paragraph breaks.

    段落结构对后续的摘要提示词有用，不能整体压成一行。
    Paragraph structure helps the later summarisation prompt, so it must not be
    flattened into a single line.
    """
    if not text:
        return ""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


__all__ = ["MIN_EXTRACTED_CHARS", "extract_article", "fetch_article"]
