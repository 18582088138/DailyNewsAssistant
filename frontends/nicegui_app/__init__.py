"""
NiceGUI 前端 / The NiceGUI front-end：文章台账工作台。

    dna gui

三个模块 / Three modules:
    `main`         装配与路由
    `ledger_table` 表格渲染（一篇文章一行，每种产物一列）
    `actions`      按钮 → dna.produce 的桥梁，**所有 LLM 调用走 run.io_bound**

前端层不含业务逻辑：CLI 的 `dna produce` 与这里的重做按钮调的是同一个
`dna.produce.service.produce()`，因此两个前端不会各错一套。
The front-end holds no business logic: `dna produce` and the redo buttons both call
`dna.produce.service.produce()`, so the two cannot break differently.
"""
