# issue 006 — P3 开发中发现的两个问题

> 日期：2026-09-02　状态：均已修复并有回归测试
> 两个都是**写测试时才暴露的**，不是真机验证发现的——记下来是因为它们暴露的
> 时机本身有参考价值。

---

## A. 台账数据库不跟随 `DATA_DIR`，测试写进了真实数据库

### 现象

给 P3 的取数层写测试时，用 `tmp_path` 构造了隔离的 `Settings`，但测试之间互相
污染：本该空库的用例读到了 23 条记录。

### 根因

`Settings` 里 `data_dir` 与 `db_path` 是**两个独立配置项**，`db_file` 按仓库根解析：

```python
data_dir: Path = Path("./data")
db_path: Path = Path("./data/dna.db")

@property
def db_file(self) -> Path:
    return self.resolve(self.db_path)      # ← 按 PROJECT_ROOT 解析
```

于是：

```
Settings(data_dir="/tmp/xxx/data")
  data_path → /tmp/xxx/data              ✅ 跟着走了
  db_file   → <项目>/data/dna.db          ❌ 没跟着走
```

**测试实际上在写真实的项目台账。** 事后核查确认，`dna stats` 里多出了
`s1` / `a` / `b` 三个来源共 4 条测试数据（`https://e.com/x` 之类），已清除。

### 为什么这不只是测试问题

真实场景同样会中招：把 `DATA_DIR` 改到别的盘，**文章落盘搬家了，台账留在原地**。
台账里每条记录的 `store_dir` 都指向新目录下不存在的位置，而且
**不会报任何错**——`dna list` 照常显示，只是 `dna show` 打不开内容。

这类「配置改了一半」的失效最难查，因为它看起来一切正常。

### 修复

台账相对路径按**数据目录**解析，绝对路径原样使用：

```python
@property
def db_file(self) -> Path:
    if self.db_path.is_absolute():
        return self.db_path
    return self.data_path / (self.db_path.name or "dna.db")
```

相对路径**只取文件名**，是为了让 `.env` 里现存的 `DB_PATH=./data/dna.db`
不会变成 `<data>/data/dna.db`。相对值在这里的实际用途只是给台账换个文件名；
要换位置用绝对路径表达更清楚。

回归测试：`tests/core/test_config.py` 三条（跟随 `DATA_DIR` / 绝对路径原样 /
旧写法不重复前缀）。

### 教训

**互相依赖的路径不该是两个独立配置项。** 一个能被单独改到不一致的配置，
迟早会被改到不一致。

---

## B. NFKC 归一化把中文逗号折成半角

### 现象

给清洗节点写测试时断言「中文标点原样保留」，直接失败：

```
assert normalize_text("模型发布了，性能很好。") == "模型发布了，性能很好。"
E   AssertionError: assert '模型发布了,性能很好。' == '模型发布了，性能很好。'
```

### 根因

`unicodedata.normalize("NFKC", text)` 会把全角标点折成 ASCII：
`，`(U+FF0C) → `,`、`：` → `:`、`（）` → `()`。

这与模块文档里写的「不改动标点——那属于内容，不属于格式」**直接矛盾**：
代码说的和做的不是一回事。

### 为什么这很要紧

产物是要发到公众号、小红书、知乎的**中文稿**。中文里混着半角逗号读起来就像机翻，
而这会一路带进摘要、图文稿、口播稿——**每一个发布形态都受影响**。

`。`(U+3002) 和 `、`(U+3001) 不在 NFKC 折叠范围内，所以只有部分标点会变，
产出的是「半角逗号 + 全角句号」这种更难看的混合，也更不容易一眼看出问题。

### 修复

不再全量 NFKC，改成显式的折叠表——只折「本来就是 ASCII、只是被打成了全角」的字符：

```python
_FULLWIDTH_FOLD = str.maketrans({
    **{chr(0xFF10 + i): chr(0x30 + i) for i in range(10)},   # ０-９
    **{chr(0xFF21 + i): chr(0x41 + i) for i in range(26)},   # Ａ-Ｚ
    **{chr(0xFF41 + i): chr(0x61 + i) for i in range(26)},   # ａ-ｚ
    "　": " ", "－": "-", "～": "~",
})
```

保留了原本的目的（`ＧＰＴ－４` 与 `GPT-4` 归一，否则去重会漏判），
同时中文标点分毫不动。

```
normalize_text("ＧＰＴ－４ 发布了，性能提升２０％。")
  → "GPT-4 发布了，性能提升20％。"
```

回归测试：`tests/pipeline/test_clean.py::test_fullwidth_alphanumerics_fold_but_chinese_punctuation_does_not`
逐个断言 `，。；：！？（）、「」《》—…` 都不被改动。

### 教训

**「归一化」这类现成函数的作用范围要逐字核对，不能按名字想当然。** NFKC 的 K
是 compatibility，它折叠的东西比「全角转半角」多得多。这次是文档先写对了、
代码写错了，测试照文档写才逼出来——**先写清楚意图再写断言**是有价值的。

---

## 相关

- 代码：`src/dna/core/config.py`、`src/dna/pipeline/clean.py`
- 测试：`tests/core/test_config.py`、`tests/pipeline/test_clean.py`
