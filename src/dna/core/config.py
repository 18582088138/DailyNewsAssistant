"""
配置加载 / Configuration loading.

分两层 / Two layers:
  1. `.env`  —— 密钥与运行时开关，由 Settings（pydantic-settings）读取，不进 git
                Secrets and runtime switches, read by Settings, never committed.
  2. `config/*.yaml` —— 订阅源清单与个人偏好，可热改、可进 git
                Source list and personal preferences: human-editable, committed.

密钥只从 .env 读取，代码内零硬编码。
Secrets are only ever read from .env; nothing is hard-coded.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from dna.core.errors import ConfigError
from dna.core.models import Language, SourceKind

# 仓库根目录：src/dna/core/config.py -> 上溯三层
# Repository root: this file is at src/dna/core/config.py, so go up three levels.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


# ---------------------------------------------------------------------------
# .env 层 / The .env layer
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """
    运行时配置，字段名与 .env 变量名一一对应（大小写不敏感）。
    Runtime settings; each field maps to the same-named .env variable (case-insensitive).
    """

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- LLM ----
    llm_provider: str = "deepseek"
    llm_fallback_provider: str | None = "openrouter"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = ""

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:9b"

    vllm_base_url: str = "http://localhost:8000/v1"
    vllm_model: str = ""

    openvino_llm_dir: str = ""
    openvino_llm_device: str = "GPU"

    # ---- Embeddings ----
    embedding_model: str = "BAAI/bge-m3"

    # ---- TTS ----
    tts_provider: str = "qwen3_ov"
    qwen3_tts_model_dir: str = ""
    tts_device: str = "GPU"
    tts_voice_host: str = ""
    tts_voice_guest: str = ""

    # ---- Sources ----
    rsshub_base_url: str = "http://localhost:1200"

    # ---- Inbox（飞书机器人，部署在私人电脑）/ Feishu bot, deployed on a personal machine ----
    inbox_provider: str = "feishu"
    inbox_enabled: bool = False
    inbox_send_receipt: bool = True

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_allowed_users: str = Field(
        default="",
        description="允许投递的 open_id，逗号分隔；空 = 拒收全部 / empty means reject everyone",
    )

    dingtalk_client_id: str = ""
    dingtalk_client_secret: str = ""
    dingtalk_allowed_users: str = ""

    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    inbox_allowed_senders: str = ""
    inbox_subject_prefix: str = "DNA"
    inbox_poll_interval: int = 300

    # ---- Runtime ----
    default_language: Language = Language.ZH
    output_dir: Path = Path("./outputs")
    data_dir: Path = Path("./data")
    db_path: Path = Path("./data/dna.db")
    max_items_per_source: int = 30
    digest_max_entries: int = 15
    log_level: str = "INFO"

    # ---- Proxy ----
    # NO_PROXY 必须包含 localhost，否则本地 Ollama / RSSHub 调用会被代理拦截。
    # NO_PROXY must cover localhost, otherwise local Ollama / RSSHub calls get proxied.
    http_proxy: str = ""
    https_proxy: str = ""
    ftp_proxy: str = ""
    no_proxy: str = "localhost,127.0.0.1,0.0.0.0,::1,127.0.0.0/8,10.0.0.0/8,192.168.0.0/16"

    # -- 派生属性 / derived helpers ------------------------------------------

    @property
    def project_root(self) -> Path:
        """仓库根目录 / Repository root."""
        return PROJECT_ROOT

    def resolve(self, p: Path) -> Path:
        """把相对路径按仓库根解析为绝对路径 / Resolve a relative path against the repo root."""
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    @property
    def output_path(self) -> Path:
        """产物根目录（绝对路径）/ Absolute output root."""
        return self.resolve(self.output_dir)

    @property
    def data_path(self) -> Path:
        """数据根目录（绝对路径）/ Absolute data root."""
        return self.resolve(self.data_dir)

    @property
    def db_file(self) -> Path:
        """SQLite 台账文件（绝对路径）/ Absolute path of the SQLite ledger."""
        return self.resolve(self.db_path)

    @property
    def feishu_allowed_user_list(self) -> list[str]:
        """
        飞书白名单 open_id 列表。空列表表示拒收全部——这是刻意的安全默认。
        Whitelisted Feishu open_ids. An empty list rejects everyone, on purpose.
        """
        return _split_csv(self.feishu_allowed_users)

    @property
    def inbox_allowed_sender_list(self) -> list[str]:
        """邮箱兜底 adapter 的发件人白名单 / Sender whitelist for the email fallback adapter."""
        return _split_csv(self.inbox_allowed_senders)

    @property
    def no_proxy_list(self) -> list[str]:
        """NO_PROXY 条目列表 / Entries of the NO_PROXY setting."""
        return _split_csv(self.no_proxy)

    @property
    def proxy_enabled(self) -> bool:
        """是否配置了代理 / Whether any proxy is configured."""
        return bool(self.http_proxy or self.https_proxy)

    def localhost_bypasses_proxy(self) -> bool:
        """
        本地回环地址是否已在 NO_PROXY 中 / Whether loopback addresses bypass the proxy.

        Ollama(11434) 与自建 RSSHub(1200) 都跑在 localhost 上；若 NO_PROXY 漏了
        localhost，这些本地调用会被送去公司代理并失败——这是常见且难查的坑。
        Ollama and the self-hosted RSSHub both listen on localhost. If NO_PROXY
        omits it, those local calls are sent to the corporate proxy and fail —
        a common and hard-to-diagnose problem.
        """
        if not self.proxy_enabled:
            return True
        entries = {e.lower() for e in self.no_proxy_list}
        return bool(entries & {"localhost", "127.0.0.1", "127.0.0.0/8"})

    def api_key_for(self, provider: str) -> str:
        """
        取某个云端 provider 的 API key / Return the API key configured for a cloud provider.

        本地 provider（ollama / vllm / openvino）无需 key，返回空字符串。
        Local providers need no key and return an empty string.
        """
        return {
            "deepseek": self.deepseek_api_key,
            "openrouter": self.openrouter_api_key,
        }.get(provider.lower(), "")


def _split_csv(raw: str) -> list[str]:
    """逗号分隔字符串 → 去空去重的列表 / Split a CSV string into a clean list."""
    seen: dict[str, None] = {}
    for part in raw.split(","):
        item = part.strip()
        if item:
            seen.setdefault(item, None)
    return list(seen)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    读取并缓存运行时配置 / Load and cache the runtime settings.

    进程内只解析一次 .env；测试中如需重新加载，调用 reload_settings()。
    The .env file is parsed once per process; tests can call reload_settings().
    """
    return Settings()


def reload_settings() -> Settings:
    """清缓存后重新加载配置 / Clear the cache and reload settings."""
    get_settings.cache_clear()
    return get_settings()


# ---------------------------------------------------------------------------
# YAML 层 / The YAML layer
# ---------------------------------------------------------------------------


class SourceConfig(BaseModel):
    """一个订阅源的配置 / Configuration of a single news source."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str
    name: str
    kind: SourceKind = SourceKind.RSS
    url: str
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    max_items: int | None = Field(default=None, description="覆盖全局上限 / overrides the global cap")
    lang: Language | None = Field(default=None, description="源语言，用于决定是否需要翻译 / source language")


class Profile(BaseModel):
    """
    个人偏好：决定选题倾向、篇幅与语言 / Personal preferences driving selection and length.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    focus_keywords: list[str] = Field(default_factory=list, description="加权关键词 / keywords to boost")
    exclude_keywords: list[str] = Field(default_factory=list, description="排除关键词 / keywords to drop")
    video_keywords: list[str] = Field(
        default_factory=list, description="命中则倾向标记 need_video / hints for the video flag"
    )
    languages: list[Language] = Field(default_factory=lambda: [Language.ZH])
    digest_max_entries: int = 15
    summary_max_sentences: int = 2
    video_duration_seconds: tuple[int, int] = (20, 25)


def _read_yaml(path: Path) -> Any:
    """读取 YAML，文件缺失或格式错误时抛 ConfigError / Read YAML, raising ConfigError on problems."""
    if not path.exists():
        raise ConfigError(f"配置文件不存在 / config file not found: {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - 依赖 yaml 内部错误信息
        raise ConfigError(f"配置文件解析失败 / failed to parse {path}: {exc}") from exc


def load_sources(path: Path | None = None) -> list[SourceConfig]:
    """
    加载订阅源清单 / Load the source list from config/sources.yaml.

    只返回 enabled 为真的源；id 重复会直接报错，避免后续静默覆盖。
    Only enabled sources are returned. Duplicate ids raise, to avoid silent overwrites.
    """
    path = path or (DEFAULT_CONFIG_DIR / "sources.yaml")
    data = _read_yaml(path)
    raw_list = data.get("sources", []) if isinstance(data, dict) else data
    if not isinstance(raw_list, list):
        raise ConfigError(f"sources.yaml 顶层应为 sources 列表 / expected a 'sources' list in {path}")

    sources = [SourceConfig(**item) for item in raw_list]

    seen: set[str] = set()
    for s in sources:
        if s.id in seen:
            raise ConfigError(f"订阅源 id 重复 / duplicate source id: {s.id}")
        seen.add(s.id)

    return [s for s in sources if s.enabled]


def load_profile(path: Path | None = None) -> Profile:
    """加载个人偏好 / Load personal preferences from config/profile.yaml."""
    path = path or (DEFAULT_CONFIG_DIR / "profile.yaml")
    return Profile(**_read_yaml(path))


__all__ = [
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_ENV_FILE",
    "PROJECT_ROOT",
    "Profile",
    "Settings",
    "SourceConfig",
    "get_settings",
    "load_profile",
    "load_sources",
    "reload_settings",
]
