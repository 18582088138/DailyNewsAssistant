"""
重要性打分与 need_video 判定 / Importance scoring and video flagging.

给每个事件打一个 0~1 的分，日报按分排序取前 N 条；同时标出哪些适合做短视频。
Scores each event from 0 to 1 so the digest can rank and take the top N, and flags the
ones worth turning into a short video.

**这一层不调用 LLM**，全部是可解释的规则。
This layer calls no LLM; every rule is explainable.

为什么规则而不是让 LLM 打分 / Why rules rather than asking an LLM to score:
    1. **可解释**——分数由哪几条规则贡献一目了然，调不对时知道该改哪
    2. **稳定**——同样的输入永远同样的分；LLM 打分每次都会飘，日报排序跟着抖
    3. **免费**——每条都问一次 LLM，几十条的成本不低，而排序本身并不需要语言理解
    LLM 更适合做摘要那种「必须理解内容」的活；排序是可以用信号量化的。
    Rules are explainable (it is obvious which rule contributed what), stable (the same
    input always scores the same, whereas an LLM's scores drift and the ordering jitters
    with them) and free. An LLM is better spent on summarising, which genuinely requires
    understanding; ranking can be quantified from signals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from dna.core.config import Profile
from dna.core.logging import get_logger
from dna.core.models import Cluster, MediaKind

logger = get_logger("pipeline.score")

# 这些值的**唯一来源是 `profile.tuning`**（`config/profile.yaml`）。
# 它们决定「日报收录什么」，写死在代码里等于把选题口味焊死；默认值在 `core/config.Tuning`。
# The live values come from `profile.tuning`; defaults live on the `Tuning` model.


@dataclass
class ScoreBreakdown:
    """
    一条评分的组成 / The make-up of one score.

    留着每一项的贡献值，而不是只给总分：日报排序不对时，能立刻看出是关键词
    没配好还是新鲜度算错了。只给一个总分等于没法调。
    Each component is retained rather than only the total: when the ordering looks wrong
    it is immediately visible whether the keywords or the freshness term is at fault. A
    bare total would be untunable.
    """

    total: float = 0.0
    keyword: float = 0.0
    sources: float = 0.0
    freshness: float = 0.0
    substance: float = 0.0
    media: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)

    def explain(self) -> str:
        """人类可读的评分解释 / A human-readable explanation."""
        parts = [
            f"关键词 {self.keyword:.2f}",
            f"多源 {self.sources:.2f}",
            f"新鲜度 {self.freshness:.2f}",
            f"篇幅 {self.substance:.2f}",
            f"配图 {self.media:.2f}",
        ]
        hit = f"（命中：{'、'.join(self.matched_keywords)}）" if self.matched_keywords else ""
        return f"{self.total:.3f} = {' + '.join(parts)}{hit}"


def score_cluster(
    cluster: Cluster,
    profile: Profile,
    *,
    now: datetime | None = None,
) -> ScoreBreakdown:
    """
    给一个事件打分 / Score one event.

    五个信号 / Five signals:
        关键词  命中 profile.focus_keywords 的程度——这是「我关心什么」的直接表达
        多源    几家媒体都报了，说明是真事件而不是一家的软文
        新鲜度  48 小时内线性衰减，昨天的新闻今天不该排在最前
        篇幅    正文长度，太短的多半是快讯占位
        配图    有图的更适合发布，图文/视频/小红书都要图
    """
    moment = now or datetime.now()
    breakdown = ScoreBreakdown()

    breakdown.keyword, breakdown.matched_keywords = _keyword_signal(cluster, profile)
    breakdown.sources = _source_signal(cluster, profile)
    breakdown.freshness = _freshness_signal(cluster, moment, profile)
    breakdown.substance = _substance_signal(cluster, profile)
    breakdown.media = _media_signal(cluster)

    tuning = profile.tuning
    breakdown.total = round(
        tuning.weight_keyword * breakdown.keyword
        + tuning.weight_multi_source * breakdown.sources
        + tuning.weight_freshness * breakdown.freshness
        + tuning.weight_substance * breakdown.substance
        + tuning.weight_media * breakdown.media,
        4,
    )
    return breakdown


def needs_video(cluster: Cluster, profile: Profile, score: float) -> bool:
    """
    判断是否适合做短视频 / Whether this event suits a short video.

    三个条件满足其一即可 / Any one of three conditions:
        1. 命中 profile.video_keywords（「发布」「开源」「融资」这类有画面感的事件）
        2. 有官方视频素材——素材现成，做起来最省力
        3. 分数很高（前排内容值得多做一种形态）

    刻意宽松：**漏标的代价大于误标**。误标了人在界面上取消勾选即可；
    漏标则那条内容根本不会出现在视频候选里，人不会主动去找。
    Deliberately permissive: a missed flag costs more than a false one. A false flag is
    unticked in the UI, whereas a missed one never appears among the video candidates at
    all and nobody goes looking for it.
    """
    window = profile.tuning.video_scan_chars
    text = f"{cluster.canonical.title}\n{cluster.canonical.text[:window]}".lower()

    if any(keyword.lower() in text for keyword in profile.video_keywords):
        return True

    if any(m.kind is MediaKind.VIDEO for m in cluster.canonical.media):
        return True

    return score >= 0.65


# ---------------------------------------------------------------------------
# 各信号 / individual signals
# ---------------------------------------------------------------------------


def _keyword_signal(cluster: Cluster, profile: Profile) -> tuple[float, list[str]]:
    """
    关键词命中度 / Degree of keyword match.

    标题命中算满分，正文命中算半分：标题里出现「多模态」说明这就是主题，
    正文里顺带提一句则弱得多。不区分的话，泛泛提及会挤掉真正相关的内容。
    A hit in the title counts fully, one in the body counts half: a keyword in the title
    means it is the subject, whereas a passing mention in the body is far weaker. Without
    that distinction, incidental mentions crowd out genuinely relevant items.
    """
    keywords = profile.focus_keywords
    if not keywords:
        return 0.0, []

    title = cluster.canonical.title.lower()
    body = cluster.canonical.text[: profile.tuning.focus_scan_chars].lower()

    hits = 0.0
    matched: list[str] = []
    for keyword in keywords:
        needle = keyword.lower()
        if needle in title:
            hits += 1.0
            matched.append(keyword)
        elif needle in body:
            hits += 0.5
            matched.append(keyword)

    # 除以 3 而不是除以关键词总数：配了 20 个关键词时命中 3 个已经很相关了，
    # 按总数归一会让所有条目的分都趋近于 0，排序失去区分度。
    # Divided by three rather than by the keyword count: with twenty keywords configured,
    # matching three already means highly relevant, and normalising by the total would
    # push every score toward zero and flatten the ranking.
    return min(hits / 3.0, 1.0), matched


def _source_signal(cluster: Cluster, profile: Profile) -> float:
    """
    多源报道程度 / How many outlets covered it.

    按不同的 source_id 计数，而不是成员数：同一个源发了两遍不算多源。
    Counted by distinct source_id rather than member count: one source publishing twice
    is not corroboration.
    """
    distinct = len({m.source_id for m in cluster.members if m.source_id})
    if distinct <= 1:
        return 0.0
    saturation = max(2, profile.tuning.source_saturation)
    return min((distinct - 1) / (saturation - 1), 1.0)


def _freshness_signal(cluster: Cluster, now: datetime, profile: Profile) -> float:
    """
    新鲜度 / Freshness.

    没有发布时间的**给 0.5 而不是 0**：很多 feed 不提供 pubDate，
    当成「很旧」会把整个源沉到底部，等于变相封杀。
    Items without a publication time score 0.5 rather than 0: many feeds omit pubDate,
    and treating "unknown" as "old" would sink an entire source to the bottom — an
    effective ban by accident.
    """
    published = cluster.canonical.published_at
    if published is None:
        return 0.5

    # feed 里偶尔有未来时间（时区处理错误），按最新处理
    age = now - published.replace(tzinfo=None) if published.tzinfo else now - published
    if age <= timedelta(0):
        return 1.0
    hours = profile.tuning.freshness_window_hours
    if age >= timedelta(hours=hours):
        return 0.0
    return 1.0 - (age.total_seconds() / (hours * 3600))


def _substance_signal(cluster: Cluster, profile: Profile) -> float:
    """正文篇幅 / Body length, saturating."""
    length = len(cluster.canonical.text)
    return min(length / max(1, profile.tuning.substance_saturation), 1.0)


def _media_signal(cluster: Cluster) -> float:
    """
    配图与视频 / Images and video.

    有视频给满分，有图给 0.6：三个发布场景都要素材，视频素材最稀缺也最值钱。
    Video scores fully and images 0.6: all three publishing formats need material, and
    video material is both the scarcest and the most valuable.
    """
    media = cluster.canonical.media
    if any(m.kind is MediaKind.VIDEO for m in media):
        return 1.0
    if any(m.kind is MediaKind.IMAGE for m in media):
        return 0.6
    return 0.0


# ---------------------------------------------------------------------------
# 批量 / batch
# ---------------------------------------------------------------------------


def rank_clusters(
    clusters: list[Cluster],
    profile: Profile,
    *,
    now: datetime | None = None,
    limit: int | None = None,
) -> list[tuple[Cluster, ScoreBreakdown, bool]]:
    """
    给全部事件打分并排序 / Score every event and rank them.

    返回 / Returns:
        [(cluster, 评分明细, need_video), …]，按分数从高到低

    `limit` 在**排序之后**截断，因此拿到的是分最高的 N 条，而不是碰巧靠前的 N 条。
    The limit is applied after sorting, so the result is the top N by score rather than
    whichever N happened to come first.
    """
    scored = [(c, score_cluster(c, profile, now=now)) for c in clusters]
    scored.sort(key=lambda pair: pair[1].total, reverse=True)

    if limit is not None:
        scored = scored[:limit]

    result = [(c, b, needs_video(c, profile, b.total)) for c, b in scored]
    logger.info(
        "打分完成：%d 个事件，其中 %d 个标记 need_video",
        len(result),
        sum(1 for _, _, v in result if v),
    )
    return result


__all__ = [
    "ScoreBreakdown",
    "needs_video",
    "rank_clusters",
    "score_cluster",
]
