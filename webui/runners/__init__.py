"""后台任务 runner 包（021 B4-B6 拆分自 webui/app.py 的 create_app 闭包）。

每个 runner 以 ``ctx``（webui.pipeline_context.PipelineContext）为第一
参数显式访问共享运行态；原可 patch 符号按 031 B9 语义拆解：boss、
ai_service、ScraperExecutor、threading、uuid、os 模块级直连（patch
其真实家），_BossCdpSource 经 ctx.source_class、_theme_path 经
ctx.theme_path 显式注入。runners 之间禁止互相 import。
"""
