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
    """LLM / TTS 提供方调用失败 / An LLM or TTS provider call failed."""


class RenderError(DNAError):
    """渲染失败（Markdown / HTML / 长图 / 音频）/ Rendering failed."""


class StoreError(DNAError):
    """落盘或数据库操作失败 / Filesystem or database operation failed."""


class InboxError(DNAError):
    """远程投递接口异常 / Remote inbox (Feishu bot) error."""
