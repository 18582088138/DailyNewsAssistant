"""
test_config.py —— 配置加载单元测试 / Configuration loading unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/core/test_config.py -v

覆盖 / Covers:
    1. Settings 默认值正确，且不依赖本机 .env
    2. .env 文件能被正确读取并覆盖默认值
    3. 相对路径按仓库根解析为绝对路径（output_path / data_path / db_file）
    4. 飞书白名单解析：逗号分隔、去空格、去重；**空值必须解析为空列表（拒收全部）**
    5. api_key_for() 对云端 provider 返回 key，对本地 provider 返回空串
    6. 代理配置：localhost_bypasses_proxy() 能识别 NO_PROXY 漏放行 localhost 的情况
    7. load_sources()：只返回 enabled 的源、id 重复报错、文件缺失报 ConfigError
    8. load_profile()：正常解析，字段类型正确
    9. 仓库自带的 config/sources.yaml 与 config/profile.yaml 能被真实加载

预期 / Expected:
    20 passed；耗时 < 2s；全程不联网、不读写真实 outputs/ 与 data/
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dna.core.config import (
    PROJECT_ROOT,
    Profile,
    Settings,
    load_profile,
    load_sources,
)
from dna.core.errors import ConfigError
from dna.core.models import Language, SourceKind


# --- Settings 基础 / Settings basics -----------------------------------------


def test_settings_defaults_without_env_file() -> None:
    """不读 .env 时应落到代码里的默认值 / Falls back to in-code defaults."""
    s = Settings(_env_file=None)
    assert s.llm_provider == "deepseek"
    assert s.default_language is Language.ZH
    assert s.digest_max_entries == 15
    assert s.inbox_provider == "feishu"
    assert s.inbox_enabled is False  # 开发机默认不启用飞书投递


def test_settings_reads_env_file(tmp_path: Path) -> None:
    """.env 中的值应覆盖默认值 / Values in .env override the defaults."""
    env = tmp_path / ".env"
    env.write_text(
        "LLM_PROVIDER=ollama\n"
        "DIGEST_MAX_ENTRIES=7\n"
        "DEFAULT_LANGUAGE=en\n"
        "INBOX_ENABLED=true\n",
        encoding="utf-8",
    )
    s = Settings(_env_file=env)
    assert s.llm_provider == "ollama"
    assert s.digest_max_entries == 7
    assert s.default_language is Language.EN
    assert s.inbox_enabled is True


def test_relative_paths_resolve_against_project_root() -> None:
    """相对路径应解析到仓库根下 / Relative paths resolve under the repository root."""
    s = Settings(_env_file=None, output_dir=Path("./outputs"), db_path=Path("./data/dna.db"))
    assert s.output_path.is_absolute()
    assert s.output_path == (PROJECT_ROOT / "outputs").resolve()
    assert s.db_file == (PROJECT_ROOT / "data" / "dna.db").resolve()


def test_absolute_paths_are_left_alone(tmp_path: Path) -> None:
    """绝对路径不应被再次拼接 / Absolute paths are used as-is."""
    s = Settings(_env_file=None, output_dir=tmp_path / "out")
    assert s.output_path == tmp_path / "out"


# --- 白名单解析（安全相关）/ whitelist parsing (security-critical) -------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),  # 空 = 拒收全部，这是刻意的安全默认
        ("   ", []),
        ("ou_a", ["ou_a"]),
        ("ou_a,ou_b", ["ou_a", "ou_b"]),
        (" ou_a , ou_b ,, ", ["ou_a", "ou_b"]),  # 去空格、忽略空项
        ("ou_a,ou_a,ou_b", ["ou_a", "ou_b"]),  # 去重且保序
    ],
)
def test_feishu_allowed_users_parsing(raw: str, expected: list[str]) -> None:
    """飞书白名单解析 / Feishu whitelist parsing; empty must mean 'reject everyone'."""
    s = Settings(_env_file=None, feishu_allowed_users=raw)
    assert s.feishu_allowed_user_list == expected


def test_api_key_lookup() -> None:
    """云端 provider 返回 key，本地 provider 返回空串 / Key lookup per provider."""
    s = Settings(_env_file=None, deepseek_api_key="sk-ds", openrouter_api_key="sk-or")
    assert s.api_key_for("deepseek") == "sk-ds"
    assert s.api_key_for("DeepSeek") == "sk-ds"  # 大小写不敏感
    assert s.api_key_for("openrouter") == "sk-or"
    assert s.api_key_for("ollama") == ""
    assert s.api_key_for("unknown") == ""


# --- 代理配置 / proxy configuration ------------------------------------------


def test_no_proxy_defaults_bypass_localhost() -> None:
    """默认 NO_PROXY 必须放行本地回环 / The default NO_PROXY must bypass loopback."""
    s = Settings(_env_file=None, https_proxy="http://child-prc.intel.com:913")
    assert s.proxy_enabled is True
    assert s.localhost_bypasses_proxy() is True
    assert "localhost" in s.no_proxy_list


def test_no_proxy_missing_localhost_is_detected() -> None:
    """
    NO_PROXY 漏了 localhost 必须能被检出。
    A NO_PROXY that omits localhost must be detected — otherwise local Ollama and
    RSSHub calls silently get routed through the corporate proxy.
    """
    s = Settings(
        _env_file=None,
        https_proxy="http://child-prc.intel.com:913",
        no_proxy="10.0.0.0/8",
    )
    assert s.localhost_bypasses_proxy() is False


def test_no_proxy_irrelevant_without_proxy() -> None:
    """未配代理时不应误报 / With no proxy configured, the check must not complain."""
    s = Settings(_env_file=None, http_proxy="", https_proxy="", no_proxy="")
    assert s.proxy_enabled is False
    assert s.localhost_bypasses_proxy() is True


# --- YAML 层 / the YAML layer ------------------------------------------------


def _write_sources(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_sources_returns_only_enabled(tmp_path: Path) -> None:
    """只返回启用的源 / Disabled sources are filtered out."""
    path = _write_sources(
        tmp_path,
        """
sources:
  - id: a
    name: A
    kind: rss
    url: https://a.com/feed
    enabled: true
  - id: b
    name: B
    kind: rss
    url: https://b.com/feed
    enabled: false
""",
    )
    sources = load_sources(path)
    assert [s.id for s in sources] == ["a"]
    assert sources[0].kind is SourceKind.RSS


def test_load_sources_rejects_duplicate_ids(tmp_path: Path) -> None:
    """id 重复必须报错，避免后续静默覆盖 / Duplicate ids must raise."""
    path = _write_sources(
        tmp_path,
        """
sources:
  - id: dup
    name: A
    url: https://a.com/feed
  - id: dup
    name: B
    url: https://b.com/feed
""",
    )
    with pytest.raises(ConfigError, match="duplicate source id"):
        load_sources(path)


def test_load_sources_missing_file_raises(tmp_path: Path) -> None:
    """文件缺失应抛 ConfigError 而不是 FileNotFoundError / Missing file raises ConfigError."""
    with pytest.raises(ConfigError, match="config file not found"):
        load_sources(tmp_path / "nope.yaml")


def test_load_profile(tmp_path: Path) -> None:
    """偏好文件解析 / Profile parsing."""
    path = tmp_path / "profile.yaml"
    path.write_text(
        """
focus_keywords: [大模型, agent]
exclude_keywords: [招聘]
languages: [zh, en]
digest_max_entries: 8
video_duration_seconds: [20, 25]
""",
        encoding="utf-8",
    )
    p = load_profile(path)
    assert p.focus_keywords == ["大模型", "agent"]
    assert p.languages == [Language.ZH, Language.EN]
    assert p.digest_max_entries == 8
    assert p.video_duration_seconds == (20, 25)


def test_profile_defaults() -> None:
    """偏好默认值 / Profile defaults."""
    p = Profile()
    assert p.languages == [Language.ZH]
    assert p.summary_max_sentences == 2


# --- 仓库自带配置必须可加载 / shipped config must load -------------------------


def test_shipped_config_files_load() -> None:
    """
    仓库里的 config/sources.yaml 与 profile.yaml 必须能被真实加载。
    The config files shipped in the repo must actually parse — this catches typos
    committed by hand.
    """
    sources = load_sources()
    assert len(sources) > 0
    assert all(s.enabled for s in sources)

    profile = load_profile()
    assert profile.digest_max_entries > 0
    assert len(profile.focus_keywords) > 0
