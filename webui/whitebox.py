from __future__ import annotations
import json, os
from dataclasses import dataclass; from pathlib import Path; from typing import Any
from webui.logging_setup import get_logger, redact; from webui.whitebox_rules import CONCLUSION_LABELS, reduce_conclusion
_logger = get_logger(__name__)
class WhiteboxError(RuntimeError): pass
class WhiteboxConflictError(WhiteboxError): pass
class WhiteboxWriteError(WhiteboxError): pass
class WhiteboxNotFoundError(WhiteboxError): pass
@dataclass(frozen=True)
class WhiteboxRunRef:
    id: str; owner_kind: str; owner_id: str
    @property
    def run_id(self) -> str: return self.id
    def __str__(self) -> str: return self.id
    def __getitem__(self, key: str) -> Any: return {'id': self.id, 'run_id': self.id, 'owner_kind': self.owner_kind, 'owner_id': self.owner_id}[key]
class WhiteboxService:
    _EVENT_TYPES = {'task_started', 'stage_started', 'unit_started', 'page_started', 'page_completed', 'scope_completed', 'source_exhausted', 'explicit_empty', 'unit_failed', 'unit_incomplete', 'unit_skipped', 'account_switched', 'account_switch', 'account_pool_snapshot', 'account_allocation', 'account_handoff', 'browser_restarted', 'browser_recovered', 'stall_detected', 'retry_started', 'retry_abandoned', 'ai_request_failed', 'ai_keep_all_fallback', 'checkpoint_restored', 'recovery_completed', 'submission_failed', 'whitebox_incomplete', 'emergency_record_imported', 'task_finalized', 'task_interrupted'}
    def __init__(self, store: Any, *, emergency_path: str | Path | None=None, sync_task_state: Any | None=None):
        self.store = store
        self.emergency_path = Path(emergency_path) if emergency_path else self._default_emergency_path(store)
        self.sync_task_state = sync_task_state
    @staticmethod
    def _default_emergency_path(store: Any) -> Path:
        db_path = getattr(store, 'db_path', None)
        if isinstance(db_path, (str, bytes, os.PathLike)):
            try:
                return Path(os.fsdecode(db_path)).with_name('whitebox-emergency.jsonl')
            except (TypeError, ValueError, OSError):
                pass
        return Path.home() / '.career-scout' / 'webui' / 'whitebox-emergency.jsonl'
    def begin(self, owner_kind: str, owner_id: str, plan: dict[str, Any], parent_owner_id: str | None=None) -> WhiteboxRunRef:
        self._validate_plan(plan)
        try:
            row = self.store.create_whitebox_run(owner_kind, owner_id, plan, parent_owner_id)
        except ValueError as exc:
            if 'conflict' in str(exc).lower():
                raise WhiteboxConflictError(str(exc)) from exc
            self._persist_emergency({'owner_kind': owner_kind, 'owner_id': owner_id, 'stage': 'begin', 'event_type': 'whitebox_incomplete', 'idempotency_key': f'begin:{owner_kind}:{owner_id}', 'occurred_at': _now(), 'payload': {'reason': str(exc)}}, None)
            raise
        except Exception as exc:
            self._persist_emergency({'owner_kind': owner_kind, 'owner_id': owner_id, 'stage': 'begin', 'event_type': 'whitebox_incomplete', 'idempotency_key': f'begin:{owner_kind}:{owner_id}', 'occurred_at': _now(), 'payload': {'reason': type(exc).__name__}}, None)
            raise WhiteboxWriteError('whitebox begin persistence failed') from exc
        run_id = str(row.get('id') if isinstance(row, dict) else row['id'])
        return WhiteboxRunRef(run_id, str(owner_kind), str(owner_id))
    def record(self, run_ref: WhiteboxRunRef | str | dict[str, Any], fact: dict[str, Any]) -> dict[str, Any]:
        run_id = self._run_id(run_ref)
        normalized = self._validate_fact(fact)
        try:
            receipt = self.store.append_whitebox_event(run_id, normalized)
            self._project_unit(run_id, normalized)
            return {'run_id': run_id, 'sequence': int(receipt.get('sequence') if isinstance(receipt, dict) else receipt['sequence']), 'idempotency_key': normalized['idempotency_key'], 'duplicate': self._receipt_is_duplicate(receipt, normalized)}
        except Exception as exc:
            self._handle_required_write_failure(run_id, normalized, exc); raise WhiteboxWriteError('required whitebox fact could not be persisted') from exc
    def finalize(self, run_ref: WhiteboxRunRef | str | dict[str, Any], *, lifecycle_end: str | None=None, task_returncode: int | None=None) -> dict[str, Any]:
        run_id = self._run_id(run_ref)
        run = self.store.get_whitebox_run_by_id(run_id)
        if run is None:
            raise WhiteboxNotFoundError(run_id)
        importer = getattr(self.store, 'import_whitebox_emergency', None)
        if callable(importer):
            try:
                importer(self.emergency_path, run_id, owner_kind=run.get('owner_kind'), owner_id=run.get('owner_id'))
            except Exception as exc:
                self._handle_required_write_failure(run_id, {'event_type': 'whitebox_incomplete', 'stage': 'finalize'}, exc)
                raise WhiteboxWriteError('whitebox emergency import failed') from exc
        plan = _decode(run.get('plan_json'), {})
        units = self.store.list_whitebox_units(run_id)
        events = self.store.list_whitebox_events(run_id)
        # A terminal failed/interrupted close is durable.  A later caller may
        # retry ``finalize`` for idempotency, but omitting an explicit new end
        # must not silently turn that old terminal failure into success.
        stored_conclusion = str(run.get('conclusion') or '').strip().lower()
        effective_end = lifecycle_end
        if effective_end is None and stored_conclusion in {'failed', 'interrupted'}:
            effective_end = stored_conclusion
        last_interrupt = max(
            [int(event.get('sequence') or 0) for event in events
             if str(event.get('event_type') or '') == 'task_interrupted'] or [0]
        )
        progress_after_interrupt = any(
            int(event.get('sequence') or 0) > last_interrupt and
            str(event.get('event_type') or '') in {
                'unit_started', 'page_started', 'page_completed', 'scope_completed',
                'unit_failed', 'unit_incomplete', 'unit_skipped', 'submission_failed',
            }
            for event in events
        )
        if str(lifecycle_end or '').strip().lower() in {'succeeded', 'success', 'completed'} and (
            stored_conclusion == 'interrupted' or (last_interrupt and not progress_after_interrupt)
        ):
            effective_end = 'interrupted'
        result = reduce_conclusion(plan, units, lifecycle_end=effective_end, run_unique_count=getattr(self, '_run_unique_count', None) if getattr(self, '_run_unique_count', None) is not None else run.get('run_unique_count'), events=events)
        summary = result.setdefault('summary', {})
        summary['observed_units'] = len(units)
        try:
            updated = self.store.finalize_whitebox(
                run_id, result, lifecycle_status='terminal',
                final_event={'idempotency_key': f"finalized:{run_id}:{int(run.get('revision') or 0) + 1}", 'event_type': 'task_finalized', 'occurred_at': _now(), 'stage': 'finalize', 'required_evidence': False, 'payload': {'conclusion': result.get('conclusion')}},
            )
            result['revision'] = int(updated.get('revision') or 0)
            self._sync_task_state(run, result, task_returncode=task_returncode)
        except Exception as exc:
            self._handle_required_write_failure(run_id, {'idempotency_key': f'finalize:{run_id}', 'event_type': 'whitebox_incomplete', 'occurred_at': _now(), 'stage': 'finalize', 'required_evidence': True, 'payload': {'reason': type(exc).__name__}}, exc); raise WhiteboxWriteError('whitebox finalization persistence failed') from exc
        return result
    def report(self, owner_kind: str, owner_id: str, *, include_events: bool=False, after_sequence: int=0, event_limit: int=100, cursor: int | None=None) -> dict[str, Any]:
        if cursor is not None:
            after_sequence = cursor
        run = self.store.get_whitebox_run(owner_kind, owner_id)
        if run is None:
            if not self._business_owner_exists(owner_kind, owner_id):
                raise WhiteboxNotFoundError(f'{owner_kind}:{owner_id}')
            return self.legacy_report(owner_kind, owner_id)
        units = self.store.list_whitebox_units(run['id'])
        plan = _decode(run.get('plan_json'), {}); events = self.store.list_whitebox_events(run['id'])
        integrity = self._integrity_from_run(run)
        if integrity is not None:
            end = run.get('conclusion') if run.get('conclusion') in {'failed', 'interrupted'} else None
            integrity = reduce_conclusion(plan, units, lifecycle_end=end, run_unique_count=run.get('run_unique_count'), events=events)
            integrity['revision'] = int(run.get('revision') or 0)
        report = {'owner_kind': str(owner_kind), 'owner_id': str(owner_id), 'lifecycle_status': run.get('lifecycle_status'), 'integrity': integrity, 'plan': plan, 'summary': {'planned_units': int(run.get('planned_unit_count') or 0), 'completed_units': int(run.get('completed_unit_count') or 0), 'failed_units': int(run.get('failed_unit_count') or 0), 'unknown_units': int(run.get('unknown_unit_count') or 0), 'unit_output_sum': int(run.get('unit_output_sum') or 0), 'run_unique_count': int(run.get('run_unique_count') or 0), 'quality_counts': _decode(run.get('quality_counts_json'), {})}, 'units': units}
        if include_events:
            limit = max(1, min(1000, int(event_limit))); events = self.store.list_whitebox_events(run['id'], after_sequence=after_sequence, limit=limit + 1)
            has_more = len(events) > limit; events = events[:limit]; report['events'] = [self._public_event(event) for event in events]
            report['events_truncated'] = bool(has_more and events)
            if has_more and events: report['next_sequence'] = int(events[-1]['sequence'])
        return report
    def integrity_for_result(self, run_id: str, scrape_task_id: str='') -> dict[str, Any]:
        run = self.store.get_screening_run(run_id) if run_id else None
        if run is None:
            return self.report('screening', run_id)['integrity']
        if run.get('record_kind') != 'result_snapshot':
            for kind in ('screening', 'scrape', 'recrawl'):
                if self.store.get_whitebox_run(kind, str(run_id)) is not None:
                    return self.report(kind, str(run_id))['integrity']
            return self.report('screening', str(run_id))['integrity']
        params = run.get('execution_params') or {}
        source_id = str(scrape_task_id or params.get('scrape_task_id') or '')
        status = str(run.get('status') or '')
        if status != 'scraped_only' and source_id:
            candidates = []
            try: candidates = self.store.latest_screen_runs_for_source(source_id)
            except Exception: candidates = []
            for candidate in reversed(candidates):
                candidate_id = str(candidate.get('id') or '')
                if self.store.get_whitebox_run('screening', candidate_id) is not None:
                    return self.report('screening', candidate_id)['integrity']
        if source_id and self.store.get_whitebox_run('scrape', source_id) is not None:
            return self.report('scrape', source_id)['integrity']
        return self.legacy_report('screening', str(run_id))['integrity']
    def record_for_owner(self, owner_kind: str, owner_id: str, fact: dict[str, Any]) -> bool:
        run = self.store.get_whitebox_run(str(owner_kind), str(owner_id))
        if not isinstance(run, dict) or not run.get('id'):
            return False
        normalized = dict(fact)
        unit_key = str(normalized.get('unit_key') or '')
        requested_attempt = max(1, int(normalized.get('attempt_no') or 1))
        if unit_key:
            try:
                all_units = list(self.store.list_whitebox_units(run['id']))
                matching = [
                    unit for unit in all_units
                    if str(unit.get('unit_key') or '') == unit_key
                ]
                if not matching:
                    # Diagnostics may know the failing stage but not the
                    # runner's internal unit key.  Never create an orphan
                    # projection such as ``ai_screen`` that the frozen plan
                    # cannot reduce.  Bind it to the last active planned unit
                    # (or the last planned unit after terminal completion).
                    stage = str(normalized.get('stage') or '')
                    candidates = [
                        unit for unit in all_units
                        if str(unit.get('status') or 'planned')
                        not in {'succeeded', 'empty'}
                    ]
                    same_stage = [
                        unit for unit in candidates
                        if stage and str(unit.get('stage') or '') == stage
                    ]
                    target = (same_stage or candidates or all_units)[-1:]
                    if target:
                        target_unit = target[0]
                        unit_key = str(target_unit.get('unit_key') or '')
                        normalized['unit_key'] = unit_key
                        normalized['unit_kind'] = target_unit.get('unit_kind')
                        normalized['stage'] = target_unit.get('stage') or normalized.get('stage')
                        requested_attempt = max(1, int(target_unit.get('attempt_no') or 1))
                        normalized['attempt_no'] = requested_attempt
                        matching = [
                            unit for unit in all_units
                            if str(unit.get('unit_key') or '') == unit_key
                        ]
                if matching:
                    latest = max(matching, key=lambda unit: int(unit.get('attempt_no') or 1))
                    latest_attempt = int(latest.get('attempt_no') or 1)
                    latest_status = str(latest.get('status') or 'planned')
                    if requested_attempt <= latest_attempt and latest_status in {'failed', 'incomplete', 'skipped'}:
                        requested_attempt = latest_attempt + 1
                        normalized['attempt_no'] = requested_attempt
                        normalized['idempotency_key'] = (
                            f"{normalized.get('idempotency_key')}:attempt{requested_attempt}"
                        )
            except Exception:
                _logger.debug('白箱尝试上下文读取失败，保留原尝试号', exc_info=True)
        self.record(run['id'], normalized)
        return True
    def mark_submission_failed(self, owner_kind: str, owner_id: str, plan: dict[str, Any],
                               reason: str, *, parent_owner_id: str | None = None,
                               stage: str = 'submit') -> dict[str, Any]:
        """Persist one failed fact per planned unit and close the run."""
        run = self.store.get_whitebox_run(owner_kind, owner_id)
        if run is None:
            self.begin(owner_kind, owner_id, plan, parent_owner_id=parent_owner_id)
            run = self.store.get_whitebox_run(owner_kind, owner_id)
        if not run: raise WhiteboxNotFoundError(f'{owner_kind}:{owner_id}')
        now = _now()
        for unit in self.store.list_whitebox_units(run['id']):
            key = str(unit.get('unit_key') or '')
            if not key: continue
            attempt = int(unit.get('attempt_no') or 1)
            if str(unit.get('status') or 'planned') != 'planned': attempt += 1
            self.record(run['id'], {'idempotency_key': f'submission-failed:{owner_id}:{key}:{attempt}',
                                    'event_type': 'submission_failed', 'occurred_at': now,
                                    'stage': stage, 'unit_kind': unit.get('unit_kind'),
                                    'unit_key': key, 'attempt_no': attempt, 'required_evidence': True,
                                    'severity': 'error', 'payload': {'error_code': 'submit_failed',
                                    'error_reason': reason}})
        return self.finalize(run['id'], lifecycle_end='failed')
    @staticmethod
    def _validate_plan(plan: Any) -> None:
        if not isinstance(plan, dict) or not isinstance(plan.get('units'), list) or (not plan['units']): raise ValueError('whitebox plan must contain a non-empty units list')
        seen: set[str] = set()
        for item in plan['units']:
            if not isinstance(item, dict): raise ValueError('whitebox plan units must be objects')
            key = str(item.get('unit_key') or item.get('key') or '').strip()
            if not key or key in seen: raise ValueError('whitebox plan contains duplicate or empty unit_key')
            seen.add(key)
    def _validate_fact(self, fact: Any) -> dict[str, Any]:
        if not isinstance(fact, dict): raise ValueError('whitebox fact must be an object')
        required = ('idempotency_key', 'event_type', 'occurred_at', 'stage', 'required_evidence', 'payload')
        missing = [name for name in required if name not in fact]
        if missing: raise ValueError(f"whitebox fact missing: {', '.join(missing)}")
        normalized = dict(fact)
        normalized['idempotency_key'] = str(fact['idempotency_key']).strip()
        normalized['event_type'] = str(fact['event_type']).strip()
        normalized['stage'] = str(fact['stage']).strip()
        if not normalized['idempotency_key'] or not normalized['event_type'] or (not normalized['stage']): raise ValueError('whitebox fact has empty identity fields')
        if normalized['event_type'] not in self._EVENT_TYPES: raise ValueError(f"unknown whitebox event_type: {normalized['event_type']}")
        if not isinstance(fact['payload'], dict): raise ValueError('whitebox fact payload must be an object')
        raw_required = fact['required_evidence']
        if not isinstance(raw_required, bool):
            if isinstance(raw_required, int) and raw_required in (0, 1): raw_required = bool(raw_required)
            else: raise ValueError('whitebox fact required_evidence must be boolean')
        normalized['required_evidence'] = raw_required
        normalized['payload'] = _safe_payload(fact['payload'])
        if 'attempt_no' in normalized and normalized['attempt_no'] is not None:
            normalized['attempt_no'] = max(1, int(normalized['attempt_no']))
        return normalized
    def _project_unit(self, run_id: str, fact: dict[str, Any]) -> None:
        key = fact.get('unit_key')
        if not key: return
        planned_units = self.store.list_whitebox_units(run_id)
        requested_attempt = int(fact.get('attempt_no') or 1)
        matching_units = [unit for unit in planned_units if str(unit.get('unit_key') or '') == str(key) and int(unit.get('attempt_no') or 1) == requested_attempt]
        target_unit = matching_units[-1] if matching_units else None
        events = self.store.list_whitebox_events(run_id)
        related = [event for event in events if str(event.get('unit_key') or '') == str(key) and int(event.get('attempt_no') or requested_attempt) == requested_attempt]
        if not related: return
        pages: dict[int, dict[str, Any]] = {}; page_evidence_missing = False; status = 'planned'; degraded = False
        scope_complete: bool | None = None; source_exhausted: bool | None = None; stop_reason = None
        returned_total = 0; scope_returned_total = None; unique_count = 0; scope_unique_count = None
        page_unique_sum = 0; page_has_new_unique_count = False; page_cumulative_unique_counts: list[int] = []
        quality: dict[str, int] = {}; planned_pages = None; error_code = None; error_reason = None; explicit_empty = False
        for event in related:
            payload = _decode(event.get('payload_json'), {})
            etype = str(event.get('event_type') or '')
            if etype == 'unit_started': status = 'running'
            elif etype == 'page_completed':
                try:
                    required_page_fields = ('page', 'planned_pages', 'returned_count', 'new_unique_count', 'has_more', 'resume_page')
                    if any((name not in payload for name in required_page_fields)): raise KeyError('page evidence fields')
                    page = int(payload['page'])
                    if page > 0 and int(payload['planned_pages']) >= 0:
                        pages[page] = payload
                    else: page_evidence_missing = True
                except (KeyError, TypeError, ValueError):
                    page_evidence_missing = True
                planned_pages = payload.get('planned_pages', planned_pages)
            elif etype == 'scope_completed':
                scope_complete = _bool_or_none(payload.get('scope_complete'))
                source_exhausted = _bool_or_none(payload.get('source_exhausted'))
                stop_reason = payload.get('stop_reason') or stop_reason
                if str(stop_reason or '') == 'explicit_empty':
                    explicit_empty = True
                scope_returned_total = max(scope_returned_total or 0, int(payload.get('returned_total_count') or 0))
                scope_value = max(0, int(payload.get('unit_unique_count') or 0))
                scope_unique_count = max(scope_unique_count or 0, scope_value)
                if status not in {'failed', 'incomplete', 'skipped'}: status = 'succeeded'
            elif etype == 'source_exhausted': source_exhausted = True; stop_reason = 'source_exhausted'
            elif etype == 'explicit_empty':
                explicit_empty = True
                stop_reason = 'explicit_empty'
            elif etype in {'unit_failed', 'submission_failed'}:
                status = 'failed'
                error_code = payload.get('error_code') or payload.get('code') or 'unit_failed'
                error_reason = payload.get('error_reason') or payload.get('reason') or '计划单元执行失败'
                stop_reason = payload.get('stop_reason') or 'soft_failure'
            elif etype == 'unit_incomplete':
                status = 'incomplete'; stop_reason = payload.get('stop_reason') or 'unknown'; error_code = payload.get('error_code') or error_code; error_reason = payload.get('error_reason') or payload.get('reason') or error_reason
            elif etype == 'unit_skipped': status = 'skipped'; stop_reason = payload.get('stop_reason') or 'unknown'
            elif etype in {'ai_request_failed', 'ai_keep_all_fallback', 'browser_restarted', 'browser_recovered', 'account_switch', 'account_switched'}:
                degraded = True; status = 'incomplete' if etype == 'ai_keep_all_fallback' else status
            if payload.get('degraded'): degraded = True
            raw_quality = payload.get('quality_counts')
            if isinstance(raw_quality, dict):
                for name, value in raw_quality.items():
                    try:
                        amount = max(0, int(value or 0))
                    except (TypeError, ValueError):
                        continue
                    quality[str(name)] = quality.get(str(name), 0) + amount
        for payload in pages.values():
            returned_total += max(0, int(payload.get('returned_count') or payload.get('jobs_count') or 0))
            if 'new_unique_count' in payload:
                page_has_new_unique_count = True
                page_unique_sum += max(0, int(payload.get('new_unique_count') or 0))
            elif 'unit_unique_count' in payload:
                page_cumulative_unique_counts.append(max(0, int(payload.get('unit_unique_count') or 0)))
            if payload.get('explicit_empty'): explicit_empty = True
        if scope_returned_total is not None: returned_total = max(returned_total, scope_returned_total)
        if scope_unique_count is not None:
            unique_count = scope_unique_count
        elif page_has_new_unique_count:
            unique_count = page_unique_sum
        elif page_cumulative_unique_counts:
            unique_count = max(page_cumulative_unique_counts)
        if status == 'succeeded' and unique_count == 0 and explicit_empty: status = 'empty'
        if status == 'succeeded' and planned_pages is not None:
            try:
                if int(planned_pages) > 0 and not pages: page_evidence_missing = True
            except (TypeError, ValueError):
                page_evidence_missing = True
        evidence = bool(scope_complete is True and status in {'succeeded', 'empty'} and (not page_evidence_missing))
        if status == 'succeeded' and unique_count == 0 and (not explicit_empty): evidence = False
        if page_evidence_missing and (not error_code):
            error_code = 'page_evidence_missing'; error_reason = '页面完成证据字段缺失'
        previous_ids = [unit.get('id') for unit in planned_units if str(unit.get('unit_key') or '') == str(key) and int(unit.get('attempt_no') or 1) < requested_attempt]
        self.store.upsert_whitebox_unit(run_id, {'stage': str((target_unit or {}).get('stage') or fact.get('stage') or 'task'), 'unit_kind': str((target_unit or {}).get('unit_kind') or fact.get('unit_kind') or 'unit'), 'unit_key': str(key), 'attempt_no': requested_attempt, 'recovered_from_unit_id': previous_ids[-1] if previous_ids else None, 'planned_pages': planned_pages, 'completed_pages': len(pages), 'last_completed_page': max(pages) if pages else None, 'scope_complete': scope_complete, 'source_exhausted': source_exhausted, 'returned_total_count': returned_total, 'unit_unique_count': unique_count, 'stop_reason': stop_reason, 'status': status, 'degraded': degraded, 'evidence_complete': evidence, 'quality_counts': quality, 'error_code': error_code, 'error_reason': error_reason})
    def _sync_task_state(self, run: dict[str, Any], result: dict[str, Any], *, task_returncode: int | None=None) -> None:
        if self.sync_task_state is not None: self.sync_task_state(run, result); return
        if str(run.get('owner_kind') or '') != 'legacy_task': return
        owner_id = str(run.get('owner_id') or '')
        if not owner_id or not hasattr(self.store, 'get_task'): return
        try:
            current = self.store.get_task(owner_id)
        except Exception: return
        mapping = {'succeeded': 'succeeded', 'empty': 'succeeded', 'partial': 'partial', 'failed': 'failed', 'unverifiable': 'partial', 'interrupted': 'interrupted'}
        target = mapping.get(result.get('conclusion'))
        if not target or current.get('status') == target: return
        try:
            self.store.update_task(owner_id, target, returncode=task_returncode, error=result.get('primary_reason'))
        except Exception as exc:
            _logger.error('legacy task state sync failed owner=%s error=%s', owner_id, type(exc).__name__)
            raise WhiteboxWriteError('legacy task state synchronization failed') from exc
    def _handle_required_write_failure(self, run_id: str, fact: dict[str, Any], exc: BaseException) -> None:
        _logger.error('whitebox write failed run=%s event=%s error=%s', run_id, fact.get('event_type'), type(exc).__name__)
        try:
            if hasattr(self.store, 'mark_whitebox_incomplete'):
                self.store.mark_whitebox_incomplete(run_id, stage=str(fact.get('stage') or 'unknown'), reason=type(exc).__name__)
                return
        except Exception as marker_exc: _logger.error('whitebox incomplete marker failed run=%s error=%s', run_id, type(marker_exc).__name__)
        if not self._persist_emergency({'owner_id': run_id, 'stage': fact.get('stage'), 'event_type': 'whitebox_incomplete', 'idempotency_key': f"incomplete:{run_id}:{fact.get('idempotency_key')}", 'occurred_at': _now(), 'payload': {'reason': type(exc).__name__}}, run_id):
            _logger.critical('whitebox emergency persistence failed run=%s', run_id)
    def _persist_emergency(self, record: dict[str, Any], run_id: str | None) -> bool:
        try:
            record = dict(record)
            if run_id:
                record.setdefault('run_id', run_id)
            append = getattr(self.store, 'append_whitebox_emergency', None)
            if append is not None:
                return bool(append(self.emergency_path, record))
            self.emergency_path.parent.mkdir(parents=True, exist_ok=True)
            with self.emergency_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(_safe_payload(record), ensure_ascii=False, sort_keys=True) + '\n')
            return True
        except Exception as exc:
            _logger.error('whitebox emergency append failed run=%s error=%s', run_id or '-', type(exc).__name__); return False
    def _business_owner_exists(self, owner_kind: str, owner_id: str) -> bool:
        probes = {'legacy_task': ('get_task',), 'workbench': ('get_search_run',), 'tuning': ('get_tuning_round', 'get_task_manifest'), 'recrawl': ('get_screening_run',), 'scrape': ('get_screening_run', 'get_task'), 'screening': ('get_screening_run', 'get_task')}.get(owner_kind, ())
        for name in probes:
            fn = getattr(self.store, name, None)
            if fn is None: continue
            try:
                if fn(owner_id) is not None: return True
            except (KeyError, TypeError, AttributeError): continue
        return False
    @staticmethod
    def legacy_report(owner_kind: str, owner_id: str) -> dict[str, Any]:
        integrity = {'conclusion': 'unverifiable', 'label': '无法确认', 'degraded': False, 'evidence_complete': False, 'primary_code': 'legacy_evidence_missing', 'primary_reason': '历史证据不足，无法确认', 'recommendation': '建议重新执行', 'revision': 0}
        return {'owner_kind': str(owner_kind), 'owner_id': str(owner_id), 'lifecycle_status': 'terminal', 'integrity': integrity, 'plan': {'units': []}, 'summary': {'planned_units': 0, 'completed_units': 0, 'failed_units': 0, 'unknown_units': 0, 'unit_output_sum': 0, 'run_unique_count': 0, 'quality_counts': {}}, 'units': []}
    @staticmethod
    def _integrity_from_run(run: dict[str, Any]) -> dict[str, Any]:
        if not run.get('conclusion') and str(run.get('lifecycle_status') or '') != 'terminal':
            return None
        conclusion = run.get('conclusion') or 'unverifiable'
        return {'conclusion': conclusion, 'label': CONCLUSION_LABELS.get(conclusion, '无法确认'), 'degraded': bool(run.get('degraded')), 'evidence_complete': bool(run.get('evidence_complete')), 'primary_code': run.get('primary_code'), 'primary_reason': run.get('primary_reason'), 'recommendation': _recommendation(conclusion), 'revision': int(run.get('revision') or 0)}
    @staticmethod
    def _public_event(event: dict[str, Any]) -> dict[str, Any]:
        payload = _decode(event.get('payload_json'), {})
        return {'sequence': int(event.get('sequence') or 0), 'stage': event.get('stage'), 'unit_kind': event.get('unit_kind'), 'unit_key': event.get('unit_key'), 'attempt_no': event.get('attempt_no'), 'event_type': event.get('event_type'), 'required_evidence': bool(event.get('required_evidence')), 'severity': event.get('severity'), 'payload': payload, 'occurred_at': event.get('occurred_at'), 'recorded_at': event.get('recorded_at')}
    @staticmethod
    def _run_id(ref: WhiteboxRunRef | str | dict[str, Any]) -> str:
        if isinstance(ref, WhiteboxRunRef):
            return ref.id
        if isinstance(ref, dict):
            return str(ref.get('id') or ref.get('run_id') or '')
        return str(ref)
    @staticmethod
    def _receipt_is_duplicate(receipt: Any, fact: dict[str, Any]) -> bool:
        return bool(isinstance(receipt, dict) and receipt.get('_duplicate'))
def _decode(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(value or '')
    except (TypeError, ValueError): return default
    return parsed
def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sensitive = {'key', 'token', 'secret', 'password', 'cookie', 'authorization', 'api_key', 'apikey', 'jd', 'jd_body', 'resume_text', 'resume_body', 'prompt', 'raw_response'}
        return {str(k): '[REDACTED]' if str(k).lower() in sensitive else _safe_payload(v) for k, v in value.items() if str(k).lower() not in {'full_resume', 'full_jd', 'credential'}}
    if isinstance(value, (list, tuple)): return [_safe_payload(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)): return value
    return redact(str(value))[:4000]
def _bool_or_none(value: Any) -> bool | None:
    if value is None: return None
    if isinstance(value, bool): return value
    if isinstance(value, str):
        if value.strip().lower() in {'true', '1', 'yes'}: return True
        if value.strip().lower() in {'false', '0', 'no'}: return False
    if isinstance(value, (int, float)): return bool(value)
    return None
def _recommendation(conclusion: str) -> str:
    return {'succeeded': '可查看结果', 'empty': '可调整条件后重试', 'partial': '查看已有结果或重试缺失部分', 'failed': '查看原因后重试', 'unverifiable': '建议重新执行', 'interrupted': '可继续或重新执行'}.get(conclusion, '建议重新执行')
def _now() -> str:
    from webui.store_helpers import _now as store_now
    return store_now()
from webui.whitebox_evidence import ScrapeEvidence
Whitebox = WhiteboxService
def build_tuning_plan(round_kind: str) -> dict[str, Any]:
    kind = str(round_kind or 'unknown').strip() or 'unknown'
    return {'stages': [kind], 'units': [{'unit_key': f'round:{kind}', 'unit_kind': 'tuning_round', 'stage': kind, 'required': True}]}
