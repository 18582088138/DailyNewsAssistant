"""
Qwen3-TTS · PyTorch (CUDA) 后端 / The PyTorch CUDA backend.

给带 NVIDIA 卡的部署环境用；OpenVINO 那个后端给 Intel CPU / 核显 / NPU 用。
两者**对上层是同一个协议**，换环境只是换 `.env` 里的 `TTS_PROVIDER`。
For deployments with an NVIDIA card. Both backends satisfy the same protocol, so moving
between environments is a one-line configuration change.

用的是 Qwen3-TTS 官方的 `Qwen3TTSModel`，不是 OpenVINO 那份参考实现——
它的 `generate_custom_voice` 签名和返回值与 OV 版完全一致，
所以分段、拼接、进度、容错全都复用 `Qwen3Backend`，这里只负责把模型加载起来。
It uses the upstream `Qwen3TTSModel`, whose call signature matches the OpenVINO wrapper,
so everything except loading is inherited.

────────────────────────────────────────────────────────────────────────────
本机跑不了这个后端，这是**预期**的 / This backend does not run on this machine
────────────────────────────────────────────────────────────────────────────
2026-09-04 实测：本机 `torch 2.8.0+cpu`（CPU-only 构建，`torch.version.cuda` 为
None），且没有 NVIDIA 卡；本地也没有原始 PyTorch 权重（只有转换好的 OV IR）。
因此本阶段交付的是**代码 + 离线单测**，真机联调在有卡的机器上做——
和飞书机器人（P9）同一个处理方式：环境不具备就不假装验证过。

要在有卡的机器上跑，需要 / To run it where a card exists:
    1. 装 CUDA 版 torch：pip install torch --index-url https://download.pytorch.org/whl/cu124
    2. 下原始权重：huggingface-cli download Qwen/Qwen3-TTS-CustomVoice-0.6B
    3. .env：TTS_PROVIDER=qwen3_torch
              QWEN3_TTS_TORCH_MODEL_DIR=<权重目录>
              TTS_DEVICE=cuda:0
              TTS_DTYPE=bfloat16
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dna.core.logging import get_logger
from dna.tts.base import TTSError, TTSInfo
from dna.tts.qwen3_base import Qwen3Backend

logger = get_logger("tts.qwen3_torch")

PROVIDER_NAME = "qwen3_torch"

# 精度 / dtypes
#
# bfloat16 是默认：0.6B 的模型在 fp32 下显存与速度都没必要，而 bf16 在
# Ampere 及以后的卡上是原生支持的。老卡（Turing 及更早）不支持 bf16，要改 float16。
# bfloat16 by default: fp32 buys nothing for a 0.6B model, and bf16 is native from
# Ampere onwards. Older cards need float16.
DEFAULT_DTYPE = "bfloat16"
_DTYPES = ("bfloat16", "float16", "float32")


class Qwen3TorchTTS(Qwen3Backend):
    """Qwen3-TTS on PyTorch（CUDA / CPU / MPS）."""

    def __init__(
        self,
        model_dir: Path | str,
        *,
        device: str = "cuda:0",
        dtype: str = DEFAULT_DTYPE,
        repo_dir: Path | str | None = None,
    ) -> None:
        super().__init__(model_dir, device=device.lower(), repo_dir=repo_dir)
        self.dtype = (dtype or DEFAULT_DTYPE).lower()

    @property
    def info(self) -> TTSInfo:
        return TTSInfo(
            name=PROVIDER_NAME,
            model=f"{self.model_dir.name}({self.dtype})",
            device=self.device,
        )

    def _load_model(self) -> Any:
        self._check_device()
        self._ensure_qwen_tts_importable()

        import torch
        from qwen_tts.inference.qwen3_tts_model import Qwen3TTSModel

        dtype = getattr(torch, self.dtype)
        logger.info("加载 PyTorch 权重：%s @ %s / %s", self.model_dir.name, self.device, self.dtype)
        return Qwen3TTSModel.from_pretrained(
            str(self.model_dir),
            device_map=self.device,
            dtype=dtype,
        )

    # ------------------------------------------------------------------
    # 内部实现 / internals
    # ------------------------------------------------------------------

    def _check_device(self) -> None:
        """
        设备与精度都在加载**之前**校验 / Device and dtype are checked before loading.

        `torch 2.x+cpu` 这种 CPU-only 构建请求 cuda 时，报错信息是
        「Torch not compiled with CUDA enabled」——看得懂，但要等模型都读进内存了才抛。
        先查一遍，几毫秒就能给出一句能直接照做的提示。
        A CPU-only build raises only after the weights are in memory; checking first turns
        a long wait into an actionable message in milliseconds.
        """
        if self.dtype not in _DTYPES:
            raise TTSError(f"TTS_DTYPE 只能是 {'、'.join(_DTYPES)}，收到 {self.dtype}")

        try:
            import torch
        except ImportError as exc:
            raise TTSError(f"没装 PyTorch：{exc}　pip install 'dna[tts]'") from exc

        if self.device.startswith("cuda"):
            if not torch.cuda.is_available():
                built = torch.version.cuda or "无（CPU-only 构建）"
                raise TTSError(
                    f"请求 {self.device} 但 CUDA 不可用。"
                    f"torch={torch.__version__}，编译时的 CUDA={built}。"
                    "装 CUDA 版 torch，或把 TTS_PROVIDER 改回 qwen3_ov 用 OpenVINO 后端。"
                )
            index = int(self.device.split(":")[1]) if ":" in self.device else 0
            count = torch.cuda.device_count()
            if index >= count:
                raise TTSError(f"请求 {self.device}，但本机只有 {count} 张卡（编号 0~{count - 1}）")

        elif self.device == "mps" and not torch.backends.mps.is_available():
            raise TTSError("请求 mps 但本机不支持（只有 Apple Silicon 上才有）")

        elif self.device not in ("cpu", "mps") and not self.device.startswith("cuda"):
            raise TTSError(f"未知的 torch 设备：{self.device}　可用：cuda[:N] / cpu / mps")


__all__ = ["DEFAULT_DTYPE", "PROVIDER_NAME", "Qwen3TorchTTS"]
