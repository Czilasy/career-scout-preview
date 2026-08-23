"""调优轮次执行器 TuningRoundRunner（021 B7 自 pipeline_exec.py 搬运）。

run_search / fetch_job_details / close_debug_chrome 经门面动态取用。
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from types import MappingProxyType




class TuningStageError(RuntimeError):
    """A stage failure with a safe, controller-visible error code."""

    def __init__(self, error_code: str, message: str):
        self.error_code = str(error_code)
        super().__init__(message)




class TuningRoundRunner:
    """用冻结 manifest 机械分派五种真实阶段，不作候选或参数决策。"""

    ROUND_KINDS = frozenset({"list", "detail", "rough", "fine", "end_to_end"})

    def __init__(self, *, workspace_root, source_factory, ai_settings_provider):
        self.workspace_root = Path(workspace_root).resolve()
        self.source_factory = source_factory
        self.ai_settings_provider = ai_settings_provider

    def _read_artifact(
        self, manifest: dict, *, path_field: str, digest_field: str,
        required: bool,
    ) -> dict:
        frozen = manifest.get("frozen_input", {})
        path = frozen.get(path_field)
        if not path:
            if required:
                raise ValueError(f"轮次缺少冻结输入 {path_field}")
            return {}
        absolute = (self.workspace_root / str(path)).resolve()
        experiment_root = (
            self.workspace_root / "tuning" / manifest["experiment_id"]
        ).resolve()
        if experiment_root not in absolute.parents:
            raise ValueError("冻结输入产物越过实验根目录")
        if not absolute.is_file():
            if required:
                raise ValueError("冻结输入产物不存在")
            return {}
        try:
            artifact_bytes = absolute.read_bytes()
            payload = json.loads(artifact_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("冻结输入产物不可读") from exc
        expected_digest = frozen.get(digest_field)
        if not isinstance(expected_digest, str) or not expected_digest:
            raise ValueError(f"轮次缺少冻结输入 {digest_field}")
        actual_digest = "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
        if actual_digest != expected_digest:
            raise ValueError("冻结输入产物摘要不匹配")
        if not isinstance(payload, dict):
            raise ValueError("冻结输入产物必须是 JSON 对象")
        return payload

    def _ai_settings(self) -> tuple[str, str, str]:
        settings = self.ai_settings_provider()
        endpoint = str(settings.get("endpoint_url") or "")
        api_key = str(settings.get("api_key") or "")
        model = str(settings.get("model") or "")
        if not endpoint or not api_key:
            raise ValueError("AI 阶段缺少已配置的端点或凭据")
        return endpoint, api_key, model

    @staticmethod
    def _retry_limits_from_manifest(manifest: dict):
        """Build the immutable AI transport retry budget authorized by a manifest."""
        from webui.ai_retry import normalize_retry_policy
        policy = normalize_retry_policy(manifest.get("retry_policy"))
        if not policy:
            return None
        from webui.ai_retry import AI_TRANSPORT_RETRY_CODES
        limits = {
            str(code): max(0, int(entry.get("max_retries", 0)))
            for code, entry in policy.items()
        }
        # 只把 AI 传输层可覆盖的码传给 call_ai；其余码走各自阶段逻辑。
        transport_limits = {
            code: retries for code, retries in limits.items()
            if code in AI_TRANSPORT_RETRY_CODES
        }
        if not transport_limits:
            return None
        return MappingProxyType(transport_limits)

    def execute(self, manifest: dict, *, measurement_callback=None) -> dict:
        from webui import pipeline_exec as _facade
        from webui.ai import match_jds, screen_jobs
        from webui.execution_config import ExecutionConfigSnapshot

        kind = manifest.get("round_kind")
        if kind not in self.ROUND_KINDS:
            raise ValueError(f"未知轮次类型: {kind}")
        config = ExecutionConfigSnapshot.from_dict(manifest["execution_config"])
        fixed = manifest["fixed_fields"]
        params = {
            "keyword": ",".join(fixed["keywords"]),
            "city": (["全国"] if fixed["scope_kind"] == "nationwide"
                     else list(fixed["cities"])),
            "pages": fixed["pages_per_combination"], "filters": {},
        }
        artifact_dir = (
            self.workspace_root / "tuning" / manifest["experiment_id"]
            / "artifacts" / manifest["round_id"]
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        base_context = self._read_artifact(
            manifest, path_field="artifact_manifest_path",
            digest_field="artifact_digest", required=True,
        )
        source_context = self._read_artifact(
            manifest, path_field="source_artifact_path",
            digest_field="source_artifact_digest",
            required=kind in {"detail", "rough", "fine"},
        )
        quality_context = base_context.get("quality_context")
        if not isinstance(quality_context, dict):
            raise ValueError("冻结输入产物缺少 quality_context")
        source = (
            self.source_factory(
                artifact_root=artifact_dir,
                platform=manifest.get("fixed_fields", {}).get("platform"),
            )
            if kind in {"list", "detail", "end_to_end"}
            else None
        )
        if kind in {"list", "end_to_end"}:
            listed = _facade.run_search(
                params, source, pages=fixed["pages_per_combination"],
                artifact_dir=str(artifact_dir), execution_config=config,
                measurement_callback=measurement_callback,
                close_chrome_on_success=(kind == "list"),
            )
            if not listed.get("ok"):
                raise TuningStageError(
                    listed.get("hard_stop_code") or "list_stage_failed",
                    listed.get("error") or "list 阶段失败",
                )
            jobs = listed["jobs"]
            if kind == "list":
                return {"round_kind": kind, "jobs": jobs, "list_result": listed}
        else:
            jobs = source_context.get("jobs")
            if not isinstance(jobs, list):
                raise ValueError("阶段输入产物缺少 jobs 列表")
        for index, job in enumerate(jobs):
            if isinstance(job, dict):
                job.setdefault("_tuning_measurement_index", index)
        base_input_count = len(jobs)
        if kind in {"detail", "end_to_end"}:
            detailed = _facade.fetch_job_details(
                jobs, source, artifact_dir=str(artifact_dir),
                execution_config=config, measurement_callback=measurement_callback,
                emit_terminal_events=(kind == "detail"),
            )
            if detailed.get("hard_stop"):
                raise TuningStageError(
                    detailed.get("hard_stop_code") or "detail_stage_failed",
                    detailed.get("hard_stop_code") or "detail 阶段硬阻断",
                )
            jobs = detailed["jobs"]
            if kind == "detail":
                return {"round_kind": kind, **detailed}
            _facade.close_debug_chrome()
        if kind in {"rough", "end_to_end"}:
            criteria = quality_context.get("screening_fields")
            if not isinstance(criteria, dict):
                raise ValueError("AI 粗筛缺少冻结 criteria")
            # B033：粗筛输入补求职画像全文（放宽规则以画像表述为准）
            criteria = dict(criteria)
            criteria["profile_summary"] = quality_context.get("profile_summary") or ""
            endpoint, api_key, model = self._ai_settings()
            retry_limits = self._retry_limits_from_manifest(manifest)
            rough = screen_jobs(
                jobs, criteria, endpoint, api_key, model=model,
                raise_on_systemic=True, execution_config=config,
                measurement_callback=measurement_callback,
                emit_kept_terminal=(kind == "rough"),
                measurement_input_count=base_input_count,
                retry_limits=retry_limits,
            )
            kept = set(rough["kept"])
            jobs = [job for job in jobs if str(job.get("job_id", "")) in kept]
            if kind == "rough":
                return {"round_kind": kind, **rough}
        if kind in {"fine", "end_to_end"}:
            profile_summary = quality_context.get("profile_summary")
            if not isinstance(profile_summary, str) or not profile_summary.strip():
                raise ValueError("AI 精筛缺少冻结 profile_summary")
            endpoint, api_key, model = self._ai_settings()
            from webui.ai_retry import normalize_retry_policy
            normalized_policy = normalize_retry_policy(
                manifest.get("retry_policy")) or {}
            missing_entry = normalized_policy.get("ai_missing_job") or {}
            try:
                missing_retry_budget = max(
                    0, int(missing_entry.get("max_retries", 0)))
            except (TypeError, ValueError):
                missing_retry_budget = 0
            fine = match_jds(
                jobs, profile_summary, endpoint, api_key, model=model,
                raise_on_systemic=True, execution_config=config,
                measurement_callback=measurement_callback,
                measurement_input_count=base_input_count,
                missing_result_retry_budget=missing_retry_budget,
                retry_limits=self._retry_limits_from_manifest(manifest),
                criteria=quality_context.get("screening_fields"),
                profile_facts=quality_context.get("profile_facts"),
            )
            return {"round_kind": kind, "jobs": jobs, **fine}
        raise ValueError(f"轮次 {kind} 未产生结果")
