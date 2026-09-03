"""
test_urls.py —— URL 规范化与提取单元测试 / URL canonicalisation and extraction tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/core/test_urls.py -v

覆盖 / Covers:
    1. scheme / host 转小写，但 **path 保持大小写敏感**（path 大小写有意义）
    2. 省略默认端口（http:80 / https:443），非默认端口保留
    3. **剔除追踪参数**：utm_* 前缀、fbclid / spm / from 等具名参数
    4. **保留有意义的参数**：`?id=123`、微信的 `__biz/mid/idx`——清空会把不同文章合并
    5. 查询参数排序：仅顺序不同的 URL 必须规范化为同一个
    6. 去 fragment、去末尾斜杠（根路径除外）
    7. url_hash 稳定且跨追踪参数一致（一级去重的基础）
    8. extract_urls：从中文文本中提取、去重保序、剥掉结尾标点
    9. host_of / same_url

为什么值得细测 / Why this matters:
    规范化是**一级去重的基础**。规则太松会把同一篇文章当成两条（日报里出现重复）；
    太严会把不同文章合并成一条（日报里丢内容）。两种错误都只在生产数据上才显形。
    Canonicalisation underpins first-pass de-duplication. Too lenient and one article
    appears twice in the digest; too aggressive and distinct articles are merged and
    content is lost. Both only surface on real data.

预期 / Expected:
    41 passed；耗时 < 1s；纯字符串运算，无网络请求
"""

from __future__ import annotations

import pytest

from dna.core.urls import (
    canonicalize_url,
    extract_urls,
    host_of,
    same_url,
    title_from_url,
    url_hash,
)


# --- 大小写与端口 / case and ports -------------------------------------------


def test_scheme_and_host_lowercased() -> None:
    """scheme 与 host 转小写 / Scheme and host are lowercased."""
    assert canonicalize_url("HTTPS://Example.COM/a") == "https://example.com/a"


def test_path_case_is_preserved() -> None:
    """
    path 必须保持大小写 / The path must keep its case.

    很多站点的文章 id 是大小写敏感的（如 bilibili 的 BV 号），
    一起转小写会导致 404 和错误合并。
    """
    assert canonicalize_url("https://example.com/BV1xx411c7XX") == "https://example.com/BV1xx411c7XX"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:80/a", "http://example.com/a"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),  # 非默认端口保留
    ],
)
def test_default_ports_omitted(raw: str, expected: str) -> None:
    """省略默认端口，保留非默认端口 / Default ports are dropped, others kept."""
    assert canonicalize_url(raw) == expected


# --- 追踪参数 / tracking parameters -------------------------------------------


@pytest.mark.parametrize(
    "param",
    [
        "utm_source=weibo", "utm_medium=social", "utm_campaign=x",
        "fbclid=abc", "gclid=abc", "spm=a2h0k", "from=groupmessage",
        "share_source=copy_web", "ref=hn", "isappinstalled=0",
    ],
)
def test_tracking_params_are_stripped(param: str) -> None:
    """常见追踪参数必须剔除 / Common tracking parameters must be stripped."""
    assert canonicalize_url(f"https://example.com/a?{param}") == "https://example.com/a"


def test_tracking_stripped_but_content_params_kept() -> None:
    """追踪参数剔除、内容参数保留 / Tracking goes, content stays."""
    result = canonicalize_url("https://example.com/p?id=123&utm_source=x&fbclid=y")
    assert result == "https://example.com/p?id=123"


def test_meaningful_params_are_never_dropped() -> None:
    """
    **不能无脑清空 query** —— 微信公众号文章的 id 就在参数里，
    清空会把不同文章合并成同一条。
    The query must not simply be discarded: WeChat article ids live there, and
    dropping them would merge distinct articles into one.
    """
    url = "https://mp.weixin.qq.com/s?__biz=MzA5&mid=2650&idx=1&sn=abc"
    result = canonicalize_url(url)
    for key in ("__biz", "mid", "idx", "sn"):
        assert key in result


def test_query_params_are_sorted() -> None:
    """仅参数顺序不同应规范化为同一个 / Parameter order must not create a difference."""
    assert canonicalize_url("https://e.com/a?b=2&a=1") == canonicalize_url("https://e.com/a?a=1&b=2")


# --- fragment 与斜杠 / fragments and slashes ----------------------------------


def test_fragment_removed() -> None:
    """`#comments` 不构成不同文章 / A fragment does not make a different article."""
    assert canonicalize_url("https://example.com/a#comments") == "https://example.com/a"


def test_trailing_slash_removed() -> None:
    """去掉末尾斜杠 / A trailing slash is removed."""
    assert canonicalize_url("https://example.com/a/") == "https://example.com/a"


def test_root_slash_preserved() -> None:
    """根路径的斜杠保留 / The root path keeps its slash."""
    assert canonicalize_url("https://example.com/") == "https://example.com/"


# --- 异常输入 / edge cases ----------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_url_returns_empty(raw: str) -> None:
    """空输入返回空串 / Empty input yields an empty string."""
    assert canonicalize_url(raw) == ""


@pytest.mark.parametrize("raw", ["not a url", "/relative/path", "mailto:a@b.com"])
def test_non_http_url_passed_through(raw: str) -> None:
    """非 HTTP URL 原样返回，由调用方决定怎么处理 / Non-HTTP input passes through."""
    assert canonicalize_url(raw) == raw


# --- 哈希 / hashing -----------------------------------------------------------


def test_url_hash_is_stable() -> None:
    """
    同一 URL 的哈希必须稳定 —— 它是台账的唯一键，不能用带随机种子的内置 hash()。
    The hash must be stable: it is the ledger's unique key, so Python's seeded
    built-in hash() cannot be used.
    """
    assert url_hash("https://example.com/a") == url_hash("https://example.com/a")


def test_url_hash_ignores_tracking_params() -> None:
    """
    带不带追踪参数应得到同一个哈希 —— 这是一级去重能生效的关键。
    Tracking parameters must not change the hash; this is what makes first-pass
    de-duplication work.
    """
    assert url_hash("https://example.com/a?utm_source=x") == url_hash("https://example.com/a")


def test_url_hash_differs_for_different_articles() -> None:
    """不同文章哈希不同 / Different articles hash differently."""
    assert url_hash("https://example.com/a") != url_hash("https://example.com/b")


def test_url_hash_length() -> None:
    """哈希长度固定，便于做数据库列 / A fixed-width hash, convenient as a DB column."""
    assert len(url_hash("https://example.com/a")) == 16


# --- 文本提取 / extraction from text ------------------------------------------


def test_extract_single_url() -> None:
    """从一句话里提取链接 / Extract a link from a sentence."""
    assert extract_urls("看看这个 https://a.com/x 挺有意思") == ["https://a.com/x"]


def test_extract_multiple_urls_keeps_order() -> None:
    """多个链接按出现顺序返回 / Multiple links keep their order."""
    text = "第一个 https://a.com/1 第二个 https://b.com/2"
    assert extract_urls(text) == ["https://a.com/1", "https://b.com/2"]


def test_extract_dedupes() -> None:
    """重复链接只保留一次 / Repeated links appear once."""
    assert extract_urls("https://a.com/x 和 https://a.com/x") == ["https://a.com/x"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("链接是 https://a.com/x。", "https://a.com/x"),  # 中文句号
        ("链接是 https://a.com/x，然后", "https://a.com/x"),  # 中文逗号
        ("(https://a.com/x)", "https://a.com/x"),  # 英文括号
        ("（https://a.com/x）", "https://a.com/x"),  # 中文括号
        ("看 https://a.com/x!", "https://a.com/x"),
    ],
)
def test_trailing_punctuation_trimmed(text: str, expected: str) -> None:
    """
    结尾标点不能算进 URL / Trailing punctuation must not become part of the URL.

    用户从手机上转发消息时，链接后面几乎总是跟着标点。
    Links forwarded from a phone almost always have punctuation right after them.
    """
    assert extract_urls(text) == [expected]


def test_extract_keeps_meaningful_query() -> None:
    """URL 里的查询参数不应被截断 / Query strings must survive extraction."""
    assert extract_urls("看 https://a.com/x?id=1&b=2 吧") == ["https://a.com/x?id=1&b=2"]


def test_extract_no_urls() -> None:
    """没有链接时返回空列表 / No links yields an empty list."""
    assert extract_urls("这段话里没有链接") == []


def test_extract_from_empty() -> None:
    """空输入安全 / Empty input is safe."""
    assert extract_urls("") == []


# --- 辅助函数 / helpers -------------------------------------------------------


def test_same_url_ignores_tracking() -> None:
    """same_url 忽略追踪参数差异 / same_url ignores tracking differences."""
    assert same_url("https://a.com/x?utm_source=1", "https://a.com/x") is True
    assert same_url("https://a.com/x", "https://a.com/y") is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.example.com/a", "www.example.com"),
        ("https://Example.COM:8080/a", "example.com"),
        ("not a url", ""),
    ],
)
def test_host_of(url: str, expected: str) -> None:
    """取主机名，用于署名与图片命名 / Hostname, used for credits and filenames."""
    assert host_of(url) == expected


# --- URL 推标题 / deriving a title from a URL ----------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # 有意义的 slug 直接用
        ("https://example.com/2026/09/gpt-5-released", "gpt 5 released"),
        ("https://example.com/posts/why_rag_fails.html", "why rag fails"),
        # 纯数字末段没有信息量，补主机名与前段
        ("https://zhuanlan.zhihu.com/p/2067333343717355528",
         "zhuanlan.zhihu.com p 2067333343717355528"),
        ("https://www.qbitai.com/2026/09/482337.html", "www.qbitai.com 2026 09 482337"),
        # 又长又带大写的随机串同样是 id，不是标题
        ("https://mp.weixin.qq.com/s/VgO-WiLWNRzuSGSweHrY7g",
         "mp.weixin.qq.com s VgO WiLWNRzuSGSweHrY7g"),
        # 没有路径时退回主机名
        ("https://example.com/", "example.com"),
        ("https://example.com", "example.com"),
    ],
)
def test_title_from_url(url: str, expected: str) -> None:
    """
    抓取失败时用 URL 推一个标题。

    写死「(抓取失败)」会让所有失败的文章同名、落盘目录也同名，人根本认不出哪篇是哪篇。
    """
    assert title_from_url(url) == expected


def test_title_from_url_distinguishes_two_failures() -> None:
    """
    两篇不同的失败文章必须得到不同的标题——这正是当初写死常量的问题所在。
    """
    a = title_from_url("https://zhuanlan.zhihu.com/p/111")
    b = title_from_url("https://zhuanlan.zhihu.com/p/222")

    assert a != b
