# Git 命令汇总（人工手动执行）/ Git Commands — run manually

> 约定：AI **不执行** `git commit` / `git push`，只在每阶段末尾在此汇总命令，由你审阅后手动执行。
> 注意：`git add -A` 有误提交 `.env` 和 `outputs/` 的风险，一律用**显式路径**添加，或先 `git status` 确认。

---

## 阶段：调研 + 构建方案 + 方案调整（2026-09-01）

初始化仓库（首次执行一次）：

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant
git init
git branch -M main
```

确认 `.env` 已被忽略（**务必先跑这条，输出应为 `.env`**）：

```bash
git check-ignore -v .env
git status --short          # 确认列表里没有 .env / outputs/
```

提交本阶段文档与配置：

```bash
git add .gitignore .env.example
git add docs/00_STAGE_SUMMARY.md docs/01_research.md docs/02_development_plan.md
git add docs/08_feishu_bot_deployment.md docs/git_commands.md
git commit -m "docs: 调研报告与开发方案定稿(v5)，确立分层架构与 P0-P10 计划

- 01_research: 环境/依赖/本地 Qwen3-TTS OV 资产核查，技术选型与风险
                补充远程投递接口选型(飞书长连接)与资产台账可行性评估
- 02_development_plan: 核心公用 + 应用/前端独立的三层架构
                v2 三场景全双语(narration 层公用) / 顺序改为 图文-视频-播客
                    DeepSeek 设为默认 / 新增 inbox 与 ledger 模块
                v3 期次目录改名 YYYYMMDD-DailyNews，去掉中间层
                v4 远程投递改为飞书机器人+长连接(免公网回调)
                v5 飞书功能部署在私人电脑，公司电脑只做离线单测
- 08_feishu_bot_deployment: 飞书机器人部署指南(后台配置/权限/发布/
                open_id/Windows常驻/验证清单/排错表)
- 00_STAGE_SUMMARY: 基础要求、技术栈定稿、进度看板、方案调整决议
- 配置骨架: .env.example / .gitignore（.env 含密钥，不入库）"
```

---

## 阶段：P0 脚手架（2026-09-01）

提交前先确认密钥与产物没被带上：

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant
git check-ignore -v .env outputs data    # 三者都应被忽略
git status --short                        # 确认列表里没有 .env / outputs/ / data/
```

跑一遍测试确认绿灯：

```bash
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest -v
# 预期：95 passed
/c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m frontends.cli.main doctor
# 预期：0 FAIL，末行「环境就绪。」
```

添加与提交：

```bash
git add pyproject.toml README.md
git add src/dna/__init__.py src/dna/core/
git add frontends/
git add config/sources.yaml config/profile.yaml
git add tests/
git add docs/00_STAGE_SUMMARY.md docs/03_unit_tests.md docs/git_commands.md

git commit -m "feat(P0): 项目脚手架 —— 配置/数据模型/命名规则/日志/环境自检/CLI

核心层 src/dna/core/
- config: .env + YAML 两层配置；路径按仓库根解析；飞书白名单解析
          (空=拒收全部)；代理校验 (NO_PROXY 必须放行 localhost，
          否则本地 Ollama/RSSHub 被公司代理拦截)
- models: RawItem/Article/NewsItem/Cluster/DigestEntry/DailyDigest 等
          8 个 Pydantic 模型；双语字段成对 + 缺英文回退中文；
          DailyDigest 作为唯一事实源，JSON 往返无损 (重做功能的地基)
- naming: 期次目录 YYYYMMDD-DailyNews；topics 两位序号+slug；
          slug 剔除 Windows 非法字符与保留设备名
- logging/errors/doctor: rich+文件双输出；异常分层；11 类环境自检

前端 frontends/cli: dna doctor / config / sources / version
  - doctor 有 FAIL 时退出码 1，可做脚本门禁
  - config 输出密钥只显前 7 位

配置 config/: sources.yaml (7 源) + profile.yaml (关注词/篇幅/时长)

测试 tests/: 95 passed in 0.49s，全部离线
  - 重点覆盖 Windows 路径安全、密钥脱敏、白名单空值、代理 localhost 放行

文档: README + docs/03_unit_tests.md"
```

---

## 阶段：P1 LLM 抽象层（2026-09-01）

跑测试确认绿灯：

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant
PY=/c/Users/test/miniforge3/envs/ov_env_py312/python.exe

$PY -m pytest                        # 预期：189 passed, 2 deselected
$PY -m pytest tests/llm -m live      # 预期：1 passed（DeepSeek）, 1 skipped（Ollama 未运行时）
git status --short                   # 确认没有 .env / outputs/ / data/
```

添加与提交：

```bash
git add src/dna/llm/ src/dna/core/errors.py
git add tests/llm/ tests/__init__.py tests/core/__init__.py tests/frontends/__init__.py
git add docs/03_unit_tests.md docs/00_STAGE_SUMMARY.md docs/git_commands.md
git add docs/issues/001-ollama-reasoning-token-budget.md
git add docs/issues/002-small-model-echoes-json-schema.md

git commit -m "feat(P1): LLM 抽象层 —— API 与本地推理统一接口，含重试降级

src/dna/llm/
- base: LLMProvider 抽象；chat/chat_text/chat_json/health_check 通用实现
        chat_json 内置「提取 JSON → 校验 → 带错误回灌重试」，让同一份业务
        代码在云端与本地模型间无缝切换
- openai_compat: 一份实现覆盖 DeepSeek/OpenRouter/vLLM/Ollama（协议相同，
        避免四份各自的重试与错误分类逻辑）；本地服务关闭 trust_env 绕过代理
- ollama_provider: 复用 OpenAI 兼容端点，base_url 归一化
- openvino_provider: P10 接口占位，误用时给出明确指引
- factory: 指数退避重试 + 限流/超时可重试、鉴权不可重试直接切备用
- errors: ProviderError 增加 retryable 分级与 4 个子类

真机验证发现并修复两个问题（已归档 docs/issues/）:
- 001 推理模型 token 预算被思维链耗尽，原报错指不到根因
      现在读 finish_reason 与 reasoning 字段，报错点明根因且标记不可重试
- 002 小模型把 JSON Schema 原样抄回，修复重试反馈无效导致三次全废
      现在提示词区分 Schema 与数据、显式识别 Schema 回声、启用服务端 JSON 模式

测试 tests/llm/: 189 passed（全量），全部离线；live 用例真实打通 DeepSeek
  重点覆盖字符串内括号不截断、错误分类、退避不真等待、Schema 回声纠正"
```

---

## 阶段：P2 信息源与抽取（2026-09-01）

先装本阶段依赖（若尚未安装）：

```bash
PY=/c/Users/test/miniforge3/envs/ov_env_py312/python.exe
$PY -m pip install feedparser trafilatura
```

跑测试与真机验证（**本阶段不调用 LLM，无费用**，可放心重复执行）：

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant

$PY -m pytest                                    # 预期：316 passed, 2 deselected
$PY -m frontends.cli.main fetch --limit 3        # 采集预览：4 个源可用
$PY -m frontends.cli.main fetch --source qbitai --limit 1 --extract   # 含正文抽取
git status --short                               # 确认没有 .env / outputs/ / data/
```

添加与提交：

```bash
git add src/dna/core/urls.py src/dna/sources/ src/dna/extract/
git add frontends/cli/main.py config/sources.yaml README.md
git add tests/core/test_urls.py tests/sources/ tests/extract/ tests/fixtures/
git add docs/03_unit_tests.md docs/00_STAGE_SUMMARY.md docs/git_commands.md
git add docs/02_development_plan.md docs/10_sources_guide.md
git add docs/issues/003-p2-live-verification-findings.md

git commit -m "feat(P2): 信息源与抽取层 —— RSS/RSSHub/用户投递 + 正文与媒体抽取

core/urls: URL 规范化（一级去重的基础）
  剔除 utm_*/fbclid/spm/from 等追踪参数，但保留内容参数
  （微信文章 id 就在 __biz/mid/idx 里，清空会把不同文章合并）
  url_hash 用 sha256 而非内置 hash（后者带随机种子，不能做台账主键）
  extract_urls 剥掉结尾中英文标点（手机转发的链接几乎必带）

sources/: SourceAdapter 抽象 + RSS/RSSHub/用户投递三个实现
  抓取与解析分离，parse_feed 是纯函数，可用本地样例离线测试
  registry.collect 逐源隔离错误：日报每天无人值守跑，
  任何一个源挂掉都不该导致当天没有日报

extract/: trafilatura 主 + bs4 兜底；og:image/正文图/官方视频
  抽取失败降级为「仅标题+链接」而非抛异常——链接本身仍有价值
  每个媒体资产强制带 source_url 与 credit（发布合规，事后补不回来）

真机验证发现并修复三个问题（docs/issues/003）:
  A feed 失效被静默记成「成功 0 条」——feedparser 的 bozo 不可靠
    （HTML 错误页的 bozo 是 False），改用 version 判据
  B og:image 是站点 logo 时成为日报封面（量子位实测），
    社交图同样过图标过滤；标记改为按词匹配，避免误杀
    overhead-view.jpg 这类真实配图
  C 三个订阅源实测失效，换成已验证可用的 InfoQ / arXiv

sources/discover: dna probe —— 探测网站的 feed 地址并当场验证
  三级策略：地址本身 → 页面 <link rel=alternate> 声明 → 常见路径猜测
  路径猜测不可省：实测 InfoQ 的 feed 可用但首页并不声明它
  验证通过直接输出可粘贴的 sources.yaml 片段

CLI 新增 dna fetch（采集预览）与 dna probe（feed 探测），均不调用 LLM
源清单扩到 7 个启用 + 6 个已验证可用待开启，均为实测结果
文档 10_sources_guide: 如何自行添加信息源

测试: 333 passed，全部离线，用 tests/fixtures/ 的本地样例"
```

---

## 阶段：P3 Pipeline 核心

> 待 P3 完成后补充。
