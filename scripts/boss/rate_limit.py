# -*- coding: utf-8 -*-

"""运行级 API 请求计数限流（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

import threading
from scripts.boss.exceptions import RequestLimitExceededError
from scripts.boss.constants import log
import sys as _sys
def _facade():
    return _sys.modules.get("scripts.boss_cdp_raw")

# 全局请求计数器
# 运行级请求计数器（B053）：in-process 模式下多轮任务共处同一进程，必须按单次抓取运行隔离；
# worker 线程通过锁共享同一计数对象。
_request_counter = None


_request_counter_lock = threading.Lock()


def begin_request_run():
    """开启一次抓取运行的独立请求计数（B053）。"""
    global _request_counter
    with _request_counter_lock:
        _request_counter = 0


def incr_request():
    """递增运行级请求计数，命中上限时抛出显式异常（B053）。"""
    global _request_counter
    with _request_counter_lock:
        if _request_counter is None:
            _request_counter = 0
        _request_counter += 1
        current = _request_counter
        if current > _facade().MAX_API_REQUESTS:
            raise RequestLimitExceededError(
                f"已达到单次最大请求数 {_facade().MAX_API_REQUESTS}，停止抓取")
        if current >= _facade().MAX_API_REQUESTS * 0.8:
            log.warning(f"⚠️ 请求次数接近上限: {current}/{_facade().MAX_API_REQUESTS}")
