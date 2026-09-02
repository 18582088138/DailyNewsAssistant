"""
OpenVINO 本地推理 provider —— 接口占位 / OpenVINO local provider — interface placeholder.

按方案安排在 P10 实现。这里先把类建好并注册进工厂，好处是：
  1. 工厂、配置、GUI 的 provider 列表现在就能把它算进去，届时不用改这些地方
  2. 误用（LLM_PROVIDER=openvino）会得到明确的说明，而不是 KeyError
Implementation is scheduled for P10. The class exists now so the factory, config
and GUI provider list already account for it, and so that setting
LLM_PROVIDER=openvino today produces a clear message instead of a KeyError.

实现时的要点 / Notes for the implementation:
    - 用 optimum.intel 的 OVModelForCausalLM 加载 IR，device 取 OPENVINO_LLM_DEVICE
    - 复用 dna.llm.parsing.strip_think_tags 处理 Qwen3 的思维链
    - 本地推理没有 token 计费，Usage 可用 tokenizer 统计后回填，供台账统计耗时/长度
"""

from __future__ import annotations

from dna.core.errors import ProviderError
from dna.llm.base import ChatMessage, ChatResult, LLMProvider, ProviderInfo


class OpenVINOProvider(LLMProvider):
    """
    以 OpenVINO IR 在 Intel CPU/GPU 上本地推理 / Local inference on Intel CPU/GPU.

    尚未实现；调用时抛出带指引的异常。
    Not implemented yet; calling it raises an actionable error.
    """

    def __init__(self, *, model_dir: str = "", device: str = "GPU") -> None:
        self._info = ProviderInfo(
            name="openvino",
            model=model_dir or "(未配置)",
            is_local=True,
            base_url=None,
        )
        self._model_dir = model_dir
        self._device = device

    @property
    def info(self) -> ProviderInfo:
        return self._info

    def _complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
        timeout: float,
        json_mode: bool = False,
    ) -> ChatResult:
        raise ProviderError(
            "OpenVINO LLM provider 尚未实现（计划于 P10 阶段完成）。"
            "当前请把 .env 的 LLM_PROVIDER 改为 deepseek / openrouter / ollama。"
            " / The OpenVINO provider is scheduled for phase P10; use deepseek,"
            " openrouter or ollama for now."
        )


__all__ = ["OpenVINOProvider"]
