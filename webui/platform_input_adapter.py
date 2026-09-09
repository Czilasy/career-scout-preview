"""平台输入快照 hash 适配器。

轮询编排只依赖 ``resolve_platform_input_adapter`` 的公开能力；平台字段
差异留在这里，不让公共调度器维护平台名称分支。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from webui.pipeline_exec_artifacts import _combo_hash


@dataclass(frozen=True)
class PlatformInputAdapter:
    """一个平台的计划输入 hash 计算能力。"""

    key: str
    _compute_input_hash: Callable[[dict, int], str]
    _build_plan_item: Callable[..., dict[str, Any]] | None = None
    _apply_resume_fields: Callable[[dict[str, Any], int, list[dict]], None] | None = None

    def compute_input_hash(self, plan_item: dict, target_pages: int) -> str:
        """按平台合同重算子范围计划的输入 hash。"""
        return self._compute_input_hash(plan_item, int(target_pages))

    def build_plan_item(
            self, *, keyword: str, city: str, location: dict,
            combo_key: str, target_pages: int, start_page: int,
            list_output_path: str | None, source_filters: dict,
            existing_jobs: list[dict] | None = None,
    ) -> dict[str, Any]:
        """构造平台 source 所需的计划输入，公共编排器不感知字段差异。"""
        if self._build_plan_item is None:
            raise RuntimeError(f"platform plan builder unavailable: {self.key}")
        return self._build_plan_item(
            keyword=keyword,
            city=city,
            location=location,
            combo_key=combo_key,
            target_pages=int(target_pages),
            start_page=max(1, int(start_page)),
            list_output_path=list_output_path,
            source_filters=source_filters,
            existing_jobs=list(existing_jobs or []),
        )

    def apply_resume_fields(
            self, plan_item: dict[str, Any], *, start_page: int,
            existing_jobs: list[dict] | None = None,
    ) -> None:
        """更新重启续抓字段，保留平台特有的计划形状。"""
        plan_item["start_page"] = max(1, int(start_page))
        if self._apply_resume_fields is not None:
            self._apply_resume_fields(
                plan_item, max(1, int(start_page)), list(existing_jobs or []),
            )


def compute_zhilian_input_hash(payload: Any) -> str:
    """计算智联输入快照 hash，保留旧 source adapter 的稳定算法。"""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _compute_boss_input_hash(plan_item: dict, target_pages: int) -> str:
    return _combo_hash(
        str(plan_item.get("keyword") or ""),
        str(plan_item.get("city") or ""),
        int(target_pages),
        plan_item.get("source_filters") or {},
    )


def _compute_zhilian_plan_hash(plan_item: dict, target_pages: int) -> str:
    return compute_zhilian_input_hash({
        "platform": "zhilian",
        "keyword": str(plan_item.get("keyword") or ""),
        "city": plan_item.get("city") or {},
        "target_pages": int(target_pages),
        "route_city_code": str(plan_item.get("route_city_code") or ""),
    })


def _build_boss_plan_item(*, keyword: str, city: str, location: dict,
                          combo_key: str, target_pages: int, start_page: int,
                          list_output_path: str | None, source_filters: dict,
                          existing_jobs: list[dict]) -> dict[str, Any]:
    """BOSS plan shape; kept behind the platform adapter boundary."""
    del location, existing_jobs
    return {
        "keyword": keyword,
        "city": city,
        "source_filters": dict(source_filters or {}),
        "combo_key": combo_key,
        "target_pages": target_pages,
        "input_hash": _compute_boss_input_hash({
            "keyword": keyword,
            "city": city,
            "source_filters": source_filters or {},
        }, target_pages),
        "list_output_path": list_output_path,
        "start_page": start_page,
    }


def _build_zhilian_plan_item(*, keyword: str, city: str, location: dict,
                             combo_key: str, target_pages: int, start_page: int,
                             list_output_path: str | None, source_filters: dict,
                             existing_jobs: list[dict]) -> dict[str, Any]:
    """智联 plan shape; city snapshot and route code stay in the adapter."""
    del source_filters
    from webui.location_scope import build_zhilian_city_snapshot
    from webui.platforms import resolve_platform_city

    city_entry = resolve_platform_city("zhilian", city)
    if location:
        city_snapshot = build_zhilian_city_snapshot(location, city_entry)
        route_city_code = str(
            location.get("city_code") or city_entry.platform_code
        )
    else:
        city_snapshot = {
            "name": city_entry.name,
            "label": city_entry.label,
            "platform_code": city_entry.platform_code,
            "mapping_version": city_entry.mapping_version,
        }
        route_city_code = city_entry.platform_code
    payload = {
        "platform": "zhilian",
        "keyword": keyword,
        "city": city_snapshot,
        "target_pages": target_pages,
        "route_city_code": route_city_code,
    }
    return {
        "platform": "zhilian",
        "keyword": keyword,
        "city": city_snapshot,
        "combo_key": combo_key,
        "target_pages": target_pages,
        "input_hash": compute_zhilian_input_hash(payload),
        "list_output_path": list_output_path,
        "start_page": start_page,
        "existing_jobs": list(existing_jobs or []),
        "route_city_code": route_city_code,
    }


def _apply_zhilian_resume_fields(
        plan_item: dict[str, Any], start_page: int, existing_jobs: list[dict],
) -> None:
    """智联重启时保留已抓职位，避免公共编排器写平台字段。"""
    plan_item["start_page"] = max(1, int(start_page))
    plan_item["existing_jobs"] = list(existing_jobs)


_PLATFORM_INPUT_ADAPTERS = {
    "boss": PlatformInputAdapter(
        "boss", _compute_boss_input_hash, _build_boss_plan_item,
    ),
    "zhilian": PlatformInputAdapter(
        "zhilian", _compute_zhilian_plan_hash, _build_zhilian_plan_item,
        _apply_zhilian_resume_fields,
    ),
}


def resolve_platform_input_adapter(platform: str | None) -> PlatformInputAdapter:
    """返回平台输入 hash 能力；空/未知平台保持 BOSS 兼容默认。"""
    key = str(platform or "boss")
    return _PLATFORM_INPUT_ADAPTERS.get(key, _PLATFORM_INPUT_ADAPTERS["boss"])
