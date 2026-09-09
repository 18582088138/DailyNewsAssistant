# 提示词 / Prompts

**这个目录是提示词正文的唯一来源。** 代码里不再有提示词字符串——
`src/dna/core/prompts.py` 负责读，`docs/06_prompt_spec.md` 记为什么这么写。

改一句话的完整流程：

```bash
# 1. 改文件（比如把「信息密度优先」那条改得更狠）
#    config/prompts/_shared/professionalism.zh.md

# 2. 看真正发出去的提示词长什么样——零 LLM 调用，不花钱
dna prompt 97233627 -t narration

# 3. 真机跑一次看产物——**会计费**
dna prompt 97233627 -t narration --run

# 4. 满意了就正式生成
dna produce 97233627 -k narration --force
```

第 2 步看到的提示词与第 4 步真正发出去的**逐字节相同**：两条路都走
`dna.produce.service.generate_text()`，只是前者把 provider 换成了一个
只记录不作答的假 provider。所以在调试台里验证过的提示词，在应用里必然生效。

---

## 文件清单

| 文件 | 任务 | 调用次数 |
|---|---|---|
| `summarize.md` | 条目摘要（`dna digest` 每条一次 · `produce -k summary`） | 每条 1 次 |
| `translate.md` | 中→英翻译（英文摘要、趋势段落） | 每 10 条 1 次 |
| `trend.md` | 当日主线提炼（日报开篇 / 播客开场） | 每期 1 次 |
| `shortvideo.zh.md` · `shortvideo.en.md` | 短视频文案（25~35 秒） | 1~3 次（回炉） |
| `narration.zh.md` · `narration.en.md` | 口播文案（1~2 分钟） | 1~3 次（回炉） |
| `longform_outline.md` | 长文案提纲 | 每篇 1 次 |
| `longform_section.md` | 长文案分节展开 | **每节 1 次**（4~8 次） |
| `tts_preprocess.md` | 朗读友好化（型号、公式、多音字、断句） | 每段音频 1 次 |
| `_shared/professionalism.{zh,en}.md` | 三种文案共用的写作要求 | —— |
| `_shared/instruction_block.{zh,en}.md` | 「本次的额外要求」抬头 | —— |
| `_shared/language_directive.en.md` | 英文长文案每节都要重复的语言声明 | —— |

`_shared/` 下的是**片段**，被上面若干个任务引用，不单独调用。
改 `professionalism.zh.md` 会同时影响短视频、口播、长文案三种产物——
这正是它单独成文件的原因：改一处而不是六处。

`dna prompt --list` 打印这张表的实时版本（哪个任务读哪些文件）。

---

## 两条语法

### 占位符 `{{name}}`

```markdown
- **口播正文**控制在 **{{lo_chars}}~{{hi_chars}} 字**
- 最后一句固定收尾：「{{cta}}」
```

取值由代码传入。**模板里有、代码没给的占位符会直接报错**，不会静默把
`{{cta}}` 原样发给模型（那一串会照样计费，还会印进产物）。
所以不要自己发明新占位符——加一个就得同时改调用它的那个模块。
`dna prompt --list` 之外，`python -c "from dna.core.prompts import placeholders_of; print(placeholders_of('narration.zh'))"`
可以查某个文件用了哪些。

**为什么是双花括号。** 提示词里到处是 `P(A|B)`、`{"spoken": "…"}`、
`[pause:400ms]` 这类举例用的字面量。用 Python 的 `{name}` 语法就得让改提示词的
人记住转义规则，而改提示词的人不该需要懂转义。双花括号让单花括号原样穿过。

### 分块 `## @key`

整行 `## @key` 起一个新块；第一个标记之前的内容是主块（`main`）。
长文案的 专题/访谈 两种变体因此不必各存一份完整文件：

```markdown
（主块：两种模式共用的任务描述）

## @role.feature

- 输出为单个轮次，speaker 固定为 `narrator`

## @role.interview

- 输出为对话轮次，speaker 只能是 `host` 或 `guest`
```

行内出现的 `## @key` 不算标记，只有整行才算。

---

## 三条注意事项

**1. 换行是有意义的，不要为了排版折行。**
文件内容逐字节发给模型。提示词的编号列表靠换行成立，折掉就变成一段糊在一起的
文字。`_shared/instruction_block.en.md` 与 `_shared/language_directive.en.md`
里那两行很长，那是刻意的——它们在原来的代码里也是一行。

**2. 块的两端会被 strip，块内部原样保留。**
文件末尾的换行、块之间的空行都是排版而不是内容。

**3. 改完会 LLM 缓存 miss，这是对的。**
缓存键是 provider + model + 完整 messages（`src/dna/llm/cache.py`）。
改一个字就换一个键，于是拿不到旧答案——正是想要的行为，否则调完提示词还是
拿回改之前那一版。反过来说：**没改而重跑一次是免费的**，
`dna prompt --run` 在同一份提示词上重跑不会重复计费，
要另抽一个样本才需要 `--no-cache`。

---

## 别在这里做的事

- **不要把「为什么这么写」写进提示词文件**——那是给人看的，写进去会跟着发给模型
  并计费。调优记录、实测对照、踩过的坑都放 `docs/06_prompt_spec.md`。
- **不要删文件**。缺文件不会降级运行：`dna doctor` 会 FAIL，
  加载器会抛 `ConfigError`。这是刻意的——空的 system prompt 照样发请求、
  照样计费，只是产出是垃圾，而这种失败在日志里看起来像模型变笨了。
