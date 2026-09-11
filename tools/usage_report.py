"""
token 与费用报表 / Token and cost report.

    python tools/usage_report.py --days 3
    python tools/usage_report.py --days 7 --by session
    python tools/usage_report.py --days 1 --project DailyNews

数据来自 Claude Code 自己的转写文件（`~/.claude/projects/*/*.jsonl`），**每条消息
都记了模型名与 token 用量**；金额按 `tools/pricing.json` 的单价算。

这个脚本存在的唯一理由：**实测发现费用里 58% 是「重读上下文」**
（缓存读 22 亿 token），而输出只占 13%。没有这张表的话，优化方向只能靠猜 ——
而凭感觉会去优化「少写代码」，那一项根本不是大头。
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
from pathlib import Path

FIELDS = ("input", "cache_write", "cache_read", "output")
KEYS = {
    "input": "input_tokens",
    "cache_write": "cache_creation_input_tokens",
    "cache_read": "cache_read_input_tokens",
    "output": "output_tokens",
}


def transcripts_root() -> Path:
    if env := os.environ.get("CLAUDE_CONFIG_DIR"):
        return Path(env) / "projects"
    return Path.home() / ".claude" / "projects"


def load_pricing() -> dict:
    path = Path(__file__).resolve().parent / "pricing.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def rate_for(pricing: dict, model: str) -> dict:
    return pricing.get("models", {}).get(model) or pricing["default"]


def cost(pricing: dict, model: str, counts: collections.Counter) -> float:
    rate = rate_for(pricing, model)
    return sum(counts[f] * rate[f] for f in FIELDS) / 1e6


def local_day(stamp: str, offset_hours: float) -> str:
    when = dt.datetime.fromisoformat(stamp)
    return (when + dt.timedelta(hours=offset_hours)).strftime("%m-%d")


def collect(root: Path, days: int, project: str, offset: float):
    """扫转写文件，按 (分组键, 模型) 汇总 token 与轮数。"""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    per: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    turns: collections.Counter = collections.Counter()
    tools: collections.Counter = collections.Counter()
    for path in root.glob("*/*.jsonl"):
        if project and project.lower() not in path.parent.name.lower():
            continue
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                stamp = entry.get("timestamp")
                if not stamp:
                    continue
                when = dt.datetime.fromisoformat(stamp)
                if when < cutoff:
                    continue
                message = entry.get("message") or {}
                model = message.get("model") or "?"
                usage = message.get("usage") or {}
                key = (local_day(stamp, offset), path.stem[:8], model)
                for field, source in KEYS.items():
                    per[key][field] += usage.get(source, 0)
                turns[key] += 1
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tools[key] += 1
    return per, turns, tools


def main() -> int:
    parser = argparse.ArgumentParser(description="token 与费用报表")
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--by", choices=("day", "session"), default="day")
    parser.add_argument("--project", default="", help="按项目目录名过滤（子串）")
    parser.add_argument("--tz-offset", type=float, default=8.0, help="本地时区偏移小时")
    args = parser.parse_args()

    pricing = load_pricing()
    root = transcripts_root()
    if not root.is_dir():
        print(f"找不到转写目录：{root}")
        return 1

    per, turns, tools = collect(root, args.days, args.project, args.tz_offset)
    if not per:
        print("这个时间范围内没有记录。")
        return 0

    grouped: dict[tuple, collections.Counter] = collections.defaultdict(
        collections.Counter)
    g_turns, g_tools = collections.Counter(), collections.Counter()
    for (day, session, model), counts in per.items():
        key = (day, model) if args.by == "day" else (session, model)
        grouped[key].update(counts)
        g_turns[key] += turns[(day, session, model)]
        g_tools[key] += tools[(day, session, model)]

    head = "日期" if args.by == "day" else "会话"
    print(f"{head:<9}{'模型':<18}{'轮':>6}{'工具':>6}{'输出':>10}"
          f"{'缓存读':>13}{'每轮上下文':>11}{'$':>9}{'其中重读':>9}")
    total = collections.Counter()
    total_cost = 0.0
    for key in sorted(grouped):
        label, model = key
        counts = grouped[key]
        turn_count = g_turns[key] or 1
        money = cost(pricing, model, counts)
        reread = counts["cache_read"] * rate_for(pricing, model)["cache_read"] / 1e6
        print(f"{label:<9}{model.replace('claude-', ''):<18}{g_turns[key]:>6}"
              f"{g_tools[key]:>6}{counts['output']:>10,}{counts['cache_read']:>13,}"
              f"{counts['cache_read'] // turn_count:>11,}{money:>9.2f}"
              f"{reread / (money or 1) * 100:>8.0f}%")
        total.update(counts)
        total_cost += money

    print(f"\n合计：输出 {total['output']:,} · 缓存读 {total['cache_read']:,} "
          f"· 缓存写 {total['cache_write']:,} · 输入 {total['input']:,}")
    print(f"估算费用 ${total_cost:.2f}（单价见 tools/pricing.json，改那里即可重算）")
    if total["cache_read"]:
        print("提示：「每轮上下文」就是每轮要重读的 token 数 —— 这一列越小越省钱，"
              "压它的办法是一个任务一个会话、做完 /clear。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
