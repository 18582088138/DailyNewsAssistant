"""
字幕切分效果探针 / Inspect how a real take gets segmented.

拿一次**真实合成**的逐段产物跑一遍切分，把每段的停顿、条数、时长摊开看。
调 `SplitParams` 的旋钮之前先跑它，比对着代码猜快得多。
Run this before tuning the knobs.

用法 / Usage:
    python tools\\probe_subtitles.py <tts 目录>
    python tools\\probe_subtitles.py <tts 目录> --params max_chars=14,min_chars=8

（解释器用 `ov_env_py312`，见 `CLAUDE.md` 的环境节。）

不给目录时自动挑 `outputs/articles` 下**最近修改**的那个 `tts` 目录。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dna.tts.cue_split import SplitParams, align  # noqa: E402
from dna.tts.vad import UnsupportedWav, pause_points, read_mono  # noqa: E402


def latest_tts_dir() -> Path | None:
    """最近的 tts 产物目录 / the most recently touched tts directory."""
    root = Path(__file__).resolve().parent.parent / "outputs" / "articles"
    if not root.is_dir():
        return None
    candidates = [p for p in root.rglob("tts") if p.is_dir()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def parse_params(raw: str | None) -> SplitParams:
    params = SplitParams()
    if not raw:
        return params
    for item in raw.split(","):
        key, _, value = item.partition("=")
        key = key.strip()
        if not hasattr(params, key):
            print(f"  未知参数 / unknown: {key}")
            continue
        current = getattr(params, key)
        params = replace(params, **{key: type(current)(value)})
    return params


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raw = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--params=")), None)

    directory = Path(args[0]) if args else latest_tts_dir()
    if directory is None or not directory.is_dir():
        print("找不到 tts 目录 / no tts directory found")
        return 1

    params = parse_params(raw)
    print(f"目录 / directory: {directory}")
    print(f"参数 / params   : {params}")
    print()

    wavs = sorted(directory.glob("seg_*.wav"))
    if not wavs:
        print("这个目录里没有逐段 wav / no per-piece wavs here")
        return 1

    total_cues = 0
    print(f"{'段':<16}{'时长':>8}{'停顿':>6}{'条数':>6}{'平均':>8}{'最长':>8}")
    for path in wavs:
        wav = path.read_bytes()
        try:
            samples, rate = read_mono(wav)
            seconds = len(samples) / rate if rate else 0.0
            pauses = pause_points(wav)
        except (UnsupportedWav, ValueError, OSError) as exc:
            print(f"{path.name:<16}  读不了 / unreadable: {exc}")
            continue

        # 没有真实文本时用一段占位朗读稿：这里看的是**时间轴**的形状
        placeholder = "这是一段用来测量切分形状的占位文本。" * 4
        cues = align(placeholder, placeholder, seconds, pauses, params)
        durations = [c.duration for c in cues] or [0.0]
        total_cues += len(cues)
        print(f"{path.name:<16}{seconds:>7.2f}s{len(pauses):>6}{len(cues):>6}"
              f"{sum(durations) / len(durations):>7.2f}s{max(durations):>7.2f}s")

    print()
    print(f"合计 {len(wavs)} 段 / {total_cues} 条字幕（占位文本，只反映时间轴形状）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
