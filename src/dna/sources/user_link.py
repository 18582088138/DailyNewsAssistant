"""
用户投递的链接 / Links submitted by the user.

两个入口共用这一层 / Shared by both entry points:
    - GUI 里手动粘贴（via=gui）
    - 飞书机器人转发（via=inbox，P9 接入）

与 RSS 的区别在于**没有 feed 可解析**：只有一个 URL，标题和正文都要靠后续的
抽取层去取。因此这里只负责「把用户发来的一坨文本变成干净的 RawItem 列表」。
Unlike RSS there is no feed to parse: there is only a URL, and the title and body
must come from the extraction layer. This module's whole job is turning whatever
text the user sent into a clean list of RawItem.
"""

from __future__ import annotations

from datetime import datetime

from dna.core.config import SourceConfig
from dna.core.logging import get_logger
from dna.core.models import RawItem, SourceKind
from dna.core.urls import canonicalize_url, extract_urls
from dna.sources.base import SourceAdapter

logger = get_logger("sources.user_link")

USER_SOURCE_ID = "user"


class UserLinkSource(SourceAdapter):
    """
    用户投递的链接集合 / A batch of user-submitted links.

    与其它 adapter 不同，它不主动抓取——链接由外部推进来（GUI 或飞书机器人），
    `fetch()` 只是把已收集的内容交出去。
    Unlike the other adapters this one does not pull: links are pushed in from the
    GUI or the Feishu bot, and `fetch()` merely hands over what has been collected.
    """

    def __init__(
        self, config: SourceConfig | None = None, via: SourceKind = SourceKind.GUI
    ) -> None:
        super().__init__(config or default_config())
        self.via = via
        self._items: list[RawItem] = []

    def submit(self, text: str, *, submitted_at: datetime | None = None) -> list[RawItem]:
        """
        提交一段可能含链接的文本 / Submit text that may contain links.

        返回本次**新增**的条目（本批次内已出现过的链接会被跳过）。
        Returns the items newly added by this call; links already seen in this batch
        are skipped.
        """
        added = items_from_text(text, source_id=self.id, via=self.via, submitted_at=submitted_at)

        known = {item.url for item in self._items}
        fresh = [item for item in added if item.url not in known]
        self._items.extend(fresh)

        if len(added) != len(fresh):
            logger.debug("本批次内跳过 %d 条重复链接", len(added) - len(fresh))
        return fresh

    def fetch(self, limit: int | None = None) -> list[RawItem]:
        """交出已投递的条目 / Hand over the submitted items."""
        cap = self.effective_limit(limit, len(self._items))
        return self._items[:cap]

    def clear(self) -> None:
        """清空已投递内容 / Drop everything submitted so far."""
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


def items_from_text(
    text: str,
    *,
    source_id: str = USER_SOURCE_ID,
    via: SourceKind = SourceKind.GUI,
    submitted_at: datetime | None = None,
) -> list[RawItem]:
    """
    从一段文本抽出全部链接并生成 RawItem / Turn free text into RawItem objects.

    纯函数，无网络请求；标题留空，由抽取层填。
    A pure function with no network access. Titles are left empty for the extraction
    layer to fill in.

    去重按**规范化后的 URL** 判断：用户很容易把同一篇文章的不同分享链接（带不同
    追踪参数）发两次，按原始字符串比对会漏掉。
    De-duplication compares canonicalised URLs: the same article is easily submitted
    twice through different share links carrying different tracking parameters, which
    a raw string comparison would miss.
    """
    seen_canonical: set[str] = set()
    items: list[RawItem] = []

    for url in extract_urls(text):
        canonical = canonicalize_url(url)
        if not canonical or canonical in seen_canonical:
            continue
        seen_canonical.add(canonical)

        items.append(
            RawItem(
                source_id=source_id,
                via=via,
                url=url,
                title="",  # 由 extract 层补全 / filled in by the extraction layer
                fetched_at=submitted_at or datetime.now(),
            )
        )

    return items


def default_config() -> SourceConfig:
    """用户投递源的默认配置 / Default configuration for the user-submission source."""
    return SourceConfig(
        id=USER_SOURCE_ID,
        name="用户投递",
        kind=SourceKind.INBOX,
        url="",
        enabled=True,
        tags=["user"],
    )


__all__ = ["USER_SOURCE_ID", "UserLinkSource", "default_config", "items_from_text"]
