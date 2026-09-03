"""
文章落盘 / Article persistence.

把抓到的正文与配图存到磁盘，**让人能直接打开核对抓得对不对**。
Writes the extracted body and images to disk so the results can be opened and checked
by hand.

目录结构 / Layout:
    data/articles/<YYYYMMDD>/<slug>__<id8>/
    ├── article.md      正文（带 YAML frontmatter，可直接阅读）
    ├── meta.json       完整的 Article 结构，供程序复用
    ├── references.md   本篇的全部来源链接与图片出处
    └── images/
        ├── 01_<host>.jpg
        └── 01_<host>.jpg.json   ← 每张图的来源与署名

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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dna.core.logging import get_logger
from dna.core.models import Article, MediaAsset, MediaKind
from dna.core.naming import slugify
from dna.core.urls import host_of
from dna.extract.media import looks_like_image_url
from dna.sources.http import fetch_bytes
from dna.store.video_store import DEFAULT_MAX_VIDEOS, download_videos

logger = get_logger("store.article_store")

DEFAULT_MAX_IMAGES = 10  # 见 extract/media.py 的说明：多存是为了攒素材
MIN_IMAGE_BYTES = 8 * 1024  # 小于 8KB 的多半是图标或占位图
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# 降级文章里的正文粘贴位置标记 / where the body goes in a degraded article
# 抓不到正文的站点（需登录、强反爬）由人工补，`dna sync` 从这个标记之后读回。
# For sites whose body cannot be fetched the text is pasted by hand, and `dna sync`
# reads everything after this marker back into the ledger.
MANUAL_BODY_MARKER = "<!-- 正文粘贴区 / paste the article body below this line -->"

# 常见图片扩展名 / recognised image extensions
_EXT_BY_SIGNATURE = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG": ".png",
    b"GIF8": ".gif",
    b"RIFF": ".webp",
}


@dataclass
class SavedArticle:
    """一次落盘的结果 / The result of persisting one article."""

    directory: Path
    article_path: Path
    meta_path: Path
    references_path: Path
    image_paths: list[Path] = field(default_factory=list)
    skipped_images: list[tuple[str, str]] = field(default_factory=list)
    video_paths: list[Path] = field(default_factory=list)
    skipped_videos: list[tuple[str, str]] = field(default_factory=list)

    @property
    def image_count(self) -> int:
        return len(self.image_paths)

    @property
    def video_count(self) -> int:
        return len(self.video_paths)


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
        root:               数据根目录（通常是 settings.data_path）
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
# 渲染 / rendering
# ---------------------------------------------------------------------------


def _render_markdown(article: Article, article_id: str) -> str:
    """
    渲染成带 frontmatter 的 Markdown / Render Markdown with a YAML frontmatter block.

    frontmatter 让文件既能被人直接读，也能被程序解析出元信息，
    不用另开一个文件。
    The frontmatter keeps the file readable by a person while still carrying metadata a
    program can parse, avoiding a second file.
    """
    lines = [
        "---",
        f"id: {article_id}",
        f"title: {_yaml_scalar(article.title)}",
        f"url: {article.url}",
        f"author: {_yaml_scalar(article.author or '')}",
        f"published_at: {article.published_at.isoformat() if article.published_at else ''}",
        f"extracted_at: {article.extracted_at.isoformat(timespec='seconds')}",
        f"extraction_ok: {str(article.extraction_ok).lower()}",
        f"text_length: {len(article.text)}",
        f"media_count: {len(article.media)}",
        "---",
        "",
        f"# {article.title}",
        "",
        f"> 来源：{article.url}",
        "",
    ]

    if not article.extraction_ok:
        lines += [
            "> ⚠️ **正文抽取降级**：只拿到标题与链接。",
            "> 该站点可能需要登录、有反爬，或页面结构特殊。",
            "",
            MANUAL_BODY_MARKER,
            "",
            "_把原文正文粘贴到这一行下面，保存后执行 `dna sync "
            f"{article_id[:8]}` 回写台账。_",
            "",
        ]

    lines.append(article.text if article.text else "")
    lines.append("")
    return "\n".join(lines)


def _render_references(article: Article, saved: SavedArticle) -> str:
    """
    渲染来源清单 / Render the reference list.

    每张图都列出它的原始地址与所属文章，供发布时标注出处。
    Each image lists its original address and the article it came from, for attribution
    at publishing time.
    """
    lines = [f"# 来源 / References — {article.title}", "", f"- 原文：{article.url}"]

    if article.author:
        lines.append(f"- 作者：{article.author}")

    images = [m for m in article.media if m.kind is MediaKind.IMAGE]
    videos = [m for m in article.media if m.kind is MediaKind.VIDEO]

    if saved.image_paths:
        lines += ["", "## 已下载的配图 / Downloaded images", ""]
        for path, asset in zip(saved.image_paths, images, strict=False):
            credit = f"（来源：{asset.credit}）" if asset.credit else ""
            lines.append(f"- `{path.name}` ← {asset.url} {credit}")
            if asset.caption:
                lines.append(f"  - 说明：{asset.caption}")

    if saved.skipped_images:
        lines += ["", "## 未下载的图片 / Skipped images", ""]
        lines += [f"- {url} —— {reason}" for url, reason in saved.skipped_images]

    if saved.video_paths:
        lines += ["", "## 已下载的视频 / Downloaded videos", ""]
        for path, asset in zip(saved.video_paths, videos, strict=False):
            size_mb = path.stat().st_size / (1024 * 1024)
            lines.append(f"- `videos/{path.name}`（{size_mb:.1f} MB） ← {asset.url}")

    if saved.skipped_videos:
        lines += ["", "## 未下载的视频 / Skipped videos", ""]
        lines += [f"- {url} —— {reason}" for url, reason in saved.skipped_videos]

    # 没下载成功的也要把地址列出来，至少人能点开看
    # List the addresses even when nothing downloaded, so the clip is still reachable.
    undownloaded = [v for v in videos if not saved.video_paths]
    if undownloaded and not saved.skipped_videos:
        lines += ["", "## 官方视频 / Official videos", ""]
        lines += [f"- {v.url}" for v in undownloaded]

    lines.append("")
    return "\n".join(lines)


def read_body(article_path: Path) -> str:
    """
    从 article.md 读回正文 / Read the body back out of article.md.

    优先取「正文粘贴区」标记之后的内容——降级文章由人工补正文，补在标记之下；
    没有标记的（正常抓取的文章）则取来源行之后的全部内容。
    Content after the paste marker wins: a degraded article gets its body filled in by
    hand below that marker. Without a marker — a normally extracted article — everything
    after the source line is taken.

    抛出 / Raises:
        OSError: 文件不存在或不可读
    """
    raw = article_path.read_text(encoding="utf-8")

    if MANUAL_BODY_MARKER in raw:
        tail = raw.split(MANUAL_BODY_MARKER, 1)[1]
        # 丢掉紧随其后的操作提示行（以 _ 包裹的斜体），它不是正文
        lines = [ln for ln in tail.splitlines() if not _is_hint_line(ln)]
        return "\n".join(lines).strip()

    body = raw
    if body.startswith("---"):
        parts = body.split("\n---\n", 1)
        body = parts[1] if len(parts) == 2 else body

    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") or stripped.startswith("> "):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def read_title(article_path: Path) -> str:
    """
    从 article.md 读回标题 / Read the title back out of article.md.

    取第一个一级标题。人工补正文时通常会顺手把标题也改对——抓取失败的文章标题是
    从 URL 推出来的（`zhuanlan.zhihu.com p 2067…`），根本不能直接当日报标题用。
    The first level-one heading is taken. Whoever pastes the body usually fixes the title
    too: a failed fetch derives its title from the URL, which is unusable in a digest.

    抛出 / Raises:
        OSError: 文件不存在或不可读
    """
    for line in article_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _is_hint_line(line: str) -> bool:
    """判断是否是给人看的操作提示行 / Whether the line is an instruction, not body text."""
    stripped = line.strip()
    return stripped.startswith("_把原文正文粘贴") or stripped == "_（无正文）_"


def _yaml_scalar(text: str) -> str:
    """
    转成安全的 YAML 标量 / Turn text into a safe YAML scalar.

    标题里常有冒号（「发布：新一代模型」），不加引号会让 frontmatter 解析失败。
    Titles routinely contain colons, which would break the frontmatter if unquoted.
    """
    cleaned = " ".join((text or "").split()).replace('"', "'")
    return f'"{cleaned}"'


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
        except Exception as exc:  # noqa: BLE001 - 单张图失败不影响其它图与正文
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
