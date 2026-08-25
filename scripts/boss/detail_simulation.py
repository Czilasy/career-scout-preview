"""详情抓取人形模拟行为（024）：加载随机等待、人形滚动、概率鼠标移动。

参考 roadmap/boss-zhipin-scraper 的 ``human_scroll`` / ``human_mouse_jitter``
实现，按 024 冻结表 #12-#14 以档位（stable/balanced/extreme）给出参数：
- 详情加载等待（随机区间）：stable 5-10s / balanced 3-6s / extreme 1-2s
- 详情滚动（次数）：stable 3-7 / balanced 2-4 / extreme 0-1
- 鼠标移动概率：stable 50% / balanced 30% / extreme 无

custom 档与未传参路径不调用本模块（零仿真，与现状一致）。本模块为内部
行为域，不 import ``detail_scrape``，由 detail_scrape 仅做接线调用。
"""

from __future__ import annotations

import random
from typing import Any

# 024 冻结表 #12-#14：wait_range=加载等待区间（秒）、scroll_range=滚动次数
# 区间（含两端）、mouse_prob=鼠标移动概率。
SIMULATION_PARAMS: dict[str, dict[str, Any]] = {
    "stable": {
        "wait_range": (5.0, 10.0),
        "scroll_range": (3, 7),
        "mouse_prob": 0.5,
    },
    "balanced": {
        "wait_range": (3.0, 6.0),
        "scroll_range": (2, 4),
        "mouse_prob": 0.3,
    },
    "extreme": {
        "wait_range": (1.0, 2.0),
        "scroll_range": (0, 1),
        "mouse_prob": 0.0,
    },
}


def resolve_params(mode: str) -> dict[str, Any]:
    """按档位解析模拟参数；未知档位抛 ValueError。"""
    try:
        return SIMULATION_PARAMS[mode]
    except KeyError as exc:
        raise ValueError(f"未知模拟档位: {mode}") from exc


def simulate_after_load(ws, sid, *, params: dict[str, Any], sleeper, label_prefix: str = ""):
    """页面加载完成后、提取 JD 前模拟真人行为。

    - 随机等待 ``wait_range`` 秒（经 ``sleeper``，label 带 ``sim_*`` 前缀，
      便于测试与日志区分等待类型）。
    - 人形滚动 ``scroll_range`` 内随机次数：大部分向下滚、15% 概率回滚
      （模拟阅读回看），滚动间隔随机（2-4s 或 0.5-1.5s）。
    - 以 ``mouse_prob`` 概率随机移动一次鼠标（Input.dispatchMouseEvent）。

    任何模拟失败都吞掉（不中断抓取主流程，滚动/鼠标仅为行为伪装，
    失败不影响 JD 提取与 safe event 契约）。
    """
    # 1. 加载后随机等待
    wait = random.uniform(params["wait_range"][0], params["wait_range"][1])
    sleeper(wait, label=f"{label_prefix}sim_load_wait")

    # 2. 人形滚动（随机次数、随机距离、偶尔回滚）
    scrolls = random.randint(params["scroll_range"][0], params["scroll_range"][1])
    for _ in range(scrolls):
        if random.random() < 0.15:
            delta = -random.randint(50, 150)
        else:
            delta = random.randint(150, 500)
        try:
            ws.eval_js(f"window.scrollBy(0,{delta}); void(0);", sid)
        except Exception:
            pass
        if random.random() < 0.3:
            sleeper(random.uniform(2.0, 4.0), label=f"{label_prefix}sim_scroll_pause")
        else:
            sleeper(random.uniform(0.5, 1.5), label=f"{label_prefix}sim_scroll_pause")

    # 3. 概率鼠标移动
    if random.random() < params["mouse_prob"]:
        try:
            ws.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": random.randint(100, 800),
                "y": random.randint(100, 600),
            }, sid)
        except Exception:
            pass
