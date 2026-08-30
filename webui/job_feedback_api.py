"""Standalone HTTP route registrar for lifecycle/reminders/events/advice (spec 002 Task 005).

Exposes the frozen ``job-feedback-v1`` contract on top of the already
implemented domain modules:

- ``webui.job_feedback.JobFeedbackService`` — command semantics, time
  validation, receipts, events and the reminder projection;
- ``webui.job_advice.generate_advice`` — read-only AI adapter with rule
  fallback;
- ``webui.store.TaskStore`` — persistence and the dual-index job upsert.

This layer only performs the auth boundary hand-off (the hosting app's
``before_request`` guards apply once the blueprint is registered), request
parsing, domain delegation and stable error mapping.  It never copies
action/time/reminder/advice rules, never accepts a ``platform`` filter for
reminders, and every read is side-effect free.

Registration into the real application is owned by Task 008; this module
stays importable and testable in isolation via
:func:`create_job_feedback_blueprint` / :func:`register_job_feedback_routes`.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from webui import ai as ai_module
from webui.job_advice import JobAdviceInput, generate_advice
from webui.job_feedback import (
    REMINDER_THRESHOLD_HOURS,
    JobFeedbackError,
    JobFeedbackService,
)
from webui.platforms import PlatformError, normalize_job_url

__all__ = [
    "create_job_feedback_blueprint",
    "register_job_feedback_routes",
]

# Frozen contract: error_code -> HTTP status. 031 B3 起唯一来源在
# webui/constants.py（_FEEDBACK_ERROR_STATUS），此处保留历史别名。
from webui.constants import _FEEDBACK_ERROR_STATUS as _ERROR_STATUS


_MSG_PROFILE_ID_REQUIRED = "profile_id 不能为空"
_MSG_PROFILE_OR_JOB_NOT_FOUND = "画像或岗位不存在"


def _error(code: str, user_message: str, details: dict | None = None):
    """Stable error body; never leaks SQL, paths, keys or tracebacks."""
    status = _ERROR_STATUS.get(code, 500)
    return jsonify({
        "ok": False,
        "error_code": code,
        "user_message": user_message,
        "details": details or {},
    }), status


def _domain_error_response(exc: JobFeedbackError):
    return _error(exc.code, str(exc), exc.details)


def _persistence_error_response():
    return _error("persistence_failed", "岗位数据保存失败，请重试")


def _default_ai_credentials(store):
    """Resolve AI settings/credentials from the store; never raises."""
    try:
        settings = store.get_ai_settings()
    except Exception:
        settings = {"is_configured": False}
    credential_ref = ""
    api_key = ""
    try:
        credential_ref = store.get_credential_ref() or ""
        if credential_ref:
            api_key = ai_module.retrieve_api_key(credential_ref) or ""
    except Exception:
        api_key = ""
    return settings, credential_ref, api_key, settings.get("model", "")


def _parse_int(value, default):
    if value is None or str(value).strip() == "":
        return default
    return int(str(value).strip())


def _resolve_read_only_job(store, platform: str, platform_job_id: str, canonical_url: str):
    """Read-only identity lookup; a read request never creates a job."""
    try:
        normalized_url = normalize_job_url(platform, canonical_url)
    except PlatformError as exc:
        raise JobFeedbackError("platform_url_mismatch", "岗位规范链接与平台不匹配") from exc
    if not normalized_url:
        raise JobFeedbackError("platform_url_mismatch", "岗位规范链接与平台不匹配")
    with store._connection() as conn:
        row = conn.execute(
            "SELECT id FROM jobs WHERE (platform=? AND platform_job_id=?) "
            "OR canonical_url=? LIMIT 1",
            (platform, platform_job_id, normalized_url),
        ).fetchone()
    if row is None:
        raise JobFeedbackError("not_found", "岗位不存在")
    return str(row["id"])


def create_job_feedback_blueprint(
    store,
    *,
    advice_provider=None,
    ai_credentials_provider=None,
) -> Blueprint:
    """Build the isolated blueprint; no app-level side effects.

    ``advice_provider`` and ``ai_credentials_provider`` are test seams only;
    production wiring uses the store-backed defaults.
    """
    service = JobFeedbackService(store)
    credentials_provider = ai_credentials_provider or (
        lambda: _default_ai_credentials(store)
    )
    blueprint = Blueprint("job_feedback_api", __name__)

    # == state (read-only) ================================================

    @blueprint.route("/api/profile-jobs/state", methods=["GET"])
    def job_state():
        try:
            profile_id = (request.args.get("profile_id") or "").strip()
            if not profile_id:
                return _error("invalid_request", _MSG_PROFILE_ID_REQUIRED)
            try:
                store.get_profile(profile_id)
            except KeyError:
                return _error("not_found", _MSG_PROFILE_OR_JOB_NOT_FOUND)

            job_id = (request.args.get("job_id") or "").strip()
            if job_id:
                try:
                    store.get_job(job_id)
                except KeyError:
                    return _error("not_found", _MSG_PROFILE_OR_JOB_NOT_FOUND)
            else:
                platform = (request.args.get("platform") or "").strip()
                platform_job_id = (request.args.get("platform_job_id") or "").strip()
                canonical_url = (request.args.get("canonical_url") or "").strip()
                if not (platform and platform_job_id and canonical_url):
                    return _error("job_identity_incomplete", "岗位身份三元组不完整")
                job_id = _resolve_read_only_job(
                    store, platform, platform_job_id, canonical_url,
                )

            try:
                state = service.get_state(profile_id, job_id)
            except JobFeedbackError as exc:
                if exc.code == "profile_job_not_found":
                    return jsonify({"ok": True, "exists": False, "job_id": job_id})
                return _domain_error_response(exc)
            return jsonify({"ok": True, "exists": True, "state": state})
        except JobFeedbackError as exc:
            return _domain_error_response(exc)
        except Exception:
            return _persistence_error_response()

    # == actions (commands) ===============================================

    @blueprint.route("/api/profile-jobs/actions", methods=["POST"])
    def job_action():
        try:
            raw = request.get_json(silent=True)
            if not isinstance(raw, dict):
                return _error("invalid_request", "请求体必须为 JSON 对象")
            profile_id = raw.get("profile_id")
            if profile_id in (None, ""):
                return _error("invalid_request", _MSG_PROFILE_ID_REQUIRED)
            result = service.execute_action(
                request_id=raw.get("request_id"),
                profile_id=profile_id,
                job=raw.get("job"),
                action=raw.get("action"),
                applied_at=raw.get("applied_at"),
                target_status=raw.get("target_status"),
            )
            return jsonify({"ok": True, **result})
        except JobFeedbackError as exc:
            return _domain_error_response(exc)
        except Exception:
            return _persistence_error_response()

    # == events (read-only) ================================================

    @blueprint.route("/api/profile-jobs/<profile_id>/<job_id>/events", methods=["GET"])
    def job_events(profile_id, job_id):
        try:
            try:
                after_sequence = _parse_int(request.args.get("after_sequence"), 0)
            except ValueError:
                return _error("invalid_request", "after_sequence 必须为整数")
            if after_sequence < 0:
                return _error("invalid_request", "after_sequence 不能为负数")
            try:
                limit = _parse_int(request.args.get("limit"), 100)
            except ValueError:
                return _error("invalid_limit", "事件数量超出范围")
            events = service.list_events(
                profile_id, job_id,
                after_sequence=after_sequence, limit=limit,
            )
            next_after = events[-1]["sequence"] if events else after_sequence
            return jsonify({
                "ok": True,
                "events": events,
                "next_after_sequence": next_after,
            })
        except JobFeedbackError as exc:
            return _domain_error_response(exc)
        except Exception:
            return _persistence_error_response()

    # == reminders (read-only, never platform-filtered) ====================

    @blueprint.route("/api/job-reminders/count", methods=["GET"])
    def reminder_count():
        try:
            profile_id = (request.args.get("profile_id") or "").strip()
            if not profile_id:
                return _error("invalid_request", _MSG_PROFILE_ID_REQUIRED)
            total = service.count_reminders(profile_id)
            return jsonify({
                "ok": True,
                "profile_id": profile_id,
                "threshold_hours": REMINDER_THRESHOLD_HOURS,
                "total": total,
            })
        except JobFeedbackError as exc:
            return _domain_error_response(exc)
        except Exception:
            return _persistence_error_response()

    @blueprint.route("/api/job-reminders", methods=["GET"])
    def reminder_list():
        try:
            profile_id = (request.args.get("profile_id") or "").strip()
            if not profile_id:
                return _error("invalid_request", _MSG_PROFILE_ID_REQUIRED)
            try:
                limit = _parse_int(request.args.get("limit"), 100)
            except ValueError:
                return _error("invalid_limit", "提醒列表最多返回 100 条")
            result = service.list_reminders(profile_id, limit=limit)
            return jsonify({"ok": True, **result})
        except JobFeedbackError as exc:
            return _domain_error_response(exc)
        except Exception:
            return _persistence_error_response()

    # == advice (read-only, rule fallback) =================================

    @blueprint.route("/api/profile-jobs/<profile_id>/<job_id>/advice", methods=["POST"])
    def job_advice(profile_id, job_id):
        try:
            try:
                store.get_profile(profile_id)
                job_row = store.get_job(job_id)
            except KeyError:
                return _error("not_found", _MSG_PROFILE_OR_JOB_NOT_FOUND)
            try:
                state = service.get_state(profile_id, job_id)
            except JobFeedbackError as exc:
                if exc.code == "profile_job_not_found":
                    return _error("reminder_not_eligible", "该岗位当前不在提醒范围内")
                return _domain_error_response(exc)
            reminder = state.get("reminder") or {}
            if not reminder.get("eligible"):
                return _error("reminder_not_eligible", "该岗位当前不在提醒范围内")

            settings, credential_ref, api_key, model = credentials_provider()
            result = generate_advice(
                JobAdviceInput(
                    jd=str(job_row.get("jd") or "") or None,
                    applied_at=state.get("applied_at"),
                    last_follow_up_at=state.get("last_follow_up_at"),
                    elapsed_days=reminder.get("elapsed_days"),
                ),
                ai_settings=settings,
                credential_ref=credential_ref,
                api_key=api_key,
                model=model,
                provider=advice_provider,
            )
            return jsonify({
                "ok": True,
                "action": result.action,
                "reason": result.reason,
                "source": result.source,
            })
        except JobFeedbackError as exc:
            return _domain_error_response(exc)
        except Exception:
            return _persistence_error_response()

    return blueprint


def register_job_feedback_routes(app, ctx, **options) -> Blueprint:
    """Register the registrar on any Flask app (isolated fixture or app.py).

    ``ctx`` 是应用装配上下文（真实装配传 PipelineContext，隔离夹具可传任意
    带 ``store`` 属性的对象）。The hosting app's ``before_request``
    session/build-identity guards apply to these routes automatically, which
    is the intended auth boundary.
    """
    blueprint = create_job_feedback_blueprint(ctx.store, **options)
    app.register_blueprint(blueprint)
    return blueprint
