"""
共用的 HTTP 抓取 / Shared HTTP fetching.

RSS 抓取与正文抽取都要发 HTTP 请求，超时、重试、User-Agent、代理处理只写一份。
Both feed fetching and article extraction issue HTTP requests; timeouts, retries,
User-Agent and proxy handling are implemented once here.

代理约定 / Proxy behaviour:
    默认 `trust_env=True`，走系统代理（公司网络下访问外网必需）。
    访问 localhost 的本地服务（自建 RSSHub）时用 `local=True` 关闭代理，
    避免本地请求被送去公司代理——NO_PROXY 并非所有 HTTP 栈都能正确解析。
    The default honours the system proxy, which the corporate network requires.
    For localhost services use `local=True`: not every HTTP stack parses NO_PROXY's
    CIDR entries correctly, so local requests can otherwise end up at the proxy.
"""

from __future__ import annotations

import httpx

from dna.core.errors import SourceError
from dna.core.logging import get_logger
from dna.core.urls import is_local_url

logger = get_logger("sources.http")

DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB，防止误抓到超大文件

# 用真实浏览器 UA：不少站点对默认的 python-httpx UA 直接返回 403
# A browser UA is required: many sites return 403 for the default python-httpx one.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def fetch_text(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    local: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
    headers: dict[str, str] | None = None,
) -> str:
    """
    抓取一个 URL 并返回文本 / Fetch a URL and return its text.

    参数 / Args:
        local:     目标是否为本机服务；为真时绕过系统代理
        max_bytes: 内容大小上限，超出即报错而不是把内存吃满

    抛出 / Raises:
        SourceError: 网络失败、HTTP 错误状态、内容过大
    """
    merged = {**DEFAULT_HEADERS, **(headers or {})}
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            trust_env=not local,
            headers=merged,
        ) as client:
            response = client.get(url)
            response.raise_for_status()

            if len(response.content) > max_bytes:
                raise SourceError(
                    f"内容过大（{len(response.content)} 字节 > {max_bytes}）：{url}"
                    f" / response too large"
                )
            return response.text

    except httpx.HTTPStatusError as exc:
        raise SourceError(
            f"HTTP {exc.response.status_code}：{url} / HTTP error {exc.response.status_code}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise SourceError(f"请求超时（{timeout}s）：{url} / request timed out") from exc
    except httpx.HTTPError as exc:
        raise SourceError(f"请求失败：{url} —— {exc} / request failed") from exc


def fetch_bytes(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    local: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
    referer: str | None = None,
) -> bytes:
    """
    抓取二进制内容 / Fetch binary content.

    用于下载配图；与 fetch_text 分开是因为图片不能按文本解码。
    Used for downloading images, which must not be decoded as text.

    参数 / Args:
        referer: 图片所在的文章地址 / the article page the image belongs to

    **`referer` 对下载配图几乎是必需的**：很多图床做了防盗链，只看 `Referer`
    是否来自本站，不带就直接返回 403。实测量子位的图床 `i.qbitai.com` 即是如此
    ——不带 Referer 全部 403，带上立刻 200。
    A `referer` is all but mandatory for images: many image hosts implement hotlink
    protection keyed on that header and return 403 without it. Verified on 量子位's
    image host, which returns 403 for every image unless the article URL is supplied.
    """
    headers = dict(DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer

    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, trust_env=not local, headers=headers
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            if len(response.content) > max_bytes:
                raise SourceError(f"内容过大：{url} / response too large")
            return response.content
    except httpx.HTTPStatusError as exc:
        raise SourceError(f"HTTP {exc.response.status_code}：{url}") from exc
    except httpx.HTTPError as exc:
        raise SourceError(f"请求失败：{url} —— {exc}") from exc


# `is_local_url` 的唯一实现在 `core/urls.py`（这里曾经有一份子串版，
# 会把 http://localhost.example.com 也认成本机）。这里只做转发，保持调用点不变。


__all__ = ["DEFAULT_HEADERS", "DEFAULT_TIMEOUT", "fetch_bytes", "fetch_text", "is_local_url"]
