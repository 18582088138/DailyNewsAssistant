"""
Qwen3-TTS · OpenVINO 后端 / The OpenVINO backend.

跑在 Intel CPU / 核显 / NPU 上，模型已在本机转换并验证通过。本模块**不做转换**。
Runs on Intel CPU, integrated GPU or NPU. The IR was converted and verified separately.

依赖三个外部目录 / Three external directories:
    QWEN3_TTS_MODEL_DIR   已转换的 IR 模型
    QWEN3_TTS_HELPER_DIR  参考实现 `qwen_3_tts_helper.py` 所在目录
    QWEN3_TTS_REPO_DIR    Qwen3-TTS 源码仓库（提供 `qwen_tts` 包）

为什么不把参考实现拷进仓库 / Why the reference implementation is not vendored:
    它有 12 万字符，且依赖 `qwen_tts` 包——那个包本来就在仓库之外。
    拷进来既躲不掉外部依赖，又多出一份要跟上游同步的副本。
    改成按路径引用之后，`dna doctor` 会检查它在不在，缺了会明确报出来。

────────────────────────────────────────────────────────────────────────────
上游把设备写死了，这里把它掰回来 / Upstream pins the device; this module unpins it
────────────────────────────────────────────────────────────────────────────
`qwen_3_tts_helper.OVQwen3TTSModel.__init__` 与 `from_pretrained` 里都写着
`tmp_device = "GPU"`，传进去的 `device` 参数**只对 speaker encoder 生效**。
照原样调用的话，`.env` 里的 `TTS_DEVICE` 改了没有任何反应。

这个项目已经栽过一次同样的跟头（`profile.yaml` 的时长区间改了没反应，
构建器用的是自己的默认值），所以这里不重复。

**声码器（speech tokenizer 的 decoder）保持在 CPU**，与上游一致：
本机验证过的就是这个配置，没有理由在这一步改动一个已经验证过的东西。
The vocoder stays on CPU exactly as upstream has it: that is the configuration the model
was verified with, and this is not the place to change a validated setting.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from dna.tts.base import TTSError, TTSInfo
from dna.tts.qwen3_base import Qwen3Backend

PROVIDER_NAME = "qwen3_ov"
HELPER_MODULE = "qwen_3_tts_helper"

# OpenVINO 的设备名 / OpenVINO device names
SUPPORTED_DEVICES = ("CPU", "GPU", "NPU")


class Qwen3OpenVINOTTS(Qwen3Backend):
    """本地 Qwen3-TTS（OpenVINO IR）/ The local Qwen3-TTS on OpenVINO."""

    def __init__(
        self,
        model_dir: Path | str,
        *,
        device: str = "GPU",
        helper_dir: Path | str | None = None,
        repo_dir: Path | str | None = None,
    ) -> None:
        super().__init__(model_dir, device=device.upper(), repo_dir=repo_dir)
        self.helper_dir = Path(helper_dir) if helper_dir else None

    @property
    def info(self) -> TTSInfo:
        return TTSInfo(name=PROVIDER_NAME, model=self.model_dir.name, device=self.device)

    def _load_model(self) -> Any:
        self._check_device()
        self._ensure_qwen_tts_importable()
        helper = self._import_helper()
        return _load_on_device(helper, self.model_dir, self.device)

    # ------------------------------------------------------------------
    # 内部实现 / internals
    # ------------------------------------------------------------------

    def _check_device(self) -> None:
        """
        设备不可用时**立即报错**，而不是让 OpenVINO 在半小时后才崩。
        Fails immediately rather than letting OpenVINO die half an hour in.
        """
        try:
            import openvino as ov

            available = ov.Core().available_devices
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"OpenVINO 不可用：{exc}") from exc

        if not any(d.split(".")[0] == self.device for d in available):
            raise TTSError(
                f"设备 {self.device} 不可用；本机可用：{'、'.join(available)}　"
                "改 .env 里的 TTS_DEVICE"
            )

    def _import_helper(self) -> Any:
        """导入参考实现 / Import the reference implementation."""
        if self.helper_dir is not None:
            path = str(self.helper_dir)
            if path not in sys.path:
                sys.path.insert(0, path)

        try:
            return importlib.import_module(HELPER_MODULE)
        except ImportError as exc:
            raise TTSError(
                f"导入不到 {HELPER_MODULE}.py：{exc}　"
                "在 .env 里设 QWEN3_TTS_HELPER_DIR 指向 openvino_notebooks 的 "
                "notebooks/qwen3-tts 目录"
            ) from exc


def _load_on_device(helper: Any, model_dir: Path, device: str) -> Any:
    """
    按指定设备加载 / Load with the device actually applied.

    见模块文档：上游把 talker 与 speech tokenizer 的设备写死成 GPU，忽略传入的参数。
    这里把两个类临时换成「固定用我们这台设备」的子类，调完在 `finally` 里换回去。

    为什么用临时替换而不是复刻一遍装配流程 / Why substitution rather than re-implementing:
        `from_pretrained` 还负责找 processor、找 speech tokenizer、读 generation config，
        复刻一遍就等于抄 50 行会随上游变化而失效的代码。替换两个类只针对
        「设备被写死」这一个问题，其余照走上游的路径。
        `from_pretrained` also locates the processor and the speech tokenizer and reads
        the generation config. Re-implementing it would copy fifty lines that go stale
        with upstream, whereas substituting two classes targets only the pinned device.
    """
    talker_cls = helper.OVQwen3TTSTalkerForConditionalGeneration
    tokenizer_cls = helper.OVQwen3TTSSpeechTokenizer

    class _PinnedTalker(talker_cls):  # type: ignore[misc, valid-type]
        def __init__(self, md, _ignored_device, config):  # noqa: ANN001
            super().__init__(md, device, config)

    class _PinnedTokenizer(tokenizer_cls):  # type: ignore[misc, valid-type]
        # 只改 encoder 的设备；decoder（声码器）在上游内部固定为 CPU，保持不动
        def __init__(self, md, _ignored_device="CPU"):  # noqa: ANN001
            super().__init__(md, device)

    # 上游那两行 `Loading OpenVINO Talker on GPU` 的打印读的是它自己的局部变量，
    # **替换之后这句话就不再是真的了**。以 `provider.info` 与 `model.talker.device`
    # 为准；`dna tts` 显示的是前者。
    # Upstream's "Loading OpenVINO Talker on GPU" line reads its own local variable and
    # stops being true once the classes are substituted; `provider.info` is authoritative.
    helper.OVQwen3TTSTalkerForConditionalGeneration = _PinnedTalker
    helper.OVQwen3TTSSpeechTokenizer = _PinnedTokenizer
    try:
        return helper.OVQwen3TTSModel.from_pretrained(str(model_dir), device=device)
    finally:
        helper.OVQwen3TTSTalkerForConditionalGeneration = talker_cls
        helper.OVQwen3TTSSpeechTokenizer = tokenizer_cls


__all__ = ["PROVIDER_NAME", "SUPPORTED_DEVICES", "Qwen3OpenVINOTTS"]
