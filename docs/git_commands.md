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

## 阶段：P4 落盘与产出台账

> 待 P4 完成后补充。
