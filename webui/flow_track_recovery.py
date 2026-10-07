"""047 US2：failed 单轨重试编排（无 SQL）。

- 抓取失败：由 FlowSubmissionService.retry_failed_track 复用冻结提交快照提交新抓取。
- AI 失败：本 mixin 复用该轨已有持久化抓取输入与旧 AI 断点，经既有
  FlowAiCoordinator 白箱与平台 lane 创建新 AI run；旧失败 run、旧结果指针与
  兄弟轨道完全不动，提交失败只补偿本次新尝试。

claim/CAS 与绑定在 webui/store_flow_retry.py 的事务内完成；本域只做编排。
"""

from __future__ import annotations

from webui.flow_errors import FlowResumeError, public_flow_message
from webui.flow_task_state import FlowStateClosureError, close_flow_task_state
from webui.logging_setup import get_logger

_logger = get_logger(__name__)


class FlowTrackRecoveryMixin:
    """Retry one failed Track inside its current Flow (AI lane)."""

    def retry_failed_ai_track(
        self,
        *,
        flow_id,
        platform,
        profile_id,
        expected_run_id="",
        expected_updated_at=None,
        expected_track_id="",
        flow=None,
        track=None,
    ) -> dict:
        """Create one fresh AI attempt from this track's persisted scrape input.

        C2：AI failed 的新 run 复用 source scrape、frozen 条件、账号身份与
        既有 ``resume_from_run_id`` 断点能力；缺输入/身份时给出可解释 409，
        不提交、不重抓兄弟。旧 result 指针在 claim 时保留，新结果提交成功
        后由既有 ``complete_flow_track`` 流程替换。
        """
        platform = str(platform or "").strip().lower()
        if track is None:
            raise KeyError(f"{flow_id}:{platform}")
        track_id = str(track.get("id") or "").strip()
        expected_track_id = str(expected_track_id or "").strip()
        if expected_track_id and expected_track_id != track_id:
            raise FlowResumeError(
                "preflight_resume_unavailable",
                "平台运行线已变化，请刷新后重试",
            )
        old_run_id = str(expected_run_id or "").strip()
        if not old_run_id:
            raise FlowResumeError(
                "preflight_resume_unavailable",
                "失败的筛选任务缺少可复用的记录，请重新开始这一平台的筛选",
            )
        old_run = self.store.get_screening_run(old_run_id)
        if old_run is None or old_run.get("record_kind") == "result_snapshot":
            raise FlowResumeError(
                "preflight_resume_unavailable",
                "失败的筛选任务记录不可用，请刷新后重试",
            )
        old_params = dict(old_run.get("execution_params") or {})
        if (
            str(old_params.get("flow_id") or "") != str(flow_id)
            or (track_id and str(old_params.get("track_id") or "") != track_id)
            or str(old_params.get("platform") or platform) != platform
        ):
            raise FlowResumeError(
                "preflight_resume_unavailable",
                "失败的筛选任务不属于当前运行线，请刷新后重试",
            )
        source_run_id = str(
            old_params.get("scrape_task_id")
            or track.get("scrape_run_id")
            or ""
        ).strip()
        if not source_run_id:
            raise FlowResumeError(
                "preflight_resume_unavailable",
                "找不到这条运行线已持久化的抓取输入，无法重试",
            )
        source_run = self.store.get_screening_run(source_run_id) or {}
        if not self.store.load_scrape_run_jobs(source_run_id):
            raise FlowResumeError(
                "preflight_resume_unavailable",
                "抓取输入为空或已不可用，无法重试筛选",
            )
        source_params = dict(source_run.get("execution_params") or {})
        identity = {}
        for key in ("browser_account", "cdp_port", "profile_key"):
            value = old_params.get(key) or source_params.get(key)
            if value in (None, ""):
                raise FlowResumeError(
                    "source_cdp_unavailable",
                    public_flow_message("source_cdp_unavailable"),
                )
            identity[key] = value
        screening_fields = old_run.get("frozen_filters") or {}
        if not isinstance(screening_fields, dict):
            raise FlowResumeError(
                "preflight_resume_unavailable",
                "筛选条件快照不可用，请重新开始这一平台的筛选",
            )

        attempt_params = dict(old_params)
        attempt_params.pop("retry_of_run_id", None)
        attempt_params.update({
            "platform": platform,
            "flow_id": str(flow_id),
            "track_id": track_id,
            "profile_id": str(profile_id),
            "scrape_task_id": source_run_id,
            "browser_account": identity["browser_account"],
            "cdp_port": identity["cdp_port"],
            "profile_key": identity["profile_key"],
        })
        claim = self.store.claim_flow_track_retry(
            flow_id,
            platform,
            profile_id=profile_id,
            expected_track_id=track_id,
            expected_run_id=old_run_id,
            expected_updated_at=expected_updated_at,
            stage="ai",
            attempt_params=attempt_params,
            frozen_filters=screening_fields,
            source_count=self.store.count_scrape_run_jobs(source_run_id),
        )
        if not claim.get("claimed"):
            raise FlowResumeError(
                "preflight_resume_unavailable",
                public_flow_message("preflight_resume_unavailable"),
            )
        task_id = str(claim["new_run_id"])
        future = None
        try:
            register = getattr(self.ctx, "register_pipeline_task", None)
            if not callable(register):
                raise RuntimeError("pipeline task registration unavailable")
            task = register(task_id, "ai_screen", source_task_id=source_run_id)
            with self.ctx.lock:
                task.update({
                    "flow_id": str(flow_id),
                    "profile_id": str(profile_id),
                    "platform": platform,
                    "browser_account": identity["browser_account"],
                    "cdp_port": identity["cdp_port"],
                    "profile_key": identity["profile_key"],
                    "retry_of_run_id": old_run_id,
                    "source_task_id": source_run_id,
                })
            from webui.flow_ai_coordinator import FlowAiCoordinator
            from webui.flow_service import submit_platform_task

            coordinator = FlowAiCoordinator(self.ctx)
            coordinator.begin_whitebox(task_id=task_id, scrape_task_id=source_run_id)
            # resume_from_run_id=task_id：runner 认定主记录已由本事务创建，
            # 不再 INSERT OR REPLACE（那会清空 Track 绑定并抹掉 retry 关联）；
            # 新run 以自己的白箱 owner 从已有抓取输入重新执行 AI，旧失败 run 保留。
            future = submit_platform_task(
                self.ctx,
                flow_id,
                platform,
                self.ctx.run_ai_screen_task,
                task_id,
                screening_fields,
                str(old_params.get("profile_summary") or ""),
                source_run_id,
                task_id,
                old_params.get("profile_facts"),
                cross_platform_dedupe=bool(
                    old_params.get("cross_platform_dedupe", True)
                ),
            )
            if future is None:
                raise RuntimeError("platform executor rejected task")
            from webui.flow_future import attach_ai_future_failure

            attach_ai_future_failure(
                future,
                self.ctx,
                task_id=task_id,
                scrape_task_id=source_run_id,
                platform=platform,
            )
            return self.store.get_flow(flow_id, profile_id=profile_id)
        except Exception as exc:  # noqa: BLE001 - compensate only this new attempt
            self._compensate_ai_retry(
                flow_id=flow_id,
                platform=platform,
                profile_id=profile_id,
                source_run_id=source_run_id,
                task_id=task_id,
                future=future,
                exc=exc,
            )
            raise

    def _compensate_ai_retry(
        self, *, flow_id, platform, profile_id, source_run_id, task_id, future, exc,
    ) -> None:
        """Close only this new AI attempt; the old failed run stays untouched."""
        if future is not None:
            cancel = getattr(future, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception as cancel_exc:  # noqa: BLE001 - visible cleanup loss
                    _logger.warning(
                        "AI retry future cancellation failed (%s)",
                        type(cancel_exc).__name__,
                    )
        with self.ctx.lock:
            task = self.ctx.tasks.get(task_id)
            if task is not None:
                stop_event = task.get("stop_event")
                if stop_event is not None:
                    try:
                        from webui.task_pause_support import (
                            STOP_MODE_CANCEL,
                            request_stop,
                        )

                        request_stop(task, stop_event, STOP_MODE_CANCEL)
                    except Exception:  # noqa: BLE001 - keep closing the attempt
                        _logger.debug("AI retry stop signal failed", exc_info=True)
                task["status"] = "failed"
                task["error"] = public_flow_message(
                    getattr(exc, "error_code", "") or "flow_ai_start_failed"
                )
        code = str(getattr(exc, "error_code", "") or "flow_ai_start_failed")
        if code not in public_flow_codes():
            code = "flow_ai_start_failed"
        try:
            close_flow_task_state(
                self.ctx,
                task_id=task_id,
                scrape_task_id=source_run_id,
                flow_id=flow_id,
                profile_id=profile_id,
                status="failed",
                error_code=code,
                reason=public_flow_message(code, exc),
                platform=platform,
                stage="ai",
            )
        except Exception as cleanup_exc:  # noqa: BLE001 - compensation must be visible
            _logger.warning(
                "AI retry compensation failed (%s)", type(cleanup_exc).__name__,
            )
            raise FlowStateClosureError() from cleanup_exc
        for name in ("schedule_pipeline_task_cleanup", "release_worker_resume_claims"):
            callback = getattr(self.ctx, name, None)
            if callable(callback):
                try:
                    if name == "release_worker_resume_claims":
                        callback(self.ctx.tasks.get(task_id))
                    else:
                        callback(task_id)
                except Exception:  # noqa: BLE001 - never hide cleanup loss
                    _logger.debug("%s failed during AI retry cleanup", name, exc_info=True)


def public_flow_codes() -> frozenset:
    """Public Flow error codes accepted for AI retry closure."""
    from webui.flow_errors import FLOW_ERROR_MESSAGES

    return frozenset(FLOW_ERROR_MESSAGES) | frozenset({
        "source_cdp_unavailable", "source_login_required", "browser_busy",
        "account_pool_empty", "flow_ai_start_failed", "whitebox_incomplete",
    })


__all__ = ["FlowTrackRecoveryMixin"]