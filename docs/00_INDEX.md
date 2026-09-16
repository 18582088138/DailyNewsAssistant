# 00 文档导览 / Docs Index

> **按需加载。** 不要一次性把 `docs/` 读进来 —— 主上下文每多一份文档，
> 之后每一轮都要为它重复付费（成本 ≈ 上下文大小 × 轮数）。
> 下面「什么时候加载它」那一列，就是判据。

| 文档 | 什么时候加载它 |
|---|---|
| **入口看板** | |
| [00_STAGE_SUMMARY.md](00_STAGE_SUMMARY.md) | 新任务开始。确认当前批次/阶段、已冻结的方向、问题台账索引 |
| **落盘与数据契约**（动之前必读） | |
| [05_output_spec.md](05_output_spec.md) | 要写任何产物文件、改任何输出路径之前。**落盘唯一权威** |
| [07_db_schema.md](07_db_schema.md) | 要读写 SQLite 台账、改统计口径之前 |
| **架构地图** | |
| [04_architecture_src.md](04_architecture_src.md) | 改后端代码前找「这个函数在哪一层」「症状 → 文件」 |
| [04_architecture_frontends.md](04_architecture_frontends.md) | 改 CLI 或 NiceGUI 前，同上 |
| **提示词与费用** | |
| [06_prompt_spec.md](06_prompt_spec.md) | 改 LLM 节点的提示词、字数预算、时长窗口。**文案禁令以这份为唯一版本** |
| **命令操作指南**（只读与当前子命令对应的那一份） | |
| [10_sources_guide.md](10_sources_guide.md) | `dna probe` / 加信息源 / feed 找不到时的出路 |
| [11_article_store_guide.md](11_article_store_guide.md) | `dna list` `show` `add` `refetch` `sync` `delete` `stats` |
| [12_digest_guide.md](12_digest_guide.md) | `dna digest`：clean → dedup → score → summarize → translate → trend |
| [13_workbench_guide.md](13_workbench_guide.md) | NiceGUI 工作台：产出矩阵、逐格重做、批量、设置面板 |
| [14_tts_guide.md](14_tts_guide.md) | `dna tts` / 语音合成 / TTS 操作台 / 音色与参考音频 |
| **测试台账** | |
| [03_unit_tests.md](03_unit_tests.md) | 要跑测试、要新写测试。**清单不在这里，`tools/check.py --list` 现算** |
| **决策历史**（低频） | |
| [01_research.md](01_research.md) | 只在问「当初为什么选这个」时 |
| [02_development_plan.md](02_development_plan.md) | 同上。§9.2/§9.3 是已取消布局的唯一权威裁决 |
| **部署打包**（极低频） | |
| [08_feishu_bot_deployment.md](08_feishu_bot_deployment.md) | 真要部署飞书机器人那一刻。⚠️ **代码未实现**（P9），这是部署指南不是现状 |
| [09_packaging.md](09_packaging.md) | 真要打包分发那一刻 |
| **根因记录** | |
| `issues/001` ~ `issues/013` | 遇到似曾相识的报错，**按编号查一份，不要通读**。索引在 `00_STAGE_SUMMARY` §六 |
| **一次性** | |
| `git_commands.md` | 准备人工提交那一刻。覆写前先 `git log` 核对上一组已执行 |

## 不在 docs/ 里，但同样是权威

| 位置 | 内容 |
|---|---|
| [../CLAUDE.md](../CLAUDE.md) | 工作方式、架构铁律、成本纪律、花钱的地方。**与任何文档冲突时以它为准** |
| `config/profile.yaml` · `config/prompts/` | 用户手工调过，**文档不复述其中数值**；回写只按行 patch |
| `tools/check.py --list` | 测试清单与数量 |
| `dna doctor` | 环境实际状态（系统环境变量优先级高于 `.env`） |
