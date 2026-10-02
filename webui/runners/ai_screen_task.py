"""AI 筛选后台任务 runner（021 B6 外迁自 webui/app.py）。
编排 StageA 字段粗筛 → 批量抓 JD → StageB JD 精筛三段流程：冻结运行时
读取、续跑断点装载、跨平台去重、AI 凭据校验、终态判定与结果轮落库、
异常救援（systemic 暂停 / 结果轮保存失败降级）。三段执行体分别在
ai_screen_rough / ai_screen_jd / ai_screen_fine 段模块（单向 import，
段模块间不互相 import）；共享运行态经 ctx 取用；ai_service /
threading 模块级直连（031 B9 门面拆除）；延迟 import 保持原位原语义。
"""
from __future__ import annotations
from webui import ai as ai_service
import threading
import time
from webui.diagnostics import record_failure
from webui.error_registry import is_recoverable_systemic_block
from webui.task_runner_support import _carry_batch_signal
from webui.task_runners import _split_resume_verdicts
from webui.runners.ai_screen_rough import run_rough_stage
from webui.runners.ai_screen_jd import run_jd_stage
from webui.runners.ai_screen_fine import run_fine_stage
from webui.ai_screen_failure import mark_flow_failure as _mark_flow_failure
from webui.ai_screen_failure import persist_ai_worker_failure as _persist_ai_worker_failure
from webui.ai_domain_context import DomainContextError, ScreeningPolicyIncompatibleError, load_resume_progress as _load_resume_progress, prepare_domain_run as _prepare_domain_run
from webui.flow_task_state import FlowStateClosureError

def run_ai_screen_task(ctx, task_id, screening_fields, profile_summary, scrape_task_id, resume_from_run_id='', profile_facts=None, execution_config=None, cross_platform_dedupe=True):
    """AI 筛选任务：StageA 字段粗筛 → 批量抓 JD → StageB JD 精筛。
    读取最近一次原始抓取结果，两段式 AI 筛选后把带 verdict 的最终结果
    持久化到数据库（供结果页恢复）。
    全程进度落库（screening_runs）+ 中间产物落盘（JD 断点文件 /
    screening_results 判定）：进程重启或失败后，同一抓取任务再次发起
    筛选且条件一致时自动接着上次进度（``resume_from_run_id``）。
    ``execution_config``: 可选 — 续跑（暂停后继续）时由调用方传入本轮
    刷新后的配置快照；未提供时从父抓取 run 读取冻结配置（新建任务路径）。
    """
    from webui.cross_platform_dedupe import apply_to_screening_input
    with ctx.lock:
        task = ctx.tasks.get(task_id)
        if task is None:
            task = {'kind': 'ai_screen', 'status': 'queued', 'progress': {}, 'logs': [], 'result': None, 'error': '', 'source_task_id': scrape_task_id, 'started_at': int(time.time() * 1000), 'finished_at': None, 'stop_event': threading.Event()}
            ctx.tasks[task_id] = task
        if task.get('status') == 'cancelled':
            ctx.release_worker_resume_claims(task)
            return
        if ctx.is_user_finished(task_id):
            ctx.release_worker_resume_claims(task)
            return
        task['status'] = 'running'
        stop_event = task.get('stop_event')
        if resume_from_run_id and (not task.get('resumed_from')):
            task['resumed_from'] = resume_from_run_id
    if resume_from_run_id and (not profile_facts):
        try:
            _prev_run = ctx.store.get_screening_run(resume_from_run_id)
            _prev_params = (_prev_run or {}).get('execution_params') or {}
            if not profile_facts:
                profile_facts = _prev_params.get('profile_facts')
        except ctx.operational_errors as exc:
            from webui.logging_setup import get_logger
            get_logger(__name__).debug(
                "resume profile facts lookup failed: %s", type(exc).__name__
            )
    try:
        ctx.activate_task_browser(task_id)
    except Exception as exc:  # noqa: BLE001 - worker failures must be durable
        raw_code = str(
            getattr(exc, "error_code", "")
            or getattr(exc, "failed_code", "")
            or "internal_error"
        )
        code = raw_code if raw_code in {
            "source_cdp_unavailable", "browser_busy",
        } or is_recoverable_systemic_block(raw_code) else "internal_error"
        _persist_ai_worker_failure(
            ctx, task_id, scrape_task_id, code,
            "AI 筛选启动失败", platform=(
                (ctx.tasks.get(task_id) or {}).get("platform")
                if hasattr(ctx, "tasks") else None
            ),
            status="paused" if code != "internal_error" else "failed",
        )
        return
    from webui.store_helpers import _now
    from webui.whitebox import WhiteboxService
    _whitebox = WhiteboxService(ctx.store) if hasattr(ctx.store, 'create_whitebox_run') else None
    _whitebox_ref = None
    if _whitebox is not None:
        try:
            begin_evidence = _whitebox.resume if resume_from_run_id == task_id else _whitebox.begin
            _whitebox_ref = begin_evidence('screening', task_id, {
                'stages': ['ai_rough', 'jd_detail', 'ai_fine'],
                'units': [
                    {'unit_key': 'ai_rough', 'unit_kind': 'ai_stage', 'stage': 'ai_rough', 'required': True},
                    {'unit_key': 'jd_detail', 'unit_kind': 'ai_stage', 'stage': 'jd_detail', 'required': True},
                    {'unit_key': 'ai_fine', 'unit_kind': 'ai_stage', 'stage': 'ai_fine', 'required': True},
                ],
            }, parent_owner_id=scrape_task_id)
        except Exception:
            _whitebox = None
            _persist_ai_worker_failure(
                ctx, task_id, scrape_task_id, "whitebox_incomplete",
                "任务证据白箱初始化失败",
            )
            return

    def _whitebox_attempt(unit_key):
        """Start a fresh evidence attempt after a paused/failed unit.

        A resumed AI run reuses the screening owner id, so the whitebox run
        itself is intentionally reused.  Failed or interrupted unit facts
        must remain auditable, while a later successful retry must project to
        a new attempt instead of being blocked by the old terminal fact.
        """
        if _whitebox_ref is None or not unit_key:
            return 1
        try:
            units = _whitebox.store.list_whitebox_units(_whitebox_ref.id)
            matching = [
                unit for unit in units
                if str(unit.get('unit_key') or '') == str(unit_key)
            ]
            if not matching:
                return 1
            latest = max(matching, key=lambda unit: int(unit.get('attempt_no') or 1))
            attempt = int(latest.get('attempt_no') or 1)
            if str(latest.get('status') or 'planned') in {'failed', 'incomplete', 'skipped'}:
                return attempt + 1
            return attempt
        except Exception:
            return 1

    def _wb_record(event_type, stage, key, payload, *, required=True, severity='info'):
        if _whitebox_ref is None:
            return
        record_key = key
        record_stage = stage
        if event_type in {'unit_failed', 'submission_failed', 'ai_request_failed'} and record_key not in {'ai_rough', 'jd_detail', 'ai_fine'}:
            record_key = _current_whitebox_unit
            record_stage = record_key
        _whitebox.record(_whitebox_ref, {'idempotency_key': f'{event_type}:{task_id}:{record_key or record_stage}:{_whitebox_attempt(record_key)}', 'event_type': event_type, 'occurred_at': _now(), 'stage': record_stage, 'unit_kind': 'ai_stage' if record_key else None, 'unit_key': record_key, 'attempt_no': _whitebox_attempt(record_key), 'required_evidence': required, 'severity': severity, 'payload': payload or {}})

    def _wb_finish(lifecycle_end=None):
        return _whitebox.finalize(_whitebox_ref, lifecycle_end=lifecycle_end) if _whitebox_ref is not None else None
    _wb_record('task_started', 'ai_screen', None, {'planned_units': 3}, required=False)
    last_event_stage = None
    _current_whitebox_unit = 'ai_rough'

    def emit(**kw):
        nonlocal last_event_stage, _current_whitebox_unit
        stage = str(kw.get('stage', ''))
        _current_whitebox_unit = {
            'screen_a': 'ai_rough',
            'screen_a_done': 'ai_rough',
            'ensure_chrome': 'jd_detail',
            'fetch_jd': 'jd_detail',
            'jd_detail': 'jd_detail',
            'screen_b': 'ai_fine',
            'ai_fine': 'ai_fine',
            'done': 'ai_fine',
        }.get(stage, _current_whitebox_unit)
        current = int(kw.get('current') or 0)
        total = int(kw.get('total') or 0)
        kw['overall_percent'] = ctx.screen_overall_percent(stage, current, total)
        if not kw.get('message'):
            kw['message'] = ctx.screen_stage_messages.get(stage, '')
        event_stage = ctx.event_stage_names.get(stage)
        stage_events = []
        if stage == 'done' and last_event_stage:
            stage_events.append(('stage_complete', {'stage': last_event_stage, **{key: kw[key] for key in ('current', 'total', 'total_matched', 'total_mismatch', 'total_pending', 'total_dropped') if key in kw}}))
            last_event_stage = None
        elif event_stage and event_stage != last_event_stage:
            if last_event_stage:
                stage_events.append(('stage_complete', {'stage': last_event_stage}))
            stage_events.append(('stage_start', {'stage': event_stage}))
            last_event_stage = event_stage
        if stage_events:
            ctx.store.append_task_events(task_id, stage_events)
        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if task is None:
                return
            task['progress'] = _carry_batch_signal(task.get('progress'), kw, stage)
            msg = kw.get('message')
            if msg:
                task['logs'].append(msg)

    def _stop_requested():
        return stop_event is not None and stop_event.is_set()

    def _raw_stop_mode():
        """停在任务上的原始停止原因（pause/cancel/finish/terminate/空）。"""
        with ctx.lock:
            t = ctx.tasks.get(task_id)
        return str((t or {}).get('stop_mode') or '')

    def _stop_mode():
        with ctx.lock:
            t = ctx.tasks.get(task_id)
            if t is None or t.get('stop_event') is None or (not t['stop_event'].is_set()):
                return None
            return 'pause' if t.get('stop_mode') == 'pause' else 'cancel'

    def _mark_paused():
        """用户暂停：把原 run 写为 paused，不产生历史轮（017-US1）。"""
        _wb_record('unit_incomplete', 'ai_screen', 'ai_fine', {'stop_reason': 'user_paused'}, severity='warning')
        _wb_finish('operator_stop')
        ctx.write_run(task_id, status='paused', error_code='user_paused', error_reason='用户已暂停，结果已保留')
        run = ctx.store.get_screening_run(task_id) or {}
        stage = str(run.get('current_stage') or (task.get('progress') or {}).get('stage') or '')
        ctx.store.append_task_event(task_id, 'pause', {'stage': stage, 'code': 'user_paused', 'processed': int(run.get('processed_count') or 0), 'total': int(run.get('source_count') or 0)})
        with ctx.lock:
            t = ctx.tasks.get(task_id)
            if t is not None:
                t['status'] = 'paused'
                t['error'] = '任务已暂停，结果已保留'
                ctx.release_worker_resume_claims(t)

    def _handle_user_stop():
        if _raw_stop_mode() in ('finish', 'terminate'):
            # 结束保存/终止由对应接口负责定稿（接口自己写终态与部分快照）。
            # worker 只释放续跑占位，不写任何终态，避免与接口抢写。
            with ctx.lock:
                t = ctx.tasks.get(task_id)
            ctx.release_worker_resume_claims(t)
            return
        if _stop_mode() == 'pause':
            _mark_paused()
        else:
            _mark_cancelled()

    def _mark_cancelled():
        """用户取消：标 cancelled（不覆盖为 done/failed），落清理定时。
        017-US1：取消不再写历史快照；底层已抓岗位数据保留。
        """
        _wb_record('task_interrupted', 'ai_screen', None, {'stop_reason': 'user_cancelled'}, severity='warning')
        _wb_finish('cancelled')
        ctx.write_run(task_id, status='cancelled', error_reason=ctx.msg_user_stopped_screen)
        with ctx.lock:
            t = ctx.tasks.get(task_id)
            if t is not None:
                t['status'] = 'cancelled'
                t['error'] = ctx.msg_user_stopped_screen
                ctx.release_worker_resume_claims(t)
        ctx.schedule_pipeline_task_cleanup(task_id)
    finalized = False
    try:
        with ctx.lock:
            source_task = ctx.tasks.get(scrape_task_id)
            source_result = source_task.get('result') if isinstance(source_task, dict) else None
        if not isinstance(source_result, dict):
            # A completed scrape can outlive its in-memory task (cleanup or
            # process restart).  Recover the durable source through the same
            # production helper used by the HTTP AI route.
            source_snapshot = ctx.ensure_scrape_source(scrape_task_id)
            if isinstance(source_snapshot, dict):
                source_task = source_snapshot
                source_result = source_snapshot.get('result')
        if not isinstance(source_result, dict):
            raise RuntimeError('invalid_scrape_task')
        source_run = ctx.store.get_screening_run(scrape_task_id)
        # Recovery is only allowed to use a parent scrape as a completed
        # input when that scrape carries terminal whitebox evidence.  Legacy
        # rows may still contain jobs and ``ok=True`` without proving that
        # every planned source unit completed; preserve that uncertainty in
        # the child AI conclusion instead of upgrading it to success.
        try:
            _source_wb = ctx.store.get_whitebox_run('scrape', scrape_task_id)
            _source_evidence_ok = bool(
                _source_wb
                and _source_wb.get('evidence_complete')
                and _source_wb.get('conclusion') in {'succeeded', 'empty'}
                and _source_wb.get('lifecycle_status') == 'terminal'
            )
        except Exception:
            _source_evidence_ok = False
        if not _source_evidence_ok:
            _wb_record(
                'whitebox_incomplete', 'scrape', None,
                {'reason': 'parent_evidence_missing',
                 'parent_owner_id': scrape_task_id},
                severity='error',
            )
        source_params = (source_run.get('execution_params') if isinstance(source_run, dict) else None) or {}
        # Preserve the Flow/Track ownership on the process run when the
        # runner materializes its durable row.  The API creates the row before
        # submission, and this runner's INSERT OR REPLACE must not erase that
        # binding while it refreshes the frozen AI snapshot.
        frozen_flow_id = str(source_params.get('flow_id') or '').strip() or None
        frozen_track_id = str(source_params.get('track_id') or '').strip() or None
        frozen_platform = task.get('platform') or source_params.get('platform') or (source_run or {}).get('platform') or 'boss'
        frozen_cdp_port = task.get('cdp_port') or source_params.get('cdp_port')
        frozen_profile_key = task.get('profile_key') or source_params.get('profile_key')
        frozen_browser_account = task.get('browser_account') or source_params.get('browser_account')
        ai_task_input_digest = task.get('task_input_digest') or source_params.get('task_input_digest') or (source_run or {}).get('task_input_digest')
        domain_context = _prepare_domain_run(ctx, scrape_task_id=scrape_task_id, platform=frozen_platform, screening_fields=screening_fields, resume_from_run_id=resume_from_run_id, profile_id=task.get('profile_id'))
        from webui.execution_config import ExecutionConfigSnapshot, FrozenTaskScope
        try:
            defer_partial_status = False
            if execution_config is None:
                execution_config = ExecutionConfigSnapshot.from_dict(source_params['execution_config'])
            frozen_scope = FrozenTaskScope.from_dict(source_params['frozen_scope'])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError('frozen_snapshot_missing') from exc
        with ctx.lock:
            task['config_digest'] = execution_config.config_digest
            task['scope_digest'] = frozen_scope.scope_digest
        if source_result.get('hard_stop'):
            _hs_code = source_result.get('hard_stop_code') or 'source_blocked'
            _completed_combos = source_result.get('completed_combos') or []
            ctx.store.create_screening_run(task_id, frozen_filters=screening_fields, source_count=len(source_result.get('jobs') or []), profile_id=str(task.get('profile_id') or '') or None, execution_params={'scrape_task_id': scrape_task_id, 'profile_summary': profile_summary or '', 'profile_facts': profile_facts, 'browser_account': frozen_browser_account or ctx.account_for_run(), 'active_account_at_freeze': str(task.get('active_account_at_freeze') or '') or ctx.account_for_run(), 'execution_config': execution_config.to_dict(), 'frozen_scope': frozen_scope.to_dict(), 'platform': frozen_platform, 'flow_id': frozen_flow_id, 'track_id': frozen_track_id, 'cdp_port': frozen_cdp_port, 'profile_key': frozen_profile_key, 'task_input_digest': ai_task_input_digest, 'cross_platform_dedupe': cross_platform_dedupe, **(domain_context.metadata())}, backend_version=ctx.backend_version)
            ctx.store.save_filter_snapshot(task_id, platform=frozen_platform, task_input_digest=ai_task_input_digest)
            ctx.write_run(task_id, status='running', current_stage='scrape')
            ctx.write_run(task_id, status='paused', error_code=_hs_code, current_stage='scrape')
            ctx.store.save_checkpoint(task_id, 'scrape', _completed_combos)
            ctx.store.append_task_event(task_id, 'pause', {'stage': 'scrape', 'code': _hs_code, 'completed_combos': len(_completed_combos), 'total_combos': source_result.get('combinations') or 0})
            ctx.record_pause_failure(task_id, 'scrape', _hs_code, str(source_result.get('error') or '') or f'列表抓取被阻断（{_hs_code}）', processed=len(_completed_combos), total=int(source_result.get('combinations') or 0))
            with ctx.lock:
                t = ctx.tasks.get(task_id)
                if t is not None:
                    t['status'] = 'paused'
                    t['error'] = f"列表抓取被阻断（{_hs_code}）：已完成 {len(_completed_combos)}/{source_result.get('combinations') or 0} 个组合。处理完成后点「继续」"
            ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
            return
        if not source_result.get('ok') and (not source_result.get('jobs')):
            raise RuntimeError('invalid_scrape_task')
        raw_jobs = [dict(job) for job in source_result.get('jobs', []) if isinstance(job, dict)]
        for job in raw_jobs:
            job['job_id'] = str(job.get('platform_job_id') or job.get('job_id') or job.get('id') or '')
            job.setdefault('platform_job_id', job['job_id'])
        if not raw_jobs:
            raise RuntimeError('empty_scrape_result')
        if resume_from_run_id != task_id:
            ctx.store.create_screening_run(task_id, frozen_filters=screening_fields, source_count=len(raw_jobs), profile_id=str(task.get('profile_id') or '') or None, execution_params={'scrape_task_id': scrape_task_id, 'profile_summary': profile_summary, 'profile_facts': profile_facts, 'browser_account': frozen_browser_account or ctx.account_for_run(), 'active_account_at_freeze': str(task.get('active_account_at_freeze') or '') or ctx.account_for_run(), 'execution_config': execution_config.to_dict(), 'frozen_scope': frozen_scope.to_dict(), 'platform': frozen_platform, 'flow_id': frozen_flow_id, 'track_id': frozen_track_id, 'cdp_port': frozen_cdp_port, 'profile_key': frozen_profile_key, 'task_input_digest': ai_task_input_digest, 'cross_platform_dedupe': cross_platform_dedupe, **(domain_context.metadata())}, backend_version=ctx.backend_version)
            ctx.store.save_filter_snapshot(task_id, platform=frozen_platform, task_input_digest=ai_task_input_digest)
            ctx.write_run(task_id, status='running', current_stage='ai_rough')
        else:
            resumed_run = ctx.store.get_screening_run(task_id)
            if resumed_run is None or resumed_run.get('status') != 'running':
                raise RuntimeError('resume_run_not_claimed')
            _frozen_dedupe = (resumed_run.get('execution_params') or {}).get('cross_platform_dedupe')
            if _frozen_dedupe is not None:
                cross_platform_dedupe = bool(_frozen_dedupe)
        # 轨道必须指名这条 AI run：接管式续跑的新 run 行在这里才存在，
        # 绑定后轨道级暂停/恢复才有可操作对象。绑定失败不推翻已在跑的 worker，
        # 但必须留下可观测记录（轨道随后会被启动归一认领）。
        if frozen_flow_id:
            try:
                from webui.flow_task_coordinator import ensure_ai_run_bound_to_track

                ensure_ai_run_bound_to_track(ctx, task_id)
            except Exception as exc:  # noqa: BLE001 - worker keeps its durable row
                from webui.logging_setup import get_logger

                get_logger(__name__).warning(
                    "Flow AI run %s could not take its Track (%s)",
                    task_id, type(exc).__name__,
                )
        resume_verdicts, resume_jd = _load_resume_progress(ctx, task_id=task_id, resume_from_run_id=resume_from_run_id, platform=frozen_platform, scrape_task_id=scrape_task_id, screening_fields=screening_fields, profile_summary=profile_summary, profile_facts=profile_facts, selection=domain_context.selection)
        resume_fine_verdicts = _split_resume_verdicts(resume_verdicts)[0] if resume_from_run_id else {}
        if resume_verdicts or resume_jd:
            emit(stage='resume', message=f'接着上次进度：已有 {len(resume_verdicts)} 条判定、{len(resume_jd)} 条 JD，跳过重复工作')
        # Spec041：去重判定源按当前画像取轮，别的画像的历史轮不参与；老任务无画像时保持旧口径。
        _dedupe = apply_to_screening_input(
            ctx.store, raw_jobs, frozen_platform, profile_summary,
            profile_id=str(task.get('profile_id') or '') or None,
            enabled=cross_platform_dedupe)
        _dup_entries = _dedupe.dropped_entries
        _dup_ids = {str(e.get('job_id') or '') for e in _dup_entries}
        if _dup_entries:
            ctx.store.save_verdict_and_checkpoint_atomic(task_id, 'ai_rough', _dedupe.dup_verdicts, sorted(_dup_ids | set(ctx.store.load_checkpoint(task_id, 'ai_rough'))))
            emit(stage='screen_a', current=0, total=len(raw_jobs), message=_dedupe.progress_message)
            ctx.store.append_task_event(task_id, 'cross_platform_dedup', _dedupe.ledger_payload())
        settings = ctx.store.get_ai_settings()
        cred_ref = ctx.store.get_credential_ref()
        api_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ''
        endpoint = settings.get('endpoint_url', '')
        model = settings.get('model', '')
        if not ai_service.is_ai_available(settings, cred_ref, api_key) or not endpoint:
            raise ai_service.AISecurityError(ai_service.ERROR_NOT_CONFIGURED)
        criteria = dict(screening_fields or {})
        criteria.update({'profile_summary': profile_summary or '', **domain_context.criteria_state()})
        if _stop_requested():
            _handle_user_stop()
            return
        rough_outcome = run_rough_stage(ctx, task_id, raw_jobs, criteria, endpoint, api_key, model, execution_config, resume_from_run_id, resume_verdicts, _dup_ids, _dup_entries, frozen_platform, emit, _stop_requested, _handle_user_stop)
        if rough_outcome is None:
            return
        survivors, dropped = rough_outcome
        enriched = [dict(job) for job in survivors]
        jd_path = ctx.jd_checkpoint_path(ctx.app.config['RESULT_DIR'], task_id)
        jd_outcome = run_jd_stage(ctx, task_id, enriched, survivors, resume_jd, jd_path, frozen_platform, frozen_cdp_port, frozen_profile_key, frozen_browser_account, execution_config, stop_event, emit, _stop_requested, _handle_user_stop, save_jd_checkpoint=ctx.save_jd_checkpoint, domain_context=(None if resume_from_run_id == task_id else domain_context))
        if jd_outcome is None:
            return
        jd_map, jd_failures = jd_outcome
        _wb_record('unit_incomplete' if jd_failures else 'scope_completed', 'jd_detail', 'jd_detail', {'scope_complete': not bool(jd_failures), 'returned_total_count': len(jd_map), 'unit_unique_count': len(jd_map), 'stop_reason': 'job_detail_failed' if jd_failures else ('explicit_empty' if not enriched else 'target_reached')}, severity='warning' if jd_failures else 'info')
        if not jd_failures and not enriched:
            _wb_record('explicit_empty', 'jd_detail', 'jd_detail', {'empty_evidence': {'kind': 'stage_input_empty', 'reason': 'no_survivors_after_ai_rough'}}, severity='info')
        match_count = run_fine_stage(ctx, task_id, enriched, profile_summary, criteria, endpoint, api_key, model, profile_facts, execution_config, resume_from_run_id, resume_fine_verdicts, frozen_platform, emit, _stop_requested, _handle_user_stop)
        if match_count is None:
            return
        if _stop_requested():
            _handle_user_stop()
            return
        for job in enriched:
            job.setdefault('platform', frozen_platform)
        for job in dropped:
            job.setdefault('platform', frozen_platform)
        result = {'ok': True, 'jobs': enriched, 'dropped': dropped, 'total_scraped': len(raw_jobs), 'total_kept': len(enriched), 'total_matched': match_count, 'total_dropped': len(dropped), 'profile_summary': profile_summary, 'profile_facts': profile_facts, 'error': ''}
        job_events = []
        for job in enriched:
            verdict = job.get('verdict')
            is_failure = verdict == 'uncertain'
            job_events.append(('job_fail' if is_failure else 'job_success', {'stage': 'ai_fine', 'job_id': str(job.get('job_id') or ''), 'verdict': verdict, 'failed_code': job.get('failed_code') or job.get('jd_failed_code') if is_failure else None, 'reason': job.get('verdict_reason', ''), **({'evidence_detail': str(job.get('jd_failed_evidence') or '')} if is_failure else {})}))
        ctx.store.append_task_events(task_id, job_events)
        mismatch_count = sum((1 for job in enriched if job.get('verdict') == 'not_match'))
        pending_count = sum((1 for job in enriched if job.get('verdict') not in ('match', 'not_match', 'mismatch')))
        processed_count = match_count + mismatch_count
        _wb_result = _wb_finish()
        if _wb_result is not None:
            result['integrity'] = _wb_result
            result['ok'] = _wb_result.get('conclusion') in {'succeeded', 'empty'}
        _conclusion = str((result.get('integrity') or {}).get('conclusion') or 'unverifiable')
        ctx.write_run(task_id, match_count=match_count, mismatch_count=mismatch_count, pending_count=pending_count, processed_count=processed_count, total_scraped=len(raw_jobs), total_kept=len(enriched), total_dropped=len(dropped), current_stage='done')
        _dedupe_note = f'（抓取 {len(raw_jobs)}，跨平台重复 {_dedupe.deduped_count}，实际筛选 {len(raw_jobs) - _dedupe.deduped_count}）' if _dedupe.deduped_count else ''
        terminal_message = f'筛选完成，但有 {pending_count} 条待确认：匹配 {match_count} 条{_dedupe_note}' if pending_count else f'筛选完成：匹配 {match_count} 条{_dedupe_note}'
        emit(stage='done', current=processed_count, total=len(raw_jobs), total_matched=match_count, total_mismatch=mismatch_count, total_pending=pending_count, total_dropped=len(dropped), message=terminal_message)
        if ctx.is_user_finished(task_id):
            final_db_status = 'partial'
        elif _conclusion in {'partial', 'unverifiable'}:
            _progress_run = ctx.store.get_screening_run(task_id) or {}
            if _progress_run.get('status') not in ('queued', 'running'):
                raise RuntimeError(f"invalid_ai_terminal_status:{_progress_run.get('status')}")
            _accounted = sum((int(_progress_run.get(name) or 0) for name in ('processed_count', 'pending_count', 'total_dropped')))
            if _accounted < int(_progress_run.get('source_count') or 0):
                raise RuntimeError('invalid_ai_terminal_status:paused')
            defer_partial_status = True
            final_db_status = 'partial'
        elif _conclusion == 'failed':
            ctx.write_run(task_id, status='failed', error_code=(result.get('integrity') or {}).get('primary_code'), error_reason=(result.get('integrity') or {}).get('primary_reason'))
            final_db_status = 'failed'
        else:
            final_db_status = ctx.store.finalize_run_status(task_id)
            if final_db_status not in ('succeeded', 'partial'):
                if ctx.is_user_finished(task_id):
                    final_db_status = 'partial'
                else:
                    raise RuntimeError(f'invalid_ai_terminal_status:{final_db_status}')
        finalized = True
        from webui.screen_flow import build_round_script_params
        from webui.result_rounds import save_finished_round

        # A Flow result is publishable only when the final whitebox conclusion
        # is complete.  Keep source jobs visible through the scrape run, but
        # never create a result snapshot that would make the Track look
        # AI-screened when the AI evidence is partial, failed, or unverifiable.
        if frozen_flow_id and _conclusion in {'partial', 'failed', 'unverifiable'}:
            _failure_code = (
                'flow_result_incomplete'
                if _conclusion in {'partial', 'failed'}
                else 'whitebox_incomplete'
            )
            _mark_flow_failure(
                ctx,
                task_id,
                scrape_task_id,
                _failure_code,
                'AI 筛选未完成，岗位暂未标记为已筛选',
                platform=frozen_platform,
            )
            result['source_run_id'] = None
            return

        ai_run_for_params = ctx.store.get_screening_run(task_id) or {}
        saved_script_params = build_round_script_params(ctx.store, ai_run_for_params, screening_fields, frozen_platform)
        source_run_id = save_finished_round(ctx.store, result, saved_script_params, scrape_task_id=scrape_task_id, status='done' if result.get('ok') else 'partial', execution_config=execution_config.to_dict(), platform=frozen_platform, started_at=task.get('started_at'), finished_at=int(time.time() * 1000))
        if frozen_flow_id and not source_run_id:
            _mark_flow_failure(
                ctx,
                task_id,
                scrape_task_id,
                'flow_result_empty',
                'AI 筛选未生成可展示结果',
                platform=frozen_platform,
            )
            result['source_run_id'] = None
            return
        if source_run_id:
            try:
                parent_run = ctx.store.get_screening_run(scrape_task_id) or {}
                flow_id = (parent_run.get('execution_params') or {}).get('flow_id')
                flow_service = getattr(ctx, 'flow_service', None)
                if flow_id and flow_service is not None:
                    flow_service.mark_result_ready(
                        flow_id=flow_id,
                        platform=frozen_platform,
                        profile_id=parent_run.get('profile_id'),
                        result_run_id=source_run_id,
                    )
            except Exception as flow_exc:  # noqa: BLE001 - result stays durable
                from webui.logging_setup import get_logger
                get_logger(__name__).warning(
                    'B096 Flow result binding failed: %s', type(flow_exc).__name__
                )
                if frozen_flow_id:
                    try:
                        _mark_flow_failure(
                            ctx,
                            task_id,
                            scrape_task_id,
                            'flow_result_save_failed',
                            '筛选结果保存失败，请重试',
                            platform=frozen_platform,
                        )
                    except FlowStateClosureError:
                        raise
                    except Exception as closure_exc:  # noqa: BLE001
                        raise FlowStateClosureError() from closure_exc
                    raise FlowStateClosureError() from flow_exc
        if defer_partial_status and (not ctx.is_user_finished(task_id)):
            _integrity = result.get('integrity') or {}
            ctx.write_run(task_id, status='partial', error_code=_integrity.get('primary_code'), error_reason=_integrity.get('primary_reason'))
        result['source_run_id'] = source_run_id
        try:
            _saved_run = ctx.store.get_screening_run(source_run_id) or {}
            ctx.store.append_task_event(task_id, 'history_snapshot', {'snapshot_run_id': source_run_id, 'status': _saved_run.get('status') or 'done', 'jobs': len(enriched), 'dropped': len(dropped)})
        except ctx.operational_errors:
            pass
        ctx.prune_history_best_effort()
        # 043：整条进出——定稿流程清更早中间档 + 无主账本兜底（服务内部 best-effort）。
        from webui import run_cleanup
        run_cleanup.prune_after_finalize(ctx.store, str(source_run_id or ""))
        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if task is not None:
                task['result'] = result
                task['status'] = 'done' if result.get('ok') else 'partial'
        ctx.schedule_pipeline_task_cleanup(task_id)
        ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
        ctx.remove_jd_checkpoint(jd_path)
    except (DomainContextError, ScreeningPolicyIncompatibleError) as exc:
        # 先完成失败补偿，证据记录异常不能阻止终态落库或释放占位。
        _persist_ai_worker_failure(ctx, task_id, scrape_task_id, exc.error_code, str(exc))
        try:
            _wb_record('unit_failed', 'ai_screen', 'ai_screen', {'error_code': exc.error_code}, severity='error')
            _wb_finish('failed')
        except Exception as marker_exc:
            from webui.logging_setup import get_logger
            get_logger(__name__).warning("Domain failure evidence finalization failed: %s", type(marker_exc).__name__)
        return
    except FlowStateClosureError:
        # The Flow coordinator has already attempted the atomic durable
        # closure.  Preserve the safe, observable error for the caller rather
        # than converting it into an apparently successful worker return.
        raise
    except ai_service.AISecurityError as exc:
        error_message = ai_service.user_facing_error(exc.error_code)
        _mark_flow_failure(ctx, task_id, scrape_task_id, exc.error_code, error_message)
        try:
            _wb_record('unit_failed', 'ai_screen', 'ai_screen', {'error_code': exc.error_code, 'error_reason': error_message}, severity='error')
            _wb_finish('failed')
        except Exception as marker_exc:
            from webui.logging_setup import get_logger
            get_logger(__name__).warning(
                "AI security failure whitebox finalization failed: %s",
                type(marker_exc).__name__,
            )
        if not ctx.is_user_finished(task_id):
            record_failure(ctx.store, task_id, stage='ai_screen', error_code=exc.error_code, reason=error_message, correlation_id=task_id, diagnostics=dict(getattr(exc, 'diagnostics', None) or {}), exception=exc, whitebox_unit=_current_whitebox_unit)
        persistence_error = None
        try:
            ctx.write_run(task_id, status='failed', error_code=exc.error_code, error_reason=error_message)
        except Exception as persist_exc:
            persistence_error = type(persist_exc).__name__
        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if task is not None:
                if ctx.is_user_finished(task_id):
                    task['status'] = 'cancelled'
                    task['error'] = ctx.msg_user_finished
                else:
                    task['status'] = 'failed'
                    task['error'] = error_message if persistence_error is None else f'{error_message}；状态保存失败：{persistence_error}'
        ctx.schedule_pipeline_task_cleanup(task_id)
        with ctx.lock:
            _terminal_status = (ctx.tasks.get(task_id) or {}).get('status')
        if _terminal_status in ('cancelled', 'failed'):
            ctx.clear_auto_screen(task_id)
        ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
    except Exception as exc:
        error_message = ai_service.user_facing_error('internal_error')
        try:
            _wb_record('unit_failed', 'ai_screen', 'ai_screen', {'error_code': 'internal_error', 'error_reason': error_message}, severity='error')
            _wb_finish('failed')
        except Exception as marker_exc:
            from webui.logging_setup import get_logger
            get_logger(__name__).warning(
                "AI internal failure whitebox finalization failed: %s",
                type(marker_exc).__name__,
            )
        if finalized and (not ctx.is_user_finished(task_id)):
            _downgraded = False
            try:
                _current_status = str((ctx.store.get_screening_run(task_id) or {}).get('status') or '')
                if _current_status in ('queued', 'running'):
                    _downgraded = True
                else:
                    _downgraded = ctx.store.downgrade_succeeded_if_no_result_round(task_id, error_code='result_round_save_failed', error_reason='筛选已完成但结果保存失败，点继续可重试保存')
            except ctx.operational_errors:
                _downgraded = False
            try:
                ctx.store.append_task_event(
                    task_id,
                    'result_round_save_failed',
                    {
                        'downgraded': bool(_downgraded),
                        'error_code': 'result_round_save_failed',
                    },
                )
            except ctx.operational_errors:
                pass
            if _downgraded:
                error_message = '筛选已完成但结果保存失败，点继续可重试保存'
        _fail_code = 'result_round_save_failed' if finalized and (not ctx.is_user_finished(task_id)) and _downgraded else 'internal_error'
        _mark_flow_failure(ctx, task_id, scrape_task_id, _fail_code, error_message)
        if not ctx.is_user_finished(task_id):
            record_failure(ctx.store, task_id, stage='ai_screen', error_code=_fail_code, reason=error_message, correlation_id=task_id, diagnostics={}, exception=exc, include_traceback=True, whitebox_unit=_current_whitebox_unit)
        persistence_error = None
        try:
            ctx.write_run(task_id, status='failed', error_code=_fail_code, error_reason=error_message)
        except Exception as persist_exc:
            persistence_error = type(persist_exc).__name__
        with ctx.lock:
            task = ctx.tasks.get(task_id)
            if task is not None:
                if ctx.is_user_finished(task_id):
                    task['status'] = 'cancelled'
                    task['error'] = ctx.msg_user_finished
                else:
                    task['status'] = 'failed'
                    task['error'] = error_message if persistence_error is None else f'{error_message}；状态保存失败：{persistence_error}'
        ctx.schedule_pipeline_task_cleanup(task_id)
        ctx.release_worker_resume_claims(ctx.tasks.get(task_id))
