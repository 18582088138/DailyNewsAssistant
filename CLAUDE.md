# DailyNewsAssistant — 给 AI 协作者的工作说明

> 文档**按需加载**：默认只读这一份和 [docs/00_INDEX.md](docs/00_INDEX.md)。
> 需要哪一份，索引里写了「什么时候加载它」。不要一次性把 `docs/` 读进来。

## 环境

- Python：conda 环境 `ov_env_py312`（3.12）。bash 里直接调
  `/c/Users/test/miniforge3/envs/ov_env_py312/python.exe` ——
  **不要用 `conda run ... python -c`**（会吞掉 `-c` 参数）。
  PATH 上的 `python` 是 miniforge base 3.13，**不是**项目环境。
- 装包：`pip install -e .`（两个包根：`src/` 与仓库根）。
- 密钥只从 `.env` 读；任何终端输出里都要打码（用户会截屏）。

## 一条架构铁律

```
frontends → apps → narration/render → pipeline → sources/inbox/llm/tts/store → core
```

依赖严格单向，**禁止反向 import**；**前端零业务逻辑** —— CLI 与 GUI 必须调同一个后端函数。
新增一种发布形态 = 新增一个 `apps/*.py` + 模板，核心层零改动。

## 工作方式（六条，每条都能被脚本或 hook 检查）

1. **先给计划、验收标准、成本估算，再动手。** 验收标准说不清就先问，不要边做边猜。
2. **每个 feature / bug fix 必须给出验收手段**：一个会从红变绿的 pytest 节点，
   或一条带预期输出的命令。给不出来，说明需求还没定义清楚。
3. **报告完成前必须跑 `python tools/check.py`**（ruff + 全量测试 + `dna doctor`）。
   「我觉得没问题」不是验收，退出码才是。
4. **搜索交给子代理**：要翻很多文件才能回答的问题，派子代理去翻，只要结论。
   主上下文每多一个文件，之后每一轮都要为它重复付费。
5. **一个任务一个会话，做完 `/clear`。** 实测费用里约 58% 是「重读上下文」，
   而输出只占 13%：`成本 ≈ 上下文大小 × 轮数`。用 `python tools/usage_report.py --days 1` 自查。
6. **AI 不执行 `git commit` / `git push`**（hook 会拦）。命令写进
   `docs/git_commands.md`，由人工执行。那个文件是**一次性的**：
   覆写之前先 `git log` 核对上一组已经执行过。

## 成本纪律

- 先 `grep` 定位，再局部读；**同一个文件不读第二遍**。
- 改文件用 Edit，不要写补丁脚本（脚本改完，工具会把整个文件重新注入上下文）。
  只有「同一模式要改十处以上」才值得写脚本，且脚本里不要出现转义字符。
- 注释只写**为什么**，不写是什么。判据：删掉它，三个月后有人会不会把代码改错？
  不会就删。**只有公共 API / 跨层接口保留中英双语**，内部 helper、私有函数、测试一律中文。
- 单文件 **≤500 行**；散文（注释 + docstring）占非空行 **≤25%**。
  这两条由 `tools/check.py --max-lines 500 --prose-ratio 25` 把关。
- 文档按改动类型更新：bug 修复只写 `docs/issues/NNN`；新功能只动它自己那一份 guide；
  阶段收尾（用户点名 review 时）才动 `00_STAGE_SUMMARY` 与 `03_unit_tests`。
- **文档里不写会腐烂的数字**（测试数、行数、OK 数）。要数字就现算：
  `tools/check.py --list`。`tools/check_docs.py` 会拦住手抄的数字和断链。

## 花钱的地方

- **真实 LLM 调用要花钱。** 默认 `pytest` 是安全的（`live`/`slow` 已排除）；
  `pytest -m ''` 与 `-m live` **会真扣钱**，hook 会拦。
- 已有产物不加 `--force` 就复用，零调用 —— 这是 produce 层最重要的护栏。
- `longform` 不进 `--all`：一篇 5~9 次调用，是其余三种加起来的数倍。
- 本地 LLM（Ollama / OpenVINO）**已冻结**，不做功能开发也不做功能测试。
- TTS 全程本地、零费用，但很慢（口播整篇约 6 分钟，长文案约 37 分钟）。
- 用户手工调过的 `config/profile.yaml` 与 `config/prompts/` **不要回改**；
  配置回写只能按行 patch（`yaml.safe_dump` 会抹掉注释）。

## 自动反馈（`.claude/hooks/`）

| 时机 | 做什么 |
|---|---|
| 改完 `*.py` | `ruff check` 那一个文件（0.1s），失败立刻顶回来；并记下要跑哪个测试包 |
| 一轮结束 | 只跑这一轮改动涉及的测试包（2.7~15.5s），失败顶回来让你先修 |
| 执行 Bash 前 | 拦 `git commit`/`push`、`pytest -m ''`/`-m live`、`git add -A` |

换机器：把本机解释器路径写进 `.claude/hooks/python-path.txt`（不入库），别改 `settings.json`。
