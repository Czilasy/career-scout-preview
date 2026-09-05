"""搜索执行入口使用的白箱计划和提交失败记录。"""

from __future__ import annotations

from typing import Any

from webui.pipeline_exec import expand_combinations
from webui.whitebox import WhiteboxService


def scrape_plan(script_params: dict[str, Any], pages: int) -> dict[str, Any]:
    units = []
    for item in expand_combinations(script_params):
        units.append({
            "unit_key": str(item.get("combo_key") or f"{item.get('keyword', '')}|{item.get('city', '')}"),
            "unit_kind": "keyword_city",
            "stage": "scrape_list",
            "planned_pages": pages,
            "required": True,
        })
    return {"stages": ["scrape_list"], "units": units}


def begin_scrape_whitebox(store: Any, task_id: str, script_params: dict[str, Any], pages: int) -> None:
    WhiteboxService(store).begin("scrape", task_id, scrape_plan(script_params, pages))


def mark_scrape_submission_failed(
    store: Any,
    task_id: str,
    script_params: dict[str, Any],
    reason: str,
    *,
    stage: str,
    pages: int = 0,
) -> None:
    WhiteboxService(store).mark_submission_failed(
        "scrape",
        task_id,
        scrape_plan(script_params, pages),
        reason,
        stage=stage,
    )
