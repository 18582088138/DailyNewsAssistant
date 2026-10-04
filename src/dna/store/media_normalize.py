"""
素材合规化 / Making hand-dropped assets conform to the on-disk rules.

用户在界面上点开「媒体」格，把图片和视频自己拷进 `images/` 与 `videos/`，回头再点一次，
这里就把它们改成**与抓取下来的素材同样的形状**：文件头认得出的真实扩展名、
`NN_<来源>.<ext>` 的文件名、以及一张写明出处的同名 `.json`。

The user opens the media cell, drops their own pictures and clips into `images/` and
`videos/`, then comes back and clicks again: this module reshapes them to match what a
fetched asset looks like — a real extension read from the magic bytes, an
`NN_<origin>.<ext>` filename, and a sibling `.json` recording where it came from.

为什么非要统一形状 / Why the shape matters:
    那些文件名、扩展名与边车是**可追溯性**的一部分，不是审美（见 `05_output_spec.md` §四）。
    用户拷进来的文件如果叫 `微信图片_2026.png`，发布时就没有任何东西能说明它是哪来的。
    统一之后，每一件素材都有一张边车可查。
    Those names, extensions and sidecars are part of traceability, not cosmetics. A file
    dropped in as `WeChat Image_2026.png` leaves nothing behind saying where it came from;
    after normalisation every asset has a sidecar to check.

三条尺度 / Three deliberately conservative rules:

    1. **不删除任何文件。** 认不出来的（.txt、损坏的、太小的）只报告，留给人处理。
       静默删掉用户自己拷进来的东西是不可逆的。
    2. **不覆盖已有的出处。** 边车已存在就原样保留——那一份是当初下载时记下的真出处，
       重写它等于伪造来源。
    3. **不编造出处。** 没有边车的文件记成「人工放入（出处未记录）」，而不是替用户
       声称它是从某个站点来的。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from dna.core.config import Settings, get_settings
from dna.core.logging import get_logger
from dna.core.urls import is_custom_url

# `images` 与 `videos` 的判定必须与下载时**完全一致**，所以直接借用落盘那两个模块里
# 的私有表与私有函数，而不是各写一份：那两份迟早在某次格式调整后对不上——下载路径
# 认它是图、这里认不出，同一张图于是有了两种命运。
# The signature tables stay single-sourced by borrowing the privates from the two store
# modules that download media: identification must match the download path exactly, and a
# second copy would drift the first time a format is added.
from dna.store.article_store import _EXT_BY_SIGNATURE, MIN_IMAGE_BYTES
from dna.store.ledger import Ledger
from dna.store.video_store import MIN_VIDEO_BYTES, _looks_like_video

logger = get_logger("store.media_normalize")

# 合规的文件名：`NN_<来源>` + 扩展名 / the compliant shape: `NN_<origin>` plus extension
_COMPLIANT_STEM = re.compile(r"^\d{2}_.+$")

# 本地文件没有来源主机名，用这个占位 / local files have no source host, so this stands in
LOCAL_ORIGIN = "custom"

# 认得出的视频扩展名 / extensions that can accompany a recognised video header
_VIDEO_SUFFIXES = (".mp4", ".m4v", ".mov", ".webm", ".mkv", ".flv")

# 只要文件头，不读整个文件 / only the header is needed, never the whole file
_HEADER_BYTES = 64

IMAGE_DIR = "images"
VIDEO_DIR = "videos"

# 抓取来的文章不做整理 / fetched articles are left alone
SKIPPED_FETCHED = (
    "这是抓取来的文章，素材是当时下载的历史档案，不做整理（改名等于改写历史）。"
    "整理只针对「新建」的文章。"
)

_IMAGE = "image"
_VIDEO = "video"


@dataclass
class MediaReport:
    """一次合规化的结果 / The outcome of one normalisation pass."""

    renamed: list[tuple[str, str]] = field(default_factory=list)
    moved: list[tuple[str, str]] = field(default_factory=list)
    sidecars_written: list[str] = field(default_factory=list)
    unrecognized: list[tuple[str, str]] = field(default_factory=list)
    image_count: int = 0
    video_count: int = 0
    skipped_reason: str = ""
    """
    整趟被跳过的原因 / why the whole pass was skipped.

    抓取来的文章会填这一项：它的素材是**抓取时的历史档案**，改名就是改写历史。
    A fetched article fills this in: its assets are a historical archive, and renaming them
    would rewrite history.
    """

    @property
    def skipped(self) -> bool:
        """整趟是不是被跳过了 / Whether the pass was skipped entirely."""
        return bool(self.skipped_reason)

    @property
    def changed(self) -> bool:
        """有没有真的动过东西 / Whether anything was actually touched."""
        return bool(self.renamed or self.moved or self.sidecars_written)

    @property
    def empty(self) -> bool:
        """素材文件夹里什么都没有 / Nothing at all in the asset folders."""
        return not (self.image_count or self.video_count or self.unrecognized)

    def summary(self) -> str:
        """
        给界面与命令行用的一句话 / One line for the UI and the CLI.

        「文件夹是空的」与「本来就合规」是两件事：前者是没东西可做，后者是做了检查、
        一切已经就位。混成一句会让人以为自己少拷了文件。
        "The folders are empty" and "already in order" differ: the first means there was
        nothing to do, the second that everything checked out. Merging them would leave the
        user wondering whether their files ever arrived.
        """
        if self.skipped:
            return self.skipped_reason
        if self.empty:
            return "素材文件夹是空的，没有可整理的东西"

        parts = [f"素材 {self.image_count} 图 / {self.video_count} 视频"]
        for label, count in (
            ("改名", len(self.renamed)),
            ("移到正确文件夹", len(self.moved)),
            ("补出处", len(self.sidecars_written)),
            ("认不出", len(self.unrecognized)),
        ):
            if count:
                parts.append(f"{label} {count}")
        if not self.changed and not self.unrecognized:
            parts.append("本来就合规")
        return "　·　".join(parts)

    def details(self) -> list[str]:
        """逐条明细，供界面展开 / Per-item detail, for the UI to expand."""
        lines = [f"改名：{old} → {new}" for old, new in self.renamed]
        lines += [f"移到 {new}（原本在错误的文件夹里）" for _, new in self.moved]
        lines += [f"补写出处边车：{name}" for name in self.sidecars_written]
        lines += [f"认不出 {name}：{reason}" for name, reason in self.unrecognized]
        return lines


def normalize_media(article_id: str, *, settings: Settings | None = None) -> MediaReport:
    """
    把一篇的素材整理成合规形状并回写计数 / Normalise one article's assets and update counts.

    幂等：已经合规的文件不会被改名，已有的边车不会被覆盖，所以每次点开媒体格都跑一遍
    是安全的。
    Idempotent: compliant files keep their names and existing sidecars are never
    overwritten, so running it on every media-cell click is safe.

    **只整理人工创作的文章。**抓取来的文章直接返回一条「已跳过」——
    它的 `images/` `videos/` 是**抓取当时的历史档案**，改名就是改写历史，
    而历史产物一律只读（要重来有 `dna refetch`）。
    Only hand-authored articles are tidied. A fetched article comes back as "skipped": its
    asset folders are the historical record of what was downloaded, and renaming files in
    there would rewrite history. Historical artefacts are read-only by rule.

    返回 / Returns:
        `MediaReport`；文章不存在或未落盘时返回空报告
    """
    s = settings or get_settings()
    ledger = Ledger(s.db_file)
    record = ledger.get(article_id)
    if record is None or not record.store_dir:
        return MediaReport()
    if not is_custom_url(record.url):
        return MediaReport(skipped_reason=SKIPPED_FETCHED)

    article_dir = s.output_path / record.store_dir
    report = MediaReport()
    for name in (IMAGE_DIR, VIDEO_DIR):
        _scan(article_dir / name, article_dir=article_dir, report=report)

    # 计数放在所有搬移**之后**：视频被搬进 videos/ 时要算进去，搬出去的不算，
    # 一边扫一边数会在搬移方向不同时多数或少数一件。
    report.image_count = _count_assets(article_dir / IMAGE_DIR, wanted=_IMAGE)
    report.video_count = _count_assets(article_dir / VIDEO_DIR, wanted=_VIDEO)

    if record.image_count != report.image_count or record.video_count != report.video_count:
        ledger.set_media_counts(
            article_id, image_count=report.image_count, video_count=report.video_count
        )
        logger.info(
            "素材计数更新：%s（%d 图 / %d 视频）",
            article_id[:8],
            report.image_count,
            report.video_count,
        )
    return report


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _scan(directory: Path, *, article_dir: Path, report: MediaReport) -> None:
    """
    整理一个素材目录 / Normalise one asset directory.

    判定**只看文件头，不看目录名**：把视频拷进 `images/` 是常见的手滑，
    按目录名判定的话它会被当成一张图留在那里，永远也找不到。
    The decision comes from the magic bytes, never the folder name: dropping a clip into
    `images/` is an easy mistake, and trusting the folder would leave it filed as a
    picture where nobody would look for it.
    """
    if not directory.is_dir():
        return

    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() == ".json":
            continue

        kind, extension, reason = _classify(path)
        if kind is None:
            report.unrecognized.append((path.name, reason))
            continue

        before = path.name
        target_dir = article_dir / (VIDEO_DIR if kind == _VIDEO else IMAGE_DIR)
        if target_dir != directory:
            target_dir.mkdir(parents=True, exist_ok=True)
            path = _place(path, target_dir, extension)
            report.moved.append((before, f"{target_dir.name}/{path.name}"))
        else:
            path = _place(path, directory, extension)
            if path.name != before:
                report.renamed.append((before, path.name))

        # 原始文件名要在**改名之前**记下来，否则边车里那一栏只是新名字的复述
        if _write_sidecar(path, kind=kind, original_name=before):
            report.sidecars_written.append(path.name)


def _classify(path: Path) -> tuple[str | None, str, str]:
    """
    按文件头认出这是什么 / Identify the file by its magic bytes.

    返回 / Returns:
        ("image" | "video" | None, 该有的扩展名, 认不出时的原因)
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            header = handle.read(_HEADER_BYTES)
    except OSError as exc:
        return None, "", f"读不到（{exc}）"

    for signature, extension in _EXT_BY_SIGNATURE.items():
        if header.startswith(signature):
            if size < MIN_IMAGE_BYTES:
                return None, "", f"过小（{size} 字节），可能是图标或占位图"
            return _IMAGE, extension, ""

    if _looks_like_video(header):
        if size < MIN_VIDEO_BYTES:
            return None, "", f"过小（{size} 字节），不可能是真视频"
        suffix = path.suffix.lower()
        return _VIDEO, suffix if suffix in _VIDEO_SUFFIXES else ".mp4", ""

    return None, "", "文件头既不像图片也不像视频（损坏、或不是素材文件）"


def _place(path: Path, directory: Path, extension: str) -> Path:
    """
    把文件放到该在的目录并改成合规名字 / Put a file in place with a compliant name.

    已经合规、扩展名也正确的文件原样返回——**幂等就靠这一条**。
    A file that is already compliant with the right extension is returned untouched; that
    single check is what makes the whole pass idempotent.
    """
    stem = path.stem
    if not _COMPLIANT_STEM.match(stem):
        stem = f"{_next_index(directory):02d}_{LOCAL_ORIGIN}"
    target = directory / f"{stem}{extension}"

    if target == path:
        return path
    while target.exists():
        target = directory / f"{_next_index(directory):02d}_{LOCAL_ORIGIN}{extension}"
    path.rename(target)
    return target


def _next_index(directory: Path) -> int:
    """目录里下一个可用的两位序号 / The next free two-digit index in a directory."""
    used = {
        int(path.stem[:2])
        for path in directory.iterdir()
        if path.suffix.lower() != ".json" and len(path.stem) >= 2 and path.stem[:2].isdigit()
    }
    index = 1
    while index in used:
        index += 1
    return index


def _write_sidecar(path: Path, *, kind: str, original_name: str) -> bool:
    """
    需要时补一张出处边车 / Write an attribution sidecar when one is missing.

    **已存在就原样保留。** 那一份是下载时记下的真出处；重写它等于伪造来源。
    An existing sidecar is kept as is: it holds the origin recorded at download time, and
    rewriting it would amount to forging provenance.

    返回是否新写了一张 / Returns whether a new sidecar was written.
    """
    sidecar = path.with_suffix(path.suffix + ".json")
    if sidecar.exists():
        return False

    try:
        size = path.stat().st_size
    except OSError:  # pragma: no cover - 刚放好的文件不该读不到
        size = 0

    # 出处一栏不编造：没有边车就是「出处未记录」，把这句话写清楚，
    # 比替用户声称它是从某个站点下来的要诚实。
    payload = {
        "source_url": "",
        "credit": "人工放入（出处未记录）",
        "caption": None,
        "bytes": size,
        "origin": "manual",
        "original_name": original_name,
    }
    payload["image_url" if kind == _IMAGE else "video_url"] = ""
    sidecar.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return True


def _count_assets(directory: Path, *, wanted: str) -> int:
    """
    清点目录里有多少件素材 / Count the assets in one directory.

    只数**认得出是这一类素材**的文件：边车和散落的 `.txt` 不算，
    否则台账上的数字会比媒体格显示的「N 图」多，人一核对就以为丢了文件。
    Only files recognised as that kind of asset are counted, never sidecars or stray text
    files — otherwise the ledger would outnumber what the cell shows.
    """
    if not directory.is_dir():
        return 0

    total = 0
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() == ".json":
            continue
        kind, _, _ = _classify(path)
        if kind == wanted:
            total += 1
    return total


__all__ = [
    "IMAGE_DIR",
    "LOCAL_ORIGIN",
    "SKIPPED_FETCHED",
    "VIDEO_DIR",
    "MediaReport",
    "normalize_media",
]
