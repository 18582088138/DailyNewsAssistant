"""
文章落盘 / Article persistence.

把抓到的正文与配图存到磁盘，**让人能直接打开核对抓得对不对**。
Writes the extracted body and images to disk so the results can be opened and checked
by hand.

目录结构 / Layout:
    outputs/articles/<YYYYMMDD>/<slug>__<id8>/
    ├── article.md          正文（带 YAML frontmatter，可直接阅读）
    ├── meta.json           完整的 Article 结构，供程序复用
    ├── references.md       本篇的全部来源链接与媒体出处
    ├── images/
    │   ├── 01_<host>.jpg
    │   └── 01_<host>.jpg.json   ← 每张图的来源与署名
    ├── videos/
    ├── summary.zh.md       中文总结         ┐
    ├── summary.en.md       英文总结         │ 由 dna.produce 生成，
    ├── shortvideo.zh.md    短视频文案 25~35s │ 每一项都可单独重做
    ├── narration.zh.md     口播文案 1~2min  │
    └── longform.zh.md/.json 长文案 10~15min ┘

放在 `outputs/` 而不是 `data/`：这里全是**产物**——要打开、要拷走、要发布的东西。
`data/` 留给程序自己的台账数据库与 LLM 缓存。
These are deliverables — opened, copied out and published — so they live under
`outputs/`, while `data/` keeps the ledger and cache that only the program cares about.

为什么图片要配一个同名 .json / Why each image carries a sidecar .json:
    产物要发到公众号、小红书等公开平台，每张图都必须能追溯出处。
    把出处写在图片旁边而不是只写在数据库里，是为了让「图片被单独拷走」时
    信息不丢——实际使用中图片一定会被单独拷来拷去。
    Published output needs traceable attribution for every image. Keeping the origin
    next to the file rather than only in the database means the information survives
    when an image is copied out on its own, which in practice always happens.

目录名带日期与 id 后缀：日期让人按天翻看，id 后缀保证不同文章同标题时不冲突。
The directory name carries the date for day-by-day browsing and an id suffix so two
articles sharing a title never collide.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from dna.core.logging import get_logger
from dna.core.models import Article, MediaAsset, MediaKind
from dna.core.naming import slugify
from dna.core.urls import host_of
from dna.extract.media import looks_like_image_url
from dna.sources.http import fetch_bytes
from dna.store.article_render import (
    MANUAL_BODY_MARKER,
    SavedArticle,
    _render_markdown,
    _render_references,
    read_body,
)
from dna.store.video_store import DEFAULT_MAX_VIDEOS, download_videos

logger = get_logger("store.article_store")

DEFAULT_MAX_IMAGES = 10  # 见 extract/media.py 的说明：多存是为了攒素材
MIN_IMAGE_BYTES = 8 * 1024  # 小于 8KB 的多半是图标或占位图
MAX_IMAGE_BYTES = 8 * 1024 * 1024


# 常见图片扩展名 / recognised image extensions
_EXT_BY_SIGNATURE = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG": ".png",
    b"GIF8": ".gif",
    b"RIFF": ".webp",
}



def article_dir(root: Path, article_id: str, title: str, when: datetime | None = None) -> Path:
    """
    计算一篇文章的落盘目录 / Work out the directory for one article.

    纯函数，不建目录 / A pure function; it creates nothing.
    """
    day = (when or datetime.now()).strftime("%Y%m%d")
    return root / "articles" / day / f"{slugify(title or 'untitled', max_len=40)}__{article_id[:8]}"


def save_article(
    article: Article,
    article_id: str,
    *,
    root: Path,
    when: datetime | None = None,
    download_images: bool = True,
    max_images: int = DEFAULT_MAX_IMAGES,
    download_videos_too: bool = True,
    max_videos: int = DEFAULT_MAX_VIDEOS,
) -> SavedArticle:
    """
    把一篇文章落盘 / Persist one article to disk.

    参数 / Args:
        root:               产物根目录（settings.output_path）
        download_images:    是否下载配图；关掉可以快速验证正文抽取
        max_images:         最多下载几张图
        download_videos_too: 是否下载官方视频（比图片慢得多，可单独关掉）
        max_videos:         最多下载几个视频

    图片与视频下载失败都不影响正文落盘——失败原因记在 `skipped_*` 里，
    正文本身仍然可用。
    A failed image or video download never blocks the body from being written; the
    reason is recorded in `skipped_*` and the text remains usable.
    """
    directory = article_dir(root, article_id, article.title, when)
    _remove_stale_dirs(root, article_id, keep=directory)
    directory.mkdir(parents=True, exist_ok=True)

    saved = SavedArticle(
        directory=directory,
        article_path=directory / "article.md",
        meta_path=directory / "meta.json",
        references_path=directory / "references.md",
    )

    saved.article_path.write_text(_render_markdown(article, article_id), encoding="utf-8")
    saved.meta_path.write_text(
        article.model_dump_json(indent=2, exclude_none=False), encoding="utf-8"
    )

    if download_images:
        images = [m for m in article.media if m.kind is MediaKind.IMAGE][:max_images]
        _clear_media_dir(directory / "images")
        _download_images(images, directory / "images", saved)

    if download_videos_too:
        videos = [m for m in article.media if m.kind is MediaKind.VIDEO]
        _clear_media_dir(directory / "videos")
        saved.video_paths, saved.skipped_videos = download_videos(
            videos, directory / "videos", max_videos=max_videos
        )

    saved.references_path.write_text(_render_references(article, saved), encoding="utf-8")
    logger.debug(
        "已落盘：%s（正文 %d 字，图 %d 张，视频 %d 个）",
        directory,
        len(article.text),
        saved.image_count,
        saved.video_count,
    )
    return saved


def _clear_media_dir(directory: Path) -> None:
    """
    重抓前清空媒体目录 / Empty a media directory before re-downloading.

    重抓写进同一个目录，但这次抽到的图可能比上次少（修好了过滤规则、站点删了图）。
    不清空的话，上一轮的文件会留在盘上，而 references.md 是按本轮结果重新生成的，
    于是出现「盘上 3 张、清单里 2 张」的孤儿文件——既没有出处可查，做素材时还会
    被误当成本篇的配图用出去。实测重抓 MiniMax 那篇时，被新过滤规则剔掉的作者头像
    就这样留了下来。
    A re-fetch writes into the same directory but may extract fewer images than last time
    — a fixed filter, or the site removing a picture. Without clearing, the previous
    run's files remain while references.md is regenerated from this run, leaving orphans
    that have no recorded attribution yet still look like this article's images.
    """
    if not directory.exists():
        return

    import shutil

    shutil.rmtree(directory, ignore_errors=True)


def _remove_stale_dirs(root: Path, article_id: str, *, keep: Path) -> None:
    """
    清掉同一篇文章遗留的旧目录 / Drop stale directories left by the same article.

    目录名带标题，标题一变（重抓拿到了正确标题、或修好了抽取 bug）就会换一个
    新目录，旧的留在盘上成为垃圾——同一个 id 出现两份，让人分不清哪份是新的。
    The directory name embeds the title, so whenever the title changes — a re-fetch
    recovering the real title, or an extraction fix — a new directory appears and the old
    one lingers as garbage, leaving two copies under one id with no way to tell which is
    current.

    只删「同 id、非当前」的目录，绝不碰别的文章。
    Only directories carrying this same id and not the current one are removed; nothing
    belonging to another article is ever touched.
    """
    import shutil

    articles_root = root / "articles"
    if not articles_root.is_dir():
        return

    suffix = f"__{article_id[:8]}"
    for day_dir in articles_root.iterdir():
        if not day_dir.is_dir():
            continue
        for candidate in day_dir.glob(f"*{suffix}"):
            if candidate.is_dir() and candidate.resolve() != keep.resolve():
                shutil.rmtree(candidate, ignore_errors=True)
                logger.info("清理同一文章的旧目录：%s", candidate)



# ---------------------------------------------------------------------------
# 图片下载 / image downloading
# ---------------------------------------------------------------------------


def _download_images(assets: list[MediaAsset], directory: Path, saved: SavedArticle) -> None:
    """
    下载配图并写出来源 sidecar / Download images and write their attribution sidecars.

    过滤掉过小的文件：抽取阶段只能按 HTML 属性判断尺寸，很多站点不写宽高，
    真正的图标要到下载后看字节数才认得出来。
    Files that are too small are dropped here: at extraction time only the declared HTML
    dimensions are available and many sites omit them, so genuine icons can only be
    recognised by byte size after downloading.
    """
    if not assets:
        return

    directory.mkdir(parents=True, exist_ok=True)

    for index, asset in enumerate(assets, start=1):
        try:
            # 带上原文地址作为 Referer：多数图床有防盗链，不带一律 403
            # The article URL goes out as Referer: most image hosts block hotlinking.
            data = fetch_bytes(
                asset.url, timeout=20, max_bytes=MAX_IMAGE_BYTES, referer=asset.source_url
            )
        except Exception as exc:
            saved.skipped_images.append((asset.url, f"下载失败：{_short(exc)}"))
            continue

        if len(data) < MIN_IMAGE_BYTES:
            saved.skipped_images.append((asset.url, f"过小（{len(data)} 字节），可能是图标"))
            continue

        extension = _guess_extension(data, asset.url)
        if extension is None:
            saved.skipped_images.append((asset.url, "无法识别的图片格式"))
            continue

        filename = f"{index:02d}_{_safe_host(asset.url)}{extension}"
        path = directory / filename
        path.write_bytes(data)

        # 出处写在图片旁边，图片被单独拷走时信息不丢
        path.with_suffix(path.suffix + ".json").write_text(
            json.dumps(
                {
                    "source_url": asset.source_url,
                    "image_url": asset.url,
                    "credit": asset.credit,
                    "caption": asset.caption,
                    "bytes": len(data),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        saved.image_paths.append(path)


def _guess_extension(data: bytes, url: str) -> str | None:
    """
    判断图片格式 / Determine the image format.

    先看文件头再看 URL 后缀：URL 后缀会骗人（`.php` 返回 jpeg、`.jpg` 返回 HTML
    错误页都很常见），文件头不会。
    The magic bytes are checked before the URL extension: extensions lie routinely — a
    `.php` endpoint serving JPEG, or a `.jpg` URL returning an HTML error page — while
    the header does not.
    """
    for signature, extension in _EXT_BY_SIGNATURE.items():
        if data.startswith(signature):
            return extension

    if looks_like_image_url(url):
        suffix = Path(url.split("?")[0]).suffix.lower()
        if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"):
            return ".jpg" if suffix == ".jpeg" else suffix

    return None


def _safe_host(url: str) -> str:
    """主机名转成可用作文件名的形式 / A hostname usable inside a filename."""
    return slugify(host_of(url).replace(".", "-"), max_len=30) or "img"


def _short(exc: Exception) -> str:
    """异常压成一行 / Squash an exception into one line."""
    text = " ".join(str(exc).split())
    return text if len(text) <= 100 else f"{text[:100]}…"


__all__ = [
    "DEFAULT_MAX_IMAGES",
    "MANUAL_BODY_MARKER",
    "MIN_IMAGE_BYTES",
    "SavedArticle",
    "article_dir",
    "read_body",
    "save_article",
]
