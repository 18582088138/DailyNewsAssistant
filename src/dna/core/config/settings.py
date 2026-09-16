"""
`.env` 层 / The .env layer.

密钥与运行时开关，由 pydantic-settings 读取，**不进 git**。
Secrets and runtime switches; never committed.

这一层与 YAML 层（`profile.py`）刻意分开：一个是「这台机器怎么跑」，
一个是「内容怎么选、写多长」。前者换机器就改，后者用户天天调。
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from dna.core.logging import get_logger
from dna.core.models import Language

_logger = get_logger("core.config")

def _project_root() -> Path:
    """
    找到 `.env`、`config/`、`data/`、`outputs/` 所在的那个目录 / Locate the app root.

    三种情况 / Three cases:

    1. `DNA_HOME` —— 显式指定，优先级最高。给测试用（不打包也能验证下面那条分支），
       也给「程序放 C 盘、数据放 D 盘」这种部署方式用。
    2. **打包成 exe 之后**（`sys.frozen`）—— 用 exe 自己所在的目录。
       不能再靠 `__file__`：PyInstaller 把源码解到一个临时目录里，`__file__` 指向那里，
       于是配置、数据库、产出全写进临时目录，程序一退出就没了，且没有任何报错。
       这是打包最容易踩、最难查的一个坑。
    3. 源码运行 —— **向上找 `pyproject.toml`**，那个目录就是仓库根。

    Under PyInstaller `__file__` points into a temporary extraction directory, so config,
    database and outputs would all be written somewhere that vanishes on exit, silently.

    第 3 条为什么不数层级：这里原先写的是 `parents[3]`（当时本文件在
    `src/dna/core/config.py`）。把 config 拆成包之后文件深了一层，`parents[3]`
    就指到了 `src/` —— 于是 `config/`、`data/`、`outputs/` 全都找错地方，
    而代码本身一个字都没动。**层级计数会被目录结构的任何改动悄悄改坏**，
    找标记文件不会。
    Counting parents broke silently when this file moved one level deeper; looking for
    the marker file cannot.
    """
    override = os.environ.get("DNA_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    # 找不到标记（不该发生）时退回旧行为，但别静默：路径错了一切都错
    _logger.warning("找不到 pyproject.toml，仓库根按目录层级猜：%s", here)
    return here.parents[4]


PROJECT_ROOT = _project_root()
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

    # ---- TTS（全部走独立服务 / entirely delegated to a service）----
    #
    # 语音合成不在本项目内实现：模型加载、8GB 卡上的轮动换权重、失控重试、
    # 音色克隆/设计全在 **Agent_TTS_Module** 那一侧。这里只有一个地址。
    # 换机器（本机 → 4060 那台）改的就是 TTS_SERVICE_URL 这一行。
    # Synthesis lives in Agent_TTS_Module; this project holds only its address.
    tts_service_url: str = "http://127.0.0.1:8300"

    # Agent_TTS_Module 的仓库根目录 / where Agent_TTS_Module lives.
    # 只为**自动拉起**服务而配：服务没在线时从这里 `python -m agentic_tts.cli serve`。
    # 留空 = 不自动拉起，只报「服务不在线」。远程地址同理（拉不起别人机器上的进程）。
    tts_module_dir: str = ""
    # 拉起服务用哪个解释器；留空 = 当前这个（同一个 conda 环境）
    tts_python: str = ""
    tts_autostart: bool = True
    # 拉起尝试次数与单次等待上限。三次都失败就上报 TTS 不可用——
    # 权重加载在冷启动时要几十秒，超时给足，但不能无限等。
    tts_start_attempts: int = 3
    tts_start_timeout: float = 120.0
    # 单段合成的 HTTP 超时。CPU 上 RTF≈13，一段 120 字要三四分钟，
    # 默认的 20 秒会在**服务正常工作时**把请求掐掉，这是最难查的一类失败。
    tts_request_timeout: float = 900.0

    # 合成耗时 ÷ 音频时长。**只是数量级，不是承诺。**
    # 代码里曾写死 2.5（0.6B OpenVINO 核显上量的），换 1.7B 后 CPU 实测约 13 ——
    # 于是界面上「大约等多久」少报五倍，人会以为卡死了（见 issues/009）。
    # 换机器就该改这一行，而不是改代码。
    tts_rtf_estimate: float = 13.0

    # 送进 TTS 之前先用一次 LLM 做朗读友好化（见 tts/preprocess.py）。
    # 这是音频产物唯一的 LLM 调用，很小；不想花这一次就设成 false。
    tts_preprocess: bool = True

    # 逐段音频与字幕放在文章目录的哪个子目录
    #
    # **一篇文章一个文件夹，重做就覆盖**，不按次数/时间戳再分一层：
    # 分层的话，「这篇的音频到底是哪一份」要靠人比时间戳，而人只会打开最上面那个。
    # One folder per article, overwritten on redo: timestamped sub-folders leave the
    # question "which one is current" to whoever is reading the directory.
    tts_artifact_dirname: str = "tts"

    # 默认合成路径是否顺带导出字幕（一段音频一条字幕，SRT）
    # 剪映/Premiere/DaVinci 都能直接导入 SRT，所以不做 txt 退化。
    tts_subtitles: bool = True

    # 默认怎么发声 / how the default voice is produced:
    #   voice_clone  —— 克隆 TTS_REF_AUDIO 里那把嗓子（**默认**）
    #   custom_voice —— 用服务端内置音色（TTS_VOICE_HOST / _GUEST）
    #
    # 默认走克隆，是因为内置音色**跟着权重变**：同一个名字（Serena）在
    # 0.6B 与 1.7B 上并不是同一把嗓子，换服务端配置就会换声音。
    # 克隆锁的是一个音频文件，只要文件不换，声音就不换。
    # Cloning is the default because built-in speakers change with the checkpoint: the
    # same name is not the same voice across models, whereas a reference file is fixed.
    tts_mode: str = "voice_clone"

    # 参考音频。相对路径按**数据目录**解析（`data/ref_audio/...`）——
    # 它是素材，和台账、落盘文章一样属于 data/，不该散在仓库根目录。
    # Relative paths resolve against the data directory: this is material, like the
    # ledger and the stored articles.
    tts_ref_audio: str = "ref_audio/qwen3-tts-cpu.wav"

    # 参考音频里说的原话，一字不差。
    # **留空 = 走纯 x-vector 克隆**（只取音色，不需要原话）。
    # 填了就走 ICL 克隆：上游硬要求它与音频完全对应，写错会得到更差的结果，
    # 所以默认留空 —— 不知道原话时，x-vector 是唯一诚实的选择。
    # Empty means x-vector-only cloning; ICL requires an exact transcript, and a wrong
    # one degrades the result, so silence is the honest default.
    tts_ref_text: str = ""

    # 嘉宾（访谈稿的第二个人）的参考音频。
    # **留空 = 嘉宾用内置音色**，因为两个角色必须听得出区别：
    # 都克隆同一个文件的话，访谈稿两个人一把嗓子，双角色就白做了。
    # Empty means the guest uses a built-in speaker: the two roles must be
    # distinguishable, and cloning one file for both defeats the point.
    tts_ref_audio_guest: str = ""
    tts_ref_text_guest: str = ""

    tts_voice_host: str = ""
    tts_voice_guest: str = ""

    # ---- Sources ----
    rsshub_base_url: str = "http://localhost:1200"

    # ---- Inbox（飞书机器人，部署在私人电脑）/ Feishu bot, deployed on a personal machine ----
    #
    # ⚠️ **P9 未实现**：`src/dna/` 下没有 inbox 模块。这三项只被 `dna doctor` 与
    # `dna config` 读去显示，不影响任何行为。部署方案见 08_feishu_bot_deployment.md。
    # 钉钉与 IMAP 的配置曾经也在这里（共 7 项，零消费者），已删 —— 留着只会让人
    # 以为配了就能用。
    inbox_provider: str = "feishu"
    inbox_enabled: bool = False

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_allowed_users: str = Field(
        default="",
        description="允许投递的 open_id，逗号分隔；空 = 拒收全部",
    )

    # ---- Runtime ----
    default_language: Language = Language.ZH
    output_dir: Path = Path("./outputs")
    data_dir: Path = Path("./data")
    db_path: Path = Path("./data/dna.db")
    max_items_per_source: int = 30

    # 日志级别。**留空 = 各前端用自己的默认**（CLI 用 WARNING、GUI 用 INFO）：
    # 命令行的输出是给人读的表格与进度，掺进 INFO 会把它冲烂；而 GUI 那个终端
    # 不是界面，多点上下文只有好处。所以这里不能给一个统一的默认值。
    # 填了就两边都听它 —— 这个字段以前完全不生效：两个前端各自写死了级别，
    # 界面上还提示「重启才生效」（issues/014）。
    log_level: str = ""

    # 日报条数上限只有 Profile 那一份（`config/profile.yaml`）。这里曾经也有一个
    # 同名字段，被 Profile 那份完全遮住 —— 在 `.env` 里设 DIGEST_MAX_ENTRIES
    # 一直是没有效果的，而界面上看不出来。

    # ---- Proxy ----
    # 这些值**必须靠 apply_proxy_env() 才会生效**：httpx 走 trust_env，读的是
    # os.environ，而 pydantic-settings 只把 .env 读进 Settings 对象。
    # 少了那一步，`.env` 里改代理等于没改 —— 真正生效的只有系统环境变量。
    # FTP_PROXY 曾经也在这里，零消费者，已删。
    http_proxy: str = ""
    https_proxy: str = ""
    no_proxy: str = "localhost,127.0.0.1,0.0.0.0,::1,127.0.0.0/8,10.0.0.0/8,192.168.0.0/16"

    # -- 派生属性 / derived helpers ------------------------------------------

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


def field_default(model: type[BaseModel], name: str) -> Any:
    """
    取某个模型字段的默认值 / The declared default of one model field.

    给下游模块的**兜底常量**用：`summarize.py` 需要一个「不传参时用什么」的值，
    但不能自己再写一遍数字 —— 同一个参数在两个文件里各写死一份，是这个项目
    反复出过问题的那一类（用户点名的第 13 条）。指向模型，就只有一处定义。

    `Settings` 也能用：它是 `BaseSettings`，同样有 `model_fields`。
    Lets a downstream module state its fallback without restating the number.
    """
    return model.model_fields[name].get_default()


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


# 每个代理设置对应的环境变量名。大小写两份都写：httpx / requests / urllib
# 各自只认其中一种拼写，只写一种就会出现「换个库就不走代理」。
_PROXY_ENV_NAMES: dict[str, tuple[str, str]] = {
    "http_proxy": ("HTTP_PROXY", "http_proxy"),
    "https_proxy": ("HTTPS_PROXY", "https_proxy"),
    "no_proxy": ("NO_PROXY", "no_proxy"),
}


def apply_proxy_env(settings: Settings | None = None) -> list[str]:
    """
    把 `.env` 里的代理设置接进 `os.environ` / Publish the .env proxies to the environment.

    没有这一步，`.env` 里的 `HTTP_PROXY` **完全不生效**：`httpx` 走 `trust_env`，
    读的是 `os.environ`，而 `pydantic-settings` 只把 `.env` 读进 `Settings` 对象。
    于是界面上有开关、`dna doctor` 会显示、改了却毫无反应 —— 而真正在起作用的
    一直只是系统环境变量。这是本项目最难查的一类失效：没有报错，只是不生效。

    **系统环境变量优先**（`env > config`）：已经在 `os.environ` 里的一律不动。
    返回实际写入的变量名，供启动日志与测试断言。
    """
    s = settings or get_settings()
    written: list[str] = []
    for field, names in _PROXY_ENV_NAMES.items():
        value = (getattr(s, field, "") or "").strip()
        if not value:
            continue
        if any(os.environ.get(n) for n in names):
            continue  # 系统已经配了，不覆盖
        for n in names:
            os.environ[n] = value
        written.append(names[0])
    if written:
        _logger.debug("代理设置已接入环境变量：%s", ", ".join(written))
    return written
