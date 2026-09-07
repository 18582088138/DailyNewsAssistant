# Git 命令汇总（人工手动执行）/ Git Commands — run manually

> 约定：AI **不执行** `git commit` / `git push`，只在每阶段末尾在此汇总命令，由你审阅后手动执行。
> 注意：`git add -A` 有误提交 `.env` 和 `outputs/` 的风险，一律用**显式路径**添加，或先 `git status` 确认。

---

## 分步提交（2026-09-06 起）

**一步一个功能面，`git add` 与 `git commit` 成对出现**，不再把一个阶段攒成一个
大而全的 add 块 + 一条百行 commit message。

```bash
# --- 步骤 N/M · 这一步是什么 ---
git add <显式路径>
git add <显式路径>
git commit -m "type(scope): 一句话

- 要点
- 要点"
```

### 怎么切

**按功能面切，不按改动主题切。**一个阶段里的改动主题常有四五个（缓存、标识规则、
数据建模、界面……），但 `git add` 的粒度是**整个文件的当前内容**——
`produce/service.py` 一个文件里往往同时装着其中的三四个。想按主题切就只能
`git add -p` 逐块挑，挑错一块就是一个编译不过的提交。所以切在**文件边界**上，
一步一层。这个项目的天然分法是：

```
台账 store  →  文案 narration/pipeline  →  产物 produce  →  界面 frontends/CLI  →  文档 docs
```

**顺序自下而上。**新增的参数一律带默认值，上层不动也调得通，所以停在任何一步，
那个 commit 单独检出都能起来。反过来先提交界面，界面会调到还不存在的接口。

**同生共死的文件必须同一步。**典型是 `core/config.py` 与 `config/*.yaml`：
Profile 删了字段而 yaml 还留着那一行，pydantic 直接 `extra_forbidden` 起不来。

**测试只在最后跑一次。**`pytest` 读的是工作区、不是索引，中间步骤跑等于反复跑同一份
代码。要验证某个 commit 自洽只有干净检出这一条路（见下一节第 2 条）。

### commit message

三行以内 + 要点。根因分析写进 `docs/issues/NNN`，不写进 commit——
提交完成之后没有人会再从 `git log` 里翻它。

---

## 每次提交后必做的两条检查

「按文件列表提交」有一个不会报错的失效模式：**某一轮的 add 块没执行，
后面几轮各自只列自己改到的文件，漏下的就永远没人捡**。工作区里文件都在、
测试全绿，只有干净克隆才会暴露。2026-09-04 就是这样发现 HEAD 起不来的
（`produce/__init__.py` 与 `narration/__init__.py` 从没进过库），
补救见[补提交](#补提交2026-09-04-把-p35-漏掉的文件一次补齐)。

```bash
# 1. 工作区必须干净——还有东西剩下就说明这一轮又漏了
git status --short                 # 预期：空

# 2. 把 HEAD 单独克隆出来试一次 import —— 唯一能发现「文件没进库」的办法
rm -rf /c/tmp/headcheck
git clone -q --no-hardlinks . /c/tmp/headcheck
PY=/c/Users/test/miniforge3/envs/ov_env_py312/python.exe
PYTHONPATH=/c/tmp/headcheck/src $PY -c \
  "from dna.produce import produce, DISPLAY_ORDER; from dna.narration import estimate_seconds; print('HEAD import ok')"
rm -rf /c/tmp/headcheck
```

第 2 条尤其要跑在**新增了包目录**的那一轮之后——新目录的 `__init__.py`
是最容易漏的一个文件，而漏了它整个包在干净环境里就 import 不到。

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
# 注意：docs/issues/ 已被 .gitignore 忽略，不要 git add，否则会报错

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

## 阶段：P2+ 落盘与台账（2026-09-02，应用户要求从 P4 提前）

> ⚠️ 你已把 `docs/issues/` 加入 `.gitignore`，因此下面的命令**不包含 issues 文档**。
> 若希望问题台账入库，请从 `.gitignore` 里去掉该行。

验证（**不调用 LLM，无费用**）：

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant
PY=/c/Users/test/miniforge3/envs/ov_env_py312/python.exe

$PY -m pytest                                  # 预期：402 passed, 3 deselected
$PY -m frontends.cli.main fetch --limit 2      # 采集入库
$PY -m frontends.cli.main list                 # 总表
$PY -m frontends.cli.main stats                # 统计
git status --short                             # 确认没有 .env / outputs/ / data/
```

添加与提交：

```bash
git add src/dna/store/ src/dna/sources/filters.py src/dna/sources/http.py
git add src/dna/core/config.py src/dna/extract/article.py
git add frontends/cli/main.py config/sources.yaml README.md
git add tests/store/ tests/sources/test_filters.py tests/extract/test_extract.py
git add docs/00_STAGE_SUMMARY.md docs/02_development_plan.md docs/03_unit_tests.md
git add docs/10_sources_guide.md docs/11_article_store_guide.md docs/git_commands.md

git commit -m "feat(P2+): 文章总表、正文与图片落盘、按源过滤、指定链接抓取

应用户要求把 P4 的落盘与台账提前——P2 抓完不落盘就无法验证抓取质量。

store/ledger: SQLite 文章总表（标题/链接/状态/正文长度/配图数/落盘位置）
  跨日去重的落点：同一篇文章明天还在 feed 里也不会重复抓取与发布
  feed_title 单独存源自带标题，每次采集从源刷新，永不被抽取结果覆盖
  schema 带 user_version 迁移，加字段不需要删库重来
store/article_store: 正文落 article.md（带 frontmatter）+ meta.json + references.md
  配图下载并为每张图写同名 .json 记录出处——图片被单独拷走时信息不丢
  格式按文件头判断而非 URL 后缀
store/intake: 采集→过滤→抓正文→落盘→记台账 一条流程，逐条隔离错误

sources/filters: 按源的 include/exclude/标题长度/时效过滤
  在抓正文之前执行，被滤掉的条目不产生网络、存储与 LLM 成本
  全局排除词对所有源生效；include 只取源级（全局强制命中会误杀大量正常资讯）

CLI: dna add（抓指定链接）/ list（总表）/ show（核对结果）/ refetch / stats
  fetch 改为完整入库，加 --dry-run 保留原预览行为

真机验证发现并修复两个问题（docs/issues/004，未入库）:
  A 图床防盗链导致配图全部 403 —— 下载时带原文地址作 Referer
  B 抽取降级时站点通用标题覆盖了 RSS 正确标题，且覆盖后不可恢复
    —— 降级时改用 RSS 标题；新增 feed_title 列保存源标题

测试: 402 passed，全部离线（图片下载被 monkeypatch 拦截）"
```

---

## 阶段：P2++ 视频落盘与人工补正文

> `docs/issues/` 已在 .gitignore 里，issue 005 的文件不要 `git add`。

```bash
git add src/dna/store/video_store.py         src/dna/store/article_store.py         src/dna/store/intake.py         src/dna/store/ledger.py         src/dna/store/__init__.py         src/dna/extract/media.py         src/dna/extract/article.py         src/dna/core/urls.py         frontends/cli/main.py         tests/store/test_video_store.py         tests/store/test_article_store.py         tests/store/test_ledger.py         tests/core/test_urls.py         tests/extract/test_extract.py         pyproject.toml         docs/00_STAGE_SUMMARY.md         docs/03_unit_tests.md         docs/11_article_store_guide.md         docs/git_commands.md

git commit -m "feat(store): 视频落盘、配图上限提至 10、人工补正文回写链路

store/video_store: 新增视频下载，两条路径
  直链 .mp4 走 HTTP（带 Referer 过防盗链）；播放器/iframe 交给 yt-dlp
  按文件头校验是不是视频——.mp4 的 URL 返回 HTML 错误页很常见
  yt-dlp 优先选单文件 mp4：合并音视频轨需要 ffmpeg，而 ffmpeg 不保证装了
  失败只记原因不抛异常：视频下载失败远比图片频繁（地域限制、会员墙、平台封禁）
  台账 video_count 记实际下载成功数，不是抽到的链接数

配图上限 5 -> 10（extract/media 与 store/article_store 两处都要改，只改一处不生效）
  日报只用 1~3 张，多存的是素材库；重抓拿不回当初那些图（站点会换图删图）
  CLI 加 --max-images / --no-videos

extract/media: 新增按 class/alt/id 过滤装饰图
  微信图片地址是 CDN 随机串，路径上没有任何特征，只看路径的过滤器使不上劲
  但 class="jump_author_avatar" alt="作者头像" 很老实
  不过滤的话每篇微信文章都会白白存进一张作者头像当素材

core/urls: 新增 title_from_url，抓取失败时从 URL 推标题
  原先写死「(抓取失败)」让所有失败文章同名，目录里认不出哪篇是哪篇
  ——而这些正是最需要人工处理的文章

store/article_store: 重抓前清空 images/ videos/
  重抓写进同一目录但 references.md 按本轮重新生成，旧文件成了没有出处的孤儿
  落盘前清掉同 id 的其他目录（标题变化会换新目录，旧的留成垃圾）

人工补正文回写链路（知乎类强反爬站点）:
  降级文章的 article.md 放明确的粘贴标记，并写出该执行的命令
  dna sync <id> 读回正文与标题 -> meta.json + 台账，状态升到 ok
  不回写的话台账永远是 0 字/degraded，后续挑文章会当空文章跳过，补了等于白补

真机验证：微信两篇成功（3151/4069 字，2/10 张图），知乎 403 降级后手工补正文回写成功

测试: 445 passed，yt-dlp 与网络全部 monkeypatch 拦截，不联网不产生费用"
```

---

## 阶段：P3 Pipeline 核心

> `docs/issues/` 在 .gitignore 里，issue 006 的文件不要 `git add`。
> `data/llm_cache/` 与 `outputs/` 同样不入库。

```bash
git add src/dna/pipeline/         src/dna/llm/cache.py         src/dna/llm/factory.py         src/dna/core/config.py         frontends/cli/main.py         tests/pipeline/         tests/llm/test_cache.py         tests/llm/fakes.py         tests/llm/test_factory.py         tests/core/test_config.py         pyproject.toml         docs/00_STAGE_SUMMARY.md         docs/03_unit_tests.md         docs/06_prompt_spec.md         docs/12_digest_guide.md         docs/git_commands.md

git commit -m "feat(pipeline): P3 流水线核心 —— 清洗/去重/打分/摘要/双语/趋势 + LLM 响应缓存

pipeline/clean: NFKC 全量归一会把中文逗号折成半角，改为显式折叠表
  只折本来就是 ASCII、只是被打成全角的字符；中文标点是内容不是格式
  产物是要发公众号和小红书的中文稿，混着半角逗号读起来像机翻

pipeline/dedup: 三级递进——规范 URL / SimHash 2-gram / 语义向量（可选）
  指纹用 blake2b 不用内置 hash：后者每进程加盐，跨日去重重启后会静默失效
  阈值汉明距离≤3 取保守值：漏合并读者看得出，错合并读者无从察觉
  签名只取正文前 500 字——转载常在文末追加内容，用全文比对会把相同的拉开
  向量层加载失败时降级而非报错，前两级照常工作

pipeline/score: 五信号加权规则打分，不用 LLM
  可解释（知道该改哪）、稳定（同输入同分，排序不抖）、免费
  关键词除以 3 而非关键词总数：配 20 个词时按总数归一会让所有分趋近 0
  无发布时间给 0.5 不给 0：很多 feed 不给 pubDate，当成很旧等于封杀整个源
  need_video 刻意宽松——误标可取消勾选，漏标则根本不会出现在候选里

pipeline/summarize·translate·trend: 三个 LLM 节点
  提示词构建拆成纯函数，测试断言我们问了什么而非模型答了什么
  翻译按 id 对齐不按顺序：漏译一条会让后面全部错位且不报错
  综述少于 4 条不调用——两三条谈不上趋势，硬写只得到废话还要花钱
  全部失败降级不抛异常：日报无人值守跑，一条翻车不该让整期作废

pipeline/flow: run_daily 编排，dry_run 停在费用分界线上
  clean/dedup/score 免费，summarize/translate/trend 计费
  先看清单再付费，而不是付完钱才发现选错了

llm/cache: 磁盘响应缓存，相同请求不再计费
  键含 provider+模型+完整消息+调用参数，换模型自然错开
  不缓存失败（报错重试要真的重试）；文件损坏当未命中（缓存不能成为故障源）
  实测 5 条日报首次 6 次调用/10.7 秒，复跑 0 次调用/0.1 秒

core/config: db_file 改为按数据目录解析，不再按仓库根
  原先 data_dir 与 db_path 是两个独立项，改 DATA_DIR 会让文章搬家而台账留原地
  台账里 store_dir 全指向空目录，且不报任何错——测试因此写进了真实数据库

CLI: 新增 dna digest（--dry-run / --limit / --bilingual / -a 指定文章 / --no-cache）

真机验证（DeepSeek，共 8 次调用）：5 条中英双语日报 + 主线综述
  综述正确提炼出跨条目共性而非复述标题；2TP×4USP、XPU Kernel 等术语原样保留

测试: 610 passed，全部使用假 provider，零 LLM 调用、零费用、不联网"
```

---

## 阶段：P3.5 台账工作台

> ⚠️ **这一块下面的命令没有被执行过**（`git log` 里没有对应提交），
> 后面两个阶段的提交只带走了它们各自列出的文件，
> 于是 P3.5 的代码**只进去了一半**。补救见文末的
> [「补提交」](#补提交2026-09-04-把-p35-漏掉的文件一次补齐)。
> 这里保留原样作为记录，**不要再单独执行**。

> `docs/issues/` 在 .gitignore 里，issue 007 不要 `git add`。

```bash
git add src/dna/produce/         src/dna/narration/         src/dna/store/migrate_layout.py         src/dna/store/db.py         src/dna/store/ledger.py         src/dna/store/__init__.py         src/dna/store/article_store.py         src/dna/store/intake.py         src/dna/core/config.py         src/dna/extract/media.py         src/dna/pipeline/source.py         frontends/nicegui_app/         frontends/cli/main.py         tests/narration/         tests/produce/         tests/store/test_migrate_layout.py         tests/store/test_ledger.py         tests/extract/test_extract.py         tests/pipeline/test_source.py         config/profile.yaml         pyproject.toml         docs/00_STAGE_SUMMARY.md         docs/03_unit_tests.md         docs/06_prompt_spec.md         docs/07_db_schema.md         docs/13_workbench_guide.md         docs/git_commands.md

git commit -m "feat(workbench): P3.5 台账工作台 —— GUI 产出矩阵 + 单篇五种产物

目录迁移：文章目录从 data/ 搬到 outputs/articles/
  里面全是产物——正文、配图、视频、各类文案，是要打开要拷走要发布的东西
  data/ 只留台账数据库与 LLM 缓存
  migrate_layout 先移文件再改库：反过来的话失败时每行都指向不存在的目录
  没有台账引用的孤儿目录只报告不删除——盘上的东西是用户的

store: schema v3 新增 productions 表
  每次生成插新行、redo_of_id 指向上一版，不原地更新
  原地更新会把上一版连同它的模型和时间一起抹掉，而内容出问题时
  「这段稿子是哪天用哪个模型写的」必须能回答
  失败也记一行：不记的话表格显示未生成，人会以为没跑过，再点一次再失败一次
  production_matrix 一次查完整页——50 行 × 5 种产物逐格查库是 250 次往返

narration: 三种文案，共用一套专业性约束
  短视频 25~35s（一条只讲一个点）／口播 1~2min（必须有技术深度）
  长文案 5~15min，专题单角色／访谈双角色
  长文案分段生成：提纲 1 次 + 每节 1 次。3000~4000 字远超单次输出的可靠范围，
  一次生成会丢结构、重复论述、越写越水——而不要白话正是核心要求
  时长超区间带具体差多少字回炉，最多 2 次；仍不达标则返回结果而不是报错
  长度只在时长空间定义一处，字数由它换算（原先两种单位并存已经出现矛盾）

produce: 单篇产物层，CLI 与 GUI 调同一个 service
  已有产物且未 force 时零调用——GUI 按钮就在手边，误触不该等于计费
  长文案不进 --all：一篇 5~9 次调用，是其余四项加起来的两倍多
  前置缺失自动补（英文总结依赖中文总结），不报错让人手动跑

extract: 补 funder/sponsor/partner 标记
  arXiv 摘要页没有配图，页脚的 simons-foundation.png 顶上成了条目封面

config: 媒体上限可配置，命令行 > 源级 > profile 全局
  arXiv 摘要页没有配图，微信长文可能有二十几张，一个全局值伺候所有源
  要么浪费带宽要么漏素材

GUI: dna gui —— 一篇文章一行，五种产物各一列，每格可单独重做
  所有 LLM 调用走 run.io_bound，否则一次调用冻住整个界面像崩了一样
  顶部常驻缓存命中率：看不见的成本最容易失控

图片水印：实测无法在抓取时绕开（发布方烧进像素，探测过各种变体只有一个版本），
按用户决定不处理

真机验证（DeepSeek，11 次调用）：短视频 26s、口播 99s、访谈长文案 7.4min/29 轮
  生成内容里的 304B、167GB、0.0102、UD-Q8_K_XL 等全部能在原文找到，零编造

测试: 705 passed，全部假 provider，零 LLM 调用、零费用"
```

---

## 阶段：P3.5 bis 文案质量返工

> 用户给了人工撰写的参考稿，指出「信息量不够，同时没有把文章讲清楚」。
> `docs/issues/` 在 .gitignore 里，issue 008 不要 `git add`。

```bash
git add src/dna/narration/duration.py \
        src/dna/narration/script_builder.py \
        src/dna/narration/longform.py \
        src/dna/pipeline/summarize.py \
        src/dna/produce/service.py \
        src/dna/core/config.py \
        tests/narration/ \
        tests/produce/test_service.py \
        tests/pipeline/test_summarize.py \
        config/profile.yaml \
        docs/03_unit_tests.md \
        docs/06_prompt_spec.md \
        docs/13_workbench_guide.md \
        docs/git_commands.md

git commit -m "fix(narration): 文案信息量不够 —— 字数换算用错了语速

根因不在提示词措辞，在单位。先量了一遍参考稿与生成稿：
  人工撰写的参考稿 199 字符 → 27.8 秒 ＝ 7.2 字符/秒
  本系统生成的稿子 185 字符 → 25.8 秒 ＝ 7.2 字符/秒
两篇长度几乎一样，所以问题不是稿子太短，而是同样的秒数里放的事实太少。

duration: 拆出 prompt_char_budget，与 target_chars 分开
  target_chars 是物理量（estimate_seconds 的严格逆运算，纯中文自洽）
  prompt_char_budget 是要告诉模型写多少字，按中英混排实测密度放大 1.5 倍
  按 4.5 字/秒折算，25~35 秒只要 112~157 字，而实测装得下 168~235 字
  模型照少的写，时长恰好落在区间下沿：验收通过、回炉不触发、
  信息量少掉三分之一，而且没有任何环节报错——这是完全静默的错误
  验收仍然只走 estimate_seconds，放大只作用于提示词，不放松标准

  回炉差值改按上一稿实测密度换算（observed_chars_per_second）
  固定 4.5 字/秒对 8 字符/秒的技术稿只会少要求删一半，
  第二稿仍然超时，那次调用白花

  教训：换算系数必须来自实测，不能来自定义。4.5 字/秒是中文播报语速的
  定义值，对纯中文稿成立；稿子里有三成英文时它就不是那个场景的答案了
  （issue 007-C 修的是同一个量两种单位，这次是两种单位都对但用错了场合）

script_builder: 信息密度列为第一要求
  时长固定，而且发布时视频还会加速播放，成败在同样的秒数里装多少事实
  每句必须携带至少一个具体信息点，写不出信息点的句子直接删掉
  禁止的填充句式逐条点名，不再笼统说不要白话——笼统的禁令模型执行不了
  四类都来自真机产出：提示词里的结构提示被原样写进稿子（这意味着什么）、
  第一人称（我最关注的是）、形容词结尾、填充语

  短视频从一条只讲一个点改为覆盖文章主干
  初版产出技术正确却没把文章讲清楚：观众知道某个量化档位困惑度没变，
  却不知道这是哪个模型、发布了没有
  第一句必须是事件本身，之后每句换一个新事实
  主标题写事件、副标题堆亮点（参考稿的写法）

  口播也返回主副标题——1~2 分钟的稿子也是要发到平台上的视频
  结尾落在具体建议或具体数字上，不用形容词收束

  正文上限 3000 → 6000 字。4069 字的技术稿被切掉的尾部含采样参数
  和一条关键局限（官方没给 Jinja chat template，能跑和跑对是两回事），
  而那正是必须说局限这条规则要用的料，模型根本没看到
  摘要节点保持 3000：它每条都跑，上限直接乘以条目数；文案单篇按需跑

summarize: 加信息完整性与指标取舍优先级
  原文讲了几件事就都要覆盖到，不要只写前一半
  1~2 句装不下所有数字，所以取舍规则必须写进提示词；不写的话模型
  按原文出现顺序取，而技术文章开头往往是文件体积这类次要细节
  实测对照：模型自选写了模型文件 167GB，参考摘要写的是激活参数 13B

config: cta_line 进 profile，时长区间接通到构建器
  结尾引导语是账号品牌，换栏目就改配置；提示词只留怎么写好文案的规则
  profile.yaml 里三行时长配置此前是死配置：构建器用自己的默认参数，
  改了完全没反应——比不提供这个配置更糟，因为人会以为改生效了
  longform 默认窗口 (600,900) → (300,900)，与代码下限对齐；
  两处写不同的值会出现配置说 10 分钟起、代码按 5 分钟起

参考稿里的得分 50、激活参数 13B、百万 Token 上下文在原文中都不存在
（逐个检索确认），是撰稿人从别处知道的。提示词有硬约束不确定的宁可不写，
所以系统不会写出这几个数字——这是正确行为。要让跨源事实进文案需要
采集侧聚合多家来源，不是放松约束

真机复验（DeepSeek，4 次调用，长文案未复跑）：
  短视频 185字/26s → 289字/31s，8 个事实
  口播 550字/99s → 840字/94s，无第一人称，新增 PD 分离与 KLD 差 8 倍
  生成内容里的 304B、167GB、0.0102、0.0747、384K 等全部能在原文找到，零编造

长文案实跑两篇后发现分节重复：CES 那篇第 7 节把第 6 节的产品逐个重讲一遍，
9 个实体全部重复、238 字零新信息。根因是 previous_tail 只给上一节最后 120 字，
模型不知道前面几节覆盖了什么，把「其他创新硬件」理解成再说一遍我知道的硬件

longform: 每节提示词放完整提纲并标注已讲过/现在写这节/留给后面，不增加调用次数
  提纲阶段要求各节互斥，禁止用「其他」「其余」「补充」命名任何一节
  局限那条放宽为局限/代价/前提条件/未解决的问题，原文没明说时讲共同短板
  复验同一篇：4078字/628秒/7节 → 5524字/902秒/8节含局限节
  剩下的跨节重复是局限节在点评前文，属预期行为

落盘路径以 P3.5 为准（用户决定）：条目级只存 outputs/articles/<日期>/<slug>__<id8>/
  原方案的 topics/ 与 _history/ 取消，期次目录只放整期产物、条目按 id 引用
  data/ 只放 dna.db 与 llm_cache/，不放任何产物
  清理了 data/articles/ 下 2 个残留目录（标题改名前的旧副本，内容与新目录相同）
  已核查全仓 22 处写文件位置，条目内容一律走 output_path / store_dir，目前没有散落

测试: 725 passed，全部假 provider，零 LLM 调用、零费用"
```

---

## 阶段：P3.5 ter 工作台改版 + 链接导入

> `docs/issues/` 在 .gitignore 里，issue 008 不要 `git add`。

```bash
git add frontends/nicegui_app/         src/dna/produce/tasks.py         tests/frontends/test_workbench.py         docs/13_workbench_guide.md         docs/03_unit_tests.md         docs/git_commands.md

git commit -m "feat(gui): 工作台改版 —— 修布局重叠、冻结表头、重做移出格子、链接导入

布局：flex 换成 CSS Grid
  先前 flex + 固定宽度 + no-wrap，窗口一窄，flex-1 的标题被压到零宽以下，
  后面的固定宽度列就叠在标题上；Grid 配 min-width 从根上不可能重叠，
  宽度不够出横向滚动条。已用 Playwright 在 1600px 与 900px 两种宽度下核对
  标题按字符数截断（默认 42 字）。CSS 的 ellipsis 是按像素截的，
  CJK 字符宽度是拉丁字母两倍，纯靠它中文标题露出的字数只有英文的一半，
  而这张表以中文标题为主

表头 sticky：表格自己是滚动容器，翻到第 30 行仍看得见哪列是哪个功能
  列宽只有 108px，没有表头认不出「口播」和「短视频」

列分隔线 + 抓取信息与产物之间加一道重线分组
  文章多的时候没有竖线根本对不上哪一列是哪个功能

重做按钮从格子移进展开面板
  格子只有 108px 宽，重做按钮和「打开内容」的点击区域挨在一起，
  而两者代价完全不对等：一个免费，一个是一次计费调用
  现在点格子展开内容，重做在面板里，等于要求先看见内容再决定要不要重做——
  这本来就是重做之前该走的一步。按钮琥珀色，和其它操作视觉上分开

展开面板新增：下载文案 / 下载 TTS 用的 JSON / 打开产物目录 /
  直接打开 images 与 videos（要拷的是素材本身，少点一层）
  素材按钮只在目录真实存在且非空时出现——点开是空文件夹比没按钮更让人困惑
  音频与成片按钮摆出来但禁用，tooltip 写明等 P6：不摆没人知道将来会有，
  摆了却能点是骗人
  打开文件夹在服务端执行，绑定地址不是本机时拒绝并说明原因，
  否则会在服务器上悄悄弹窗而点的人什么也看不到

展开内容改为按需读取：一页 50 行 × 5 种产物进页面就全读是 250 次磁盘 I/O

配色：深色控制台，青绿=已生成复用不花钱，琥珀=会计费，红=失败
  每种状态同时带形状符号（●○▲—），不只靠颜色——色觉障碍与黑白截图下都要能分辨
  字体只用 Windows 本地有的栈，不走 CDN：公司代理会把 Google Fonts 拦下来

tasks: TaskSpec 加 spoken 字段，标出哪些产物要被念出来
  界面据此决定哪几格该有音频入口；P6/P7 的 TTS 也用它

新增链接导入（右上角，不调用 LLM 不产生费用）
  支持一次多条，也支持直接粘一整段带链接的文字——真实投递就是从群聊里
  复制出来的一段话，链接夹在中文之间、结尾带中文句号
  输入框下实时列出识别到的链接：粘一整段聊天记录时，这是唯一能提前发现
  少粘一条或多认一个图片地址的机会，抓完再发现台账里已经有垃圾行了
  走 intake_urls，与 dna add 同一个入口，不在界面里另写一套

修：确认框里的长文案时长预估改走 plan_target_seconds
  界面里原本自带一份 min(text_len*1.2, 4000)/4.5/60，是已废弃的按字符数推导，
  核心改了之后报的分钟数和实际生成的对不上，而这个数字正是人决定花不花钱的依据

修：表格容器排到筛选栏后面（NiceGUI 按创建顺序布局，原先表格跑到了筛选栏上面）

新增 NEW 标识：标出刚导入、还没调过 LLM 的条目
  判定在 dna.produce.is_new_article（产物层，不在前端）：
  一条产物记录都没有（含失败的）且首次入库在 new_badge_hours 小时内（默认 24）
  失败的尝试同样清掉标识——失败记录说明 LLM 已经调过、钱已经花了，
  而且那一格显示 ▲，和 NEW 摆在一起是自相矛盾的信号
  时间窗不是可选的：实测台账 37 篇里 32 篇从来没有任何产物（RSS 存量大多如此），
  只看有没有产物的话 NEW 会挂在 32 行上，而这个标识的全部意义就是
  从几十行里找出刚粘进去的那几条；设 0 可关掉时间窗
  用第四种颜色（青蓝），和已生成的青绿、计费的琥珀、失败的红都分得开——
  复用颜色会让人把「新导入」误读成「已完成」
  整行左侧也加青蓝竖线：只靠标题旁一个小标签，横向滚动到右边就看不见了
  减少动效偏好下只关呼吸动画，标识本身保留——动效是装饰，标识是信息
  配套「只看新导入」筛选与「N 条新导入」计数

测试: 752 passed
  新增 tests/frontends/test_workbench.py 22 条 + tests/produce/test_service.py +5
  锁的是纯函数与文件系统契约——布局观感测不出来，靠人工看；
  但这些出错时界面会安静地显示错误的东西（该禁用的按钮没禁用、点下去 404、
  表头与数据列错开一格、NEW 挂错行）"
```

---

## 补提交（2026-09-04）：把 P3.5 漏掉的文件一次补齐

> **当前 HEAD 是跑不起来的。** 已验证：把 HEAD 单独 clone 出来，
> `from dna.produce import produce` 直接 `ImportError`——
> `src/dna/produce/__init__.py` 与 `src/dna/narration/__init__.py` 从来没进过库。
> 命令行的 `dna produce`、`dna gui` 在干净克隆上都会在 import 阶段就挂掉。

### 怎么漏的

P3.5 那一块的 `git add` 命令**没有被执行过**（`git log` 里没有 P3.5 的提交）。
后面两个阶段（文案返工、工作台改版）的提交各自只列了自己改到的文件，
于是 P3.5 的产物被切成了两半：

| 已进库 | 漏在外面 |
|---|---|
| `produce/{tasks,service}.py`（被文案返工那次带走） | **`produce/__init__.py`** |
| `narration/{duration,script_builder,longform}.py`（同上） | **`narration/__init__.py`** |
| `frontends/nicegui_app/*`（被工作台改版那次带走） | `store/migrate_layout.py` |
| | `store/{db,ledger,article_store,intake,__init__}.py` 的 P3.5 改动 |
| | `extract/media.py`、`pipeline/source.py`、`cli/main.py` 的 P3.5 改动 |
| | `docs/07_db_schema.md`、`tests/produce/__init__.py`、`tests/store/test_migrate_layout.py` |

**根因不是手滑，是「按文件列表提交」这个做法本身**：每次只列「这一轮改了什么」，
上一轮漏下的就永远不会被后一轮捡起来，而且**不会报错**——工作区里文件都在，
测试全绿，只有干净克隆才会暴露。

### 补提交

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant

# 先确认敏感路径都在忽略里（四条都应该有输出）
git check-ignore -v .env outputs data docs/issues

# 再确认没有别的东西混进来
git status --short
```

```bash
git add .gitignore         src/dna/produce/__init__.py         src/dna/narration/__init__.py         src/dna/store/migrate_layout.py         src/dna/store/__init__.py         src/dna/store/db.py         src/dna/store/ledger.py         src/dna/store/article_store.py         src/dna/store/intake.py         src/dna/extract/media.py         src/dna/pipeline/source.py         frontends/cli/main.py         tests/produce/__init__.py         tests/store/test_migrate_layout.py         tests/store/test_ledger.py         tests/extract/test_extract.py         tests/pipeline/test_source.py         docs/07_db_schema.md         docs/00_STAGE_SUMMARY.md         docs/02_development_plan.md         docs/git_commands.md

git commit -m "fix(repo): 补齐 P3.5 漏提交的文件 —— HEAD 此前无法 import

P3.5 的 git add 块没有被执行过，后面两个阶段的提交各自只带走了自己列出的
文件，于是 P3.5 的代码只进去了一半。已验证 HEAD 单独 clone 出来
from dna.produce import produce 直接 ImportError，dna produce 与 dna gui
在干净克隆上都会在 import 阶段挂掉

补进来的：
  produce/__init__.py 与 narration/__init__.py —— 两个包的 __init__ 从没进库，
    这是 HEAD 起不来的直接原因；子模块在，包的再导出不在
  store/migrate_layout.py —— 文章目录从 data/ 迁到 outputs/
    先移文件再改库：反过来的话失败时每行 store_dir 都指向不存在的目录
    没有台账引用的孤儿目录只报告不删除——盘上的东西是用户的
  store/db.py —— schema v3，productions 表
    每次生成插新行、redo_of_id 指向上一版，不原地更新
    原地更新会把上一版连同它的模型和时间一起抹掉，而换模型之后
    「哪些产物是旧口径的」必须能筛出来，否则只能全部重跑
  store/ledger.py —— ProductionRecord 与产物矩阵
    production_matrix 一次查完整页：50 行 × 5 种产物逐格查库是 250 次往返
    latest_production 按自增 id 倒序而不是 created_at——同一秒内重做两次
    时间戳相同，按时间排序会拿到不确定的那一版
  store/article_store.py + intake.py —— 落盘根目录改到 outputs/
    里面全是产物（正文、配图、视频、文案），是要打开要拷走要发布的东西
    data/ 只留台账数据库与 LLM 缓存
  extract/media.py —— 补 funder/sponsor/partner 标记
    arXiv 摘要页本来没有配图，页脚的 simons-foundation.png 顶上成了条目封面
    沿用词边界匹配而非子串，partnership-diagram.png 这类真实配图不受影响
  pipeline/source.py —— 按 article_ids 取单篇
  cli/main.py —— dna produce / gui / migrate-layout
  .gitignore —— docs/issues/ 不入库
  docs/07_db_schema.md —— 两张表的结构与取舍

历史顺序因此是乱的：P3.5 的代码排在依赖它的两个提交之后，
中间那两个提交单独 checkout 出来是跑不起来的。不做 rebase 重写——
手动提交的仓库改历史风险大于收益，往后不再断链即可

以后每次提交后必须做的检查（见 git_commands.md 开头）：
  git status --short 必须是空的；克隆 HEAD 试一次 import"
```

### 提交后必须验证（这一步是这次漏提交的真正修复）

```bash
# 1. 工作区必须干净——还有东西剩下就说明又漏了
git status --short          # 预期：空

# 2. 把 HEAD 单独克隆出来试着 import，这是唯一能发现「文件没进库」的办法
#    工作区里文件都在、测试全绿，只有干净克隆才会暴露
rm -rf /c/tmp/headcheck
git clone -q --no-hardlinks . /c/tmp/headcheck
PY=/c/Users/test/miniforge3/envs/ov_env_py312/python.exe
PYTHONPATH=/c/tmp/headcheck/src $PY -c "from dna.produce import produce, DISPLAY_ORDER; print('HEAD import ok')"
rm -rf /c/tmp/headcheck
```

预期最后一行输出 `HEAD import ok`。

---

## 清理（2026-09-04）：干净检出跑不过的那个测试 + 四处冗余

补提交之后做了一次干净检出验证，发现**没有 `.env` 的克隆上有 1 个测试失败**，
顺带查了一遍冗余。

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant
PY=/c/Users/test/miniforge3/envs/ov_env_py312/python.exe
$PY -m pytest              # 预期 753 passed
$PY -m frontends.cli.main doctor   # 预期 0 FAIL
git status --short
```

```bash
git add src/dna/core/config.py         src/dna/core/doctor.py         src/dna/narration/script_builder.py         src/dna/narration/longform.py         src/dna/pipeline/flow.py         src/dna/produce/__init__.py         src/dna/produce/service.py         src/dna/produce/tasks.py         src/dna/store/intake.py         frontends/nicegui_app/actions.py         tests/core/test_doctor.py         docs/03_unit_tests.md         docs/git_commands.md

git commit -m "refactor: 修干净检出跑不过的测试，去掉四处冗余

doctor.run_all 加 env_file 参数，测试注入临时 .env
  .env 按约定不入库，干净克隆里没有它，check_env_file 于是 FAIL
  写死路径的话这个检查读的是开发机上那个未跟踪的文件——
  测试「在我机器上过、在干净克隆里挂」，而挂的原因和被测代码毫无关系
  已验证：无 .env 的干净检出上 753 passed（此前 1 failed）
  另加一条反向测试：.env 不存在时必须是阻塞项，不能降级为警告——
  没有 .env 就没有 API key，所有花钱的节点都跑不了

config 新增 safe_profile()，替掉四份抄来抄去的 _safe_profile
  pipeline/flow、store/intake、produce/service、nicegui_app/actions 各有一份，
  四份差别只有日志文案，而界面那份还漏了日志
  文档里写清什么时候不该用它：dna config 这类「就是要显示配置对不对」的地方
  要用 load_profile 让错误浮出来

narration: _article_block 两份合一，改为公开的 article_block
  longform 那份漏掉了「正文为空时禁止编造」这条——两份同样的东西一定会漂移，
  而漂移的方向偏偏是把安全约束丢掉
  （实际走不到：can_build_longform 要求正文 ≥800 字。但这是运气不是设计）

删掉三个零引用的函数：
  actions.kind_label —— 只是 spec(kind).label 的包装
  actions.run_refetch —— 界面从没接过这个按钮；核心的 refetch_article 保留，
    哪天要加「重抓」包一层就有（CLI 的 dna refetch 一直可用）
  produce.available_kinds —— 设计上给界面禁用按钮用，但它每次还要再查一次库，
    而表格里 record 已经在手上，ledger_table 用的是本地一行判断

tasks.py: 删掉没用到的 Callable 导入，并修正模块文档
  文档写着「每个任务声明四件事：输出文件名、生成函数、前置依赖、是否进批量」，
  但 TaskSpec 里从来没有「生成函数」这个字段——分派在 service._generate
  放在 tasks 里会让它反向依赖 narration 与 pipeline，而它现在是一张
  零依赖的纯数据表，两个前端都能安全导入

测试: 753 passed（+1 反向测试），干净检出同样 753 passed"
```

提交后跑一遍文件开头那两条检查。

---

## 阶段：P4 期次落盘与装配（2026-09-04）

P4 原计划的「台账 DB + 条目级落盘」已在 P2+ 与 P3.5 提前交付，本轮补的是
**期次级**那一半：期次目录规则、`_references.md`、装配层、`dna issue`。

> ⚠️ **P4 与 P4.5 合成一个提交，命令在下一节。**
>
> 两个阶段是同一次开发里连着做的，改到了同一批文件
> （`frontends/cli/main.py`、`tests/core/test_doctor.py`、四份文档）。
> 拆成两个提交的话，`git add <文件>` staged 的是**文件当前的全部内容**，
> P4.5 的改动会跟着进 P4 那个提交——而 `doctor.py` 留在第二个提交里，
> 于是**第一个提交的测试跑不过**（`test_doctor` 里那条新测试找不到对应实现）。
>
> 这正是本文件开头那两条检查要防的东西：中间那个提交单独 checkout 出来是坏的，
> 而工作区里一切正常，不跑干净检出根本发现不了。
> 与其造一个已知会坏的历史，不如合成一个提交。

本轮改了什么，见下一节提交信息的前半部分。

## 阶段：P4.5 语音合成层（2026-09-04）

应用户要求把 TTS 独立成阶段：「后面所有需要输出音频的部分都需要依赖这个功能」。
同时适配 PyTorch CUDA 后端，以支持不同部署环境。

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant

# --- P4：期次落盘与装配 ---
git add src/dna/store/issue_store.py
git add src/dna/store/__init__.py
git add src/dna/core/naming.py
git add src/dna/core/__init__.py
git add src/dna/extract/media.py
git add tests/store/test_issue_store.py
git add tests/core/test_naming.py
git add tests/extract/test_extract.py
git add docs/05_output_spec.md
git add docs/12_digest_guide.md

# --- P4.5：语音合成层 ---
git add src/dna/tts/
git add src/dna/produce/documents.py
git add src/dna/produce/service.py
git add src/dna/produce/tasks.py
git add src/dna/core/config.py
git add src/dna/core/doctor.py
git add frontends/cli/main.py
git add frontends/nicegui_app/actions.py
git add frontends/nicegui_app/detail_panel.py
git add frontends/nicegui_app/ledger_table.py
git add tests/tts/
git add tests/produce/test_service.py
git add tests/core/test_doctor.py
git add pyproject.toml
git add .env.example
git add docs/14_tts_guide.md
git add docs/00_STAGE_SUMMARY.md
git add docs/02_development_plan.md
git add docs/03_unit_tests.md
git add docs/13_workbench_guide.md
git add docs/git_commands.md

# ⚠️ docs/issues/ 是 gitignored，009 不要 add
git status --short          # 预期：上面这些都在 staged，且没有 .env / outputs/

git commit -m "feat: P4 期次落盘与装配 + P4.5 语音合成层

（两个阶段连着做，改到同一批文件，拆开提交会让中间那个提交跑不过测试）

═══ P4：期次落盘与装配 ═══

新增 store/issue_store.py：期次目录只放整期产物，条目按 id 引用
  期次目录里不出现任何条目资产。一篇文章可以进多期（跨日的后续报道），
  复制会产生两份各自漂移的副本：重做了条目的口播稿，期次里那份还是旧的，
  而且没有任何提示。按 id 引用只有一处真相，重做之后期次这边自动就是新的
  条目 id、台账主键、DigestEntry.id 都是 url_hash(url)，三者互相查得到

_references.md 是发布时的合规依据
  每一张图、每一段视频的原始地址都要在这里查得到
  与条目目录里的 references.md 各管一件事：那份记下载下来的文件名，
  这份记本期用到了谁并指路过去。本地文件名会随重抓变化，原始地址才是署名要引的

未能定位的条目不静默跳过
  条目目录被手工删掉时链接会指空。少写一行链接是最坏的处理方式——
  产物看起来完整，实际缺了出处。三处同时报：文件末尾列清单、
  SavedIssue.unresolved 带结构化结果、dna issue 那一列显示「未定位」
  台账记着目录但目录已不存在，同样算未定位：写一个点开是 404 的链接比不写更糟

CLI: dna digest 改调 save_issue（前端不再自己拼路径），新增 dna issue
  dna issue --refresh 只重建 _references.md，不碰 _digest.json、不调 LLM
  重抓会改标题→改 slug→改目录名，期次里的链接因此指错；
  修链接是零成本操作，而重跑流水线要花钱，两件事不能写在一个命令里

naming: 删掉 topic_dir_name 与 history_dir_name
  topics/ 与 _history/ 两套布局都已取消（§9.2 §9.3），
  留着会让模块文档变成一份描述作废布局的「权威说明」，比没有更糟

extract/media: 词边界正则末尾加 s?，拦住复数装饰目录
  实测 20260902 那期 arXiv 条目的 5 张「配图」全是页脚装饰，其中 4 张走
  /images/icons/social/...；词表里有 icon，但边界要求其后是非字母数字，
  icons 因此整个漏过去。同时删掉 funders/sponsors/partners 三个手写复数
  这个缺陷是 _references.md 暴露的：把每张图的地址平铺出来才看得见

文档: 新增 05_output_spec.md 作为落盘路径的唯一权威

═══ P4.5：语音合成层 ═══

新增 src/dna/tts/：与 dna/llm/ 平级的能力层
  qwen3_ov     OpenVINO IR，跑 Intel CPU/核显/NPU（本机已验证）
  qwen3_torch  PyTorch，跑 NVIDIA CUDA / cpu / mps
  两者调用签名完全一致，公共部分（分段·拼接·停顿·进度·逐段容错）在
  qwen3_base.py 只有一份，各自只实现「怎么加载」——加一个部署环境只多一个 _load_model()
  换环境改 .env 里的 TTS_PROVIDER 一行，produce/ 与两个前端一行不动

让 TTS_DEVICE 真的生效
  上游 qwen_3_tts_helper 把 talker 与 speech tokenizer 的设备写死成 GPU
  （tmp_device = \"GPU\"），传进去的 device 只对 speaker encoder 生效——
  照原样调用的话 .env 里那一行是死配置
  这个项目已经栽过一次同样的跟头（profile.yaml 的时长区间改了没反应）
  加载时把两个类临时换成固定设备的子类，用完在 finally 换回去；
  声码器保持 CPU，与上游一致——本机验证过的就是这个配置
  已验证：TTS_DEVICE=CPU 时 talker.device == 'cpu'

三种音频进 productions 矩阵，复用已有的全部护栏
  shortvideo_audio / narration_audio / longform_audio，各自 requires 对应的稿子
  已存在不重跑 · 失败也记账 · 逐格重做 · GUI 矩阵，一条都不用重写
  TaskSpec 加 audio_of 与二进制载荷；_generate 的元组返回改成 Generated 结构体
  （加一种载荷时改一个字段，而不是改所有调用点的解包）

前置一律不 force —— 修掉一个会偷偷计费的默认值
  原先给前置传的是 force=force，也就是说点「重新合成音频」会连带把口播稿
  重新调一遍 LLM。下游重做是免费的，上游重做是要花钱的，
  让一个动作同时触发两者，等于把「免费」按钮偷偷接上账单

produce/documents.py：把渲染与解析放在一起
  产物抬头里有 `> 口播文案　·　来源：https://…`。整篇送进 TTS 的话，
  模型会把网址一个字符一个字符念出来——不是音质变差，是整段废掉
  而且这条失效不会报错：文件生成了、时长也有，只有听的人才发现
  所以 script_block（写）与 spoken_text（读）放进同一个文件，由往返测试钉住

一段失败不毁整篇
  长文案有几十段，跑到第 40 段崩掉就把前面 39 段的半小时赔进去
  缺段照样落盘但标记 complete=False 并报出缺了几段——
  残缺的音频被当成成品发出去才是最坏的结果

GUI：合成按钮不用琥珀色
  琥珀色在这个界面里专表示「这会计费」。音频跑在本地不花钱，
  用同一个颜色会让「花钱」这个信号贬值；tooltip 里写的是预估等待时间
  预计超 5 分钟先弹确认框；合成期间提示条显示「12/47 段（已产出 83 秒）」——
  半小时的等待只给一个转圈，人分不清是在跑还是卡死了

doctor：TTS 检查只看当前配的那个后端 + 新增 qwen_tts 包检查
  Intel 机器上没有 CUDA 权重是正常的，报成问题只会让人学会忽略 doctor
  qwen_tts 是 editable 安装的，源码仓库从 openvino_notebooks 搬到 Models 之后
  安装记录就指向了不存在的路径，而 ModuleNotFoundError 完全看不出是搬家导致的
  新增 QWEN3_TTS_REPO_DIR 兜底，并由这条检查把原因说出来

CLI：dna tts（检查后端 / --say 试听）+ dna produce --kind *_audio

测试: 802 passed（P4 +15 期次装配 +1 复数目录 -3 naming；P4.5 +29 tts +6 音频 +1 doctor）
真机验证: dna issue --refresh 对 20260902 期，5 条全部定位到
          dna tts --say 46 字 → 8.4 秒音频，RTF 2.67
          dna produce decef4a4 --kind narration_audio → 827 字 → 153 秒
          零费用（全程本地）

已知偏差: 口播稿估算 93.9 秒，实际合成 153.3 秒，差 63%
          实测 Qwen3-TTS 中文语速 5.4 字/秒
          本阶段不改：发布加速倍率未定，且改语速会连带推翻 issue 008
          刚校准过的字数。见 docs/issues/009（gitignored，本地留档）

qwen3_torch 后端: 本机 torch 2.8.0+cpu 且无 N 卡，交付代码与离线单测，
                  真机联调在有卡的机器上做——与飞书机器人（P9）同一个处理方式"
```

提交后跑一遍文件开头那两条检查。**本轮新增了两个包目录**
（`src/dna/tts/` 与 `tests/tts/`），第 2 条尤其要跑——`__init__.py` 是最容易漏的文件。

---

## 阶段：P3.5 quater 实测返工（2026-09-06）

> 已提交为 `c0e4814`，**27 个文件一个 commit**——本阶段是最后一次这么提交。
> 下一阶段起按上面的[分步提交](#分步提交2026-09-06-起)执行。
> 这一轮如果重来，会切成五步：
> 台账（db/ledger + 其测试）→ 文案层（narration/pipeline + 其测试）→
> 产物层（produce/config/profile + 其测试）→ 界面与 CLI → 文档。

用户实测报了六条：两个**没有任何报错的失效**（重做拿回旧答案、NEW 过夜消失），
四条功能调整（修改指令、表头字号、英文并入总结、文稿加语言开关）。

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant

# --- 核心 ---
git add src/dna/store/db.py
git add src/dna/store/ledger.py
git add src/dna/produce/tasks.py
git add src/dna/produce/service.py
git add src/dna/narration/duration.py
git add src/dna/narration/script_builder.py
git add src/dna/narration/longform.py
git add src/dna/pipeline/summarize.py
git add src/dna/pipeline/translate.py
git add src/dna/tts/factory.py
git add src/dna/core/config.py

# --- 前端 ---
git add frontends/cli/main.py
git add frontends/nicegui_app/actions.py
git add frontends/nicegui_app/ledger_table.py
git add frontends/nicegui_app/detail_panel.py
git add frontends/nicegui_app/theme.py

# --- 配置与测试 ---
git add config/profile.yaml
git add tests/produce/test_service.py
git add tests/narration/test_duration.py
git add tests/store/test_ledger.py
git add tests/frontends/test_workbench.py

# --- 文档 ---
git add docs/00_STAGE_SUMMARY.md
git add docs/03_unit_tests.md
git add docs/06_prompt_spec.md
git add docs/07_db_schema.md
git add docs/13_workbench_guide.md
git add docs/git_commands.md

# ⚠️ docs/issues/ 是 gitignored，010 不要 add
git status --short          # 预期：上面 27 个都在 staged，没有多余的

git commit -m "fix+feat: 实测返工 —— 重做绕开缓存、NEW 按来源判定、语言成为产物维度

═══ 两个没有任何报错的失效 ═══

重做调了 LLM 但内容一字未变（看起来像「生成了没保存」）
  LLM 响应缓存故意不设过期，键是 provider + 模型 + 完整提示词——
  同样的输入永远给同样的输出，这正是它平时的价值
  但重做的字面意思就是要一份不一样的：提示词没改、temperature 是写死的，
  于是缓存命中，模型确实被调了、文件确实被重写了，内容却一字未变
  台账里留着痕迹：此前两次重做的字数都是 258，一模一样
  修法：force=True 时构造不带缓存的 provider
  顺带修掉两个放大它的因素：刷新后保持展开（原先面板会关掉，
  而那正是最想看新内容的时刻）；新增「修改指令」（改了提示词自然错开缓存）
  真机复验 1 次调用：内容确实变了，新版还多带出一个原文里的事实

NEW 标识过一夜就消失
  判定原本是「最近 24 小时内导入 且 零产物」。实测 fe3b7d3c 导入 47 小时、
  零产物，被时间窗清掉了
  时间窗当初压的是泛滥（37 篇里 32 篇零产物，全挂 NEW 等于没有标识），
  但它压错了维度——把「昨天粘的、今天还没处理」这类最需要标识的行清掉了
  改成看来源：人工投递（gui/inbox）的是「我特意要处理的」，RSS 抓的是候选池
  实测 37 行里 5 行挂 NEW，既不泛滥也不会过夜清空
  Profile.new_badge_hours 与 profile.yaml 里对应那行一并删除——
  判定里不再有时间成分，留着就是个改了没反应的死配置

═══ 语言成为产物的一个维度（台账 v4）═══

summary_zh + summary_en 合并成 summary + lang
  它们本来就是同一份东西的两个语言版本，却占了表格两列
  要给短视频/口播/长文案都加英文版时这个建模撑不住：照原样得再造三个 kind
  加各自的音频，枚举翻倍而语义没变清楚一点
  (article_id, kind, lang) 现在才是一份产物的完整标识；文件名走 filename_for(lang)
  迁移把 9 行历史数据改写到位

查询必须带语言
  漏掉的话，生成过英文版之后再查中文版会拿到英文那一行——
  「已存在就跳过」于是拿英文版冒充中文版，一次都不会报错

英文文稿原生生成，不翻译
  中文 30 秒的稿子翻成英文不是 30 秒的稿子，而时长正是这三种文案的验收标准，
  刚按人工参考稿校准过一轮（issue 008）。走翻译等于把那一轮作废
  英文写作要求逐条重写而非直译：它是给模型看的指令，用目标语言写遵守得明显更好；
  且「白话」「套话」直译过去会变成空泛的 avoid vague language，
  等于把最要紧的那条规则说没了

英文预算不乘混排系数（这是「英文文本量太大」的根因之一）
  1.5 量的是「中文字数 vs 中英混排稿的实际字符数」，英文单位本来就是词
  照样乘会让英文稿超长 50%
  英文总结另外收紧：提示词从「与原文长度相当」改成至多 45 词，
  schema 上限 800 → 400——提示词是建议，schema 是约束，
  放着一个两倍于目标的上限等于告诉模型写到 800 也算合格

长文案每一节都重复一遍语言声明
  它是分十几次调用拼起来的，只在提纲那次说「用英文写」，
  后面几节的模型看不到那句话，会跟着中文原文滑回中文——
  拼出来是中英夹杂的半成品，而这时钱已经花完了

═══ 修改指令 ═══

produce(instructions=...) 接在提示词末尾，优先级高于默认要求
  放最后是因为后出现的指令权重更高，而这段的用途正是覆盖默认要求
  空指令时返回空串，提示词逐字不变——否则每篇都会因为多一个空标题
  而错开 LLM 缓存，白花一轮钱
  指令记进 productions.instructions，界面下次自动预填：
  调稿子是渐进的，不该每次都要人回忆上次改了什么

═══ 界面 ═══

表头 11px 灰色 → 13px 加粗白色，下边框加重
  它是「哪一列是哪个功能」的唯一答案，读不到的表头等于没有表头
格子上加 EN 标记：收起状态下语言开关看不见，这是唯一的「已有英文版」信号
刷新后自动展开原来那一格

测试: 813 passed（+10）
  两条无报错失效都钉在「意图」而不是「实现」上：
  重做那条断言传给 get_llm 的 cache 参数，NEW 那条把入库时间推到一年前
真机复验: dna produce decef4a4 --kind summary --force（1 次调用）
界面验证: Playwright 截图，零费用"
```

提交后跑一遍文件开头那两条检查。**本轮改了台账 schema（v3 → v4）**，
干净克隆跑测试时会新建库、直接建到 v4；而你自己那份库是迁移上来的，
`dna list` 能正常出结果就说明迁移没问题（已验证：9 行 summary_zh/en 全部改写到位）。

### 追加：实测第二轮的三处修正（同一个提交，文件已在上面的 add 列表里）

`service.py` / `detail_panel.py` / `ledger_table.py` / 两个测试文件都已包含在上面，
**不需要再 add 别的**。若想把这三处单独说明，在 commit message 末尾补一段：

```
═══ 实测第二轮 ═══

「修改指令」关掉后仍然生效
  输入框的文字与开关状态原本是同一个字段，关掉只把框藏起来，
  预填的上一句要求照旧进提示词。台账坐实：14:45 那次无指令，
  之后三次都记着「长度增加到40s」——人只写过一次
  改成 use / instructions 两个字段：关掉是「这次不发」，不是「删掉」

台账字数比文件里写的多两百
  chars 记的是整个文件长度，抬头带着标题和整条 URL。
  400 字的口播稿记成 600 多，与同一格的预估秒数、文件里那行
  「约 N 秒 · M 字」三个数字互相打架。改成只数正文
  （历史行仍是旧数字，重做一次即刷新）

重做比首次慢：不是错觉，是多花的调用
  6.5s/2 次 → 11.9s/3 次 → 8.9s/3 次。重做不走缓存（本就是设计），
  而回炉次数随「指令要 40 秒 vs 窗口 25~35 秒」的冲突涨到上限
```

---

## 阶段：改用分步提交（2026-09-06）

用户要求：**每一步都要包含 `git add` 和 `git commit`**，按代码功能分步，
不再一个大而全的 add 块。规则写在本文件开头的[分步提交](#分步提交2026-09-06-起)
与 `00_STAGE_SUMMARY.md` 要求 5b（Skill 与 memory 同步更新，不在仓库里）。

本轮只动文档，一步就够：

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant

# --- 步骤 1/1 · 提交守则 ---
git add docs/git_commands.md
git add docs/00_STAGE_SUMMARY.md

git commit -m "docs: 改用分步提交，一步一个功能面

- git add 与 git commit 成对出现，按文件边界自下而上切：
  store → narration/pipeline → produce → frontends/CLI → docs
- 按改动主题切要 git add -p，挑错一块就是编译不过的提交
- core/config.py 与 config/*.yaml 这类同生共死的文件必须同一步"

git status --short          # 预期：空
```

---

## 阶段：TTS 改为调用独立服务（2026-09-08）

删掉本项目内的全部 TTS 实现，改为调用 `Agent_TTS_Module` 提供的 TTS service。
**七步，自下而上**（协议 → 客户端/看门人 → 预处理 → 工厂 → 产物层 → 前端 → 文档配置），
每一步都能单独通过 `python -m pytest -q`。

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant

# --- 步骤 1/7 · 删掉本地 TTS 后端，协议保留 ---
git rm src/dna/tts/qwen3_base.py src/dna/tts/qwen3_openvino.py src/dna/tts/qwen3_torch.py
git add src/dna/tts/base.py

git commit -m "refactor(tts): 删除本地 OpenVINO/PyTorch 后端，只留协议

- 同一件事维护两份实现，两边的音质、参数与失败处理迟早对不上；
  权重轮动/失控拦截/克隆/字幕都已在 Agent_TTS_Module 里做完并实测
- AudioClip 增加 run 与 artifacts：服务端留下的逐段 wav 与字幕要能带回来
- 协议一行未改，这正是当初按「将来会换成服务」设计的结果
- RTF_ESTIMATE 注释写明它是 0.6B 核显上的数量级，1.7B CPU 实测约 13"

# --- 步骤 2/7 · HTTP 客户端与服务看门人 ---
git add src/dna/tts/client.py
git add src/dna/tts/supervisor.py

git commit -m "feat(tts): 服务客户端 + 探活与自动拉起

- client：/health /info /voices /tts/synthesize /gui/handoff /outputs
  本机地址关掉 trust_env——公司代理会把 127.0.0.1 也代理走再回 502，
  报错看起来像服务挂了，而服务好得很（sources/http.py 踩过同一个坑）
- supervisor：不在线就起 agentic_tts.cli serve，最多 3 次，之后上报不可用
  第一次可能只是冷启动慢，第二次排除端口竞争，第三次失败就是环境问题
- 子进程日志写 data/logs/tts_service.log：拉起失败时原因只在那里
- Windows 上子进程脱离控制台组，否则 Ctrl+C 工作台会连带杀死共用服务
- 远程地址一律不代为启动，并在报错里说清是这个原因"

# --- 步骤 3/7 · 朗读友好化（一次 LLM 调用）---
git add src/dna/tts/preprocess.py

git commit -m "feat(tts): 送进 TTS 前用一次 LLM 做朗读友好化

- 一次调用解决四类问题：型号/公式/符号的读法、多音字同音替换、
  合理断句、插入 [pause:400ms]
- 不堆规则表：规则永远穷举不完，还会互相干扰
  （4060 改成四零六零之后，4060 Ti 又不对了）
- 三条护栏：调用失败退回原文；长度偏离 0.6~1.8 倍之外丢弃改写结果
  （那说明模型自己续写或大段删除，音频里多念不存在的内容更严重）；
  TTS_PREPROCESS=false 可彻底关掉
- 这次调用属于本项目，TTS service 是纯 TTS、自己从不调 LLM"

# --- 步骤 4/7 · 工厂与包出口 ---
git add src/dna/tts/factory.py
git add src/dna/tts/__init__.py

git commit -m "refactor(tts): 工厂只产服务 provider，音色默认值改用服务端写法

- provider 按服务地址缓存：它缓存了 /info 与音色清单，重建就要多两次往返
- 默认音色 Serena / Uncle_Fu（服务端内置 speaker 的大小写）
- 小写音色名按服务端清单纠正——大小写不符会在加载完权重之后才报错，
  那时已经白等了几十秒"

# --- 步骤 5/7 · 服务 provider 与产物层接线 ---
git add src/dna/tts/service.py
git add src/dna/produce/service.py
git add src/dna/produce/__init__.py

git commit -m "feat(produce): 音频改走 TTS 服务，逐段合成并收回产物

- 一段一个请求，不用 /tts/batch：整批一个请求界面只能看转圈，
  中途失败一段就整批失败；逐段发才能报「第 7/23 段」并只丢失败那段
- 拼接留在本侧（协议原样）：段间 0.25s、换角色 0.5s；用标准库 wave，
  不为搬运帧数拉 numpy 依赖
- 抽出 _build_segments，供流水线与「交给 TTS 界面」共用——
  否则界面里看到的分段和自动合成出来的对不上
- 新增 import_audio()：收下在 TTS 界面生成的音频并记台账，
  不记的话那一格仍显示未生成，人会以为界面里忙半天的成果丢了
- 逐段音频写进 <文章目录>/tts/<run>/，用手上的字节而不是回头下载：
  服务端按「段号+音色」命名，同一音色出现两次会互相覆盖（实测踩到）"

# --- 步骤 6/7 · 工作台：动态进度与「高级配置」---
git add frontends/nicegui_app/audio_progress.py
git add frontends/nicegui_app/actions.py
git add frontends/nicegui_app/detail_panel.py
git add frontends/nicegui_app/ledger_table.py
git add frontends/cli/main.py

git commit -m "feat(frontends): 音频进度浮窗 + 高级配置跳转 TTS 界面

- audio_progress：秒表 + 进度条 + 已产出秒数 + 按实测速度推的剩余时间。
  静止的转圈只能回答「在跑吗」，而且卡死的页面上它照样在转（那是 CSS 动画）
- 段数未知前保持不确定态，不画 0%——0% 会被读成「卡在开头」
- 高级配置开关：交接单 POST 给服务、新标签页打开 TTS 多段界面、
  右下角每 4 秒轮询（上限 90 分钟），出活后拷回产物并记台账
- 轮询而不是回调：TTS 可能在另一台机器上，不该知道工作台的地址
- dna tts 先探活再列音色；合成结果把 音频/耗时/RTF 分开报"

# --- 步骤 7/7 · 配置、体检、文档与测试 ---
git add src/dna/core/config.py
git add src/dna/core/doctor.py
git add .env.example
git add pyproject.toml
git add tests/conftest.py
git add tests/tts/test_tts.py
git add tests/core/test_doctor.py
git add tests/produce/test_service.py
git add docs/14_tts_guide.md
git add README.md
git add docs/git_commands.md

git commit -m "chore(tts): 配置项换成服务地址，体检改查服务，文档与测试跟上

- 配置：TTS_SERVICE_URL / TTS_GUI_URL / TTS_MODULE_DIR / TTS_AUTOSTART /
  TTS_START_ATTEMPTS / TTS_REQUEST_TIMEOUT / TTS_PREPROCESS，
  删掉 TTS_PROVIDER 与四个 QWEN3_TTS_* 路径
- TTS_REQUEST_TIMEOUT 默认 900s：CPU 上一段 120 字要三四分钟，
  短超时会在服务正常工作时把请求掐掉
- doctor：check_tts_model/check_tts_repo → check_tts_service，
  「不在线但能自动拉起」报 OK（那是正常状态，报成问题会让人去做本已自动的事）
- pip install -e .[tts] 现在只装 httpx：没有 torch/openvino/transformers
- 测试夹具关掉 TTS_PREPROCESS：它是一次真的 LLM 调用，
  开着会把「音频不重新计费」这类断言搅浑；预处理本身单独测
- 全量：819 passed, 3 deselected"

git status --short          # 预期：空
```

**手动验证（提交前后都可以跑）**：

```bash
dna doctor                          # 「TTS 服务」一项
dna tts                             # 探活 + 自动拉起 + 列音色
dna tts --say "今天的人工智能资讯" -o /c/tmp/tts-check.wav
dna gui                             # 展开口播格 → 合成音频 / 高级配置
```

### 补一步 8/8 · 三处返修（2026-09-08）

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant

git add frontends/nicegui_app/actions.py
git add frontends/cli/main.py
git add src/dna/core/config.py
git add src/dna/tts/base.py src/dna/tts/factory.py
git add src/dna/tts/client.py src/dna/tts/service.py
git add .env.example
git add docs/14_tts_guide.md docs/git_commands.md

git commit -m "fix(tts): 高级配置 NameError；默认改用音色克隆

- actions.py 漏了 get_tts / TTSError 的 import，「高级配置」点开就 NameError
- 默认发声方式改为 voice_clone，克隆 data/ref_audio/qwen3-tts-cpu.wav：
  内置音色跟着权重变（同一个 Serena 在 0.6B 与 1.7B 上不是同一把嗓子），
  换 checkpoint 声音就变且不报错；克隆锁的是一个文件
- VoiceSpec 增加 mode / ref_audio / ref_text / x_vector_only：
  显式带上而不是让后端按「有没有 ref_audio」猜——参考音频不存在时
  那种推断会静默降级成另一把嗓子，而那正是最该报错的时刻
- TTS_REF_TEXT 留空即走纯 x-vector：不知道原话时编一句会让克隆明显变差
- 嘉宾默认仍用内置音色：都克隆同一个文件等于没做双角色
- 克隆模式不再往服务端传音色名，服务端视其为冲突参数并报错
- 远程服务自动把参考音频转 base64：路径在服务端解析，
  远程会报「参考音频不存在」，看起来像文件丢了，其实是机器不对
- dna tts 打印发声方式；交接单只在内置音色时带音色名"

git status --short          # 预期：空
```

### 补一步 9/9 · 第三轮返修：一个进程、字幕、原地覆盖（2026-09-08）

```bash
cd /c/Users/test/Downloads/xkd/DailyNewsAssistant

git add src/dna/core/config.py
git add src/dna/tts/supervisor.py
git add src/dna/tts/subtitle.py
git add src/dna/tts/base.py src/dna/tts/service.py
git add src/dna/produce/service.py
git add frontends/nicegui_app/audio_progress.py
git add frontends/nicegui_app/actions.py
git add frontends/nicegui_app/detail_panel.py
git add frontends/cli/main.py
git add .env.example
git add tests/tts/test_tts.py tests/produce/test_service.py
git add docs/14_tts_guide.md docs/git_commands.md

git commit -m "perf(tts): 界面挂在服务上（一份权重）；默认导出字幕；一篇一个文件夹

- 不再把 TTS 界面当第二个应用拉起：它挂在服务的 /gui 上，同进程同端口、
  **共用一份权重**。先前两个进程各加载一份 1.7B，8 GB 卡顶满，
  而且点一次「高级配置」要等第二个进程冷启动
- 配置 TTS_GUI_URL → TTS_GUI_PATH（默认 /gui）；前者保留为单独部署时的覆盖项
- 默认合成路径导出 SRT：与音频同名同目录（narration_audio.srt）。
  时间轴含段间静音，逐段时长按波形量（字幕误差是累积的），
  控制标记不进字幕，UTF-8 带 BOM（否则剪映读中文乱码）
- 一篇文章一个文件夹、重做覆盖：产物目录不再按 run/时间戳分层，
  服务端 run 名也按「文章+产物+语言」固定 —— 分层之后哪份是最新的只能靠人比时间
- 高级配置的等待改用与本地合成**同一个进度浮窗**：TTS 界面每生成一段就把
  done/total 写回交接单，进度条据此推进
- 交接单带 return_url（取自浏览器地址栏）：那边生成完自己关掉标签页／跳回工作台，
  产物拷进文章目录，并在那一格下面留一句「已由 TTS 界面生成并收下」
- 逐段生成只报进度，只有「全部生成/合并」才算完成 —— 否则会把半成品收走
- TTSProvider.synthesize 增加 run 参数；假 provider 与音色用例跟上"

git status --short          # 预期：空
```

