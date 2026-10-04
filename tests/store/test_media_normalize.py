"""
test_media_normalize.py —— 素材合规化单元测试 / Hand-dropped asset normalisation tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/store/test_media_normalize.py -v

对应的人工验证 / Matching manual check:
    dna gui → 点「媒体」格 → 把几张图拷进弹出的 images/ → 再点一次「媒体」格
    提示条应报「素材 N 图 / 0 视频　·　改名 N　·　补出处 N」

覆盖 / Covers:
    1. 扩展名按**文件头**改对（`.jpg` 里其实装着 PNG）
    2. 非合规文件名改成 `NN_custom.<ext>`
    3. 已合规的文件**不动**（幂等：连跑两次第二次什么都不做）
    4. 已有的边车**不被覆盖**（那是真出处，重写等于伪造来源）
    5. 缺边车时补一张，写明「人工放入（出处未记录）」与**原始文件名**
    6. 认不出的文件（.txt、损坏的、过小的）**只报告不删除**
    7. 视频被拷进 `images/` 时搬到 `videos/`（判定只看文件头，不看目录名）
    8. 台账的 image_count / video_count 跟着更新
    9. **抓取来的文章整趟跳过**——历史素材只读，改名等于改写历史

为什么「不删除、不覆盖」要单独守着 / Why non-destruction is guarded explicitly:
    这两条错了都是**不可逆**的：用户自己拷进来的素材被静默删掉，或者下载时记下的
    真实出处被一句「人工放入」顶掉。两者都不会报错，只在发布时才发现对不上。

预期 / Expected:
    耗时 < 2s；只写 tmp_path，不联网、不调 LLM
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from dna.core.config import Settings
from dna.core.models import RawItem, SourceKind
from dna.store.custom_article import create_custom_article, media_folders
from dna.store.ledger import Ledger
from dna.store.media_normalize import normalize_media

NOW = datetime(2026, 10, 4, 9, 0, 0)

# 各格式的最小文件头，后面补足到超过最小体积 / magic bytes, padded past the size floor
JPEG = b"\xff\xd8\xff" + b"x" * 20_000
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20_000
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"x" * 20_000
MP4 = b"\x00\x00\x00\x20ftypisom" + b"x" * 40_000
TINY = b"\xff\xd8\xff" + b"x" * 100
NOT_MEDIA = "这只是一份笔记。".encode()


def _ledger(settings: Settings) -> Ledger:
    return Ledger(settings.db_file)


def _article_dir(settings: Settings, store_dir: str) -> Path:
    return settings.output_path / store_dir


def _custom(settings: Settings) -> tuple[str, Path]:
    """建一篇人工创作的文章并返回 (id, 文章目录) / Build one and return (id, directory)."""
    record = create_custom_article(settings=settings, now=NOW)
    media_folders(record.id, settings=settings)  # 建出 images/ 与 videos/
    assert record.store_dir
    return record.id, _article_dir(settings, record.store_dir)


# ---------------------------------------------------------------------------
# 1-2. 改名与扩展名 / renaming and extensions
# ---------------------------------------------------------------------------


def test_extension_is_fixed_from_the_magic_bytes(settings: Settings) -> None:
    """扩展名骗人时要按文件头改对：`photo.jpg` 里装着 PNG 就改成 `.png`。"""
    article_id, directory = _custom(settings)
    (directory / "images" / "01_wechat.jpg").write_bytes(PNG)

    report = normalize_media(article_id, settings=settings)

    assert (directory / "images" / "01_wechat.png").is_file()
    assert not (directory / "images" / "01_wechat.jpg").exists()
    assert ("01_wechat.jpg", "01_wechat.png") in report.renamed


def test_non_compliant_name_becomes_numbered_custom(settings: Settings) -> None:
    """`微信图片_2026.png` 这种名字改成 `NN_custom.png`。"""
    article_id, directory = _custom(settings)
    (directory / "images" / "微信图片_2026.png").write_bytes(PNG)

    normalize_media(article_id, settings=settings)

    assert (directory / "images" / "01_custom.png").is_file()


def test_numbering_avoids_collisions(settings: Settings) -> None:
    """两个文件抢同一个序号时不能互相覆盖 / No two files may end up with one number."""
    article_id, directory = _custom(settings)
    (directory / "images" / "01_custom.png").write_bytes(PNG)
    (directory / "images" / "随手存的图.png").write_bytes(JPEG)

    normalize_media(article_id, settings=settings)

    names = sorted(p.name for p in (directory / "images").iterdir() if p.suffix != ".json")
    assert names == ["01_custom.png", "02_custom.jpg"]


# ---------------------------------------------------------------------------
# 3-4. 幂等与不覆盖 / idempotence and non-overwriting
# ---------------------------------------------------------------------------


def test_second_pass_changes_nothing(settings: Settings) -> None:
    """
    连跑两次，第二次必须什么都不做 / The second pass must be a no-op.

    不幂等的话，用户每点一次媒体格都会重新洗一遍文件编号，边车也会被反复重写。
    """
    article_id, directory = _custom(settings)
    (directory / "images" / "微信图片.png").write_bytes(PNG)

    first = normalize_media(article_id, settings=settings)
    second = normalize_media(article_id, settings=settings)

    assert first.changed
    assert not second.changed
    assert second.renamed == [] and second.sidecars_written == []


def test_existing_sidecar_is_never_overwritten(settings: Settings) -> None:
    """
    已有的边车是当初下载时记下的真出处，**不能被重写**。

    这条比改名重要得多：把 `source_url` 抹成空、credit 换成「人工放入」，
    发布时就再也没有东西能说明这张图是谁的了。
    """
    article_id, directory = _custom(settings)
    image = directory / "images" / "01_qbitai.jpg"
    image.write_bytes(JPEG)
    truth = {"source_url": "https://qbitai.com/p/1", "credit": "量子位", "bytes": len(JPEG)}
    (directory / "images" / "01_qbitai.jpg.json").write_text(
        json.dumps(truth, ensure_ascii=False), encoding="utf-8"
    )

    report = normalize_media(article_id, settings=settings)

    assert report.sidecars_written == []
    assert json.loads((directory / "images" / "01_qbitai.jpg.json").read_text("utf-8")) == truth


# ---------------------------------------------------------------------------
# 5. 补出处 / filling in provenance
# ---------------------------------------------------------------------------


def test_missing_sidecar_records_manual_origin_and_original_name(
    settings: Settings,
) -> None:
    """
    缺边车时补一张，写明「人工放入（出处未记录）」与**改名前的**文件名。

    `original_name` 在改名之前取；反过来取的话那一栏只是新名字的复述，毫无用处。
    """
    article_id, directory = _custom(settings)
    (directory / "images" / "微信图片_2026.png").write_bytes(PNG)

    normalize_media(article_id, settings=settings)

    sidecar = json.loads((directory / "images" / "01_custom.png.json").read_text("utf-8"))
    assert sidecar["original_name"] == "微信图片_2026.png"
    assert sidecar["origin"] == "manual"
    assert "出处未记录" in sidecar["credit"]
    assert sidecar["bytes"] == len(PNG)
    assert sidecar["image_url"] == ""


def test_video_sidecar_uses_the_video_key(settings: Settings) -> None:
    """视频边车用 `video_url` 那一栏 / Video sidecars carry `video_url`."""
    article_id, directory = _custom(settings)
    (directory / "videos" / "片子.mp4").write_bytes(MP4)

    normalize_media(article_id, settings=settings)

    sidecar = json.loads((directory / "videos" / "01_custom.mp4.json").read_text("utf-8"))
    assert sidecar["video_url"] == ""
    assert "image_url" not in sidecar


# ---------------------------------------------------------------------------
# 6. 不删除 / nothing is deleted
# ---------------------------------------------------------------------------


def test_unrecognised_files_are_reported_not_deleted(settings: Settings) -> None:
    """认不出的文件只报告、**原样留着** / Unrecognised files are reported and kept."""
    article_id, directory = _custom(settings)
    (directory / "images" / "notes.txt").write_bytes(NOT_MEDIA)
    (directory / "images" / "tiny_icon.png").write_bytes(TINY)

    report = normalize_media(article_id, settings=settings)

    assert (directory / "images" / "notes.txt").is_file()
    assert (directory / "images" / "tiny_icon.png").is_file()
    reasons = dict(report.unrecognized)
    assert "notes.txt" in reasons
    assert "过小" in reasons["tiny_icon.png"]
    assert report.image_count == 0, "认不出的文件不该被算进素材计数"


def test_recognised_assets_are_counted(settings: Settings) -> None:
    """只数认得出是素材的文件，边车与 .txt 不算 / Sidecars and text files never count."""
    article_id, directory = _custom(settings)
    (directory / "images" / "a.png").write_bytes(PNG)
    (directory / "images" / "b.png").write_bytes(JPEG)
    (directory / "images" / "notes.txt").write_bytes(NOT_MEDIA)
    (directory / "videos" / "c.mp4").write_bytes(MP4)

    report = normalize_media(article_id, settings=settings)

    assert report.image_count == 2
    assert report.video_count == 1
    record = _ledger(settings).get(article_id)
    assert record is not None
    assert (record.image_count, record.video_count) == (2, 1)


# ---------------------------------------------------------------------------
# 7. 放错目录 / wrong folder
# ---------------------------------------------------------------------------


def test_video_dropped_into_images_is_moved(settings: Settings) -> None:
    """
    判定只看文件头：视频被拷进 `images/` 要搬到 `videos/`。

    按目录名判定的话它会作为一张「图」永远留在那里，而用户按「N 图」也看不出问题。
    """
    article_id, directory = _custom(settings)
    (directory / "images" / "错放的片子.mp4").write_bytes(MP4)

    report = normalize_media(article_id, settings=settings)

    assert not (directory / "images" / "错放的片子.mp4").exists()
    assert (directory / "videos" / "01_custom.mp4").is_file()
    assert report.video_count == 1
    assert report.image_count == 0
    assert report.moved


def test_image_dropped_into_videos_is_moved_back(settings: Settings) -> None:
    """反过来也一样 / And the other way round."""
    article_id, directory = _custom(settings)
    (directory / "videos" / "错放的图.png").write_bytes(WEBP)

    normalize_media(article_id, settings=settings)

    assert (directory / "images" / "01_custom.webp").is_file()
    assert not (directory / "videos" / "错放的图.png").exists()


# ---------------------------------------------------------------------------
# 8-9. 空目录与普通文章 / empty folders and fetched articles
# ---------------------------------------------------------------------------


def test_empty_folders_report_nothing_to_do(settings: Settings) -> None:
    """空素材文件夹要说「没东西可整理」，而不是「已整理 0 件」。"""
    article_id, _ = _custom(settings)
    report = normalize_media(article_id, settings=settings)

    assert report.empty
    assert "没有可整理" in report.summary()


def test_fetched_article_is_left_alone(settings: Settings) -> None:
    """
    **抓取来的文章一律不整理。**它的素材是当时下载的历史档案，改名就是改写历史。

    这条守卫比「幂等」更要紧：幂等只保证重复跑不出错，而这一条保证**第一次跑**
    也不会去动一篇已有文章的 `images/`——即便里面真有名字不规范的文件
    （旧版本下载的、或人工丢进去的）。
    This guard matters more than idempotence: idempotence only says a second run is safe,
    whereas this says the first run never touches an existing article's assets — even when a
    non-compliant name genuinely is in there.
    """
    ledger = _ledger(settings)
    article_id, _ = ledger.register(
        RawItem(source_id="qbitai", via=SourceKind.RSS, url="https://e.com/a", title="抓来的")
    )
    directory = settings.output_path / "articles" / "20261004" / f"fetched__{article_id[:8]}"
    (directory / "images").mkdir(parents=True)
    # 故意放一个**不合规**的文件名：整理逻辑若跑起来一定会改名
    odd = directory / "images" / "微信图片.png"
    odd.write_bytes(PNG)
    ledger.set_store_dir(article_id, directory.relative_to(settings.output_path).as_posix())

    report = normalize_media(article_id, settings=settings)

    assert report.skipped, "抓取来的文章应当整趟跳过"
    assert "历史档案" in report.summary()
    assert not report.changed
    assert odd.is_file(), "文件必须原封不动，名字也不能改"
    assert not (directory / "images" / "01_custom.png").exists()


def test_missing_article_is_an_empty_report(settings: Settings) -> None:
    """不存在的文章返回空报告 / A missing article yields an empty report."""
    report = normalize_media("nope", settings=settings)
    assert report.empty
    assert not report.changed
