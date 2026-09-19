"""
长文案的数据模型 / The long-form script's models.

专题与访谈**产出同一个结构**（`mode` 不同、`speaker` 不同），所以下游的 TTS
照着 `speaker` 分配音色即可，不必分两条代码路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, Field

# 章节数量范围 / how many sections
MIN_SECTIONS = 4
MAX_SECTIONS = 8


class LongformMode(StrEnum):
    """长文案形式 / Long-form mode."""

    FEATURE = "feature"  # 专题：单角色
    INTERVIEW = "interview"  # 访谈：双角色


# 角色名 / speaker labels
SPEAKERS = {
    LongformMode.FEATURE: {"narrator": "旁白"},
    LongformMode.INTERVIEW: {"host": "主持人", "guest": "嘉宾"},
}


class SectionPlan(BaseModel):
    """提纲里的一节 / One section of the outline."""

    title: str = Field(max_length=40, description="小节标题，用于人工核对结构，不会被念出来")
    points: list[str] = Field(
        min_length=1, max_length=5, description="本节要讲的要点，每条一句话"
    )
    # 上限跟着 prompt_char_budget 走：15 分钟的混排密度预算约 6000 字，
    # 分 4 节就是每节 1500。上限压在 1200 会让模型的规划被 Schema 反复驳回，
    # 白花修复重试的调用。
    # The ceiling tracks `prompt_char_budget`: a fifteen-minute budget is around 6000
    # characters at mixed density, or 1500 across four sections. Capping at 1200 makes the
    # schema reject the model's own plan and burns repair retries.
    target_chars: int = Field(ge=150, le=2000, description="本节目标字数")


class OutlinePlan(BaseModel):
    """长文案提纲 / The long-form outline."""

    sections: list[SectionPlan] = Field(min_length=1, max_length=MAX_SECTIONS)


class Turn(BaseModel):
    """一次发言 / One speaker turn."""

    speaker: str = Field(description="feature 模式固定 narrator；interview 模式为 host 或 guest")
    text: str = Field(min_length=1)


class SectionScript(BaseModel):
    """一节展开后的稿子 / One expanded section."""

    turns: list[Turn] = Field(min_length=1)


@dataclass
class LongformResult:
    """一篇长文案的结果 / The result of one long-form script."""

    mode: LongformMode
    turns: list[Turn] = field(default_factory=list)
    sections: list[SectionPlan] = field(default_factory=list)
    calls: int = 0
    seconds: float = 0.0

    @property
    def text(self) -> str:
        """拼成人读的全文 / The whole script as a person reads it."""
        labels = SPEAKERS[self.mode]
        if self.mode is LongformMode.FEATURE:
            return "\n\n".join(t.text for t in self.turns)
        return "\n\n".join(
            f"**{labels.get(t.speaker, t.speaker)}：** {t.text}" for t in self.turns
        )

    @property
    def chars(self) -> int:
        return sum(len(t.text) for t in self.turns)

    def to_json_dict(self) -> dict:
        """
        导出给 TTS 的结构 / The structure the TTS stage consumes.

        两种模式产出同一种结构，只是 speaker 取值不同——TTS 侧照着 speaker
        分配音色即可，不必知道这篇是专题还是访谈。
        Both modes emit the same shape and differ only in the speaker values, so the TTS
        stage assigns voices without needing to know which mode produced the script.
        """
        return {
            "mode": str(self.mode),
            "speakers": SPEAKERS[self.mode],
            "est_seconds": self.seconds,
            "turns": [{"speaker": t.speaker, "text": t.text} for t in self.turns],
        }

