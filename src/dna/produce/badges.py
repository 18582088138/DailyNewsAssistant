"""
工作台上的「NEW」标识 / The workbench's NEW badge.

这是 `produce/` 里唯一**与产物内容无关**的判断：它只看来源与有没有产物记录。
单独放一个文件，是因为它和生成、落盘、计费都不在一条线上。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from dna.core.logging import get_logger
from dna.store.ledger import ArticleRecord

logger = get_logger("produce.service")

# 算「人工投递」的来源 / the sources that count as manually submitted
#
# 界面上粘的链接与飞书投进来的，都是**人特意挑出来要处理的**；RSS 抓来的是候选池。
# NEW 标识区分的正是这两者，见 `is_new_article`。
# A pasted or messaged link was deliberately chosen; the RSS feed is a candidate pool.
MANUAL_SOURCES = frozenset({"gui", "inbox"})

# 订阅导入的 NEW 标识挂多久 / how long a subscription import stays badged
#
# 只用于「或」的右边（见 `is_new_article`）：它只能加标识、拿不掉。
# Used only as an OR: it can add the badge, never remove one.
NEW_WINDOW_HOURS = 24.0

def is_new_article(
    record: ArticleRecord,
    productions: dict,
    *,
    now: datetime | None = None,
    window_hours: float = NEW_WINDOW_HOURS,
) -> bool:
    """
    这篇是「刚进来、还没动过」的吗 / Is this a freshly arrived, untouched article?

    先要**一条产物记录都没有**（包括失败的那种），然后满足其一 / No production record
    at all, then either of:
        1. **是人工投递进来的**（界面粘的链接或飞书投的）——不看时间，一直挂着
        2. 或者 `first_seen_at` 在 `window_hours` 之内——**刚从订阅导进来的那批**

    标识**一直挂着，直到对它调过一次 LLM 为止**。
    The badge stays until an LLM has been called for the article.

    为什么第 2 条算上失败的 / Why a failed attempt also clears the flag:
        失败的记录说明**已经调过 LLM 了**（钱已经花了），这篇不再是「没动过」。
        而且失败的格子本身就显示 ▲，和 NEW 摆在一起是自相矛盾的信号：
        一个说「还没开始」，一个说「试过而且出错了」。
        A failure record means the LLM was already called and the money already spent, so
        the article is no longer untouched. The cell also already shows a failure glyph,
        and pairing it with NEW would send contradicting signals.

    时间窗是「或」，不是「改」 / The window is an OR, never a replacement:
        **纯时间窗**曾经用过并被否掉：它让昨天粘进来、今天还没处理的链接第二天就
        失去标识——而那恰恰是最需要标识的一条（实测 `fe3b7d3c` 导入 47 小时、
        零产物，正是这样丢掉的）。那个理由针对的是「窗口能把标识**拿掉**」。
        现在窗口只出现在 `or` 的右边：它只能**加**标识，人工投递那条判据一个字没动，
        于是否掉纯时间窗的理由不再适用。
        加它是因为订阅导入的那批一条标识都没有，人分不出哪些是这次新到的。
        The pure window was tried and rejected because it *removed* the badge from a
        still-untouched pasted link. As an OR it can only *add* one, leaving the manual
        rule untouched — so that objection no longer applies. It exists because a
        subscription import otherwise arrives entirely unmarked.
    """
    if productions:
        return False
    if record.via in MANUAL_SOURCES:
        return True
    if record.first_seen_at is None:
        return False
    return record.first_seen_at >= (now or datetime.now()) - timedelta(hours=window_hours)
