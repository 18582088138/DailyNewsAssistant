"""
配置必须真的生效 / Every setting must actually do something.

    $PY -m pytest tests/core/test_config_effective.py -q

由来见 [issues/014]。这一批 bug 的共同点是**配置写了、代码不听**，而且完全静默：
界面显示保存成功、`dna doctor` 照样把值列出来，行为一点没变。
所以这里钉三件事：

1. `.env` 里的代理真的接进 `os.environ`（httpx 走 trust_env，读的是那里）
2. `DEFAULT_LANGUAGE` / `LOG_LEVEL` 真的被前端读走，不再是写死的常量
3. **每个字段都有真实消费者** —— 一条遍历模型字段的静态断言，长期防回归
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dna.core.config import Profile, Settings, Tuning, apply_proxy_env
from dna.extract.article import extract_article
from dna.produce.tasks import normalize_lang

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 一、代理：.env → os.environ
# ---------------------------------------------------------------------------


def test_代理设置会写进环境变量(monkeypatch) -> None:
    """修复前这一步根本不存在：.env 里改代理等于没改。"""
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None, http_proxy="http://proxy:913", no_proxy="localhost")
    written = apply_proxy_env(settings)

    assert "HTTP_PROXY" in written
    # 大小写两份都要写：httpx / requests / urllib 各认一种拼写
    assert os.environ["HTTP_PROXY"] == "http://proxy:913"
    assert os.environ["http_proxy"] == "http://proxy:913"


def test_系统环境变量优先于_env(monkeypatch) -> None:
    """
    优先级是 env > config；已经在环境里的值不许被 .env 覆盖。

    注意 Windows 上 `os.environ` **大小写不敏感**——`HTTP_PROXY` 与 `http_proxy`
    是同一个键，所以这里只能设一次，不能设了大写再删小写。
    """
    monkeypatch.setenv("HTTP_PROXY", "http://system:8080")

    settings = Settings(_env_file=None, http_proxy="http://from-dotenv:913")
    written = apply_proxy_env(settings)

    assert "HTTP_PROXY" not in written
    assert os.environ["HTTP_PROXY"] == "http://system:8080"


def test_没配代理时什么都不写(monkeypatch) -> None:
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)

    assert apply_proxy_env(Settings(_env_file=None, http_proxy="", no_proxy="")) == []
    assert "HTTPS_PROXY" not in os.environ


# ---------------------------------------------------------------------------
# 二、默认语言与日志级别
# ---------------------------------------------------------------------------


def test_默认语言来自配置而不是写死的常量(monkeypatch) -> None:
    """
    修复前 `produce/tasks.py` 有一个同名的硬编码 "zh" 盖住了配置，
    于是界面上那个下拉、`.env` 里那一行都是摆设。
    """
    import dna.core.config as config_module

    monkeypatch.setenv("DEFAULT_LANGUAGE", "en")
    config_module.get_settings.cache_clear()
    try:
        assert normalize_lang("") == "en"
        assert normalize_lang(None) == "en"
        # 显式传值仍然说话最响
        assert normalize_lang("zh") == "zh"
        # 不认识的值退回配置里那个，而不是退回 "zh"
        assert normalize_lang("de") == "en"
    finally:
        config_module.get_settings.cache_clear()


def test_日志级别留空才用前端默认() -> None:
    """
    留空是刻意的：CLI 的输出是给人读的表格，掺进 INFO 会冲烂；
    GUI 那个终端不是界面，多点上下文只有好处。所以不能给统一默认值。
    """
    assert Settings(_env_file=None).log_level == ""
    assert Settings(_env_file=None, log_level="DEBUG").log_level == "DEBUG"


def test_cli_把配置里的日志级别传给_setup_logging(monkeypatch) -> None:
    """钉的是意图：级别从哪里来，而不是它等于什么。"""
    import dna.core.config as config_module
    import frontends.cli.app as cli  # callback 与 setup_logging 都在装配根里

    seen: dict[str, str] = {}
    monkeypatch.setattr(cli, "setup_logging", lambda **kw: seen.update(level=kw["level"]))
    monkeypatch.setattr(cli, "apply_proxy_env", lambda *_a, **_k: [])

    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    config_module.get_settings.cache_clear()
    try:
        cli._before_any_command()
        assert seen["level"] == "ERROR"
    finally:
        config_module.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 三、配图上限：配置里写多少就抓多少
# ---------------------------------------------------------------------------


def _html_with_images(count: int) -> str:
    body = "".join(
        f'<p>段落{i} 这里要够长才算得上正文，凑够字数让抽取不降级。</p>'
        f'<img src="https://example.com/pic{i}.jpg" width="800" height="600">'
        for i in range(count)
    )
    return f"<html><head><title>测试</title></head><body><article>{body}</article></body></html>"


@pytest.mark.parametrize("cap", [3, 12, 40])
def test_配图上限一路传到抽取阶段(cap: int) -> None:
    """
    修复前 `extract_article` 不接这个参数，抽取阶段先按写死的 10 砍一刀，
    于是 profile.yaml 里写 100 也只会存到 10 —— 而且完全静默（issues/014）。
    """
    article = extract_article(_html_with_images(50), "https://example.com/a", max_images=cap)
    images = [m for m in article.media if m.kind.value == "image"]
    assert len(images) == cap


def test_不传上限时仍然有兜底() -> None:
    article = extract_article(_html_with_images(50), "https://example.com/a")
    images = [m for m in article.media if m.kind.value == "image"]
    assert 0 < len(images) <= 50


# ---------------------------------------------------------------------------
# 四、静态断言：每个字段都得有人读
# ---------------------------------------------------------------------------

# 只被「显示配置」读的字段。它们不驱动行为，但摆出来是有用的——
# 前提是**明确知道**它们只是展示，而不是以为配了就生效。
DISPLAY_ONLY: frozenset[str] = frozenset(
    {
        "inbox_provider",     # dna doctor / dna config 显示；P9 模块未实现
        "inbox_enabled",      # 同上
        "feishu_app_id",      # P9 有完整部署文档，代码未实现
        "feishu_app_secret",
        "feishu_allowed_users",
        "embedding_model",    # 二级聚类默认关闭，开启时才读
        "vllm_base_url",      # provider 可互换，vLLM 在 Linux 上才用
        "vllm_model",
        "openvino_llm_dir",   # 本地 LLM 已冻结，接口保留
        "openvino_llm_device",
    }
)


def _sources() -> str:
    """
    `src/` 与 `frontends/` 的全部 Python 源码，**剥掉行注释**后拼成一串供查引用。

    必须剥注释：不剥的话，一句「这里曾经读 `profile.languages`」的注释就能让
    一个真正的假配置通过检查 —— 实测正是这么被骗过一次的。
    （三引号文档字符串里的提及仍然能骗过它，那是这条启发式已知的上限。）
    """
    parts = []
    for root in ("src", "frontends"):
        for path in (REPO / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                code = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
                parts.append(code)
    return "\n".join(parts)


@pytest.mark.parametrize("model", [Settings, Profile, Tuning], ids=["Settings", "Profile", "Tuning"])
def test_没有配了不生效的字段(model) -> None:
    """
    遍历模型的每个字段，要求它在 `src/` 或 `frontends/` 里至少被引用一次
    （定义那一行不算）。

    这条测试是 issues/014 的长期护栏：假配置比魔数更坏 —— 用户改了，
    界面显示保存成功，行为一点没变。以前有 18 个这样的字段。

    **`Tuning` 必须单独点名。** 只查 `Profile` 的话，嵌套模型整体算「被引用过」
    （`profile.tuning` 有人读），里面每一项是死的都查不出来 —— 本批加 `Tuning`
    时就差点这么放进去 8 个新的假配置。
    """
    code = _sources()
    orphans = []
    for name in model.model_fields:
        if name in DISPLAY_ONLY:
            continue
        # 定义那一行长得像 `name: int = 3`，引用长得像 `.name` 或 `"name"`
        if f".{name}" in code or f'"{name}"' in code or f"'{name}'" in code:
            continue
        orphans.append(name)

    assert not orphans, (
        f"{model.__name__} 有配了不生效的字段：{orphans}。"
        "要么接上真实消费者，要么删掉——留一个改了没反应的开关比没有更坏。"
    )
