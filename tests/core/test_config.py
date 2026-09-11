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
   10. 仓库根的三种定位：`DNA_HOME` 覆盖 > 打包后用 exe 所在目录 > 源码上溯三层
   11. 六个区间**写反就报错**（上下限相等仍然合法）——设置面板把它们全放开了
   12. 已实测失效/反爬的源不再出现在 config/sources.yaml 里（可用但关闭的那批仍在）

预期 / Expected:
    耗时 < 2s；全程不联网、不读写真实 outputs/ 与 data/
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

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


@pytest.mark.parametrize(
    "field",
    [
        "summary_chars",
        "shortvideo_chars",
        "narration_chars",
        "video_duration_seconds",
        "narration_duration_seconds",
        "longform_duration_seconds",
    ],
)
def test_reversed_windows_are_rejected(field: str) -> None:
    """
    区间写反必须被挡下来。

    `(100, 80)` 是合法的 `tuple[int, int]`，然后让 `low <= n <= high` **永远为假**——
    每一篇产物都被标成超长，而配置文件看上去毫无问题。设置面板把这六项都放开了，
    写反只需要一次手滑，所以判定放在模型上，命令行和界面同时受益。
    """
    with pytest.raises(ValidationError, match="下限大于上限"):
        Profile(**{field: (100, 80)})


def test_equal_bounds_are_allowed() -> None:
    """上下限相等是合法的：`(100, 100)` 表示「就要 100 字」，不是写反。"""
    assert Profile(summary_chars=(100, 100)).summary_chars == (100, 100)


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


def test_dead_sources_stay_out_of_the_shipped_config() -> None:
    """
    已实测失效/反爬的源不得再出现在 config/sources.yaml 里。

    它们原先以 `enabled: false` 留在配置里「以免重复踩坑」，实际效果相反：
    在「从订阅导入」的源列表里照样占位，每次都要重新确认一遍「是不是忘了开」。
    记录挪进 docs/issues/003-p2-live-verification-findings.md，配置里只留指针。
    """
    text = (PROJECT_ROOT / "config" / "sources.yaml").read_text(encoding="utf-8")

    for dead in ("jiqizhixin", "36kr", "techcrunch-ai", "zhihu-hot"):
        assert dead not in text, f"{dead} 已实测失效，不要加回来（见 issues/003）"

    # 反过来：实测可用只是暂时关掉的那批必须留着，别连它们一起删了
    assert "sspai" in text
    assert "arxiv-cs-cl" in text


def test_ledger_follows_the_data_directory(tmp_path: Path) -> None:
    """
    台账必须跟着 DATA_DIR 走。

    按仓库根解析的话，改了 DATA_DIR 会让文章落盘搬家而台账留在原地，两者静默失联——
    台账里记着 store_dir，指向的却是空目录。这个 bug 是写 P3 取数层的测试时
    才暴露的：测试用 tmp_path 建库，实际却在写真实的项目数据库。
    """
    s = Settings(_env_file=None, data_dir=tmp_path / "mydata", db_path=Path("./data/dna.db"))

    assert s.db_file.parent == s.data_path
    assert s.db_file == tmp_path / "mydata" / "dna.db"


def test_absolute_db_path_is_used_as_is(tmp_path: Path) -> None:
    """绝对路径原样使用——便于刻意把台账放到别处（如共享盘）。"""
    elsewhere = tmp_path / "elsewhere" / "ledger.db"
    s = Settings(_env_file=None, data_dir=tmp_path / "mydata", db_path=elsewhere)

    assert s.db_file == elsewhere


def test_legacy_db_path_prefix_is_not_duplicated(tmp_path: Path) -> None:
    """
    旧配置里 DB_PATH 写成 `./data/dna.db`，不能变成 `<data>/data/dna.db`。

    .env 里现存的就是这个写法，改动不能让已有部署找不到自己的台账。
    """
    s = Settings(_env_file=None, data_dir=tmp_path / "data", db_path=Path("./data/dna.db"))

    assert s.db_file == tmp_path / "data" / "dna.db"


def test_relative_db_path_only_contributes_its_file_name(tmp_path: Path) -> None:
    """相对路径只贡献文件名——换位置请用绝对路径，语义更清楚。"""
    s = Settings(_env_file=None, data_dir=tmp_path / "d", db_path=Path("./whatever/ledger.db"))

    assert s.db_file == tmp_path / "d" / "ledger.db"


# --- 仓库根的定位 / locating the project root ----------------------------------


def test_dna_home_overrides_the_project_root(tmp_path: Path, monkeypatch) -> None:
    """
    `DNA_HOME` 显式指定仓库根。

    它不是为打包加的，是为**测试**加的：没有它，验证「打包成 exe 之后路径指哪」
    要真去打一个包。顺带也支持「程序放 C 盘、数据放 D 盘」。
    """
    from dna.core import config as config_module

    monkeypatch.setenv("DNA_HOME", str(tmp_path))

    assert config_module._project_root() == tmp_path.resolve()


def test_frozen_root_is_the_executable_directory(monkeypatch) -> None:
    """
    打包之后用 exe 自己所在的目录，**不能用 `__file__`**。

    PyInstaller 下 `__file__` 在临时解包目录里，`config/`、`.env`、`data/`、
    `outputs/` 会全部指到那儿——程序每次启动都是一个空数据库，而且不报错。
    Under PyInstaller `__file__` lives in a temporary extraction directory, so every
    launch would silently start from an empty database.
    """
    from dna.core import config as config_module

    monkeypatch.delenv("DNA_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"D:\DNA\dna_gui.exe", raising=False)

    assert config_module._project_root() == Path(r"D:\DNA")


def test_source_root_is_the_repository(monkeypatch) -> None:
    """源码运行时上溯三层就是仓库根——config/ 与 src/ 应当都在那儿。"""
    from dna.core import config as config_module

    monkeypatch.delenv("DNA_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    root = config_module._project_root()

    assert (root / "config").is_dir()
    assert (root / "src" / "dna").is_dir()
