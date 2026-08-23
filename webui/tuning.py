"""SPEC011: 深度调优实验控制模块（兼容门面）。

提供实验生命周期、候选管理、租约协调和跨重启恢复的深模块接口。
所有方法只操作实验表族，永不修改 advanced_config_state（FR-042/SC-014）。
021 B7 后实现拆至 tuning_* 域模块，本文件组装 TuningController 并
re-export 全部既有符号，旧 import 路径保持可用。
"""

from __future__ import annotations

from webui.store import TaskStore

from webui.tuning_candidates import TuningCandidatesMixin
from webui.tuning_digest import _SHA256_PREFIX, sha256_path
from webui.tuning_events import (
    ALLOWED_COUNT_KEYS,
    ALLOWED_EVENT_TYPES,
    ALLOWED_METADATA_KEYS,
    MeasurementSink,
)
from webui.tuning_experiments import _PROCESS_OWNER_TOKENS, TuningExperimentsMixin
from webui.tuning_manifests import TuningManifestsMixin
from webui.tuning_quality import TuningQualityMixin
from webui.tuning_rounds import RoundAdapter, TuningRoundsMixin


class TuningController(
    TuningExperimentsMixin,
    TuningRoundsMixin,
    TuningQualityMixin,
    TuningManifestsMixin,
    TuningCandidatesMixin,
):
    """实验控制面：管理实验、候选、轮次和租约。

    不持有可变全局状态；所有持久化通过 TaskStore 完成。
    """
