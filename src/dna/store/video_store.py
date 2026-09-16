"""
视频落盘 / Video persistence.

把文章里的官方视频下载到本地。
Downloads the official videos referenced by an article.

两条路径 / Two strategies:
    1. **直链视频文件**（`<video src="....mp4">`）→ 直接 HTTP 下载，带 Referer 防盗链
    2. **播放器页面 / iframe**（腾讯视频、B站、YouTube、微信视频号…）→ 交给 yt-dlp

为什么要分两条 / Why the split:
    直链下载是纯 HTTP，快且零依赖；yt-dlp 能解析播放器，但启动慢、依赖外部项目、
    且平台改版时会失效。能用直链就不动 yt-dlp。
    A direct download is plain HTTP — fast and dependency-free. yt-dlp can resolve
    players but is slow to start, depends on an external project, and breaks whenever a
    platform changes. Use it only when the plain path cannot work.

失败不抛异常 / Failures never raise:
    视频下载失败得远比图片频繁——大量平台有地域限制、会员墙、或干脆封禁下载。
    一个视频下不下来不该让整篇文章白抓，原因记进 `skipped` 供人工核对。
    Video downloads fail far more often than images: region locks, paywalls and outright
    download bans are all common. One failed video must not waste the whole article, so
    the reason is recorded in `skipped` for manual review.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from dna.core.logging import get_logger
from dna.core.models import MediaAsset
from dna.core.naming import slugify
from dna.core.urls import host_of
from dna.sources.http import fetch_bytes

logger = get_logger("store.video_store")

DEFAULT_MAX_VIDEOS = 2
MAX_VIDEO_BYTES = 300 * 1024 * 1024  # 300MB，单条资讯视频远小于此
MIN_VIDEO_BYTES = 16 * 1024  # 小于 16KB 的不可能是真视频，多半是错误页

# 整次重试的次数 / whole-attempt retries.
# 注意 `_download_with_ytdlp` 里的 `"retries": 2` 是 yt-dlp **分片级**的重试，
# 解析播放器失败时它一次都不会重来——这个常量管的是那一层。
# 重试之间不 sleep：视频失败的主因是地域限制、会员墙、封禁下载，等多久都一样；
# 重试针对的是网络抖动和临时 5xx。
DOWNLOAD_ATTEMPTS = 3

# 直链视频扩展名 / extensions that indicate a directly downloadable file
_DIRECT_SUFFIXES = (".mp4", ".m4v", ".webm", ".mov", ".mkv")

# 视频文件头 / magic bytes, used to reject HTML error pages served as .mp4
_VIDEO_SIGNATURES = (
    (4, b"ftyp"),  # MP4 / MOV：前 4 字节是 box 长度，第 5~8 字节是 'ftyp'
    (0, b"\x1a\x45\xdf\xa3"),  # WebM / Matroska
    (0, b"FLV"),
)


def is_direct_video_url(url: str) -> bool:
    """
    判断是否可以直接 HTTP 下载 / Whether the URL is a plain downloadable file.

    只看路径部分的后缀，忽略查询串——`.mp4?token=…` 仍然是直链。
    Only the path suffix is examined; a query string is ignored, so `.mp4?token=…`
    still counts as direct.
    """
    path = url.split("?")[0].split("#")[0].lower()
    return path.endswith(_DIRECT_SUFFIXES)


def download_videos(
    assets: list[MediaAsset],
    directory: Path,
    *,
    max_videos: int = DEFAULT_MAX_VIDEOS,
    timeout: float = 180.0,
    attempts: int = DOWNLOAD_ATTEMPTS,
) -> tuple[list[Path], list[tuple[str, str]]]:
    """
    下载视频 / Download the videos.

    每条视频最多尝试 `attempts` 次，**全部失败也不抛异常**，只把最后一次的原因
    记进 `skipped`（前几次记 debug 日志）。上层据此提醒，而不是中断整篇文章。
    Each video is attempted up to `attempts` times and still never raises: only the last
    reason is recorded so the caller can warn rather than abort the article.

    返回 / Returns:
        (已下载的文件路径, [(url, 失败原因), …])
    """
    saved: list[Path] = []
    skipped: list[tuple[str, str]] = []

    if not assets:
        return saved, skipped

    directory.mkdir(parents=True, exist_ok=True)

    for index, asset in enumerate(assets[:max_videos], start=1):
        stem = f"{index:02d}_{_safe_host(asset.url)}"
        path, reason = _download_one(asset, directory, stem, timeout=timeout, attempts=attempts)

        if path is None:
            skipped.append((asset.url, reason or "未能取得视频文件"))
            continue

        saved.append(path)
        _write_sidecar(path, asset)

    return saved, skipped


def _download_one(
    asset: MediaAsset,
    directory: Path,
    stem: str,
    *,
    timeout: float,
    attempts: int,
) -> tuple[Path | None, str]:
    """
    下载一条视频，失败重试 / Fetch one video, retrying on failure.

    每次重试前清掉上一次留下的同名残片：yt-dlp 中途失败可能留下 `.part` 或
    分轨文件，下一次的 glob 会把它当成产物捡回来，于是「成功」拿到一个放不了的文件。
    Leftovers from a failed attempt are removed first: yt-dlp can leave `.part` or
    per-track files behind, and the next attempt's glob would happily return one.
    """
    reason = ""
    for attempt in range(1, max(1, attempts) + 1):
        _clear_leftovers(directory, stem)
        try:
            if is_direct_video_url(asset.url):
                path = _download_direct(asset, directory, stem, timeout=timeout)
            else:
                path = _download_with_ytdlp(asset, directory, stem, timeout=timeout)
        except Exception as exc:
            reason = _short(exc)
            path = None
        else:
            if path is not None:
                return path, ""
            reason = "未能取得视频文件"

        if attempt < attempts:
            logger.debug(
                "视频下载第 %d/%d 次失败，重试：%s（%s）", attempt, attempts, asset.url, reason
            )

    return None, reason


def _clear_leftovers(directory: Path, stem: str) -> None:
    """清掉上一次尝试留下的残片 / Drop partial files from a previous attempt."""
    for path in directory.glob(f"{stem}.*"):
        # 文件被占用时留着即可，下一步会覆盖
        with contextlib.suppress(OSError):
            path.unlink()


# ---------------------------------------------------------------------------
# 直链下载 / direct download
# ---------------------------------------------------------------------------


def _download_direct(
    asset: MediaAsset, directory: Path, stem: str, *, timeout: float
) -> Path | None:
    """直接 HTTP 下载一个视频文件 / Fetch a plain video file over HTTP."""
    data = fetch_bytes(
        asset.url, timeout=timeout, max_bytes=MAX_VIDEO_BYTES, referer=asset.source_url
    )

    if len(data) < MIN_VIDEO_BYTES:
        raise ValueError(f"文件过小（{len(data)} 字节），可能是错误页")

    if not _looks_like_video(data):
        raise ValueError("下载到的不是视频文件（文件头不匹配，可能是防盗链错误页）")

    suffix = Path(asset.url.split("?")[0]).suffix.lower() or ".mp4"
    path = directory / f"{stem}{suffix}"
    path.write_bytes(data)
    return path


def _looks_like_video(data: bytes) -> bool:
    """
    按文件头确认是视频 / Confirm by magic bytes that this really is a video.

    和图片同理：`.mp4` 的 URL 返回 HTML 错误页非常常见，只看后缀会存一堆废文件。
    Same reasoning as for images: a `.mp4` URL returning an HTML error page is common,
    and trusting the extension would litter the disk with junk.
    """
    return any(data[offset : offset + len(sig)] == sig for offset, sig in _VIDEO_SIGNATURES)


# ---------------------------------------------------------------------------
# 播放器解析 / player extraction via yt-dlp
# ---------------------------------------------------------------------------


def _download_with_ytdlp(
    asset: MediaAsset, directory: Path, stem: str, *, timeout: float
) -> Path | None:
    """
    用 yt-dlp 解析播放器页面并下载 / Resolve a player page with yt-dlp and download it.

    格式优先选**单文件 mp4**：合并音视频轨需要 ffmpeg，而 ffmpeg 不一定装了。
    宁可要一个清晰度略低但一定能播的文件，也不要一个需要外部工具才能拼起来的。
    A single-file mp4 is preferred because merging separate audio and video tracks needs
    ffmpeg, which may not be installed. A slightly lower-resolution file that definitely
    plays beats one that needs an external tool to assemble.
    """
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的提示路径
        raise RuntimeError(
            "未安装 yt-dlp，无法下载播放器嵌入的视频；执行 `pip install yt-dlp` 后重试"
        ) from exc

    options = {
        # 单文件优先，逐级降级，最后兜底 best（可能需要 ffmpeg 合并）
        "format": "best[height<=1080][ext=mp4]/best[ext=mp4]/best[height<=1080]/best",
        "outtmpl": str(directory / f"{stem}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": timeout,
        "retries": 2,
        "max_filesize": MAX_VIDEO_BYTES,
        "noplaylist": True,  # iframe 常指向合集，只要当前这一个
        "http_headers": {"Referer": asset.source_url} if asset.source_url else {},
    }

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        options["proxy"] = proxy

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(asset.url, download=True)

    # 文件名由 yt-dlp 按实际格式决定，回头按 stem 前缀找出来
    # yt-dlp picks the final extension itself, so locate the file by its stem.
    produced = sorted(p for p in directory.glob(f"{stem}.*") if p.suffix != ".json")
    if not produced:
        title = (info or {}).get("title", "")
        raise ValueError(f"yt-dlp 未产出文件{f'（{title}）' if title else ''}")
    return produced[0]


# ---------------------------------------------------------------------------
# 共用 / shared
# ---------------------------------------------------------------------------


def _write_sidecar(path: Path, asset: MediaAsset) -> None:
    """
    在视频旁边写出处 / Record the origin next to the video.

    与图片同样的理由：视频会被单独拷进剪辑软件，出处不能只留在数据库里。
    Same reasoning as images: the file gets copied into an editor on its own, so the
    attribution cannot live only in the database.
    """
    import json

    path.with_suffix(path.suffix + ".json").write_text(
        json.dumps(
            {
                "source_url": asset.source_url,
                "video_url": asset.url,
                "credit": asset.credit,
                "caption": asset.caption,
                "bytes": path.stat().st_size,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _safe_host(url: str) -> str:
    """主机名转成可用作文件名的形式 / A hostname usable inside a filename."""
    return slugify(host_of(url).replace(".", "-"), max_len=30) or "video"


def _short(exc: Exception) -> str:
    """异常压成一行 / Squash an exception into one line."""
    text = " ".join(str(exc).split())
    return text if len(text) <= 140 else f"{text[:140]}…"


__all__ = [
    "DEFAULT_MAX_VIDEOS",
    "DOWNLOAD_ATTEMPTS",
    "MAX_VIDEO_BYTES",
    "MIN_VIDEO_BYTES",
    "download_videos",
    "is_direct_video_url",
]
