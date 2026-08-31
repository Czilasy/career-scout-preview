"""运行时上下文对象（021 B3，spec 021 data-model「PipelineContext」）。

create_app 内共享运行态（任务表、锁、store、run 写入助手、调优 runner、
source 工厂等）的显式载体；四个后台 runner（tuning_manifest / pipeline /
recrawl / ai_screen）及其嵌套助手经本对象访问共享状态，替代闭包捕获。

031 B9（FR-020）：动态门面已拆除。可 patch 符号（``boss`` /
``_BossCdpSource`` / ``ai_service`` / ``ScraperExecutor`` / ``threading`` /
``uuid`` / ``os`` / ``_theme_path``）不再经 ``__getattr__`` 回读
``webui.app``——``source_class`` / ``theme_path`` 为构造期显式注入字段，
其余符号由生产模块直连真实家（``scripts.boss_cdp_raw`` / ``webui.ai`` /
标准库）。测试打桩一律指向真实家（如 ``webui.ai.X``、
``webui.process_executor.ScraperExecutor.execute``、``threading.Timer``），
不再隔空改主文件。

字段集以四个 runner 的完整闭包捕获清单为验收基准（2026-08-23 提取），
随 B4-B6 外迁批次按实际捕获补全，不新增语义。
"""

from __future__ import annotations


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
    - ``source_class``：BOSS 数据源类（031 B9 构造期注入）
    """

    def __init__(self, *, app=None, store=None, tasks=None, lock=None,
                 resume_claims=None, executor=None, write_run=None,
                 make_cdp_source=None, tuning_round_runner=None,
                 source_class=None, theme_path=None, **extra):
        self.app = app
        self.store = store
        self.tasks = tasks if tasks is not None else {}
        self.lock = lock
        self.resume_claims = resume_claims if resume_claims is not None else set()
        self.executor = executor
        self.write_run = write_run
        self.make_cdp_source = make_cdp_source
        self.tuning_round_runner = tuning_round_runner
        # 031 B9：BOSS 数据源类显式注入（原先经 webui.app._BossCdpSource 模块
        # 全局在调用时取用，测试靠 patch 主文件替换；改为构造期注入后，测试
        # 直接替换本字段即可，不必再隔空改主文件）。
        self.source_class = source_class
        # 031 B9：主题文件路径函数显式注入（原经动态门面取 webui.app._theme_path）。
        self.theme_path = theme_path
        # 外迁批次（B4-B6）按实际捕获清单补全的 create_app 级助手/常量
        # （is_user_finished、record_pause_failure、event_stage_names 等），
        # 统一落实例属性；不新增语义，只承载引用。
        for name, value in extra.items():
            setattr(self, name, value)
