"""平台筛选 schema 投影与取值校验（021 B7 自 platforms.py 搬运）。"""

from __future__ import annotations

from typing import Any

from webui.platforms_registry import get_platform
from webui.platforms_schema import _BOSS_EXCLUSIVE_FIELDS, _ZHILIAN_EXCLUSIVE_FIELDS




# ---------------------------------------------------------------------------
# AI 筛选 schema 投影与快照
# ---------------------------------------------------------------------------

def project_filter_schema(platform: str) -> dict[str, Any]:
    """投影平台 AI 筛选 schema 为 API 响应（contracts/http-api.md /api/filter-labels）。"""
    reg = get_platform(platform)
    schema = reg.filter_schema
    return {
        "ok": True,
        "platform": schema.platform,
        "schema_version": schema.schema_version,
        "enabled_for_new_tasks": schema.enabled_for_new_tasks,
        "fields": [
            {
                "key": f.key,
                "label": f.label,
                "multiple": f.multiple,
                "options": [{"value": o.value, "label": o.label} for o in f.options],
            }
            for f in schema.fields
        ],
    }




def validate_filter_values(
    platform: str, *, schema_version: int, screening_fields: dict[str, Any],
) -> dict[str, list[str]]:
    """按平台 schema 校验 AI 筛选值。

    返回规范化后的 ``{field_key: [stable_value, ...]}``。跨平台字段或
    未知值抛 ValueError，调用方映射为 ``422 filter_validation_failed``。
    """
    reg = get_platform(platform)
    schema = reg.filter_schema
    if schema_version != schema.schema_version:
        raise ValueError(
            f"filter_schema_version_mismatch: 期望 {schema.schema_version}, "
            f"实际 {schema_version}"
        )

    if not isinstance(screening_fields, dict):
        raise ValueError("screening_fields 必须为对象")


    normalized: dict[str, list[str]] = {}
    for key, raw_value in screening_fields.items():
        field = schema.get_field(key)
        if field is None:
            raise ValueError(f"filter_validation_failed: 平台 {platform} 不支持字段 {key}")
        # 跨平台专属字段检查（防御性：schema 已隔离，此处显式拒绝）。
        if key in _ZHILIAN_EXCLUSIVE_FIELDS and platform != "zhilian":
            raise ValueError(f"filter_validation_failed: 平台 {platform} 不支持字段 {key}")
        if key in _BOSS_EXCLUSIVE_FIELDS and platform != "boss":
            raise ValueError(f"filter_validation_failed: 平台 {platform} 不支持字段 {key}")
        values = _coerce_value_list(raw_value)
        if not field.multiple and len(values) > 1:
            raise ValueError(f"字段 {key} 不允许多选")
        valid_values = field.option_values()
        for v in values:
            if v not in valid_values:
                raise ValueError(
                    f"filter_validation_failed: 字段 {key} 值 {v!r} 不在平台 {platform} schema 中"
                )
        normalized[key] = values
    return normalized




def _coerce_value_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = [raw]
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            result.append(text)
    return result




def build_filter_snapshot(
    platform: str, *, schema_version: int, screening_fields: dict[str, Any],
) -> dict[str, Any]:
    """构建完整冻结筛选快照：保存字段键、稳定值和当时标签。

    快照格式见 data-model.md ScreeningRun.filter_snapshot_json。
    """
    reg = get_platform(platform)
    schema = reg.filter_schema
    normalized = validate_filter_values(
        platform, schema_version=schema_version, screening_fields=screening_fields,
    )
    fields_snapshot: dict[str, Any] = {}
    for key, values in normalized.items():
        field = schema.get_field(key)
        labels = [field.label_for(v) or "" for v in values]
        fields_snapshot[key] = {"values": list(values), "labels": labels}
    return {
        "schema_version": schema.schema_version,
        "platform": platform,
        "fields": fields_snapshot,
    }
