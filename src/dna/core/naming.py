"""
命名与路径规则 / Naming and path rules.

产物目录规范 / Output layout（权威说明见 `docs/05_output_spec.md`）:

    outputs/
    ├── 20260901-DailyNews/                     期次级：只放整期产物
    │   ├── _digest.json                        结构化事实源
    │   ├── _references.md                      全期来源汇总
    │   ├── graphic/                            场景1 整期图文
    │   └── podcast/                            场景3 整期播客
    └── articles/20260901/<slug>__<id8>/        条目级：单篇的全部资产

两级目录**按 id 相互引用，不复制文件**：一篇文章可以进多期，复制就会出现两份
会各自漂移的副本。期次目录的装配在 `store/issue_store.py`，条目目录在
`store/article_store.py`。
The two levels reference each other by id rather than copying: an article can appear in
several issues, and copies would drift apart.

本模块只负责「叫什么名字」，不做任何 I/O。
This module only decides names; it performs no I/O.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date as Date
from datetime import datetime

ISSUE_SUFFIX = "DailyNews"

# Windows 保留字符与保留设备名 / characters and device names Windows forbids
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# 连续的空白或分隔符 / runs of whitespace or separators
_SEPARATORS = re.compile(
    r"[\s_\-–—·、,，.。!！?？:：;；'\"“”‘’()（）\[\]【】{}<>《》/\\|+*#@&$%^~`=]+"
)

DEFAULT_SLUG_MAX_LEN = 40


def slugify(title: str, max_len: int = DEFAULT_SLUG_MAX_LEN) -> str:
    """
    把新闻标题转成可安全用作目录名的 slug，中英文混排都能处理。
    Turn a headline into a directory-safe slug; handles mixed Chinese/English text.

    规则 / Rules:
      1. Unicode 归一化（全角转半角等）/ normalise unicode (full-width to half-width)
      2. 剔除 Windows 非法字符与控制字符 / strip characters Windows forbids
      3. 各类分隔符统一折叠成单个 "-" / collapse all separators into a single hyphen
      4. 截断到 max_len，避免超出路径长度上限 / truncate to stay within path limits
      5. 结果为空或撞上 Windows 保留名时回退为 "untitled" / fall back when unusable

    中文字符予以保留（不做拼音转写），因为目录名要人眼可读。
    Chinese characters are kept as-is rather than romanised, so the folder stays readable.
    """
    text = unicodedata.normalize("NFKC", title or "").strip()
    text = _ILLEGAL_CHARS.sub("", text)
    text = _SEPARATORS.sub("-", text)
    text = text.strip("-")

    if len(text) > max_len:
        text = text[:max_len].rstrip("-")

    # Windows 不允许目录名以点或空格结尾 / Windows forbids trailing dots and spaces
    text = text.rstrip(". ")

    if not text or text.upper() in _WINDOWS_RESERVED:
        return "untitled"
    return text


def issue_dir_name(day: Date | datetime) -> str:
    """
    一期日报的目录名 / Directory name for one daily issue.

    >>> issue_dir_name(datetime.date(2026, 9, 1))
    '20260901-DailyNews'
    """
    if isinstance(day, datetime):
        day = day.date()
    return f"{day:%Y%m%d}-{ISSUE_SUFFIX}"


def lang_suffix_name(stem: str, lang: str, ext: str) -> str:
    """
    带语言后缀的文件名 / A filename carrying its language suffix.

    >>> lang_suffix_name("daily", "zh", "md")
    'daily.zh.md'
    """
    return f"{stem}.{lang}.{ext.lstrip('.')}"


__all__ = [
    "DEFAULT_SLUG_MAX_LEN",
    "ISSUE_SUFFIX",
    "issue_dir_name",
    "lang_suffix_name",
    "slugify",
]
