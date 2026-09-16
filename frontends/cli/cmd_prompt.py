"""提示词实验台 / The prompt lab。"""

from __future__ import annotations

import typer
from rich.table import Table

from dna.core.config import get_settings
from frontends.cli.app import app, console
from frontends.cli.render import (
    _resolve_article,
)


@app.command(name="prompt")
def prompt_lab_cmd(
    article_id: str | None = typer.Argument(
        None, help="文章 id，前 8 位即可；配 --list 时可省略"
    ),
    task: str | None = typer.Option(
        None, "--task", "-t", help="summary | shortvideo | narration | longform"
    ),
    lang: str = typer.Option("zh", "--lang", "-l", help="输出语言：zh | en"),
    variant: str | None = typer.Option(
        None, "--variant", help="长文案形式：feature（专题）| interview（访谈）"
    ),
    instructions: str = typer.Option(
        "", "--instructions", "-i", help="本次的额外要求，测试它接在提示词末尾的效果"
    ),
    run_live: bool = typer.Option(
        False, "--run", help="真机跑一次并打印产物（**会产生费用**）"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过 --run 的确认"),
    no_cache: bool = typer.Option(
        False, "--no-cache", help="配 --run：绕过 LLM 缓存，在同一份提示词上另抽一个样本"
    ),
    save_dir: bool = typer.Option(
        False, "--save", help="把提示词与产物存到 outputs/prompt_lab/，方便前后对比"
    ),
    list_tasks: bool = typer.Option(False, "--list", help="列出各任务读哪些提示词文件"),
    full: bool = typer.Option(False, "--full", help="打印完整提示词，不折叠长文案的中间几节"),
) -> None:
    """
    提示词调试台 / Tune the prompts —— 看提示词、试提示词。

    提示词正文都在 `config/prompts/`，一个任务一个文件。改完用这个命令验证：

    \b
      dna prompt --list                       # 各任务读哪些文件
      dna prompt a1b2c3d4 -t narration        # 看真正发出去的提示词（免费）
      dna prompt a1b2c3d4 -t narration --run  # 真机跑一次看产物（**计费**）

    **默认不花钱**：不加 --run 时一次 LLM 调用都不发，只把提示词渲染出来。
    长文案会打印「提纲 + 每节」十几条，因为每一节的提示词都不一样。

    看到的提示词与 `dna produce` / 工作台点「重做」发出去的**逐字节相同**——
    两条路走的是同一个 `dna.produce.service.generate_text()`，只是这里换了 provider。
    所以在这里调好的提示词，在应用里必然生效。

    ⚠️ `--run` 不写文件也不记台账：调试台跑十次不该在产物目录里留十份垃圾。
    要正式产出请用 `dna produce`。
    """
    from dna.produce import prompt_lab
    from dna.produce.tasks import spec

    if list_tasks:
        table = Table(title="提示词文件 / prompt files", header_style="bold")
        table.add_column("任务", style="bold")
        table.add_column("提示词文件（config/prompts/ 下）")
        for kind in prompt_lab.TASKS:
            table.add_row(
                f"{kind}\n[dim]{spec(kind).label}[/dim]",
                "\n".join(prompt_lab.prompt_files_for(kind)),
            )
        console.print(table)
        console.print(
            "\n[dim]占位符写 {{name}}；整行 `## @key` 起一个新块。"
            "改完运行 dna prompt <id> -t <task> 看效果。[/dim]"
        )
        return

    if article_id is None or task is None:
        console.print("[yellow]请给出文章 id 与 --task，或用 --list 看有哪些任务。[/yellow]")
        console.print(f"[dim]可选任务：{' | '.join(str(k) for k in prompt_lab.TASKS)}[/dim]")
        raise typer.Exit(code=1)

    record = _resolve_article(article_id)
    task_spec = spec(task)
    if task_spec.kind not in prompt_lab.TASKS:
        console.print(f"[red]{task} 不调用 LLM，没有提示词可调。[/red]")
        raise typer.Exit(code=1)
    if task_spec.needs_variant and variant is None:
        variant = "feature"
        console.print("[dim]未指定 --variant，按专题（单角色）[/dim]")

    if run_live and not yes:
        console.print(
            f"[yellow]--run 会真实调用 LLM，预估 {task_spec.approx_calls} 次，产生费用。[/yellow]"
        )
        if not typer.confirm("继续？"):
            raise typer.Exit(code=1)

    if run_live:
        with console.status(f"真机生成中：{task_spec.label}…"):
            result = prompt_lab.run(
                record.id,
                task_spec.kind,
                lang=lang,
                variant=variant,
                instructions=instructions,
                cache=not no_cache,
            )
    else:
        result = prompt_lab.render(
            record.id,
            task_spec.kind,
            lang=lang,
            variant=variant,
            instructions=instructions,
        )

    console.print(f"\n[bold]{result.article_title}[/bold]")
    console.print(
        f"[dim]{result.article_id[:8]} · 正文 {result.body_chars} 字 · "
        f"provider {result.provider}[/dim]"
    )
    console.print(f"[dim]提示词文件：{'、'.join(prompt_lab.prompt_files_for(task_spec.kind))}[/dim]")

    if not result.ok:
        console.print(f"\n[red]{result.error}[/red]")
        raise typer.Exit(code=1)

    # 长文案十几条提示词全打出来会淹掉终端；默认只展开首尾，中间给一行提要。
    # `--full` 全展开，`--save` 落盘之后随便看。
    total = len(result.prompts)
    for index, prompt in enumerate(result.prompts, 1):
        folded = not full and total > 4 and 2 <= index < total
        console.rule(f"[bold cyan]提示词 {index}/{total}[/bold cyan]")
        if folded:
            console.print(f"[dim]（{len(prompt)} 字，已折叠；--full 展开）[/dim]")
            continue
        console.print(prompt, markup=False, highlight=False)

    if result.output:
        console.rule("[bold green]产物[/bold green]")
        console.print(result.output, markup=False, highlight=False)

    console.print(f"\n{result.summary()}")

    if save_dir:
        from datetime import datetime

        from dna.core.naming import slugify

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        root = (
            get_settings().output_path
            / "prompt_lab"
            / f"{stamp}__{task_spec.kind}.{result.lang}__{slugify(result.article_title, 24)}"
        )
        prompt_lab.save(result, root)
        console.print(f"[dim]已存：{root}[/dim]")
