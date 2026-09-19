"""
test_prompts.py —— 提示词加载器单元测试 / Prompt loader unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/core/test_prompts.py -v

对应的人工验证 / Matching manual check:
    dna prompt --list                    # 各任务读哪些文件
    dna prompt <id> -t narration         # 看真正发出去的提示词（免费）
    dna doctor                           # 「提示词」一项应为 ok

覆盖 / Covers:
    1. 读一个真实存在的提示词文件
    2. 分块：`## @key` 起新块，标记之前的内容是 main
    3. 没有分块标记时整个文件都是 main
    4. 块内容两端 strip，块内部的换行原样保留（**提示词对换行敏感**）
    5. 占位符 `{{name}}` 被替换；多给的值忽略
    6. **模板里有、调用方没给的占位符要报错**，不能静默留下 `{{cta}}`
    7. 缺文件 / 缺块一律 ConfigError，不降级
    8. 单花括号与提示词里的字面量（`P(A|B)`）原样穿过，不被当成占位符
    9. **改文件后自动重载**：缓存键含 mtime，长驻的 GUI 才能立刻用上新版
   10. `available_prompts` 列出全部文件、排除 README
   11. 仓库里真实的提示词文件都能解析，且必需的块齐全（与 doctor 同一份清单）

为什么要测「缺占位符报错」/ Why the missing-placeholder case is tested:
    静默留下的 `{{cta}}` 会原样发给模型并计费，产出里就多一串花括号——
    而这类错误恰恰发生在改提示词的时候，也就是这套机制最常被动到的时候。

预期 / Expected:
    耗时 < 1s；纯文件读写，不联网、不调 LLM、零费用
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dna.core import prompts
from dna.core.errors import ConfigError


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """每个用例前后都清缓存，避免用例间互相影响。"""
    prompts.clear_cache()


@pytest.fixture
def prompt_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 PROMPTS_DIR 指到临时目录 / Point PROMPTS_DIR at a temp directory."""
    root = tmp_path / "prompts"
    (root / "_shared").mkdir(parents=True)
    monkeypatch.setattr(prompts, "PROMPTS_DIR", root)
    return root


# ---------------------------------------------------------------------------
# 读取与分块 / loading and blocks
# ---------------------------------------------------------------------------


def test_loads_a_file(prompt_dir: Path) -> None:
    (prompt_dir / "demo.md").write_text("你是一位编辑。\n", encoding="utf-8")
    assert prompts.load_prompt("demo") == "你是一位编辑。"


def test_no_marker_means_whole_file_is_main(prompt_dir: Path) -> None:
    (prompt_dir / "demo.md").write_text("第一行\n第二行\n", encoding="utf-8")
    assert prompts.load_prompt("demo") == "第一行\n第二行"
    assert prompts.blocks_of("demo") == ["main"]


def test_splits_on_block_markers(prompt_dir: Path) -> None:
    (prompt_dir / "demo.md").write_text(
        "主块内容\n\n## @role.feature\n\n单角色\n\n## @role.interview\n\n双角色\n",
        encoding="utf-8",
    )
    assert prompts.load_prompt("demo") == "主块内容"
    assert prompts.load_prompt("demo", "role.feature") == "单角色"
    assert prompts.load_prompt("demo", "role.interview") == "双角色"
    assert prompts.blocks_of("demo") == ["main", "role.feature", "role.interview"]


def test_keeps_newlines_inside_a_block(prompt_dir: Path) -> None:
    """
    块内部的换行原样保留 / Newlines inside a block survive verbatim.

    提示词的编号列表靠换行成立，折掉就变成一段糊在一起的文字；
    而且 LLM 缓存键是完整 messages，动一个换行就是全量 miss。
    """
    (prompt_dir / "demo.md").write_text("要求：\n1. 甲\n2. 乙\n\n收尾。\n", encoding="utf-8")
    assert prompts.load_prompt("demo") == "要求：\n1. 甲\n2. 乙\n\n收尾。"


def test_marker_must_be_a_whole_line(prompt_dir: Path) -> None:
    """行内出现 `## @x` 不算标记，否则提示词里没法提到这个写法。"""
    (prompt_dir / "demo.md").write_text("提示：用 ## @key 起一个新块\n", encoding="utf-8")
    assert prompts.blocks_of("demo") == ["main"]
    assert "## @key" in prompts.load_prompt("demo")


# ---------------------------------------------------------------------------
# 占位符 / placeholders
# ---------------------------------------------------------------------------


def test_fills_placeholders(prompt_dir: Path) -> None:
    (prompt_dir / "demo.md").write_text("写 {{lo}}~{{hi}} 字，收尾：「{{cta}}」\n", encoding="utf-8")
    out = prompts.render_prompt("demo", lo=110, hi=160, cta="关注我")
    assert out == "写 110~160 字，收尾：「关注我」"


def test_extra_values_are_ignored(prompt_dir: Path) -> None:
    """多给的值不报错：一次 render 可以供多个块共用同一批取值。"""
    (prompt_dir / "demo.md").write_text("写 {{lo}} 字\n", encoding="utf-8")
    assert prompts.render_prompt("demo", lo=110, unused="x") == "写 110 字"


def test_missing_placeholder_raises(prompt_dir: Path) -> None:
    """
    **不能静默留下 `{{cta}}`。** 那一串会原样发给模型并计费。
    """
    (prompt_dir / "demo.md").write_text("写 {{lo}} 字，收尾：{{cta}}\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        prompts.render_prompt("demo", lo=110)
    assert "cta" in str(excinfo.value)
    assert "demo.md" in str(excinfo.value)


def test_single_braces_pass_through(prompt_dir: Path) -> None:
    """
    单花括号与提示词里的字面量原样穿过 / Literals survive untouched.

    这是**不用 `str.format`** 的理由：`P(A|B)`、`{"a": 1}`、`[pause:400ms]`
    这类东西在提示词里是举例用的，用 format 就得让改提示词的人记住转义规则。
    """
    (prompt_dir / "demo.md").write_text(
        '例：`P(A|B)`、`{"spoken": "…"}`、`[pause:400ms]`，写 {{lo}} 字\n', encoding="utf-8"
    )
    out = prompts.render_prompt("demo", lo=110)
    assert 'P(A|B)' in out and '{"spoken": "…"}' in out and "[pause:400ms]" in out


def test_placeholders_of_lists_them(prompt_dir: Path) -> None:
    (prompt_dir / "demo.md").write_text("{{rules}} 写 {{lo}}~{{hi}} 字\n", encoding="utf-8")
    assert prompts.placeholders_of("demo") == ["hi", "lo", "rules"]


# ---------------------------------------------------------------------------
# 失败与重载 / failures and reloading
# ---------------------------------------------------------------------------


def test_missing_file_raises(prompt_dir: Path) -> None:
    """缺文件不降级：空提示词照样发请求、照样计费，产出是垃圾。"""
    with pytest.raises(ConfigError) as excinfo:
        prompts.load_prompt("nope")
    assert "nope.md" in str(excinfo.value)


def test_missing_block_raises_and_lists_what_exists(prompt_dir: Path) -> None:
    (prompt_dir / "demo.md").write_text("主块\n\n## @a\n\n甲\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        prompts.load_prompt("demo", "b")
    message = str(excinfo.value)
    assert "@b" in message
    assert "a" in message  # 报错里带上现有的块名，省一次翻文件


def test_edit_takes_effect_without_restart(prompt_dir: Path) -> None:
    """
    **改文件后自动重载。** 缓存键含 mtime。

    `dna gui` 是长驻进程：改完提示词点「重做」要用新的那一版，否则人会以为
    自己改的没生效，转头去改别的地方。
    """
    path = prompt_dir / "demo.md"
    path.write_text("第一版\n", encoding="utf-8")
    assert prompts.load_prompt("demo") == "第一版"

    # mtime 的精度可能撑不住同一毫秒内的两次写，显式往前推一秒
    import os

    stat = path.stat()
    path.write_text("第二版\n", encoding="utf-8")
    os.utime(path, (stat.st_atime + 1, stat.st_mtime + 1))
    assert prompts.load_prompt("demo") == "第二版"


def test_available_prompts_excludes_readme(prompt_dir: Path) -> None:
    (prompt_dir / "a.md").write_text("甲\n", encoding="utf-8")
    (prompt_dir / "README.md").write_text("说明\n", encoding="utf-8")
    (prompt_dir / "_shared" / "b.md").write_text("乙\n", encoding="utf-8")
    assert prompts.available_prompts() == ["_shared/b", "a"]


# ---------------------------------------------------------------------------
# 仓库里真实的提示词 / the real prompts in this repository
# ---------------------------------------------------------------------------


def test_repository_prompts_all_parse() -> None:
    """
    仓库里每个提示词文件都读得出非空的 main 块。

    这一条会在「新加了提示词文件但写错了分块语法」时立刻红。
    """
    names = prompts.available_prompts()
    assert names, "config/prompts/ 下应该有提示词文件"
    for name in names:
        assert prompts.load_prompt(name).strip(), f"{name} 的 main 块是空的"


def test_doctor_prompt_check_passes() -> None:
    """
    必需的提示词与块齐全 / Every required prompt and block is present.

    与 `doctor.check_prompts()` 用同一份清单，避免两处各写一遍再各自漂移。
    """
    from dna.core.doctor import Status, check_prompts

    result = check_prompts()
    assert result.status is Status.OK, result.detail
