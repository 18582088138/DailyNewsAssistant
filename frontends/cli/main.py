"""
命令行前端的入口 / The CLI entry point.

前端层不含任何业务逻辑，只负责「收参数 → 调核心 → 渲染结果」。
The front-end holds no business logic: it parses arguments, calls the core and renders.

这个文件**只做两件事**：把各 `cmd_*` 模块 import 进来（`@app.command()` 在
导入时完成注册），以及把 `app` 再导出。

`app` 必须留在这里：`pyproject.toml` 的 entry point 钉的是
`dna = "frontends.cli.main:app"`，换个位置就装不上了。
The re-export is load-bearing: the console-script entry point names this module.

命令分在哪几个文件里 / Where the commands live:
    cmd_env       version · doctor · config · sources · gui
    cmd_articles  fetch · add · list · show · sync · refetch · delete
    cmd_produce   produce · tts
    cmd_publish   digest · issue
    cmd_admin     stats · migrate-layout · probe
    cmd_prompt    prompt
    render        表格与进度的渲染辅助（不含命令）
"""

from __future__ import annotations

# 导入即注册：`@app.command()` 在模块导入时把命令挂到 app 上。
from frontends.cli import (  # noqa: F401
    cmd_admin,
    cmd_articles,
    cmd_env,
    cmd_produce,
    cmd_prompt,
    cmd_publish,
)
from frontends.cli.app import app, console

__all__ = ["app", "console"]


def run() -> None:
    """`python -m frontends.cli.main` 的入口。"""
    app()


if __name__ == "__main__":
    run()
