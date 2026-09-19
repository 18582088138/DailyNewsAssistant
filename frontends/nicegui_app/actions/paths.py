"""产物路径与达标判定 / Where a production lives and whether it hit its window。"""

from __future__ import annotations

from pathlib import Path

from dna.core.config import get_settings
from dna.core.logging import get_logger
from dna.produce import (
    ProductionKind,
    read_production,
    spec,
)
from dna.produce.tasks import json_sidecar, normalize_lang
from dna.store import ProductionRecord
from dna.store.ledger import ArticleRecord

logger = get_logger("gui.actions")

def production_text(
    article_id: str, kind: ProductionKind | str, lang: str = ""
) -> str:
    """读回产物内容供预览 / Read a production back for preview."""
    lang = normalize_lang(lang)
    return read_production(article_id, kind, lang=lang)


def article_directory(record: ArticleRecord) -> str:
    """产物目录的绝对路径 / The absolute path of the article's directory."""
    if not record.store_dir:
        return ""
    return str(get_settings().output_path / record.store_dir)


def production_file(
    record: ArticleRecord, kind: ProductionKind | str, lang: str = ""
) -> Path | None:
    """
    某个产物的文件路径 / The file path of one production.

    给下载按钮用。**返回 None 表示文件不在**——按钮据此禁用，
    而不是让人点了之后拿到一个 404。
    Used by the download buttons. `None` means the file is missing, so the button is
    disabled rather than handing the user a 404 after the click.
    """
    lang = normalize_lang(lang)
    if not record.store_dir:
        return None
    path = get_settings().output_path / record.store_dir / spec(kind).filename_for(lang)
    return path if path.exists() else None


def subtitle_file(
    record: ArticleRecord, kind: ProductionKind | str, lang: str = ""
) -> Path | None:
    """
    音频旁边的字幕 / The subtitle file next to one audio production.

    字幕是**音频那一格的产物**（`produce()` 写在音频同名 `.srt` 上，时间轴按每段
    波形的真实长度算），但界面上此前完全没提过它——于是「操作台按分段出字幕」
    这件事做了，人看不见，只能当成没做。
    Written all along, but nothing in the UI ever pointed at it.
    """
    lang = normalize_lang(lang)
    audio = production_file(record, kind, lang)
    if audio is None:
        return None
    srt = audio.with_suffix(".srt")
    return srt if srt.exists() else None


def production_sidecar(
    record: ArticleRecord, kind: ProductionKind | str, lang: str = ""
) -> Path | None:
    """
    产物的 JSON 附件 / A production's JSON sidecar, when it has one.

    目前只有长文案有：按发言人切好的轮次，P6/P7 的 TTS 要用它分配音色。
    Only the long-form script has one today: the speaker turns the TTS stage needs.
    """
    lang = normalize_lang(lang)
    name = json_sidecar(spec(kind), lang)
    if not name or not record.store_dir:
        return None
    path = get_settings().output_path / record.store_dir / name
    return path if path.exists() else None


def media_folders(record: ArticleRecord) -> dict[str, Path]:
    """
    素材子目录 / The media sub-directories that actually exist.

    只返回**真实存在且非空**的，界面才不会给出一个点开是空的按钮。
    Only directories that exist and hold something, so no button opens onto nothing.
    """
    directory = article_directory(record)
    if not directory:
        return {}

    result: dict[str, Path] = {}
    for label, name in (("配图", "images"), ("视频", "videos")):
        path = Path(directory) / name
        if path.is_dir() and any(path.iterdir()):
            result[label] = path
    return result


def body_file(record: ArticleRecord) -> Path | None:
    """
    正文文件 / The stored article body.

    **返回 None 表示没有可打开的东西**——抓取失败的文章目录里根本没有 `article.md`，
    界面据此不把那一格做成可点的。
    `None` means there is nothing to open: a failed fetch leaves no `article.md`, and the
    cell stays unclickable rather than opening onto an error.
    """
    directory = article_directory(record)
    if not directory:
        return None
    path = Path(directory) / "article.md"
    return path if path.is_file() else None


def media_target(record: ArticleRecord) -> Path | None:
    """
    媒体格该打开哪个目录 / Which directory the media cell opens.

    有图开 `images/`，否则有视频开 `videos/`，都没有返回 None。
    **配图优先**：绝大多数文章只有配图，视频是少数；而两者都有时人要看的
    通常是配图（它进图文版），视频还在展开面板里另有入口。
    Images win when both exist: they are what almost every article has and what the
    graphic edition uses, while videos keep their own entry in the detail panel.
    """
    folders = media_folders(record)
    return folders.get("配图") or folders.get("视频")


def over_target(
    record: ProductionRecord | None, kind: ProductionKind | str
) -> bool:
    """
    这一格的字数是否落在目标区间之外 / Whether this cell's length misses its window.

    **现算，不从库里读。** 台账里不存「是否合格」这个布尔值：改了 `profile.yaml`
    的字数窗口，历史产物应当跟着重新判定，而存下来的布尔值不会变——于是界面上
    显示的合格标准和下一次生成用的标准不是同一个。
    Computed rather than stored: editing the character window in `profile.yaml` must
    re-judge existing productions, whereas a stored boolean would not change and the
    table's notion of "acceptable" would drift from the generator's.

    窗口取自 `char_window()`，和生成时验收用的是同一个函数——否则会出现
    「生成时报合格、表格里标超长」。
    """
    from dna.core.config import safe_profile
    from dna.produce.tasks import char_window

    if record is None or not record.ok or not record.chars:
        return False
    window = char_window(kind, safe_profile())
    if window is None:
        return False
    low, high = window
    return not (low <= record.chars <= high)


def target_window(kind: ProductionKind | str) -> tuple[int, int] | None:
    """这类产物的字数区间 / The character window for one kind，供提示文案用。"""
    from dna.core.config import safe_profile
    from dna.produce.tasks import char_window

    return char_window(kind, safe_profile())
