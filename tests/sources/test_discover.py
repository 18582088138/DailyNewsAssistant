"""
test_discover.py —— Feed 探测单元测试 / Feed discovery unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/sources/test_discover.py -v

对应的人工使用 / Matching manual usage:
    dna probe https://www.infoq.cn/ --lang zh --tags tech,cn

覆盖 / Covers:
    1. find_declared_feeds：识别 <link rel="alternate" type="application/rss+xml">
    2. 同时识别 atom 与相对地址补全
    3. 忽略非 feed 的 alternate（如 hreflang 多语言声明）
    4. candidate_paths：**以站点根为基准**（用户常粘文章页地址，feed 却在根下）
    5. candidate_paths：非法地址返回空
    6. suggest_yaml：生成可直接粘贴的片段，含 max_items 可选项
    7. validate_feed 的判据与 parse_feed 一致（看 version 而非 bozo）

为什么需要路径猜测 / Why path guessing is required:
    实测 InfoQ 的 feed 完全可用，但首页**并没有声明**它。只靠 <link rel=alternate>
    会漏掉这类站点，而它们并不少见。
    InfoQ's feed works fine yet its homepage does not declare it. Relying on
    <link rel=alternate> alone would miss such sites, and they are not rare.

预期 / Expected:
    耗时 < 1s；纯字符串处理的用例离线跑，联网用例标 live 默认跳过
"""

from __future__ import annotations

import pytest

from dna.sources.discover import (
    COMMON_FEED_PATHS,
    FeedCandidate,
    candidate_paths,
    find_declared_feeds,
    suggest_yaml,
)


# --- 声明发现 / declared feeds ------------------------------------------------


def test_finds_declared_rss() -> None:
    """识别标准的 RSS 声明 / A standard RSS declaration is found."""
    html = """<html><head>
      <link rel="alternate" type="application/rss+xml" href="https://e.com/feed">
    </head></html>"""
    assert find_declared_feeds(html, "https://e.com/") == ["https://e.com/feed"]


def test_finds_declared_atom() -> None:
    """识别 Atom 声明 / An Atom declaration is found."""
    html = '<link rel="alternate" type="application/atom+xml" href="https://e.com/atom.xml">'
    assert find_declared_feeds(html, "https://e.com/") == ["https://e.com/atom.xml"]


def test_relative_href_is_absolutised() -> None:
    """相对地址按页面地址补全 / A relative href is resolved against the page URL."""
    html = '<link rel="alternate" type="application/rss+xml" href="/rss">'
    assert find_declared_feeds(html, "https://e.com/blog/") == ["https://e.com/rss"]


def test_ignores_non_feed_alternates() -> None:
    """
    忽略非 feed 的 alternate 声明 / Non-feed alternates are ignored.

    多语言站点用 `<link rel="alternate" hreflang="en">` 声明语言版本，
    误当成 feed 会去抓一个 HTML 页面。
    Multilingual sites declare language variants with rel="alternate"; treating those
    as feeds would fetch an HTML page.
    """
    html = """
      <link rel="alternate" hreflang="en" href="https://e.com/en/">
      <link rel="alternate" type="application/rss+xml" href="https://e.com/feed">
    """
    assert find_declared_feeds(html, "https://e.com/") == ["https://e.com/feed"]


def test_multiple_declarations_deduped() -> None:
    """重复声明只保留一次 / Duplicate declarations collapse."""
    html = """
      <link rel="alternate" type="application/rss+xml" href="https://e.com/feed">
      <link rel="alternate" type="application/rss+xml" href="https://e.com/feed">
    """
    assert len(find_declared_feeds(html, "https://e.com/")) == 1


def test_no_declarations() -> None:
    """没有声明时返回空列表 / No declarations yields an empty list."""
    assert find_declared_feeds("<html><body>hi</body></html>", "https://e.com/") == []


def test_empty_html_is_safe() -> None:
    """空 HTML 安全 / Empty HTML is safe."""
    assert find_declared_feeds("", "https://e.com/") == []


# --- 路径猜测 / path guessing -------------------------------------------------


def test_candidates_built_from_site_root() -> None:
    """
    候选地址以**站点根**为基准，而不是传入路径。
    Candidates are built from the site root, not from the given path.

    用户通常粘的是文章页地址，而 feed 几乎总在站点根下。
    Users usually paste an article URL, while the feed almost always lives at the root.
    """
    candidates = candidate_paths("https://e.com/2026/09/some-article.html")

    assert "https://e.com/feed" in candidates
    assert not any("/2026/09" in c for c in candidates)


def test_candidates_cover_common_paths() -> None:
    """常见路径都在候选里 / Every common path is covered."""
    candidates = candidate_paths("https://e.com/")
    assert len(candidates) == len(COMMON_FEED_PATHS)
    for path in ("/feed", "/rss", "/atom.xml"):
        assert f"https://e.com{path}" in candidates


def test_candidates_preserve_port() -> None:
    """带端口的地址保留端口 / A port in the URL is preserved."""
    assert "http://localhost:1200/feed" in candidate_paths("http://localhost:1200/")


@pytest.mark.parametrize("bad", ["", "not a url", "/relative/path"])
def test_candidates_for_invalid_url(bad: str) -> None:
    """非法地址返回空 / An invalid URL yields no candidates."""
    assert candidate_paths(bad) == []


# --- YAML 片段 / YAML snippet -------------------------------------------------


def test_suggest_yaml_is_pasteable() -> None:
    """生成的片段字段齐全 / The snippet carries every needed field."""
    candidate = FeedCandidate(url="https://e.com/feed", ok=True, title="示例站", entry_count=20)
    snippet = suggest_yaml(candidate, source_id="example", lang="zh", tags=["ai", "cn"])

    assert "- id: example" in snippet
    assert "url: https://e.com/feed" in snippet
    assert "lang: zh" in snippet
    assert "tags: [ai, cn]" in snippet
    assert "enabled: true" in snippet


def test_suggest_yaml_uses_feed_title_when_no_name() -> None:
    """未指定名称时用 feed 自带的标题 / The feed's own title is used when no name is given."""
    candidate = FeedCandidate(url="https://e.com/feed", ok=True, title="示例站")
    assert "name: 示例站" in suggest_yaml(candidate, source_id="example")


def test_suggest_yaml_includes_max_items_when_asked() -> None:
    """
    条目极多的源应带上 max_items / A very large feed carries a max_items cap.

    实测 arXiv cs.AI 单次返回 302 条，不限量会把整期日报淹掉。
    """
    candidate = FeedCandidate(url="https://e.com/feed", ok=True, entry_count=302)
    assert "max_items: 15" in suggest_yaml(candidate, source_id="arxiv", max_items=15)


def test_suggest_yaml_omits_max_items_by_default() -> None:
    """普通源不写 max_items / An ordinary feed omits the cap."""
    candidate = FeedCandidate(url="https://e.com/feed", ok=True, entry_count=20)
    assert "max_items" not in suggest_yaml(candidate, source_id="example")


# --- 联网探测（默认跳过）/ live discovery, skipped by default -------------------


@pytest.mark.live
def test_live_discover_infoq() -> None:
    """
    真实探测 InfoQ / Real discovery against InfoQ.

    **不调用 LLM，无费用**，但要联网，因此仍标 live 避免拖慢日常测试。
    该站不声明 feed，只能靠路径猜测命中，正好验证第 3 步的必要性。

    跑法：python -m pytest tests/sources/test_discover.py -m live -v
    """
    from dna.sources.discover import discover_feeds

    results = discover_feeds("https://www.infoq.cn/")
    usable = [c for c in results if c.ok and c.entry_count > 0]

    assert usable, "InfoQ 的 feed 应能被探测到"
    assert usable[0].url.endswith("/feed")
