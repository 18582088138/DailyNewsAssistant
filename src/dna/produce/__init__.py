"""
单篇产物层 / Per-article production layer.

    from dna.produce import produce, produce_all, ProductionKind

一篇文章能生成五种产物，每一种都可以单独重做：

| kind | 产物 | 文件 | 进 `--all` |
|---|---|---|---|
| `summary_zh` | 总结 | `summary.zh.md` | ✅ |
| `summary_en` | 英文总结 | `summary.en.md` | ✅（依赖中文总结） |
| `shortvideo` | 短视频文案 25~35s | `shortvideo.zh.md` | ✅ |
| `narration` | 口播文案 1~2min | `narration.zh.md` | ✅ |
| `longform` | 长文案 10~15min | `longform.zh.md` + `.json` | ❌ **须显式指定** |

CLI 与 GUI 都调 `service.produce()`，**业务逻辑只有一份**——
前端层不含逻辑是本项目的铁律，两个前端也因此不会各错一套。
Both front-ends call `service.produce()`. Keeping logic out of the front-end is a
standing rule here, and it also means the two cannot break differently.

费用可见 / Cost stays visible:
    已有产物且未 force 时**直接返回、不调 LLM**——GUI 里按钮就在手边，
    误触一次不该等于一次计费。长文案单篇 5~9 次调用，因此不进批量。
    An existing production is reused unless forced, because the buttons sit right there
    and a mis-click must not cost money. The long-form script runs five to nine calls per
    article and therefore never joins the batch.
"""

from dna.produce.service import (
    ProduceResult,
    is_new_article,
    produce,
    produce_all,
    read_production,
)
from dna.produce.tasks import (
    DISPLAY_ORDER,
    TASKS,
    ProductionKind,
    TaskSpec,
    batch_kinds,
    estimate_calls,
    spec,
)

__all__ = [
    "DISPLAY_ORDER",
    "TASKS",
    "ProduceResult",
    "ProductionKind",
    "TaskSpec",
    "is_new_article",
    "batch_kinds",
    "estimate_calls",
    "produce",
    "produce_all",
    "read_production",
    "spec",
]
