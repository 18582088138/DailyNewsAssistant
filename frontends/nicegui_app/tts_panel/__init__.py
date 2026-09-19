"""
TTS 操作台 / The TTS console.

**每天都用的那块面板**，不是罕用功能：调文本、校对生成内容都在这里做。

四件用户角度的事 / Four things from the user's side:
    · **点开不合成** —— 它是一个描边按钮，和「合成音频」之间隔一条竖线
    · `✔ / ✎ / ○` 三态；`▶` 生成本段**免费、不写台账、而且音频会被复用**
    · 正文可编辑，改完会写回稿子（长文案例外，它按发言人分轮存在 JSON 边车里）
    · 服务离线时面板照样能开，音色框退成自由文本

完整说明见 `docs/14_tts_guide.md`，那份是 TTS 的唯一权威。

分在哪几个文件里 / Where things live:
    model   三态与一段的状态模型
    shell   `open_panel` 入口
    view    整块布局与每段控件
    edit    插标记、删段、重新拆条、回填
    synth   逐段生成、全部生成、拼接保存
"""

from frontends.nicegui_app.tts_panel.model import (
    EVENT_MARKERS,
    STATE_DONE,
    STATE_HINTS,
    STATE_NEW,
    STATE_STALE,
    _Panel,
    _Piece,
)
from frontends.nicegui_app.tts_panel.shell import (
    open_panel,
)

# `_Panel` / `_Piece` 也导出：它们是测试直接构造的状态模型（三态与复用的
# 判定全在上面），不导出就得让测试去 import 子模块，等于把拆法固化进测试。
__all__ = [
    "EVENT_MARKERS",
    "STATE_DONE",
    "STATE_HINTS",
    "STATE_NEW",
    "STATE_STALE",
    "_Panel",
    "_Piece",
    "open_panel",
]
