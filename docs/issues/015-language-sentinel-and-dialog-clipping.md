# 015 · 表格产物全变「未生成」、展开面板 KeyError · 设置面板被裁掉 · `src/` 里落了数据

日期：2026-09-16 · 来源：用户验证 `DNA_v0.1` 的拆分改动时实测三条

## ① 语言哨兵漏到下游（回归，静默 + 崩溃各一半）

```
File "frontends/nicegui_app/detail_panel.py", line 134, in _render_summary
    ui.label(f"{task.label}　{LANGUAGE_LABELS[lang]}")
KeyError: ''
```

**根因不是拆文件，是批次 2 修「假配置」时留下的半截。**
`DEFAULT_LANGUAGE` 原先是 `tasks.py` 里写死的常量 `"zh"`：`.env` 里那一行、
设置面板里那个「默认语言」下拉全是摆设。修法是换成读配置的函数 `default_language()`，
于是所有 `lang: str = DEFAULT_LANGUAGE` 的默认参数只能改成 `lang: str = ""`
——**函数的默认值不能在导入时就去读配置**，否则又定死了一次。

空串从此是个**哨兵**，意思是「按 `.env` 的 `DEFAULT_LANGUAGE`」，它本身不是语言码。
问题是只有一部分函数在入口 `normalize_lang()`，漏掉的那些直接拿它用：

| 用法 | 后果 |
|---|---|
| 当字典键：`RowView.production()` 查 `productions[(kind, "")]` | 库里只有 `zh` / `en` → **整张表每一格都显示「未生成」，完全静默** |
| 当下标：`LANGUAGE_LABELS[lang]` | `KeyError: ''`，展开面板一点就崩，于是「文章用不了 LLM 功能」 |
| 跟默认语言比：`lang == default_language()` | 中文版的提示条被标成「（EN）」 |
| 直接当查询条件：`ledger.latest_production(id, kind, "")` | 查不到稿子 → 音频按钮禁用、字幕按钮禁用、修改指令不预填 |

**为什么全套测试都绿还漏了。** 渲染冒烟测试构造的 `RowView` 产物字典是空的，
走的是「查不到 → 画未生成」那条路；而崩溃在「查到了要画出来」那条路上。
展开面板要点开格子才渲染，那套测试又明写「不测交互」。两件事叠起来，
这个错在测试网里没有任何一处能碰到它。

### 修法与规矩

凡是 `lang` 参数默认为哨兵空串的函数，**入口第一句必须是 `lang = normalize_lang(lang)`**。
一刀切而不是「用得到的地方再归一化」——上一次就是靠人逐个判断哪里用得到，
判断漏了三处。多归一化一次是幂等的，代价是一次字典查找。

这条规矩由 `tests/produce/test_lang_sentinel.py` 静态把关：AST 扫 `src/dna` 与
`frontends`，凡是签名里有哨兵默认值的函数都会自动进用例，新写的函数不用登记。
它自己还带一个「至少得扫到十来个」的自检，防止匹配逻辑失效之后一个都没扫到还照样绿。

行为回归在 `tests/frontends/test_render_smoke.py`：
`test_有产物的行也能画`（带一条 `("summary","zh")` 的真产物记录）与
`test_展开面板能画出来`（直接调 `detail_panel.render`）。
去掉归一化那一行，这两个节点分别以断言失败和 `KeyError: ''` 变红。

## ② 设置面板没有滚动条，底下几项看不见

`.wb-dialog` 上本来就写了 `max-height: 88vh; overflow: auto`，注释还专门说了
「超高就自己滚」。可它**永远不会触发**：Quasar 的 `q-panel-parent` 是
`overflow: hidden`，分页内容比它高就直接裁掉，卡片本身高度没超，
`overflow: auto` 自然轮不到。

修法是让分页自己滚，而不是靠卡片整体滚：

```css
.wb-dialog-body { overflow-y: auto; max-height: 62vh; }
.wb-dialog-body .q-panel-parent, .wb-dialog-body .q-panel { overflow: visible; }
```

副作用是好的：底栏的「保存 / 取消」永远在屏幕上，不会随内容滚走。

**长期约束**：以后往设置面板加字段不用再担心高度——但
**任何新的 `ui.tab_panels` 都要挂 `wb-dialog-body`**，否则回到裁内容的状态。
`test_设置面板的分页能自己滚` 会在类名或那条 CSS 任意一个消失时变红。

## ③ `src/` 下出现了 `data/` 与 `outputs/`

`src/` 是包根，不是工作目录。用户看到的是 `src/data/logs/dna.log` 和一个空的
`src/outputs/`。

**根因已经修过了，这是它留下的残骸。** 仓库根原先靠 `parents[3]` 数目录层级，
`config.py` 拆成包之后文件深了一层，根就指到了 `src/`。那个日志文件里每一行都写着
`...\src\config\...`，时间戳全落在 15:58:43–15:59:13 那三十秒里——正是拆包到
`dna doctor` 抓出这个问题、改成**向上找 `pyproject.toml`** 之间的窗口。
顺带说明那三十秒里实际发生了什么：`profile.yaml` 找不到于是按默认值运行、
TTS 预处理提示词找不到于是降级用原文合成。`src/outputs/` 是空的，没有产物落错地方。

**为什么没人发现。** `.gitignore` 里的 `data/` 与 `outputs/` 不带前导斜杠，
**任何层级都匹配**，所以 `git status` 从头到尾一片干净。这类错只有人拿眼睛
看目录才会发现——正是最该交给工具的一类。

验收换成盯症状而不是盯成因：`tests/core/test_layering.py::test_src_下只有代码`
断言 `src/` 里只有包（有 `__init__.py` 的目录）和 `.py` 文件。不管是仓库根算错、
相对路径按 cwd 落盘，还是将来谁把缓存写进包里，都会在这里变红。
删掉残骸之前先跑过一次，它确实以那三条路径变红。

**顺带记一条**：`Settings.data_dir` / `output_dir` 的值是**相对路径**
（`data` / `outputs`），直接拿去落盘就会跟着 cwd 跑。全仓只有
`data_path` / `output_path` 两个派生属性在用它们（都过 `resolve()` 按仓库根解析），
**新代码也必须走这两个**。
