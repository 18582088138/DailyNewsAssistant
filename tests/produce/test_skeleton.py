"""
test_skeleton.py —— 手写稿骨架单元测试 / Hand-written script skeleton tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/produce/test_skeleton.py -v

对应的人工验证 / Matching manual check:
    dna gui → 新建一篇文章 → 展开「中视频」格
    面板里应出现一份排好版的空稿；双击它填一句自己的话，点别处保存
    那一格随即变成「已生成」（●），全程零 LLM 调用

覆盖 / Covers:
    1. 朗读类骨架带**落盘标记行**，`spoken_text` 能读回正文（否则手写稿会合成出空音频）
    2. 总结骨架**不带**标记行（总结不会被念出来，凭空补一行会让 TTS 去念它）
    3. 音频与长视频**没有骨架**（二进制 / 发言人分轮存在 JSON 里）
    4. 抬头里的产物名取自 `tasks.TASKS[].label`，改名不会两边不一致
    5. 骨架保存之后真的成为这一格的稿子，且字数与时长按真实正文重算
    6. 标记行里的秒数与字数落在配置窗口内（不是瞎编的数字）

为什么这条链路要单独守着 / Why this deserves its own test file:
    骨架是新写的第二处「产物排版」写入点。它与生成路径**排版不一致**的后果很安静：
    文件看起来正常、格子显示已生成，只有合成音频时才发现念出来是空的。
    The skeleton is a second writer of the artefact layout, and a mismatch with the
    generated path fails silently: the file looks fine and the cell says "generated", and
    only audio synthesis reveals that nothing was spoken.

预期 / Expected:
    耗时 < 2s；不联网、不调 LLM（保存走的是人工编辑路径，`calls=0`）
"""

from __future__ import annotations

from datetime import datetime

import pytest

from dna.core.config import Settings, safe_profile
from dna.core.models import Article, RawItem, SourceKind
from dna.narration.duration import count_units
from dna.produce import ProductionKind as K
from dna.produce.documents import SPOKEN_MARKER, spoken_text, strip_front_matter
from dna.produce.editing import save_production_text
from dna.produce.skeleton import PLACEHOLDER_SCRIPT, skeleton_for
from dna.produce.tasks import char_window, spec
from dna.store.ledger import Ledger

ARTICLE = Article(url="custom://abc", title="我自己写的文章")

# 骨架该有的与不该有的 / kinds that get a skeleton, and those that do not
WITH_SKELETON = (K.SUMMARY, K.SHORTVIDEO, K.NARRATION)
WITHOUT_SKELETON = (K.LONGFORM, K.SHORTVIDEO_AUDIO, K.NARRATION_AUDIO, K.LONGFORM_AUDIO)


def _marker_chars(text: str) -> int:
    """读出标记行里的估算字数 / Read the estimated character count out of the marker line."""
    line = next(ln for ln in text.splitlines() if ln.startswith(SPOKEN_MARKER))
    return int(line.split("· ")[1].split(" 字")[0])


def seed(settings: Settings) -> str:
    """放一篇可用的文章进台账 / Seed one usable article into the ledger."""
    ledger = Ledger(settings.db_file)
    article_id, _ = ledger.register(
        RawItem(
            source_id="custom_article",
            via=SourceKind.GUI,
            url="custom://abc",
            title="我自己写的文章",
        )
    )
    article = Article(url="custom://abc", title="我自己写的文章", text="我自己写的正文。")
    directory = settings.output_path / "articles" / "20261004" / f"custom__{article_id[:8]}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "meta.json").write_text(
        article.model_dump_json(indent=2), encoding="utf-8"
    )
    ledger.record_fetch(
        article_id,
        article,
        store_dir=directory.relative_to(settings.output_path).as_posix(),
        now=datetime(2026, 10, 4, 9, 0, 0),
    )
    return article_id


# ---------------------------------------------------------------------------
# 1-3. 谁有骨架、骨架长什么样 / who gets one, and what it looks like
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", WITH_SKELETON)
def test_skeleton_exists_and_is_saveable(kind: K) -> None:
    """这三种产物都要有内容非空的骨架 / These three kinds all get a non-empty skeleton."""
    text = skeleton_for(ARTICLE, kind, "zh", profile=safe_profile())
    assert text.strip()
    assert text.startswith("# 我自己写的文章")


@pytest.mark.parametrize("kind", WITHOUT_SKELETON)
def test_no_skeleton_for_binary_or_json_kinds(kind: K) -> None:
    """
    音频与长视频没有骨架 / Audio and the long-form script get none.

    音频是二进制的；长视频的稿子按发言人分轮存在 `.json` 附件里，一段纯文本写不回去
    （见 `produce/editing.py`）。给它们一个骨架只会让人以为改得动。
    """
    assert skeleton_for(ARTICLE, kind, "zh", profile=safe_profile()) == ""


@pytest.mark.parametrize("kind", (K.SHORTVIDEO, K.NARRATION))
def test_spoken_skeletons_carry_the_marker(kind: K) -> None:
    """
    朗读类骨架必须带落盘标记行，且 `spoken_text` 能读回正文。

    少了这一行，手写的稿子合成出来是**空音频**——而文件本身看起来完全正常。
    """
    text = skeleton_for(ARTICLE, kind, "zh", profile=safe_profile())

    assert SPOKEN_MARKER in text
    spoken = spoken_text(text)
    assert spoken.strip(), "骨架的标记行之后必须有正文，否则合成出来是空的"
    assert "（在这里写正文" in spoken


def test_summary_skeleton_has_no_marker() -> None:
    """
    总结**不该**有标记行 / The summary skeleton carries no marker.

    总结不会被念出来；凭空给它补一行标记，TTS 会把这行之后的文本当成要朗读的正文。
    """
    text = skeleton_for(ARTICLE, K.SUMMARY, "zh", profile=safe_profile())
    assert SPOKEN_MARKER not in text


# ---------------------------------------------------------------------------
# 4. 抬头 / the header
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", WITH_SKELETON)
def test_header_uses_the_task_label(kind: K) -> None:
    """
    抬头里的产物名取自 `tasks.TASKS[].label`，与界面表头同源。

    照抄字面量的话，标签改名之后骨架会继续写着旧名字，而表格已经是新名字了。
    """
    text = skeleton_for(ARTICLE, kind, "zh", profile=safe_profile())
    assert f"> {spec(kind).label}（zh）" in text


def test_custom_article_source_is_readable() -> None:
    """人工创作的文章没有原文地址，抬头显示来源名而不是伪 URL。"""
    text = skeleton_for(ARTICLE, K.SUMMARY, "zh", profile=safe_profile())
    assert "custom_article" in text
    assert "custom://abc" not in text


def test_skeleton_is_language_specific() -> None:
    """中英两版的抬头不同 / The two languages get different headers."""
    zh = skeleton_for(ARTICLE, K.SUMMARY, "zh", profile=safe_profile())
    en = skeleton_for(ARTICLE, K.SUMMARY, "en", profile=safe_profile())
    assert "（zh）" in zh and "（en）" in en


# ---------------------------------------------------------------------------
# 5. 保存之后真的成为这一格的稿子 / saving really does create the production
# ---------------------------------------------------------------------------


def test_saved_skeleton_becomes_the_cell_text(settings: Settings) -> None:
    """
    保存骨架之后这一格就有稿子了，且字数按**真实正文**重算。

    标记行里的秒数与字数是估算值（骨架没法替用户算），保存时必须用真内容覆盖，
    否则格子上会一直显示一个凭空来的字数。
    """
    article_id = seed(settings)
    skeleton = skeleton_for(ARTICLE, K.NARRATION, "zh", profile=safe_profile())
    estimated = _marker_chars(skeleton)

    save_production_text(article_id, K.NARRATION, skeleton, settings=settings)

    record = Ledger(settings.db_file).latest_production(article_id, str(K.NARRATION))
    assert record is not None and record.ok
    assert record.calls == 0, "手写保存不该记成一次 LLM 调用"
    assert record.chars != estimated, "必须按真实正文重算，而不是沿用骨架里的估算"
    assert record.chars == count_units(PLACEHOLDER_SCRIPT, lang="zh")


def test_saved_skeleton_can_be_read_back(settings: Settings) -> None:
    """落盘之后 `spoken_text` 读回的还是正文 / The body stays readable after saving."""
    from dna.produce.generate import read_production

    article_id = seed(settings)
    skeleton = skeleton_for(ARTICLE, K.NARRATION, "zh", profile=safe_profile())
    save_production_text(article_id, K.NARRATION, skeleton, settings=settings)

    document = read_production(article_id, K.NARRATION, lang="zh", settings=settings)
    assert SPOKEN_MARKER in document
    assert "（在这里写正文" in spoken_text(document)
    assert strip_front_matter(document).strip()


# ---------------------------------------------------------------------------
# 6. 标记行里的数字 / the numbers inside the marker line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "duration_key"),
    ((K.SHORTVIDEO, "video_duration_seconds"), (K.NARRATION, "narration_duration_seconds")),
)
def test_marker_numbers_sit_inside_the_configured_window(
    kind: K, duration_key: str
) -> None:
    """
    标记行里的秒数与字数取配置窗口的中点，不是瞎编的。

    数字看着离谱的话，用户会以为程序算错了；而它其实只是占位。
    """
    profile = safe_profile()
    text = skeleton_for(ARTICLE, kind, "zh", profile=profile)

    marker_line = next(line for line in text.splitlines() if line.startswith(SPOKEN_MARKER))
    seconds = float(marker_line.split("约 ")[1].split(" 秒")[0])
    chars = int(marker_line.split("· ")[1].split(" 字")[0])

    low, high = getattr(profile, duration_key)
    assert low <= seconds <= high
    window = char_window(kind, profile)
    assert window is not None
    assert window[0] <= chars <= window[1]
