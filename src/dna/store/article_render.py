"""
文章落盘时写出的两份 Markdown / The two Markdown files written on save.

`article.md`（正文，带 YAML frontmatter）与 `references.md`（本篇来源 +
下载下来的文件名）。抓取失败时正文里留一块**粘贴区**，`dna sync` 回写。

读回那一侧也在这里（`read_body` / `read_title`）：写与读必须对得上，
分在两个文件里迟早漂开。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dna.core.logging import get_logger
from dna.core.models import Article, MediaKind

logger = get_logger("store.article_render")

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
            (
                "_把原文正文粘贴到这一行下面，保存后执行 `dna sync "
                f"{article_id[:8]}` 回写台账。_"
            ),
            "",
        ]

    lines.append(article.text or "")
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
        if stripped.startswith(("# ", "> ")):
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

# 降级文章里的正文粘贴位置标记 / where the body goes in a degraded article
# 抓不到正文的站点（需登录、强反爬）由人工补，`dna sync` 从这个标记之后读回。
# For sites whose body cannot be fetched the text is pasted by hand, and `dna sync`
# reads everything after this marker back into the ledger.
MANUAL_BODY_MARKER = "<!-- 正文粘贴区 / paste the article body below this line -->"

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
