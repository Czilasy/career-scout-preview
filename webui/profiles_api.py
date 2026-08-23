"""画像 / 搜索运行 / 岗位反馈 API 路由（021 B6 T019 外迁自 webui/app.py）。

画像 CRUD 与简历附件、search-runs 查询/取消、岗位生命周期反馈命令、
画像-岗位备注与清理预览。路由体纯搬运：HTTP 契约零改动。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from flask import jsonify, request

from webui.app import (
    CLEANUP_EXPIRED_DAYS,
    FEEDBACK_THRESHOLD,
    _MSG_PROFILE_ID_REQUIRED,
    _feedback_error_response,
    _SharedConnectionStore,
    legacy_platform_guard,
)
from webui.job_feedback import JobFeedbackError, JobFeedbackService
from webui.workbench import (
    merge_profile_fields,
    project_card,
    select_keywords,
)

def register_profiles_routes(app, ctx):
    @app.route("/api/profiles", methods=["GET", "POST"])
    def profiles():
        if request.method == "GET":
            return jsonify({"profiles": ctx.store.list_candidate_profiles()})
        raw = request.get_json(silent=True) or {}
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError("画像名称不能为空")
        confirmed_fields = raw.get("confirmed_fields") or {}
        copy_from = raw.get("copy_from")
        profile = ctx.store.create_profile(
            name, confirmed_fields=confirmed_fields, copy_from=copy_from,
        )
        return jsonify(profile)

    @app.route("/api/profiles/<profile_id>", methods=["GET", "PATCH", "DELETE"])
    def profile_detail(profile_id):
        if request.method == "GET":
            return jsonify(ctx.store.get_profile(profile_id))
        if request.method == "DELETE":
            # 先把简历文件移到 .trash，DB 删除失败可回滚；DB 删除成功后再
            # 清理回收文件，避免 DB 失败时原始简历文件已不可恢复。
            resume_dir = Path(app.config["RESUME_DIR"]).resolve()
            trash_dir = resume_dir / ".trash"
            moved: list[tuple[Path, Path]] = []
            try:
                for r in ctx.store.list_resumes(profile_id):
                    if r.get("deleted_at"):
                        continue
                    try:
                        storage_path = ctx.store.get_resume(r["id"]).get("storage_path") or ""
                    except KeyError:
                        continue
                    if not storage_path:
                        continue
                    file_path = (resume_dir / storage_path).resolve()
                    try:
                        file_path.relative_to(resume_dir)
                    except ValueError:
                        continue
                    if not file_path.is_file():
                        continue
                    trash_dir.mkdir(parents=True, exist_ok=True)
                    trash_path = trash_dir / f"{file_path.name}.{ctx.uuid.uuid4().hex}.trash"
                    file_path.replace(trash_path)
                    moved.append((trash_path, file_path))
            except Exception as exc:
                for trash_path, original_path in reversed(moved):
                    try:
                        if trash_path.exists():
                            trash_path.replace(original_path)
                    except OSError:
                        app.logger.exception("简历文件回滚失败：%s -> %s", trash_path, original_path)
                app.logger.exception("简历文件清理失败，画像未删除：%s", exc)
                try:
                    trash_dir.rmdir()
                except OSError:
                    pass
                return jsonify({
                    "ok": False,
                    "error": "简历文件清理失败，画像未删除",
                    "error_code": "resume_cleanup_failed",
                }), 500
            try:
                result = ctx.store.delete_profile(profile_id)
            except Exception:
                for trash_path, original_path in reversed(moved):
                    try:
                        if trash_path.exists():
                            trash_path.replace(original_path)
                    except OSError:
                        app.logger.exception("DB 删除失败后简历文件回滚失败：%s -> %s", trash_path, original_path)
                try:
                    trash_dir.rmdir()
                except OSError:
                    pass
                raise
            cleanup_warning = False
            for trash_path, _original in moved:
                try:
                    if trash_path.exists():
                        trash_path.unlink()
                except OSError:
                    cleanup_warning = True
                    app.logger.warning("画像已删除，但回收文件清理失败：%s", trash_path)
            if cleanup_warning:
                result["cleanup_warning"] = True
            else:
                try:
                    trash_dir.rmdir()
                except OSError:
                    pass
            return jsonify(result)
        raw = request.get_json(silent=True) or {}
        name = raw.get("name")
        confirmed_fields = raw.get("confirmed_fields")
        return jsonify(ctx.store.update_profile(
            profile_id, name=name, confirmed_fields=confirmed_fields,
        ))

    @app.route("/api/profiles/<profile_id>/resume", methods=["POST", "DELETE"])
    def profile_resume(profile_id):
        if request.method == "DELETE":
            ctx.store.get_profile(profile_id)
            resumes = ctx.store.list_resumes(profile_id)
            deleted = False
            for r in resumes:
                if r.get("deleted_at"):
                    continue
                ctx.resume_service.delete_resume(
                    r["id"], ctx.store, resume_dir=app.config["RESUME_DIR"],
                )
                deleted = True
            return jsonify({"deleted": deleted})
        # POST: upload. A second resume starts a fresh profile; only the
        # user-confirmed fields are carried forward, never learned preference.
        current_profile = ctx.store.get_profile(profile_id)
        if "file" not in request.files:
            raise ValueError("请上传简历文件")
        upload = request.files["file"]
        file_bytes = upload.read()
        filename = upload.filename or "resume.txt"
        if current_profile.get("resume_id"):
            created = ctx.store.create_profile(
                f"{Path(filename).stem[:70] or '新简历'} 求职画像",
                confirmed_fields=current_profile.get("confirmed_fields") or {},
            )
            profile_id = created["id"]
        record = ctx.resume_service.save_resume(
            profile_id, file_bytes, filename,
            ctx.resume_service.validate_format(filename),
            app.config["RESUME_DIR"], ctx.store,
        )
        # AI parse occurs only after the user has seen and accepted the
        # pre-upload notice in the UI.  The raw text never leaves this scope.
        ai_suggestion = {}
        settings = ctx.store.get_ai_settings()
        consent = request.form.get("ai_consent") == "true"
        if settings.get("is_configured") and consent:
            cred_ref = ctx.store.get_credential_ref()
            api_key = ctx.ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
            if api_key:
                try:
                    ai_suggestion = ctx.ai_service.parse_resume(
                        record["extracted_text"],
                        settings["endpoint_url"], api_key, settings.get("model", ""),
                    )
                    ctx.store.save_resume_suggestions(record["id"], ai_suggestion)
                    ctx.store.update_ai_status("ready")
                except Exception:
                    ctx.store.update_ai_status("failed", last_error_code="parse_failed")
                    ai_suggestion = {"error": "AI 解析失败，请手动填写"}
        # Merge AI suggestion into confirmed_fields (manual wins)
        profile = ctx.store.get_profile(profile_id)
        merged = merge_profile_fields(
            profile.get("confirmed_fields") or {}, ai_suggestion,
        )
        response = jsonify({
            "resume_id": record["id"],
            "profile_id": profile_id,
            "format": record.get("format"),
            "extraction_status": "ready" if (record.get("extracted_text") or "").strip() else "empty",
            "ai_suggestion": ai_suggestion,
            "merged_fields": merged,
            "privacy_notice": "如勾选 AI 解析，简历文本会发送至你配置的 AI 服务；不会写入日志或接口响应。",
        })
        return response

    @app.route("/api/profiles/<profile_id>/resumes")
    def profile_resume_list(profile_id):
        ctx.store.get_profile(profile_id)
        return jsonify({"resumes": ctx.store.list_resumes(profile_id)})

    @app.route("/api/search-runs", methods=["POST"])
    def create_search_run():
        raw = request.get_json(silent=True) or {}
        legacy_platform_guard(raw.get("platform"))
        profile_id = raw.get("profile_id")
        if not profile_id:
            raise ValueError(_MSG_PROFILE_ID_REQUIRED)
        profile = ctx.store.get_profile(profile_id)
        confirmed_fields = profile.get("confirmed_fields") or {}
        manual_keywords = raw.get("manual_keywords") or []
        manual_filters = raw.get("manual_filters") or {}
        # Merge manual filters into confirmed_fields for keyword selection
        if manual_filters:
            confirmed_fields = {**confirmed_fields, **manual_filters}
        # Use select_keywords to enforce city requirement and cap at 3
        ai_keywords = []
        resume_id = profile.get("resume_id")
        if resume_id:
            ai_keywords = (ctx.store.get_resume(resume_id).get("suggestions") or {}).get("keywords") or []
        keywords = select_keywords(
            manual_keywords=manual_keywords,
            ai_keywords=ai_keywords,
            confirmed_fields=confirmed_fields,
        )
        if not keywords:
            raise ValueError("至少需要一个关键词才能搜索")
        run = ctx.workbench_runner.create_search_run(
            profile_id, keywords=keywords, confirmed_fields=confirmed_fields,
        )
        return jsonify(run), 202

    @app.route("/api/search-runs/<run_id>")
    def search_run_detail(run_id):
        legacy_platform_guard(request.args.get("platform"))
        run = ctx.store.get_search_run(run_id)
        run["queries"] = ctx.store.list_run_queries(run_id)
        run["events"] = ctx.store.list_search_events(run_id, after=request.args.get("after_event_id", 0, type=int))
        return jsonify(run)

    @app.route("/api/search-runs/<run_id>/cancel", methods=["POST"])
    def cancel_search_run(run_id):
        raw = request.get_json(silent=True) or {}
        legacy_platform_guard(raw.get("platform"))
        return jsonify(ctx.workbench_runner.cancel_search_run(run_id))

    @app.route("/api/search-runs/<run_id>/jobs")
    def search_run_jobs(run_id):
        legacy_platform_guard(request.args.get("platform"))
        run = ctx.store.get_search_run(run_id)
        profile_id = run["profile_id"]
        profile_jobs = ctx.store.list_profile_jobs(profile_id, run_id=run_id)
        requested_sort = request.args.get("sort", "relevance")
        if requested_sort not in {"relevance", "latest", "salary"}:
            raise ValueError("sort 必须为 relevance、latest 或 salary")
        # 批量预取 jobs，避免后续 sort key 和 cards 循环 N+1 调用 ctx.store.get_job
        jobs_by_id = ctx.store.list_jobs_by_ids([pj["job_id"] for pj in profile_jobs])

        def _job_for(item):
            return jobs_by_id.get(str(item["job_id"])) or {}

        if requested_sort == "relevance":
            profile_jobs.sort(key=lambda item: (item.get("ai_rank") is None, item.get("ai_rank") or 0, item.get("shown_at") or ""))
        elif requested_sort == "latest":
            profile_jobs.sort(key=lambda item: _job_for(item).get("last_seen_at") or "", reverse=True)
        else:
            def salary_value(item):
                match = re.search(r"\d+(?:\.\d+)?", _job_for(item).get("salary") or "")
                return float(match.group()) if match else 0
            profile_jobs.sort(key=salary_value, reverse=True)
        after_job_id = request.args.get("after_job_id")
        if after_job_id:
            ids = [item["job_id"] for item in profile_jobs]
            if after_job_id in ids:
                profile_jobs = profile_jobs[ids.index(after_job_id) + 1:]
        cards = []
        for pj in profile_jobs:
            if pj["status"] == "deleted":
                continue
            job = jobs_by_id.get(str(pj["job_id"]))
            if not job:
                continue  # job 已被清理，跳过
            # job row already carries jd; pass it as detail so project_card
            # can emit the truncated excerpt.
            # Task 008：profile_jobs.status 是当前生命周期状态的唯一来源；
            # 历史 feedback_events 只保留偏好学习语义，不再在读投影时
            # 把 applied/read/stale 覆盖回 interested。
            cards.append(project_card(job, job, interest_state=pj["status"]))
        return jsonify({"jobs": cards})

    @app.route("/api/jobs/<job_id>/feedback", methods=["POST"])
    def post_feedback(job_id):
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        action = raw.get("action")
        reason = raw.get("reason")
        if not profile_id or action not in {"interested", "not_interested"}:
            raise ValueError("需要 profile_id 和 action (interested/not_interested)")
        fb = ctx.store.create_feedback(profile_id, job_id, None, action, reason=reason)
        if action == "not_interested":
            # Task 008：显式不感兴趣是一条新的用户操作，直接把当前快照写为
            # deleted；不再依赖读取时历史反馈聚合覆盖 profile_jobs.status。
            try:
                ctx.store.update_profile_job(profile_id, job_id, status="deleted")
            except KeyError:
                pass
        feedback_count = ctx.store.count_effective_feedback(profile_id)
        settings = ctx.store.get_ai_settings()
        if feedback_count and feedback_count % FEEDBACK_THRESHOLD == 0 and settings.get("is_configured"):
            credential_ref = ctx.store.get_credential_ref()
            api_key = ctx.ai_service.retrieve_api_key(credential_ref) if credential_ref else ""
            if api_key:
                try:
                    preference = ctx.ai_service.update_preference(
                        ctx.store.get_profile(profile_id), ctx.store.list_feedback(profile_id),
                        settings["endpoint_url"], api_key, settings.get("model", ""),
                    )
                    ctx.store.save_preference_version(profile_id, feedback_count, preference)
                except (ctx.ai_service.AISecurityError, ValueError):
                    # Feedback remains valid; a failed optional preference update
                    # must not alter the user's feedback or task state.
                    ctx.store.update_ai_status("failed", last_error_code="preference_failed")
        return jsonify({
            "feedback_id": fb["id"],
            "interest_state": action,
        })

    @app.route("/api/feedback/<feedback_id>/revoke", methods=["POST"])
    def revoke_feedback(feedback_id):
        fb = ctx.store.get_feedback(feedback_id)
        already_revoked = bool(fb.get("revoked_at"))
        ctx.store.revoke_feedback(feedback_id)
        if fb["action"] == "not_interested" and not already_revoked:
            # Task 008：显式撤销也是一次用户操作。仅当当前快照仍是 deleted
            # 且没有其他生效中的不感兴趣反馈时，恢复为 new；历史事件保留。
            remaining = [
                event for event in ctx.store.list_feedback(
                    fb["profile_id"], job_id=fb["job_id"])
                if event.get("action") == "not_interested"
                and not event.get("revoked_at")
            ]
            if not remaining:
                try:
                    pj = ctx.store.get_profile_job(fb["profile_id"], fb["job_id"])
                except KeyError:
                    pj = None
                if pj is not None and pj["status"] == "deleted":
                    ctx.store.update_profile_job(
                        fb["profile_id"], fb["job_id"], status="new")
        return jsonify({"revoked": True})

    @app.route("/api/profile-jobs")
    def list_profile_jobs():
        profile_id = request.args.get("profile_id")
        if not profile_id:
            raise ValueError(_MSG_PROFILE_ID_REQUIRED)
        status = request.args.get("status")
        run_id = request.args.get("run_id")
        jobs = ctx.store.list_profile_jobs(profile_id, status=status, run_id=run_id)
        return jsonify({"jobs": jobs})

    @app.route("/api/profile-jobs/<profile_id>/<job_id>", methods=["PATCH"])
    def patch_profile_job(profile_id, job_id):
        """Legacy PATCH：迁移期兼容，全部生命周期写入转入统一命令服务。

        不允许直接 UPDATE status/applied_at 绕过事件、幂等回执和时间校验；
        request ID 来自 Idempotency-Key 请求头或 body request_id，缺失 428；
        note 与生命周期混合请求在同一事务中原子完成或整体拒绝。
        """
        raw = request.get_json(silent=True) or {}
        has_status = "status" in raw
        has_applied = "applied_at" in raw
        has_note = "note" in raw
        if not (has_status or has_applied):
            # note-only（或空载荷）保持现有独立备注语义，无需 request ID
            allowed = {k: raw[k] for k in ("note",) if k in raw}
            return jsonify(ctx.store.update_profile_job(profile_id, job_id, **allowed))

        request_id = str(
            request.headers.get("Idempotency-Key")
            or raw.get("request_id")
            or ""
        ).strip()
        if not request_id:
            return _feedback_error_response(
                "idempotency_key_required",
                "写请求必须携带 Idempotency-Key 或 request_id",
                status=428,
            )

        # 保持 legacy 语义：画像岗位关联不存在时 404，不隐式创建
        try:
            ctx.store.get_profile_job(profile_id, job_id)
        except KeyError:
            return _feedback_error_response("not_found", "画像岗位不存在", 404)

        if has_status and has_applied:
            action, target_status, applied_at = (
                "correct_status", raw["status"], raw["applied_at"])
        elif has_status:
            action, target_status, applied_at = (
                "correct_status", raw["status"], None)
        else:
            action, target_status, applied_at = (
                "correct_applied_at", None, raw["applied_at"])

        def _run_command(service):
            return service.execute_action(
                request_id=request_id,
                profile_id=profile_id,
                job={"job_id": job_id},
                action=action,
                applied_at=applied_at,
                target_status=target_status,
            )

        try:
            if has_note:
                # 混合请求：命令服务与 note 共用同一连接/事务，
                # 要么一起提交，要么整体回滚后拒绝 legacy_patch_ambiguous。
                conn = ctx.store._connect()
                try:
                    shared_service = JobFeedbackService(
                        _SharedConnectionStore(ctx.store, conn))
                    result = _run_command(shared_service)
                    conn.execute(
                        "UPDATE profile_jobs SET note=? "
                        "WHERE profile_id=? AND job_id=?",
                        (raw["note"], str(profile_id), str(job_id)),
                    )
                    conn.commit()
                except JobFeedbackError:
                    conn.rollback()
                    raise
                except sqlite3.Error:
                    conn.rollback()
                    raise JobFeedbackError(
                        "legacy_patch_ambiguous",
                        "备注与生命周期写入无法原子完成",
                    )
                finally:
                    conn.close()
            else:
                result = _run_command(ctx.job_feedback_service)
        except JobFeedbackError as exc:
            if exc.code == "legacy_patch_ambiguous":
                return _feedback_error_response(exc.code, str(exc), exc.details, 400)
            return _feedback_error_response(exc.code, str(exc), exc.details)
        except sqlite3.Error:
            return _feedback_error_response(
                "persistence_failed", "岗位数据保存失败，请重试", status=500)
        return jsonify({"ok": True, **result})

    @app.route("/api/cleanup-preview")
    def cleanup_preview():
        would_remove = ctx.store.preview_cleanup_expired_jobs(days=CLEANUP_EXPIRED_DAYS)
        return jsonify({"would_remove": len(would_remove), "items": would_remove})
