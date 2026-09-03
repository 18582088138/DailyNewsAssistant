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

    # LLM 响应缓存：相同请求直接读盘，不再计费。默认开启——
    # 开发期反复调提示词时，只有真正改动过的那部分需要重新付费。
    # Identical requests are served from disk instead of being billed again. On by
    # default: while iterating on prompts, only what actually changed costs money.
    llm_cache_enabled: bool = True

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
    def llm_cache_path(self) -> Path:
        """LLM 响应缓存目录（绝对路径）/ Absolute path of the LLM response cache."""
        return self.data_path / "llm_cache"

    @property
    def db_file(self) -> Path:
        """
        SQLite 台账文件（绝对路径）/ Absolute path of the SQLite ledger.

        相对路径按**数据目录**解析，不是按仓库根——台账和它索引的落盘文章必须
        待在一起。按仓库根解析的话，改了 `DATA_DIR` 会让文章搬家而台账留在原地，
        两者静默失联：台账里记着 store_dir，指向的却是空目录。
        A relative path resolves against the data directory rather than the repo root:
        the ledger and the articles it indexes must stay together. Resolving against the
        repo root would let a changed `DATA_DIR` move the articles while the ledger
        stayed behind, silently desynchronising the two — every store_dir would point at
        nothing.

        规则 / The rule:
            绝对路径 → 原样使用（可以刻意把台账放到共享盘等别处）
            相对路径 → **只取文件名**，放进数据目录

        只取文件名而不是拼接整个相对路径，是为了让 `.env` 里现存的
        `DB_PATH=./data/dna.db` 不会变成 `<data>/data/dna.db`。相对路径在这里的
        实际用途只是「给台账换个文件名」，需要换位置时用绝对路径表达更清楚。
        Only the file name is taken from a relative path so the existing
        `DB_PATH=./data/dna.db` does not become `<data>/data/dna.db`. A relative value
        here only ever serves to rename the ledger; relocating it is expressed more
        clearly with an absolute path.
        """
        if self.db_path.is_absolute():
            return self.db_path

        return self.data_path / (self.db_path.name or "dna.db")

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


class SourceFilter(BaseModel):
    """
    条目过滤规则 / Item filtering rules.

    在**抓正文之前**执行，因此过滤掉的条目完全不产生网络与存储开销。
    Applied before the body is fetched, so filtered items cost no network or storage.

    规则次序 / Rule order:
        exclude 命中 → 丢弃（优先级最高，宁可少收不要错收）
        include 非空且一条都没命中 → 丢弃
        标题过短 / 过旧 → 丢弃
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    include: list[str] = Field(
        default_factory=list,
        description="命中任一才保留；留空表示不限制 / keep only if one matches; empty means no limit",
    )
    exclude: list[str] = Field(
        default_factory=list, description="命中任一即丢弃 / drop if any matches"
    )
    min_title_length: int = Field(
        default=0, ge=0, description="标题最少字数，滤掉「快讯」这类空标题 / minimum title length"
    )
    max_age_days: int | None = Field(
        default=None,
        ge=0,
        description="只要最近 N 天的；None 表示不限 / keep only the last N days, None means no limit",
    )

    def is_empty(self) -> bool:
        """是否没有任何规则 / Whether no rule is configured at all."""
        return not (
            self.include or self.exclude or self.min_title_length or self.max_age_days is not None
        )


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
    filters: SourceFilter | None = Field(
        default=None, description="该源专属的过滤规则 / filtering rules specific to this source"
    )


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
    "SourceFilter",
    "get_settings",
    "load_profile",
    "load_sources",
    "reload_settings",
]
