"""
条目过滤 / Item filtering.

在**抓正文之前**执行：一条被过滤掉的条目不会产生 HTTP 请求、不会下载图片、
不会占用存储，后续也不会进入 LLM 环节。过滤放在这一步，省下的是全链路的成本。
Filtering happens before bodies are fetched: a filtered item costs no HTTP request,
no image download, no storage, and never reaches the LLM stage. Filtering here saves
cost along the whole chain.

规则合并 / Rule composition:
    全局排除词（`config/profile.yaml` 的 exclude_keywords）对所有源生效，
    每个源可以在 `config/sources.yaml` 里追加自己的规则。
    The global exclude list applies to every source; each source may add its own rules.

纯函数，无 I/O，可完全离线测试。
Pure functions with no I/O, fully testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from dna.core.config import Profile, SourceConfig, SourceFilter
from dna.core.logging import get_logger
from dna.core.models import RawItem

logger = get_logger("sources.filters")


@dataclass
class FilterOutcome:
    """
    一轮过滤的结果 / The outcome of one filtering pass.

    被丢弃的条目也要带出来：用户需要知道「今天为什么只有 8 条」，
    而不是面对一个没有解释的短日报。
    Dropped items are surfaced too: the user needs to know why today's digest has only
    eight entries rather than being handed a short digest with no explanation.
    """

    kept: list[RawItem] = field(default_factory=list)
    dropped: list[tuple[RawItem, str]] = field(default_factory=list)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)

    def reasons(self) -> dict[str, int]:
        """按原因统计丢弃数量 / Count drops grouped by reason."""
        counts: dict[str, int] = {}
        for _, reason in self.dropped:
            key = reason.split("：")[0]
            counts[key] = counts.get(key, 0) + 1
        return counts


def build_filter(source: SourceConfig, profile: Profile | None = None) -> SourceFilter:
    """
    合并全局与源级过滤规则 / Merge the global and per-source filtering rules.

    全局排除词始终生效，源级规则在其之上追加；include 只取源级——
    全局层面强制「必须命中某关键词」会把大量正常资讯误杀。
    The global excludes always apply and the per-source rules are added on top.
    `include` is per-source only: enforcing "must match a keyword" globally would
    wrongly discard large amounts of legitimate news.
    """
    own = source.filters or SourceFilter()
    global_excludes = list(profile.exclude_keywords) if profile else []

    merged_excludes = list(dict.fromkeys([*global_excludes, *own.exclude]))

    return SourceFilter(
        include=own.include,
        exclude=merged_excludes,
        min_title_length=own.min_title_length,
        max_age_days=own.max_age_days,
    )


def should_keep(
    item: RawItem, rules: SourceFilter, *, now: datetime | None = None
) -> tuple[bool, str]:
    """
    判断单个条目是否保留 / Decide whether to keep a single item.

    返回 / Returns:
        (是否保留, 丢弃原因)；保留时原因为空串

    匹配在**标题 + 源自带摘要**上做，且大小写不敏感。只匹配标题会漏掉那些
    标题含蓄、摘要里才点明主题的条目。
    Matching runs over the title plus the feed's own summary, case-insensitively.
    Matching the title alone would miss items whose subject only appears in the summary.
    """
    haystack = f"{item.title}\n{item.summary_raw}".lower()

    for word in rules.exclude:
        if word.lower() in haystack:
            return False, f"命中排除词：{word}"

    if rules.include and not any(word.lower() in haystack for word in rules.include):
        return False, f"未命中任何关注词：{', '.join(rules.include[:5])}"

    if rules.min_title_length and len(item.title.strip()) < rules.min_title_length:
        return False, f"标题过短：{len(item.title.strip())} < {rules.min_title_length}"

    if rules.max_age_days is not None and item.published_at is not None:
        cutoff = (now or datetime.now()) - timedelta(days=rules.max_age_days)
        if item.published_at < cutoff:
            return False, f"过旧：{item.published_at:%Y-%m-%d} 早于 {cutoff:%Y-%m-%d}"

    return True, ""


def apply_filters(
    items: list[RawItem],
    rules: SourceFilter,
    *,
    now: datetime | None = None,
) -> FilterOutcome:
    """
    对一批条目应用过滤规则 / Apply the filtering rules to a batch of items.

    没有任何规则时直接全量保留，省掉逐条判断。
    With no rules configured every item is kept, skipping the per-item checks.
    """
    if rules.is_empty():
        return FilterOutcome(kept=list(items))

    outcome = FilterOutcome()
    for item in items:
        keep, reason = should_keep(item, rules, now=now)
        if keep:
            outcome.kept.append(item)
        else:
            outcome.dropped.append((item, reason))

    if outcome.dropped:
        logger.debug("过滤掉 %d 条：%s", outcome.dropped_count, outcome.reasons())

    return outcome


__all__ = ["FilterOutcome", "apply_filters", "build_filter", "should_keep"]
