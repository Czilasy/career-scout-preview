"""调优测量事件聚合与质量参照比较 mixin（021 B7 自 tuning.py 搬运）。"""

from __future__ import annotations

import hashlib
import json

from webui.tuning_events import MeasurementSink

class TuningQualityMixin:
    """测量记录/聚合、质量参照构建与逐项比较、人工复核流转。"""

    # -- 测量事件 (data-model.md 2.9, T016/T017) -----------------------

    def record_measurement(
        self, *, round_id: str, event_type: str, stage: str,
        duration_ms: int, started_monotonic_ms: int | None = None,
        counts: dict | None = None, error_code: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """记录一条测量事件。

        FR-030/SC-006: 所有等待、冷却、重试、恢复时间计入总耗时。
        SC-007: 终态守恒。
        data-model.md 2.9: 禁止凭据、原始简历、原始模型响应和 JD 正文。
        """
        return self._store.save_tuning_measurement_event(
            round_id=round_id, event_type=event_type, stage=stage,
            duration_ms=duration_ms,
            started_monotonic_ms=started_monotonic_ms,
            counts=counts, error_code=error_code, metadata=metadata,
        )

    def list_measurements(self, round_id: str) -> list[dict]:
        """返回轮次的全部测量事件，按 seq 升序。"""
        return self._store.list_tuning_measurement_events(round_id)

    def aggregate_measurements(self, round_id: str) -> dict:
        """聚合轮次的测量摘要（MeasurementSummary）。

        FR-030: total_duration_ms 包含工作、等待、冷却、重试和恢复。
        SC-007: terminal_count == input_count, missing=0, duplicate=0。
        """
        events = self.list_measurements(round_id)
        round_kind = self._store.get_tuning_round(round_id)["round_kind"]
        terminal_stages = {
            "list": {"list"},
            "detail": {"detail"},
            "rough": {"rough"},
            "fine": {"fine"},
            "end_to_end": {"rough", "fine"},
        }.get(round_kind, {round_kind})
        total_duration_ms = 0
        stage_durations_ms: dict[str, int] = {}
        wait_duration_ms = 0
        retry_duration_ms = 0
        attempt_count = 0
        retry_count = 0
        error_counts: dict[str, int] = {}
        error_correlation_id = None
        input_count = 0
        terminal_count = 0
        success_count = 0
        failed_count = 0
        seen_item_indices: set = set()
        duplicate_count = 0

        for ev in events:
            ev_type = ev["event_type"]
            stage = ev["stage"] or "unknown"
            dur = ev["duration_ms"] or 0
            counts = ev.get("counts") or {}
            error_code = ev.get("error_code")

            # 总耗时是所有事件时长之和（工作、等待、重试）
            total_duration_ms += dur
            stage_durations_ms[stage] = stage_durations_ms.get(stage, 0) + dur

            if ev_type == "wait":
                wait_duration_ms += dur
            elif ev_type == "retry":
                retry_duration_ms += dur
                retry_count += 1
                attempt_count += 1
            elif ev_type == "request":
                attempt_count += 1
                if error_code:
                    error_counts[error_code] = error_counts.get(error_code, 0) + 1
                    if error_correlation_id is None:
                        error_correlation_id = (ev.get("metadata") or {}).get(
                            "correlation_id"
                        )
            elif ev_type == "item_terminal":
                if "input_count" in counts:
                    input_count = max(input_count, int(counts["input_count"]))
                if stage not in terminal_stages:
                    continue
                terminal_count += 1
                item_idx = counts.get("item_index")
                if item_idx is not None:
                    if item_idx in seen_item_indices:
                        duplicate_count += 1
                    else:
                        seen_item_indices.add(item_idx)
                status = counts.get("status", "")
                if status in ("success", "dropped", "kept", "match", "not_match"):
                    success_count += 1
                elif status in ("failed", "unavailable", "uncertain"):
                    failed_count += 1
            elif ev_type == "stage" and "input_count" in counts:
                input_count = max(input_count, int(counts["input_count"]))

        # 如果 input_count 已知，missing = input - terminal
        missing_count = 0
        if input_count > 0:
            missing_count = max(0, input_count - terminal_count)

        return {
            "total_duration_ms": total_duration_ms,
            "stage_durations_ms": stage_durations_ms,
            "work_duration_ms": total_duration_ms - wait_duration_ms - retry_duration_ms,
            "wait_duration_ms": wait_duration_ms,
            "retry_duration_ms": retry_duration_ms,
            "attempt_count": attempt_count,
            "retry_count": retry_count,
            "error_counts": error_counts,
            "error_correlation_id": error_correlation_id,
            "input_count": input_count,
            "terminal_count": terminal_count,
            "success_count": success_count,
            "failed_count": failed_count,
            "missing_count": missing_count,
            "duplicate_count": duplicate_count,
            "quality_diff_count": 0,
        }

    def aggregate_hard_error(
        self, round_id: str, *, fallback_code: str | None = None,
    ) -> dict:
        """Reduce the persisted attempt-error chain to one blocker."""
        priority = {
            "auth_failed": 100,
            "quota_exhausted": 95,
            "rate_limited": 90,
            "network_error": 80,
            "timeout": 75,
            "server_error": 70,
            "truncated": 60,
            "invalid_response": 50,
        }
        failures = []
        for event in self.list_measurements(round_id):
            code = event.get("error_code")
            if event.get("event_type") != "request" or not code:
                continue
            metadata = event.get("metadata") or {}
            failures.append({
                "code": code,
                "correlation_id": metadata.get("correlation_id"),
                "seq": int(event.get("seq") or 0),
            })
        if failures:
            selected = max(
                failures,
                key=lambda item: (priority.get(item["code"], 40), item["seq"]),
            )
        else:
            selected = {
                "code": fallback_code or "unknown_hard_error",
                "correlation_id": None,
                "seq": 0,
            }
        return {
            "code": selected["code"],
            "correlation_id": selected["correlation_id"],
            "attempt_error_count": len(failures),
        }

    def build_measurement_sink(self, round_id: str) -> MeasurementSink:
        """构建绑定到指定轮次的 allowlisted measurement sink。

        T017: pipeline_exec / ai.py / boss_cdp_raw 通过此 sink 记录测量事件，
        无需直接依赖 TuningController 内部方法。sink 在写入前过滤敏感字段。
        """
        return MeasurementSink(self, round_id)

    def aggregate_round_evidence(self, round_id: str) -> dict:
        """聚合轮次证据：MeasurementSummary + artifact_digest。

        T017: 执行报告只能引用这些证据及其摘要（plan.md §3）。
        返回的 artifact_digest 基于 MeasurementSummary 的规范 JSON。
        """
        summary = self.aggregate_measurements(round_id)
        # 规范化 JSON 摘要用于计算 digest
        canonical = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        import hashlib
        artifact_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return {
            "round_id": round_id,
            "summary": summary,
            "artifact_digest": artifact_digest,
            "event_count": len(self.list_measurements(round_id)),
        }

    # -- T020: 质量参考与逐项比较 (FR-026/027/028/034) -------------------

    def build_quality_reference(
        self, *, experiment_id: str, input_version_id: str,
        baseline_round_results: list[list[dict]],
    ) -> dict:
        """FR-026: 通过低压力配置的重复运行建立质量参考。

        从多轮基线的逐项结果计算共识和波动范围。
        baseline_round_results: 每个元素是一轮的逐项结果列表。
        """
        if not baseline_round_results:
            raise ValueError("baseline_round_results 不能为空")
        repetition_count = len(baseline_round_results)
        # 收集每个 item_index 的全部 verdict
        item_verdicts: dict[int, list[str]] = {}
        for rep in baseline_round_results:
            for item in rep:
                idx = item["item_index"]
                verdict = item["verdict"]
                item_verdicts.setdefault(idx, []).append(verdict)
        # 计算共识和稳定性
        item_results_list = []
        per_item_stability = {}
        items_with_variation = []
        for idx in sorted(item_verdicts.keys()):
            verdicts = item_verdicts[idx]
            # 共识 = 出现次数最多的 verdict
            verdict_counts: dict[str, int] = {}
            for v in verdicts:
                verdict_counts[v] = verdict_counts.get(v, 0) + 1
            consensus = max(verdict_counts, key=verdict_counts.get)
            # 稳定性是与共识一致的重复次数占总重复次数的比例
            agreement = verdict_counts[consensus]
            stability = agreement / repetition_count
            per_item_stability[idx] = stability
            if stability < 1.0:
                items_with_variation.append(idx)
            item_results_list.append({
                "item_index": idx, "verdict": consensus, "stability": stability,
            })
        average_stability = (
            sum(per_item_stability.values()) / len(per_item_stability)
            if per_item_stability else 0.0
        )
        item_results = {"items": item_results_list}
        variation_summary = {
            "repetition_count": repetition_count,
            "item_count": len(item_verdicts),
            "per_item_stability": per_item_stability,
            "average_stability": average_stability,
            "items_with_variation": items_with_variation,
        }
        # 计算 reference_digest
        canonical = json.dumps(item_results, ensure_ascii=False, sort_keys=True)
        reference_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return self._store.save_quality_reference(
            experiment_id=experiment_id,
            input_version_id=input_version_id,
            item_results=item_results,
            variation_summary=variation_summary,
            reference_digest=reference_digest,
        )

    def confirm_quality_reference(self, reference_id: str) -> dict:
        """FR-026: 确认质量参考。

        确认时将实验内其他 confirmed/review_required 参考标记为 superseded。
        """
        ref = self._store.get_quality_reference(reference_id)
        if ref["status"] not in ("building", "review_required"):
            raise ValueError(
                f"只有 building 或 review_required 状态的参考才能确认，当前: {ref['status']}"
            )
        # supersede 同实验的其他参考
        self._store.supersede_quality_references(
            ref["experiment_id"], except_id=reference_id,
        )
        # 确认当前参考
        updated = self._store.update_quality_reference_status(
            reference_id, status="confirmed",
        )
        # 设置为实验的活动参考
        self._store.set_experiment_quality_reference(
            ref["experiment_id"], reference_id,
        )
        return updated

    def get_quality_reference(self, reference_id: str) -> dict:
        """返回质量参考记录。"""
        return self._store.get_quality_reference(reference_id)

    def get_active_quality_reference(self, experiment_id: str) -> dict | None:
        """返回实验的活动质量参考（最近的 confirmed 版本）。"""
        refs = self._store.list_quality_references(experiment_id)
        for ref in refs:
            if ref["status"] == "confirmed":
                return ref
        return None

    def enforce_reference_digest_match(
        self, *, reference_id: str, expected_digest: str,
    ) -> bool:
        """data-model 2.4: 候选只能与 manifest 中记录的参考摘要匹配的参考比较。

        digest 不匹配时抛出 ValueError。
        """
        ref = self._store.get_quality_reference(reference_id)
        if ref["reference_digest"] != expected_digest:
            raise ValueError(
                "参考摘要不匹配：候选 manifest 中的参考摘要与活动参考不一致"
            )
        return True

    def compare_results_against_reference(
        self, *, candidate_item_results: list[dict],
        reference_id: str,
        expected_digest: str | None = None,
    ) -> dict:
        """FR-027: 逐项比较候选结果与参考。

        如果提供了 expected_digest，先校验摘要匹配（data-model 2.4）。
        只有用 confirmed 状态的参考才能比较。
        """
        ref = self._store.get_quality_reference(reference_id)
        if ref["status"] != "confirmed":
            raise ValueError(
                f"只能与 confirmed 状态的参考比较，当前: {ref['status']}"
            )
        if expected_digest is not None:
            self.enforce_reference_digest_match(
                reference_id=reference_id, expected_digest=expected_digest,
            )
        # 构建 reference verdict 字典
        ref_verdicts = {
            item["item_index"]: item["verdict"]
            for item in ref["item_results"].get("items", [])
        }
        candidate_verdicts = {}
        for item in candidate_item_results:
            idx = item["item_index"]
            if idx in candidate_verdicts:
                raise ValueError(f"候选结果包含重复 item_index: {idx}")
            candidate_verdicts[idx] = item["verdict"]
        differing_items = []
        matching_count = 0
        all_indexes = sorted(set(ref_verdicts) | set(candidate_verdicts))
        for idx in all_indexes:
            ref_verdict = ref_verdicts.get(idx)
            candidate_verdict = candidate_verdicts.get(idx)
            if candidate_verdict == ref_verdict:
                matching_count += 1
            else:
                differing_items.append({
                    "item_index": idx,
                    "reference_verdict": ref_verdict,
                    "candidate_verdict": candidate_verdict,
                })
        total_items = len(all_indexes)
        return {
            "total_items": total_items,
            "matching_items": matching_count,
            "differing_items": differing_items,
            "diff_count": len(differing_items),
        }

    def classify_quality_differences(
        self, *, diffs: list[dict], reference_id: str,
    ) -> dict:
        """FR-028/FR-034: 将差异分类为正常波动内或需审核。

        - 如果差异项在基线中本身就有波动（stability < 1.0）→ within_variation
        - 如果差异项在基线中完全稳定（stability == 1.0）→ review_required
        """
        ref = self._store.get_quality_reference(reference_id)
        variation = ref["variation_summary"]
        items_with_variation = set(variation.get("items_with_variation", []))
        within_variation = []
        review_required = []
        for diff in diffs:
            idx = diff["item_index"]
            if idx in items_with_variation:
                within_variation.append(diff)
            else:
                review_required.append(diff)
        return {
            "within_variation": within_variation,
            "review_required": review_required,
            "review_count": len(review_required),
        }

    def mark_review_required(
        self, *, reference_id: str, reviewed_item_ids: list[int],
    ) -> dict:
        """FR-034: 将参考标记为 review_required，记录需审核的 item。"""
        return self._store.update_quality_reference_status(
            reference_id, status="review_required",
            reviewed_item_ids=reviewed_item_ids,
        )

    def resolve_reviewed_differences(
        self, *, reference_id: str,
        resolved_item_results: list[dict],
    ) -> dict:
        """FR-034: 用户复核后创建新参考版本。

        旧参考被 superseded，新参考直接为 confirmed 状态。
        """
        old_ref = self._store.get_quality_reference(reference_id)
        # 用解决后的 item_results 创建新参考
        item_results = {"items": resolved_item_results}
        # 保留原 variation_summary
        variation_summary = old_ref["variation_summary"]
        canonical = json.dumps(item_results, ensure_ascii=False, sort_keys=True)
        reference_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        new_ref = self._store.save_quality_reference(
            experiment_id=old_ref["experiment_id"],
            input_version_id=old_ref["input_version_id"],
            item_results=item_results,
            variation_summary=variation_summary,
            reference_digest=reference_digest,
        )
        # supersede 旧参考
        self._store.supersede_quality_references(
            old_ref["experiment_id"], except_id=new_ref["id"],
        )
        # 确认新参考
        confirmed = self._store.update_quality_reference_status(
            new_ref["id"], status="confirmed",
        )
        self._store.set_experiment_quality_reference(
            old_ref["experiment_id"], new_ref["id"],
        )
        return confirmed
