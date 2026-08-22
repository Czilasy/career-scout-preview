"""Scrape-only pure builders: 跳过 AI 直接查看结果的纯构建函数。

017 收口后，历史轮写入统一由 ``webui.result_rounds`` 服务承担（save_* 均已
迁移）；本模块只保留纯构建函数，供调用方拼装 script_params 等无副作用参数。

分层：app.py（路由）→ result_rounds.py（服务）→ store/mixins（数据访问）。
引用方向：scrape_only → result_rounds。
"""

from __future__ import annotations

# 纯构建函数唯一实现已随写入服务迁至 result_rounds（017 收口），
# 此处转发保持旧调用方公共 API 不变；引用方向 scrape_only → result_rounds。
from webui.result_rounds import build_undecided_result  # noqa: F401


def build_screen_script_params(screening_fields: dict | None, platform: str) -> dict:
    """与现有 AI 保存一致的 script_params 形态（screening + platform）。"""
    return {"screening": dict(screening_fields or {}), "platform": platform}


def merge_round_script_params(
    parent_script_params: dict | None,
    screening_fields: dict | None,
    platform: str,
) -> dict:
    """合并父抓取 script_params 与 AI 筛选快照参数，保留父轮关键词/城市。"""
    return {
        **dict(parent_script_params or {}),
        "screening": dict(screening_fields or {}),
        "platform": platform,
    }
