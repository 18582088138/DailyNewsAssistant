"""
test_video_store.py —— 视频落盘单元测试 / Video persistence unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/store/test_video_store.py -v

对应的人工验证 / Matching manual check:
    dna add <某篇带视频的文章链接>
    dna show <id>          # 「视频 N 个」一节列出文件与大小
    然后打开 data/articles/<日期>/<slug>__<id>/videos/ 直接播放核对

覆盖 / Covers:
    1. 直链判定：只看路径后缀，带查询串的 `.mp4?token=…` 仍算直链
    2. 直链下载：成功时写出文件 + 同名 .json 出处
    3. **按文件头校验是不是视频**——`.mp4` 的 URL 返回 HTML 错误页极常见
    4. 过小的文件被拒（错误页往往只有几百字节）
    5. 单个视频失败不影响其余视频，原因进 skipped
    6. 非直链走 yt-dlp 分支（本测试里被 monkeypatch 拦截，不联网不下载）
    7. max_videos 限制生效
    8. 空列表不建目录——没有视频的文章不该多出一个空 videos/
    9. 整次重试三次：第三次成功不记 skipped；三次都失败也不抛异常
   10. 重试之间清残片——否则上一次的半个文件会被当成这一次的成品

为什么失败不抛异常 / Why failures never raise:
    视频下载失败远比图片频繁：地域限制、会员墙、平台封禁下载都很常见。
    一个视频下不下来不该让整篇文章白抓，原因记进 skipped 供人工核对。
    Video downloads fail far more often than images. One failure must not waste the
    whole article, so the reason is recorded for manual review.

预期 / Expected:
    耗时 < 1s；
    只写 tmp_path，网络与 yt-dlp 全部被 monkeypatch 拦截，不联网不产生费用
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dna.core.models import MediaAsset, MediaKind
from dna.store import video_store
from dna.store.video_store import download_videos, is_direct_video_url

# 最小可用的 MP4 文件头：前 4 字节是 box 长度，第 5~8 字节是 'ftyp'
# A minimal MP4 header: four length bytes followed by the 'ftyp' box marker.
MP4_HEADER = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 40_000
WEBM_HEADER = b"\x1a\x45\xdf\xa3" + b"\x00" * 40_000
HTML_ERROR_PAGE = b"<!DOCTYPE html><html><body>403 Forbidden</body></html>" * 500


def _asset(url: str, *, source_url: str = "https://example.com/post") -> MediaAsset:
    return MediaAsset(
        kind=MediaKind.VIDEO,
        url=url,
        source_url=source_url,
        credit="example.com",
        caption="示例视频",
    )


# ---------------------------------------------------------------------------
# 直链判定 / direct-URL detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://cdn.example.com/a.mp4", True),
        ("https://cdn.example.com/a.webm", True),
        ("https://cdn.example.com/a.MOV", True),  # 大小写不敏感
        # 带查询串仍然是直链——CDN 几乎都会挂签名参数
        ("https://cdn.example.com/a.mp4?token=abc&expires=1", True),
        ("https://cdn.example.com/a.mp4#t=10", True),
        # 播放器页面不是直链，要交给 yt-dlp
        ("https://v.qq.com/x/page/abc.html", False),
        ("https://www.youtube.com/watch?v=abc", False),
        ("https://www.bilibili.com/video/BV1xx", False),
    ],
)
def test_is_direct_video_url(url: str, expected: bool) -> None:
    """直链判定只看路径后缀，忽略查询串与片段。"""
    assert is_direct_video_url(url) is expected


# ---------------------------------------------------------------------------
# 直链下载 / direct download
# ---------------------------------------------------------------------------


def test_direct_download_writes_file_and_sidecar(tmp_path: Path, monkeypatch) -> None:
    """直链下载成功：写出视频文件，并在旁边写出处 .json。"""
    monkeypatch.setattr(video_store, "fetch_bytes", lambda *a, **k: MP4_HEADER)

    saved, skipped = download_videos([_asset("https://cdn.example.com/clip.mp4")], tmp_path)

    assert skipped == []
    assert len(saved) == 1
    assert saved[0].name == "01_cdn-example-com.mp4"
    assert saved[0].read_bytes() == MP4_HEADER

    sidecar = saved[0].with_suffix(".mp4.json")
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert meta["video_url"] == "https://cdn.example.com/clip.mp4"
    assert meta["source_url"] == "https://example.com/post"
    assert meta["credit"] == "example.com"
    assert meta["bytes"] == len(MP4_HEADER)


def test_direct_download_passes_referer(tmp_path: Path, monkeypatch) -> None:
    """
    下载必须带上原文地址作为 Referer。

    图床与视频源普遍有防盗链，不带 Referer 一律 403（见 issues/004-A）。
    """
    seen: dict[str, object] = {}

    def fake_fetch(url, **kwargs):
        seen.update(kwargs)
        return MP4_HEADER

    monkeypatch.setattr(video_store, "fetch_bytes", fake_fetch)
    download_videos([_asset("https://cdn.example.com/clip.mp4")], tmp_path)

    assert seen["referer"] == "https://example.com/post"


def test_html_error_page_is_rejected(tmp_path: Path, monkeypatch) -> None:
    """
    `.mp4` 的 URL 返回 HTML 错误页时必须被拒。

    只信 URL 后缀会在盘上留下一堆「打不开的 mp4」，而且台账会显示视频数 1，
    看起来一切正常。文件头是唯一可靠的判据。
    """
    monkeypatch.setattr(video_store, "fetch_bytes", lambda *a, **k: HTML_ERROR_PAGE)

    saved, skipped = download_videos([_asset("https://cdn.example.com/clip.mp4")], tmp_path)

    assert saved == []
    assert len(skipped) == 1
    assert "不是视频文件" in skipped[0][1]


def test_tiny_file_is_rejected(tmp_path: Path, monkeypatch) -> None:
    """过小的文件被拒——几百字节的多半是错误页而不是视频。"""
    monkeypatch.setattr(video_store, "fetch_bytes", lambda *a, **k: b"\x00\x00\x00\x20ftyp")

    saved, skipped = download_videos([_asset("https://cdn.example.com/clip.mp4")], tmp_path)

    assert saved == []
    assert "过小" in skipped[0][1]


def test_webm_is_accepted(tmp_path: Path, monkeypatch) -> None:
    """WebM 的文件头与 MP4 不同，同样要认得。"""
    monkeypatch.setattr(video_store, "fetch_bytes", lambda *a, **k: WEBM_HEADER)

    saved, skipped = download_videos([_asset("https://cdn.example.com/clip.webm")], tmp_path)

    assert skipped == []
    assert saved[0].suffix == ".webm"


# ---------------------------------------------------------------------------
# 隔离与限量 / isolation and limits
# ---------------------------------------------------------------------------


def test_one_failure_does_not_stop_the_rest(tmp_path: Path, monkeypatch) -> None:
    """
    单个视频失败不影响其余视频。

    一篇文章里常有多个视频，第一个下不了就放弃全部是最糟的行为。
    """

    def fake_fetch(url, **kwargs):
        if "bad" in url:
            raise RuntimeError("HTTP 403")
        return MP4_HEADER

    monkeypatch.setattr(video_store, "fetch_bytes", fake_fetch)

    saved, skipped = download_videos(
        [
            _asset("https://cdn.example.com/bad.mp4"),
            _asset("https://cdn.example.com/good.mp4"),
        ],
        tmp_path,
    )

    assert len(saved) == 1
    assert saved[0].name.endswith("good.mp4") or "02_" in saved[0].name
    assert len(skipped) == 1
    assert "403" in skipped[0][1]


def test_max_videos_is_honoured(tmp_path: Path, monkeypatch) -> None:
    """限量生效：视频文件很大，不能因为一篇文章嵌了十个就全下。"""
    monkeypatch.setattr(video_store, "fetch_bytes", lambda *a, **k: MP4_HEADER)

    assets = [_asset(f"https://cdn.example.com/{i}.mp4") for i in range(5)]
    saved, _ = download_videos(assets, tmp_path, max_videos=2)

    assert len(saved) == 2


def test_no_assets_creates_no_directory(tmp_path: Path) -> None:
    """没有视频时不该多出一个空的 videos/ 目录，否则人会以为漏下了。"""
    target = tmp_path / "videos"
    saved, skipped = download_videos([], target)

    assert saved == []
    assert skipped == []
    assert not target.exists()


# ---------------------------------------------------------------------------
# yt-dlp 分支 / the yt-dlp path
# ---------------------------------------------------------------------------


def test_player_url_goes_through_ytdlp(tmp_path: Path, monkeypatch) -> None:
    """
    非直链走 yt-dlp 分支。

    这里只验证「分发正确」并且产出的文件被找到；真正调用 yt-dlp 会联网，
    属于 live 测试范畴。
    """
    calls: list[str] = []

    def fake_ytdlp(asset, directory, stem, *, timeout):
        calls.append(asset.url)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stem}.mp4"
        path.write_bytes(MP4_HEADER)
        return path

    monkeypatch.setattr(video_store, "_download_with_ytdlp", fake_ytdlp)

    saved, skipped = download_videos([_asset("https://v.qq.com/x/page/abc.html")], tmp_path)

    assert calls == ["https://v.qq.com/x/page/abc.html"]
    assert skipped == []
    assert saved[0].exists()
    # 走 yt-dlp 的视频同样要有出处 sidecar
    assert saved[0].with_suffix(".mp4.json").exists()


def test_ytdlp_failure_is_recorded_not_raised(tmp_path: Path, monkeypatch) -> None:
    """yt-dlp 失败（平台封禁、改版）只记原因，不能中断整篇文章的落盘。"""

    def boom(asset, directory, stem, *, timeout):
        raise RuntimeError("Unsupported URL")

    monkeypatch.setattr(video_store, "_download_with_ytdlp", boom)

    saved, skipped = download_videos([_asset("https://v.qq.com/x/page/abc.html")], tmp_path)

    assert saved == []
    assert "Unsupported URL" in skipped[0][1]


# ---------------------------------------------------------------------------
# 整次重试 / whole-attempt retries
# ---------------------------------------------------------------------------


def test_retries_until_success(tmp_path: Path, monkeypatch) -> None:
    """前两次网络抖动，第三次成功——skipped 里不该出现它。"""
    attempts: list[int] = []

    def flaky(asset, directory, stem, *, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("Connection reset")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stem}.mp4"
        path.write_bytes(MP4_HEADER)
        return path

    monkeypatch.setattr(video_store, "_download_with_ytdlp", flaky)

    saved, skipped = download_videos([_asset("https://v.qq.com/x/page/abc.html")], tmp_path)

    assert len(attempts) == 3
    assert skipped == []
    assert saved[0].exists()


def test_gives_up_after_three_attempts(tmp_path: Path, monkeypatch) -> None:
    """三次都失败：记 skipped，**不抛异常**，原因取最后一次的。"""
    attempts: list[str] = []

    def always_fails(asset, directory, stem, *, timeout):
        attempts.append(f"第 {len(attempts) + 1} 次")
        raise RuntimeError(f"失败 {len(attempts)}")

    monkeypatch.setattr(video_store, "_download_with_ytdlp", always_fails)

    saved, skipped = download_videos([_asset("https://v.qq.com/x/page/abc.html")], tmp_path)

    assert len(attempts) == video_store.DOWNLOAD_ATTEMPTS == 3
    assert saved == []
    assert "失败 3" in skipped[0][1]


def test_leftovers_are_cleared_between_attempts(tmp_path: Path, monkeypatch) -> None:
    """
    上一次留下的残片不能被下一次当成成品。

    `_download_with_ytdlp` 靠 `directory.glob(f"{stem}.*")` 找产物。第一次写了半个
    文件就断了的话，第二次的 glob 会「找到」它并当成功返回——报出来的是一个放不了
    的文件，而台账显示成功。
    """
    seen: list[list[str]] = []

    def leaves_partial(asset, directory, stem, *, timeout):
        directory.mkdir(parents=True, exist_ok=True)
        seen.append(sorted(p.name for p in directory.glob(f"{stem}.*")))
        (directory / f"{stem}.mp4.part").write_bytes(b"partial")
        raise RuntimeError("中途断了")

    monkeypatch.setattr(video_store, "_download_with_ytdlp", leaves_partial)

    saved, skipped = download_videos([_asset("https://v.qq.com/x/page/abc.html")], tmp_path)

    assert saved == []
    assert skipped  # 失败了，但每次开始时目录里都是干净的
    assert seen == [[], [], []]
