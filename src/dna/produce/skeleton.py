"""
手写稿的骨架 / A skeleton for a hand-written script.

某一格产物还没生成时，展开面板里给出的不是一句「还没有生成」，而是一份**排好版的空稿**：
抬头、主副标题、朗读标记行都在，用户双击进去把自己的内容填进去即可，一个字都不用 LLM。
When a production does not exist yet, opening its panel offers a formatted empty script
rather than a bare "not generated yet": the header, the title pair and the spoken marker
line are already in place, so the user can double-click and write their own — not a single
LLM call.

为什么不预先生成占位产物 / Why placeholders are not written at creation time:
    `produce()` 默认**复用已有产物**。建文章时就把四格写成占位稿的话，之后点「生成」
    会直接复用那份占位稿、根本不调模型——一个看起来成功、内容却是模板的产物。
    所以骨架**只显示在编辑器里**，落盘发生在用户真的保存的那一刻。
    `produce()` reuses what already exists, so pre-writing placeholder files would make
    "generate" reuse the placeholder instead of calling the model: a successful-looking
    artefact whose content is a template. The skeleton therefore lives only in the editor,
    and is written only when the user actually saves.

骨架的排版必须与生成路径一致（`documents.spoken_block` / `front_matter`）：它要能被
`spoken_text` 读回正文、被 TTS 念出来，否则手写稿会合成出空音频。
The layout must match the generated path, or a hand-written script would not be readable by
the spoken-text parser and would synthesise to empty audio.
"""

from __future__ import annotations

from dna.core.config import Profile, safe_profile
from dna.core.models import Article
from dna.produce.documents import front_matter, spoken_block
from dna.produce.tasks import ProductionKind, char_window, json_sidecar, spec

# 骨架里的占位句子 / the placeholder lines inside a skeleton
PLACEHOLDER_MAIN_TITLE = "（在此填主标题）"
PLACEHOLDER_SUBTITLE = "（在此填副标题）"
PLACEHOLDER_SCRIPT = "（在这里写正文。保存之后这一格就是你自己的稿子，不会再调用 LLM。）"
PLACEHOLDER_SUMMARY = "（在这里写总结。）"


def skeleton_for(
    article: Article,
    kind: ProductionKind | str,
    lang: str,
    *,
    profile: Profile | None = None,
) -> str:
    """
    这一格的空稿骨架 / The empty skeleton for one production cell.

    返回**空串**表示这一类产物不该有骨架，调用方应退回原来的「还没有生成」提示：
    音频是二进制的、长视频的稿子按发言人分轮存在 JSON 附件里，两者都不是
    「双击一段文本改一改」能解决的（长视频要改请去 TTS 操作台）。
    An empty string means this kind gets no skeleton and the caller should fall back to the
    plain "not generated yet" note: audio is binary, and the long-form script keeps its
    speaker turns in a JSON sidecar, so neither is a block of text one can double-click
    and edit.
    """
    task = spec(kind)
    if task.audio_of is not None or json_sidecar(task, lang):
        return ""

    prof = profile or safe_profile()
    header = front_matter(article, f"{task.label}（{lang}）")
    if not task.spoken:
        return header + PLACEHOLDER_SUMMARY + "\n"

    # 朗读标记行里的秒数与字数是**估算**，取配置窗口的中点：数字要看起来合理，
    # 用户保存时 `save_production_text` 会按真实正文重算一遍。
    # The seconds and characters are estimates taken from the middle of the configured
    # window; they only need to look plausible, and `save_production_text` recomputes both
    # from the real body the moment the user saves.
    window = char_window(task.kind, prof)
    chars = sum(window) // 2 if window else 0
    minutes = _duration_window(task.kind, prof)
    seconds = sum(minutes) / 2 if minutes else 0.0
    return (
        header
        + spoken_block(
            title=PLACEHOLDER_MAIN_TITLE,
            subtitle=PLACEHOLDER_SUBTITLE,
            text=PLACEHOLDER_SCRIPT,
            seconds=seconds,
            chars=chars,
        )
    )


def _duration_window(kind: ProductionKind, profile: Profile) -> tuple[int, int] | None:
    """这一类产物的时长窗口（秒）/ The duration window in seconds for one kind."""
    if kind is ProductionKind.SHORTVIDEO:
        return profile.video_duration_seconds
    if kind is ProductionKind.NARRATION:
        return profile.narration_duration_seconds
    return None


__all__ = [
    "PLACEHOLDER_MAIN_TITLE",
    "PLACEHOLDER_SCRIPT",
    "PLACEHOLDER_SUBTITLE",
    "PLACEHOLDER_SUMMARY",
    "skeleton_for",
]
