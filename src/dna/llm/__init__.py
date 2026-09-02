"""
LLM 层 / LLM layer：API 与本地推理的统一抽象。

业务代码只 import 这里的东西，不要直接 import 具体 provider 实现。
Business code imports from here only; never import a concrete provider directly.

    from dna.llm import get_llm, system, user

    llm = get_llm()
    text = llm.chat_text([system("你是资讯编辑"), user("总结这条新闻…")])
    obj  = llm.chat_json([user("抽取要点…")], MySchema)
"""

from dna.llm.base import (
    ChatMessage,
    ChatResult,
    LLMProvider,
    ProviderInfo,
    Usage,
    assistant,
    system,
    user,
)
from dna.llm.factory import (
    KNOWN_PROVIDERS,
    ResilientProvider,
    RetryPolicy,
    build_provider,
    get_llm,
    is_configured,
)
from dna.llm.parsing import extract_json_block, strip_think_tags

__all__ = [
    "KNOWN_PROVIDERS",
    "ChatMessage",
    "ChatResult",
    "LLMProvider",
    "ProviderInfo",
    "ResilientProvider",
    "RetryPolicy",
    "Usage",
    "assistant",
    "build_provider",
    "extract_json_block",
    "get_llm",
    "is_configured",
    "strip_think_tags",
    "system",
    "user",
]
