"""
test_article_store.py —— 文章落盘单元测试 / Article persistence unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/store/test_article_store.py -v

对应的人工验证 / Matching manual check:
    dna add <某篇文章链接>
    dna show <id>          # 显示落盘位置
    然后直接打开 data/articles/<日期>/<slug>__<id>/article.md 核对

覆盖 / Covers:
    1. 目录命名：日期 + 标题 slug + id 后缀；同标题不同文章不冲突
    2. article.md：YAML frontmatter 可解析、正文完整、标题里的冒号被正确转义
    3. 降级抽取时 article.md 里带明显的警示，而不是让人以为抓成功了
    4. meta.json：完整的 Article 结构可被反序列化回来
    5. references.md：列出原文链接与每张图的出处
    6. 图片下载：**按文件头判断格式**，不信 URL 后缀
    7. 图片下载：**过小的文件被跳过**（下载后才认得出的图标）
    8. 图片下载：每张图配一个同名 .json 记录出处
    9. **单张图失败不影响正文落盘**，失败原因记进 skipped_images
   10. 下载失败/格式无法识别时的处理

为什么图片出处要写在图片旁边 / Why attribution sits next to each image:
    产物要发到公开平台，每张图都要能追溯出处。写在图片旁边而不是只写在数据库里，
    是为了图片被单独拷走时信息不丢——实际使用中图片一定会被单独拷来拷去。
    Published output needs traceable attribution. Keeping it beside the file means the
    information survives when an image is copied out on its own, which always happens.

预期 / Expected:
    耗时 < 2s；只写 tmp_path，图片下载全部被 monkeypatch 拦截，不联网
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from dna.core.models import Article, MediaAsset, MediaKind
from dna.store import article_render, article_store
from dna.store.article_store import article_dir, save_article

NOW = datetime(2026, 9, 2, 10, 0, 0)

# 各种格式的最小文件头 / minimal magic bytes per format
JPEG = b"\xff\xd8\xff" + b"x" * 20_000
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20_000
TINY = b"\xff\xd8\xff" + b"x" * 100  # 太小，应被跳过


def make_article(
    *, title: str = "某公司发布多模态大模型", text: str = "正文内容。" * 50, ok: bool = True,
    images: list[str] | None = None,
) -> Article:
    """构造一篇抽取结果 / Build an extraction result."""
    return Article(
        url="https://news.example.com/posts/1",
        title=title,
        text=text,
        author="张三",
        published_at=NOW,
        extraction_ok=ok,
        media=[
            MediaAsset(
                kind=MediaKind.IMAGE,
                url=url,
                source_url="https://news.example.com/posts/1",
                credit="news.example.com",
                caption="配图说明",
            )
            for url in (images or [])
        ],
    )


class FakeDownloads(dict):
    """
    预置的图片响应 + 调用记录 / Canned image responses plus a call log.

    像普通 dict 一样按 URL 存字节内容；`calls` 里记录每次调用的参数，
    用于断言 Referer 这类请求头是否正确传出。
    Behaves as a plain dict keyed by URL, while `calls` records the arguments of each
    invocation so request details such as the Referer can be asserted on.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, object]] = []


@pytest.fixture
def fake_download(monkeypatch: pytest.MonkeyPatch) -> FakeDownloads:
    """
    拦截图片下载 / Intercept image downloads.

    测试必须离线：返回预置的字节内容，按 URL 决定给什么。
    Tests must stay offline; canned bytes are returned per URL.
    """
    downloads = FakeDownloads()

    def fake_fetch(url: str, **kwargs: object) -> bytes:
        downloads.calls.append({"url": url, **kwargs})
        if url not in downloads:
            raise RuntimeError(f"模拟下载失败：{url}")
        return downloads[url]

    monkeypatch.setattr(article_store, "fetch_bytes", fake_fetch)
    return downloads


# --- 目录命名 / directory naming ----------------------------------------------


def test_directory_name_has_date_slug_and_id(tmp_path: Path) -> None:
    """目录名含日期、标题 slug 与 id 后缀 / The name carries date, slug and id suffix."""
    directory = article_dir(tmp_path, "abcdef1234567890", "某公司发布模型", NOW)

    assert directory.parent.name == "20260902"
    assert directory.name.startswith("某公司发布模型__")
    assert directory.name.endswith("abcdef12")


def test_same_title_different_articles_do_not_collide(tmp_path: Path) -> None:
    """
    同标题的不同文章不会撞目录 / Two articles sharing a title never collide.

    多个源转载同一篇时标题完全相同，靠 id 后缀区分。
    """
    a = article_dir(tmp_path, "1111111111111111", "同样的标题", NOW)
    b = article_dir(tmp_path, "2222222222222222", "同样的标题", NOW)
    assert a != b


def test_directory_is_created_on_save(tmp_path: Path, fake_download: FakeDownloads) -> None:
    """落盘时自动建目录 / The directory is created when saving."""
    saved = save_article(make_article(), "abc123", root=tmp_path, when=NOW)
    assert saved.directory.is_dir()


# --- article.md ---------------------------------------------------------------


def test_article_markdown_has_parseable_frontmatter(
    tmp_path: Path, fake_download: FakeDownloads
) -> None:
    """frontmatter 可被 YAML 解析 / The frontmatter parses as YAML."""
    import yaml

    saved = save_article(make_article(), "abc123", root=tmp_path, when=NOW)
    content = saved.article_path.read_text(encoding="utf-8")
    front = yaml.safe_load(content.split("---")[1])

    assert front["id"] == "abc123"
    assert front["extraction_ok"] is True
    assert front["text_length"] > 0


def test_title_with_colon_is_escaped(tmp_path: Path, fake_download: FakeDownloads) -> None:
    """
    标题里的冒号必须转义 / A colon in the title must be escaped.

    「发布：新一代模型」这种标题很常见，不加引号会让 frontmatter 解析直接失败。
    Titles like "发布：新一代模型" are common and would break the frontmatter unquoted.
    """
    import yaml

    saved = save_article(
        make_article(title="重磅发布: 新一代模型"), "abc123", root=tmp_path, when=NOW
    )
    front = yaml.safe_load(saved.article_path.read_text(encoding="utf-8").split("---")[1])

    assert "新一代模型" in front["title"]


def test_body_text_is_written(tmp_path: Path, fake_download: FakeDownloads) -> None:
    """正文完整写入 / The body text is written in full."""
    saved = save_article(make_article(text="独特的正文标记"), "abc", root=tmp_path, when=NOW)
    assert "独特的正文标记" in saved.article_path.read_text(encoding="utf-8")


def test_degraded_extraction_is_flagged_in_markdown(
    tmp_path: Path, fake_download: FakeDownloads
) -> None:
    """
    **降级抽取必须在文件里明确标出。**
    A degraded extraction must be flagged inside the file.

    否则人工核对时会以为抓成功了，只是这篇文章正文本来就短。
    Otherwise a manual check would assume the fetch succeeded and the article was simply
    short.
    """
    saved = save_article(
        make_article(text="", ok=False), "abc", root=tmp_path, when=NOW
    )
    content = saved.article_path.read_text(encoding="utf-8")

    assert "降级" in content
    assert "extraction_ok: false" in content


# --- meta.json ----------------------------------------------------------------


def test_meta_json_roundtrips(tmp_path: Path, fake_download: FakeDownloads) -> None:
    """
    meta.json 能反序列化回 Article / meta.json deserialises back into an Article.

    程序后续要复用抽取结果（比如重做输出而不重抓），必须能原样读回来。
    Later stages reuse the extraction result — regenerating an output without
    re-fetching — so it must load back unchanged.
    """
    original = make_article(images=["https://cdn.example.com/a.jpg"])
    saved = save_article(original, "abc", root=tmp_path, when=NOW)

    restored = Article.model_validate_json(saved.meta_path.read_text(encoding="utf-8"))
    assert restored.title == original.title
    assert restored.media[0].source_url == original.media[0].source_url


# --- references.md ------------------------------------------------------------


def test_references_lists_source_url(tmp_path: Path, fake_download: FakeDownloads) -> None:
    """来源清单列出原文链接 / The reference file lists the article URL."""
    saved = save_article(make_article(), "abc", root=tmp_path, when=NOW)
    assert "https://news.example.com/posts/1" in saved.references_path.read_text(encoding="utf-8")


def test_references_lists_downloaded_images(
    tmp_path: Path, fake_download: FakeDownloads
) -> None:
    """来源清单列出已下载的图及其出处 / Downloaded images and their origins are listed."""
    fake_download["https://cdn.example.com/a.jpg"] = JPEG
    saved = save_article(
        make_article(images=["https://cdn.example.com/a.jpg"]), "abc", root=tmp_path, when=NOW
    )
    content = saved.references_path.read_text(encoding="utf-8")

    assert "https://cdn.example.com/a.jpg" in content
    assert "news.example.com" in content


# --- 图片下载 / image downloading ---------------------------------------------


def test_image_is_downloaded_with_sidecar(
    tmp_path: Path, fake_download: FakeDownloads
) -> None:
    """
    每张图配一个同名 .json 记录出处。
    Each image gets a sidecar .json recording where it came from.
    """
    fake_download["https://cdn.example.com/a.jpg"] = JPEG
    saved = save_article(
        make_article(images=["https://cdn.example.com/a.jpg"]), "abc", root=tmp_path, when=NOW
    )

    assert saved.image_count == 1
    image_path = saved.image_paths[0]
    assert image_path.exists()

    sidecar = json.loads(image_path.with_suffix(image_path.suffix + ".json").read_text("utf-8"))
    assert sidecar["source_url"] == "https://news.example.com/posts/1"
    assert sidecar["image_url"] == "https://cdn.example.com/a.jpg"
    assert sidecar["credit"] == "news.example.com"


def test_referer_is_sent_when_downloading(
    tmp_path: Path, fake_download: FakeDownloads
) -> None:
    """
    **下载配图必须带上原文地址作为 Referer。**
    Image downloads must carry the article URL as the Referer.

    真机验证发现：量子位的图床 `i.qbitai.com` 做了防盗链，不带 Referer 时
    **每一张图都返回 403**，带上立刻 200。多数中文站点的图床都是这样。
    这也是 MediaAsset.source_url 必须在抽取时就记下来的另一个理由。
    Live verification found that 量子位's image host returns 403 for every image
    unless the Referer is supplied. Most Chinese image hosts behave this way, which is
    a further reason MediaAsset.source_url must be captured at extraction time.

    见 docs/issues/004-image-hotlink-protection.md
    """
    fake_download["https://cdn.example.com/a.jpg"] = JPEG
    save_article(
        make_article(images=["https://cdn.example.com/a.jpg"]), "abc", root=tmp_path, when=NOW
    )

    assert fake_download.calls, "应当发起了下载"
    assert fake_download.calls[0]["referer"] == "https://news.example.com/posts/1"


def test_extension_from_magic_bytes_not_url(
    tmp_path: Path, fake_download: FakeDownloads
) -> None:
    """
    **格式按文件头判断，不信 URL 后缀。**
    The format comes from the magic bytes, never the URL extension.

    URL 后缀经常骗人：`.php` 端点返回 JPEG、`.jpg` 地址返回 HTML 错误页都很常见。
    Extensions lie routinely — a `.php` endpoint serving JPEG, or a `.jpg` URL
    returning an HTML error page.
    """
    fake_download["https://cdn.example.com/image.php?id=1"] = PNG
    saved = save_article(
        make_article(images=["https://cdn.example.com/image.php?id=1"]),
        "abc",
        root=tmp_path,
        when=NOW,
    )
    assert saved.image_paths[0].suffix == ".png"


def test_tiny_image_is_skipped(tmp_path: Path, fake_download: FakeDownloads) -> None:
    """
    **过小的文件被跳过。**
    Files that are too small are skipped.

    抽取阶段只能按 HTML 属性判尺寸，很多站点不写宽高；真正的图标要下载后
    看字节数才认得出来。
    At extraction time only the declared dimensions are available and many sites omit
    them, so genuine icons can only be recognised by byte size after downloading.
    """
    fake_download["https://cdn.example.com/icon.jpg"] = TINY
    saved = save_article(
        make_article(images=["https://cdn.example.com/icon.jpg"]), "abc", root=tmp_path, when=NOW
    )

    assert saved.image_count == 0
    assert any("过小" in reason for _, reason in saved.skipped_images)


def test_unrecognised_format_is_skipped(
    tmp_path: Path, fake_download: FakeDownloads
) -> None:
    """无法识别的格式被跳过 / An unrecognised format is skipped."""
    fake_download["https://cdn.example.com/thing"] = b"<html>not an image</html>" * 1000
    saved = save_article(
        make_article(images=["https://cdn.example.com/thing"]), "abc", root=tmp_path, when=NOW
    )

    assert saved.image_count == 0
    assert any("格式" in reason for _, reason in saved.skipped_images)


def test_download_failure_does_not_block_article(
    tmp_path: Path, fake_download: FakeDownloads
) -> None:
    """
    **单张图下载失败不影响正文落盘。**
    A failed image download never blocks the body from being written.

    图片下载是最容易失败的一环（防盗链、CDN 限流）。因为一张图丢掉整篇正文
    是不划算的。
    Image downloads fail most often — hotlink protection, CDN throttling — and losing a
    whole article over one image would be the wrong trade.
    """
    fake_download["https://cdn.example.com/ok.jpg"] = JPEG
    # 第二张未登记 → 下载时抛异常
    saved = save_article(
        make_article(images=["https://cdn.example.com/ok.jpg", "https://cdn.example.com/bad.jpg"]),
        "abc",
        root=tmp_path,
        when=NOW,
    )

    assert saved.article_path.exists()
    assert saved.image_count == 1
    assert any("下载失败" in reason for _, reason in saved.skipped_images)


def test_max_images_is_respected(tmp_path: Path, fake_download: FakeDownloads) -> None:
    """图片数量上限生效 / The image cap is applied."""
    urls = [f"https://cdn.example.com/{i}.jpg" for i in range(5)]
    for url in urls:
        fake_download[url] = JPEG

    saved = save_article(
        make_article(images=urls), "abc", root=tmp_path, when=NOW, max_images=2
    )
    assert saved.image_count == 2


def test_images_can_be_skipped_entirely(tmp_path: Path, fake_download: FakeDownloads) -> None:
    """
    可以关掉图片下载 / Image downloading can be turned off.

    只验证正文抽取时不必等图片下载，快很多。
    Verifying body extraction alone is much faster without waiting on images.
    """
    fake_download["https://cdn.example.com/a.jpg"] = JPEG
    saved = save_article(
        make_article(images=["https://cdn.example.com/a.jpg"]),
        "abc",
        root=tmp_path,
        when=NOW,
        download_images=False,
    )

    assert saved.image_count == 0
    assert saved.article_path.exists()


# --- 人工补正文 / hand-written bodies -----------------------------------------


def test_degraded_article_gets_a_paste_marker(tmp_path: Path) -> None:
    """
    降级文章的 article.md 里要有明确的粘贴位置。

    知乎、小红书这类站点抓不到正文，只能人工补。没有标记的话，人不知道该往哪贴，
    程序也不知道该从哪读回来。
    """
    saved = save_article(make_article(text="", ok=False), "abc12345", root=tmp_path, when=NOW)
    body = saved.article_path.read_text(encoding="utf-8")

    assert article_store.MANUAL_BODY_MARKER in body
    assert "dna sync abc12345" in body


def test_read_body_returns_pasted_text(tmp_path: Path) -> None:
    """人工贴在标记之后的内容能被原样读回来，操作提示行不算正文。"""
    saved = save_article(make_article(text="", ok=False), "abc12345", root=tmp_path, when=NOW)

    original = saved.article_path.read_text(encoding="utf-8")
    saved.article_path.write_text(original + "\n这是我手动补的正文。\n第二段。\n", encoding="utf-8")

    body = article_render.read_body(saved.article_path)

    assert body == "这是我手动补的正文。\n第二段。"
    assert "粘贴" not in body  # 操作提示行必须被剔掉


def test_read_body_of_a_normal_article_skips_frontmatter(tmp_path: Path) -> None:
    """
    正常抓取的文章没有粘贴标记，读回时要跳过 frontmatter、标题行与来源行。

    否则 `dna sync` 会把 YAML 元信息当成正文写回台账。
    """
    saved = save_article(make_article(text="第一段。\n第二段。"), "abc", root=tmp_path, when=NOW)

    body = article_render.read_body(saved.article_path)

    assert body == "第一段。\n第二段。"
    assert "extraction_ok" not in body
    assert "来源：" not in body


def test_read_body_of_an_untouched_degraded_article_is_empty(tmp_path: Path) -> None:
    """
    还没人工补写时读回来必须是空的。

    读回一句提示文字会让台账显示「已有正文 20 字」，比显示 0 字更糟——
    后续流程会拿这句提示去做摘要。
    """
    saved = save_article(make_article(text="", ok=False), "abc", root=tmp_path, when=NOW)

    assert article_render.read_body(saved.article_path) == ""


# --- 旧目录清理 / stale directory cleanup --------------------------------------


def test_stale_directory_of_the_same_article_is_removed(tmp_path: Path) -> None:
    """
    同一篇文章换了标题后，旧目录必须被清掉。

    目录名带标题，重抓拿到正确标题就会换一个新目录；旧的留在盘上，同一个 id
    出现两份，人分不清哪份是新的（issues/004-B 修复后实际发生过）。
    """
    save_article(make_article(title="被污染的站点通用标题"), "abc12345", root=tmp_path, when=NOW)
    save_article(make_article(title="真正的文章标题"), "abc12345", root=tmp_path, when=NOW)

    dirs = sorted(p.name for p in (tmp_path / "articles" / "20260902").iterdir())

    assert len(dirs) == 1
    assert "真正的文章标题" in dirs[0]


def test_cleanup_never_touches_another_article(tmp_path: Path) -> None:
    """清理只认同一个 id，绝不能误删别的文章。"""
    save_article(make_article(title="第一篇"), "aaaaaaaa", root=tmp_path, when=NOW)
    save_article(make_article(title="第二篇"), "bbbbbbbb", root=tmp_path, when=NOW)
    save_article(make_article(title="第一篇改名了"), "aaaaaaaa", root=tmp_path, when=NOW)

    dirs = sorted(p.name for p in (tmp_path / "articles" / "20260902").iterdir())

    assert len(dirs) == 2
    assert any("第二篇" in d for d in dirs)
    assert any("第一篇改名了" in d for d in dirs)


# --- 视频 / videos -------------------------------------------------------------


def test_videos_are_listed_in_references(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """下载成功的视频要出现在 references.md 里，带文件名与大小。"""
    article = make_article()
    article.media.append(
        MediaAsset(
            kind=MediaKind.VIDEO,
            url="https://cdn.example.com/clip.mp4",
            source_url="https://news.example.com/posts/1",
            credit="news.example.com",
        )
    )

    def fake_download(assets, directory, **kwargs):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "01_cdn-example-com.mp4"
        path.write_bytes(b"\x00" * (2 * 1024 * 1024))
        return [path], []

    monkeypatch.setattr(article_store, "download_videos", fake_download)
    saved = save_article(article, "abc", root=tmp_path, when=NOW)

    references = saved.references_path.read_text(encoding="utf-8")

    assert saved.video_count == 1
    assert "已下载的视频" in references
    assert "videos/01_cdn-example-com.mp4" in references
    assert "2.0 MB" in references


def test_undownloadable_video_still_shows_its_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    视频下不下来时，地址仍要写进 references.md。

    平台封禁下载很常见，但链接本身还有价值——人可以点开看，也能手动录屏。
    """
    article = make_article()
    article.media.append(
        MediaAsset(
            kind=MediaKind.VIDEO,
            url="https://v.qq.com/x/page/abc.html",
            source_url="https://news.example.com/posts/1",
        )
    )

    monkeypatch.setattr(
        article_store,
        "download_videos",
        lambda assets, directory, **kw: ([], [("https://v.qq.com/x/page/abc.html", "平台不支持")]),
    )
    saved = save_article(article, "abc", root=tmp_path, when=NOW)

    references = saved.references_path.read_text(encoding="utf-8")

    assert saved.video_count == 0
    assert "未下载的视频" in references
    assert "https://v.qq.com/x/page/abc.html" in references
    assert "平台不支持" in references


def test_videos_can_be_skipped_entirely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """可以只关视频不关图片——视频比图片慢一个数量级，调试正文时要能跳过。"""
    called = False

    def spy(*args, **kwargs):
        nonlocal called
        called = True
        return [], []

    monkeypatch.setattr(article_store, "download_videos", spy)
    save_article(make_article(), "abc", root=tmp_path, when=NOW, download_videos_too=False)

    assert called is False


def test_refetch_clears_images_left_by_the_previous_run(
    tmp_path: Path, fake_download: FakeDownloads
) -> None:
    """
    重抓时上一轮的图片必须被清掉。

    references.md 按本轮结果重新生成，旧文件留着就成了孤儿：盘上 3 张、清单里 2 张，
    多出来的那张没有出处可查，做素材时还会被误当成本篇配图用出去。
    实测重抓微信文章时，被新过滤规则剔掉的作者头像正是这样留下来的。
    """
    fake_download["https://cdn.example.com/a.jpg"] = JPEG
    fake_download["https://cdn.example.com/b.jpg"] = JPEG

    first = save_article(
        make_article(images=["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"]),
        "abc",
        root=tmp_path,
        when=NOW,
    )
    assert first.image_count == 2

    # 第二轮只剩一张图（过滤规则收紧、或站点删了图）
    second = save_article(
        make_article(images=["https://cdn.example.com/a.jpg"]), "abc", root=tmp_path, when=NOW
    )

    on_disk = [p for p in (second.directory / "images").glob("*") if p.suffix != ".json"]
    assert second.image_count == 1
    assert len(on_disk) == 1, "上一轮的图片没有被清掉，会变成没有出处的孤儿文件"


def test_read_title_picks_up_a_hand_edited_heading(tmp_path: Path) -> None:
    """
    人工改过的标题要能读回来。

    抓取失败的文章标题是从 URL 推出来的（`zhuanlan.zhihu.com p 2067…`），
    根本不能当日报标题用；补正文的人通常顺手就把标题改对了。
    """
    saved = save_article(make_article(text="", ok=False), "abc", root=tmp_path, when=NOW)

    original = saved.article_path.read_text(encoding="utf-8")
    edited = original.replace("# 某公司发布多模态大模型", "# 我改对的标题")
    saved.article_path.write_text(edited, encoding="utf-8")

    assert article_render.read_title(saved.article_path) == "我改对的标题"
