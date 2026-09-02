"""
异常定义 / Exception hierarchy.

全部业务异常继承 DNAError，便于上层（CLI / GUI）统一捕获并给出友好提示，
同时与 Python 内建异常区分开。
All domain exceptions derive from DNAError so that the CLI / GUI layer can catch
them uniformly and distinguish them from built-in Python errors.
"""

from __future__ import annotations


class DNAError(Exception):
    """所有 DailyNewsAssistant 业务异常的基类 / Base class for all domain errors."""


class ConfigError(DNAError):
    """配置缺失或非法 / Configuration is missing or invalid."""


class SourceError(DNAError):
    """信息源采集失败 / Failed to fetch from a news source."""


class ExtractionError(DNAError):
    """正文或媒体抽取失败 / Failed to extract article body or media assets."""


class ProviderError(DNAError):
    """
    LLM / TTS 提供方调用失败 / An LLM or TTS provider call failed.

    子类用 `retryable` 区分「重试可能有用」与「重试没意义」，
    工厂层据此决定是退避重试还是直接切备用 provider。
    Subclasses expose `retryable` so the factory can decide between backing off
    and switching straight to the fallback provider.
    """

    retryable: bool = False


class RateLimitError(ProviderError):
    """触发限流，退避后重试通常有效 / Rate-limited; backing off usually helps."""

    retryable = True


class ProviderTimeoutError(ProviderError):
    """调用超时 / The provider call timed out."""

    retryable = True


class ProviderResponseError(ProviderError):
    """
    返回内容不可用（空回复、JSON 解析失败、结构校验不通过）。
    The response was unusable: empty, unparseable JSON, or failing schema validation.
    """

    retryable = True


class AuthError(ProviderError):
    """
    鉴权失败。重试没有意义，应直接切换到备用 provider。
    Authentication failed. Retrying is pointless; switch to the fallback instead.
    """

    retryable = False


class RenderError(DNAError):
    """渲染失败（Markdown / HTML / 长图 / 音频）/ Rendering failed."""


class StoreError(DNAError):
    """落盘或数据库操作失败 / Filesystem or database operation failed."""


class InboxError(DNAError):
    """远程投递接口异常 / Remote inbox (Feishu bot) error."""
