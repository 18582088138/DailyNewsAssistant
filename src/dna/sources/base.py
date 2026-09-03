"""
信息源抽象 / News source abstraction.

每个信息源（RSS、RSSHub、用户投递）都实现 SourceAdapter，向流水线交付统一的
RawItem 列表。新增一类源只需新增一个 adapter，上层零改动。
Every source — RSS, RSSHub, user submissions — implements SourceAdapter and hands
the pipeline a uniform list of RawItem. Adding a source type means adding one
adapter and changing nothing above it.

设计约定 / Design rules:
    1. **抓取与解析分离**：`fetch()` 负责 I/O，`parse()` 是纯函数。
       解析逻辑因此可以用本地样例离线测试，不需要联网。
       Fetching and parsing are separate: `fetch()` does I/O, `parse()` is pure, so
       parsing can be tested offline against local samples.
    2. **单个源失败不能拖垮整轮采集**：错误在 registry 层被隔离并记录。
       One failing source must not abort the whole run; errors are isolated in the
       registry layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from dna.core.config import SourceConfig
from dna.core.models import RawItem


class SourceAdapter(ABC):
    """一个信息源 / A single news source."""

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    @property
    def id(self) -> str:
        """源标识 / Source identifier."""
        return self.config.id

    @property
    def name(self) -> str:
        """源显示名 / Human-readable source name."""
        return self.config.name

    @abstractmethod
    def fetch(self, limit: int | None = None) -> list[RawItem]:
        """
        抓取该源的最新条目 / Fetch the latest items from this source.

        参数 / Args:
            limit: 最多返回多少条；None 表示用配置里的上限
                   maximum number of items; None uses the configured cap

        抛出 / Raises:
            SourceError: 网络失败或内容无法解析
        """

    def effective_limit(self, limit: int | None, default: int) -> int:
        """
        计算本次实际生效的条数上限 / Resolve the item cap for this call.

        优先级 / Precedence: 调用参数 > 源配置 max_items > 全局默认
        """
        if limit is not None:
            return limit
        if self.config.max_items is not None:
            return self.config.max_items
        return default

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.id}>"


__all__ = ["SourceAdapter"]
