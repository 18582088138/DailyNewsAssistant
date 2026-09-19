"""
清洗归一化 / Cleaning and normalisation.

把台账里的文章记录变成流水线内部统一的 `NewsItem`。
Turns ledger records into the pipeline's uniform `NewsItem`.

这一层**不调用 LLM**，全部是纯函数，因此可以完全离线测试。
This layer calls no LLM and is entirely pure, so it tests completely offline.

它负责的三件事 / Its three jobs:
    1. 去掉正文里的样板噪声（「点击关注」「原文链接」「免责声明」…）
    2. 归一化空白与全角标点，让后续的相似度比较不被格式差异干扰
    3. 丢掉不具备成稿价值的条目（标题为空、正文与标题都太短）

为什么清洗要在去重之前 / Why cleaning precedes de-duplication:
    同一篇文章被两个号转发时，正文主体相同但各自带着不同的推广尾巴。
    不先剥掉尾巴，SimHash 会认为它们是两篇不同的文章。
    The same article reposted by two accounts shares its body but carries different
    promotional tails. Without stripping those first, SimHash reads them as distinct.
"""

from __future__ import annotations

import re

from dna.core.logging import get_logger
from dna.core.models import MediaAsset, NewsItem, SourceKind
from dna.core.urls import canonicalize_url, url_hash

logger = get_logger("pipeline.clean")

# 正文里没有信息量的样板行 / boilerplate lines that carry no information
# 整行匹配而不是子串匹配：「关注」两个字出现在正文里很正常
# （「值得关注的是…」），只有当它**独占一行**时才是推广语。
# Whole-line matching rather than substring: the word "follow" appears legitimately
# inside prose and is only promotional when it occupies a line by itself.
_BOILERPLATE_LINE_RE = re.compile(
    r"^\s*(?:"
    r"点击(?:上方)?[「\"']?.{0,20}[」\"']?关注"
    r"|长按识别.{0,20}二维码"
    r"|扫码(?:关注|加入|报名).{0,30}"
    r"|(?:点击)?阅读原文"
    r"|原文链接[:：]?"
    r"|本文(?:首发于|转载自|来源[:：]).{0,60}"
    r"|(?:版权|免责)声明[:：]?.{0,80}"
    r"|未经授权.{0,40}(?:转载|使用)"
    r"|欢迎(?:关注|订阅|投稿|转发).{0,40}"
    r"|(?:—{2,}|-{2,}|={2,}|\*{2,})"
    r"|advertisement"
    r"|sponsored content"
    r"|subscribe to our newsletter.{0,40}"
    r"|read more[:：]?"
    r")\s*$",
    re.IGNORECASE,
)

# 连续空行压成一个 / collapse runs of blank lines
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_SPACE_RUN_RE = re.compile(r"[ \t 　]+")

# 成稿门槛 / minimum viable content
MIN_TITLE_CHARS = 4

# 这里曾经有一个 `MIN_BODY_CHARS = 40`，**从来没有被任何代码读过**，
# 但它和 `extract/article.py` 里那个 80 同名 —— 两个不同的门槛长着同一个名字，
# 谁读到哪一个全看 import 了谁。删掉它，抽取那一侧改叫 `MIN_EXTRACTED_CHARS`。


# 需要折成半角的全角字符 / full-width characters folded to their ASCII form
# **刻意不用 NFKC 全量归一**：NFKC 会把中文逗号「，」折成 ASCII 逗号「,」，
# 而产物是要发到公众号和小红书的中文稿，混着半角逗号读起来就像机翻。
# 这里只折「本来就是 ASCII、只是被打成了全角」的那些字符。
# NFKC is deliberately not applied wholesale: it folds the Chinese comma into an ASCII
# one, and the output is Chinese copy for publication where that reads like machine
# translation. Only characters that are ASCII by nature but happened to be typed
# full-width are folded.
_FULLWIDTH_FOLD = str.maketrans(
    {
        **{chr(0xFF10 + i): chr(0x30 + i) for i in range(10)},  # ０-９ → 0-9
        **{chr(0xFF21 + i): chr(0x41 + i) for i in range(26)},  # Ａ-Ｚ → A-Z
        **{chr(0xFF41 + i): chr(0x61 + i) for i in range(26)},  # ａ-ｚ → a-z
        "　": " ",  # 全角空格
        "－": "-",  # －：出现在 GPT－4 这类型号里
        "～": "~",  # ～：出现在 20～25 秒这类范围里
    }
)


def normalize_text(text: str) -> str:
    """
    归一化文本 / Normalise text.

    全角字母数字（`ＡＩ`、`２０２６`）折成半角：它们与半角是同一个东西，
    不统一的话去重会漏判，摘要里也会混着两种宽度。
    Full-width letters and digits are folded: they mean the same as their half-width
    forms, and leaving both around defeats de-duplication and yields mixed-width text.

    **中文标点原样保留**——它属于内容，不属于格式。见 `_FULLWIDTH_FOLD` 的说明。
    Chinese punctuation is preserved: it is content, not formatting.
    """
    if not text:
        return ""

    normalized = text.translate(_FULLWIDTH_FOLD)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _SPACE_RUN_RE.sub(" ", normalized)
    lines = [line.strip() for line in normalized.split("\n")]
    return _BLANK_RUN_RE.sub("\n\n", "\n".join(lines)).strip()


def strip_boilerplate(text: str) -> str:
    """
    剥掉样板噪声行 / Remove boilerplate lines.

    只删整行命中的，绝不删半行：宁可留下一点噪声，也不能把正文切坏。
    Only whole matching lines are dropped, never part of a line: leaving a little noise
    is far better than cutting into the article body.
    """
    if not text:
        return ""

    kept = [line for line in text.split("\n") if not _BOILERPLATE_LINE_RE.match(line)]
    return _BLANK_RUN_RE.sub("\n\n", "\n".join(kept)).strip()


def clean_text(text: str) -> str:
    """归一化 + 去样板，一步到位 / Normalise and de-boilerplate in one step."""
    return strip_boilerplate(normalize_text(text))


def is_publishable(title: str, text: str) -> bool:
    """
    判断一条内容是否够格进入流水线 / Whether an item is worth processing.

    标题太短的直接丢；正文太短的**不丢**——抽取降级的文章只有标题和链接，
    但它仍然是一条真实资讯，读者点链接就能看。为一个技术问题丢掉真新闻不划算。
    Items with a too-short title are dropped. A short body is *not* grounds for dropping:
    a degraded extraction leaves only a title and a link, yet that is still a real news
    item the reader can open. Losing real news to a technical failure is a bad trade.
    """
    return len(title.strip()) >= MIN_TITLE_CHARS


def to_news_item(
    *,
    url: str,
    title: str,
    text: str = "",
    source_id: str = "",
    via: SourceKind = SourceKind.RSS,
    published_at=None,
    media: list[MediaAsset] | None = None,
) -> NewsItem | None:
    """
    构造一条清洗后的 NewsItem / Build one cleaned NewsItem.

    不合格时返回 None 而不是抛异常——一条脏数据不该中断整期日报。
    Returns None rather than raising: one dirty record must not abort the whole issue.
    """
    clean_title = normalize_text(title)
    body = clean_text(text)

    if not is_publishable(clean_title, body):
        logger.debug("丢弃不合格条目：%s（标题 %d 字）", url, len(clean_title))
        return None

    canonical = canonicalize_url(url)
    return NewsItem(
        id=url_hash(url),
        source_id=source_id,
        via=via,
        url=url,
        canonical_url=canonical,
        title=clean_title,
        text=body,
        published_at=published_at,
        media=list(media or []),
    )


def clean_all(records: list[dict]) -> list[NewsItem]:
    """
    批量清洗 / Clean a batch.

    输入是「字段字典」而不是某个具体的记录类型，好让台账记录、投递条目、
    测试样本都能直接喂进来，不必各写一套转换。
    The input is a plain field mapping rather than a concrete record type so ledger rows,
    inbox submissions and test fixtures can all feed in without bespoke adapters.
    """
    items: list[NewsItem] = []
    for record in records:
        item = to_news_item(
            url=record.get("url", ""),
            title=record.get("title", ""),
            text=record.get("text", ""),
            source_id=record.get("source_id", ""),
            via=record.get("via", SourceKind.RSS),
            published_at=record.get("published_at"),
            media=record.get("media"),
        )
        if item is not None:
            items.append(item)

    logger.info("清洗完成：%d 进 → %d 出", len(records), len(items))
    return items


__all__ = [
    "MIN_TITLE_CHARS",
    "clean_all",
    "clean_text",
    "is_publishable",
    "normalize_text",
    "strip_boilerplate",
    "to_news_item",
]
