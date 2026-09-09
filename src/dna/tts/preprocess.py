"""
朗读友好化 / Making copy speakable —— 送进 TTS 之前的**一次** LLM 调用。

要解决的是「字面正确、念出来不对」这一类问题 / What it fixes:
    · 不易读的写法：`RTX 4060`、`o3-mini`、`3.5x`、`≈`、`P(A|B)` —— 模型会逐字符
      拼读或干脆跳过
    · 多音字：地名人名与专业词里的破音字（「模」型 / 「重」量级），换成**同音的
      另一个字**是最省事的解法（改音标要引擎支持，换字不需要）
    · 断句：稿子是按视觉排版写的，念起来该在哪停不一定跟标点一致
    · 自然度：适度的语气标点与 `[pause:400ms]` 停顿标记

为什么用一次 LLM 而不是堆规则 / Why one LLM call rather than rules:
    规则表必须穷举，而它永远穷举不完 —— 每来一个新缩写、新公式就得改代码，
    还会互相干扰（把 `4060` 改成「四零六零」之后，`4060 Ti` 又不对了）。
    一次调用解决一类问题，而且**可缓存**：同一段稿子重复合成不会再花钱。
    A rule table must enumerate what cannot be enumerated, and the rules interfere with
    each other. One call handles the class of problem, and it is cached.

这一步**属于本项目，不属于 TTS service**。那个服务是纯 TTS：给文本给参数、回音频，
它自己从不调用 LLM。而「什么写法念得顺」是文案层的判断，不是合成器该管的事 ——
所以准备工作放在交出去之前，由这边做完。
This step belongs here, not to the service: the service is pure TTS and never calls an
LLM, while judging what reads well aloud is a copy-level decision.

⚠️ **失败、超时、返回明显不对时一律退回原文。** 音频能不能出来，不该取决于一次
可选的润色。`TTS_PREPROCESS=false` 可以彻底关掉。
Failures fall back to the original text: whether audio exists must not depend on an
optional polish step.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from dna.core.config import Settings, get_settings
from dna.core.logging import get_logger
from dna.core.prompts import load_prompt
from dna.llm.base import LLMProvider, system, user
from dna.tts.segment import clean_for_speech

logger = get_logger("tts.preprocess")

# 改写后的长度允许偏离多少 / how far the rewrite may drift in length
#
# 朗读友好化是**改写法，不是改内容**：字数应该在原文附近浮动
# （展开一个公式会变长，删掉括注会变短）。超出这个区间说明模型
# 自己续写或大段删除了，那种结果宁可不要 —— 音频里多念一段
# 不存在的内容，比读音不完美严重得多。
# The rewrite changes spelling, not substance. Falling outside this band means the model
# continued the text or dropped a chunk, and invented content is far worse than an
# imperfect pronunciation.
_MIN_RATIO = 0.6
_MAX_RATIO = 1.8


class SpokenOut(BaseModel):
    """LLM 的返回结构 / The structured reply."""

    spoken: str = Field(description="改写后的朗读稿")
    notes: list[str] = Field(default_factory=list, description="改了什么，每条一句话")


@dataclass
class PreparedSpeech:
    """
    预处理的结果 / The outcome of preprocessing.

    `used_llm` 要分得清：退回原文和润色成功是两回事，前者意味着音质问题还在。
    Whether the LLM was actually used matters: a fallback means the reading problems
    are still there.
    """

    text: str
    used_llm: bool = False
    notes: list[str] = field(default_factory=list)
    reason: str = ""
    """没用上 LLM 的原因 / why the LLM result was not used（成功时为空）。"""


# 提示词正文在 `config/prompts/tts_preprocess.md` / The prompt text lives in that file.
PROMPT_NAME = "tts_preprocess"


def system_prompt() -> str:
    """朗读友好化的 system prompt / The read-aloud rewriter's system prompt。"""
    return load_prompt(PROMPT_NAME)


def prepare_for_speech(
    text: str,
    *,
    llm: LLMProvider | None = None,
    settings: Settings | None = None,
) -> PreparedSpeech:
    """
    把稿子改成朗读稿 / Turn copy into something worth reading aloud.

    两步，顺序不能反 / Two steps in this order:
        1. `clean_for_speech` —— 去掉 Markdown 与网址（**规则化的部分只留这一点**：
           它是格式清洗，不是语言判断，交给 LLM 既慢又不稳）
        2. 一次 LLM 调用做朗读友好化

    参数 / Args:
        llm: 注入 provider；不给则按 .env 构造（测试一律注入）

    **不抛异常。** 任何环节出问题都退回上一步的文本，并在 `reason` 里说明。
    """
    s = settings or get_settings()
    cleaned = clean_for_speech(text)
    if not cleaned:
        return PreparedSpeech(text="", reason="没有可朗读的内容")

    if not s.tts_preprocess:
        return PreparedSpeech(text=cleaned, reason="已关闭 TTS 预处理（TTS_PREPROCESS=false）")

    try:
        provider = llm or _default_llm()
        result = provider.chat_json(
            [system(system_prompt()), user(cleaned)],
            SpokenOut,
            temperature=0.2,
            # 输出与输入同量级，给两倍余量就够；不设上限时小模型会自己续写下去
            max_tokens=min(8000, max(600, len(cleaned) * 2)),
        )
    except Exception as exc:  # noqa: BLE001 - 预处理失败绝不能挡住音频
        logger.warning("TTS 预处理失败，用原文合成：%s", " ".join(str(exc).split())[:200])
        return PreparedSpeech(text=cleaned, reason=f"LLM 调用失败：{exc}"[:300])

    spoken = (result.spoken or "").strip()
    ratio = len(spoken) / len(cleaned)
    if not spoken or not _MIN_RATIO <= ratio <= _MAX_RATIO:
        logger.warning("TTS 预处理结果长度异常（%d → %d 字，%.2fx），退回原文",
                       len(cleaned), len(spoken), ratio)
        return PreparedSpeech(
            text=cleaned,
            reason=f"改写后 {len(spoken)} 字 / 原文 {len(cleaned)} 字，偏离过大，已退回原文",
        )

    logger.info("TTS 预处理：%d → %d 字，%d 条改动", len(cleaned), len(spoken),
                len(result.notes))
    return PreparedSpeech(text=spoken, used_llm=True, notes=list(result.notes))


def _default_llm() -> LLMProvider:
    """
    默认 provider / The default provider.

    **开缓存**：同一段稿子重做音频时，这一次调用就是免费的。
    预处理是纯函数式的改写，缓存命中不会带来任何"不该复用"的问题。
    Cached: redoing the audio for the same script costs nothing, and the rewrite is
    deterministic enough that reuse is always correct.
    """
    from dna.llm.factory import get_llm

    return get_llm(cache=True)


__all__ = ["PROMPT_NAME", "PreparedSpeech", "SpokenOut", "prepare_for_speech", "system_prompt"]
