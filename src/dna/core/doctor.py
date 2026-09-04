"""
环境自检 / Environment self-check.

`dna doctor` 的实现。每一项检查都是独立的纯函数，返回 CheckResult 而不打印，
这样既能被 CLI 渲染成表格，也能被单元测试直接断言。
Implementation behind `dna doctor`. Every check is an independent function that
returns a CheckResult instead of printing, so the CLI can render it as a table
and the unit tests can assert on it directly.

失败分级 / Severity levels:
    OK   —— 通过
    WARN —— 当前阶段用不到，或属于可选能力，不阻塞开发
    FAIL —— 会导致功能不可用，必须处理
    SKIP —— 本机不适用（如飞书投递部署在私人电脑上）
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dna.core.config import DEFAULT_ENV_FILE, Settings, get_settings

MIN_PYTHON = (3, 12)

# 各阶段依赖 / dependencies grouped by development phase
PHASE_PACKAGES: dict[str, tuple[str, ...]] = {
    "P0 core": ("pydantic", "pydantic_settings", "dotenv", "yaml", "typer", "rich"),
    "P1 llm": ("openai",),
    "P2 sources": ("feedparser", "trafilatura", "bs4", "httpx"),
    "P3 pipeline": ("sklearn", "sentence_transformers", "numpy"),
    "P5 render": ("jinja2", "playwright", "PIL", "markdown"),
    "P6 tts": ("openvino", "transformers"),
    "P8 gui": ("nicegui",),
    "P9 inbox": ("lark_oapi",),
}

# 云端 provider 需要 API key，本地 provider 不需要
# Cloud providers need an API key; local ones do not.
LOCAL_PROVIDERS = {"ollama", "vllm", "openvino"}


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class CheckResult:
    """一项检查的结果 / The outcome of a single check."""

    name: str
    status: Status
    detail: str
    hint: str = ""

    @property
    def failed(self) -> bool:
        return self.status is Status.FAIL


# ---------------------------------------------------------------------------
# 单项检查 / Individual checks
# ---------------------------------------------------------------------------


def check_python() -> CheckResult:
    """Python 版本是否满足要求 / Whether the interpreter is new enough."""
    v = sys.version_info
    current = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= MIN_PYTHON:
        return CheckResult("Python 版本", Status.OK, f"{current} @ {sys.executable}")
    return CheckResult(
        "Python 版本",
        Status.FAIL,
        f"{current}，需要 >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
        hint="conda activate ov_env_py312",
    )


def check_packages() -> list[CheckResult]:
    """
    按阶段检查依赖是否可导入 / Check importability of dependencies, grouped by phase.

    P0 缺失记 FAIL，后续阶段缺失只记 WARN——依赖是按阶段增量安装的。
    Missing P0 packages fail; later phases only warn, since deps are installed
    incrementally as each phase begins.
    """
    results: list[CheckResult] = []
    for phase, mods in PHASE_PACKAGES.items():
        missing = [m for m in mods if importlib.util.find_spec(m) is None]
        if not missing:
            results.append(CheckResult(f"依赖 {phase}", Status.OK, f"{len(mods)} 个包齐备"))
        elif phase.startswith("P0"):
            results.append(
                CheckResult(
                    f"依赖 {phase}",
                    Status.FAIL,
                    f"缺失：{', '.join(missing)}",
                    hint="pip install -e .",
                )
            )
        else:
            results.append(
                CheckResult(
                    f"依赖 {phase}",
                    Status.WARN,
                    f"待安装：{', '.join(missing)}",
                    hint=f"到该阶段再装：pip install -e .[{phase.split()[1]}]",
                )
            )
    return results


def check_env_file(env_file: Path = DEFAULT_ENV_FILE) -> CheckResult:
    """.env 是否存在 / Whether the .env file exists."""
    if env_file.exists():
        return CheckResult(".env 配置", Status.OK, str(env_file))
    return CheckResult(
        ".env 配置",
        Status.FAIL,
        f"未找到 {env_file}",
        hint="cp .env.example .env 后填写密钥",
    )


def check_llm_config(settings: Settings) -> CheckResult:
    """
    当前 LLM provider 的配置是否完整 / Whether the active LLM provider is fully configured.
    """
    provider = settings.llm_provider.lower()
    if provider in LOCAL_PROVIDERS:
        return CheckResult("LLM 配置", Status.OK, f"provider={provider}（本地，无需 API key）")

    key = settings.api_key_for(provider)
    if key:
        return CheckResult("LLM 配置", Status.OK, f"provider={provider}，API key 已配置（{key[:7]}…）")
    return CheckResult(
        "LLM 配置",
        Status.FAIL,
        f"provider={provider}，但未配置 API key",
        hint=f"在 .env 里填写 {provider.upper()}_API_KEY",
    )


def check_playwright() -> CheckResult:
    """
    Playwright 的 Chromium 内核是否已下载 / Whether Playwright's Chromium is installed.

    长图渲染依赖它；只装 pip 包是不够的，必须再跑一次 `playwright install chromium`。
    Long-image rendering depends on it. Installing the pip package is not enough;
    the browser binary must be downloaded separately.
    """
    if importlib.util.find_spec("playwright") is None:
        return CheckResult("Playwright 内核", Status.WARN, "playwright 包未安装（P5 阶段再装）")

    # 浏览器默认下载到 %LOCALAPPDATA%/ms-playwright（Windows）
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    candidates = [Path(root)] if root else []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "ms-playwright")
    candidates.append(Path.home() / ".cache" / "ms-playwright")

    for base in candidates:
        if base.exists() and any(base.glob("chromium-*")):
            return CheckResult("Playwright 内核", Status.OK, f"chromium 已就绪：{base}")

    return CheckResult(
        "Playwright 内核",
        Status.WARN,
        "未找到 chromium（P5 长图渲染需要）",
        hint="playwright install chromium",
    )


def check_tts_model(settings: Settings) -> CheckResult:
    """本地 Qwen3-TTS OpenVINO 模型是否就位 / Whether the local Qwen3-TTS IR model is present."""
    raw = settings.qwen3_tts_model_dir.strip()
    if not raw:
        return CheckResult("Qwen3-TTS 模型", Status.WARN, "未配置 QWEN3_TTS_MODEL_DIR（P6 阶段再配）")

    model_dir = Path(raw)
    if not model_dir.exists():
        return CheckResult(
            "Qwen3-TTS 模型",
            Status.WARN,
            f"目录不存在：{model_dir}",
            hint="核对 .env 里的 QWEN3_TTS_MODEL_DIR",
        )

    xml_count = len(list(model_dir.glob("*.xml")))
    if xml_count == 0:
        return CheckResult(
            "Qwen3-TTS 模型",
            Status.WARN,
            f"目录存在但没有 OpenVINO IR (*.xml)：{model_dir}",
            hint="确认已完成 OpenVINO 转换",
        )
    return CheckResult("Qwen3-TTS 模型", Status.OK, f"{xml_count} 个 IR 文件 @ {model_dir}")


def check_writable_dirs(settings: Settings) -> list[CheckResult]:
    """产物与数据目录是否可写 / Whether the output and data directories are writable."""
    results: list[CheckResult] = []
    for label, path in (("产物目录", settings.output_path), ("数据目录", settings.data_path)):
        try:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path, delete=True):
                pass
            results.append(CheckResult(label, Status.OK, str(path)))
        except OSError as exc:
            results.append(
                CheckResult(label, Status.FAIL, f"{path} 不可写：{exc}", hint="检查路径权限或改 .env 中的目录配置")
            )
    return results


def check_inbox(settings: Settings) -> CheckResult:
    """
    远程投递接口状态 / Status of the remote inbox.

    该功能部署在用户私人电脑，公司电脑上默认关闭，因此这里报 SKIP 而非 FAIL。
    This runs on the user's personal machine only, so an unconfigured inbox on the
    development machine is reported as SKIP rather than FAIL.
    """
    if not settings.inbox_enabled:
        return CheckResult(
            "远程投递（飞书）",
            Status.SKIP,
            "INBOX_ENABLED=false（部署在私人电脑，开发机不启用）",
            hint="部署步骤见 docs/08_feishu_bot_deployment.md",
        )

    if not (settings.feishu_app_id and settings.feishu_app_secret):
        return CheckResult(
            "远程投递（飞书）",
            Status.FAIL,
            "已启用但缺少 FEISHU_APP_ID / FEISHU_APP_SECRET",
            hint="见 docs/08_feishu_bot_deployment.md §2.5",
        )

    if not settings.feishu_allowed_user_list:
        return CheckResult(
            "远程投递（飞书）",
            Status.FAIL,
            "白名单为空，将拒收全部消息（安全默认）",
            hint="按 docs/08_feishu_bot_deployment.md §3.3 获取 open_id 并填入 FEISHU_ALLOWED_USERS",
        )

    return CheckResult(
        "远程投递（飞书）",
        Status.OK,
        f"已启用，白名单 {len(settings.feishu_allowed_user_list)} 人",
    )


def check_proxy(settings: Settings) -> CheckResult:
    """
    代理配置是否合理 / Whether the proxy configuration is sane.

    重点检查 NO_PROXY 是否放行 localhost——Ollama 与自建 RSSHub 都在本机端口上，
    漏配会让本地调用被送去公司代理，报错信息还很难懂。
    The key check is that NO_PROXY bypasses localhost: Ollama and the self-hosted
    RSSHub listen locally, and routing them through the corporate proxy produces
    confusing failures.
    """
    if not settings.proxy_enabled:
        return CheckResult("代理配置", Status.OK, "未配置代理（直连）")

    if not settings.localhost_bypasses_proxy():
        return CheckResult(
            "代理配置",
            Status.FAIL,
            f"已配置代理 {settings.https_proxy}，但 NO_PROXY 未放行 localhost",
            hint="在 .env 的 NO_PROXY 里加入 localhost,127.0.0.1，否则本地 Ollama / RSSHub 调用会失败",
        )

    return CheckResult(
        "代理配置",
        Status.OK,
        f"{settings.https_proxy}；NO_PROXY 已放行本地回环（{len(settings.no_proxy_list)} 项）",
    )


def check_git() -> CheckResult:
    """
    git 是否可用 / Whether git is available.

    项目约定由人工提交，这里只确认工具存在，不做任何仓库操作。
    Commits are made manually by the user; this only confirms the tool exists.
    """
    path = shutil.which("git")
    if path:
        return CheckResult("git", Status.OK, path)
    return CheckResult("git", Status.WARN, "未找到 git（提交需要）", hint="安装 Git for Windows")


# ---------------------------------------------------------------------------
# 汇总 / Aggregation
# ---------------------------------------------------------------------------


def run_all(
    settings: Settings | None = None, *, env_file: Path = DEFAULT_ENV_FILE
) -> list[CheckResult]:
    """
    执行全部检查 / Run every check and return the results in display order.

    参数 / Args:
        env_file: `.env` 的位置。**可注入是必须的**——`.env` 按约定不入库，
            而干净克隆里没有它，`check_env_file` 就会 FAIL。写死路径的话
            这个检查会读开发机上那个未跟踪的文件，于是测试「在我机器上过、
            在干净克隆里挂」，而挂的原因和被测代码毫无关系。
            Injectable by necessity: `.env` is deliberately untracked, so a clean clone
            has none and the check fails. With the path hard-coded the check reads an
            untracked file on the developer's machine, and the test passes there while
            failing on a fresh clone for a reason unrelated to the code under test.
    """
    s = settings or get_settings()
    results: list[CheckResult] = [check_python()]
    results.extend(check_packages())
    results.append(check_env_file(env_file))
    results.append(check_llm_config(s))
    results.append(check_playwright())
    results.append(check_tts_model(s))
    results.extend(check_writable_dirs(s))
    results.append(check_proxy(s))
    results.append(check_inbox(s))
    results.append(check_git())
    return results


def summarize(results: list[CheckResult]) -> dict[Status, int]:
    """按状态统计 / Count results by status."""
    counts = {st: 0 for st in Status}
    for r in results:
        counts[r.status] += 1
    return counts


def has_failure(results: list[CheckResult]) -> bool:
    """是否存在阻塞性问题 / Whether any blocking problem was found."""
    return any(r.failed for r in results)


__all__ = [
    "CheckResult",
    "Status",
    "check_env_file",
    "check_git",
    "check_inbox",
    "check_llm_config",
    "check_packages",
    "check_playwright",
    "check_proxy",
    "check_python",
    "check_tts_model",
    "check_writable_dirs",
    "has_failure",
    "run_all",
    "summarize",
]
