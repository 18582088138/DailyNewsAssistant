# 001 · 推理模型的 token 预算被思维链耗尽

| | |
|---|---|
| 发现阶段 | P1 LLM 抽象层，真机验证时 |
| 日期 | 2026-09-01 |
| 状态 | ✅ 已修复（报错可指到根因）+ ⚠️ 遗留性能结论 |
| 影响面 | P3 摘要、P6 视频口播稿、P7 播客脚本——所有设 `max_tokens` 的调用 |

---

## 现象

用本地 Ollama 的 `qwen3.5:9b` 调用 `chat(..., max_tokens=64)`，报错：

```
ollama:qwen3.5:9b 去掉思维链后内容为空 / empty after stripping <think>
```

这个报错把排查方向完全带偏了——看起来像是 `<think>` 剥离逻辑写错了，实际不是。

## 复现

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe - <<'EOF'
import httpx
c = httpx.Client(trust_env=False, timeout=240)
r = c.post("http://localhost:11434/v1/chat/completions", json={
    "model": "qwen3.5:9b",
    "messages": [{"role": "user", "content": "用一个词回答：1+1等于几？"}],
    "max_tokens": 512, "temperature": 0.3})
d = r.json()
print("finish:", d["choices"][0]["finish_reason"])
print("keys:", list(d["choices"][0]["message"].keys()))
print("content:", repr(d["choices"][0]["message"]["content"]))
print("usage:", d["usage"])
EOF
```

前置条件：Ollama 服务在跑（`ollama serve`），已拉取 `qwen3.5:9b`。

## 实测结果

| max_tokens | finish_reason | content | completion_tokens |
|---|---|---|---|
| 64 | `length` | `''` | 64 |
| 512 | `length` | `''` | 512 |
| 2000（不限） | `stop` | `'二'` | **900** |

进一步用抽象层实测（`c:/tmp/dna_ollama_check.py`）：回答「二」一个字，
**耗掉 2244 个 completion tokens、314 秒**。

## 根因

两点，都和最初的判断不同：

1. **Ollama 的 OpenAI 兼容端点把思维链放在独立的 `reasoning` 字段**，不是内联的
   `<think>…</think>`。message 的结构是 `{role, content, reasoning}`。
   所以 `strip_think_tags` 根本没参与——`content` 本来就是空的。
2. **推理消耗的是 completion token 预算**。`max_tokens` 卡住的是「推理 + 回答」的总量，
   模型还没推理完就被截断，于是 `content` 为空、`finish_reason` 为 `length`。

原来的报错只看到「文本为空」，没有读 `finish_reason`，也没有读 `reasoning`，
因此给出的信息完全无法定位问题。

## 修复

`src/dna/llm/openai_compat.py`：

1. `_extract_text()` 同时读取 `reasoning` / `reasoning_content` 与 `finish_reason`，
   思维链存入 `ChatResult.reasoning`（与正文分开，业务逻辑不读它）
2. 新增 `_empty_content_error()`，按情况给出可执行的报错：
   - `finish_reason == "length"` → 指明被 `max_tokens` 截断、报出已消耗的 token 数；
     若存在 `reasoning`，额外说明「预算被思维链吃光，请调大 max_tokens 或换非推理模型」
   - 只有 `reasoning` 没有 `content` → 说明模型只输出了思维链
3. **截断错误标记为 `retryable = False`**——用同样的 `max_tokens` 重试必然再次截断，
   重试只是把失败时间拉长三倍

修复后的报错：

```
ollama:qwen3.5:9b 正文为空且响应被 max_tokens 截断（finish_reason=length，已用 16
completion tokens）。该模型是推理模型，token 预算被思维链耗尽——请调大 max_tokens，
或改用非推理模型。
```

## 回归测试

```bash
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/llm/test_provider.py -k "reasoning or truncat" -v
```

- `test_reasoning_field_is_captured_separately`
- `test_truncated_by_max_tokens_gives_actionable_error`
- `test_truncation_error_is_not_retryable`
- `test_truncation_without_reasoning_still_explains`

## ⚠️ 遗留结论：影响 Local LLM 迁移路线

`qwen3.5:9b` 回答一个字要 **2244 tokens / 314 秒**。按每期日报 15 条、每条至少一次
摘要调用估算，纯摘要环节就要 **1~2 小时**，还不含翻译与趋势提炼。

对后续阶段的约束：

1. **P3 起，本地模型不要设小的 `max_tokens`**；需要控制长度就在提示词里约束句数，
   而不是靠截断
2. **迁移到 Local LLM 时优先选非推理模型**，或使用免思考变体
   （本机已有 `gurubot/Qwen3.5-35B-A3B-GGUF-unsloth-nothink`）
3. 云端 DeepSeek 作为默认的选择因此更加合理：P3–P7 的功能验证走云端，
   Local 迁移放到 P10 并单独做性能评估
4. 日报是每天跑一次的离线批处理，对延迟不敏感，所以本地方案并非不可行，
   但需要在 P10 实测整期耗时后再决定是否作为默认

## 关联

- `docs/issues/002-small-model-echoes-json-schema.md`（同一次真机验证中发现）
- `docs/02_development_plan.md` §8 LLM 兼容层
