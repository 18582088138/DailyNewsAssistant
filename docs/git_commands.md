# 待执行的提交命令

> **这个文件是一次性的。** 每次覆写，只留当前这一组；上一组的内容在 `git log` 里。
>
> **覆写前必须先 `git log` 核对上一组是否已执行。** 本次核对结果（2026-10-01）：
> 上一组「批次 1~5」已随 PR #1 合入 `main`（`7b80aeb`）；随后的 pyproject 修复
> 也已提交（`1916bb4`）。Agent_TTS_Module 侧的两个 MiniMax 提交
> （`102d430`、`19316b1`）同样已执行，故安全覆写。
>
> `git add` 一律写明路径，**不用 `-A`**：`.env` 有密钥、`outputs/` 有几百 MB 产物。
> hook 会拦下 `git commit` / `git push` —— 这些命令由人工执行。
>
> ⚠️ **不要提交** `.claude/optimization_brief.md`。

分支：`main` · 本组内容：**MiniMax 克隆音色在新机器上可复现**

## ① DailyNewsAssistant：模板改成「MiniMax 已克隆音色当内置音色」

```bash
cd c:/Users/test/Downloads/xkd/DailyNewsAssistant
git add .env.example docs/git_commands.md
git commit -m "chore(env): 模板默认走 MiniMax 已克隆音色（custom_voice + voice_id）

- TTS_MODE=custom_voice，TTS_VOICE_HOST=va0d7850d_790636，TTS_VOICE_GUEST=male-qn-jingying
- Token Plan key 不能新克隆（2061），只能用账号里已有的克隆音色；
  voice_id 绑定账号而非机器，新机器同一个 key 即可复现，不依赖本地克隆缓存
- TTS_REF_AUDIO 指向 ref_audio_zh.mp3（已入库，va0d7850d_* 的来源音频）"
git push origin main
```

## ② Agent_TTS_Module：无需操作

`102d430`、`19316b1` 已在 `origin/main`（2026-10-01 核对 `git status -sb` 无 ahead）。

## 新机器上的步骤（不入库的部分）

```bash
git clone https://github.com/18582088138/DailyNewsAssistant.git
git clone https://github.com/18582088138/Agentic_TTS_Module.git Agent_TTS_Module
cp DailyNewsAssistant/.env.example DailyNewsAssistant/.env     # 填 LLM 等密钥、TTS_MODULE_DIR
cp Agent_TTS_Module/.env.example   Agent_TTS_Module/.env       # 填 TTS_API_KEY（同一个 MiniMax key）
```

密钥只靠人工拷贝，**永远不入库**。`voices/api_voice_cache.json` 不需要拷 ——
custom_voice 路径不读它。
