"""
Feed 探测 / Feed discovery.

给一个网址，找出它可用的 RSS/Atom 地址，并**当场验证**是否真的能解析出条目。
Given any URL, find its usable RSS/Atom endpoints and verify on the spot that they
actually parse into entries.

为什么需要这个 / Why this exists:
    往 `config/sources.yaml` 里加源时，最容易犯的错是填一个「看起来像 feed」但实际
    返回 HTML 的地址——站点改版后旧地址常常如此。这类地址加进去之后，采集时会每天
    失败一次，而问题要等到有人翻日志才被发现。
    The common mistake when adding to `config/sources.yaml` is an address that looks
    like a feed but actually serves HTML — typical of a site after a redesign. Such an
    entry then fails once a day until somebody reads the logs.

    先探测再添加，把这个问题挡在配置之前。
    Probing before adding keeps the problem out of the configuration in the first place.

探测顺序 / Discovery order:
    1. 传入的地址本身是不是 feed（最常见：用户直接贴了 feed 地址）
    2. 页面 HTML 里 `<link rel="alternate" type="application/rss+xml">` 的声明
    3. 常见路径猜测（/feed、/rss、/atom.xml …）

第 3 步是必要的：实测 InfoQ 的 feed 完全可用，但首页并没有声明它。
Step 3 is necessary: InfoQ's feed works fine yet its homepage does not declare it.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import feedparser
from bs4 import BeautifulSoup

from dna.core.logging import get_logger
from dna.sources.http import fetch_text, is_local_url

logger = get_logger("sources.discover")

# 常见的 feed 路径，按命中概率排序 / common feed paths, most likely first
COMMON_FEED_PATHS: tuple[str, ...] = (
    "/feed",
    "/rss",
    "/rss.xml",
    "/feed.xml",
    "/atom.xml",
    "/index.xml",
    "/feed/atom",
    "/rss/index.xml",
    "/blog/feed",
    "/feeds/posts/default",  # Blogger
    "/?feed=rss2",  # WordPress
)


@dataclass(frozen=True)
class FeedCandidate:
    """
    一个候选 feed 地址及其验证结果 / A candidate feed URL and its verification result.
    """

    url: str
    ok: bool
    title: str = ""
    entry_count: int = 0
    version: str = ""
    reason: str = ""
    latest_title: str = ""

    def __str__(self) -> str:
        mark = "OK" if self.ok else "--"
        return f"[{mark}] {self.url}"


def validate_feed(url: str, *, timeout: float = 20.0) -> FeedCandidate:
    """
    验证一个地址是否为可用的 feed / Verify whether a URL is a usable feed.

    判据与 `parse_feed` 保持一致：看 feedparser 是否识别出 feed 格式（`version`），
    而不是看 `bozo`——HTML 错误页的 `bozo` 是 False（见 issue 003）。
    The criterion matches `parse_feed`: whether feedparser recognised a feed format
    via `version`, not the `bozo` flag, which is False for HTML error pages (issue 003).
    """
    try:
        content = fetch_text(url, timeout=timeout, local=is_local_url(url))
    except Exception as exc:  # noqa: BLE001 - 探测阶段任何失败都只是「这个地址不行」
        return FeedCandidate(url=url, ok=False, reason=_short(exc))

    parsed = feedparser.parse(content)
    version = parsed.get("version") or ""

    if not version:
        return FeedCandidate(url=url, ok=False, reason="不是 RSS/Atom（可能返回了 HTML 页面）")

    entries = parsed.entries
    return FeedCandidate(
        url=url,
        ok=True,
        title=str(parsed.feed.get("title", "")).strip(),
        entry_count=len(entries),
        version=version,
        latest_title=str(entries[0].get("title", "")).strip() if entries else "",
        reason="" if entries else "格式合法但当前没有条目",
    )


def find_declared_feeds(html: str, base_url: str) -> list[str]:
    """
    从页面 HTML 里读出站点声明的 feed 地址 / Read the feeds a page declares.

    纯函数，可离线测试 / A pure function, testable offline.

    站点通过 `<link rel="alternate" type="application/rss+xml" href="...">` 声明。
    相对地址按 base_url 补全。
    """
    soup = BeautifulSoup(html or "", "html.parser")
    found: dict[str, None] = {}

    for link in soup.find_all("link"):
        rel = link.get("rel") or []
        rel_text = " ".join(rel).lower() if isinstance(rel, list) else str(rel).lower()
        type_text = (link.get("type") or "").lower()
        href = (link.get("href") or "").strip()

        if not href or "alternate" not in rel_text:
            continue
        if not any(marker in type_text for marker in ("rss", "atom", "xml")):
            continue

        found.setdefault(urljoin(base_url, href), None)

    return list(found)


def candidate_paths(url: str) -> list[str]:
    """
    生成常见 feed 路径的候选地址 / Build candidate URLs from the common feed paths.

    纯函数，可离线测试 / A pure function, testable offline.

    以站点根为基准而不是当前路径：用户通常贴的是文章页地址，
    而 feed 几乎总在站点根下。
    Candidates are built from the site root rather than the given path: users usually
    paste an article URL, while the feed almost always lives at the root.
    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return []

    root = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    return [f"{root}{path}" for path in COMMON_FEED_PATHS]


def discover_feeds(
    url: str, *, timeout: float = 20.0, max_probes: int = 12
) -> list[FeedCandidate]:
    """
    探测一个网址可用的 feed / Discover usable feeds for a URL.

    返回全部尝试过的候选（含失败的），可用的排在前面。
    Returns every candidate tried, failures included, with the usable ones first.

    参数 / Args:
        max_probes: 最多尝试多少个地址，避免对目标站点造成压力
                    caps the number of requests so the target site is not hammered
    """
    results: list[FeedCandidate] = []
    tried: set[str] = set()

    def probe(candidate_url: str) -> FeedCandidate | None:
        if candidate_url in tried or len(tried) >= max_probes:
            return None
        tried.add(candidate_url)
        outcome = validate_feed(candidate_url, timeout=timeout)
        results.append(outcome)
        return outcome

    # 1) 传入地址本身可能就是 feed
    first = probe(url)
    if first is not None and first.ok:
        return _sorted(results)

    # 2) 页面里声明的 feed
    try:
        html = fetch_text(url, timeout=timeout, local=is_local_url(url))
        for declared in find_declared_feeds(html, url):
            probe(declared)
    except Exception as exc:  # noqa: BLE001 - 抓不到首页就跳过声明发现
        logger.debug("读取页面声明失败：%s —— %s", url, exc)

    if any(c.ok for c in results):
        return _sorted(results)

    # 3) 常见路径猜测
    for candidate in candidate_paths(url):
        probe(candidate)

    return _sorted(results)


def suggest_yaml(
    candidate: FeedCandidate,
    *,
    source_id: str,
    name: str = "",
    lang: str = "zh",
    tags: list[str] | None = None,
    max_items: int | None = None,
) -> str:
    """
    生成可直接粘进 `config/sources.yaml` 的片段 / A snippet ready to paste into sources.yaml.

    纯函数，可离线测试 / A pure function, testable offline.
    """
    lines = [
        f"  - id: {source_id}",
        f"    name: {name or candidate.title or source_id}",
        "    kind: rss",
        f"    url: {candidate.url}",
        f"    lang: {lang}",
        f"    tags: [{', '.join(tags or [])}]",
    ]
    if max_items is not None:
        lines.append(f"    max_items: {max_items}")
    lines.append("    enabled: true")
    return "\n".join(lines)


def _sorted(candidates: list[FeedCandidate]) -> list[FeedCandidate]:
    """可用的排前面，条目多的优先 / Usable first, then by entry count."""
    return sorted(candidates, key=lambda c: (not c.ok, -c.entry_count))


def _short(exc: Exception) -> str:
    """把异常压成一行 / Squash an exception into one line."""
    text = " ".join(str(exc).split())
    return text if len(text) <= 120 else f"{text[:120]}…"


__all__ = [
    "COMMON_FEED_PATHS",
    "FeedCandidate",
    "candidate_paths",
    "discover_feeds",
    "find_declared_feeds",
    "suggest_yaml",
    "validate_feed",
]
