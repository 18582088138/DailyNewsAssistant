"""表格数据：一行长什么样、一页怎么取 / The table's row and page models。"""

from __future__ import annotations

from dataclasses import dataclass

from dna.core.config import get_settings
from dna.core.logging import get_logger
from dna.produce import (
    ProductionKind,
    is_new_article,
)
from dna.produce.tasks import normalize_lang
from dna.store import FetchStatus, Ledger, ProductionRecord
from dna.store.ledger import ArticleRecord

logger = get_logger("gui.actions")

@dataclass
class RowView:
    """
    表格里的一行 / One row of the workbench table.

    把「文章记录 + 该文章的全部产物」打包在一起，界面层就不必再各自查库。
    Bundles the article record with all of its productions so the view layer never
    queries the database itself.
    """

    article: ArticleRecord
    productions: dict[tuple[str, str], ProductionRecord]
    """按 `(产物类型, 语言)` 索引 / keyed by kind and language."""

    is_new: bool = False
    """
    刚导入、还没调过 LLM / freshly imported and never sent to an LLM.

    判定在 `dna.produce.is_new_article`，不在这里——「有没有产物」是产物层的知识，
    界面只负责把它画成一个标识。
    The predicate lives in `dna.produce.is_new_article`: whether anything has been
    produced is the production layer's knowledge, and the view only draws the badge.
    """

    def production(
        self, kind: ProductionKind, lang: str = ""
    ) -> ProductionRecord | None:
        # 必须归一化：字典的键来自库里的 `lang` 列（只有 `zh` / `en`），
        # 而 `""` 是「按配置的默认语言」这个意思的**哨兵**，不是一个键。
        # 不归一化就是每一格都查不到、整张表显示成「未生成」，而且完全静默。
        lang = normalize_lang(lang)
        return self.productions.get((str(kind), lang))

    def has_language(self, kind: ProductionKind, lang: str) -> bool:
        """该语言版本是否已经生成 / Whether that language edition exists."""
        record = self.production(kind, lang)
        return bool(record and record.ok)

    @property
    def media_label(self) -> str:
        """配图与视频数量 / Image and video counts."""
        video = f"·{self.article.video_count}▶" if self.article.video_count else ""
        return f"{self.article.image_count}图{video}" if self.article.image_count or video else "—"

    @property
    def body_label(self) -> str:
        """正文字数 / Body length."""
        return f"{self.article.text_len} 字" if self.article.text_len else "—"


@dataclass
class PageView:
    """
    一页表格 / One page of the table.

    页数在这里算好，界面只管画：分页控件与「共 N 条」如果各算各的，
    两个数字迟早不一致，而人只会觉得「这表是坏的」。
    Paging is computed here so the control and the counter cannot disagree.
    """

    rows: list[RowView]
    total: int
    page: int
    page_size: int
    pages: int
    scanned_cap: bool = False


# 后置筛选一次最多扫多少行 / how many rows a post-filter may scan
#
# `only_new` / `only_gaps` 要看产物矩阵，SQL 层数不出来，只能取一批回来在
# Python 里筛。扫上限存在是为了不让「勾一下开关」变成全表扫描；**撞到上限
# 必须说出来**——一个看起来「筛完了」而其实只筛了一部分的列表，比明说
# 「只筛了最近 1000 条」危险得多。
# Post-filters need the production matrix, so they scan a capped batch. Hitting the cap
# is surfaced: a list that looks complete but is not is worse than an honest bound.
SCAN_CAP = 1000


def load_rows(
    *,
    status: str | None = None,
    source: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    only_gaps: bool = False,
    only_new: bool = False,
) -> PageView:
    """
    读取一页表格数据 / Load one page of the table.

    **产物状态一次查完**（`production_matrix`），不是每行每格查一次——
    一页 50 行 × 5 种产物就是 250 次查询，界面会肉眼可见地卡。
    Production status is fetched in a single query rather than per cell: fifty rows times
    five kinds would be 250 round trips and the lag would be visible.

    两条路 / Two paths:
        无后置筛选 —— SQL 直接 LIMIT/OFFSET，总数走 `ledger.count()`
        有后置筛选 —— 取 `SCAN_CAP` 行在 Python 里筛完再切页（见 `SCAN_CAP`）
    """
    settings = get_settings()
    ledger = Ledger(settings.db_file)

    page = max(1, int(page))
    page_size = max(1, int(page_size))
    status_filter = FetchStatus(status) if status else None
    post_filtered = only_new or only_gaps

    if post_filtered:
        records = ledger.list(
            status=status_filter, source_id=source, search=search, limit=SCAN_CAP
        )
        scanned_cap = len(records) >= SCAN_CAP
    else:
        records = ledger.list(
            status=status_filter, source_id=source, search=search,
            limit=page_size, offset=(page - 1) * page_size,
        )
        scanned_cap = False

    matrix = ledger.production_matrix([r.id for r in records])

    rows = [
        RowView(
            article=r,
            productions=(prods := matrix.get(r.id, {})),
            is_new=is_new_article(r, prods),
        )
        for r in records
    ]

    if only_new:
        rows = [row for row in rows if row.is_new]

    if only_gaps:
        # 「有缺口的」= 常规四项里至少有一项没生成。长文案不算——它本来就是按需的，
        # 把它计入的话所有文章都会显示有缺口，筛选就失去意义了。
        # "Has gaps" means at least one of the four routine kinds is missing. The
        # long-form script is excluded: it is on-demand by design, and counting it would
        # mark every article as having a gap, making the filter useless.
        from dna.produce.tasks import batch_kinds

        rows = [
            row
            for row in rows
            if any(
                (p := row.production(k)) is None or not p.ok for k in batch_kinds()
            )
        ]

    if post_filtered:
        total = len(rows)
        rows = rows[(page - 1) * page_size : page * page_size]
    else:
        total = ledger.count(status=status_filter, source_id=source, search=search)

    pages = max(1, -(-total // page_size))  # 向上取整 / ceiling division
    return PageView(
        rows=rows, total=total, page=min(page, pages), page_size=page_size,
        pages=pages, scanned_cap=scanned_cap,
    )
