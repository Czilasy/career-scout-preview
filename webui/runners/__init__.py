"""后台任务 runner 包（021 B4-B6 拆分自 webui/app.py 的 create_app 闭包）。

每个 runner 以 ``ctx``（webui.pipeline_context.PipelineContext）为第一
参数显式访问共享运行态；可被 ``patch("webui.app.X")`` 的符号（boss、
_BossCdpSource、ai_service、ScraperExecutor、threading、uuid、os、
_theme_path）MUST 经 ctx 动态门面取用，禁止本包内 import 固化。
runners 之间禁止互相 import。
"""
