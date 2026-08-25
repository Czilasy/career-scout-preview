"""档位配置数据域（024）：三档×三规模冻结数值、任务规模阈值、get_mode_config。

自 ``webui/execution_config.py`` 外迁（该文件已达 800 行硬上限，宪法 VI
「达到红线后后续改动 MUST 开新模块分流」）。本模块只承载档位数据与查询函数，
不 import ``webui.store``；``ExecutionConfigSnapshot`` 在函数内延迟 import，
避免与 ``execution_config`` 的模块级循环依赖（延迟 import 为项目既有惯例，
见 ``store_config.py``）。

- ``MODE_CONFIGS``：stable/balanced/extreme 三档 × small/medium/large 三规模，
  一档一套、三规模同值（024 冻结表 #2-#11）；3×3 matrix 结构保留，select_mode /
  create_mode_version 契约不变。
- 极限档 = 固化当前自定义值（与 custom 解耦，用户改 custom 不影响极限档）。
- ``pages`` 不属于配置快照（FR-009 维持）。
"""

from __future__ import annotations

from typing import Any

# 任务规模新口径（024）：总页数（组数 × 每组合页数）<15 小 / 15~30 中 / >30 大，
# 替换旧 9/49 阈值。SMALL_TASK_MAX=14（1-14 为小）、MEDIUM_TASK_MAX=30（15-30 为中）。
SMALL_TASK_MAX = 14
MEDIUM_TASK_MAX = 30

# 稳定档（024 冻结表）
_STABLE_CONFIG: dict[str, Any] = {
    "inter_combo_delay": 20,
    "detail_batch_size": 15,
    "detail_interval": 15,
    "detail_reset_every": 2,
    "detail_batch_cooldown": 15,
    "detail_tab_pool_size": 2,
    "screen_batch_size": 30,
    "screen_concurrency": 3,
    "match_batch_size": 3,
    "match_concurrency": 3,
}

# 平衡档（024 冻结表）
_BALANCED_CONFIG: dict[str, Any] = {
    "inter_combo_delay": 13,
    "detail_batch_size": 20,
    "detail_interval": 10,
    "detail_reset_every": 3,
    "detail_batch_cooldown": 10,
    "detail_tab_pool_size": 3,
    "screen_batch_size": 40,
    "screen_concurrency": 4,
    "match_batch_size": 4,
    "match_concurrency": 4,
}

# 极限档 = 固化当前自定义值（advanced_settings.json 现值，与 custom 解耦）
_EXTREME_CONFIG: dict[str, Any] = {
    "inter_combo_delay": 10,
    "detail_batch_size": 30,
    "detail_interval": 2,
    "detail_reset_every": 4,
    "detail_batch_cooldown": 5,
    "detail_tab_pool_size": 5,
    "screen_batch_size": 50,
    "screen_concurrency": 5,
    "match_batch_size": 5,
    "match_concurrency": 5,
}

# 3×3 matrix 结构保留、三规模同值（一档一套）
MODE_CONFIGS: dict[str, dict[str, dict[str, Any]]] = {
    "stable": {
        "small": _STABLE_CONFIG,
        "medium": _STABLE_CONFIG,
        "large": _STABLE_CONFIG,
    },
    "balanced": {
        "small": _BALANCED_CONFIG,
        "medium": _BALANCED_CONFIG,
        "large": _BALANCED_CONFIG,
    },
    "extreme": {
        "small": _EXTREME_CONFIG,
        "medium": _EXTREME_CONFIG,
        "large": _EXTREME_CONFIG,
    },
}


def get_mode_config(mode: str, *, task_size: str) -> Any:
    """FR-051/FR-056: 获取指定模式和规模的配置快照。

    FR-009: pages 不出现在返回结果中（ExecutionConfigSnapshot 不含 pages）。
    """
    from webui.execution_config import ExecutionConfigSnapshot

    if mode not in MODE_CONFIGS:
        raise ValueError(f"未知模式: {mode}")
    if task_size not in ("small", "medium", "large"):
        raise ValueError(f"未知任务规模: {task_size}")
    return ExecutionConfigSnapshot.create(MODE_CONFIGS[mode][task_size])
