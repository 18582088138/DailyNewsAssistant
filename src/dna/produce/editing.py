"""
人工编辑过的产物写回文件与台账 / Write a hand-edited production back to file and ledger.

工作台的就地编辑与 TTS 操作台的校对走同一条路：零费用、零 LLM，覆盖文件并插一行
`calls=0` 的台账记录。**字数与估算时长按新文本重算**——不重算的话格子会一直显示
LLM 那一版的数字，而人已经删掉了一半。
"""

from __future__ import annotations

from dna.core.config import Settings, get_settings
from dna.core.logging import get_logger
from dna.narration.duration import count_units, estimate_seconds
from dna.produce.documents import replace_spoken, spoken_text, strip_front_matter
from dna.produce.tasks import ProductionKind, audio_kind, json_sidecar, normalize_lang, spec
from dna.store.ledger import Ledger

logger = get_logger("produce.editing")

WORKBENCH_EDIT = "人工编辑（工作台）"


def is_editable(kind: ProductionKind | str, lang: str = "") -> bool:
    """这类产物能不能整份人工编辑 / Whether a kind can be edited as plain text。"""
    lang = normalize_lang(lang)
    task = spec(kind)
    return task.audio_of is None and not json_sidecar(task, lang)


def save_production_text(
    article_id: str,
    kind: ProductionKind | str,
    markdown: str,
    *,
    lang: str = "",
    instructions: str = WORKBENCH_EDIT,
    settings: Settings | None = None,
) -> int:
    """
    整份产物文件写回 / Overwrite a whole production file with edited text.

    中视频类（有对应音频的稿子）按 `spoken_text` 数字数、估时长，并刷新「中视频（约 N 秒
    · M 字）」那一行；总结只数抬头之外的正文。长视频的稿子在 `.json` 附件里，拒绝。

    返回新插入的台账行 id / Returns the id of the inserted ledger row.
    """
    s = settings or get_settings()
    task = spec(kind)
    lang = normalize_lang(lang)
    if not is_editable(task.kind, lang):
        raise ValueError(f"{task.label}不能在这里改：请在 TTS 操作台里改")

    record = Ledger(s.db_file).get(article_id)
    if record is None or not record.store_dir:
        raise ValueError(f"台账里没有这篇文章或它还没落盘：{article_id}")

    text = markdown.strip()
    seconds: float | None = None
    if audio_kind(task.kind) is not None:
        spoken = spoken_text(text)
        text = replace_spoken(text, spoken, lang=lang).strip()
        chars = count_units(spoken, lang=lang)
        seconds = estimate_seconds(spoken, lang=lang)
    else:
        chars = len(strip_front_matter(text))

    path = s.output_path / record.store_dir / task.filename_for(lang)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")

    logger.info("人工编辑已写回：%s（%d 字）", path.name, chars)
    return Ledger(s.db_file).record_production(
        article_id,
        str(task.kind),
        status="ok",
        lang=lang,
        instructions=instructions,
        output_path=path.relative_to(s.output_path).as_posix(),
        chars=chars,
        est_seconds=seconds,
        # 模型与调用留空是台账上「这一版不是 LLM 写的」的唯一标记
        llm_provider=None,
        llm_model=None,
        calls=0,
    )


__all__ = ["WORKBENCH_EDIT", "is_editable", "save_production_text"]
