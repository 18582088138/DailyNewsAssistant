# 03 单元测试说明 / Unit Test Reference

> **这份文档不列清单、不写数量。** 清单会腐烂，`tools/check.py --list` 现算。
> 这里只写三件不会腐烂的事：怎么跑、什么标记、**怎么写才钉得住**。
>
> 规矩：某模块测试不通过，不进入下一模块。测试文件**头部注释**写完整复测命令。

---

## 一、怎么跑

```bash
PY=/c/Users/test/miniforge3/envs/ov_env_py312/python.exe   # conda run 会吞 -c 参数
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant

$PY tools/check.py               # 验收门：ruff + 全量测试 + dna doctor（报告完成前必跑）
$PY tools/check.py --fast        # 只跑 git 改过的那几个包
$PY tools/check.py --list        # 测试清单与数量（现算，别手抄进文档）

$PY -m pytest                    # 默认：只跑离线快测，零费用
$PY -m pytest -v                 # 带用例名
$PY -m pytest --durations=10     # 找最慢的几个
$PY -m pytest --cov=dna          # 覆盖率（需 pytest-cov）
```

### 标记约定

| 标记 | 含义 | 默认 |
|---|---|---|
| 无标记 | 纯离线，不联网不落真实盘 | ✅ 跑 |
| `@pytest.mark.live` | 真实调用外部服务，**产生实际费用** | ⬜ 跳过 |
| `@pytest.mark.slow` | 耗时长（TTS 合成、端到端） | ⬜ 跳过 |

默认跳过规则写在 `pyproject.toml` 的 `addopts = "-q -m 'not live and not slow'"`。

> ⚠️ **这条闸只在本机生效。** `addopts` 是本仓库的 `pyproject.toml`，
> `.claude/hooks/guard_bash.py` 只拦 Claude 会话里的命令。在别的终端手工跑
> `pytest -m ''` 不受任何约束，**项目没有 CI 兜底**——提交前跑 `tools/check.py` 是唯一的闸。

### 💰 LLM 测试纪律

真实 LLM 调用**要花钱**，所以：

1. **日常开发一律用假响应**：`tests/llm/fakes.py` 的 `FakeOpenAIClient`（冒充 SDK）
   与 `ScriptedProvider`（冒充 provider）。涉及 LLM 的单测断言的应该是
   **提示词构造是否正确**与**返回解析是否正确**，而不是去问真实模型
2. `-m live` 只在阶段验收时手动跑一次，不放进日常循环
3. `pytest`（默认）不产生费用；**`pytest -m ""` 会把 live 跑起来**
4. **本地 LLM 测试已冻结**：接口保留、测试冻结，理由见
   [issues/001](issues/001-ollama-reasoning-token-budget.md)

---

## 二、怎么写才钉得住

下面每一条都是从一次真实的「测试全绿但功能是坏的」里换来的。

### 1. 断言**意图**，不要断言结果

重做功能曾经调了 LLM 却拿回字节相同的旧答案（缓存键是提示词，重做既没改提示词也没绕开缓存）。
断言「两次结果不同」测不出任何东西——假 provider 本来每次就给不同答案。要钉的是意图：

```python
assert seen == [True, False], "首次生成走缓存，重做必须绕开"   # 传给 get_llm 的 cache 参数
```

### 2. 边界值只能证明边界挪了，证明不了边界没了

NEW 标识曾经过夜消失（24 小时时间窗压错了维度）。修法是判定里**不再有时间成分**，
于是测试把入库时间推到**一年前**再断言标识还在。写「25 小时前」只能证明窗口变长。

### 3. 静默失效要正面写一条反向测试

这类 bug 的共同点是**不报错**：文件生成了、时长也有，只有人去看才发现。
典型的三个：口播稿把网址逐字念出来、字数预算系统性少要三分之一、
`.env` 缺失时 doctor 不阻塞。都要有一条「它坏的时候必须红」的测试，
而不是只测「它好的时候是绿的」。

### 4. 花钱的路径要有「不会替人花钱」的测试

- 下游重做免费、上游重做要钱 → 断言前置一律 `force=False`（缺了才补）
- 空的额外指令**不能改变提示词一个字节**，否则每篇都错开缓存白花钱
- 音频不进 `--all`；`longform` 不进 `--all`
- 已有产物不加 `--force` 就复用，**零调用**

### 5. 部分失败不能毁整批

长文案几十段，跑到第 40 段崩掉不能把前 39 段的半小时一起赔进去。
缺段照样落盘，但 `complete=False` 且报出缺了几段——
**残缺的产物被当成成品发出去才是最坏的结果**。

### 6. 干净检出验证（只有它能发现的一类问题）

`.env` 按约定不入库，克隆里没有它。`run_all` 曾把路径写死，测试读的是
**开发机上那个未跟踪的文件**——于是「在我机器上过、在干净克隆里挂」，
而挂的原因和被测代码毫无关系。工作区里文件都在，本地永远全绿。

```bash
git clone -q --no-hardlinks . /c/tmp/verify
PYTHONPATH=/c/tmp/verify/src:/c/tmp/verify $PY -m pytest -p no:cacheprovider
rm -rf /c/tmp/verify
```

### 7. 护栏自己也要有测试

`.claude/hooks/` 是整套验收的地基，它静默失效的时候没有任何人会发现。
`tests/hooks/` 把每一条已知的绕过写成一个用例（`git -c x=y commit`、`git -C . add -A`、
测试失败后二次 Stop 不得放行）。改 hook 必须先让对应用例变红。

---

## 三、测试布局

```
tests/core  tests/llm  tests/sources  tests/extract  tests/pipeline
tests/narration  tests/store  tests/produce  tests/tts
tests/frontends  tests/hooks
```

与 `src/dna/` 的分层一一对应；前端测试在 `tests/frontends/`，护栏测试在 `tests/hooks/`。
具体有哪些文件、各多少用例：`$PY tools/check.py --list`。
