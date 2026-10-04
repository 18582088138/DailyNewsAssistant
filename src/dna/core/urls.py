"""
URL 规范化与提取 / URL canonicalisation and extraction.

规范化是**一级去重的基础**：同一篇文章从不同渠道拿到时，URL 往往只差追踪参数
（`?utm_source=weibo`、`?from=groupmessage`、`#comments`），字符串比对会当成两篇。
Canonicalisation is the basis of first-pass de-duplication: the same article arrives
from different channels with URLs that differ only by tracking parameters, which a
plain string comparison would treat as two distinct items.

纯字符串处理，无网络请求，因此可以完全离线测试。
Pure string handling with no network access, so it is fully testable offline.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# 追踪参数：命中即剔除 / tracking parameters, always stripped
_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        # 通用广告与来源追踪
        "fbclid", "gclid", "dclid", "msclkid", "yclid", "twclid", "igshid",
        "mc_cid", "mc_eid", "_hsenc", "_hsmi", "vero_id", "wickedid",
        # 中文平台常见
        "spm", "scm", "from", "share_source", "share_medium", "share_plat",
        "share_tag", "share_session_id", "share_token", "isappinstalled",
        "wxshare_count", "srcid", "sharer_shareinfo", "sharer_shareinfo_first",
        "chksm", "key", "uin", "devicetype", "exportkey", "pass_ticket",
        # 其它
        "ref", "referrer", "source", "src", "cmpid", "ncid", "smid",
    }
)

# 追踪参数前缀：以此开头即剔除 / parameter prefixes that are always tracking
_TRACKING_PREFIXES: tuple[str, ...] = ("utm_", "pk_", "at_", "ito_", "__twitter_")

# 默认端口，出现时省略 / default ports, omitted when present
_DEFAULT_PORTS = {"http": "80", "https": "443"}

# 从自由文本中提取 URL / find URLs inside free text
# 结尾的中英文标点不算 URL 的一部分（用户粘贴时常带句号或右括号）
_URL_RE = re.compile(
    r"""https?://[^\s<>"'）)】\]}，。；、！？]+""",
    re.IGNORECASE,
)

# 结尾若残留这些字符则剥掉 / trailing characters to trim off a captured URL
_TRAILING_JUNK = ".,;:!?'\"、，。；：！？）)】]}>"


def canonicalize_url(url: str) -> str:
    """
    把 URL 规范化成可用于比对的形式 / Normalise a URL into a comparable form.

    处理 / Steps:
        1. scheme 与 host 转小写（host 大小写不敏感，path 敏感，不能一起转）
        2. 省略默认端口（http:80 / https:443）
        3. 剔除追踪参数（utm_* 等前缀，以及 fbclid / spm / from 等具名参数）
        4. 剩余查询参数按键排序，消除仅参数顺序不同的差异
        5. 去掉 fragment（`#comments` 不构成不同文章）
        6. 去掉末尾斜杠（根路径除外）

    >>> canonicalize_url("HTTPS://Example.COM:443/a/?utm_source=x&b=2&a=1#top")
    'https://example.com/a?a=1&b=2'

    非法或空 URL 原样返回，由调用方决定如何处理。
    Malformed or empty URLs are returned unchanged for the caller to handle.
    """
    if not url or not url.strip():
        return ""

    try:
        parts = urlparse(url.strip())
    except ValueError:
        return url.strip()

    if not parts.scheme or not parts.netloc:
        return url.strip()

    scheme = parts.scheme.lower()

    # host 转小写但保留用户信息与端口 / lowercase the host, keep userinfo and port
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, _, hostport = netloc.rpartition("@")
        netloc = f"{userinfo}@{hostport.lower()}"
    else:
        netloc = netloc.lower()

    # 省略默认端口
    if ":" in netloc:
        host, _, port = netloc.rpartition(":")
        if port == _DEFAULT_PORTS.get(scheme):
            netloc = host

    query = urlencode(sorted(_keep_meaningful_params(parts.query)))

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse((scheme, netloc, path, parts.params, query, ""))


def _keep_meaningful_params(query: str) -> list[tuple[str, str]]:
    """
    过滤掉追踪参数，保留真正影响内容的参数 / Drop tracking params, keep meaningful ones.

    注意不能无脑清空 query：很多站点的文章 id 就在参数里
    （如 `?id=12345`、微信公众号的 `?__biz=...&mid=...&idx=1`），清空会把
    不同文章合并成同一条。
    The query cannot simply be discarded: many sites carry the article id there
    (`?id=12345`, or WeChat's `__biz`/`mid`/`idx`), and dropping it would merge
    distinct articles into one.
    """
    kept: list[tuple[str, str]] = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in _TRACKING_PARAMS:
            continue
        if any(lowered.startswith(prefix) for prefix in _TRACKING_PREFIXES):
            continue
        kept.append((key, value))
    return kept


def url_hash(url: str) -> str:
    """
    由规范化 URL 生成稳定标识 / A stable id derived from the canonical URL.

    用作 NewsItem.id 与台账 items 表的唯一键，因此必须**跨进程、跨次运行稳定**
    ——不能用 Python 内置的 hash()，它带随机种子。
    Used as NewsItem.id and the unique key of the ledger's items table, so it must
    be stable across processes and runs — Python's built-in hash() is seeded and
    therefore unusable here.
    """
    canonical = canonicalize_url(url)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# 人工创作文章的伪 URL 方案 / the pseudo-scheme used by hand-authored articles
#
# 为什么非要造一个 URL：`url_hash("")` 是常量（sha256("") 的前 16 位），
# 而 id 就是 url_hash(url)。空 URL 的每一篇都会撞成同一行，`register()` 从此
# 永远返回第一篇。造一个带 uuid 的方案，id 就自然唯一，且**不碰任何既有路径**。
# Why a synthetic URL is needed: url_hash("") is a constant and the id *is*
# url_hash(url), so every URL-less article would collide on one row and register()
# would keep returning the first one forever. A uuid-bearing scheme makes the id
# unique on its own and leaves every existing path untouched.
CUSTOM_URL_SCHEME = "custom"

# 台账里的 source_id，同时也是人看到的来源名 / the ledger source_id, also the human label
CUSTOM_SOURCE_LABEL = "custom_article"


def is_custom_url(url: str) -> bool:
    """
    这个地址是不是人工创作文章的伪 URL / Whether this is a hand-authored pseudo-URL.

    >>> is_custom_url("custom://3f2a")
    True
    >>> is_custom_url("https://example.com/a")
    False
    """
    try:
        return urlparse(url).scheme == CUSTOM_URL_SCHEME
    except ValueError:
        return False


def display_source(url: str) -> str:
    """
    给人看的来源 / The source as a person should read it.

    人工创作的文章没有原文地址，把伪 URL 印进产物抬头（`> 总结（zh）　·　来源：custom://…`）
    对读者毫无意义。这些抬头是要跟着稿子发出去的，所以统一显示成来源名。
    A hand-authored article has no source address, and printing the pseudo-URL into the
    artefact header would mean nothing to a reader — yet those headers travel with the
    published script, so they show the source label instead.

    >>> display_source("custom://3f2a")
    'custom_article'
    >>> display_source("https://example.com/a")
    'https://example.com/a'
    """
    return CUSTOM_SOURCE_LABEL if is_custom_url(url) else url


def extract_urls(text: str) -> list[str]:
    """
    从自由文本中提取全部 URL，去重保序 / Extract every URL from free text.

    用于用户投递：手机上转发过来的消息通常是「看看这个 https://… 挺有意思」这种
    夹杂形式，甚至一条消息里带好几个链接。
    Used for user submissions: a forwarded message is typically prose with a link
    embedded, and may contain several links at once.

    >>> extract_urls("看看这个 https://a.com/x 还有 https://b.com/y。")
    ['https://a.com/x', 'https://b.com/y']
    """
    found: dict[str, None] = {}
    for match in _URL_RE.findall(text or ""):
        cleaned = match.rstrip(_TRAILING_JUNK)
        if cleaned:
            found.setdefault(cleaned, None)
    return list(found)


def same_url(a: str, b: str) -> bool:
    """两个 URL 规范化后是否相同 / Whether two URLs are the same once canonicalised."""
    return canonicalize_url(a) == canonicalize_url(b)


def host_of(url: str) -> str:
    """
    取 URL 的主机名，用于来源署名与配图文件命名。
    The hostname of a URL, used for attribution and image filenames.

    >>> host_of("https://www.example.com/a")
    'www.example.com'
    """
    try:
        return urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return ""


def title_from_url(url: str) -> str:
    """
    从 URL 猜一个可读标题 / Derive a readable title from a URL.

    抓取失败时用作标题兜底。写死一个「(抓取失败)」会让**所有**失败的文章挤在同一个
    名字下，落盘目录也全叫这个，人根本认不出哪篇是哪篇；用 URL 至少还能对上号。
    Used as the title fallback when a fetch fails. A hard-coded "(fetch failed)" would
    give *every* failed article the same name — and the same directory name — leaving no
    way to tell them apart, whereas the URL can at least be matched back to the link.

    路径末段是有意义的词就用它，否则退回「主机名 + 末段」。
    The last path segment is used when it carries words; otherwise host plus segment.

    >>> title_from_url("https://example.com/2026/09/gpt-5-released")
    'gpt 5 released'
    >>> title_from_url("https://zhuanlan.zhihu.com/p/2067333343717355528")
    'zhuanlan.zhihu.com p 2067333343717355528'
    """
    host = host_of(url)
    try:
        segments = [s for s in urlparse(url).path.split("/") if s]
    except ValueError:
        segments = []

    if not segments:
        return host or url

    last = segments[-1]
    # 去掉扩展名：/foo/bar.html 的标题是 bar
    if "." in last:
        last = last.rsplit(".", 1)[0]
    readable = last.replace("-", " ").replace("_", " ").strip()

    # 不可读的末段（知乎的纯数字、微信的随机串）单独看没有信息量，补上主机名与前段
    # An unreadable segment — Zhihu's digits, WeChat's random string — carries no
    # information on its own, so the host and preceding segments are prepended.
    if _is_opaque(readable):
        prefix = " ".join(segments[:-1][-2:])
        return " ".join(part for part in (host, prefix, readable) if part)

    return readable or host


def _is_opaque(segment: str) -> bool:
    """
    判断路径末段是不是无意义的 id / Whether a path segment is a meaningless id.

    两个信号：**没有字母**（`/p/2067333343717355528`），或者**又长又带大写**
    （`/s/VgO-WiLWNRzuSGSweHrY7g`）。真正的标题 slug 几乎总是全小写的短词。
    Two signals: no letters at all, or long with uppercase in it. A genuine title slug is
    almost always short lowercase words.
    """
    if not any(c.isalpha() for c in segment):
        return True
    return len(segment) >= 12 and any(c.isupper() for c in segment)


_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})  # noqa: S104


def is_local_url(url: str) -> bool:
    """
    这个地址是不是指向本机 / Whether the URL points at this machine.

    本机地址必须**绕过公司代理**：自建 RSSHub 在 localhost:1200、TTS 服务在
    127.0.0.1:8300，走代理一律失败，而且报出来的错和代理毫无关系。

    按 hostname 判，不按子串。`sources/http.py` 与 `tts/client.py` 曾各有一份实现，
    其中子串那份会把 `http://localhost.example.com` 也认成本机 —— 两份语义不同的
    同名函数，取决于谁 import 了谁。
    Matched on the parsed hostname rather than a substring: the substring variant also
    accepted `http://localhost.example.com` as local.
    """
    return (urlparse(url).hostname or "") in _LOCAL_HOSTS


__all__ = [
    "CUSTOM_SOURCE_LABEL",
    "CUSTOM_URL_SCHEME",
    "canonicalize_url",
    "display_source",
    "extract_urls",
    "host_of",
    "is_custom_url",
    "is_local_url",
    "same_url",
    "title_from_url",
    "url_hash",
]
