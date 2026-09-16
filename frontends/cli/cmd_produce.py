"""单篇产物与语音合成 / Per-article production and speech synthesis。"""

from __future__ import annotations

import typer

from dna.core.config import get_settings
from frontends.cli.app import app, console
from frontends.cli.render import (
    _resolve_article,
)


@app.command()
def produce(
    article_id: str = typer.Argument(..., help="文章 id，前 8 位即可"),
    kind: str | None = typer.Option(
        None,
        "--kind",
        "-k",
        help=(
            "summary | shortvideo | narration | longform"
            " | shortvideo_audio | narration_audio | longform_audio"
        ),
    ),
    lang: str = typer.Option("zh", "--lang", "-l", help="输出语言：zh | en"),
    instructions: str = typer.Option(
        "", "--instructions", "-i", help="本次的额外要求，例如「加长到 40 秒」"
    ),
    all_kinds: bool = typer.Option(False, "--all", help="生成常规三项（不含长文案与音频）"),
    variant: str | None = typer.Option(
        None, "--variant", help="长文案形式：feature（专题，单角色）| interview（访谈，双角色）"
    ),
    force: bool = typer.Option(False, "--force", help="已有产物也重做（会重新计费）"),
) -> None:
    """
    为一篇文章生成产物 / Produce content for one article.

    ⚠️ **文案类产物会调用 LLM 并产生费用。** 已有的产物默认直接复用、不重复计费，
    要重做请加 --force。

    长文案（5~15 分钟）**不在 --all 里**，必须显式 --kind longform：
    它一篇要 5~9 次调用，是其余三项加起来的两倍多。

    `--lang en` 出英文版。**英文版单独计费**，而且不进 --all——
    多数文章不需要英文版，跟着批量跑等于每篇都翻倍。
    总结类的英文版翻译已写好的中文（便宜）；三种文稿的英文版**原生重写**，
    因为中文 30 秒的稿子翻成英文不是 30 秒的稿子，而时长是它们的验收标准。

    `--force` 时**不走 LLM 缓存**：缓存故意不设过期、键是完整提示词，
    照常走的话重做会拿回一模一样的旧答案。要引导出不同的结果，
    再配 `--instructions "加长到 40 秒"`。

    三种 `*_audio` 是**本地 TTS 合成，一分钱不花，但很花时间**（实测 RTF≈2.5：
    口播约 4 分钟，长文案约 37 分钟）。它们同样不在 --all 里，理由是时间不是钱。
    """
    from dna.produce import ProductionKind, produce_all, spec
    from dna.produce import produce as produce_one
    from dna.produce.tasks import batch_kinds, estimate_calls

    record = _resolve_article(article_id)

    if not all_kinds and kind is None:
        console.print("[yellow]请指定 --kind，或用 --all 生成常规四项。[/yellow]")
        console.print(f"[dim]可选：{' | '.join(str(k) for k in ProductionKind)}[/dim]")
        raise typer.Exit(code=1)

    if all_kinds:
        console.print(
            f"[dim]将生成 {len(batch_kinds())} 项，预估 "
            f"{estimate_calls(list(batch_kinds()))} 次 LLM 调用（已有产物会跳过）[/dim]"
        )
        with console.status(f"生成中：{record.title[:30]}…"):
            results = produce_all(record.id, lang=lang, force=force)
    else:
        task = spec(kind)
        if task.needs_variant and variant is None:
            variant = "feature"
            console.print("[dim]未指定 --variant，按专题（单角色）生成[/dim]")

        if task.audio_of is not None:
            console.print(
                "[dim]本地 TTS 合成，不产生费用；长稿子要跑几十分钟，进度见日志[/dim]"
            )
        else:
            console.print(f"[dim]预估 {task.approx_calls} 次 LLM 调用[/dim]")

        with console.status(f"生成中：{task.label}…"):
            results = [
                produce_one(
                    record.id,
                    kind,
                    lang=lang,
                    variant=variant,
                    force=force,
                    instructions=instructions,
                )
            ]

    console.print(f"\n[bold]{record.title}[/bold]")
    for result in results:
        style = "green" if result.ok else "red"
        marker = "[dim]○[/dim]" if result.skipped else f"[{style}]●[/{style}]"
        console.print(f"  {marker} {result.summary()}")

    if any(r.ok and not r.skipped for r in results):
        console.print(f"\n[dim]产物目录：{get_settings().output_path / record.store_dir}[/dim]")


@app.command()
def tts(
    say: str | None = typer.Option(None, "--say", help="合成一句话试听，写成 wav"),
    out: str = typer.Option("tts-check.wav", "--out", "-o", help="试听文件写到哪"),
    voice: str | None = typer.Option(None, "--voice", help="指定音色；默认按 .env"),
) -> None:
    """
    检查 TTS 服务 / Check the TTS service.

    **不产生任何费用**（加 --say 时会有一次很小的 LLM 预处理调用，可用
    TTS_PREPROCESS=false 关掉）。服务不在线时会**自动拉起**，三次失败才报不可用——
    这条命令同时也是在验「自动拉起这条路通不通」。

    加 --say 会真的合成一句话并写成 wav，用来确认「服务在、音色对、声音正常」。
    换部署环境（本机 → 4060 那台）后第一件事就该跑它：改 TTS_SERVICE_URL 再跑一次。
    """
    import time as _time
    from pathlib import Path

    from dna.tts import SpeechSegment, VoiceSpec, get_tts, voice_for_role
    from dna.tts.base import TTSError
    from dna.tts.supervisor import ensure_service

    settings = get_settings()
    provider = get_tts(settings)

    # 探活与自动拉起放在最前面：后面每一步都依赖服务在线，
    # 让它们各自失败一次只会给出三条互相矛盾的报错。
    try:
        with console.status(f"检查 TTS 服务 {settings.tts_service_url}…"):
            status = ensure_service(settings)
    except TTSError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"\n[green]{status.summary()}[/green]")
    console.print(f"[bold]引擎[/bold]　{provider.info}")

    try:
        speakers = provider.available_speakers()
    except TTSError as exc:
        console.print(f"[red]取音色清单失败：{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[dim]可用音色（{len(speakers)}）：{'、'.join(speakers)}[/dim]")
    def _describe(role: str) -> str:
        """一行说清这个角色怎么发声 / One line: how this role speaks."""
        spec_ = voice_for_role(role, settings)
        if spec_.ref_audio:
            kind = "x-vector 克隆" if spec_.x_vector_only else "ICL 克隆"
            return f"{kind} {Path(spec_.ref_audio).name}"
        return f"内置音色 {spec_.speaker}"

    console.print(f"[dim]主持人：{_describe('host')}　嘉宾：{_describe('guest')}[/dim]")

    if say is None:
        console.print("\n[green]服务就绪。[/green][dim]加 --say \"一句话\" 可实际合成试听。[/dim]")
        return

    # 默认拿 voice_for_role 给的那一份**整体**，不要只取 speaker：
    # 克隆模式的信息（ref_audio / x_vector_only）都在那个对象里，
    # 只抄一个字段就等于把「默认走克隆」这件事悄悄丢掉。
    # The whole spec is used: clone fields live on it, and copying one field would
    # silently drop cloning.
    spec_ = voice_for_role("host", settings)
    if voice:
        spec_ = VoiceSpec(speaker=voice, language=spec_.language)   # --voice 指定 = 用内置音色
    console.print(f"[dim]发声方式：{spec_.mode}"
                  + (f"　参考音频 {spec_.ref_audio}" if spec_.ref_audio else
                     f"　音色 {spec_.speaker}") + "[/dim]")
    started = _time.perf_counter()
    with console.status(f"合成中（约 {len(say) / 4.5 * settings.tts_rtf_estimate:.0f} 秒起）…"):
        try:
            clip = provider.synthesize([SpeechSegment(text=say, voice=spec_)])
        except TTSError as exc:
            console.print(f"[red]合成失败：{exc}[/red]")
            raise typer.Exit(code=1) from exc
    elapsed = _time.perf_counter() - started

    path = Path(out).resolve()
    path.write_bytes(clip.wav)
    # 耗时与音频时长**分开报**：只给一个数字会被读成"这次跑了多久"，
    # 而音频时长通常小一个数量级（CPU 上 RTF≈13）。
    console.print(
        f"\n[green]已写入：[/green]{path}\n"
        f"[dim]{len(say)} 字　音频 {clip.seconds:.1f}s　耗时 {elapsed:.1f}s"
        f"　RTF {elapsed / clip.seconds:.2f}[/dim]"
    )
    if clip.artifacts:
        console.print(f"[dim]服务端产物：{clip.run}/（{len(clip.artifacts)} 个文件）[/dim]")
