#!/usr/bin/env python3
"""
BOSS直聘职位抓取 + 分析 — 纯 CDP raw protocol

功能:
  1. 搜索特定职位 (关键词 + 城市)
  2. 筛选公司规模、融资阶段、薪资范围、经验、学历、行业
  3. 抓取详情页 JD 并分析薪资范围和技能要求
  4. 输出结构化 JSON + CSV + 终端分析报告
  5. 环境检查、Chrome CDP 自动启动、登录状态检测

用法:
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --city 101020100 --pages 5
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --scale 305 --salary 406
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --analysis
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --detail
  uv run python3 scripts/boss_cdp_raw.py --check
  uv run python3 scripts/boss_cdp_raw.py --setup-chrome
  uv run python3 scripts/boss_cdp_raw.py --version

021 B8 T026 后本文件为兼容门面：实现按域归组至 scripts/boss/，
re-export 全部既有符号（经 __getattr__ 动态代理，保持旧 import 与 patch 面），
CLI 行为不变（宪法 VI）。
"""

__version__ = "1.8.1"

import os
import random
import sys
import time

# Direct execution (`python scripts/boss_cdp_raw.py`) puts only `scripts/` on
# sys.path. Add the repository root before importing the package-qualified
# companion module so CLI modes work outside `python -m` invocation.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 延迟赋值全局（B044 运行时依赖注入）：外部以 boss.requests / boss.websocket
# 读写，权威保持在本门面命名空间，require_runtime_dependencies 写入时同步回写。
websocket = None
requests = None
_run_active = False  # 是否正在 run_search_programmatic 组合运行内

from scripts.boss.exceptions import CDPUnavailableError, ResultFileWriteError

from scripts.boss_cdp_signals import (
    RATE_LIMIT_KEYWORDS,
    RISK_CONTROL_KEYWORDS,
    DETAIL_RATE_LIMIT_KEYWORDS,
    VERDICT_CONFIRMED,
    VERDICT_RETRY,
    VERDICT_STOP,
    api_code_diagnosis,
    api_code_hint,
    classify_list_diagnosis,
    detail_page_hint,
    emit_failure_line,
    is_risk_api_code,
    looks_like_detail_rate_limited,
    looks_like_rate_limited,
    looks_like_risk_control,
)

import scripts.boss.browser as _boss_browser
import scripts.boss.cdp_session as _boss_cdp_session
import scripts.boss.city_map as _boss_city_map
import scripts.boss.cli as _boss_cli
import scripts.boss.constants as _boss_constants
import scripts.boss.detail_analyze as _boss_detail_analyze
import scripts.boss.detail_parse as _boss_detail_parse
import scripts.boss.detail_scrape as _boss_detail_scrape
import scripts.boss.exceptions as _boss_exceptions
import scripts.boss.login as _boss_login
import scripts.boss.output as _boss_output
import scripts.boss.programmatic as _boss_programmatic
import scripts.boss.rate_limit as _boss_rate_limit
import scripts.boss.runtime as _boss_runtime
import scripts.boss.search as _boss_search
import scripts.boss.session_import as _boss_session_import
import scripts.boss.smoke as _boss_smoke

_BOSS_MODULES = [m for m in list(globals().values()) if getattr(m, "__name__", "").startswith("scripts.boss.")]

# 门面自注册与转发（021 B8 T026）：
# - 模块模式（__name__ == scripts.boss_cdp_raw）：属性经 __getattr__ 代理到子模块。
# - 测试 exec 模式（test_boss_programmatic 以 boss_cdp_raw_programmatic 从文件
#   路径 exec 门面）：覆盖固定名指向本实例，属性读取优先转发到模块门面
#   （_BOSS_PRIMARY），使 patch 模块门面 / patch exec 实例两个场景互通。
if __name__ != "scripts.boss_cdp_raw":
    _BOSS_PRIMARY = sys.modules.get("scripts.boss_cdp_raw")
    sys.modules["scripts.boss_cdp_raw"] = sys.modules[__name__]
else:
    _BOSS_PRIMARY = None


def __getattr__(name):
    if _BOSS_PRIMARY is not None:
        try:
            return getattr(_BOSS_PRIMARY, name)
        except AttributeError:
            pass
    for _m in _BOSS_MODULES:
        if hasattr(_m, name):
            return getattr(_m, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


if __name__ == "__main__":
    try:
        sys.modules[__name__].main()  # main 经 __getattr__ 代理解析（LOAD_GLOBAL 不触发模块 __getattr__）
    except CDPUnavailableError as exc:
        # 026：浏览器失联统一映射退出码 2（source_cdp_unavailable），
        # 让编排层走 is_browser_lost → 自动重启 / 重启失败暂停，
        # 而不是裸退出 1 被分类成 source_unknown_error 静默标待确认。
        emit_failure_line("source_cdp_unavailable", str(exc))
        sys.exit(2)
    except ConnectionError as exc:
        # CDPSession.send 在运行中 WebSocket 断开时抛内置 ConnectionError
        # （cdp_session.py）；同样归入浏览器失联退出码 2。
        emit_failure_line("source_cdp_unavailable", str(exc))
        sys.exit(2)
    except ResultFileWriteError as exc:
        # 026 B079：结果文件写失败重试耗尽——专门退出码 + 结构化失败行
        # （source_result_write_failed），绝不回退退出码 1 误报登录失效。
        emit_failure_line("source_result_write_failed", str(exc))
        sys.exit(4)
