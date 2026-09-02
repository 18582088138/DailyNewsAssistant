"""
本地 Ollama provider / Local Ollama provider.

Ollama 自带 OpenAI 兼容端点（`/v1`），所以这里直接复用 OpenAICompatProvider，
而不是再写一套 `/api/chat` 的原生客户端。
Ollama exposes an OpenAI-compatible endpoint at `/v1`, so this reuses
OpenAICompatProvider instead of adding a second, native `/api/chat` client.

这样做的收益 / Why this matters:
    重试分类、超时处理、<think> 剥离、用量统计这些逻辑只有一份实现，
    云端与本地的行为因此天然一致——这正是后续「迁移到 Local LLM」时
    最容易出问题的地方。
    Retry classification, timeout handling, <think> stripping and usage accounting
    exist in exactly one place, so cloud and local behave identically. That is
    precisely where a migration to a local LLM usually goes wrong.
"""

from __future__ import annotations

from typing import Any

from dna.llm.openai_compat import OpenAICompatProvider

# Ollama 不校验 API key，但 OpenAI SDK 要求非空 / Ollama ignores the key, the SDK requires one
_PLACEHOLDER_KEY = "ollama"


class OllamaProvider(OpenAICompatProvider):
    """
    通过 Ollama 在本机运行的模型 / A model served locally by Ollama.

    参数 / Args:
        base_url: Ollama 根地址，如 http://localhost:11434（不含 /v1）
        model:    模型名，如 qwen3.5:9b
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3.5:9b",
        timeout: float = 300.0,  # 本地推理比云端慢，超时给宽一些
        client: Any | None = None,
    ) -> None:
        super().__init__(
            name="ollama",
            model=model,
            api_key=_PLACEHOLDER_KEY,
            base_url=normalize_base_url(base_url),
            is_local=True,
            timeout=timeout,
            client=client,
        )


def normalize_base_url(base_url: str) -> str:
    """
    把 Ollama 根地址补成 OpenAI 兼容端点 / Normalise the Ollama root into its OpenAI endpoint.

    容忍用户在 .env 里写成带或不带 `/v1`、带或不带结尾斜杠的各种形式。
    Tolerates the various forms a user may put in .env, with or without `/v1`
    and with or without a trailing slash.

    >>> normalize_base_url("http://localhost:11434")
    'http://localhost:11434/v1'
    >>> normalize_base_url("http://localhost:11434/v1/")
    'http://localhost:11434/v1'
    """
    trimmed = base_url.rstrip("/")
    return trimmed if trimmed.endswith("/v1") else f"{trimmed}/v1"


__all__ = ["OllamaProvider", "normalize_base_url"]
