"""高级配置与模式版本域（021 B2 拆分自 webui/store.py）：db_meta、自定义配置、
模式选择/创建/应用/回滚、legacy 高级设置导入、活跃 worker 计数。

以 mixin 形式由 webui/store.py 的 TaskStore 组装；实例状态（db_path、
_connection 等）来自 TaskStore 核心。模块不得 import webui.store。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from webui.store_helpers import (
    _decode_json,
    _now,
    _uuid,
)
from webui.store_constants import (
    _SHA256_PREFIX,
)


class StoreConfigMixin:
    def get_db_meta(self) -> dict | None:
        """Return the lightweight live/test marker row for this database."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT env, created_at, updated_at FROM db_meta WHERE id = 1"
            ).fetchone()
        return dict(row) if row is not None else None

    def get_advanced_config_state(self) -> dict:
        """返回当前高级配置状态：selection、custom config、mode version。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM advanced_config_state WHERE id = 1"
            ).fetchone()
        if row is None:
            return {
                "active_selection": "custom",
                "active_mode_version_id": None,
                "last_custom_config": None,
                "last_custom_digest": None,
                "legacy_imported_at": None,
            }
        custom_config = _decode_json(row["last_custom_config_json"], None)
        return {
            "active_selection": row["active_selection"],
            "active_mode_version_id": row["active_mode_version_id"],
            "last_custom_config": custom_config,
            "last_custom_digest": row["last_custom_digest"],
            "legacy_imported_at": row["legacy_imported_at"],
        }

    def save_custom_config(self, config: dict) -> str:
        """原子保存完整自定义配置（含 JD 并发 Tab 数），返回 digest。

        部分字段保存被拒绝；pages 不属于配置快照。
        """
        from webui.execution_config import (
            DEFAULT_DETAIL_TAB_POOL_SIZE,
            SPEED_FIELDS,
            ExecutionConfigSnapshot,
        )
        config = dict(config)
        config.setdefault("detail_tab_pool_size", DEFAULT_DETAIL_TAB_POOL_SIZE)
        missing = [f for f in SPEED_FIELDS if f not in config]
        if missing:
            raise ValueError(f"缺少必填字段: {missing}")
        # 验证配置快照有效性（含物理边界校验）
        snapshot = ExecutionConfigSnapshot.create(config)
        config_json = json.dumps(snapshot.to_dict(), ensure_ascii=False)
        with self._connection() as conn:
            conn.execute(
                "UPDATE advanced_config_state "
                "SET active_selection = 'custom', "
                "    last_custom_config_json = ?, "
                "    last_custom_digest = ?, "
                "    updated_at = ? "
                "WHERE id = 1",
                (config_json, snapshot.config_digest, _now()),
            )
        return snapshot.config_digest

    def select_mode(self, mode: str, *, task_size: str) -> dict:
        """选择系统参考模式，返回对应规模的配置快照。

        FR-009: pages 不出现在返回结果中。
        FR-056: 根据任务规模载入内部配置。
        """
        from webui.execution_config import ExecutionConfigSnapshot, get_mode_config
        if mode not in ("stable", "balanced", "extreme", "custom"):
            raise ValueError(f"未知模式: {mode}")
        if task_size not in ("small", "medium", "large"):
            raise ValueError(f"未知任务规模: {task_size}")
        if mode == "custom":
            state = self.get_advanced_config_state()
            if state["last_custom_config"] is None:
                raise ValueError("无最近自定义配置可恢复")
            with self._connection() as conn:
                conn.execute(
                    "UPDATE advanced_config_state "
                    "SET active_selection = 'custom', updated_at = ? WHERE id = 1",
                    (_now(),),
                )
            return {
                "selection": "custom",
                "config": state["last_custom_config"],
                "mode_version_id": state["active_mode_version_id"],
            }
        state = self.get_advanced_config_state()
        active_version_id = state["active_mode_version_id"]
        version_digest = None
        if active_version_id:
            version = self.get_mode_version(active_version_id)
            try:
                slot = version["matrix"][mode][task_size]
            except (KeyError, TypeError) as exc:
                raise ValueError("活动模式版本缺少所需配置槽位") from exc
            snapshot = ExecutionConfigSnapshot.create(slot)
            version_digest = version["version_digest"]
        else:
            snapshot = get_mode_config(mode, task_size=task_size)
        # 更新活跃选择
        with self._connection() as conn:
            conn.execute(
                "UPDATE advanced_config_state SET active_selection = ?, updated_at = ? WHERE id = 1",
                (mode, _now()),
            )
        return {
            "selection": mode,
            "config": snapshot.to_dict(),
            "mode_version_id": active_version_id,
            "mode_version_digest": version_digest,
        }

    def create_mode_version(
        self, *, matrix: dict, manual_ranges: dict,
        source_experiment_id: str | None = None,
    ) -> str:
        """创建一个候选模式版本（3 模式 × 3 规模）。

        不完整矩阵被拒绝（data-model.md 不变量：No partial mode matrix）。
        """
        required_modes = {"stable", "balanced", "extreme"}
        required_sizes = {"small", "medium", "large"}
        if not isinstance(matrix, dict):
            raise ValueError("matrix 必须是字典")
        extra_modes = set(matrix.keys()) - required_modes
        missing_modes = required_modes - set(matrix.keys())
        if missing_modes:
            raise ValueError(f"matrix 缺少模式: {missing_modes}")
        if extra_modes:
            raise ValueError(f"matrix 包含未知模式: {extra_modes}")
        from webui.execution_config import ExecutionConfigSnapshot
        for mode, sizes in matrix.items():
            if not isinstance(sizes, dict):
                raise ValueError(f"模式 {mode} 的规模配置必须是字典")
            missing_sizes = required_sizes - set(sizes.keys())
            if missing_sizes:
                raise ValueError(f"模式 {mode} 缺少规模: {missing_sizes}")
            extra_sizes = set(sizes.keys()) - required_sizes
            if extra_sizes:
                raise ValueError(f"模式 {mode} 包含未知规模: {extra_sizes}")
            for size, config in sizes.items():
                ExecutionConfigSnapshot.create(config)
        version_id = _uuid()
        matrix_json = json.dumps(matrix, ensure_ascii=False, sort_keys=True)
        ranges_json = json.dumps(manual_ranges, ensure_ascii=False, sort_keys=True)
        version_digest = _SHA256_PREFIX + hashlib.sha256(
            (matrix_json + ranges_json).encode("utf-8")
        ).hexdigest()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO mode_config_versions "
                "(id, source_experiment_id, status, matrix_json, "
                " manual_ranges_json, version_digest, created_at) "
                "VALUES (?, ?, 'candidate', ?, ?, ?, ?)",
                (version_id, source_experiment_id, matrix_json, ranges_json,
                 version_digest, _now()),
            )
        return version_id

    def get_mode_version(self, version_id: str) -> dict:
        """Return one complete mode version without mutating active state."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM mode_config_versions WHERE id = ?", (version_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"模式版本不存在: {version_id}")
        return {
            "id": row["id"],
            "source_experiment_id": row["source_experiment_id"],
            "status": row["status"],
            "matrix": _decode_json(row["matrix_json"], {}),
            "manual_ranges": _decode_json(row["manual_ranges_json"], {}),
            "version_digest": row["version_digest"],
            "created_at": row["created_at"],
            "applied_at": row["applied_at"],
        }

    def get_experiment_mode_version(self, experiment_id: str) -> dict | None:
        """Return the newest complete candidate/active version for an experiment."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM mode_config_versions "
                "WHERE source_experiment_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (experiment_id,),
            ).fetchone()
        return self.get_mode_version(row["id"]) if row is not None else None

    def get_previous_mode_version(self, active_version_id: str) -> dict | None:
        """Return the newest complete superseded version available for rollback."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM mode_config_versions "
                "WHERE id != ? AND status = 'superseded' "
                "ORDER BY COALESCE(applied_at, created_at) DESC LIMIT 1",
                (active_version_id,),
            ).fetchone()
        return self.get_mode_version(row["id"]) if row is not None else None

    def apply_mode_version(self, version_id: str) -> None:
        """原子应用模式版本：旧的被 superseded，新的变 active。"""
        version = self.get_mode_version(version_id)
        source_experiment_id = version.get("source_experiment_id")
        if source_experiment_id:
            experiment = self.get_tuning_experiment(source_experiment_id)
            issues = self.get_tuning_completion_issues(source_experiment_id)
            if experiment["status"] != "completed" or issues:
                raise ValueError("实验候选版本尚未通过全部最终轮次门禁")
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM mode_config_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"模式版本不存在: {version_id}")
            # 旧 active 版本标记为 superseded
            conn.execute(
                "UPDATE mode_config_versions SET status = 'superseded' WHERE status = 'active'"
            )
            # 新版本标记为 active
            conn.execute(
                "UPDATE mode_config_versions SET status = 'active', applied_at = ? WHERE id = ?",
                (_now(), version_id),
            )
            # 更新活跃选择
            conn.execute(
                "UPDATE advanced_config_state "
                "SET active_mode_version_id = ?, updated_at = ? WHERE id = 1",
                (version_id, _now()),
            )

    def rollback_mode_version(self, version_id: str) -> None:
        """回退到指定版本：整体恢复。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM mode_config_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"模式版本不存在: {version_id}")
            conn.execute(
                "UPDATE mode_config_versions SET status = 'superseded' WHERE status = 'active'"
            )
            conn.execute(
                "UPDATE mode_config_versions SET status = 'active', applied_at = ? WHERE id = ?",
                (_now(), version_id),
            )
            conn.execute(
                "UPDATE advanced_config_state "
                "SET active_mode_version_id = ?, updated_at = ? WHERE id = 1",
                (version_id, _now()),
            )

    def import_legacy_advanced_settings(self, legacy_path) -> None:
        """一次性导入旧 advanced_settings.json。

        已导入过时不覆盖；pages 不导入到配置快照。
        """
        legacy_path = Path(legacy_path)
        # 检查是否已导入
        with self._connection() as conn:
            row = conn.execute(
                "SELECT legacy_imported_at FROM advanced_config_state WHERE id = 1"
            ).fetchone()
            if row is not None and row["legacy_imported_at"] is not None:
                return  # 已导入，不覆盖
        if not legacy_path.is_file():
            return
        try:
            raw = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(raw, dict):
            return
        # 只取速度字段，排除 pages；旧 9 字段配置补默认 JD Tab 数
        from webui.execution_config import DEFAULT_DETAIL_TAB_POOL_SIZE, SPEED_FIELDS
        config = {k: v for k, v in raw.items() if k in SPEED_FIELDS}
        config.setdefault("detail_tab_pool_size", DEFAULT_DETAIL_TAB_POOL_SIZE)
        if len(config) != len(SPEED_FIELDS):
            return  # 字段不完整，不导入
        self.save_custom_config(config)
        with self._connection() as conn:
            conn.execute(
                "UPDATE advanced_config_state SET legacy_imported_at = ? WHERE id = 1",
                (_now(),),
            )

    # -- recovery maintenance lock ---------------------------------------

    def _active_worker_count(self, conn=None) -> int:
        if conn is None:
            with self._connection() as owned_conn:
                return self._active_worker_count(owned_conn)
        counts = [
            conn.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE status IN ('queued', 'running')"
            ).fetchone()["n"],
            conn.execute(
                "SELECT COUNT(*) AS n FROM search_runs WHERE status IN ('queued', 'running')"
            ).fetchone()["n"],
            conn.execute(
                "SELECT COUNT(*) AS n FROM screening_runs WHERE status IN ('queued', 'running')"
            ).fetchone()["n"],
        ]
        return sum(int(value or 0) for value in counts)
