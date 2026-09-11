"""
test_config_edit.py —— 配置回写单元测试 / Configuration write-back unit tests

复测命令 / Re-run:
    /c/Users/test/miniforge3/envs/ov_env_py312/python.exe -m pytest tests/core/test_config_edit.py -v

对应的人工验证 / Matching manual check:
    dna gui                      # 设置面板改一项 cta_line 保存
    git diff config/profile.yaml # **应该只有那一行变化，注释一个字都不动**

覆盖 / Covers:
    1. 改一个值之后**其余行逐字节不变**——注释是这个项目最贵的资产
    2. 行内注释保留，连对齐空白一起保留（值长度不变时整行 byte-identical）
    3. 沿用原来的书写风格：流式 `[80, 100]` 还是流式，块状列表还是块状
    4. 未知 key 抛 ConfigError，**不追加到文件末尾**
    5. 校验失败时文件一个字节都没写
    6. `.env` 行替换 + 找不到时追加；注释掉的同名行保持注释
    7. 密钥留空或仍是打码值时**不覆盖**原值
    8. 白名单外的 key 被拒
    9. 引号规则交给 yaml：`yes` / `123` / 带 `#` 的字符串都不会读错
   10. TTS 子页那 16 个 key 一次写入：往返一致、原注释不动、重复保存不堆行

为什么不用 safe_dump / Why not round-trip through safe_dump:
    `profile.yaml` 三分之二的行是「为什么」注释，`safe_dump` 一次全抹，
    还会按字母重排 key，把「哪几项是一组」的信息也一起弄丢。
    Two thirds of the file is rationale comments; a dump round-trip erases all of them
    and re-orders the keys, destroying the grouping as well.

预期 / Expected:
    耗时 < 1s；只写 tmp_path，不联网、不调用 LLM
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
import yaml
from dotenv import dotenv_values

from dna.core.config_edit import (
    ENV_ALLOWLIST,
    is_mask,
    mask_secret,
    patch_env_lines,
    patch_yaml_values,
    save_env,
    save_profile,
)
from dna.core.errors import ConfigError

SAMPLE = """\
# 关注的关键词 / focus keywords
#
# 为什么用关键词而不是让 LLM 判断：规则可解释、稳定、免费。
focus_keywords:
  - 大模型
  - 具身智能

# 每期日报最多收录条数 / max entries per issue
digest_max_entries: 15

# ------------------------------------------------------------
# 字数窗口 —— **验收标准是字数，不是秒数**
# ------------------------------------------------------------
summary_chars: [80, 100]        # 条目摘要
narration_chars: [270, 540]     # 短片解说

cta_line: "关注我，下期分享 AI 行业最新进展"
"""


# --- YAML 按行替换 / line-level YAML patching ----------------------------------


def test_only_the_target_line_changes() -> None:
    """
    改一个值，其余每一行逐字节不变。

    这是整个模块存在的理由：注释、空行、分隔线、缩进全部原样保留。
    """
    out = patch_yaml_values(SAMPLE, {"digest_max_entries": 20})

    before = SAMPLE.splitlines()
    after = out.splitlines()
    assert len(before) == len(after)
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert differing == [8]
    assert after[8] == "digest_max_entries: 20"


def test_inline_comment_and_its_padding_survive() -> None:
    """行内注释保留，对齐空白也保留——值宽度不变时整行 byte-identical。"""
    out = patch_yaml_values(SAMPLE, {"summary_chars": [80, 999]})

    assert "summary_chars: [80, 999]        # 条目摘要" in out
    assert "narration_chars: [270, 540]     # 短片解说" in out


def test_flow_style_is_preserved() -> None:
    """原本写成 `[80, 100]` 的就还是流式，不会被摊成多行列表。"""
    out = patch_yaml_values(SAMPLE, {"summary_chars": [90, 120]})

    assert "summary_chars: [90, 120]" in out
    assert "  - 90" not in out


def test_block_list_is_preserved_and_fully_replaced() -> None:
    """块状列表还是块状；旧条目**全部**换掉，不是追加。"""
    out = patch_yaml_values(SAMPLE, {"focus_keywords": ["大模型", "AI 芯片"]})

    parsed = yaml.safe_load(out)
    assert parsed["focus_keywords"] == ["大模型", "AI 芯片"]
    assert "具身智能" not in out
    assert "  - AI 芯片\n" in out
    # 紧跟在列表后面的空行与下一段注释没被吞掉
    assert "# 每期日报最多收录条数 / max entries per issue" in out


def test_unknown_key_raises_instead_of_appending() -> None:
    """
    找不到的 key 报错，不追加。

    静默追加会得到一个重复 key 的 YAML：`safe_load` 取后一个，而人打开文件
    看到的是前一个——同一个文件，程序和人读出两个值，这种 bug 查不出来。
    """
    with pytest.raises(ConfigError, match="no_such_key"):
        patch_yaml_values(SAMPLE, {"no_such_key": 1})


def test_no_updates_returns_the_text_unchanged() -> None:
    out = patch_yaml_values(SAMPLE, {})
    assert out == SAMPLE


@pytest.mark.parametrize("value", ["yes", "no", "123", "1.5", "带 # 号", "", "待续..."])
def test_scalars_round_trip(value: str) -> None:
    """引号规则交给 yaml：`yes` 不加引号会读成布尔值，`123` 会读成整数。"""
    out = patch_yaml_values(SAMPLE, {"cta_line": value})
    assert yaml.safe_load(out)["cta_line"] == value


# --- 落盘 / persisting ---------------------------------------------------------


def test_save_profile_writes_and_validates(tmp_path: Path) -> None:
    """校验通过就落盘，并返回校验后的 Profile。"""
    target = tmp_path / "profile.yaml"
    target.write_text(SAMPLE, encoding="utf-8")

    profile = save_profile({"digest_max_entries": 20}, path=target)

    assert profile.digest_max_entries == 20
    assert "digest_max_entries: 20" in target.read_text(encoding="utf-8")
    assert "# 为什么用关键词而不是让 LLM 判断" in target.read_text(encoding="utf-8")


def test_save_profile_does_not_write_on_validation_failure(tmp_path: Path) -> None:
    """
    校验不过就一个字节都不写。

    界面拿到的是一条错误消息，而不是一个下次启动才会炸的配置文件。
    输入框交回来的永远是字符串，「很多」这种填法是最常见的一种失败。
    """
    target = tmp_path / "profile.yaml"
    target.write_text(SAMPLE, encoding="utf-8")

    with pytest.raises(ConfigError):
        save_profile({"digest_max_entries": "很多"}, path=target)

    assert target.read_text(encoding="utf-8") == SAMPLE


# --- .env ----------------------------------------------------------------------


ENV_SAMPLE = """\
# LLM
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-realkey0123456789
# DEEPSEEK_MODEL=deepseek-v4-flash
HTTPS_PROXY=http://proxy:913
"""


def test_env_replaces_in_place() -> None:
    out = patch_env_lines(ENV_SAMPLE, {"LLM_PROVIDER": "openrouter"})

    assert "LLM_PROVIDER=openrouter" in out
    assert out.count("LLM_PROVIDER=") == 1


def test_env_appends_when_missing() -> None:
    """
    `.env` 找不到就追加——与 YAML 不同。

    dotenv 遇到重复 key 取**最后一个**，所以追加是有效的写入方式。
    """
    out = patch_env_lines(ENV_SAMPLE, {"LOG_LEVEL": "DEBUG"})
    assert "LOG_LEVEL=DEBUG" in out


def test_commented_out_line_stays_commented() -> None:
    """注释掉的 `# KEY=…` 是「曾经用过这个值」的记录，不是当前配置。"""
    out = patch_env_lines(ENV_SAMPLE, {"DEEPSEEK_MODEL": "deepseek-chat"})

    assert "# DEEPSEEK_MODEL=deepseek-v4-flash" in out
    assert "\nDEEPSEEK_MODEL=deepseek-chat\n" in out


@pytest.mark.parametrize("value", ["http://a b:913", "带 # 号", 'has"quote', "trail "])
def test_values_needing_quotes_round_trip(value: str) -> None:
    """含空格或 `#` 的值必须加引号，否则 dotenv 会从 `#` 起当注释截掉。"""
    out = patch_env_lines(ENV_SAMPLE, {"HTTP_PROXY": value})

    assert dotenv_values(stream=StringIO(out))["HTTP_PROXY"] == value


def test_masked_secret_does_not_overwrite(tmp_path: Path) -> None:
    """
    界面把打码值放进输入框，用户不改就原样提交回来——必须认出来并跳过。

    否则真密钥会被 `sk-real…（20 字符）` 这行字覆盖掉，而且不可逆。
    """
    target = tmp_path / ".env"
    target.write_text(ENV_SAMPLE, encoding="utf-8")

    written = save_env({"DEEPSEEK_API_KEY": mask_secret("sk-realkey0123456789")}, path=target)

    assert written == []
    assert "DEEPSEEK_API_KEY=sk-realkey0123456789" in target.read_text(encoding="utf-8")


def test_blank_secret_does_not_overwrite(tmp_path: Path) -> None:
    """留空表示「不修改」，不是「清空」。"""
    target = tmp_path / ".env"
    target.write_text(ENV_SAMPLE, encoding="utf-8")

    save_env({"DEEPSEEK_API_KEY": "", "LLM_PROVIDER": "openrouter"}, path=target)

    text = target.read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=sk-realkey0123456789" in text
    assert "LLM_PROVIDER=openrouter" in text


def test_keys_outside_the_allowlist_are_refused(tmp_path: Path) -> None:
    """
    白名单而不是黑名单：`db_path` 改错了程序下次就找不到自己的数据库。

    An allowlist, not a denylist: a wrong `db_path` orphans the entire database.
    """
    target = tmp_path / ".env"
    target.write_text(ENV_SAMPLE, encoding="utf-8")

    written = save_env({"DB_PATH": "D:/elsewhere.db"}, path=target)

    assert written == []
    assert "DB_PATH" not in target.read_text(encoding="utf-8")
    assert "DB_PATH" not in ENV_ALLOWLIST


# --- 打码 / masking ------------------------------------------------------------


def test_mask_keeps_only_a_prefix_and_the_length() -> None:
    """用户会截终端和界面的图，完整密钥不能出现在任何一处。"""
    masked = mask_secret("sk-abcdefghijklmnopqrstuvwxyz")

    assert masked.startswith("sk-abcd")
    assert "efghij" not in masked
    assert "29 字符" in masked
    assert is_mask(masked)


def test_real_value_is_not_mistaken_for_a_mask() -> None:
    assert not is_mask("sk-abcdefghijklmnop")
    assert not is_mask("")


# --- TTS 设置子页新增的 key / the keys the TTS settings tab writes ---------------


TTS_ENV_SAMPLE = """\
# TTS —— 本地服务，零费用但很慢
# 服务地址；改成别的机器时连带 TTS_AUTOSTART 一起关掉
TTS_SERVICE_URL=http://127.0.0.1:8300
TTS_MODE=voice_clone
# 参考音频相对 DATA_DIR 解析，不是仓库根
TTS_REF_AUDIO=ref_audio/reference_audio_male.mp3
"""

TTS_NEW_VALUES = {
    "TTS_SERVICE_URL": "http://127.0.0.1:8400",
    "TTS_MODULE_DIR": "C:/Users/test/Downloads/xkd/Agent_TTS_Module",
    "TTS_PYTHON": "C:/Users/test/miniforge3/envs/tts/python.exe",
    "TTS_AUTOSTART": "false",
    "TTS_START_TIMEOUT": "180",
    "TTS_REQUEST_TIMEOUT": "600",
    "TTS_MODE": "custom_voice",
    "TTS_VOICE_HOST": "Cherry",
    "TTS_VOICE_GUEST": "Ethan",
    "TTS_REF_AUDIO": "ref_audio/qwen3-tts-gpu.wav",
    "TTS_REF_TEXT": "大家好，欢迎收听今天的 AI 日报。",
    "TTS_REF_AUDIO_GUEST": "ref_audio/qwen3-tts-cpu.wav",
    "TTS_REF_TEXT_GUEST": "是的，这个更新挺关键 # 顺带一提",
    "TTS_PREPROCESS": "true",
    "TTS_SUBTITLES": "false",
    "TTS_ARTIFACT_DIRNAME": "audio",
}


def _comment_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.lstrip().startswith("#")]


def test_tts_keys_round_trip_and_leave_the_comments_alone(tmp_path: Path) -> None:
    """
    设置面板 TTS 子页的 16 个 key：改→写→再读回来一致，注释逐字节不动。

    这一页是一次提交十几个 key，比之前任何一次写入都宽——已存在的替换、
    没写过的追加两条路都要走对。注释里记的是「参考音频按 DATA_DIR 解析」
    这类踩过的坑，抹掉一行就得再踩一次。

    原有注释必须原序原样留下；追加分支会在文件末尾多写一条横幅，那一条是允许的。
    """
    target = tmp_path / ".env"
    target.write_text(TTS_ENV_SAMPLE, encoding="utf-8")

    written = save_env(dict(TTS_NEW_VALUES), path=target)
    text = target.read_text(encoding="utf-8")

    assert set(written) == set(TTS_NEW_VALUES)
    assert dict(dotenv_values(stream=StringIO(text))) == TTS_NEW_VALUES

    original = _comment_lines(TTS_ENV_SAMPLE)
    kept = _comment_lines(text)
    assert kept[: len(original)] == original
    assert len(kept) - len(original) <= 1


def test_saving_the_tts_page_twice_does_not_pile_up_lines(tmp_path: Path) -> None:
    """
    第二次保存必须替换第一次追加的那些行。

    追加是 `.env` 找不到 key 时的正常写法（dotenv 取最后一个），
    所以「值对了」测不出重复——每保存一次多十几行的文件仍然读得出正确值。
    """
    target = tmp_path / ".env"
    target.write_text(TTS_ENV_SAMPLE, encoding="utf-8")

    save_env(dict(TTS_NEW_VALUES), path=target)
    save_env({**TTS_NEW_VALUES, "TTS_MODE": "voice_clone"}, path=target)
    text = target.read_text(encoding="utf-8")

    for key in TTS_NEW_VALUES:
        assert text.count(f"\n{key}=") == 1, key
    assert dotenv_values(stream=StringIO(text))["TTS_MODE"] == "voice_clone"
