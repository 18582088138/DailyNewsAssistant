# 016 改标题只改内容，不重命名产物目录

| | |
|---|---|
| 发现 | 2026-10-04，用户实测「新建文章 → 重命名标题」 |
| 影响面 | `custom_article.save_title`（以及任何将来改标题的路径） |
| 状态 | ✅ 已修 + ✅ 有测试 |

## 现象

新建一篇空白文章（标题是占位的「未命名文章」），在界面上把标题改成
`Omni-IO Skills开源，Harness让Agent长出7种模态`。结果：

```
outputs/articles/20261004/未命名文章__4111bfcf/meta.json   title = "Omni-IO Skills开源，…"
outputs/articles/20261004/未命名文章__4111bfcf/article.md  # Omni-IO Skills开源，…
                            ^^^^^^^^^^^^^^^^^^ 目录名还是占位标题
```

内容换成了新标题，**文件夹名没跟着改**。用户的原话是「另存为一个新的文件，
而不是重命名原本的文件夹」。

## 根因

目录名的规则是 `<标题slug>__<id8>`（`store/article_store.article_dir`），
而 `custom_article._edit()` 只在**台账记录里记着的那个目录**上重写
`meta.json` 与 `article.md`，**既没有重命名目录，也没有更新台账的 `store_dir`**。

`Ledger.set_store_dir()` 本来就存在（给 `dna migrate-layout` 用的），只是没人调用。

## 比「名字难看」严重得多的连带隐患

同一个文件里有 `store/article_store._remove_stale_dirs()`，它按 id 后缀
**删掉同一篇文章的其它目录**：

```python
suffix = f"__{article_id[:8]}"
for candidate in day_dir.glob(f"*{suffix}"):
    if candidate.is_dir() and candidate.resolve() != keep.resolve():
        shutil.rmtree(candidate, ignore_errors=True)
```

而 `save_article()` 每次都**用当时的标题重新算目录**。两件事凑在一起就是：

1. 改标题 → 目录名与标题脱节（本次现象）；
2. 之后只要有**任何一步走 `save_article()`**（重新抓取、将来的重新落盘），
   它会用**新标题**算出**新目录**，再按 id 后缀把**旧目录连同里面的产物**
   （总结、短视频稿、`wav`、`tts/` 分段）**一起删掉**。

也就是说「改个标题」会变成「另存一份、稿子全丢」。本次只是还没走到第 2 步。

## 修法

`custom_article._relocate()`：标题变了就**在同一个日期层里**把目录 rename 过去，
并同步 `ledger.set_store_dir()`，让台账、`meta.json`、`article.md`、目录名四者一致。

三个刻意的取舍：

| 取舍 | 理由 |
|---|---|
| 日期层**不变** | 日期层记的是「首次落盘那天」。跨天改个标题就把目录挪到新的一天，会让所有产物路径整体变动 |
| 目录名不以 `__<id8>` 结尾、或日期层不是 `YYYYMMDD` 时**不搬** | 那是有人手工挪过的目录，宁可不改名也不要乱动 |
| `slugify` 截断后 slug 没变时**不搬** | 目标目录会与自己同名，搬只会撞上「已存在」 |

## 验收

`tests/store/test_custom_article.py`：

| 用例 | 钉住什么 |
|---|---|
| `test_renaming_the_title_renames_the_folder` | 目录改名、旧目录**不存在**（不留两份）、**已有产物跟着搬**（先放一份 `summary.zh.md` 再改标题）、台账 `store_dir` 指向新目录 |
| `test_rename_keeps_the_original_day_folder` | 日期层不变 |
| `test_editing_the_body_does_not_move_the_folder` | 只改正文不动目录 |
| `test_rename_is_a_no_op_when_the_slug_is_unchanged` | slug 被截断成同一个时原地不动、但标题仍更新 |
| `test_edit_title_moves_all_three_copies` · `test_edits_do_not_wipe_the_body` | 搬家之后三处仍同步、正文不被清空（这两个用例原先用的是**创建时**的 `store_dir`，正因为它们假设「目录永不改名」，才让这个 bug 漏了过去） |

## 教训

这两个既有用例的失败方式值得记一笔：它们**都**用
`_directory(settings, record.store_dir)`（创建时的路径）去读文件。也就是说，
测试自己写死了「目录不会变」这个前提，于是目录该变而没变时，测试照样绿。

**产物路径是会被搬的**——只要一条路径上的名字是从内容推出来的（标题 → slug），
就得有一个用例专门检查「改了内容之后，名字跟着变了吗」。
