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

## 阶段：P1 LLM 抽象层

> 待 P1 完成后补充。
