"""run_search：关键词×城市组合抓取主流程（021 B7 自 pipeline_exec.py 搬运）。

ensure_chrome_ready / close_debug_chrome / load_advanced_settings 经门面动态取用。
"""

from __future__ import annotations

import random
import time
from datetime import datetime

from webui.pipeline_exec_artifacts import _combo_output_path
from webui.pipeline_exec_filters import expand_combinations
from webui.platform_input_adapter import resolve_platform_input_adapter
from webui.pipeline_exec_settings import _PIPELINE_OPERATION_ERRORS
from webui.pipeline_exec_status import (
    _SCRAPE_STAGE_MESSAGES,
    classify_preflight_failure,
    _scrape_overall_percent,
    _scrape_page_overall_percent,
    failed_code_label,
    user_visible_failure_reason,
)
from webui.source import PageEventPersistenceError, SourceOutcome
from webui.browser_recovery import BrowserRecovery
from webui.frozen_browser_identity import bind_frozen_source_profile
from webui.task_pause_support import (
    STOP_MODE_PAUSE,
    stop_mode_for_event,
)
from webui.error_registry import SYSTEMIC_BLOCK_CODES as _HARD_STOP_CODES
from webui.error_registry import resolve_code
from webui.whitebox import ScrapeEvidence, WhiteboxWriteError
from webui.whitebox_rules import reduce_conclusion

from webui.logging_setup import get_logger

_logger = get_logger(__name__)





# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

# INTER_COMBO_DELAY 现从 advanced_settings 动态读取（默认 20~40s）。


def _canonical_source_code(code: object) -> str | None:
    raw_code = str(code or "").strip()
    if not raw_code:
        return None
    resolved = resolve_code(raw_code, default="source_unknown_error")
    return resolved if resolved.startswith("source_") else None


def _is_source_hard_stop(code: object) -> bool:
    resolved = _canonical_source_code(code)
    return bool(resolved and resolved in _HARD_STOP_CODES)


def _record_source_hard_stop_evidence(
    evidence: ScrapeEvidence,
    combos: list[dict],
    code: object,
    diagnostic: object = "",
    *,
    excluded_keys: set[str] | None = None,
    platform: str = "",
) -> tuple[str, str] | None:
    """Project one source block across all not-yet-completed scrape units."""
    canonical = _canonical_source_code(code)
    if canonical is None:
        return None
    reason = user_visible_failure_reason(canonical, diagnostic, platform)
    failure = SourceOutcome.failure(
        failed_code=canonical,
        failed_reason=reason,
    )
    excluded = {str(key) for key in (excluded_keys or set())}
    for combo in combos:
        key = str(
            combo.get("combo_key")
            or f"{combo.get('keyword', '')}|{combo.get('city', '')}"
        )
        if key not in excluded:
            evidence.failed(key, failure, reason=reason)
    return canonical, reason


def run_search(params: dict, source, *, pages: int = 3,
               progress=None, stop_event=None,
               artifact_dir=None, sleeper=None,
               skip_combos: set[str] | None = None,
               on_combo_done=None,
               execution_config=None,
               measurement_callback=None,
               on_page_completed=None,
               on_issue=None,
               resume_pages: dict[str, int] | None = None,
               resume_jobs: dict[str, list[dict]] | None = None,
               close_chrome_on_success: bool = True,
               task_id=None, task_event_store=None) -> dict:
    """Execute the multi-search pipeline and return merged, filtered jobs.

    ``source`` is a ``BossCdpSource`` (or compatible) providing ``preflight``
    and ``fetch_list``.  ``progress`` is an optional callable receiving a
    dict snapshot after each step.  ``stop_event`` (threading.Event-like)
    aborts the run when set.

    ``skip_combos``: 可选，已完成的组合键集合（格式 "keyword|city"），
    断点续抓时跳过这些组合不重复抓。
    ``on_combo_done``: 可选持久化回调，收到 ``(combo_key, jobs,
    completed_combos)``。回调失败表示进度无法安全保存，流程立即硬停止。
    ``on_page_completed``: 可选页级持久化回调，收到结构化页级事件。
    回调失败表示页级快照无法安全保存，流程立即硬停止。
    ``resume_pages``: 可选映射 ``{combo_key: 恢复起始页}``，用于断点续抓
    从已持久化的页级 checkpoint 继续。
    ``resume_jobs``: 可选映射 ``{combo_key: 已持久化岗位}``，恢复时补齐快照。

    ``execution_config``: SPEC011 T006 — 可选的不可变 ExecutionConfigSnapshot。
    提供时使用冻结的 ``inter_combo_delay``，不读取 advanced_settings.json。
    未提供时回退到运行时读取（向后兼容）。pages 不属于 execution_config。

    Returns ``{"ok": bool, "jobs": [...], "total_scraped": int,
    "total_matched": int, "combinations": int, "error": str,
    "completed_combos": [...]}``.
    """
    from webui import pipeline_exec as _facade
    platform = str(getattr(source, "platform", "boss") or "boss")
    plan_adapter = resolve_platform_input_adapter(platform)
    if sleeper is None:
        sleeper = time.sleep

    frozen_binding = None
    if execution_config is not None:
        # SPEC011 T006: 使用任务创建时冻结的配置快照，不读 JSON
        _base_delay = float(execution_config.inter_combo_delay)
        frozen_binding = bind_frozen_source_profile(
            source, execution_config, activate=_facade.set_active_cdp_data_dir,
        )
    else:
        _adv = _facade.load_advanced_settings()
        if pages == 3:  # 调用方未显式指定时用用户配置
            pages = int(_adv.get("pages") or 3)
        _base_delay = float(_adv.get("inter_combo_delay") or 30.0)
    _delay_range = (max(5, _base_delay - 5), _base_delay + 5)

    combos = expand_combinations(params)

    if not combos:
        return {"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": 0,
                "integrity": reduce_conclusion(None, []),
                "error": "没有可执行的搜索组合（关键词或城市为空）"}

    evidence = ScrapeEvidence(task_event_store, task_id, combos, pages)
    if evidence.startup_error is not None:
        return evidence.finish({"ok": False, "jobs": [], "total_scraped": 0,
                                "total_matched": 0, "combinations": len(combos),
                                "hard_stop": True, "hard_stop_code": "internal_error",
                                "error": f"任务证据白箱初始化失败（{type(evidence.startup_error).__name__}）"})
    _finish = evidence.finish

    if frozen_binding is not None and not frozen_binding.ok:
        source_failure = _record_source_hard_stop_evidence(
            evidence, combos,
            frozen_binding.error_code or "source_cdp_unavailable",
            frozen_binding.error,
            platform=platform,
        )
        hard_stop_code = frozen_binding.error_code or "source_cdp_unavailable"
        if source_failure is not None:
            hard_stop_code = source_failure[0]
        return _finish({
            "ok": False,
            "jobs": [],
            "total_scraped": 0,
            "total_matched": 0,
            "combinations": len(combos),
            "hard_stop": True,
            "hard_stop_code": hard_stop_code,
            "error": frozen_binding.error,
        })

    def emit(**kw):
        stage = str(kw.get("stage", ""))
        current = int(kw.get("current") or 0)
        total = int(kw.get("total") or 0)
        kw["overall_percent"] = _scrape_overall_percent(stage, current, total)
        page_progress = kw.pop("page_progress", None)
        if page_progress is not None:
            kw["overall_percent"] = _scrape_page_overall_percent(
                stage, current, total, float(page_progress),
            )
        else:
            kw["overall_percent"] = _scrape_overall_percent(stage, current, total)
        # 调用方传了具体 message（如"完成：抓取 X 条"）就优先用；没传才回退默认文案
        if not kw.get("message"):
            kw["message"] = _SCRAPE_STAGE_MESSAGES.get(stage, "")
        if progress is not None:
            try:
                progress(kw)
            except Exception:
                _logger.debug("进度回调执行失败（不阻断搜索主流程）", exc_info=True)


    # Auto-launch the debug Chrome if it isn't running, so the user is shown
    # the browser instead of a raw infrastructure error.
    emit(stage="ensure_chrome", message="检查并启动调试浏览器…")
    cdp_port = getattr(source, "cdp_port", None)
    chrome_ok, chrome_err = _facade.ensure_chrome_ready(
        cdp_port, minimize_after_launch=True,
    )
    if not chrome_ok:
        source_code, source_reason = _record_source_hard_stop_evidence(
            evidence, combos, "source_cdp_unavailable", chrome_err,
            platform=platform,
        )
        return _finish({"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": len(combos),
                "hard_stop": True,
                "hard_stop_code": source_code,
                "error": source_reason})

    # Preflight: CDP connection + current platform login.
    emit(stage="preflight", message="检查当前平台登录状态…")
    pre = source.preflight()
    if not pre.ok:
        result = {"ok": False, "jobs": [], "total_scraped": 0,
                  "total_matched": 0, "combinations": len(combos),
                  **classify_preflight_failure(pre)}
        if result.get("hard_stop"):
            source_failure = _record_source_hard_stop_evidence(
                evidence, combos, result.get("hard_stop_code"),
                result.get("error"), platform=platform,
            )
            if source_failure is not None:
                result["hard_stop_code"], result["error"] = source_failure
        return _finish(result)

    merged: dict[str, dict] = {}
    total_scraped = 0
    failed_combos = 0
    login_skipped = 0
    completed_combos: list[str] = []
    _skip = skip_combos or set()

    # Spec 038 FR-003/007：一个任务共享同一个 R1 轮询游标，关键词×城市
    # 组合只是待处理工作项；不能每个 combo 重新从账号 1 号开始。
    try:
        from webui.account_round_robin import make_list_robin
        list_robin = make_list_robin(
            source, run_id=str(task_id or getattr(source, "run_id", "") or ""),
            switch_event_store=task_event_store,
        )
    except Exception:
        _logger.debug("make_list_robin 初始化失败，回退 legacy 单源", exc_info=True)
        list_robin = None

    for idx, combo in enumerate(combos):
        stop_mode = stop_mode_for_event(stop_event)
        if stop_mode is not None:
            emit(
                stage=("paused" if stop_mode == STOP_MODE_PAUSE else "cancelled"),
                current=len(completed_combos),
                total=len(combos),
                message="运行已暂停" if stop_mode == STOP_MODE_PAUSE else "运行已取消",
            )
            break

        kw = combo["keyword"]
        city = combo["city"]
        display_city = str(combo.get("display_city") or city)
        source_filters = dict(combo.get("source_filters") or {})
        location = combo.get("location") or {}
        combo_key = str(combo.get("combo_key") or f"{kw}|{city}")

        # 断点续抓：跳过已完成的组合
        if combo_key in _skip:
            evidence.skip(combo_key)
            completed_combos.append(combo_key)
            continue

        resume_page = max(1, int((resume_pages or {}).get(combo_key, 1)))
        if resume_page > pages:
            # 页级 checkpoint 已越过目标页数：该组合已抓满，不再用非法 start_page 续抓。
            evidence.units[combo_key].update(status="unverifiable", evidence_complete=False,
                                             error_code="page_evidence_missing")
            completed_combos.append(combo_key)
            continue

        emit(stage="searching", current=len(completed_combos), total=len(combos),
             keyword=kw, city=display_city,
             message=f"正在搜索 [{idx + 1}/{len(combos)}] {kw} · {display_city}")

        plan_item = plan_adapter.build_plan_item(
            keyword=kw,
            city=city,
            location=location,
            combo_key=combo_key,
            target_pages=pages,
            start_page=resume_page,
            list_output_path=_combo_output_path(artifact_dir, combo_key),
            source_filters=source_filters,
            existing_jobs=(resume_jobs or {}).get(combo_key),
        )
        evidence.unit_started(combo_key, pages, resume_page)
        last_page_ratio = 0.0
        page_progress_seen = False

        def _page_completed(event: dict, combo_key=combo_key, kw=kw, city=display_city):
            nonlocal last_page_ratio, page_progress_seen
            page_progress_seen = True
            event = dict(event or {})
            event.setdefault("combo_key", combo_key)
            event.setdefault("keyword", kw)
            event.setdefault("city", city)
            page = max(0, int(event.get("page") or 0))
            target = max(1, int(event.get("target_pages") or pages))
            last_page_ratio = min(1.0, max(0.0, page / target))
            # 断点续抓：页级事件推进本组合的恢复起点与已抓岗位快照，
            # 浏览器失联自动重启后从断点继续，不重抓已完成页。
            if resume_pages is not None:
                resume_pages[combo_key] = max(1, int(event.get("resume_page") or page + 1))
            if resume_jobs is not None:
                snapshot = event.get("jobs_snapshot")
                if isinstance(snapshot, list):
                    resume_jobs[combo_key] = list(snapshot)
            try:
                evidence.page(combo_key, event, target)
            except WhiteboxWriteError as exc:
                emit(stage="hard_stop", current=len(completed_combos), total=len(combos),
                     keyword=kw, city=display_city, failed_code="internal_error",
                     message="白箱页级证据写入失败，任务暂停")
                raise PageEventPersistenceError(str(exc)) from exc
            if on_page_completed is not None:
                try:
                    on_page_completed(event)
                except PageEventPersistenceError:
                    emit(
                        stage="hard_stop", current=len(completed_combos), total=len(combos),
                        keyword=kw, city=display_city, failed_code="internal_error",
                        message="页级快照持久化失败，任务暂停",
                    )
                    raise
                except _PIPELINE_OPERATION_ERRORS as exc:
                    emit(
                        stage="hard_stop", current=len(completed_combos), total=len(combos),
                        keyword=kw, city=display_city, failed_code="internal_error",
                        message="页级快照持久化失败，任务暂停",
                    )
                    raise PageEventPersistenceError(str(exc)) from exc
            emit(
                stage="page_done", current=len(completed_combos), total=len(combos),
                page=page, target_pages=target, page_progress=last_page_ratio,
                keyword=kw, city=display_city, scraped=int(event.get("jobs_count") or 0),
                message=(f"正在搜索 {kw} · {display_city}：第 {max(1, page)}/{target} 页，"
                         f"已抓 {int(event.get('jobs_count') or 0)} 条"),
            )

        recovery = BrowserRecovery(
            cdp_port=cdp_port,
            platform=platform,
            task_id=str(task_id or ""), unit_key=combo_key,
            store=task_event_store,
            on_restart=lambda: emit(
                stage="ensure_chrome", current=len(completed_combos), total=len(combos),
                keyword=kw, city=display_city,
                message="检测到浏览器失联，正在自动重启并续抓…",
            ),
        )
        def _fetch_list_once():
            if list_robin is not None:
                # Spec 038 B091 R1 轮询分摊：跨账号按配额拆子范围抓页
                return list_robin.fetch_list(source, plan_item, on_page_completed=_page_completed)
            return source.fetch_list(plan_item, on_page_completed=_page_completed)

        _skipped_login_combo = [False]

        def _notify_combo_issue(entry: dict) -> None:
            if on_issue is None:
                return
            try:
                on_issue(combo_key, entry)
            except Exception:
                _logger.debug("问题上报回调失败（不阻断搜索主流程）", exc_info=True)


        def _secondary_login_probe():
            recheck = getattr(source, "recheck_login", None)
            if callable(recheck):
                return recheck()
            return source.preflight()

        def _probe_passed(probe) -> bool:
            return probe.ok or probe.failed_code not in (
                "source_login_required", "source_blocked", "source_cdp_unavailable",
            )

        def _recheck_login_combo(outcome):
            """疑似登录失效：独立复核一次，通过则重试本组合，否则跳过。"""
            probe = _secondary_login_probe()
            if _probe_passed(probe):
                _notify_combo_issue({
                    "event": "login_recheck_passed_retry",
                    "probe": probe.failed_code or "logged_in",
                    "detail": outcome.failed_reason or outcome.safe_log or "",
                })
                emit(stage="waiting", current=len(completed_combos), total=len(combos),
                     keyword=kw, city=display_city,
                     message="登录复核通过（疑似误报），重试本组合…")
                retried = _fetch_list_once()
                if retried.ok or retried.failed_code != "source_login_required":
                    return retried
                _notify_combo_issue({
                    "event": "login_required_confirmed_after_retry",
                    "probe": probe.failed_code or "logged_in",
                    "detail": retried.failed_reason or retried.safe_log or "",
                })
                _skipped_login_combo[0] = True
                return retried
            _notify_combo_issue({
                "event": "login_required_confirmed_skip",
                "probe": probe.failed_code or "unknown",
                "detail": outcome.failed_reason or outcome.safe_log or "",
            })
            _skipped_login_combo[0] = True
            return outcome

        try:
            outcome = _fetch_list_once()
            if not outcome.ok and outcome.failed_code == "source_login_required":
                outcome = _recheck_login_combo(outcome)
        except PageEventPersistenceError as exc:
            evidence.incomplete(combo_key, "页级快照持久化失败")
            return _finish({
                "ok": False, "jobs": list(merged.values()),
                "total_scraped": total_scraped, "total_matched": len(merged),
                "combinations": len(combos), "completed_combos": completed_combos,
                "hard_stop": True, "hard_stop_code": "internal_error",
                "error": f"页级快照持久化失败（{type(exc.__cause__).__name__}），任务已暂停",
            })
        except _PIPELINE_OPERATION_ERRORS as exc:
            emit(
                stage="hard_stop", current=len(completed_combos), total=len(combos),
                keyword=kw, city=display_city, failed_code="internal_error",
                message="抓取执行失败，任务暂停",
            )
            evidence.incomplete(combo_key, "抓取执行失败", "internal_error")
            return _finish({
                "ok": False, "jobs": list(merged.values()),
                "total_scraped": total_scraped, "total_matched": len(merged),
                "combinations": len(combos), "completed_combos": completed_combos,
                "hard_stop": True, "hard_stop_code": "internal_error",
                "error": f"抓取执行失败（{type(exc).__name__}），任务已暂停",
            })
        if not outcome.ok and recovery.is_browser_lost(outcome.failed_code):
            restart_ok, restart_err = recovery.try_restart()
            if restart_ok:
                resume_page = max(1, int((resume_pages or {}).get(combo_key, 1)))
                plan_adapter.apply_resume_fields(
                    plan_item,
                    start_page=min(resume_page, pages),
                    existing_jobs=(resume_jobs or {}).get(combo_key),
                )
                try:
                    outcome = _fetch_list_once()
                except PageEventPersistenceError as exc:
                    evidence.incomplete(combo_key, "页级快照持久化失败")
                    return _finish({
                        "ok": False, "jobs": list(merged.values()),
                        "total_scraped": total_scraped, "total_matched": len(merged),
                        "combinations": len(combos), "completed_combos": completed_combos,
                        "hard_stop": True, "hard_stop_code": "internal_error",
                        "error": f"页级快照持久化失败（{type(exc.__cause__).__name__}），任务已暂停",
                    })
                except _PIPELINE_OPERATION_ERRORS as exc:
                    emit(
                        stage="hard_stop", current=len(completed_combos), total=len(combos),
                        keyword=kw, city=display_city, failed_code="internal_error",
                        message="抓取执行失败，任务暂停",
                    )
                    evidence.incomplete(combo_key, "抓取执行失败", "internal_error")
                    return _finish({
                        "ok": False, "jobs": list(merged.values()),
                        "total_scraped": total_scraped, "total_matched": len(merged),
                        "combinations": len(combos), "completed_combos": completed_combos,
                        "hard_stop": True, "hard_stop_code": "internal_error",
                        "error": f"抓取执行失败（{type(exc).__name__}），任务已暂停",
                    })
                if outcome.ok:
                    recovery.mark_progress()
                elif recovery.is_browser_lost(outcome.failed_code):
                    source_code, source_reason = _record_source_hard_stop_evidence(
                        evidence, combos, "source_cdp_unavailable",
                        outcome.failed_reason or outcome.safe_log or "",
                        excluded_keys=set(completed_combos), platform=platform,
                    )
                    label = failed_code_label(source_code, platform)
                    emit(stage="hard_stop", current=len(completed_combos), total=len(combos),
                         keyword=kw, city=display_city, failed_code=source_code,
                         message=f"自动重启后仍失联：{label}，任务暂停")
                    return _finish({"ok": False, "jobs": list(merged.values()),
                            "total_scraped": total_scraped, "total_matched": len(merged),
                            "combinations": len(combos), "completed_combos": completed_combos,
                            "hard_stop": True, "hard_stop_code": source_code,
                            "error": f"自动重启后仍失联：{label}，任务暂停"})
            else:
                source_code, cdp_reason = _record_source_hard_stop_evidence(
                    evidence, combos, "source_cdp_unavailable", restart_err,
                    excluded_keys=set(completed_combos), platform=platform,
                )
                emit(stage="hard_stop", current=len(completed_combos), total=len(combos),
                     keyword=kw, city=display_city, failed_code=source_code,
                     message=cdp_reason)
                return _finish({"ok": False, "jobs": list(merged.values()),
                        "total_scraped": total_scraped, "total_matched": len(merged),
                        "combinations": len(combos), "completed_combos": completed_combos,
                        "hard_stop": True, "hard_stop_code": source_code,
                        "error": cdp_reason})
        if not outcome.ok:
            _failure_reason = user_visible_failure_reason(
                outcome.failed_code,
                outcome.failed_reason or (
                    outcome.safe_log.split("reason=", 1)[1]
                    if outcome.safe_log and "reason=" in outcome.safe_log else ""
                ),
                platform,
            )
            source_hard_stop = (
                not _skipped_login_combo[0]
                and _is_source_hard_stop(outcome.failed_code)
            )
            if source_hard_stop:
                source_failure = _record_source_hard_stop_evidence(
                    evidence, combos, outcome.failed_code, _failure_reason,
                    excluded_keys=set(completed_combos), platform=platform,
                )
                if source_failure is None:
                    source_hard_stop = False
                else:
                    source_code, _source_reason = source_failure
            if not source_hard_stop:
                evidence.failed(
                    combo_key, outcome,
                    skipped=_skipped_login_combo[0], reason=_failure_reason,
                )
            # 二次复核确认登录失效：跳过本组合并记录原因，不整场暂停
            if _skipped_login_combo[0]:
                label = failed_code_label(outcome.failed_code, platform)
                emit(stage="combo_failed", current=len(completed_combos), total=len(combos),
                     keyword=kw, city=display_city, failed_code=outcome.failed_code,
                     combo_key=combo_key,
                     message=f"已跳过本组合（{label}，二次复核仍登录失效），原因已记录")
                failed_combos += 1
                login_skipped += 1
            elif source_hard_stop:
                # source hard stop 的主证据已按 canonical code 投影到所有
                # 未完成组合；顶层文案继续沿用当前阻断提示。
                label = failed_code_label(source_code, platform)
                emit(stage="hard_stop", current=len(completed_combos), total=len(combos),
                     keyword=kw, city=display_city, failed_code=source_code,
                     combo_key=combo_key,
                     message=f"系统性阻断：{label}，任务暂停")
                return _finish({"ok": False, "jobs": list(merged.values()),
                        "total_scraped": total_scraped, "total_matched": len(merged),
                        "combinations": len(combos), "completed_combos": completed_combos,
                        "hard_stop": True, "hard_stop_code": source_code,
                        "error": f"系统性阻断：{label}"})
            elif (resolve_code(outcome.failed_code) in _HARD_STOP_CODES
                  if outcome.failed_code else False):
                # 系统性阻断（验证码/IP风控/CDP不可用等）：立即停止，不继续跑其他组合
                label = failed_code_label(outcome.failed_code, platform)
                emit(stage="hard_stop", current=len(completed_combos), total=len(combos),
                     keyword=kw, city=display_city, failed_code=outcome.failed_code,
                     combo_key=combo_key,
                     message=f"系统性阻断：{label}，任务暂停")
                return _finish({"ok": False, "jobs": list(merged.values()),
                        "total_scraped": total_scraped, "total_matched": len(merged),
                        "combinations": len(combos), "completed_combos": completed_combos,
                        "hard_stop": True, "hard_stop_code": outcome.failed_code,
                        "error": f"系统性阻断：{label}"})
            else:
                failed_combos += 1
                # source 失败使用中央用户文案；非 source 失败保留诊断。
                label = failed_code_label(outcome.failed_code, platform)
                issue_reason = _failure_reason or label
                detail = (
                    f"（{issue_reason}）"
                    if issue_reason and issue_reason != label else ""
                )
                # 016：软失败不暂停，但必须按组合落库留痕（combo_issue 事件），
                # 供任务详情回查；不写任何账号级持久状态。
                _notify_combo_issue({
                    "kind": "combo_failed",
                    "failed_code": str(outcome.failed_code or "source_unknown_error"),
                    "reason": issue_reason[:200],
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                })
                emit(stage="combo_failed", current=len(completed_combos), total=len(combos),
                     keyword=kw, city=display_city, failed_code=outcome.failed_code,
                     **({"page_progress": last_page_ratio} if page_progress_seen else {}),
                     message=f"组合失败：{label}{detail}")
        else:
            total_scraped += len(outcome.jobs)
            evidence.completed(combo_key, outcome)
            completed_combos.append(combo_key)
            for job in outcome.jobs:
                jid = (job.get("platform_job_id") or job.get("job_id") or job.get("source_url") or "")
                if jid and jid not in merged:
                    merged[jid] = job
            if on_combo_done is not None:
                try:
                    on_combo_done(combo_key, list(outcome.jobs), list(completed_combos), outcome=outcome)
                except _PIPELINE_OPERATION_ERRORS as exc:
                    emit(
                        stage="hard_stop", current=len(completed_combos), total=len(combos),
                        keyword=kw, city=display_city, failed_code="internal_error",
                        message="组合结果持久化失败，任务暂停",
                    )
                    evidence.incomplete(combo_key, "组合结果持久化失败")
                    return _finish({
                        "ok": False,
                        "jobs": list(merged.values()),
                        "total_scraped": total_scraped,
                        "total_matched": len(merged),
                        "combinations": len(combos),
                        "completed_combos": completed_combos,
                        "hard_stop": True,
                        "hard_stop_code": "internal_error",
                        "error": f"组合结果持久化失败（{type(exc).__name__}），任务已暂停",
                    })
            emit(stage="combo_done", current=len(completed_combos), total=len(combos),
                 keyword=kw, city=display_city, scraped=len(outcome.jobs),
                 **({"page_progress": 0} if page_progress_seen else {}),
                 merged=len(merged),
                 message=f"完成 {kw} · {display_city}：本页 {len(outcome.jobs)} 条，累计去重 {len(merged)} 条")
            # T018: 记录 batch 事件（combo 输入输出数量）
            if measurement_callback is not None:
                try:
                    measurement_callback("batch", "list", 0,
                                         counts={"input_count": pages,
                                                 "output_count": len(outcome.jobs),
                                                 "batch_index": idx + 1})
                except Exception:
                    _logger.debug("观测回调执行失败（不阻断搜索主流程）", exc_info=True)


        # Delay between combinations (not after the last one).
        if idx < len(combos) - 1:
            if stop_mode_for_event(stop_event) is not None:
                break
            delay = random.uniform(*_delay_range)
            emit(stage="waiting", current=len(completed_combos), total=len(combos),
                 **({"page_progress": 0} if page_progress_seen else {}),
                 wait_seconds=int(delay),
                 message=f"防限流等待 {delay:.0f}s 后搜索下一个组合…")
            _t0_wait = time.time()
            sleeper(delay)
            # T018: 记录 wait 事件（防限流冷却时间计入总耗时）
            if measurement_callback is not None:
                try:
                    measurement_callback("wait", "list",
                                         int((time.time() - _t0_wait) * 1000),
                                         counts={"combo_index": idx + 1})
                except Exception:
                    _logger.debug("观测回调执行失败（不阻断搜索主流程）", exc_info=True)


    # 广搜策略：不做本地硬筛选，全量返回，筛选交给后续 AI 步骤。
    all_jobs = list(merged.values())

    final_stop_mode = stop_mode_for_event(stop_event)
    if final_stop_mode is not None:
        emit(
            stage=("paused" if final_stop_mode == STOP_MODE_PAUSE else "cancelled"),
            current=len(completed_combos),
            total=len(combos),
            message=(
                "运行已暂停，已保存当前断点"
                if final_stop_mode == STOP_MODE_PAUSE else "运行已取消"
            ),
        )
        stop_payload = {
            "ok": False,
            "jobs": all_jobs,
            "total_scraped": total_scraped,
            "total_matched": len(all_jobs),
            "combinations": len(combos),
            "completed_combos": completed_combos,
            "stop_mode": final_stop_mode,
            "error": (
                "用户已暂停，结果已保留"
                if final_stop_mode == STOP_MODE_PAUSE else "运行已取消"
            ),
        }
        if final_stop_mode == STOP_MODE_PAUSE:
            return evidence.pause(stop_payload)
        return _finish(stop_payload, lifecycle_end="cancelled")

    # 哨兵第三层：所有非跳过组合全失败 → 中性提示，不冒充风控
    ran_combos = len(combos) - len(_skip)
    if failed_combos > 0 and total_scraped == 0 and ran_combos > 0:
        warning_message = (
            f"因登录失效跳过了全部 {login_skipped} 个组合，请确认登录态后重试"
            if login_skipped else "所有组合均失败，请检查浏览器登录、网络或平台提示后重试。")
        emit(stage="risk_warning", current=len(completed_combos), total=len(combos),
             message=warning_message)
        return _finish({"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": len(combos),
                "completed_combos": completed_combos,
                "error": (f"因登录失效跳过了全部 {login_skipped} 个组合，请确认登录态后重试" if login_skipped
                          else "所有搜索组合均失败，请检查浏览器登录、网络或平台提示后重试")})

    # 有数据才关浏览器（任务完成）；全失败则保留窗口供用户排查/重试。
    if total_scraped > 0 and close_chrome_on_success:
        emit(stage="closing_chrome", current=len(completed_combos), total=len(combos),
             message="正在关闭调试浏览器…")
        _facade.close_debug_chrome(cdp_port)
    emit(stage="done", total_scraped=total_scraped, total_matched=len(all_jobs),
         message=f"完成：抓取 {total_scraped} 条，去重 {len(all_jobs)} 条")

    return _finish({"ok": True, "jobs": all_jobs, "total_scraped": total_scraped,
            "total_matched": len(all_jobs), "combinations": len(combos),
            "completed_combos": completed_combos,
            "error": ""})
