"""
配置回写 / Writing configuration back.

界面上的设置面板要能改 `config/profile.yaml` 与 `.env`。这里是唯一的写入口。
The settings panel edits `config/profile.yaml` and `.env`; this is the only writer.

**不用 `yaml.safe_dump` 重写整个文件。** `profile.yaml` 里三分之二的行是注释——
为什么用字数而不是秒数验收、为什么多存图、为什么删掉了 `new_badge_hours`。
`safe_dump(Profile.model_dump())` 一次就把它们全抹了，而且顺序会按字母重排，
下次有人打开这个文件时，「哪几项是一组」的信息也没了。
所以这里做的是**按行替换某个顶层 key 的值**，其余一个字节都不碰。
A `safe_dump` round-trip would erase every comment in the file and re-order the keys
alphabetically, destroying both the reasoning and the grouping. Values are patched line
by line instead; nothing else in the file is touched.

粒度够用的前提 / Why line patching suffices:
    `profile.yaml` 的字段全是顶层扁平标量、二元区间和字符串列表，没有嵌套映射。
    真要支持任意 YAML，该换 ruamel.yaml（保留注释的往返解析器），而不是把这里
    改复杂——那时候这个模块整体替换掉就行。
    Every field is a top-level flat scalar, a two-element window or a list of strings.
    Arbitrary YAML would call for ruamel.yaml rather than a more elaborate patcher here.

`.env` 与 YAML 的区别 / Why .env may append and YAML may not:
    dotenv 遇到重复 key 取**最后一个**，所以往末尾追加是有效的写入方式。
    YAML 的 `safe_load` 也取后者，但人打开文件看到的是前者——同一个文件，
    程序和人读出两个不同的值，这种 bug 没人查得出来。所以 YAML 找不到 key 就报错。
    dotenv resolves duplicates to the last occurrence, so appending genuinely works. YAML
    resolves them the same way, but a human reading the file sees the *first* — program
    and person would disagree about the same file, so a missing key raises instead.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from dna.core.config import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_ENV_FILE,
    Profile,
    reload_settings,
)
from dna.core.errors import ConfigError
from dna.core.logging import get_logger

logger = get_logger("core.config_edit")

# 可以从界面改的 .env 项 / the .env keys the settings panel may write
#
# **白名单而不是黑名单。** `.env` 里有 60 多项，其中 `data_dir`、`db_path` 这类
# 改错了程序下次就找不到自己的数据库；界面上给出来只会让人误以为可以随便调。
# 需要重启才生效的、或者改错就失联的，一律不进这张表。
# An allowlist, not a denylist: `.env` holds sixty-odd fields, and getting `db_path`
# wrong means the program cannot find its own database next launch.
ENV_ALLOWLIST: tuple[str, ...] = (
    # LLM
    "LLM_PROVIDER",
    "LLM_FALLBACK_PROVIDER",
    "DEEPSEEK_MODEL",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "LLM_CACHE_ENABLED",
    # TTS
    "TTS_SERVICE_URL",
    "TTS_PREPROCESS",
    # 网络 / network
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    # 运行 / runtime
    "DEFAULT_LANGUAGE",
    "MAX_ITEMS_PER_SOURCE",
    "LOG_LEVEL",
)

# 这些项的值**永远不回显** / values never echoed back to the UI
SECRET_KEYS: frozenset[str] = frozenset({"DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"})


# ---------------------------------------------------------------------------
# 打码 / masking
# ---------------------------------------------------------------------------


def mask_secret(value: str) -> str:
    """
    密钥脱敏 / Mask a secret for display.

    只留前 7 位加长度。用户会截终端和界面的图，完整密钥不能出现在任何一处。
    保留前几位是为了能认出「这是哪一把」，长度是为了能看出「粘贴时有没有截断」。
    Users screenshot both the terminal and the UI, so the full key must appear in
    neither. The prefix identifies which key it is; the length reveals a truncated paste.
    """
    if not value:
        return ""
    return f"{value[:7]}…（{len(value)} 字符）"


def is_mask(value: str) -> bool:
    """
    这个值是不是打码的占位符 / Whether this is a mask rather than a real value.

    界面把打码值放进输入框，用户不改就原样提交回来。**必须认出来并跳过**，
    否则真密钥会被 `sk-1234…（51 字符）` 这行字覆盖掉，而且不可逆。
    The masked value goes into the input box and comes back unchanged if untouched. It
    must be recognised and skipped, or the real key is irreversibly overwritten by it.
    """
    return "…（" in value and value.endswith("字符）")


# ---------------------------------------------------------------------------
# YAML 按行替换 / line-level YAML patching
# ---------------------------------------------------------------------------


def patch_yaml_values(text: str, updates: dict[str, Any]) -> str:
    """
    替换若干顶层 key 的值，保留全部注释与排版 / Replace values, keeping every comment.

    抛出 / Raises:
        ConfigError: 某个 key 在文件里找不到（**不追加**，理由见模块文档）

    行内注释保留 / An inline comment survives:
        `summary_chars: [80, 100]   # 条目摘要` 改成 `[90, 120]` 之后注释还在那里。
        注释写的是这一项是什么，跟值改成多少没关系。
    """
    if not updates:
        return text

    lines = text.splitlines(keepends=True)
    remaining = dict(updates)

    index = 0
    out: list[str] = []
    while index < len(lines):
        line = lines[index]
        key = _top_level_key(line)

        if key is None or key not in remaining:
            out.append(line)
            index += 1
            continue

        value = remaining.pop(key)
        block_end = _block_end(lines, index)
        out.append(_render_entry(key, value, lines[index:block_end]))
        index = block_end

    if remaining:
        missing = "、".join(sorted(remaining))
        raise ConfigError(
            f"profile.yaml 里找不到这些配置项：{missing}。"
            "不会自动追加——追加会产生重复 key，程序读后一个而人看前一个。"
        )

    return "".join(out)


_TOP_LEVEL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:(.*)$", re.DOTALL)


def _top_level_key(line: str) -> str | None:
    """这一行是不是一个顶层 key / Whether the line opens a top-level key."""
    if not line or line[0].isspace() or line.lstrip().startswith("#"):
        return None
    match = _TOP_LEVEL.match(line)
    return match.group(1) if match else None


def _block_end(lines: list[str], start: int) -> int:
    """
    这个 key 的值占到第几行 / Where this key's value block ends.

    从 key 那一行往下，缩进的非空行都属于它（列表项、缩进的注释）。
    空行与顶格的注释不算——它们是下一段的开场白，吞掉就等于把注释搬了家。
    Indented non-empty lines belong to the key. A blank line or a column-zero comment
    does not: those introduce the *next* section, and swallowing them relocates comments.
    """
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if not line.strip() or not line[0].isspace():
            break
        index += 1
    return index


def _render_entry(key: str, value: Any, original: list[str]) -> str:
    """
    生成替换后的整段文本 / Render the replacement block for one key.

    **沿用原来的书写风格**：原本写成 `[80, 100]` 就还是流式，原本是 `- a` 的多行列表
    就还是多行。风格是人选的，程序换一种写法会让 `git diff` 里出现大片与本次
    修改无关的变动，掩盖真正改了什么。
    The original style is preserved: reformatting would fill the diff with noise unrelated
    to the actual change.
    """
    head = original[0]
    newline = "\n" if head.endswith("\n") else ""
    body = head[: -len(newline)] if newline else head

    inline_value, comment = _split_comment(body.split(":", 1)[1])
    was_flow = inline_value.strip().startswith("[")
    # 原来的对齐空白照抄：值长度没变时整行逐字节相同，`git diff` 里就只剩真正改了的行。
    # Reusing the original padding keeps the line byte-identical when the value's width
    # is unchanged, so the diff shows only what actually changed.
    gap = inline_value[len(inline_value.rstrip()) :] if comment else ""
    suffix = f"{gap}{comment}" if comment else ""

    if isinstance(value, (list, tuple)) and not was_flow:
        item_newline = newline or "\n"
        rendered = "".join(f"  - {_scalar(v)}{item_newline}" for v in value)
        return f"{key}:{suffix}{newline}{rendered}"

    if isinstance(value, (list, tuple)):
        joined = ", ".join(_scalar(v) for v in value)
        return f"{key}: [{joined}]{suffix}{newline}"

    return f"{key}: {_scalar(value)}{suffix}{newline}"


def _split_comment(fragment: str) -> tuple[str, str]:
    """
    切开行内的值与注释 / Separate the inline value from its trailing comment.

    只认**前面有空白**的 `#`：`cta_line: "关注 #AI"` 里那个井号是字符串的一部分。
    Only a `#` preceded by whitespace counts, so a hash inside a quoted string survives.
    """
    in_single = in_double = False
    for position, char in enumerate(fragment):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double and position and fragment[position - 1].isspace():
            return fragment[:position], fragment[position:].rstrip()
    return fragment, ""


def _scalar(value: Any) -> str:
    """一个标量的 YAML 写法 / One scalar rendered as YAML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    # 交给 yaml 决定要不要加引号：中文、冒号、`#`、`yes`、纯数字样式的字符串各有各的
    # 规矩（`yes` 不加引号会读成布尔值），自己判断迟早漏一种。
    # 套一层单元素列表再剥掉方括号，是为了拿到**流式上下文**里的写法：直接
    # `safe_dump("x")` 会带一个文档结束标记 `...`，而正文本身就以 `...` 结尾时无法区分。
    # `width` 放到极大：默认 80 列会把长句折行，`cta_line` 这种正好会中招。
    # Dumping inside a one-element list yields the flow-context spelling and avoids the
    # `...` document-end marker, which is ambiguous when the text itself ends in dots.
    dumped = yaml.safe_dump(
        [str(value)], allow_unicode=True, default_flow_style=True, width=10**9
    ).strip()
    return dumped[1:-1].strip() or '""'


# ---------------------------------------------------------------------------
# .env 按行替换 / line-level .env patching
# ---------------------------------------------------------------------------


def patch_env_lines(text: str, updates: dict[str, str]) -> str:
    """
    替换或追加 `.env` 里的若干项 / Replace or append entries in a `.env` file.

    找不到就追加（与 YAML 不同，理由见模块文档）。注释掉的 `# KEY=…` 保持注释——
    它是「曾经用过这个值」的记录，不是当前配置。
    Missing keys are appended. A commented-out `# KEY=…` stays commented: it records a
    value once used, and is not part of the current configuration.
    """
    if not updates:
        return text

    remaining = {k.upper(): v for k, v in updates.items()}
    out: list[str] = []

    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue

        key = stripped.split("=", 1)[0].strip().upper()
        if key not in remaining:
            out.append(line)
            continue

        newline = "\n" if line.endswith("\n") else ""
        out.append(f"{key}={_env_value(remaining.pop(key))}{newline}")

    result = "".join(out)
    if remaining:
        if result and not result.endswith("\n"):
            result += "\n"
        result += "\n# 由设置面板追加 / appended by the settings panel\n"
        result += "".join(f"{k}={_env_value(v)}\n" for k, v in sorted(remaining.items()))

    return result


def _env_value(value: str) -> str:
    """
    `.env` 里一个值的写法 / One value as written in a `.env` file.

    含空格或 `#` 的加双引号——不加的话 dotenv 会把 `#` 之后当注释截掉，
    代理地址和引导语正是最可能带这两个字符的。
    Values containing spaces or a hash are quoted: otherwise dotenv truncates at the hash,
    and proxy strings and sign-off lines are exactly where those characters appear.
    """
    text = str(value)
    if text != text.strip() or any(c in text for c in ' #"'):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


# ---------------------------------------------------------------------------
# 落盘 / persisting
# ---------------------------------------------------------------------------


def save_profile(updates: dict[str, Any], *, path: Path | None = None) -> Profile:
    """
    校验后写回 `config/profile.yaml` / Validate, then write back.

    **先校验再落盘。** `Profile` 是 `extra="forbid"` 的 pydantic 模型，拼错的字段名、
    写反的区间、类型不对的值都会在这里被挡下——校验不过就一个字节都不写，
    界面拿到的是一条错误消息，而不是一个下次启动才会炸的配置文件。
    Validation first: a bad value never reaches the file, so the user gets an error
    message instead of a config that explodes at next launch.

    抛出 / Raises:
        ConfigError: 校验失败，或某个 key 在文件里找不到
    """
    target = path or (DEFAULT_CONFIG_DIR / "profile.yaml")
    text = target.read_text(encoding="utf-8") if target.exists() else ""

    current = yaml.safe_load(text) or {} if text else {}
    if not isinstance(current, dict):  # pragma: no cover - 文件被改成非映射
        raise ConfigError(f"profile.yaml 顶层应为映射 / expected a mapping in {target}")

    merged = {**current, **updates}
    try:
        profile = Profile(**merged)
    except Exception as exc:  # noqa: BLE001 - pydantic 的错误信息要原样带给用户
        raise ConfigError(f"配置校验失败 / invalid preferences: {exc}") from exc

    target.write_text(patch_yaml_values(text, updates), encoding="utf-8")
    logger.info("已更新 profile.yaml：%s", "、".join(sorted(updates)))
    return profile


def save_env(updates: dict[str, str], *, path: Path | None = None) -> list[str]:
    """
    写回 `.env` 并让新值立刻生效 / Write back to `.env` and make the values live.

    返回**实际写入的 key**：打码占位符与白名单之外的项都会被跳过，
    界面据此告诉用户「改了哪几项」，而不是笼统一句「已保存」。
    Returns the keys actually written; masks and non-allowlisted keys are skipped, so the
    UI can say which settings changed rather than just "saved".

    `get_settings()` 带 `lru_cache`，不清缓存的话这次修改要到重启才生效——
    界面上显示「已保存」而行为没变，是最难解释的一种 bug。
    `get_settings()` is cached, so without clearing it the change would only take effect
    after a restart while the UI claims success.
    """
    target = path or DEFAULT_ENV_FILE
    text = target.read_text(encoding="utf-8") if target.exists() else ""

    accepted: dict[str, str] = {}
    for raw_key, value in updates.items():
        key = raw_key.upper()
        if key not in ENV_ALLOWLIST:
            logger.warning("跳过不在白名单里的配置项：%s", key)
            continue
        if key in SECRET_KEYS and (not value or is_mask(value)):
            continue  # 没动过的密钥框，原样留着
        accepted[key] = value

    if not accepted:
        return []

    target.write_text(patch_env_lines(text, accepted), encoding="utf-8")
    reload_settings()
    logger.info("已更新 .env：%s", "、".join(sorted(accepted)))
    return sorted(accepted)


def shadowed_by_env(key: str) -> bool:
    """
    这一项是否被系统环境变量盖住了 / Whether an OS environment variable overrides it.

    pydantic-settings 的优先级是**环境变量 > .env 文件**。公司机器上
    `HTTPS_PROXY` 往往是系统级设的，这时改 `.env` 一点反应都没有——
    不在界面上标出来，人会反复改同一项然后认为程序坏了。`dna doctor` 已经在做同一件事。
    Environment variables win over the file. On a corporate machine `HTTPS_PROXY` is
    usually set system-wide, so editing `.env` does nothing; without a marker the user
    edits the same field repeatedly and concludes the program is broken.
    """
    import os

    return key.upper() in os.environ


__all__ = [
    "ENV_ALLOWLIST",
    "SECRET_KEYS",
    "is_mask",
    "mask_secret",
    "patch_env_lines",
    "patch_yaml_values",
    "save_env",
    "save_profile",
    "shadowed_by_env",
]
