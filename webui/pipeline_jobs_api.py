"""Pipeline 岗位操作 / 批量重抓 API 路由（021 B6 T019 外迁自 webui/app.py）。
单岗位 JD 按需抓取、感兴趣/不感兴趣标记与撤销、批量重抓提交与续跑。
路由体纯搬运：HTTP 契约零改动；store / 任务声明 / 重抓 runner 经 ctx
取用。
"""
from __future__ import annotations
import uuid
import sqlite3
from pathlib import Path
from flask import jsonify, request
from webui.constants import _MSG_PROFILE_ID_REQUIRED, _MSG_PROFILE_NOT_FOUND, _ZHILIAN_HOST_TOKEN
from webui.task_status import _pipeline_identity_payload
from webui.pipeline_job_identity import JobIdentityError, parse_identity_payload, resolve_job_identity
from webui.resume_identity import append_account_switch_log_line, ensure_frozen_browser_account, inherit_parent_frozen_identity
from webui.task_runners import _iso_epoch_ms
from webui.workbench import normalize_job_link_for_platform

def register_pipeline_jobs_routes(app, ctx):

    def _resolve_pipeline_job_identity(job):
        """Task 008：pipeline 入口统一走权威岗位身份解析（Task 003）。
        使用 Task 001 的 connection-aware 双索引 upsert，在调用方事务内
        完成可靠入库并返回内部 jobs.id；身份失败在关联写入前抛出，
        保证零副作用。BOSS 与智联共用同一协议，无平台分支。
        """
        identity_request = parse_identity_payload(_pipeline_identity_payload(job))
        with ctx.store._connection() as conn:
            return resolve_job_identity(conn, ctx.store, identity_request)

    def _pipeline_identity_error_response(exc: JobIdentityError):
        return (jsonify({'ok': False, 'error_code': exc.code, 'user_message': str(exc), 'details': exc.details}), exc.http_status)

    def _recrawl_whitebox_begin(task_id, job_ids, *, parent_id=None):
        from webui.whitebox import WhiteboxService
        service = WhiteboxService(ctx.store)
        return service.begin('recrawl', str(task_id), {'stages': ['recrawl'], 'units': [{'unit_key': f'job:{job_id}', 'unit_kind': 'job', 'stage': 'recrawl', 'required': True} for job_id in sorted({str(value) for value in job_ids})]}, parent_owner_id=parent_id)

    def _recrawl_whitebox_record(task_id, event_type, unit_key, payload, *, attempt=1, severity='info', required=True):
        from webui.store_helpers import _now
        from webui.whitebox import WhiteboxService
        run = ctx.store.get_whitebox_run('recrawl', str(task_id))
        if run is None:
            return False
        return WhiteboxService(ctx.store).record(run['id'], {'idempotency_key': f'{event_type}:{task_id}:{unit_key}:{attempt}', 'event_type': event_type, 'occurred_at': _now(), 'stage': 'recrawl', 'unit_kind': 'job' if unit_key else None, 'unit_key': unit_key, 'attempt_no': attempt, 'required_evidence': required, 'severity': severity, 'payload': payload or {}})

    def _recrawl_whitebox_finish(task_id, *, lifecycle_end=None):
        from webui.whitebox import WhiteboxService
        run = ctx.store.get_whitebox_run('recrawl', str(task_id))
        return WhiteboxService(ctx.store).finalize(run['id'], lifecycle_end=lifecycle_end) if run else None

    def _run_recrawl_with_whitebox(task_id, job_ids, profile_summary, source_run_id, completed_job_ids=None, profile_facts=None, execution_config=None):
        job_keys = [f'job:{value}' for value in sorted({str(value) for value in job_ids})]
        try:
            run = ctx.store.get_whitebox_run('recrawl', str(task_id))
            attempt = 1
            if run:
                attempts = [int(item.get('attempt_no') or 1) for item in ctx.store.list_whitebox_units(run['id'])]
                attempt = max(attempts or [1])
                existing_units = ctx.store.list_whitebox_units(run['id'])
                if completed_job_ids is not None and any(str(unit.get('status') or 'planned') != 'planned' for unit in existing_units):
                    attempt += 1
            completed_ids = {str(value) for value in (completed_job_ids or set())}
            active_keys = [key for key in job_keys if key.split(':', 1)[1] not in completed_ids]
            for key in active_keys:
                _recrawl_whitebox_record(task_id, 'unit_started', key, {'source_run_id': source_run_id}, attempt=attempt, required=False)
            ctx.run_recrawl_task(task_id, job_ids, profile_summary, source_run_id, completed_job_ids, profile_facts, execution_config)
            with ctx.lock:
                task = dict(ctx.tasks.get(task_id) or {})
            status = str(task.get('status') or '')
            updates = (task.get('result') or {}).get('updates') or {}
            completed_after = set(completed_ids)
            try:
                # The recrawl runner persists each successful AI batch in its
                # checkpoint before a later batch can pause.  The in-memory
                # ``updates`` map is intentionally only published at the end,
                # so use that durable checkpoint to preserve completed jobs.
                completed_after.update(
                    str(value) for value in
                    (ctx.store.load_checkpoint(task_id, 'recrawl_ai') or [])
                )
            except Exception as checkpoint_exc:
                from webui.logging_setup import get_logger
                get_logger(__name__).warning(
                    "recrawl checkpoint lookup failed: %s",
                    type(checkpoint_exc).__name__,
                )
            if status in {'cancelled', 'interrupted'}:
                _recrawl_whitebox_record(task_id, 'task_interrupted', None, {'stop_reason': 'cancelled'}, attempt=attempt, severity='warning', required=True)
                return _recrawl_whitebox_finish(task_id, lifecycle_end='cancelled')
            # The in-memory task is cleaned up after a successful runner and
            # may expose ``done`` even though the durable screening run is
            # ``succeeded``.  Both are successful lifecycle outcomes here.
            if status in {'succeeded', 'done', 'completed'}:
                for key in active_keys:
                    jid = key.split(':', 1)[1]
                    update = updates.get(jid) or {}
                    completed = bool(update) or jid in completed_after
                    _recrawl_whitebox_record(task_id, 'scope_completed', key, {'scope_complete': True, 'source_exhausted': None, 'stop_reason': 'target_reached', 'returned_total_count': int(completed), 'unit_unique_count': int(completed)}, attempt=attempt)
                return _recrawl_whitebox_finish(task_id)
            for key in active_keys:
                jid = key.split(':', 1)[1]
                update = updates.get(jid) or {}
                # A paused run may already have durably completed some jobs.
                # Preserve those facts and mark only the unfinished jobs as
                # incomplete so a later resume can recover the whole plan.
                if update or jid in completed_after:
                    _recrawl_whitebox_record(task_id, 'scope_completed', key, {'scope_complete': True, 'source_exhausted': None, 'stop_reason': 'target_reached', 'returned_total_count': 1, 'unit_unique_count': 1}, attempt=attempt)
                else:
                    _recrawl_whitebox_record(task_id, 'unit_incomplete' if status == 'paused' else 'unit_failed', key, {'error_code': 'recrawl_incomplete' if status == 'paused' else 'recrawl_failed', 'error_reason': '重抓未完成'}, attempt=attempt, severity='warning' if status == 'paused' else 'error')
            if status != 'paused':
                return _recrawl_whitebox_finish(task_id, lifecycle_end='failed')
        except Exception as exc:
            try:
                for key in job_keys:
                    _recrawl_whitebox_record(task_id, 'unit_failed', key, {'error_code': 'whitebox_incomplete', 'error_reason': type(exc).__name__}, attempt=attempt, severity='error')
                _recrawl_whitebox_finish(task_id, lifecycle_end='failed')
            except Exception as marker_exc:
                from webui.logging_setup import get_logger
                get_logger(__name__).warning(
                    "recrawl whitebox finalization failed: %s",
                    type(marker_exc).__name__,
                )
            raise

    @app.route('/api/job-detail', methods=['POST'])
    def job_detail():
        """T417: 按需抓取单个岗位的 JD 正文。
        source_run_id + platform_job_id 为权威；从 source run 继承冻结平台。
        """
        from webui.pipeline_exec import ensure_chrome_ready
        raw = request.get_json(silent=True) or {}
        job_id = str(raw.get('job_id') or '').strip()
        platform_job_id = str(raw.get('platform_job_id') or job_id).strip()
        source_run_id = str(raw.get('source_run_id') or '').strip() or None
        raw_source_url = str(raw.get('source_url') or raw.get('job_link') or '')
        if not job_id or not raw_source_url.strip():
            return (jsonify({'ok': False, 'error': '缺少 job_id 或 source_url'}), 400)
        if ctx.has_active_pipeline_task():
            return (jsonify({'ok': False, 'error': 'browser_busy', 'message': '当前已有任务在运行，请等待任务完成或结束后再抓取岗位详情'}), 409)
        parent = inherit_parent_frozen_identity(ctx.store, source_run_id, ctx.operational_errors)
        frozen_platform = parent['platform']
        frozen_browser_account = parent['browser_account']
        frozen_cdp_port = parent['cdp_port']
        frozen_profile_key = parent['profile_key']
        parent_run = parent['parent_run']
        if not source_run_id and _ZHILIAN_HOST_TOKEN in raw_source_url.lower():
            return (jsonify({'ok': False, 'error': 'run_identity_conflict', 'error_code': 'run_identity_conflict', 'message': '智联单 JD 必须携带 source_run_id，不能按 URL 猜测来源'}), 409)
        source_url = normalize_job_link_for_platform(raw_source_url, platform=frozen_platform)
        if not source_url:
            return (jsonify({'ok': False, 'error': '缺少合法 source_url'}), 400)
        if frozen_platform == 'zhilian' and _ZHILIAN_HOST_TOKEN not in source_url.lower():
            return (jsonify({'ok': False, 'error': 'platform_url_mismatch', 'message': '智联岗位 URL 必须包含 zhaopin.com'}), 422)
        if frozen_platform == 'zhilian' and (not (frozen_browser_account and frozen_cdp_port and frozen_profile_key)):
            return (jsonify({'ok': False, 'error': 'run_identity_conflict', 'error_code': 'run_identity_conflict', 'message': '智联来源 run 缺少冻结浏览器身份'}), 409)
        if source_run_id and parent_run is not None:
            ctx.activate_run_browser(parent_run)
        chrome_ok, chrome_err = ensure_chrome_ready(frozen_cdp_port if frozen_platform == 'zhilian' else None, minimize_after_launch=True)
        if not chrome_ok:
            return (jsonify({'ok': False, 'error': f'调试浏览器未能就绪：{chrome_err}'}), 503)
        source = ctx.make_cdp_source(platform=frozen_platform, browser_account=frozen_browser_account, cdp_port=frozen_cdp_port, profile_key=frozen_profile_key, run_id=source_run_id or '')
        if source is None:
            return (jsonify({'ok': False, 'error': '抓取源不可用'}), 500)
        job = {'job_id': platform_job_id, 'source_url': source_url, 'job_link': source_url}
        detail_path = str(Path(app.config['RESULT_DIR']) / f'job_detail_{platform_job_id}.json')
        with ctx.job_detail_lock:
            outcome = source.fetch_detail(job, detail_output_path=detail_path)
        if not outcome.ok:
            return (jsonify({'ok': False, 'error': f'详情抓取失败（{outcome.failed_code}）'}), 502)
        jd = str((outcome.detail or {}).get('jd', '')).strip()
        if not jd:
            return (jsonify({'ok': False, 'error': '详情页未提取到 JD 正文，岗位可能已下架'}), 502)
        return jsonify({'ok': True, 'jd': jd, 'platform': frozen_platform, 'platform_job_id': platform_job_id})

    @app.route('/api/pipeline/jobs/interest', methods=['POST'])
    def pipeline_mark_interest():
        """标记 pipeline 结果岗位为感兴趣：权威身份入库 + profile_jobs(interested)。
        复用筛选工作台的持久感兴趣区——标记后可在工作台"感兴趣"区看到
        （list_screening_interested 不按 run_id 过滤）。响应 job_id 是内部 ID。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get('profile_id')
        job = raw.get('job') or {}
        if not profile_id:
            raise ValueError(_MSG_PROFILE_ID_REQUIRED)
        try:
            ctx.store.get_profile(profile_id)
        except KeyError:
            return (jsonify({'error_code': 'not_found', 'user_message': _MSG_PROFILE_NOT_FOUND}), 404)
        try:
            resolved = _resolve_pipeline_job_identity(job)
        except JobIdentityError as exc:
            return _pipeline_identity_error_response(exc)
        ctx.store.mark_screening_interest(profile_id, resolved.job_id, run_id=None)
        return jsonify({'interest_state': 'interested', 'job_id': resolved.job_id})

    @app.route('/api/pipeline/jobs/reject', methods=['POST'])
    def pipeline_mark_reject():
        """标记 pipeline 结果岗位为不感兴趣：权威身份入库 + profile_jobs(deleted)。
        标记后进入筛选工作台垃圾桶区。响应 job_id 是内部 ID。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get('profile_id')
        job = raw.get('job') or {}
        if not profile_id:
            raise ValueError(_MSG_PROFILE_ID_REQUIRED)
        try:
            ctx.store.get_profile(profile_id)
        except KeyError:
            return (jsonify({'error_code': 'not_found', 'user_message': _MSG_PROFILE_NOT_FOUND}), 404)
        try:
            resolved = _resolve_pipeline_job_identity(job)
        except JobIdentityError as exc:
            return _pipeline_identity_error_response(exc)
        ctx.store.mark_screening_reject(profile_id, resolved.job_id, run_id=None)
        return jsonify({'reject_state': 'rejected', 'job_id': resolved.job_id})

    @app.route('/api/pipeline/jobs/reject/cancel', methods=['POST'])
    def pipeline_cancel_reject():
        """撤销 pipeline 结果岗位的不感兴趣标记：profile_jobs.status 回退。"""
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get('profile_id')
        job = raw.get('job') or {}
        if not profile_id or not isinstance(job, dict):
            return (jsonify({'error': 'missing profile_id or job'}), 400)
        try:
            ctx.store.get_profile(profile_id)
        except KeyError:
            return (jsonify({'error_code': 'not_found', 'user_message': _MSG_PROFILE_NOT_FOUND}), 404)
        try:
            resolved = _resolve_pipeline_job_identity(job)
        except JobIdentityError as exc:
            return _pipeline_identity_error_response(exc)
        try:
            ctx.store.cancel_screening_reject(profile_id, resolved.job_id)
        except sqlite3.Error as exc:
            return (jsonify({'error': f'撤销不感兴趣失败: {exc}'}), 500)
        return jsonify({'reject_state': 'cancelled', 'job_id': resolved.job_id})

    @app.route('/api/pipeline/jobs/interest/cancel', methods=['POST'])
    def pipeline_cancel_interest():
        """撤销 pipeline 结果岗位的感兴趣标记：profile_jobs.status 回退。
        payload 结构与 /api/pipeline/jobs/interest 一致（profile_id + job）；
        岗位必须能通过权威三元组或内部 ID 解析。幂等——即便当前不是
        interested 也不报错，使前端"感兴趣"按钮可再次点击取消。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get('profile_id')
        job = raw.get('job') or {}
        if not profile_id or not isinstance(job, dict):
            return (jsonify({'error': 'missing profile_id or job'}), 400)
        try:
            ctx.store.get_profile(profile_id)
        except KeyError:
            return (jsonify({'error_code': 'not_found', 'user_message': _MSG_PROFILE_NOT_FOUND}), 404)
        try:
            resolved = _resolve_pipeline_job_identity(job)
        except JobIdentityError as exc:
            return _pipeline_identity_error_response(exc)
        try:
            ctx.store.cancel_screening_interest(profile_id, resolved.job_id)
        except sqlite3.Error as exc:
            return (jsonify({'error': f'撤销感兴趣失败: {exc}'}), 500)
        return jsonify({'interest_state': 'cancelled', 'job_id': resolved.job_id})

    @app.route('/api/pipeline/jobs/<job_id>/jd', methods=['POST'])
    def pipeline_job_refetch_jd(job_id):
        """为单个岗位补抓 JD 并回写数据库中对应 job 项。
        用于 JD 抓取失败/缺失的岗位补抓；不重跑 AI、不跨 tab。与
        /api/job-detail 共用 ctx.job_detail_lock 串行化，避免并发争抢 CDP。
        """
        raw = request.get_json(silent=True) or {}
        source_run_id = str(raw.get('source_run_id') or '').strip()
        if not source_run_id:
            return (jsonify({'ok': False, 'error': 'missing_source_run_id', 'message': '必须指定目标结果轮'}), 409)
        if source_run_id:
            if ctx.store.get_pending_result(source_run_id, job_id) is None:
                return (jsonify({'ok': False, 'error': 'not_pending', 'message': '只能补抓当前待确认岗位'}), 409)
            with ctx.lock:
                for existing_id, task in ctx.tasks.items():
                    if task.get('kind') == 'recrawl' and task.get('source_run_id') == source_run_id and (task.get('status') in ('queued', 'running')):
                        return (jsonify({'ok': False, 'error': 'already_running', 'existing_task_id': existing_id}), 409)
            task_id = f'recrawl-{uuid.uuid4().hex[:12]}'
            parent_identity = None
            parent_run = None
            try:
                parent_identity = ctx.store.get_run_checkpoint_identity(source_run_id)
                parent_run = ctx.store.get_screening_run(source_run_id)
            except ctx.operational_errors:
                pass
            parent_platform = (parent_identity or {}).get('platform') or 'boss'
            parent_task_input_digest = (parent_identity or {}).get('task_input_digest')
            parent_params = (parent_run or {}).get('execution_params') or {}
            parent_browser_account = str(parent_params.get('browser_account') or '') or None
            parent_cdp_port = parent_params.get('cdp_port')
            parent_profile_key = parent_params.get('profile_key')
            gp_task_id = str(parent_params.get('scrape_task_id') or '')
            if (not parent_cdp_port or not parent_profile_key) and gp_task_id:
                try:
                    grandparent = ctx.store.get_screening_run(gp_task_id)
                    gp_params = (grandparent or {}).get('execution_params') or {}
                    parent_cdp_port = parent_cdp_port or gp_params.get('cdp_port')
                    parent_profile_key = parent_profile_key or gp_params.get('profile_key')
                except ctx.operational_errors:
                    pass
            ctx.register_pipeline_task(task_id, 'recrawl')
            with ctx.lock:
                ctx.tasks[task_id]['source_run_id'] = source_run_id
                ctx.tasks[task_id]['platform'] = parent_platform
                ctx.tasks[task_id]['cdp_port'] = parent_cdp_port
                ctx.tasks[task_id]['profile_key'] = parent_profile_key
                if parent_browser_account:
                    ctx.tasks[task_id]['browser_account'] = parent_browser_account
                elif parent_platform == 'boss':
                    from webui.pipeline_exec import account_for_role
                    ctx.tasks[task_id]['browser_account'] = account_for_role('R2', app.config['BROWSER_ACCOUNTS_PATH'], fallback=ctx.account_for_run())
                else:
                    ctx.tasks[task_id]['browser_account'] = ctx.account_for_run()
                ctx.tasks[task_id]['task_input_digest'] = parent_task_input_digest
            profile_summary = str(raw.get('profile_summary') or '')
            profile_facts = raw.get('profile_facts') or None
            ctx.store.create_screening_run(task_id, source_count=1, execution_params={'source_run_id': source_run_id, 'job_ids': [str(job_id)], 'profile_summary': profile_summary, 'profile_facts': profile_facts, 'single_retry': True, 'browser_account': ctx.tasks[task_id]['browser_account'], 'active_account_at_freeze': ctx.account_for_run(), 'platform': parent_platform, 'cdp_port': parent_cdp_port, 'profile_key': parent_profile_key, 'task_input_digest': parent_task_input_digest}, backend_version=ctx.backend_version)
            ctx.store.save_filter_snapshot(task_id, platform=parent_platform, task_input_digest=parent_task_input_digest)
            ctx.store.update_screening_run(task_id, status='running', current_stage='recrawl_fetch_jd')
            try:
                _recrawl_whitebox_begin(task_id, [str(job_id)], parent_id=source_run_id)
            except Exception as exc:
                ctx.store.update_screening_run(task_id, status='failed', error_code='whitebox_incomplete', error_reason='重抓任务证据白箱初始化失败')
                with ctx.lock:
                    task = ctx.tasks.get(task_id)
                    if task is not None:
                        task['status'] = 'failed'
                        task['error'] = '重抓任务证据白箱初始化失败'
                return (jsonify({'ok': False, 'error': 'whitebox_incomplete'}), 503)
            ctx.activate_run_browser(parent_run)
            try:
                ctx.executor.submit(_run_recrawl_with_whitebox, task_id, [str(job_id)], profile_summary, source_run_id, None, profile_facts)
            except RuntimeError as exc:
                reason = '后台任务提交失败，重抓任务已结束'
                ctx.store.update_screening_run(task_id, status='failed', error_code='internal_error', error_reason=reason)
                ctx.store.append_task_event(task_id, 'job_fail', {'stage': 'recrawl_submit', 'job_id': str(job_id), 'failed_code': 'internal_error', 'reason': reason})
                try:
                    _recrawl_whitebox_record(task_id, 'submission_failed', 'job:' + str(job_id), {'error_code': 'submit_failed', 'error_reason': reason}, severity='error')
                    _recrawl_whitebox_finish(task_id, lifecycle_end='failed')
                except Exception as marker_exc:
                    from webui.logging_setup import get_logger
                    get_logger(__name__).warning(
                        "single retry whitebox finalization failed: %s",
                        type(marker_exc).__name__,
                    )
                with ctx.lock:
                    task = ctx.tasks.get(task_id)
                    if task is not None:
                        task['status'] = 'failed'
                        task['error'] = reason
                return (jsonify({'ok': False, 'error': 'single_retry_submit_failed'}), 500)
            return (jsonify({'ok': True, 'task_id': task_id, 'source_run_id': source_run_id, 'single_retry': True}), 202)

    @app.route('/api/pipeline/recrawl', methods=['POST'])
    def pipeline_recrawl():
        """对待确认（uncertain）岗位批量重抓：缺 JD 的补抓 JD，有 JD 的用画像重跑 AI 精筛。
        切片8（FR-022/FR-037）：防并发——同 source_run_id 已有 running 重抓任务时拒绝。
        切片8（FR-023）：job_ids 缺省时从 screening_pending_results 自动读取（全部重抓只处理待确认）。
        复用 fetch_job_details（CDP 通道，内部按 detail_batch_size 分批 + 冷却）与
        match_jds（按 match_batch_size 分批）。进度走与 AI 筛选相同的轮询机制
        （前端 pollTask + TaskProgress）。判定与 JD 原地回写 screening_results，
        返回 updates 映射供前端就地合并，保留当前结果 tab。
        """
        raw = request.get_json(silent=True) or {}
        job_ids = raw.get('job_ids')
        profile_summary = str(raw.get('profile_summary') or '')
        profile_facts = raw.get('profile_facts') or None
        source_run_id = str(raw.get('source_run_id') or '').strip()
        if not source_run_id:
            return (jsonify({'ok': False, 'error': 'missing_source_run_id'}), 409)
        pending_rows = ctx.store.list_pending_results(source_run_id)
        pending_ids = {str(item.get('job_id') or '') for item in pending_rows}
        snapshot_ids = set()
        try:
            _snapshot = ctx.store.load_latest_pipeline_result(source_run_id)
            for _job in ((_snapshot or {}).get('result') or {}).get('jobs') or []:
                if not isinstance(_job, dict):
                    continue
                if str(_job.get('verdict') or '') in ('match', 'not_match', 'mismatch'):
                    continue
                _sid = ctx.recrawl_job_key(_job)
                if _sid:
                    snapshot_ids.add(_sid)
        except ctx.operational_errors:
            snapshot_ids = set()
        recrawlable_ids = pending_ids | snapshot_ids
        if not job_ids or job_ids == 'auto':
            job_ids = sorted(recrawlable_ids)
            if not job_ids:
                return (jsonify({'ok': False, 'error': 'no_recrawlable_targets', 'message': '0 个可重抓岗位'}), 400)
        if not isinstance(job_ids, list) or not job_ids:
            return (jsonify({'ok': False, 'error': '缺少 job_ids'}), 400)
        requested_ids = {str(job_id) for job_id in job_ids}
        non_pending = sorted(requested_ids - recrawlable_ids)
        if non_pending:
            return (jsonify({'ok': False, 'error': 'non_pending_job_ids', 'message': '只能重抓当前结果中未完成判定的岗位', 'job_ids': non_pending}), 409)
        if snapshot_ids and requested_ids and (not requested_ids & snapshot_ids):
            return (jsonify({'ok': False, 'error': 'no_recrawlable_targets', 'message': '0 个可重抓岗位', 'job_ids': sorted(requested_ids)}), 400)
        job_ids = sorted(requested_ids)
        parent_identity = None
        parent_run = None
        try:
            parent_identity = ctx.store.get_run_checkpoint_identity(source_run_id)
            parent_run = ctx.store.get_screening_run(source_run_id)
        except ctx.operational_errors:
            pass
        parent_platform = (parent_identity or {}).get('platform') or 'boss'
        parent_task_input_digest = (parent_identity or {}).get('task_input_digest')
        parent_params = (parent_run or {}).get('execution_params') or {}
        parent_browser_account = str(parent_params.get('browser_account') or '') or None
        parent_cdp_port = parent_params.get('cdp_port')
        parent_profile_key = parent_params.get('profile_key')
        gp_task_id = str(parent_params.get('scrape_task_id') or '')
        if (not parent_cdp_port or not parent_profile_key) and gp_task_id:
            try:
                grandparent = ctx.store.get_screening_run(gp_task_id)
                gp_params = (grandparent or {}).get('execution_params') or {}
                parent_cdp_port = parent_cdp_port or gp_params.get('cdp_port')
                parent_profile_key = parent_profile_key or gp_params.get('profile_key')
            except ctx.operational_errors:
                pass
        task_id = f'recrawl-{uuid.uuid4().hex[:12]}'
        claimed_task, existing_task_id = ctx.claim_recrawl_start(task_id, source_run_id)
        if claimed_task is None:
            return (jsonify({'ok': False, 'error': '已有重抓任务在运行，请等待完成或取消后再试', 'existing_task_id': existing_task_id}), 409)
        if parent_browser_account:
            claimed_task['browser_account'] = parent_browser_account
        elif parent_platform == 'boss':
            from webui.pipeline_exec import account_for_role
            claimed_task['browser_account'] = account_for_role('R2', app.config['BROWSER_ACCOUNTS_PATH'], fallback=ctx.account_for_run())
        else:
            claimed_task['browser_account'] = ctx.account_for_run()
        claimed_task['platform'] = parent_platform
        claimed_task['cdp_port'] = parent_cdp_port
        claimed_task['profile_key'] = parent_profile_key
        claimed_task['task_input_digest'] = parent_task_input_digest
        ctx.activate_run_browser(parent_run)
        try:
            ctx.store.create_screening_run(task_id, source_count=len(job_ids), execution_params={'source_run_id': source_run_id, 'job_ids': [str(x) for x in job_ids], 'profile_summary': profile_summary, 'profile_facts': profile_facts, 'browser_account': claimed_task['browser_account'], 'active_account_at_freeze': ctx.account_for_run(), 'platform': parent_platform, 'cdp_port': parent_cdp_port, 'profile_key': parent_profile_key, 'task_input_digest': parent_task_input_digest}, backend_version=ctx.backend_version)
            ctx.store.save_filter_snapshot(task_id, platform=parent_platform, task_input_digest=parent_task_input_digest)
            ctx.store.update_screening_run(task_id, status='running', current_stage='recrawl_fetch_jd')
        except ctx.operational_errors as exc:
            ctx.release_pipeline_claim(task_id, claimed_task)
            return (jsonify({'ok': False, 'error': f'重抓任务持久化失败：{type(exc).__name__}'}), 500)
        try:
            _recrawl_whitebox_begin(task_id, job_ids, parent_id=source_run_id)
        except Exception:
            ctx.store.update_screening_run(task_id, status='failed', error_code='whitebox_incomplete', error_reason='重抓任务证据白箱初始化失败')
            ctx.release_pipeline_claim(task_id, claimed_task)
            return (jsonify({'ok': False, 'error': 'whitebox_incomplete'}), 503)
        try:
            ctx.executor.submit(_run_recrawl_with_whitebox, task_id, [str(x) for x in job_ids], profile_summary, source_run_id, None, profile_facts)
        except RuntimeError as exc:
            reason = '后台任务提交失败，重抓任务已结束'
            try:
                ctx.store.update_screening_run(task_id, status='failed', error_code='submit_failed', error_reason=reason)
                ctx.store.append_task_event(task_id, 'submission_failed', {'stage': 'recrawl_submit', 'error_code': 'submit_failed', 'error_reason': reason})
                for _job_key in (f'job:{value}' for value in job_ids):
                    _recrawl_whitebox_record(task_id, 'submission_failed', _job_key, {'error_code': 'submit_failed', 'error_reason': reason}, severity='error')
                _recrawl_whitebox_finish(task_id, lifecycle_end='failed')
            finally:
                ctx.release_pipeline_claim(task_id, claimed_task)
            return (jsonify({'ok': False, 'error': 'recrawl_submit_failed'}), 500)
        return (jsonify({'ok': True, 'task_id': task_id, 'source_run_id': source_run_id}), 202)

    @app.route('/api/recrawl/continue/<task_id>', methods=['POST'])
    def continue_recrawl(task_id, _block_checked=False, account_switch_note=None):
        """Resume a paused recrawl in place using its persisted checkpoint.
        SPEC011 T015: 实验租约持有时拒绝继续（FR-035）。
        """
        ok, err_resp = ctx.check_tuning_lease_conflict()
        if not ok:
            return err_resp
        run = ctx.store.get_screening_run(task_id)
        if run is None:
            return (jsonify({'ok': False, 'error': 'run_not_found'}), 404)
        stage = str(run.get('current_stage') or '')
        if run.get('status') != 'paused' or not stage.startswith('recrawl_'):
            return (jsonify({'ok': False, 'error': 'not_paused_recrawl', 'status': run.get('status'), 'stage': stage}), 409)
        ctx.activate_run_browser(run)
        if not _block_checked:
            passed, code, reason = ctx.check_resume_block(run)
            if not passed:
                return (jsonify({'ok': False, 'error': 'block_not_resolved', 'error_code': code, 'error_reason': reason, 'status': 'paused'}), 409)
        with ctx.lock:
            existing = ctx.tasks.get(task_id)
            if existing is not None and existing.get('status') in ('queued', 'running'):
                return (jsonify({'ok': False, 'error': 'already_running'}), 409)
        params = run.get('execution_params') or {}
        from webui.execution_config import ExecutionConfigSnapshot
        recrawl_config = None
        try:
            if params.get('execution_config'):
                recrawl_config = ExecutionConfigSnapshot.from_dict(params['execution_config'])
        except (KeyError, TypeError, ValueError):
            recrawl_config = None
        source_run_id = str(params.get('source_run_id') or '')
        job_ids = [str(job_id) for job_id in params.get('job_ids') or []]
        profile_summary = str(params.get('profile_summary') or '')
        profile_facts = params.get('profile_facts') or None
        checkpoint_stage = 'recrawl_ai' if stage == 'recrawl_ai' else 'recrawl_jd'
        completed_job_ids = ctx.store.load_checkpoint(task_id, checkpoint_stage)
        if not job_ids:
            return (jsonify({'ok': False, 'error': 'missing_job_ids'}), 409)
        claimed_task, previous_task = ctx.claim_pipeline_task_id(task_id, 'recrawl', started_at=_iso_epoch_ms(run.get('started_at')))
        if claimed_task is None:
            return (jsonify({'ok': False, 'error': 'already_running'}), 409)
        if account_switch_note:
            append_account_switch_log_line(claimed_task, from_account=account_switch_note[0], to_account=account_switch_note[1])
        claimed_task['source_run_id'] = source_run_id
        resume_params = dict(run.get('execution_params') or {})
        claimed_task['browser_account'] = ensure_frozen_browser_account(ctx.store, task_id, run, platform=str(resume_params.get('platform') or 'boss'), fallback_account=ctx.account_for_run(run), accounts_path=app.config['BROWSER_ACCOUNTS_PATH'], role='R2')
        claimed_task['platform'] = resume_params.get('platform') or 'boss'
        claimed_task['cdp_port'] = resume_params.get('cdp_port')
        claimed_task['profile_key'] = resume_params.get('profile_key')
        claimed_task['task_input_digest'] = resume_params.get('task_input_digest')
        try:
            if not ctx.write_run(task_id, status='running'):
                ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
                return (jsonify({'ok': False, 'error': 'user_finished', 'message': '任务已结束保存，不能继续', 'status': 'completed_with_pending'}), 409)
            ctx.store.append_task_event(task_id, 'resume', {'stage': stage, 'completed': len(completed_job_ids)})
            ctx.executor.submit(_run_recrawl_with_whitebox, task_id, job_ids, profile_summary, source_run_id, completed_job_ids, profile_facts, recrawl_config)
        except ctx.operational_errors as exc:
            try:
                current = ctx.store.get_screening_run(task_id)
                if current is not None and current.get('status') == 'running':
                    ctx.write_run(task_id, status='failed', error_code='submit_failed', error_reason='继续任务提交失败，任务已结束')
                ctx.store.append_task_event(task_id, 'submission_failed', {'stage': 'resume_submit', 'error_code': 'submit_failed', 'error_reason': '继续任务提交失败，任务已结束'})
                for _job_key in (f'job:{value}' for value in job_ids):
                    _recrawl_whitebox_record(task_id, 'submission_failed', _job_key, {'error_code': 'submit_failed', 'error_reason': '继续任务提交失败，任务已结束'}, severity='error')
                _recrawl_whitebox_finish(task_id, lifecycle_end='failed')
            finally:
                ctx.release_pipeline_claim(task_id, claimed_task, previous_task)
            return (jsonify({'ok': False, 'error': 'resume_submit_failed'}), 500)
        return jsonify({'ok': True, 'task_id': task_id, 'source_run_id': source_run_id, 'completed_job_ids': sorted(completed_job_ids), 'stage': stage})
    ctx.continue_recrawl = continue_recrawl
