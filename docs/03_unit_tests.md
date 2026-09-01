# 03 单元测试说明 / Unit Test Reference

> 每个基础功能都必须配单元测试；测试文件**头部注释**写完整复测命令与说明，本文件做汇总索引。
> 规矩：**某模块测试不通过，不进入下一模块。**

---

## 通用命令

```bash
# 环境
conda activate ov_env_py312
# 或直接用绝对路径（bash 下推荐，conda run 会吞 -c 参数）
PY=/c/Users/test/miniforge3/envs/ov_env_py312/python.exe

cd /c/Users/test/Downloads/xkd/DailyNewsAssistant

$PY -m pytest                    # 默认：只跑离线快测
$PY -m pytest -v                 # 带用例名
$PY -m pytest -m ""              # 全量，含 live / slow
$PY -m pytest -m live            # 仅需联网 / 真实 LLM / 真实飞书
$PY -m pytest -m slow            # 仅耗时项（TTS 合成、E2E）
$PY -m pytest --cov=dna          # 覆盖率（需 pytest-cov）
```

### 标记约定

| 标记 | 含义 | 默认 |
|---|---|---|
| 无标记 | 纯离线，不联网不落真实盘 | ✅ 跑 |
| `@pytest.mark.live` | 需联网 / 真实 LLM / 真实飞书长连接 | ⬜ 跳过 |
| `@pytest.mark.slow` | 耗时长（TTS 合成、端到端） | ⬜ 跳过 |

默认跳过规则写在 `pyproject.toml` 的 `addopts = "-q -m 'not live and not slow'"`。

---

## P0 脚手架

**最近一次全量结果：95 passed in 0.49s（0 failed）**

### `tests/core/test_config.py` — 配置加载

```bash
$PY -m pytest tests/core/test_config.py -v
```

| 覆盖点 | 说明 |
|---|---|
| Settings 默认值 | 不依赖本机 `.env`，构造时传 `_env_file=None` 隔离 |
| `.env` 覆盖 | 文件中的值应覆盖代码默认值 |
| 路径解析 | 相对路径按仓库根解析为绝对路径（`output_path` / `data_path` / `db_file`） |
| **飞书白名单** | 逗号分隔、去空格、去重；**空值必须得到空列表 = 拒收全部** |
| `api_key_for()` | 云端 provider 返回 key，本地 provider 返回空串 |
| **代理配置** | `localhost_bypasses_proxy()` 能识别 NO_PROXY 漏放行 localhost |
| `load_sources()` | 只返回 enabled；id 重复报 `ConfigError`；文件缺失报 `ConfigError` |
| `load_profile()` | 字段类型正确（`languages` 转 `Language` 枚举、时长转元组） |
| 仓库自带配置 | `config/sources.yaml` 与 `profile.yaml` 必须真实可加载（防手写笔误） |

**预期**：20 passed，< 2s，不联网、不碰真实 `outputs/` 与 `data/`。

---

### `tests/core/test_models.py` — 核心数据模型

```bash
$PY -m pytest tests/core/test_models.py -v
```

| 覆盖点 | 说明 |
|---|---|
| `NewsItem` 校验 | 空标题报错；未知字段报错（`extra="forbid"`，防拼写错误静默生效） |
| `MediaAsset` | 必须携带 `source_url`，保证 references 能标注出处 |
| `Cluster` | `canonical` 取首个成员；`refs` 去重且保序 |
| 双语回退 | 缺英文时 `title(EN)`/`summary(EN)` 回退中文，渲染层永远拿不到 `None` |
| `has_language()` | 判断某语言是否已成稿——**补译功能的判定依据** |
| `video_entries()` | 只返回 `need_video=True` 的条目 |
| `entry_by_id()` | 命中与未命中——**重做功能的定位依据** |
| **JSON 往返无损** | `_digest.json` 是可重放事实源，重做功能完全建立在这一点上 |

**预期**：13 passed，< 2s。

---

### `tests/core/test_naming.py` — 命名与路径规则

```bash
$PY -m pytest tests/core/test_naming.py -v
```

| 覆盖点 | 说明 |
|---|---|
| 期次目录 | 必须是 `YYYYMMDD-DailyNews`（用户指定格式，不可改） |
| slug 基础 | 中英混排、空白与标点折叠成单个 `-`、超长截断 |
| **Windows 非法字符** | `<>:"/\|?*` 逐个参数化验证必须被剔除，否则建目录直接 `OSError` |
| **Windows 保留名** | `CON`/`PRN`/`NUL`/`COM1`…必须回退为 `untitled` |
| 结尾点与空格 | Windows 不允许，必须剥掉 |
| 空标题回退 | 空串、纯标点 → `untitled` |
| `topic_dir_name()` | 两位补零序号 + slug；序号 < 1 报错 |
| 端到端路径 | 用真实中文标题拼完整相对路径，逐段验证 Windows 可用 |

**预期**：26 passed，< 1s，纯字符串运算无 I/O。

> 这组测试参数化程度高（37 个用例），因为目录名一旦出错是**运行期**才炸，且在 Windows 上错得很隐蔽。

---

### `tests/core/test_doctor.py` — 环境自检

```bash
$PY -m pytest tests/core/test_doctor.py -v
# 对应的人工验证
$PY -m frontends.cli.main doctor -v
```

| 覆盖点 | 说明 |
|---|---|
| Python 版本 | 当前环境必须 ≥ 3.12 |
| 依赖分级 | P0 缺失 = FAIL；后续阶段缺失只能 WARN（依赖按阶段增量装） |
| LLM 配置 | 云端缺 key → FAIL；本地 provider 无需 key → OK；**输出必须脱敏** |
| **代理** | NO_PROXY 漏放行 localhost → FAIL（否则本地 Ollama/RSSHub 被代理拦截） |
| **远程投递** | 未启用 → SKIP（部署在私人电脑）；启用缺凭证 → FAIL；**启用但白名单为空 → FAIL** |
| TTS 模型 | 空/不存在/无 IR → WARN；有 `*.xml` → OK |
| 目录可写 | 自动创建并写临时文件验证 |
| 汇总 | `run_all` / `summarize` / `has_failure` |

**预期**：19 passed，< 3s，只在 `tmp_path` 下建目录。

**人工验证预期输出**（本机 2026-09-01）：

```
合计：15 OK · 2 WARN · 0 FAIL · 1 SKIP
环境就绪。
```

两个 WARN 分别是 P2 的 `feedparser`/`trafilatura` 和 P9 的 `lark_oapi`——到对应阶段再装，属预期。
SKIP 是飞书投递（部署在私人电脑，开发机不启用）。

---

### `tests/frontends/test_cli.py` — CLI 前端

```bash
$PY -m pytest tests/frontends/test_cli.py -v
```

| 覆盖点 | 说明 |
|---|---|
| `dna version` | 输出版本号 |
| `dna doctor` | 可运行；无阻塞项退出码 0，有阻塞项退出码 1（脚本/CI 门禁） |
| **`dna config`** | **不得泄露完整密钥**，只显示前 7 位（用户可能截图分享终端） |
| `dna sources` | 列出仓库自带订阅源 |
| 无参数 | 打印帮助而非报错 |

**预期**：7 passed，< 5s。

---

## 待补（随阶段推进填写）

| 阶段 | 测试文件 | 状态 |
|---|---|---|
| P1 | `tests/llm/test_llm_provider.py` | ⬜ |
| P2 | `tests/sources/test_rss.py` `test_user_link.py` `tests/extract/test_extract.py` | ⬜ |
| P3 | `tests/pipeline/test_clean.py` `test_dedup.py` `test_score.py` `test_summarize.py` `test_translate.py` `test_trend.py` | ⬜ |
| P4 | `tests/store/test_output_layout.py` `test_ledger.py` `test_history.py` | ⬜ |
| P5 | `tests/apps/test_graphic_daily.py` | ⬜ |
| P6 | `tests/narration/test_duration.py` `tests/apps/test_video_brief.py` `tests/tts/test_tts.py`(slow) | ⬜ |
| P7 | `tests/apps/test_podcast_script.py` | ⬜ |
| P8 | `tests/frontends/test_redo.py` | ⬜ |
| P9 | `tests/inbox/test_inbox_parse.py` `test_inbox_whitelist.py` `test_inbox_commands.py` | ⬜ |
| P10 | `tests/test_e2e.py`(slow) | ⬜ |
