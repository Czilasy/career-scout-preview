"""Thin B096 Flow/Track persistence facade.

Implementation is split by claim/transition, result projection and legacy
ownership responsibilities.  TaskStore keeps the historical import surface.
"""

from __future__ import annotations

from webui.store_flow_core import (
    FLOW_PLATFORMS, FlowConflictError, _ACTIVE_TRACK_STATUSES,
    _FLOW_PLATFORM_SET, _FLOW_SELECTIONS, _TERMINAL_TRACK_STATUSES,
    _TRACK_STATUS_TRANSITIONS,
)
from webui.store_flow_claims import StoreFlowClaimsMixin
from webui.store_flow_preflight import StoreFlowPreflightMixin
from webui.store_flow_results import StoreFlowResultsMixin
from webui.store_flow_legacy import StoreFlowLegacyMixin
from webui.store_flow_state import StoreFlowStateMixin
from webui.store_flow_core import FlowStoreSupportMixin


class StoreFlowMixin(
    StoreFlowClaimsMixin,
    StoreFlowPreflightMixin,
    StoreFlowResultsMixin,
    StoreFlowLegacyMixin,
    StoreFlowStateMixin,
    FlowStoreSupportMixin,
):
    """Compatibility facade assembled from focused B096 store mixins."""


__all__ = [
    "FLOW_PLATFORMS", "FlowConflictError", "StoreFlowMixin",
]
