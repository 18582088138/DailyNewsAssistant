"""
TTS 的环境自检 / The TTS layer's own health check.

这一条检查**曾经住在 `core/doctor.py` 里**，靠一行函数内 `from dna.tts.client import ...`
偷偷反向依赖上层 —— 那是全仓唯一的分层违规（铁律：`core` 不依赖任何人）。
延迟 import 让它不报错，但方向是错的：core 一旦认识 tts，「核心层能独立测试」
这句话就不成立了。

现在的做法：检查住在它所属的那一层，由**前端**（组装根）把它交给 `doctor.run_all`。
The check now lives in the layer it belongs to and is handed to `run_all` by the
front-end, which is the only place allowed to know about every layer.
"""

from __future__ import annotations

from pathlib import Path

from dna.core.config import Settings
from dna.core.doctor import CheckResult, Status
from dna.core.urls import is_local_url
from dna.tts.client import TTSServiceClient


def check_tts_service(settings: Settings) -> CheckResult:
    """
    TTS 服务在不在线 / Whether the TTS service is reachable.

    先前这里检查的是**本机的模型目录与 `qwen_tts` 包**——那些东西现在都在
    Agent_TTS_Module 那一侧，本项目一个模型文件都不需要。所以只剩一个问题：
    那个地址上有没有一个活着的 TTS 服务。
    This used to check local model directories; the models now live in the service, so
    the only remaining question is whether the address answers.

    **不在线不算阻塞（WARN）**，因为：
      · 采集、日报、文案全都不需要它；
      · 真要合成时会**自动拉起**（见 tts/supervisor.py），现在不在线是正常状态。
    所以这一条要说清「能不能自动拉起」，而不只是「在不在」——
    只报「不在线」会让人跑去手动开服务，而那一步本来是自动的。
    Not blocking: nothing but audio needs it, and it gets started on demand. The check
    therefore reports whether autostart is available, not merely whether it is up.
    """
    name = "TTS 服务"
    url = (settings.tts_service_url or "").strip()
    if not url:
        return CheckResult(name, Status.WARN, "未配置 TTS_SERVICE_URL")

    if TTSServiceClient(url).health():
        # **「在线」只等于那个进程活着。**服务端的 `/health` 只回一个 ok，从不碰引擎，
        # 而引擎要到第一次合成才加载模型——实测踩过一次：环境里 torchaudio 与 torch
        # 的 ABI 不匹配，doctor 全绿、合成每段必失败。这里不真合成（模型冷启动几分钟，
        # doctor 必须是秒级的），只把唯一有效的探针指出来。
        # "Online" means the process answers; the engine loads on first synthesis.
        return CheckResult(name, Status.OK, f"在线 @ {url}",
                           hint="只探到进程；引擎能不能出声要跑 dna tts --say")

    if not settings.tts_autostart:
        return CheckResult(name, Status.WARN, f"不在线 @ {url}，且已关闭自动拉起",
                           hint="TTS_AUTOSTART=true，或手动启动服务")
    if not is_local_url(url):
        return CheckResult(name, Status.WARN, f"不在线 @ {url}（远程地址，无法代为启动）",
                           hint="到那台机器上跑 python -m agentic_tts.cli serve")

    directory = (settings.tts_module_dir or "").strip()
    if not directory:
        return CheckResult(name, Status.WARN, f"不在线 @ {url}，且未配置 TTS_MODULE_DIR",
                           hint="配上 Agent_TTS_Module 的目录，合成时会自动拉起")
    if not (Path(directory) / "agentic_tts").is_dir():
        return CheckResult(name, Status.WARN,
                           f"TTS_MODULE_DIR 下没有 agentic_tts/：{directory}",
                           hint="指向 Agent_TTS_Module 的仓库根目录")

    return CheckResult(name, Status.OK, f"未运行，合成时自动拉起（{directory}）")


__all__ = ["check_tts_service"]
