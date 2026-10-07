"""Shared Flow/Track coordination for B096.

The service owns cross-flow gates and durable failure facts.  Platform-specific
scrapers are injected as callbacks, keeping this module independent of BOSS or
Zhilian browser implementations.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from webui.store_flow import FLOW_PLATFORMS, FlowConflictError
from webui.logging_setup import get_logger

_logger = get_logger(__name__)


# 047 结构前置：共享错误与安全文案搬到 flow_errors；这里保持兼容 re-export，
# 异常对象身份与旧 catch/import 语义不变。
from webui.flow_errors import (
    FLOW_ERROR_MESSAGES,
    FlowResumeError,
    PlatformUnavailableError,
    public_flow_message,
)


def submit_platform_task(ctx, flow_id, platform, fn, *args, **kwargs):
    """Submit a Flow worker through its shared platform lane.

    Legacy runs retain the single executor path.  A Flow run always carries
    its platform lane so scrape/AI resume cannot bypass the resource guard.
    """
    flow_id = str(flow_id or "").strip()
    platform = str(platform or "").strip().lower()
    lane = None
    if flow_id:
        if not platform:
            from webui.flow_task_coordinator import MissingPlatformIdentityError

            raise MissingPlatformIdentityError(
                "Flow submission has no durable platform identity"
            )
        lane = (
            getattr(ctx, "platform_executor", None)
            or getattr(ctx, "ai_platform_executor", None)
        )
        if lane is None:
            raise RuntimeError("flow platform lane unavailable")
        return lane.submit(platform, fn, *args, **kwargs)
    return ctx.executor.submit(fn, *args, **kwargs)


class FlowService:
    """Coordinate one Flow while keeping each platform Track independent."""

    _PLATFORM_SET = frozenset(FLOW_PLATFORMS)

    def __init__(
        self,
        store,
        *,
        submit_track: Callable[[str, dict, dict], Mapping | None] | None = None,
        operate_track: Callable[[str, str, dict, dict], object] | None = None,
        track_finalizing: Callable[[dict, dict], bool] | None = None,
        platform_enabled: Callable[[str], bool] | None = None,
        platform_executor=None,
    ):
        self.store = store
        self.submit_track = submit_track
        self.operate_track_callback = operate_track
        self.track_finalizing = track_finalizing
        self.platform_enabled = platform_enabled or (lambda _platform: True)
        self.platform_executor = platform_executor

    def resume_preflight_track(self, *, flow, track, profile_id):
        """Re-submit one paused no-run Track through the durable submission service."""
        callback = getattr(self, "resume_track_callback", None)
        if callable(callback):
            return callback(flow, track, profile_id)
        operation_ctx = getattr(self, "operation_context", None)
        if operation_ctx is None:
            raise FlowConflictError("暂停的平台运行线缺少可用的提交上下文")
        from webui.flow_submission_service import FlowSubmissionService

        return FlowSubmissionService(
            operation_ctx, self,
        ).resume_preflight_track(
            flow_id=flow["id"],
            platform=track["platform"],
            profile_id=profile_id,
            flow=flow,
            track=track,
        )

    def start_flow(
        self,
        *,
        profile_id,
        selection,
        confirmed_filters=None,
        start_key=None,
    ) -> dict:
        platforms = self._platforms_for_selection(selection)
        for platform in platforms:
            if not self.platform_enabled(platform):
                raise PlatformUnavailableError(platform)

        flow = self.store.create_flow(
            profile_id=profile_id,
            selection=selection,
            confirmed_filters=confirmed_filters,
            start_key=start_key,
        )
        scheduled = []
        for track in flow["tracks"]:
            # An idempotent retry must not submit a Track that already has an
            # external run or has reached a terminal/suspended state.
            if track["status"] not in ("queued", "failed"):
                continue
            if any(
                track.get(key)
                for key in ("scrape_run_id", "screen_run_id", "result_run_id")
            ):
                continue
            if self.submit_track is None:
                continue
            platform = track["platform"]
            future = None
            if self.platform_executor is not None:
                future = self.platform_executor.submit(
                    platform, self.submit_track, platform, flow, track,
                )
            scheduled.append((platform, track, future))
        for platform, track, future in scheduled:
            try:
                result = future.result() if future is not None else self.submit_track(
                    platform, flow, track,
                )
                if isinstance(result, Mapping):
                    self.store.bind_flow_track_runs(
                        flow["id"],
                        platform,
                        profile_id=flow["profile_id"],
                        scrape_run_id=result.get("scrape_run_id"),
                        screen_run_id=result.get("screen_run_id"),
                        result_run_id=result.get("result_run_id"),
                    )
                flow = self.store.update_flow_track(
                    flow["id"],
                    platform,
                    profile_id=flow["profile_id"],
                    status="running",
                    stage="scrape",
                )
                self._record_track_event(
                    flow["id"],
                    platform,
                    flow,
                    "flow_track_submitted",
                    {"stage": "scrape"},
                )
            except Exception as exc:  # noqa: BLE001 - one Track must not sink its sibling
                error_code = str(getattr(exc, "error_code", "") or "track_submit_failed")
                if error_code not in FLOW_ERROR_MESSAGES:
                    error_code = "track_submit_failed"
                reason = public_flow_message(error_code, exc)
                try:
                    flow = self.store.update_flow_track(
                        flow["id"],
                        platform,
                        profile_id=flow["profile_id"],
                        status="failed",
                        stage="scrape",
                        error_code=error_code,
                        reason=reason,
                    )
                except Exception as marker_exc:  # noqa: BLE001 - preserve the submit error
                    _logger.warning(
                        "Flow Track failure state write failed after %s: %s",
                        type(exc).__name__, type(marker_exc).__name__,
                    )
                    continue
                try:
                    self._record_track_event(
                        flow["id"],
                        platform,
                        flow,
                        "flow_track_failed",
                        {"stage": "scrape", "error_code": error_code, "reason": reason},
                    )
                except Exception as marker_exc:
                    _logger.warning(
                        "Flow Track failure event write failed after %s: %s",
                        type(exc).__name__, type(marker_exc).__name__,
                    )
        return self.store.get_flow(flow["id"], profile_id=profile_id)

    def start_single_flow(self, *, profile_id, platform, start_key=None) -> dict | None:
        """Create the real one-track Flow used by the legacy search entry."""
        try:
            self.store.get_profile(profile_id)
        except (KeyError, ValueError):
            return None
        return self.start_flow(
            profile_id=profile_id,
            selection=platform,
            confirmed_filters={str(platform).strip().lower(): {}},
            start_key=start_key,
        )

    def get_current_flow(self, profile_id):
        return self.store.get_current_flow(profile_id)

    def get_flow(self, flow_id, *, profile_id):
        return self.store.get_flow(flow_id, profile_id=profile_id)

    def list_flows(self, profile_id, *, include_legacy=False):
        """Expose Flow listing without making HTTP routes reach into store."""
        return self.store.list_flows(profile_id, include_legacy=include_legacy)

    def get_flow_results(self, flow_id, *, profile_id):
        """Expose Flow-scoped results through the service boundary."""
        return self.store.get_flow_results(flow_id, profile_id=profile_id)

    def validate_track_submission(self, *, flow_id, platform, profile_id):
        """Guard an existing Flow before the legacy search route binds a run."""
        flow = self.get_flow(flow_id, profile_id=profile_id)
        track = next(
            (item for item in flow["tracks"] if item["platform"] == str(platform).strip().lower()),
            None,
        )
        if track is None:
            raise KeyError(f"{flow_id}:{platform}")
        if track.get("status") not in {"queued", "failed", "paused"}:
            raise FlowConflictError("该平台运行线已提交或已结束")
        if any(track.get(key) for key in ("scrape_run_id", "screen_run_id", "result_run_id")):
            raise FlowConflictError("该平台运行线已提交")
        if track.get("status") == "paused" and not track.get("submission_snapshot"):
            raise FlowConflictError("暂停的平台运行线缺少可重试的搜索快照")
        return flow

    def save_track_submission_snapshot(
        self, *, flow_id, platform, profile_id, snapshot,
    ) -> dict:
        """Persist the safe request data used to retry a preflight pause."""
        return self.store.save_flow_track_submission(
            flow_id,
            platform,
            profile_id=profile_id,
            snapshot=snapshot,
        )

    def claim_track_submission(self, *, flow_id, platform, profile_id) -> dict:
        """Claim the durable Track before the API creates an external task."""
        return self.store.claim_flow_track_submission(
            flow_id, platform, profile_id=profile_id,
        )

    def bind_track_runs(self, *, flow_id, platform, profile_id, **run_ids) -> dict:
        """Bind scrape/screen/result identities through the service boundary."""
        return self.store.bind_flow_track_runs(
            flow_id, platform, profile_id=profile_id, **run_ids,
        )

    def create_scrape_run(
        self, *, task_id, profile_id, platform, flow_id, track_id,
        profile_snapshot=None,
    ) -> dict:
        """Create the scrape-side identity behind one Flow Track."""
        return self.store.create_scrape_search_run(
            task_id,
            profile_id,
            platform=platform,
            flow_id=flow_id,
            track_id=track_id,
            profile_snapshot=profile_snapshot,
        )

    def mark_submission_failed(
        self, *, flow_id, platform, profile_id, task_id=None,
        error_code="track_submit_failed", reason="", stage="scrape",
        status="failed",
    ) -> dict:
        """Close every already-created run and the Flow Track consistently."""
        if status not in {"paused", "failed"}:
            raise ValueError("status must be paused or failed")
        safe_reason = public_flow_message(error_code, reason)
        if task_id:
            # Submission failures can happen after the screening/search rows
            # are created but before the Track receives both bindings.  Keep
            # the run projections and Track transition in the store's single
            # transaction; the old per-row writes could leave a running Track
            # when the second projection failed.
            close_atomic = getattr(self.store, "close_flow_task_state_atomic", None)
            if callable(close_atomic):
                return close_atomic(
                    flow_id,
                    str(platform).strip().lower(),
                    profile_id,
                    task_run_id=task_id,
                    scrape_run_id=task_id,
                    status=status,
                    error_code=error_code,
                    error_reason=safe_reason,
                    stage=stage,
                )
            try:
                current = self.store.get_screening_run(task_id) or {}
                if status == "paused" and current.get("status") == "queued":
                    self.store.update_screening_run(task_id, status="running")
                self.store.update_screening_run(
                    task_id, status=status, error_code=error_code,
                    error_reason=safe_reason,
                )
            except Exception as exc:  # noqa: BLE001 - preserve Flow closure
                _logger.warning(
                    "Flow submission screening run close failed (%s)",
                    type(exc).__name__,
                )
            try:
                current = self.store.get_search_run(task_id)
                if status == "paused" and current.get("status") == "queued":
                    self.store.update_search_run(task_id, status="running")
                self.store.update_search_run(
                    task_id, status=status, error_code=error_code,
                )
            except (KeyError, ValueError):
                pass
            except Exception as exc:  # noqa: BLE001 - preserve Flow closure
                _logger.warning(
                    "Flow submission search run close failed (%s)",
                    type(exc).__name__,
                )
        return self.fail_track(
            flow_id=flow_id,
            platform=platform,
            profile_id=profile_id,
            error_code=error_code,
            reason=safe_reason,
            stage=stage,
            status=status,
        )

    def retry_track(
        self, *, flow_id, platform, profile_id,
        expected_run_id="", expected_updated_at=None,
        expected_track_id=None,
    ) -> dict:
        """Retry one failed Track through the dedicated submission service."""
        operation_ctx = getattr(self, "operation_context", None)
        if operation_ctx is None:
            raise FlowConflictError("失败重试缺少可用的提交上下文")
        from webui.flow_submission_service import FlowSubmissionService

        return FlowSubmissionService(operation_ctx, self).retry_failed_track(
            flow_id=flow_id,
            platform=platform,
            profile_id=profile_id,
            expected_run_id=expected_run_id,
            expected_updated_at=expected_updated_at,
            expected_track_id=expected_track_id,
        )

    def operate_track(self, *, flow_id, platform, profile_id, action,
                      expected_run_id=None, expected_updated_at=None,
                      expected_track_id=None) -> dict:
        """Thin delegate: the track action编排 lives in flow_track_operations."""
        from webui.flow_track_operations import operate_track as _operate_track

        return _operate_track(
            self,
            flow_id=flow_id,
            platform=platform,
            profile_id=profile_id,
            action=action,
            expected_run_id=expected_run_id,
            expected_updated_at=expected_updated_at,
            expected_track_id=expected_track_id,
        )

    def fail_track(
        self,
        *,
        flow_id,
        platform,
        profile_id,
        error_code,
        reason,
        stage="scrape",
        status="failed",
    ) -> dict:
        if status not in {"paused", "failed"}:
            raise ValueError("status must be paused or failed")
        safe_reason = public_flow_message(error_code, reason)
        flow = self.store.update_flow_track(
            flow_id,
            platform,
            profile_id=profile_id,
            status=status,
            stage=stage,
            error_code=error_code,
            reason=safe_reason,
        )
        self._record_track_event(
            flow_id,
            platform,
            flow,
            "flow_track_failed",
            {"stage": stage, "error_code": error_code, "reason": safe_reason},
        )
        return self.store.get_flow(flow_id, profile_id=profile_id)

    def record_preflight_failure(
        self,
        *,
        flow_id,
        platform,
        profile_id,
        error_code,
        recoverable=False,
        reason="",
    ) -> dict:
        """Persist a preflight fact without clobbering a concurrent claim."""
        safe_reason = public_flow_message(error_code, reason)
        flow, changed = self.store.record_flow_track_preflight_failure(
            flow_id,
            platform,
            profile_id=profile_id,
            error_code=error_code,
            reason=safe_reason,
            status="paused" if recoverable else "failed",
        )
        if changed:
            self._record_track_event(
                flow_id,
                platform,
                flow,
                "flow_track_failed",
                {
                    "stage": "scrape",
                    "error_code": error_code,
                    "reason": safe_reason,
                },
            )
        return flow

    def mark_scrape_complete(self, *, flow_id, platform, profile_id, screen_run_id=None):
        """Move one Track to its AI stage while siblings keep their state."""
        flow, track = self._track(flow_id, platform, profile_id)
        if track["status"] in {"failed", "stopped", "cancelled", "done", "succeeded"}:
            raise ValueError("terminal Track cannot advance from scrape")
        if screen_run_id:
            self.store.bind_flow_track_runs(
                flow_id, platform, profile_id=profile_id, screen_run_id=screen_run_id,
            )
        updated = self.store.update_flow_track(
            flow_id, platform, profile_id=profile_id, status="running", stage="ai",
        )
        self._record_track_event(
            flow_id, platform, updated, "flow_track_scrape_complete", {"stage": "ai"},
        )
        return self.store.get_flow(flow_id, profile_id=profile_id)

    def begin_ai(self, *, flow_id, platform, profile_id, screen_run_id):
        """Bind and start one Track's AI run exactly once."""
        try:
            updated = self.store.claim_flow_track_ai(
                flow_id,
                platform,
                profile_id=profile_id,
                screen_run_id=screen_run_id,
            )
        except (FlowConflictError, ValueError):
            # A concurrent claimant may have created an unbound run with the
            # same Flow identity.  Clean only that caller-owned process log;
            # wrong/missing Flow sources are retained for explicit diagnostics.
            try:
                self.store.delete_unbound_flow_ai_run(
                    screen_run_id, flow_id=flow_id,
                )
            except Exception as cleanup_exc:  # noqa: BLE001 - preserve the claim error
                _logger.debug(
                    "AI run cleanup failed (%s)", type(cleanup_exc).__name__,
                )
            raise
        try:
            self._record_track_event(
                flow_id, platform, updated, "flow_track_ai_started", {"stage": "ai"},
            )
        except Exception as exc:  # noqa: BLE001 - event failure must close the Track
            try:
                self.store.update_flow_track(
                    flow_id,
                    platform,
                    profile_id=profile_id,
                    status="failed",
                    stage="ai",
                    error_code="flow_ai_start_failed",
                    reason=public_flow_message("flow_ai_start_failed"),
                )
            except Exception as failure_exc:  # noqa: BLE001 - retain the original event error
                _logger.debug(
                    "AI Track failure write failed (%s)", type(failure_exc).__name__,
                )
            raise
        return self.store.get_flow(flow_id, profile_id=profile_id)

    def mark_result_ready(self, *, flow_id, platform, profile_id, result_run_id):
        """Publish one Track's result snapshot without touching its sibling."""
        updated = self.store.complete_flow_track(
            flow_id,
            platform,
            profile_id=profile_id,
            result_run_id=result_run_id,
        )
        self._record_track_event(
            flow_id, platform, updated, "flow_track_result_ready", {"stage": "complete"},
        )
        return self.store.get_flow(flow_id, profile_id=profile_id)

    def _track(self, flow_id, platform, profile_id):
        flow = self.store.get_flow(flow_id, profile_id=profile_id)
        track = next(
            (item for item in flow["tracks"] if item["platform"] == platform), None
        )
        if track is None:
            raise KeyError(f"{flow_id}:{platform}")
        return flow, track

    def _record_track_event(self, flow_id, platform, flow, event_type, payload):
        track = next(
            (item for item in flow.get("tracks", []) if item["platform"] == platform),
            None,
        )
        if track is None:
            return
        run_id = (
            track.get("screen_run_id")
            or track.get("scrape_run_id")
            or track.get("result_run_id")
        )
        if not run_id:
            return
        self.store.append_task_event(
            run_id,
            event_type,
            {"flow_id": str(flow_id), "platform": platform, **dict(payload or {})},
        )

    @classmethod
    def _platforms_for_selection(cls, selection):
        selection = str(selection or "").strip().lower()
        if selection == "all":
            return FLOW_PLATFORMS
        if selection in cls._PLATFORM_SET:
            return (selection,)
        raise ValueError("selection must be all, boss, or zhilian")


__all__ = [
    "FlowService",
    "FlowResumeError",
    "FlowConflictError",
    "PlatformUnavailableError",
    "public_flow_message",
    "submit_platform_task",
]
