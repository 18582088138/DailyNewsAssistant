"""`.env` 白名单字段与设置面板的读写 / The .env allowlist and settings I/O。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dna.core.config import get_settings
from dna.core.logging import get_logger
from dna.llm.factory import get_llm

logger = get_logger("gui.actions")

# ---------------------------------------------------------------------------
# 设置面板 / the settings panel
# ---------------------------------------------------------------------------


@dataclass
class EnvField:
    """
    设置面板里的一个 `.env` 项 / One `.env` entry in the settings panel.

    界面画它、`config_edit` 写它，两边都不需要知道另一边。
    """

    key: str
    group: str
    label: str
    help: str = ""
    options: tuple[str, ...] = ()
    """非空时画下拉（仍可自填）/ non-empty renders a dropdown that still accepts free text."""

    boolean: bool = False
    secret: bool = False

    page: str = "运行设置"
    """哪个子页 / which settings tab；`group` 仍是页内的小标题。"""

    needs_mode: str = ""
    """
    只在 `TTS_MODE` 等于这个值时才生效 / only meaningful in this `TTS_MODE`.

    服务端把「同时给 ref_audio 和音色名」当成互相冲突的参数**直接报错**，
    所以界面按当前模式把无关的那几项灰掉，而不是让人配好了才发现两者不能共存。
    """

    @property
    def field(self) -> str:
        """对应的 `Settings` 字段名 / the matching `Settings` attribute."""
        return self.key.lower()


# 分组与文案。**顺序就是界面上的顺序**，最常改的放最前面。
# 这张表只描述「怎么画」，能不能写由 `ENV_ALLOWLIST` 说了算——
# 少写一项这里的测试会报，多写一项 `save_env` 会拒。
# This table says how to draw; whether a key may be written is `ENV_ALLOWLIST`'s call.
ENV_FIELDS: tuple[EnvField, ...] = (
    EnvField("LLM_PROVIDER", "LLM", "主用提供方", options=("deepseek", "openrouter")),
    EnvField(
        "DEEPSEEK_MODEL", "LLM", "DeepSeek 模型",
        # 上一轮排查「GUI 卡住」的根因就是这一行被换成了推理模型，而界面上完全看不出来。
        # 推理模型每次要先想几百到几千个 token 才开始输出，慢 30~50 倍——
        # 表现和死循环一模一样。
        # A past "the GUI is frozen" investigation ended here: a reasoning model had been
        # selected, which is 30-50x slower and indistinguishable from a hang.
        help="deepseek-chat 是会话模型（日常用这个）；带 reasoner/thinking 的是推理模型，"
             "慢 30~50 倍，界面会像卡死",
        options=("deepseek-chat", "deepseek-reasoner"),
    ),
    EnvField("DEEPSEEK_BASE_URL", "LLM", "DeepSeek 接口地址"),
    EnvField("LLM_FALLBACK_PROVIDER", "LLM", "备用提供方", options=("", "openrouter", "deepseek")),
    EnvField("OPENROUTER_MODEL", "LLM", "OpenRouter 模型"),
    EnvField(
        "LLM_CACHE_ENABLED", "LLM", "启用响应缓存", boolean=True,
        help="关掉之后每次生成都真花钱；重做同一篇也不再免费",
    ),
    EnvField(
        "DEEPSEEK_API_KEY", "密钥", "DeepSeek API Key", secret=True,
        help="留空表示不修改。只显示前 7 位与长度——界面会被截图",
    ),
    EnvField(
        "OPENROUTER_API_KEY", "密钥", "OpenRouter API Key", secret=True,
        help="留空表示不修改",
    ),
    # --- TTS 子页 / the TTS tab ---
    EnvField("TTS_SERVICE_URL", "服务", "TTS 服务地址", page="TTS"),
    EnvField(
        "TTS_MODULE_DIR", "服务", "TTS 模块目录", page="TTS",
        help="留空表示用内置默认位置；自动拉起服务时从这里启动",
    ),
    EnvField(
        "TTS_PYTHON", "服务", "TTS 用的 Python", page="TTS",
        help="留空表示用当前解释器。TTS 常装在自己的环境里，两边依赖不一样",
    ),
    EnvField(
        "TTS_AUTOSTART", "服务", "服务没起来时自动拉起", boolean=True, page="TTS",
        help="关掉之后合成会直接失败并提示，而不是等模型冷启动",
    ),
    EnvField(
        "TTS_START_TIMEOUT", "服务", "启动等待上限（秒）", page="TTS",
        help="模型冷启动要一两分钟；调太小会在快好的时候放弃",
    ),
    EnvField(
        "TTS_START_ATTEMPTS", "服务", "启动重试次数", page="TTS",
        help="代码一直在读它，但此前漏在白名单外：给了「等多久」却没给「试几次」",
    ),
    EnvField(
        "TTS_REQUEST_TIMEOUT", "服务", "单次请求上限（秒）", page="TTS",
        help="本地合成 RTF≈2.5，长文案一段就要几分钟——这个值宁大勿小",
    ),
    EnvField(
        "TTS_MODE", "音色", "合成方式", options=("voice_clone", "custom_voice"), page="TTS",
        help="voice_clone 用参考音频克隆；custom_voice 用服务端的音色名。**两者不能同时给**",
    ),
    EnvField(
        "TTS_VOICE_HOST", "音色", "主播音色名", page="TTS", needs_mode="custom_voice",
        help="服务在线时下拉里就是服务端的音色表；手打一个不存在的名字，"
             "要等几分钟的合成跑完才会报错",
    ),
    EnvField(
        "TTS_VOICE_GUEST", "音色", "嘉宾音色名（双人稿）", page="TTS", needs_mode="custom_voice",
        help="只有长文案的访谈体用得上",
    ),
    EnvField(
        "TTS_REF_AUDIO", "参考音频", "主播参考音频", page="TTS", needs_mode="voice_clone",
        help="相对路径按**数据目录**解析（不是仓库根）",
    ),
    EnvField(
        "TTS_REF_TEXT", "参考音频", "主播参考音频的原话", page="TTS", needs_mode="voice_clone",
        help="留空则走纯 x-vector；随便编一句会让克隆质量明显变差",
    ),
    EnvField(
        "TTS_REF_AUDIO_GUEST", "参考音频", "嘉宾参考音频", page="TTS", needs_mode="voice_clone",
    ),
    EnvField(
        "TTS_REF_TEXT_GUEST", "参考音频", "嘉宾参考音频的原话", page="TTS",
        needs_mode="voice_clone",
    ),
    EnvField("TTS_PREPROCESS", "产物", "合成前做文本预处理", boolean=True, page="TTS"),
    EnvField(
        "TTS_SUBTITLES", "产物", "同时导出字幕", boolean=True, page="TTS",
        help="按段落时间轴出 srt，剪辑时省一遍对轴",
    ),
    EnvField(
        "TTS_ARTIFACT_DIRNAME", "产物", "音频落盘子目录名", page="TTS",
        help="在文章目录下，默认 tts",
    ),
    EnvField("HTTP_PROXY", "网络", "HTTP 代理"),
    EnvField("HTTPS_PROXY", "网络", "HTTPS 代理"),
    EnvField(
        "NO_PROXY", "网络", "不走代理的地址",
        # 少了 localhost，本机的 TTS 服务与 RSSHub 会被路由到公司代理然后失败。
        help="**必须包含 localhost 与 127.0.0.1**，否则本机的 TTS 服务和 RSSHub 会被送进代理",
    ),
    EnvField("DEFAULT_LANGUAGE", "运行", "默认语言", options=("zh", "en")),
    EnvField("MAX_ITEMS_PER_SOURCE", "运行", "每个源最多取几条"),
    EnvField("LOG_LEVEL", "运行", "日志级别", options=("DEBUG", "INFO", "WARNING", "ERROR")),
)


def env_groups(page: str = "运行设置") -> list[tuple[str, list[EnvField]]]:
    """
    某个子页的字段，按小组归拢 / One tab's fields, grouped, in declaration order.

    分页之后**每个字段有且只有一个归属**：同一项画在两页上，人会在另一页看到
    自己刚改过的旧值，然后不知道哪一份才算数。
    """
    groups: dict[str, list[EnvField]] = {}
    for field in ENV_FIELDS:
        if field.page == page:
            groups.setdefault(field.group, []).append(field)
    return list(groups.items())


def env_display(field: EnvField) -> str:
    """
    这一项现在显示什么 / What this field shows right now.

    密钥走 `mask_secret`，其余读 `Settings` 的**生效值**而不是 `.env` 的文本——
    被系统环境变量盖住时，文件里写的那个值根本不是程序在用的那个。
    Secrets are masked; everything else shows the *effective* value from `Settings` rather
    than the file's text, because an OS environment variable may be overriding it.
    """
    from dna.core.config_edit import mask_secret

    value = getattr(get_settings(), field.field, "")
    if value is None:
        return ""
    text = str(getattr(value, "value", value))  # Language 这类枚举取 .value
    return mask_secret(text) if field.secret else text


def env_shadowed(field: EnvField) -> bool:
    """这一项是否被系统环境变量盖住 / Whether an OS variable overrides it."""
    from dna.core.config_edit import shadowed_by_env

    return shadowed_by_env(field.key)


def tts_voice_options() -> list[str]:
    """服务端的音色表 / The service's voice list（离线返回空表）。"""
    from dna.tts.console import available_voices

    return available_voices()


def ref_audio_options() -> list[str]:
    """可选的参考音频 / The reference-audio candidates，相对路径。"""
    from dna.tts.console import ref_audio_choices

    return ref_audio_choices()


def ref_audio_file(raw: str) -> Path | None:
    """某个参考音频设置解析到的文件 / The file a reference-audio setting resolves to。"""
    from dna.tts.console import resolve_ref_audio

    return resolve_ref_audio(raw)


def profile_values() -> dict:
    """当前的内容偏好 / The current content preferences, as a plain dict."""
    from dna.core.config import safe_profile

    return safe_profile().model_dump(mode="json")


def save_settings(
    profile_updates: dict | None = None,
    env_updates: dict[str, str] | None = None,
) -> str:
    """
    保存设置 / Persist the settings.

    抛出 / Raises:
        ConfigError: 校验失败或配置项不存在。**上层直接把消息显示出来**——
                     pydantic 的报错已经指明是哪个字段哪里不对，改写一遍只会变模糊。

    返回一句人话，写明改了几项、哪些**需要重启**才生效。
    Returns a sentence naming what changed and what needs a restart.
    """
    from dna.core.config_edit import save_env, save_profile

    parts: list[str] = []
    if profile_updates:
        save_profile(profile_updates)
        parts.append(f"内容偏好 {len(profile_updates)} 项")

    written: list[str] = []
    if env_updates:
        written = save_env(env_updates)
        if written:
            parts.append(f"运行设置 {len(written)} 项")

    if not parts:
        return "没有改动"

    message = "已保存：" + "、".join(parts)
    # 代理与日志级别在进程启动时就被读走了，改完这次会话不会变——
    # 不说清楚的话人会以为没保存成功，然后反复点保存。
    # Proxies and the log level are read at process start; without saying so the user
    # assumes the save failed and clicks again.
    restart = [k for k in written if k in {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "LOG_LEVEL"}]
    if restart:
        message += f"　·　{'、'.join(restart)} 要重启 dna gui 才生效"
    if profile_updates:
        message += "　·　字数窗口即时生效，表格里的超长标记会跟着重判"
    return message


def cache_status() -> str:
    """
    LLM 缓存命中情况 / The LLM cache hit rate.

    常驻在顶部：**费用要一直看得见**。看不见的成本最容易失控——
    界面上按钮很多，不显示的话没人知道这一小时点掉了多少钱。
    Pinned to the top because cost must stay visible. Invisible cost is the kind that
    runs away: there are many buttons here, and without this nobody would know what an
    hour of clicking added up to.
    """
    llm = get_llm()
    return llm.stats() if hasattr(llm, "stats") else "LLM 缓存：未启用"
