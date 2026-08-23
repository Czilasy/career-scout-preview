"""运行时上下文对象（021 B3，spec 021 data-model「PipelineContext」）。

create_app 内共享运行态（任务表、锁、store、run 写入助手、调优 runner、
source 工厂等）的显式载体；四个后台 runner（tuning_manifest / pipeline /
recrawl / ai_screen）及其嵌套助手经本对象访问共享状态，替代闭包捕获。

monkeypatch 红线：测试大量使用 ``patch("webui.app.X")``，实测清单为
``_BossCdpSource``、``boss``、``threading``、``ai_service``、
``ScraperExecutor``、``uuid``、``os``、``_theme_path``。这些符号不落为
实例属性（否则 import 时固化，补丁打不到真实执行路径），统一经
``__getattr__`` 动态门面在每次访问时读取 ``webui.app`` 模块属性；
runner 模块内对它们的使用 MUST 写作 ``ctx.boss`` / ``ctx.ai_service`` 等，
禁止自有 import 固化。

字段集以四个 runner 的完整闭包捕获清单为验收基准（2026-08-23 提取），
随 B4-B6 外迁批次按实际捕获补全，不新增语义。
"""

from __future__ import annotations

# webui.app 模块级、可被 patch("webui.app.X") 替换的符号。
# ctx.X 每次访问都动态读取 webui.app 模块属性，保证补丁生效。
_PATCHABLE_APP_SYMBOLS = frozenset({
    "boss",
    "_BossCdpSource",
    "ai_service",
    "ScraperExecutor",
    "threading",
    "uuid",
    "os",
    "_theme_path",
})


class PipelineContext:
    """create_app 共享运行态的显式载体。

    组装：create_app 内构造一次（共享态定义与核心助手就绪后），
    之后只增字段引用、不改字段值。

    - ``app``：Flask 应用（config 读取）
    - ``store``：TaskStore 实例
    - ``tasks`` / ``lock`` / ``resume_claims`` / ``executor``：
      任务表、读写锁、resume 占用集合、单工作线程池
    - ``write_run``：_write_run_unless_finished 终态安全写入助手
    - ``make_cdp_source``：冻结身份 source 工厂
    - ``tuning_round_runner``：调优轮次执行器
    """

    def __init__(self, *, app=None, store=None, tasks=None, lock=None,
                 resume_claims=None, executor=None, write_run=None,
                 make_cdp_source=None, tuning_round_runner=None):
        self.app = app
        self.store = store
        self.tasks = tasks if tasks is not None else {}
        self.lock = lock
        self.resume_claims = resume_claims if resume_claims is not None else set()
        self.executor = executor
        self.write_run = write_run
        self.make_cdp_source = make_cdp_source
        self.tuning_round_runner = tuning_round_runner

    def __getattr__(self, name):
        # 动态门面：可 patch 的 webui.app 模块级符号在调用时取用。
        # __getattr__ 只在常规属性查找失败时触发，实例字段不受影响。
        if name in _PATCHABLE_APP_SYMBOLS:
            import webui.app as _app_module

            return getattr(_app_module, name)
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )
