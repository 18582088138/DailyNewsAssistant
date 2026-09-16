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
frontends → produce → pipeline → narration → store → extract → sources → llm · tts → core
```

依赖严格单向，**禁止反向 import**；**前端零业务逻辑** —— CLI 与 GUI 必须调同一个后端函数。
新增一种发布形态 = 在 `produce/` 加一种 kind + 模板，核心层零改动。

已知的唯一违规：`core/doctor.py` 反向 import `dna.tts`（批次 2 修）。

## 工作方式（每条都能被脚本或 hook 检查）

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
7. **计划必须落盘，并在仓库里留指针。** 长任务的进度写进
   `docs/00_STAGE_SUMMARY.md` §〇，计划正文的路径也写在那里。
   会话一 `/clear`，只活在上下文里的计划就没了 —— 实测重新考古花掉十几轮。
   新会话接手长任务：先读 `00_STAGE_SUMMARY` §〇，别猜。
8. **没有 CI 兜底。** `addopts` 只在本机 `pyproject.toml` 生效，
   hook 只拦 Claude 会话里的命令。人工在别的终端跑什么都不受约束 ——
   `tools/check.py` 是唯一的闸。

## 配置铁律（置信度 env > config > docs > source code）

- **可调参数一律从 `Settings`（`.env`）或 `Profile`（`config/profile.yaml`）读，
  不许写死在代码里。** 判据：这个值改一下会不会影响产物质量、费用或等待时间，
  而用户现在无法在不改代码的情况下改它。
- **新增配置字段必须同时接上真实消费者。** 半接的旋钮就是「假配置」——
  用户改了、界面显示保存成功、行为一点没变，而且完全静默。
  这一类历史上有 18 个，见 [docs/issues/014](docs/issues/014-hardcoded-config-and-fake-settings.md)。
- 模块确实需要「不传参时用什么」的兜底常量时（让纯函数能离线单测），
  用 `core.config.field_default(Profile, "字段名")` **指向模型**，不要再写一遍数字。
- 两条静态测试长期把关，改这些之前先看它们会不会红：
  `tests/core/test_config_effective.py`（每个字段都有人读 · 配置真的生效）、
  `tests/core/test_layering.py`（分层单向，连函数内 import 一起查）。
- 进阶旋钮（选题权重、判重阈值、长文案章节数）放 `Profile.tuning`，
  **不进设置面板**——面板是日常设置，不是调参台。

## 成本纪律

- 先 `grep` 定位，再局部读；**同一个文件不读第二遍**。
- 改文件用 Edit，不要写补丁脚本（脚本改完，工具会把整个文件重新注入上下文）。
  只有「同一模式要改十处以上」才值得写脚本，且脚本里不要出现转义字符。
- 注释只写**为什么**，不写是什么。判据：删掉它，三个月后有人会不会把代码改错？
  不会就删。**只有公共 API / 跨层接口保留中英双语**，内部 helper、私有函数、测试一律中文。
- 单文件 **≤500 行**，由 `tools/check.py --max-lines 500` 把关（硬门）。
- 散文（注释 + docstring）占非空行的参考线是 **40%**，`check.py` 每次都报，但
  **不拦**。它约束的是新写的代码，**不要为了压这个数字回头删已经写下的「为什么」**
  —— 这个项目最贵的 bug 全是「不报错，只是不对」，那些根因记录就是防复发的东西。
- 文档按改动类型更新：bug 修复只写 `docs/issues/NNN`；新功能只动它自己那一份 guide；
  阶段收尾（用户点名 review 时）才动 `00_STAGE_SUMMARY` 与 `03_unit_tests`。
- **文档里不写会腐烂的数字**（测试数、行数、OK 数）。要数字就现算：
  `tools/check.py --list`。`tools/check_docs.py` 会拦住手抄的数字和断链。
- **一个概念只在一处写权威**：提示词规格在 `06`、落盘在 `05`、库表在 `07`、
  前端实现在 `04`；操作指南（`10~14`）只写「界面上能做什么」，其余写一行指针。
  实测同一段内容曾被抄进 3~4 份文档，改一处等于留下三份矛盾的旧版本。

## 花钱的地方

- **真实 LLM 调用要花钱。** 默认 `pytest` 是安全的（`live`/`slow` 已排除）；
  `pytest -m ''` 与 `-m live` **会真扣钱**，hook 会拦。
- 已有产物不加 `--force` 就复用，零调用 —— 这是 produce 层最重要的护栏。
- `longform` 不进 `--all`：一篇 5~9 次调用，是其余三种加起来的数倍。
- 本地 LLM（Ollama / OpenVINO）**已冻结**，不做功能开发也不做功能测试。
- TTS 全程本地、零费用，但**很慢**（长文案是小时量级）。代码里的 `RTF_ESTIMATE`
  是旧硬件上量的，CPU 实测差约五倍 —— 别拿它给用户报预估。
- 用户手工调过的 `config/profile.yaml` 与 `config/prompts/` **不要回改**；
  配置回写只能按行 patch（`yaml.safe_dump` 会抹掉注释）。

## 自动反馈（`.claude/hooks/`）

| 时机 | 做什么 |
|---|---|
| 改完 `*.py` | `ruff check` 那一个文件（0.1s），失败立刻顶回来；并记下要跑哪个测试包 |
| 一轮结束 | 只跑这一轮改动涉及的测试包（2.7~15.5s），失败顶回来让你先修 |
| 执行 Bash 前 | 拦 `git commit`/`push`、`pytest -m ''`/`-m live`、`git add -A` |

换机器：把本机解释器路径写进 `.claude/hooks/python-path.txt`（不入库），别改 `settings.json`。
