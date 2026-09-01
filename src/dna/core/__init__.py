"""
核心层 / Core layer：配置、数据模型、命名规则、日志、异常、环境自检。

本层不依赖任何其它内部模块，是整个依赖图的底座。
This layer depends on no other internal module; it is the base of the dependency graph.
"""

from dna.core.config import Profile, Settings, SourceConfig, get_settings, load_profile, load_sources
from dna.core.errors import (
    ConfigError,
    DNAError,
    ExtractionError,
    InboxError,
    ProviderError,
    RenderError,
    SourceError,
    StoreError,
)
from dna.core.logging import get_logger, setup_logging
from dna.core.models import (
    AppKind,
    Article,
    Cluster,
    DailyDigest,
    DigestEntry,
    DigestStats,
    Language,
    MediaAsset,
    MediaKind,
    NewsItem,
    RawItem,
    SourceKind,
)
from dna.core.naming import issue_dir_name, slugify, topic_dir_name

__all__ = [
    "AppKind",
    "Article",
    "Cluster",
    "ConfigError",
    "DNAError",
    "DailyDigest",
    "DigestEntry",
    "DigestStats",
    "ExtractionError",
    "InboxError",
    "Language",
    "MediaAsset",
    "MediaKind",
    "NewsItem",
    "Profile",
    "ProviderError",
    "RawItem",
    "RenderError",
    "Settings",
    "SourceConfig",
    "SourceError",
    "SourceKind",
    "StoreError",
    "get_logger",
    "get_settings",
    "issue_dir_name",
    "load_profile",
    "load_sources",
    "setup_logging",
    "slugify",
    "topic_dir_name",
]
