"""
DailyNewsAssistant —— 每日 AI 资讯采集与多形态内容生产。
DailyNewsAssistant — daily AI news collection and multi-format content production.

分层约定（严格单向依赖）/ Layering (strictly one-way dependencies):
    frontends → apps → narration/render → pipeline → sources/inbox/llm/tts/store → core
"""

__version__ = "0.1.0"
