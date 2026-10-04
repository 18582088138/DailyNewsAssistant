# DailyNewsAssistant — 给 AI 协作者的工作说明

> 文档**按需加载**：默认只读这一份和 [docs/00_INDEX.md](docs/00_INDEX.md)。
> 需要哪一份，索引里写了「什么时候加载它」。不要一次性把 `docs/` 读进来。

## 环境

- Python：conda 环境 `ov_env_py312`（3.12）。本机解释器
  `C:\Users\75203\miniforge3\envs\ov_env_py312\python.exe`
  （git-bash 里写作 `/c/Users/75203/miniforge3/envs/ov_env_py312/python.exe`）。
  **换机器要改这一行，并把同一个路径写进 `.claude/hooks/python-path.txt`**（那条不入库，
  见 `.gitignore`）——
  **不要用 `conda run ... python -c`**（会吞掉 `-c` 参数）。
  PATH 上的 `python` 是 miniforge base 3.13，**不是**项目环境。
- `tools/check.py` 的输出带 ✔/✘：**本机控制台是 GBK，必须先 `PYTHONIOENCODING=utf-8`**，
  否则它在打印结果时自己崩掉（`UnicodeEncodeError`），看起来像检查失败。
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
3. **平时每批只跑相关的测试包，整个任务收尾时跑一次 `python tools/check.py`**
   （ruff + 全量测试 + `dna doctor`）。不做真机全功能测试。
   「我觉得没问题」不是验收，退出码才是。
4. **搜索交给子代理**：要翻很多文件才能回答的问题，派子代理去翻，只要结论。
   主上下文每多一个文件，之后每一轮都要为它重复付费。
5. **一个任务一个会话，做完 `/clear`。** 实测费用里约 58% 是「重读上下文」，
   而输出只占 13%：`成本 ≈ 上下文大小 × 轮数`。用 `python tools/usage_report.py --days 1` 自查。
6. **AI 不执行 `git commit` / `git push`。** 命令写进
   `docs/git_commands.md`，由人工执行。那个文件是**一次性的**：
   覆写之前先 `git log` 核对上一组已经执行过。
   （2026-10-04 起这条**有机制兜底**：DSH 护栏插件会拒绝这类调用，见文末。）
7. **计划必须落盘，并在仓库里留指针。** 长任务的进度写进
   `docs/00_STAGE_SUMMARY.md` §〇，计划正文的路径也写在那里。
   会话一 `/clear`，只活在上下文里的计划就没了 —— 实测重新考古花掉十几轮。
   新会话接手长任务：先读 `00_STAGE_SUMMARY` §〇，别猜。
   **计划正文优先写进仓库**（`docs/plans/<分支>.md`）：放 `~/.claude/plans/` 的
   已经丢过两份，指针变成断链。
8. **没有 CI 兜底。** `addopts` 只在本机 `pyproject.toml` 生效，
   护栏只拦**AI 的工具调用**；人工在别的终端跑什么都不受约束 ——
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
  `pytest -m ''` 与 `-m live` **会真扣钱**（DSH 护栏会拦，见文末）。
- 已有产物不加 `--force` 就复用，零调用 —— 这是 produce 层最重要的护栏。
- `longform` 不进 `--all`：一篇 5~9 次调用，是其余三种加起来的数倍。
- 本地 LLM（Ollama / OpenVINO）**已冻结**，不做功能开发也不做功能测试。
- TTS 全程本地、零费用，但**很慢**（长文案是小时量级）。代码里的 `RTF_ESTIMATE`
  是旧硬件上量的，CPU 实测差约五倍 —— 别拿它给用户报预估。
- 用户手工调过的 `config/profile.yaml` 与 `config/prompts/` **不要回改**；
  配置回写只能按行 patch（`yaml.safe_dump` 会抹掉注释）。

## 自动反馈与护栏（现状：DSH 插件，**不是** Claude Code hook）

**`.claude/hooks/` 那三个脚本在 DSH 下不生效。** `.claude/settings.json` 用的是 Claude Code
的 hook 协议，DSH 不读它。2026-10-04 实测：一轮改了 80+ 个文件，`ruff` 自动检查与
「该跑哪个测试包」的提示**一次都没出现过**（当时 ruff 甚至没装）。脚本留在仓库里备查。

护栏换成了 DSH 原生插件 **`dsh-plugin-dna-guard`**（装在 DSH profile 里：
`~/.dsh/profiles/desktop/plugins/`，**不随仓库走**；含 README 与离线 smoke 测试）。
它挂 `ctx.tools.guard`——`ToolGuard = (exec) => string | undefined`，返回字符串即拒绝：

| 拦什么 | 例子 |
|---|---|
| 写 git 历史 | `git commit`、`git push`（含 `git -C <path> commit`、`cd x && git commit`） |
| 一锅端暂存 | `git add -A` / `--all` / `.` |
| 会真花钱的测试 | `pytest -m live` / `-m slow` / `-m ''`（含 `pwsh -c "…"` 里被引号包住的） |

拒绝原因会作为**工具结果**回给模型，里面写清该怎么做（例如「写进 `docs/git_commands.md`」），
所以被拦时改道而不是重试。改插件 config 保存即 HMR 重载；关掉它把 `cordis.patch.yml`
里那条的 `enabled` 改成 `false`。

**仍然没有任何机制兜底、只能靠自觉的**：
- 改完 `*.py` 跑 `ruff check <file>`；每批跑相关测试包；收尾跑 `python tools/check.py`
- 人工在别的终端提交、或跑 `-m live` —— 护栏只拦 **AI 的工具调用**

换机器：把本机解释器路径写进 `.claude/hooks/python-path.txt`（不入库，`.gitignore` 已忽略），
并改上面环境节那一行；护栏插件需要另外拷过去。
