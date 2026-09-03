"""
RSSHub 信息源 / RSSHub news source.

自建 RSSHub 把微信公众号、知乎、微博、X 等没有官方 RSS 的平台转成 RSS，
所以这里复用 RSS 的解析逻辑，只多做一件事：把配置里的路由拼成完整地址。
A self-hosted RSSHub turns platforms without official feeds — WeChat official
accounts, Zhihu, Weibo, X — into RSS, so this reuses the RSS parsing and only adds
one thing: joining the configured route onto the instance base URL.

配置里写路由而不是完整 URL（`/zhihu/hotlist` 而非 `http://localhost:1200/zhihu/hotlist`），
这样换实例地址时只改 .env 的 RSSHUB_BASE_URL 一处，不用逐条改 sources.yaml。
Routes rather than full URLs are configured so that moving the instance means
editing RSSHUB_BASE_URL alone instead of every entry in sources.yaml.

⚠️ 自建 RSSHub 的实际接入排在 P10；本 adapter 已可用，但尚未对真实实例验证。
Actual RSSHub deployment is scheduled for P10; this adapter works but has not yet
been verified against a live instance.
"""

from __future__ import annotations

from dna.core.config import SourceConfig
from dna.core.logging import get_logger
from dna.sources.rss import RSSAdapter

logger = get_logger("sources.rsshub")


class RSSHubAdapter(RSSAdapter):
    """通过自建 RSSHub 实例访问的平台源 / A platform source served by RSSHub."""

    def __init__(self, config: SourceConfig, base_url: str) -> None:
        super().__init__(config)
        self.base_url = base_url.rstrip("/")

    def feed_url(self) -> str:
        """把路由拼成完整地址 / Join the route onto the instance base URL."""
        return join_route(self.base_url, self.config.url)


def join_route(base_url: str, route: str) -> str:
    """
    拼接 RSSHub 实例地址与路由 / Join an RSSHub base URL with a route.

    容忍配置里的各种写法：带不带前导斜杠、base 带不带尾部斜杠；
    若用户直接写了完整 URL 也原样放行，方便临时指向公共实例。
    Tolerates the forms users actually write — leading slash or not, trailing slash
    or not — and passes a full URL through unchanged so a public instance can be
    pointed at ad hoc.

    >>> join_route("http://localhost:1200", "/zhihu/hotlist")
    'http://localhost:1200/zhihu/hotlist'
    >>> join_route("http://localhost:1200/", "zhihu/hotlist")
    'http://localhost:1200/zhihu/hotlist'
    """
    if route.lower().startswith(("http://", "https://")):
        return route
    return f"{base_url.rstrip('/')}/{route.lstrip('/')}"


__all__ = ["RSSHubAdapter", "join_route"]
