"""
信息源层 / Sources layer：把各类外部渠道统一成 RawItem。

    from dna.sources import collect, build_adapters

新增一类源 = 新增一个 SourceAdapter 实现 + 在 registry 里登记，上层零改动。
Adding a source type means one new SourceAdapter plus a registry entry; nothing above changes.
"""

from dna.sources.base import SourceAdapter
from dna.sources.registry import (
    CollectResult,
    SourceFailure,
    build_adapter,
    build_adapters,
    collect,
)
from dna.sources.rss import RSSAdapter, parse_feed
from dna.sources.rsshub import RSSHubAdapter, join_route
from dna.sources.user_link import UserLinkSource, items_from_text

__all__ = [
    "CollectResult",
    "RSSAdapter",
    "RSSHubAdapter",
    "SourceAdapter",
    "SourceFailure",
    "UserLinkSource",
    "build_adapter",
    "build_adapters",
    "collect",
    "items_from_text",
    "join_route",
    "parse_feed",
]
