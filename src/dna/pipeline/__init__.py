"""
流水线层 / Pipeline layer：清洗 → 去重聚类 → 打分 → 摘要 → 翻译 → 趋势 → Digest。

    from dna.pipeline import run_daily, clean_all

这是**全公用的核心算法层**：三个发布应用（图文 / 视频 / 播客）都消费它产出的
同一份 `DailyDigest`，谁都不许往回写。
This is the shared core: the graphic, video and podcast apps all consume the one
`DailyDigest` it produces, and none of them may write back into it.

费用分界 / The cost boundary:
    `clean` / `dedup` / `score` 不调用 LLM，可随意运行；
    `summarize` / `translate` / `trend` 每次运行都计费。
    `run_daily(dry_run=True)` 只跑前半段，用于先确认选题再花钱。
    The first three nodes are free to run; the last three bill on every run.
    `run_daily(dry_run=True)` stops at the boundary so the selection can be confirmed
    before paying for it.

命名遮蔽提示 / Name shadowing note:
    `clean` / `dedup` / `score` / `summarize` / `translate` / `trend` 既是子模块名，
    也有同名的函数从这里重导出。`from dna.pipeline import dedup` 拿到的是**函数**；
    要拿模块请写 `from dna.pipeline.dedup import ...` 或 `importlib.import_module`。
    These names are both submodules and re-exported functions. Importing the bare name
    from the package yields the function; use the fully qualified module path when the
    module object itself is needed.
"""

from dna.pipeline.clean import clean_all, clean_text, normalize_text, to_news_item
from dna.pipeline.dedup import DedupResult, dedup, is_near_duplicate, simhash
from dna.pipeline.flow import PipelineReport, run_daily
from dna.pipeline.score import ScoreBreakdown, needs_video, rank_clusters, score_cluster
from dna.pipeline.source import load_candidates

__all__ = [
    "DedupResult",
    "PipelineReport",
    "ScoreBreakdown",
    "clean_all",
    "clean_text",
    "dedup",
    "is_near_duplicate",
    "load_candidates",
    "needs_video",
    "normalize_text",
    "rank_clusters",
    "run_daily",
    "score_cluster",
    "simhash",
    "to_news_item",
]
