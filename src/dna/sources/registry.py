"""
信息源装配与采集 / Source assembly and collection.

按 `config/sources.yaml` 构造 adapter，并**逐源隔离错误**地跑一轮采集。
Builds adapters from `config/sources.yaml` and runs one collection round with
per-source error isolation.

为什么必须隔离错误 / Why isolation is mandatory:
    日报每天自动跑一次。RSS 源随时可能挂掉——站点改版、证书过期、临时 502。
    若一个源抛异常就中断整轮，等于「任何一个源出问题，当天就没有日报」。
    正确的行为是记下失败、带着其余源继续。
    The digest runs unattended once a day, and feeds break constantly: sites get
    redesigned, certificates expire, servers return 502. If one exception aborted
    the round, a single broken feed would mean no digest that day. The right
    behaviour is to record the failure and carry on with the remaining sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dna.core.config import Settings, SourceConfig, get_settings, load_sources
from dna.core.errors import SourceError
from dna.core.logging import get_logger
from dna.core.models import RawItem, SourceKind
from dna.sources.base import SourceAdapter
from dna.sources.rss import RSSAdapter
from dna.sources.rsshub import RSSHubAdapter
from dna.sources.user_link import UserLinkSource

logger = get_logger("sources.registry")


def build_adapter(config: SourceConfig, settings: Settings | None = None) -> SourceAdapter:
    """
    按配置构造一个 adapter / Build one adapter from its configuration.

    抛出 / Raises:
        SourceError: 源类型不受支持
    """
    s = settings or get_settings()

    if config.kind is SourceKind.RSS:
        return RSSAdapter(config)
    if config.kind is SourceKind.RSSHUB:
        return RSSHubAdapter(config, base_url=s.rsshub_base_url)
    if config.kind in (SourceKind.INBOX, SourceKind.GUI):
        return UserLinkSource(config, via=config.kind)

    raise SourceError(f"不支持的源类型：{config.kind} / unsupported source kind")


def build_adapters(
    configs: list[SourceConfig] | None = None, settings: Settings | None = None
) -> list[SourceAdapter]:
    """
    构造全部启用的 adapter / Build every enabled adapter.

    单个源配置有问题只跳过它，不影响其余源。
    A broken entry is skipped rather than taking the others down with it.
    """
    s = settings or get_settings()
    entries = configs if configs is not None else load_sources()

    adapters: list[SourceAdapter] = []
    for config in entries:
        try:
            adapters.append(build_adapter(config, s))
        except SourceError as exc:
            logger.warning("跳过信息源 %s：%s", config.id, exc)
    return adapters


@dataclass
class SourceFailure:
    """一个源的失败记录 / A record of one source's failure."""

    source_id: str
    source_name: str
    reason: str


@dataclass
class CollectResult:
    """
    一轮采集的结果 / The outcome of one collection round.

    成功与失败都要带出来：GUI 需要显示「12 个源成功、1 个失败」，
    台账也要记录当天有哪些源没取到，否则条目变少时无从解释。
    Both successes and failures are surfaced: the GUI shows "12 succeeded, 1 failed",
    and the ledger records which sources came up empty — otherwise a short digest has
    no explanation.
    """

    items: list[RawItem] = field(default_factory=list)
    failures: list[SourceFailure] = field(default_factory=list)
    per_source: dict[str, int] = field(default_factory=dict)

    @property
    def ok_count(self) -> int:
        """成功的源数量 / Number of sources that succeeded."""
        return len(self.per_source)

    @property
    def total(self) -> int:
        """采集到的条目总数 / Total number of items collected."""
        return len(self.items)

    def summary(self) -> str:
        """一行摘要，用于日志与 CLI / A one-line summary for logs and the CLI."""
        text = f"{self.total} 条，来自 {self.ok_count} 个源"
        if self.failures:
            text += f"；{len(self.failures)} 个源失败"
        return text


def collect(
    adapters: list[SourceAdapter] | None = None,
    *,
    settings: Settings | None = None,
    limit_per_source: int | None = None,
) -> CollectResult:
    """
    跑一轮采集 / Run one collection round.

    **逐源隔离**：任何一个源抛出异常都会被记进 failures，不影响其余源。
    Per-source isolation: an exception from any source is recorded in failures and
    does not affect the others.
    """
    s = settings or get_settings()
    targets = adapters if adapters is not None else build_adapters(settings=s)
    cap = limit_per_source if limit_per_source is not None else s.max_items_per_source

    result = CollectResult()

    for adapter in targets:
        try:
            items = adapter.fetch(limit=cap)
        except SourceError as exc:
            logger.warning("信息源 %s 采集失败：%s", adapter.id, exc)
            result.failures.append(SourceFailure(adapter.id, adapter.name, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - 兜底：任何异常都不能中断整轮采集
            logger.exception("信息源 %s 抛出未预期异常", adapter.id)
            result.failures.append(
                SourceFailure(adapter.id, adapter.name, f"未预期异常 {type(exc).__name__}: {exc}")
            )
            continue

        result.items.extend(items)
        result.per_source[adapter.id] = len(items)
        logger.debug("信息源 %s 采集到 %d 条", adapter.id, len(items))

    logger.info("采集完成：%s", result.summary())
    return result


__all__ = [
    "CollectResult",
    "SourceFailure",
    "build_adapter",
    "build_adapters",
    "collect",
]
