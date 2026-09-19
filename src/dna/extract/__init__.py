"""
抽取层 / Extraction layer：把 HTML 变成正文与媒体资产。

    from dna.extract import extract_article, fetch_article, extract_media

抽取失败时降级为「仅标题 + 链接」而不是抛异常——理由见 article.py 模块文档。
Extraction failures degrade to "title and link only" rather than raising; see article.py.
"""

from dna.extract.article import MIN_EXTRACTED_CHARS, extract_article, fetch_article
from dna.extract.media import extract_media, looks_like_image_url

__all__ = [
    "MIN_EXTRACTED_CHARS",
    "extract_article",
    "extract_media",
    "fetch_article",
    "looks_like_image_url",
]
