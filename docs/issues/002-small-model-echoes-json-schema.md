# 002 · 小模型把 JSON Schema 原样抄回来

| | |
|---|---|
| 发现阶段 | P1 LLM 抽象层，真机验证时 |
| 日期 | 2026-09-01 |
| 状态 | ✅ 已修复并真机复验 |
| 影响面 | 所有 `chat_json()` 调用——P3 摘要/打分/趋势、P6 视频脚本、P7 播客脚本 |

---

## 现象

用本地 Ollama 的 `qwen2.5:3b` 调用 `chat_json()`，三次修复重试全部失败：

```
ollama:qwen2.5:3b 返回的 JSON 不合法（第 1/3 次）：2 validation errors for Item
ollama:qwen2.5:3b 返回的 JSON 不合法（第 2/3 次）：2 validation errors for Item
ollama:qwen2.5:3b 返回的 JSON 不合法（第 3/3 次）：2 validation errors for Item
ProviderResponseError: ... 在 3 次尝试后仍未返回合法 JSON
```

Pydantic 的错误细节里露出了马脚：

```
summary
  Field required [type=missing,
   input_value={'properties': {'title': ...Item', 'type': 'object'}, input_type=dict]
```

`input_value` 是 **JSON Schema 本身**。模型收到 Schema 后，把 Schema 原样抄了回来，
而不是生成一条符合该 Schema 的数据。

## 复现

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe -u /c/tmp/dna_ollama_check.py qwen2.5:3b
```

（复现脚本见本文末尾附录；前置条件：Ollama 在跑，已拉取 `qwen2.5:3b`）

## 根因

两层问题叠加：

1. **提示词没把「Schema」和「数据」区分清楚**。原提示是「请严格按照下面的 JSON Schema
   输出一个 JSON 对象」——对大模型足够，对小模型有歧义，它会理解成「把这个 JSON 输出出来」。
2. **修复重试的反馈无效**。Pydantic 报的是「Field required」，这句话完全没有提示
   「你返回的是 Schema 而不是数据」。模型拿着这个错误，根本不知道该改什么，
   于是每一轮都重复同样的错误——三次重试等于浪费三倍时间。

第 2 点是更本质的问题：**修复重试的价值完全取决于反馈信息是否指向真正的错误**。

## 修复

`src/dna/llm/base.py` 三处改动：

1. **提示词区分 Schema 与数据**（`_json_instruction`）：
   - 「请按下面的 JSON Schema 生成**一条符合该结构的数据**」
   - Schema 用 `--- Schema（仅用于说明字段要求，不要照抄）---` 包裹
   - 明确列出「不要返回上面的 Schema 定义，不要包含 properties / type / required 这些元字段」
2. **显式识别 Schema 回声**（`_looks_like_json_schema`）：解析出的对象若包含
   `properties` / `$defs` / `$schema` / `definitions` 任一特征字段，判定为回声，
   替换成针对性的纠正提示（`_repair_hint`）：
   > 你返回的是 JSON Schema 定义本身（含 properties / $defs 等字段）。我需要的是
   > **符合该 Schema 的一条真实数据**，不是 Schema 定义。
3. **启用服务端 JSON 模式**：`chat_json()` 传 `json_mode=True`，
   `OpenAICompatProvider` 转成 `response_format={"type": "json_object"}`
   （DeepSeek / Ollama / vLLM 均支持）。若端点不认该参数，自动退回普通模式重试一次，
   不让整条流水线因此失败。

## 复验

修复后同一模型、同一脚本：

```
provider=ollama:qwen2.5:3b local=True
[1] text='二' completion_tokens=2 reasoning=no ms=2812
[2] json title='某公司发布多模态大模型，官方称推理成本下降四成' score=3.0
[3] no truncation error
```

**一次通过，无需重试。** 推理模型 `qwen3.5:9b` 同样通过。

## 回归测试

```bash
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/llm/test_provider.py -k "schema or json_mode" -v
```

- `test_schema_echo_is_detected_and_corrected` —— 首次抄回 Schema，第二次带纠正提示后成功
- `test_schema_markers_are_recognised[properties/$defs/$schema/definitions]` —— 四种特征字段
- `test_chat_json_requests_server_side_json_mode`
- `test_json_mode_falls_back_when_unsupported`
- `test_chat_json_prompt_forbids_echoing_schema`

## ⚠️ 附带观察：字段语义需要在 Schema 里写死

`qwen3.5:9b` 对同一条新闻给出的 `score` 是 **95.0**，`qwen2.5:3b` 给的是 **3.0** ——
因为测试用的 Schema 只声明了 `score: float`，没写取值范围，两个模型各自脑补了
0–100 与 0–10 两种量纲。

**对 P3 的约束**：所有打分类字段必须在 Pydantic 模型上写清 `ge` / `le` 约束和
`description`。这些信息会随 `model_json_schema()` 自动进入提示词，是零成本的对齐手段。

## 关联

- `docs/issues/001-ollama-reasoning-token-budget.md`（同一次真机验证中发现）
- `docs/02_development_plan.md` §8 LLM 兼容层

---

## 附录：复现脚本

```python
"""确认本地 Ollama 走通 chat 与 chat_json / local-provider check."""
import sys
from pydantic import BaseModel
from dna.llm.ollama_provider import OllamaProvider
from dna.llm.base import user

MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:3b"
p = OllamaProvider(model=MODEL, timeout=600)
print(f"provider={p.info} local={p.info.is_local}", flush=True)

r = p.chat([user("用一个词回答：1+1等于几？")], timeout=600)
print(f"[1] text={r.text[:60]!r} completion_tokens={r.usage.completion_tokens} "
      f"reasoning={'yes' if r.reasoning else 'no'} ms={r.duration_ms}", flush=True)

class Item(BaseModel):
    title: str
    summary: str
    score: float

obj = p.chat_json(
    [user("为这条新闻生成结构化摘要：某公司发布多模态大模型，官方称推理成本下降四成。")],
    Item, timeout=600)
print(f"[2] json title={obj.title[:40]!r} score={obj.score}", flush=True)

try:
    p.chat([user("详细解释相对论的全部内容")], max_tokens=16, timeout=600)
    print("[3] no truncation error", flush=True)
except Exception as e:
    print(f"[3] truncation error -> {str(e)[:200]}", flush=True)
```
