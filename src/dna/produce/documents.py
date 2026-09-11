"""
产物文件的写与读 / Writing and reading the production files.

**写和读放在同一个文件里，是为了它们不可能各写各的。**
口播稿是这样落盘的：抬头 + 主副标题 + 一行「口播（约 N 秒 · M 字）：」+ 正文。
TTS 要的只有最后那段正文——抬头里的 URL 一旦被念出来，整段音频就废了。
分成两个模块的话，哪天改了排版，解析这边不会报错，只会**安静地把网址念出来**。
Renderer and parser live together so they cannot drift. The header carries a URL, and if
the parser falls out of step with the layout nothing raises — the URL simply gets read
aloud.

长文案走另一条路 / Long-form takes the other route:
    它已经有一份 `.json` 附件，按发言人切好了 turns。有结构化数据就用结构化数据，
    没有理由回头去解析 Markdown。
    It already ships a JSON sidecar split into speaker turns; structured data is used
    where it exists.
"""

from __future__ import annotations

import json
from pathlib import Path

from dna.core.models import Article
from dna.narration.duration import count_units, estimate_seconds
from dna.narration.script_builder import ScriptResult

# 口播正文的起始标记 / where the spoken body begins
#
# 解析靠它定位，所以它必须由**同一个文件**写出来。改这一行就要一起改渲染。
# The parser locates the body by this marker, which is why the writer sits beside it.
SPOKEN_MARKER = "**口播（"

# 抬头里那些不该被念出来的行 / header lines that must never be spoken
_METADATA_PREFIXES = ("# ", "> ", "**主标题：**", "**副标题：**")


def front_matter(article: Article, label: str) -> str:
    """
    产物文件的抬头 / The header every production file carries.

    带上原文标题与链接：这些文件会被单独拷去发布，脱离目录之后仍要能追溯来源——
    和图片旁边放 .json 是同一个道理。
    Carries the source title and link because these files get copied out for publishing
    and must remain traceable on their own — the same reasoning as the per-image sidecar.
    """
    return f"# {article.title}\n\n> {label}　·　来源：{article.url}\n\n"


def script_block(script: ScriptResult) -> str:
    """
    短视频稿与口播稿的正文排版 / The body layout shared by both video scripts.

    两种稿子都带主副标题——发布时标题栏要填，写在产物里就不用再想一遍。
    Both carry a title pair, because the publishing form needs one and having it in the
    artefact saves composing it again.
    """
    parts = []
    if script.title:
        parts.append(f"**主标题：** {script.title}\n")
    if script.subtitle:
        parts.append(f"**副标题：** {script.subtitle}\n")
    parts.append(f"{SPOKEN_MARKER}约 {script.seconds:.0f} 秒 · {script.chars} 字）：**\n")
    parts.append(f"{script.text}\n")
    return "\n".join(parts)


def spoken_text(markdown: str) -> str:
    """
    从产物文件里取出**要念的那部分** / Extract only what should be read aloud.

    优先取标记之后的内容；没有标记时（旧产物、手工编辑过的文件）退回
    「剔掉抬头与主副标题行」，而不是整篇照念。
    Content after the marker wins. Without one — an older file, or one edited by hand —
    the metadata lines are dropped rather than the whole file being read verbatim.
    """
    if not markdown:
        return ""

    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(SPOKEN_MARKER):
            return "\n".join(lines[index + 1 :]).strip()

    kept = [ln for ln in lines if not ln.startswith(_METADATA_PREFIXES)]
    return "\n".join(kept).strip()


def replace_spoken(markdown: str, text: str, *, lang: str = "zh") -> str:
    """
    换掉稿子文件里要念的那段 / Swap the spoken body of a script file，`spoken_text` 的反手。

    给 TTS 操作台里的人工校对用：校对过的文本必须回到稿子文件里，否则音频念的和
    台账里记的稿子不是一回事，而台账是「这一版到底念了什么」的唯一答案。
    Used when a person proof-reads in the TTS console: the edited text has to go back into
    the script file, or the audio and the recorded script part ways.

    **抬头逐字节保留**（标题、来源 URL、主副标题行），只有标记之后的正文被换掉。
    标记那一行里的「约 N 秒 · M 字」按新文本重算 —— 不重算的话文件会一直声称
    自己是 200 字，而正文已经 250 字了，这种谎话比没有那行更坏。
    The header is preserved byte for byte; the marker's own duration and character counts
    are recomputed, because a stale count is worse than none.

    原文没有标记时（旧产物，或被手工编辑过的文件）只留元信息行并**补上标记**，
    于是下一次 `spoken_text()` 是精确定位，而不是退回「剔掉元信息行」那条模糊路径。
    """
    spoken = text.strip()
    lines = markdown.splitlines()

    header: list[str] = []
    for line in lines:
        if line.startswith(SPOKEN_MARKER):
            break
        header.append(line)
    else:
        header = [ln for ln in lines if ln.startswith(_METADATA_PREFIXES)]
        if header:
            header.append("")

    marker = (
        f"{SPOKEN_MARKER}约 {estimate_seconds(spoken, lang=lang):.0f} 秒 · "
        f"{count_units(spoken, lang=lang)} 字）：**"
    )
    return "\n".join([*header, marker, "", spoken, ""])


def longform_turns(sidecar: Path) -> list[tuple[str, str]]:
    """
    从长文案的 JSON 附件读回角色轮次 / Read the speaker turns back from the sidecar.

    返回 `[(角色, 文本), …]`；专题模式全是 `narrator`，访谈模式是 `host`/`guest`。
    两种模式同一种结构，TTS 侧照着角色分配音色即可。

    读不到时返回空列表，由调用方决定怎么退——退到解析 Markdown 是可以的，
    但那条路径必须是**明确选择**，不该由一个异常悄悄决定。
    Returns an empty list when unreadable; falling back to parsing the Markdown is a
    decision for the caller to make explicitly rather than one an exception makes.
    """
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    turns = data.get("turns") or []
    out: list[tuple[str, str]] = []
    for turn in turns:
        text = (turn.get("text") or "").strip()
        if text:
            out.append((turn.get("speaker") or "narrator", text))
    return out


def strip_front_matter(text: str) -> str:
    """去掉抬头，取正文 / Drop the header and return the body."""
    lines = [ln for ln in text.splitlines() if not ln.startswith(("# ", "> "))]
    return "\n".join(lines).strip()


__all__ = [
    "SPOKEN_MARKER",
    "front_matter",
    "longform_turns",
    "replace_spoken",
    "script_block",
    "spoken_text",
    "strip_front_matter",
]
