"""
文本产物的生成器 / The text generators.

「一种产物 → 调哪个构建器、用哪段提示词、算哪个字数窗口」都在这里；
**「要不要生成、生成完怎么记账」不在这里**（那是 `service.py` 的事）。
"""

from __future__ import annotations

from pathlib import Path

from dna.core.config import Profile, Settings, get_settings
from dna.core.logging import get_logger
from dna.core.models import Article, Cluster, NewsItem
from dna.llm.base import LLMProvider
from dna.narration.longform import LongformMode, build_longform, can_build_longform
from dna.narration.script_builder import build_narration, build_short_video
from dna.pipeline.source import load_candidates
from dna.produce.documents import (
    front_matter,
    script_block,
    strip_front_matter,
)
from dna.produce.results import Generated
from dna.produce.tasks import (
    ProductionKind,
    TaskSpec,
    char_window,
    normalize_lang,
    spec,
)
from dna.store.ledger import Ledger

logger = get_logger("produce.generate")


def generate_text(
    task: TaskSpec,
    article: Article,
    llm: LLMProvider,
    directory: Path,
    article_id: str,
    *,
    lang: str,
    variant: str | None,
    instructions: str,
    settings: Settings,
    profile: Profile,
) -> Generated:
    """
    分派到具体的文本生成器 / Dispatch to the concrete text generator.

    时长区间与结尾引导语都从 profile 取 / Windows and sign-off come from the profile:
        写死在构建器默认值里的话，`profile.yaml` 里那三行时长配置就是死配置——
        改了没反应，比没有这个配置更糟。
        Hard-coding them in the builders' defaults would leave the three duration lines in
        `profile.yaml` inert: editing them would silently do nothing, which is worse than
        not offering the setting at all.
    """
    if task.kind is ProductionKind.SUMMARY:
        if lang == "zh":
            from dna.pipeline.summarize import summarize_cluster

            cluster = as_cluster(article, article_id)
            result = summarize_cluster(
                cluster,
                llm,
                instructions=instructions,
                chars=char_window(task.kind, profile),
                max_rewrites=profile.summary_max_rewrites,
                body_chars=profile.summary_body_chars,
            )
            if result.degraded:
                raise RuntimeError("摘要调用失败，已退回标题；请重试")
            body = front_matter(article, "总结") + result.summary + "\n"
            # calls 与 within_target 必须原样带出来：写死 calls=1 会漏掉回炉那几次，
            # 丢掉 within_target 则让写超字数的摘要一路显示成「合格」——
            # 摘要的长度检查就白做了。
            # Both must survive: a hard-coded 1 hides the rewrite calls, and dropping the
            # flag reports an over-length summary as fine, voiding its length check.
            return Generated(
                text=body,
                chars=len(result.summary),
                calls=result.calls,
                within_target=result.within_target,
            )

        # 英文总结**翻译已写好的中文**，不重写一遍：便宜，而且中英两版保证说的是
        # 同一件事。见 `tasks.TaskSpec.translated_from`。
        from dna.pipeline.translate import translate_batch

        chinese = read_production(
            article_id, ProductionKind.SUMMARY, lang="zh", settings=settings
        )
        summary_zh = strip_front_matter(chinese)
        translated = translate_batch(
            [(article_id, article.title, summary_zh)], llm, instructions=instructions
        )
        if article_id not in translated:
            raise RuntimeError("翻译未返回该条目")
        title_en, summary_en = translated[article_id]
        body = f"# {title_en}\n\n> Source: {article.url}\n\n{summary_en}\n"
        return Generated(text=body, chars=len(summary_en), calls=1)

    if task.kind is ProductionKind.SHORTVIDEO:
        low, high = profile.video_duration_seconds
        script = build_short_video(
            article,
            llm,
            low=low,
            high=high,
            chars=char_window(task.kind, profile),
            cta=profile.cta_line,
            lang=lang,
            instructions=instructions,
            max_rewrites=profile.copy_max_rewrites,
            body_chars=profile.script_body_chars,
        )
        body = front_matter(article, f"短视频文案（{lang}）") + script_block(script)
        return Generated(
            text=body,
            chars=script.chars,
            seconds=script.seconds,
            calls=script.calls,
            within_target=script.within_target,
        )

    if task.kind is ProductionKind.NARRATION:
        low, high = profile.narration_duration_seconds
        script = build_narration(
            article,
            llm,
            low=low,
            high=high,
            chars=char_window(task.kind, profile),
            cta=profile.cta_line,
            lang=lang,
            instructions=instructions,
            max_rewrites=profile.copy_max_rewrites,
            body_chars=profile.script_body_chars,
        )
        body = front_matter(article, f"口播文案（{lang}）") + script_block(script)
        return Generated(
            text=body,
            chars=script.chars,
            seconds=script.seconds,
            calls=script.calls,
            within_target=script.within_target,
        )

    if task.kind is ProductionKind.LONGFORM:
        ok, reason = can_build_longform(
            article, min_body_chars=profile.tuning.longform_min_body_chars
        )
        if not ok:
            raise ValueError(reason)

        mode = LongformMode(variant or LongformMode.FEATURE)
        low, high = profile.longform_duration_seconds
        tuning = profile.tuning
        result = build_longform(
            article, llm, mode=mode, low=low, high=high, lang=lang,
            instructions=instructions,
            expansion_ratio=tuning.longform_expansion_ratio,
            # 章节数就是这一篇的调用次数上限（每节一次），所以它必须可配
            sections=(tuning.longform_min_sections, tuning.longform_max_sections),
        )
        label = "专题" if mode is LongformMode.FEATURE else "访谈"
        outline = "\n".join(f"{i}. {s_.title}" for i, s_ in enumerate(result.sections, 1))
        body = (
            front_matter(
                article, f"长文案 · {label} · {lang}（约 {result.seconds / 60:.0f} 分钟）"
            )
            + f"<!-- 提纲\n{outline}\n-->\n\n"
            + result.text
            + "\n"
        )
        return Generated(
            text=body,
            chars=len(result.text),
            seconds=result.seconds,
            calls=result.calls,
            sidecar=result.to_json_dict(),
        )

    raise ValueError(f"未知的产物类型：{task.kind}")


def as_cluster(article: Article, article_id: str) -> Cluster:
    """
    把单篇文章包成 Cluster / Wrap one article as a single-member cluster.

    复用 `pipeline/summarize` 而不是另写一份单篇摘要：提示词、降级处理、
    标签抽取全都是现成的，重写一遍只会多出一处要同步维护的地方。
    Reuses `pipeline/summarize` rather than duplicating a single-article summariser: the
    prompt, degradation handling and tag extraction already exist, and a second copy
    would only be another place to keep in sync.
    """
    item = NewsItem(
        id=article_id,
        source_id="",
        via="rss",  # type: ignore[arg-type] - NewsItem 会做枚举转换
        url=article.url,
        canonical_url=article.url,
        title=article.title,
        text=article.text,
        published_at=article.published_at,
        media=article.media,
    )
    return Cluster(id=article_id, members=[item], canonical_url=article.url)


def load_article(article_id: str, settings: Settings) -> Article | None:
    """
    从落盘目录读回完整文章 / Read the full article back from disk.

    复用 `pipeline/source.load_candidates`：它已经处理好了「读 meta.json、
    目录缺失时降级、媒体项损坏时跳过」这些情况。
    Reuses `pipeline/source.load_candidates`, which already handles reading meta.json,
    degrading when the directory is gone, and skipping corrupt media entries.
    """
    items = load_candidates(settings=settings, article_ids=[article_id])
    if not items:
        return None

    item = items[0]
    return Article(
        url=item.url,
        title=item.title,
        text=item.text,
        published_at=item.published_at,
        media=item.media,
        extraction_ok=bool(item.text),
    )

def read_production(
    article_id: str,
    kind: ProductionKind | str,
    *,
    lang: str = "",
    settings: Settings | None = None,
) -> str:
    """读回已生成的产物内容 / Read back a production's text; empty when absent."""
    lang = normalize_lang(lang)
    s = settings or get_settings()
    record = Ledger(s.db_file).get(article_id)
    if record is None or not record.store_dir:
        return ""

    path = s.output_path / record.store_dir / spec(kind).filename_for(lang)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
