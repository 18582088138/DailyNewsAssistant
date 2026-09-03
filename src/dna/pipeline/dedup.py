"""
去重与聚类 / De-duplication and clustering.

把同一件事的多篇报道合并成一个 `Cluster`，日报里只出现一次，
但保留全部来源链接。
Merges multiple reports of one event into a single `Cluster` so the digest mentions it
once while keeping every source link.

三级递进 / Three tiers, cheapest first:
    1. **规范 URL 完全相同** —— 同一篇文章的不同分享链接（带不同 utm 参数）
    2. **SimHash 近似** —— 同一篇稿子被多个号转发，正文几乎一致
    3. **语义向量聚类**（可选）—— 不同媒体各自写的同一件事，用词不同但说的是一回事

为什么分三级而不是直接上向量 / Why tiers instead of going straight to embeddings:
    前两级是纯计算、零依赖、毫秒级；第三级要加载一个 GB 级模型。绝大多数重复属于
    前两级，先便宜后昂贵能让常见情况快得多。第三级缺失时前两级照常工作——
    **降级不是失败**，只是合并得少一些。
    The first two tiers are pure computation, dependency-free and take milliseconds; the
    third loads a gigabyte-scale model. Most duplicates are caught by the first two, so
    ordering cheap before expensive makes the common case far faster. When the third tier
    is unavailable the first two still run: degrading merges less, it does not fail.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from dna.core.logging import get_logger
from dna.core.models import Cluster, NewsItem

logger = get_logger("pipeline.dedup")

# SimHash 位数与判重阈值 / SimHash width and the near-duplicate threshold
SIMHASH_BITS = 64
DEFAULT_HAMMING_THRESHOLD = 3
"""
64 位 SimHash 上，汉明距离 ≤3 视为近重复。

这个阈值是保守的：宁可漏合并（日报里出现两条相似新闻，读者自己看得出来），
也不要错合并（把两件不同的事混成一条，读者会被误导且无从察觉）。
Conservative on purpose: missing a merge shows two similar items the reader can spot,
whereas a wrong merge fuses two different events into one and misleads invisibly.
"""

# 分词：中文按字，英文与数字按词 / tokenisation: CJK by character, latin by word
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+|[一-鿿]")


@dataclass
class DedupResult:
    """
    一轮去重的结果 / The outcome of one de-duplication pass.

    计数要能对上账：`raw_count` = 全部成员数，`len(clusters)` = 合并后条数。
    The counts must reconcile: raw_count is the total member count and len(clusters) is
    how many remain after merging.
    """

    clusters: list[Cluster] = field(default_factory=list)
    raw_count: int = 0
    merged_by_url: int = 0
    merged_by_simhash: int = 0
    merged_by_vector: int = 0

    @property
    def merged_total(self) -> int:
        """被合并掉的条数 / How many items were folded into another."""
        return self.merged_by_url + self.merged_by_simhash + self.merged_by_vector

    def summary(self) -> str:
        """一行摘要 / A one-line summary."""
        parts = [f"{self.raw_count} 条 → {len(self.clusters)} 个事件"]
        if self.merged_by_url:
            parts.append(f"同链接合并 {self.merged_by_url}")
        if self.merged_by_simhash:
            parts.append(f"近重复合并 {self.merged_by_simhash}")
        if self.merged_by_vector:
            parts.append(f"语义合并 {self.merged_by_vector}")
        return "，".join(parts)


# ---------------------------------------------------------------------------
# SimHash
# ---------------------------------------------------------------------------


def tokenize(text: str) -> list[str]:
    """
    分词 / Tokenise.

    中文按单字切、英文按词切。用单字而不是引入 jieba：SimHash 靠的是大量特征的
    统计叠加，单字粒度已经足够稳定，多一个分词依赖不值得。
    CJK is split per character and latin per word. Single characters rather than a
    segmenter such as jieba: SimHash relies on the statistical superposition of many
    features, for which character granularity is already stable enough to not justify
    another dependency.
    """
    return _TOKEN_RE.findall(text.lower())


def simhash(text: str, *, bits: int = SIMHASH_BITS) -> int:
    """
    计算 SimHash 指纹 / Compute a SimHash fingerprint.

    用 2-gram 而不是单 token 作特征：单 token 会让「AI 发布模型」和
    「模型发布 AI」得到相同指纹，词序完全丢失。
    Features are 2-grams rather than single tokens: with single tokens, "AI releases
    model" and "model releases AI" would hash identically, losing word order entirely.

    >>> simhash("") == 0
    True
    """
    tokens = tokenize(text)
    if not tokens:
        return 0

    grams = [f"{a}{b}" for a, b in zip(tokens, tokens[1:], strict=False)] or tokens

    vector = [0] * bits
    for gram in grams:
        # 用内置 hash 不行——它每次进程启动都加盐，指纹跨进程不稳定
        # The built-in hash is unusable: it is salted per process, so fingerprints
        # would not be stable across runs.
        h = int.from_bytes(hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest(), "big")
        for i in range(bits):
            vector[i] += 1 if (h >> i) & 1 else -1

    fingerprint = 0
    for i in range(bits):
        if vector[i] > 0:
            fingerprint |= 1 << i
    return fingerprint


def hamming(a: int, b: int) -> int:
    """两个指纹的汉明距离 / Hamming distance between two fingerprints."""
    return (a ^ b).bit_count()


def is_near_duplicate(a: str, b: str, *, threshold: int = DEFAULT_HAMMING_THRESHOLD) -> bool:
    """两段文本是否近重复 / Whether two texts are near-duplicates."""
    return hamming(simhash(a), simhash(b)) <= threshold


# ---------------------------------------------------------------------------
# 聚类 / clustering
# ---------------------------------------------------------------------------


def dedup(
    items: list[NewsItem],
    *,
    threshold: int = DEFAULT_HAMMING_THRESHOLD,
    use_embeddings: bool = False,
    embedding_threshold: float = 0.86,
) -> DedupResult:
    """
    把条目聚成事件 / Group items into events.

    参数 / Args:
        threshold:           SimHash 汉明距离阈值，越小越严格
        use_embeddings:      是否启用第三级语义聚类（需要 sentence-transformers 与模型）
        embedding_threshold: 余弦相似度阈值，超过则视为同一事件

    每个 cluster 的第一个成员是代表条目。**代表条目取正文最长的那篇**——
    多源报道同一件事时，内容最全的那篇最适合拿来做摘要。
    The first member of each cluster is its representative, chosen as the longest body:
    when several outlets cover one event, the fullest account summarises best.
    """
    result = DedupResult(raw_count=len(items))
    if not items:
        return result

    # -- 第一级：规范 URL 完全相同 -----------------------------------------
    by_url: dict[str, list[NewsItem]] = {}
    for item in items:
        by_url.setdefault(item.canonical_url, []).append(item)
    result.merged_by_url = len(items) - len(by_url)

    groups: list[list[NewsItem]] = list(by_url.values())

    # -- 第二级：SimHash 近重复 --------------------------------------------
    fingerprints: list[int] = [simhash(_signature(g[0])) for g in groups]
    merged: list[list[NewsItem]] = []
    merged_prints: list[int] = []

    for group, print_ in zip(groups, fingerprints, strict=True):
        target = _find_near(merged_prints, print_, threshold)
        if target is None:
            merged.append(list(group))
            merged_prints.append(print_)
        else:
            merged[target].extend(group)
            result.merged_by_simhash += 1

    # -- 第三级：语义向量（可选）-------------------------------------------
    if use_embeddings and len(merged) > 1:
        merged, vector_merges = _merge_by_embeddings(merged, embedding_threshold)
        result.merged_by_vector = vector_merges

    result.clusters = [_build_cluster(group) for group in merged]
    logger.info("去重完成：%s", result.summary())
    return result


def _signature(item: NewsItem) -> str:
    """
    参与相似度比较的文本 / The text used for similarity comparison.

    标题加正文前 500 字。用全文反而更差：转载常常在文末追加不同的推广内容，
    把它们计入会让本来相同的两篇拉开距离。开头是最不容易被改动的部分。
    Title plus the first 500 characters of the body. Using the whole text is worse:
    reposts routinely append different promotional material at the end, which pushes
    otherwise identical articles apart. The opening is the least-modified part.
    """
    return f"{item.title}\n{item.text[:500]}"


def _find_near(prints: list[int], candidate: int, threshold: int) -> int | None:
    """在已有指纹里找一个近重复 / Find an existing fingerprint within the threshold."""
    for index, existing in enumerate(prints):
        if hamming(existing, candidate) <= threshold:
            return index
    return None


def _build_cluster(members: list[NewsItem]) -> Cluster:
    """
    构造一个 cluster，代表条目排在最前 / Build a cluster with its representative first.
    """
    ordered = sorted(members, key=lambda m: len(m.text), reverse=True)
    canonical = ordered[0]
    return Cluster(id=canonical.id, members=ordered, canonical_url=canonical.canonical_url)


# ---------------------------------------------------------------------------
# 第三级：语义向量聚类 / tier three, embedding clustering
# ---------------------------------------------------------------------------


def _merge_by_embeddings(
    groups: list[list[NewsItem]], threshold: float
) -> tuple[list[list[NewsItem]], int]:
    """
    用句向量合并「说同一件事但用词不同」的组 / Merge groups by semantic similarity.

    模型加载失败时**原样返回**，不抛异常：语义聚类是锦上添花，
    为它中断整期日报是本末倒置。
    On a model-loading failure the input is returned unchanged rather than raising:
    semantic clustering is an enhancement, and aborting the whole issue for it would be
    backwards.
    """
    try:
        embeddings = _encode([_signature(g[0]) for g in groups])
    except Exception as exc:  # noqa: BLE001 - 向量层不可用不该中断流水线
        logger.warning("语义聚类不可用，仅使用 URL 与 SimHash 去重：%s", exc)
        return groups, 0

    merged: list[list[NewsItem]] = []
    merged_vectors: list[list[float]] = []
    count = 0

    for group, vector in zip(groups, embeddings, strict=True):
        target = None
        for index, existing in enumerate(merged_vectors):
            if _cosine(existing, vector) >= threshold:
                target = index
                break

        if target is None:
            merged.append(list(group))
            merged_vectors.append(vector)
        else:
            merged[target].extend(group)
            count += 1

    return merged, count


def _encode(texts: list[str]) -> list[list[float]]:
    """
    文本转向量 / Encode texts into vectors.

    延迟导入：sentence-transformers 加载要好几秒，而绝大多数命令（fetch、list、
    show）根本不需要它。放在模块顶层会拖慢每一次 CLI 启动。
    Imported lazily: sentence-transformers takes seconds to load and most commands never
    need it. A top-level import would slow down every CLI invocation.
    """
    from dna.core.config import get_settings
    from sentence_transformers import SentenceTransformer

    model = _load_model(get_settings().embedding_model, SentenceTransformer)
    return [list(map(float, v)) for v in model.encode(texts, normalize_embeddings=True)]


_MODEL_CACHE: dict[str, object] = {}


def _load_model(name: str, factory):  # noqa: ANN001, ANN202 - 类型来自可选依赖
    """
    加载并缓存向量模型 / Load the embedding model once and reuse it.

    模型有 GB 级，每次调用重新加载会让一期日报多花几分钟。
    The model is gigabytes; reloading per call would add minutes to every issue.
    """
    if name not in _MODEL_CACHE:
        logger.info("加载向量模型：%s（首次会比较慢）", name)
        _MODEL_CACHE[name] = factory(name)
    return _MODEL_CACHE[name]


def _cosine(a: list[float], b: list[float]) -> float:
    """
    余弦相似度 / Cosine similarity.

    向量已在编码时归一化，所以点积就是余弦值，不用再除模长。
    Vectors are normalised at encode time, so the dot product is already the cosine.
    """
    return sum(x * y for x, y in zip(a, b, strict=True))


__all__ = [
    "DEFAULT_HAMMING_THRESHOLD",
    "SIMHASH_BITS",
    "DedupResult",
    "dedup",
    "hamming",
    "is_near_duplicate",
    "simhash",
    "tokenize",
]
