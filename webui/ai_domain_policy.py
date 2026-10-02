"""垂直领域匹配的纯规则（B094）。

只负责三件事：把用户已有的行业选项解释成领域选择、给出稳定的规则版本与
语义摘要、判定旧筛选判定能否在新规则下复用。不读 store、不读平台注册表、
不含平台名称或平台编码，也不构造关键词词库。

引用方向：``ai_domain_context`` / ``screen_flow`` / ``ai_filters`` /
``ai_prompts`` 单向调用本模块。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: 领域规则版本。粗筛/精筛口径变化时提升，旧版本判定不再自动复用。
POLICY_VERSION = "b094-domain-v8"

#: 记录在既有 run.execution_params 里的内部元数据键（不新增表、列或接口字段）。
METADATA_VERSION_KEY = "screening_policy_version"
METADATA_SUMMARY_KEY = "domain_semantic_summary"
METADATA_SOURCE_KEY = "domain_selection_source"
METADATA_LABELS_KEY = "domain_selection_labels"

#: 规则不兼容对外错误分类（唯一登记处为 ``webui/error_registry.py``）。
INCOMPATIBLE_ERROR_CODE = "screening_policy_incompatible"

INDUSTRY_FIELD = "industry"
SNAPSHOT_VERSION_V2 = 2

#: 粗筛/精筛收到的是筛选条件的内部副本，领域口径挂在这个内部键上；
#: 它不进 API 入参、不进 frozen_filters，也不参与任何业务字段比较。
CRITERIA_DOMAIN_KEY = "domain_selection_state"

SOURCE_ORIGINAL_GROUP = "original_group"
SOURCE_PLATFORM_OVERRIDE = "platform_override"
SOURCE_LEGACY_PLATFORM = "legacy_platform"
SOURCE_NONE = "none"


class DomainSnapshotError(ValueError):
    """条件快照声明了版本却结构损坏；不得静默当作「不限」。"""

    error_code = "filter_snapshot_incompatible"


@dataclass(frozen=True)
class DomainSelection:
    """用户所选领域；``labels`` 保留整组选项原文，多选取并集。"""

    labels: tuple[str, ...] = ()
    source_kind: str = SOURCE_NONE

    @property
    def has_selection(self) -> bool:
        return bool(self.labels)


def empty_selection() -> DomainSelection:
    return DomainSelection()


def selection_from_criteria(criteria) -> DomainSelection:
    """读取两阶段调用方携带的内部领域口径；缺失即无领域选择。"""
    state = (criteria or {}).get(CRITERIA_DOMAIN_KEY) if isinstance(criteria, dict) else None
    if not isinstance(state, dict):
        return empty_selection()
    labels = normalize_labels(state.get("labels"))
    if not labels:
        return empty_selection()
    kind = str(state.get("source_kind") or "").strip() or SOURCE_PLATFORM_OVERRIDE
    return DomainSelection(labels=labels, source_kind=kind)


def normalize_labels(values) -> tuple[str, ...]:
    """去空白、去重并保持选择顺序。"""
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    labels: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in labels:
            labels.append(text)
    return tuple(labels)


def industry_selected_in_fields(screening_fields) -> bool:
    """调用方没有派生上下文时用的保守判据：已选行业即领域选择。"""
    if not isinstance(screening_fields, dict):
        return False
    return bool(normalize_labels(screening_fields.get(INDUSTRY_FIELD)))


def _snapshot_layer(container: object, platform: str, name: str) -> dict:
    if not isinstance(container, dict):
        raise DomainSnapshotError(f"{name} must be an object")
    layer = container.get(platform)
    if layer is None:
        raise DomainSnapshotError(f"{name}.{platform} is missing")
    if not isinstance(layer, dict):
        raise DomainSnapshotError(f"{name}.{platform} must be an object")
    return layer


def _list_value(container: dict, key: str, *, where: str) -> list:
    value = container.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise DomainSnapshotError(f"{where}.{key} must be an array")
    return value


def selection_from_snapshot(
    *, snapshot, platform: str, current_labels,
) -> DomainSelection:
    """按 data-model 的优先级解释领域选择。

    - V2 快照没有当前平台行业覆盖、且实际条件与冻结平台值一致 → 取统一原始组整组标签；
    - 覆盖键存在（含空数组）或实际条件已变化 → 以实际平台选择为准，不复活原组选项；
    - 没有 V2 快照 → 旧格式，只按实际平台标签解释，不逆向猜测曾选过哪个统一组。
    """
    current = normalize_labels(current_labels)
    if not isinstance(snapshot, dict) or snapshot.get("snapshotVersion") != SNAPSHOT_VERSION_V2:
        return _platform_selection(current, SOURCE_LEGACY_PLATFORM)
    mapping_version = snapshot.get("mappingVersion")
    if not isinstance(mapping_version, str) or not mapping_version.strip():
        raise DomainSnapshotError("condition snapshot requires a mappingVersion")
    unified = snapshot.get("unifiedValues")
    if not isinstance(unified, dict):
        raise DomainSnapshotError("condition snapshot requires unifiedValues")
    platform_values = _snapshot_layer(snapshot.get("platformValues"), platform, "platformValues")
    overrides = _snapshot_layer(snapshot.get("overrides"), platform, "overrides")
    group_labels = normalize_labels(
        _list_value(unified, INDUSTRY_FIELD, where="unifiedValues")
    )

    if INDUSTRY_FIELD in overrides:
        # 覆盖键存在就有含义：空数组是显式「未选择」，不与原始组求并集。
        return _platform_selection(current, SOURCE_PLATFORM_OVERRIDE)

    frozen = normalize_labels(
        _list_value(platform_values, INDUSTRY_FIELD, where="platformValues")
    )
    if set(current) != set(frozen):
        # 当前明确条件与冻结快照不同，按当前条件解释，不借旧快照复活选择。
        return _platform_selection(current, SOURCE_PLATFORM_OVERRIDE)
    if not group_labels:
        return _platform_selection(current, SOURCE_PLATFORM_OVERRIDE)
    if not current:
        return DomainSelection()
    return DomainSelection(labels=group_labels, source_kind=SOURCE_ORIGINAL_GROUP)


def _platform_selection(
    current: tuple[str, ...], source_kind: str,
) -> DomainSelection:
    if not current:
        return DomainSelection(source_kind=SOURCE_NONE)
    return DomainSelection(labels=current, source_kind=source_kind)


def semantic_summary(selection: DomainSelection) -> str:
    """由规范化选择与规则版本派生的稳定摘要；不含岗位正文、画像或凭据。"""
    payload = "|".join((POLICY_VERSION, selection.source_kind, *selection.labels))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def expected_state(selection: DomainSelection) -> tuple[str, str]:
    return (POLICY_VERSION, semantic_summary(selection))


def run_metadata(selection: DomainSelection) -> dict:
    """新建/续跑 run 需要写进 execution_params 的内部元数据。"""
    return {
        METADATA_VERSION_KEY: POLICY_VERSION,
        METADATA_SUMMARY_KEY: semantic_summary(selection),
        METADATA_SOURCE_KEY: selection.source_kind,
        METADATA_LABELS_KEY: list(selection.labels),
    }


def recorded_state(execution_params) -> tuple[str, str] | None:
    if not isinstance(execution_params, dict):
        return None
    version = execution_params.get(METADATA_VERSION_KEY)
    if not isinstance(version, str) or not version:
        return None
    summary = execution_params.get(METADATA_SUMMARY_KEY)
    return (version, str(summary or ""))


def is_verdict_state_compatible(
    execution_params, *, selection: DomainSelection | None = None,
    screening_fields=None,
) -> bool:
    """旧判定能否在新规则下复用。

    未版本化的候选只在「本次没有领域选择」时可复用，保持既有恢复行为；
    调用方没传上下文时按已选行业保守判定，不默认旧领域判定可用。
    """
    if ((selection is not None and not selection.has_selection)
            or (selection is None and not industry_selected_in_fields(screening_fields))):
        return True  # 无领域选择沿用原有恢复契约，不受领域规则升级影响。
    recorded = recorded_state(execution_params)
    if recorded is None:
        if selection is not None:
            return not selection.has_selection
        return not industry_selected_in_fields(screening_fields)
    if recorded[0] != POLICY_VERSION:
        return False
    if selection is None:
        return (not industry_selected_in_fields(screening_fields)
                and recorded[1] == semantic_summary(empty_selection()))
    return recorded[1] == semantic_summary(selection)


def prompt_labels_text(selection: DomainSelection) -> str:
    return "、".join(selection.labels)
