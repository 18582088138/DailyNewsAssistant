"""
朗读文本的清洗与分段 / Preparing text for the microphone.

两件事，顺序不能反 / Two steps, and the order matters:
    1. **清洗**：去掉 Markdown 标记、链接、括注 —— 它们是给眼睛看的，不是给耳朵听的
    2. **分段**：切成模型吃得下的长度，且**只在句子边界切**

为什么必须清洗 / Why cleaning is not optional:
    产物文件是 Markdown：抬头有 `# 标题`、`> 口播文案　·　来源：https://…`，
    正文里有 `**主标题：**`。直接送进 TTS，模型会**把网址一个字符一个字符念出来**，
    把星号念成「星星」。这不是音质问题，是整段音频废掉。
    The production files are Markdown with a title, a source URL and bold labels. Fed
    straight to the model, the URL gets spelled out character by character and the
    asterisks are voiced. That does not degrade the audio; it destroys it.

为什么必须在句子边界切 / Why splits must land on sentence boundaries:
    每一段是**独立一次合成**，模型不知道上一段的语气。在句子中间切开，
    拼起来会听见明显的断裂与语调重置。宁可某一段短一点。
    Each piece is synthesised independently with no memory of the previous one. A split
    mid-sentence is audible as a break and a pitch reset when the pieces are joined.
"""

from __future__ import annotations

import re

# 一段最多多少字 / maximum characters per segment
#
# 实测 63 字一段合成正常。上限定在 120 是留余量：太长的段容易在结尾掉字，
# 太短又会让停顿变多、听起来一顿一顿的。
# Measured fine at 63 characters. The ceiling leaves headroom: longer pieces tend to
# drop words at the tail, while shorter ones multiply the pauses and sound choppy.
DEFAULT_MAX_SEGMENT_CHARS = 120

# 句子结束符 / sentence terminators
_SENTENCE_END = "。！？!?…；;"
# 退而求其次的切分点：一句话本身就超长时用 / fallback breaks for over-long sentences
_CLAUSE_END = "，,、）)】」》"

_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+|www\.\S+")
_MD_EMPHASIS = re.compile(r"[*_`~]{1,3}")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")
_BLANK_LINES = re.compile(r"\n{3,}")


def clean_for_speech(text: str) -> str:
    """
    把 Markdown 正文清成可朗读的纯文本 / Reduce Markdown to speakable plain text.

    去掉的东西 / What is removed:
        HTML 注释（长文案的提纲藏在这里）、图片、链接**连同网址**、行内标记、
        标题井号、引用尖括号、列表符号、分隔线

    保留的东西 / What is kept:
        链接的**锚文本**（`[Qwen3](http://…)` → `Qwen3`）——那是句子的一部分，
        丢掉会让句子读不通；网址本身丢掉，因为没人想听人念 URL。
        The anchor text of a link is part of the sentence and is kept; the address is
        dropped, because nobody wants a URL read aloud.
    """
    if not text:
        return ""

    out = _HTML_COMMENT.sub(" ", text)
    out = _MD_IMAGE.sub(" ", out)
    out = _MD_LINK.sub(r"\1", out)
    out = _URL.sub(" ", out)
    out = _HTML_TAG.sub(" ", out)

    lines: list[str] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line or set(line) <= set("-=*_ "):  # 分隔线 / horizontal rules
            lines.append("")
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)  # 标题井号
        line = re.sub(r"^>\s*", "", line)  # 引用
        line = re.sub(r"^[-*+]\s+", "", line)  # 无序列表
        line = re.sub(r"^\d+[.、)]\s+", "", line)  # 有序列表
        line = _MD_EMPHASIS.sub("", line)
        lines.append(line.strip())

    out = "\n".join(lines)
    out = _BLANK_LINES.sub("\n\n", out)
    return out.strip()


def split_for_speech(text: str, *, max_chars: int = DEFAULT_MAX_SEGMENT_CHARS) -> list[str]:
    """
    切成一段段可以单独合成的文本 / Split into independently synthesisable pieces.

    三级退让 / Three tiers, each used only when the previous cannot help:
        1. 段落之间必切（段落本来就是停顿点）
        2. 段落内按句子累加，加到超过 `max_chars` 就断
        3. **单句本身就超长**时按逗号切；连逗号都没有才硬切

    空输入返回空列表，不返回 `[""]` —— 调用方据此判断「没有可念的内容」，
    而一个空字符串的分段会让 provider 生成一段静音，看上去像成功了。
    Empty input yields an empty list rather than one empty piece: the caller uses that to
    detect "nothing to say", whereas an empty piece would synthesise silence and look
    like success.
    """
    cleaned = clean_for_speech(text)
    if not cleaned:
        return []

    max_chars = max(20, max_chars)
    pieces: list[str] = []

    for paragraph in (p.strip() for p in cleaned.split("\n\n")):
        if not paragraph:
            continue
        pieces.extend(_split_paragraph(paragraph, max_chars))

    return [p for p in pieces if p.strip()]


# ---------------------------------------------------------------------------
# 内部实现 / internals
# ---------------------------------------------------------------------------


def _split_paragraph(paragraph: str, max_chars: int) -> list[str]:
    """按句子累加，超长再退一级 / Accumulate sentences, falling back when one is too long."""
    out: list[str] = []
    buffer = ""

    for sentence in _sentences(paragraph):
        if len(sentence) > max_chars:
            if buffer:
                out.append(buffer)
                buffer = ""
            out.extend(_split_long_sentence(sentence, max_chars))
            continue

        if len(buffer) + len(sentence) <= max_chars:
            buffer += sentence
        else:
            if buffer:
                out.append(buffer)
            buffer = sentence

    if buffer:
        out.append(buffer)
    return out


def _sentences(paragraph: str) -> list[str]:
    """
    切句，**标点跟着前一句走** / Split into sentences, keeping terminators attached.

    句号留在句尾不是格式洁癖：模型靠它判断句子的语调收尾，
    去掉之后每一段听起来都像话没说完。
    Keeping the full stop is not cosmetic: the model uses it to close the intonation, and
    without it every piece sounds unfinished.
    """
    out: list[str] = []
    buffer = ""
    for char in paragraph:
        buffer += char
        if char in _SENTENCE_END:
            out.append(buffer)
            buffer = ""
    if buffer.strip():
        out.append(buffer)
    return out


def _split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    """一句话本身超长时按次级标点切，最后才硬切 / Break an over-long sentence on clauses."""
    out: list[str] = []
    buffer = ""

    for char in sentence:
        buffer += char
        if char in _CLAUSE_END and len(buffer) >= max_chars * 0.6:
            out.append(buffer)
            buffer = ""
        elif len(buffer) >= max_chars:
            # 连次级标点都没有（长串英文或数字），只能硬切
            out.append(buffer)
            buffer = ""

    if buffer.strip():
        out.append(buffer)
    return out


__all__ = ["DEFAULT_MAX_SEGMENT_CHARS", "clean_for_speech", "split_for_speech"]
