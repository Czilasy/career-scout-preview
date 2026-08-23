"""run_search：关键词×城市组合抓取主流程（021 B7 自 pipeline_exec.py 搬运）。

ensure_chrome_ready / close_debug_chrome / load_advanced_settings 经门面动态取用。
"""

from __future__ import annotations

import random
import time
from datetime import datetime

from webui.pipeline_exec_artifacts import _combo_hash, _combo_output_path
from webui.pipeline_exec_filters import expand_combinations
from webui.pipeline_exec_settings import _PIPELINE_OPERATION_ERRORS
from webui.pipeline_exec_status import (
    _SCRAPE_STAGE_MESSAGES,
    _scrape_overall_percent,
    _scrape_page_overall_percent,
    failed_code_label,
)
from webui.source import PageEventPersistenceError
from webui.browser_recovery import BrowserRecovery
from webui.error_registry import SYSTEMIC_BLOCK_CODES as _HARD_STOP_CODES
from webui.error_registry import resolve_code




# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

# INTER_COMBO_DELAY 现从 advanced_settings 动态读取（默认 20~40s）。


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
               close_chrome_on_success: bool = True) -> dict:
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
    if sleeper is None:
        sleeper = time.sleep

    if execution_config is not None:
        # SPEC011 T006: 使用任务创建时冻结的配置快照，不读 JSON
        _base_delay = float(execution_config.inter_combo_delay)
    else:
        _adv = _facade.load_advanced_settings()
        if pages == 3:  # 调用方未显式指定时用用户配置
            pages = int(_adv.get("pages") or 3)
        _base_delay = float(_adv.get("inter_combo_delay") or 30.0)
    _delay_range = (max(5, _base_delay - 5), _base_delay + 5)

    combos = expand_combinations(params)

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
                pass

    if not combos:
        return {"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": 0,
                "error": "没有可执行的搜索组合（关键词或城市为空）"}

    # Auto-launch the debug Chrome if it isn't running, so the user is shown
    # the browser instead of a raw infrastructure error.
    emit(stage="ensure_chrome", message="检查并启动调试浏览器…")
    platform = str(getattr(source, "platform", "boss") or "boss")
    cdp_port = getattr(source, "cdp_port", None)
    chrome_ok, chrome_err = _facade.ensure_chrome_ready(
        cdp_port, minimize_after_launch=True,
    )
    if not chrome_ok:
        return {"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": len(combos),
                "error": f"调试浏览器未就绪：{chrome_err}。"
                         "若始终无法启动，请手动运行 scripts/boss_cdp_raw.py --setup-chrome 后重试。"}

    # Preflight: CDP connection + BOSS login.
    emit(stage="preflight", message=f"检查 {('BOSS直聘' if platform == 'boss' else '智联招聘')} 登录状态…")
    pre = source.preflight()
    if not pre.ok:
        if pre.failed_code == "source_login_required":
            msg = ("浏览器已打开，但还未登录 BOSS。请在浏览器中登录 zhipin.com，登录后重新继续。" if platform == "boss"
                   else "浏览器已打开，但还未登录智联招聘。请在浏览器中登录 zhaopin.com，登录后重新继续。")
        else:
            msg = f"预检失败：{pre.failed_code}"
        return {"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": len(combos),
                "error": msg}

    merged: dict[str, dict] = {}
    total_scraped = 0
    failed_combos = 0
    login_skipped = 0
    completed_combos: list[str] = []
    _skip = skip_combos or set()

    for idx, combo in enumerate(combos):
        if stop_event is not None and stop_event.is_set():
            emit(stage="cancelled", current=len(completed_combos), total=len(combos),
                 message="运行已取消")
            break

        kw = combo["keyword"]
        city = combo["city"]
        display_city = str(combo.get("display_city") or city)
        source_filters = dict(combo.get("source_filters") or {})
        location = combo.get("location") or {}
        combo_key = str(combo.get("combo_key") or f"{kw}|{city}")

        # 断点续抓：跳过已完成的组合
        if combo_key in _skip:
            completed_combos.append(combo_key)
            continue

        resume_page = max(1, int((resume_pages or {}).get(combo_key, 1)))
        if resume_page > pages:
            # 页级 checkpoint 已越过目标页数：该组合已抓满，不再用非法 start_page 续抓。
            completed_combos.append(combo_key)
            continue

        emit(stage="searching", current=len(completed_combos), total=len(combos),
             keyword=kw, city=display_city,
             message=f"正在搜索 [{idx + 1}/{len(combos)}] {kw} · {display_city}")

        if platform == "zhilian":
            from webui.location_scope import build_zhilian_city_snapshot
            from webui.platforms import resolve_platform_city
            from webui.source import _zhilian_input_hash
            city_entry = resolve_platform_city("zhilian", city)
            if location:
                city_snapshot = build_zhilian_city_snapshot(location, city_entry)
                route_city_code = str(location.get("city_code") or city_entry.platform_code)
            else:
                city_snapshot = {
                    "name": city_entry.name,
                    "label": city_entry.label,
                    "platform_code": city_entry.platform_code,
                    "mapping_version": city_entry.mapping_version,
                }
                route_city_code = city_entry.platform_code
            plan_item = {
                "platform": "zhilian",
                "keyword": kw,
                "city": city_snapshot,
                "combo_key": combo_key,
                "target_pages": pages,
                "input_hash": _zhilian_input_hash({
                    "platform": "zhilian", "keyword": kw,
                    "city": city_snapshot, "target_pages": pages,
                    "route_city_code": route_city_code,
                }),
                "list_output_path": _combo_output_path(artifact_dir, combo_key),
                "start_page": resume_page,
                "existing_jobs": list((resume_jobs or {}).get(combo_key) or []),
                "route_city_code": route_city_code,
            }
        else:
            plan_item = {
                "keyword": kw,
                "city": city,
                "source_filters": source_filters,
                "combo_key": combo_key,
                "target_pages": pages,
                "input_hash": _combo_hash(kw, city, pages, source_filters=source_filters),
                "list_output_path": _combo_output_path(artifact_dir, combo_key),
                "start_page": resume_page,
            }
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
            on_restart=lambda: emit(
                stage="ensure_chrome", current=len(completed_combos), total=len(combos),
                keyword=kw, city=display_city,
                message="检测到浏览器失联，正在自动重启并续抓…",
            ),
        )

        def _fetch_list_once():
            return source.fetch_list(plan_item, on_page_completed=_page_completed)

        _skipped_login_combo = [False]

        def _notify_combo_issue(entry: dict) -> None:
            if on_issue is None:
                return
            try:
                on_issue(combo_key, entry)
            except Exception:
                pass

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
            return {
                "ok": False, "jobs": list(merged.values()),
                "total_scraped": total_scraped, "total_matched": len(merged),
                "combinations": len(combos), "completed_combos": completed_combos,
                "hard_stop": True, "hard_stop_code": "internal_error",
                "error": f"页级快照持久化失败（{type(exc.__cause__).__name__}），任务已暂停",
            }
        except _PIPELINE_OPERATION_ERRORS as exc:
            emit(
                stage="hard_stop", current=len(completed_combos), total=len(combos),
                keyword=kw, city=display_city, failed_code="internal_error",
                message="抓取执行失败，任务暂停",
            )
            return {
                "ok": False, "jobs": list(merged.values()),
                "total_scraped": total_scraped, "total_matched": len(merged),
                "combinations": len(combos), "completed_combos": completed_combos,
                "hard_stop": True, "hard_stop_code": "internal_error",
                "error": f"抓取执行失败（{type(exc).__name__}），任务已暂停",
            }
        if not outcome.ok and recovery.is_browser_lost(outcome.failed_code):
            restart_ok, restart_err = recovery.try_restart()
            if restart_ok:
                resume_page = max(1, int((resume_pages or {}).get(combo_key, 1)))
                plan_item["start_page"] = min(resume_page, pages)
                if platform == "zhilian":
                    plan_item["existing_jobs"] = list((resume_jobs or {}).get(combo_key) or [])
                try:
                    outcome = _fetch_list_once()
                except PageEventPersistenceError as exc:
                    return {
                        "ok": False, "jobs": list(merged.values()),
                        "total_scraped": total_scraped, "total_matched": len(merged),
                        "combinations": len(combos), "completed_combos": completed_combos,
                        "hard_stop": True, "hard_stop_code": "internal_error",
                        "error": f"页级快照持久化失败（{type(exc.__cause__).__name__}），任务已暂停",
                    }
                except _PIPELINE_OPERATION_ERRORS as exc:
                    emit(
                        stage="hard_stop", current=len(completed_combos), total=len(combos),
                        keyword=kw, city=display_city, failed_code="internal_error",
                        message="抓取执行失败，任务暂停",
                    )
                    return {
                        "ok": False, "jobs": list(merged.values()),
                        "total_scraped": total_scraped, "total_matched": len(merged),
                        "combinations": len(combos), "completed_combos": completed_combos,
                        "hard_stop": True, "hard_stop_code": "internal_error",
                        "error": f"抓取执行失败（{type(exc).__name__}），任务已暂停",
                    }
                if outcome.ok:
                    recovery.mark_progress()
                elif recovery.is_browser_lost(outcome.failed_code):
                    label = failed_code_label(outcome.failed_code, platform)
                    emit(stage="hard_stop", current=len(completed_combos), total=len(combos),
                         keyword=kw, city=display_city, failed_code=outcome.failed_code,
                         message=f"自动重启后仍失联：{label}，任务暂停")
                    return {"ok": False, "jobs": list(merged.values()),
                            "total_scraped": total_scraped, "total_matched": len(merged),
                            "combinations": len(combos), "completed_combos": completed_combos,
                            "hard_stop": True, "hard_stop_code": outcome.failed_code,
                            "error": f"自动重启后仍失联：{label}，任务暂停"}
            else:
                label = failed_code_label("source_cdp_unavailable", platform)
                emit(stage="hard_stop", current=len(completed_combos), total=len(combos),
                     keyword=kw, city=display_city, failed_code="source_cdp_unavailable",
                     message=f"调试浏览器自动重启失败：{restart_err}")
                return {"ok": False, "jobs": list(merged.values()),
                        "total_scraped": total_scraped, "total_matched": len(merged),
                        "combinations": len(combos), "completed_combos": completed_combos,
                        "hard_stop": True, "hard_stop_code": "source_cdp_unavailable",
                        "error": f"调试浏览器自动重启失败：{restart_err}，任务暂停"}
        if not outcome.ok:
            # 二次复核确认登录失效：跳过本组合并记录原因，不整场暂停
            if _skipped_login_combo[0]:
                label = failed_code_label(outcome.failed_code, platform)
                emit(stage="combo_failed", current=len(completed_combos), total=len(combos),
                     keyword=kw, city=display_city, failed_code=outcome.failed_code,
                     combo_key=combo_key,
                     message=f"已跳过本组合（{label}，二次复核仍登录失效），原因已记录")
                failed_combos += 1
                login_skipped += 1
            elif (resolve_code(outcome.failed_code) in _HARD_STOP_CODES
                  if outcome.failed_code else False):
                # 系统性阻断（验证码/IP风控/CDP不可用等）：立即停止，不继续跑其他组合
                label = failed_code_label(outcome.failed_code, platform)
                emit(stage="hard_stop", current=len(completed_combos), total=len(combos),
                     keyword=kw, city=display_city, failed_code=outcome.failed_code,
                     combo_key=combo_key,
                     message=f"系统性阻断：{label}，任务暂停")
                return {"ok": False, "jobs": list(merged.values()),
                        "total_scraped": total_scraped, "total_matched": len(merged),
                        "combinations": len(combos), "completed_combos": completed_combos,
                        "hard_stop": True, "hard_stop_code": outcome.failed_code,
                        "error": f"系统性阻断：{label}"}
            else:
                failed_combos += 1
                # 从 safe_log 提取 reason= 后的可读原因
                _reason = ""
                if outcome.safe_log and "reason=" in outcome.safe_log:
                    _reason = outcome.safe_log.split("reason=", 1)[1]
                detail = f"（{_reason}）" if _reason else ""
                label = failed_code_label(outcome.failed_code, platform)
                # 016：软失败不暂停，但必须按组合落库留痕（combo_issue 事件），
                # 供任务详情回查；不写任何账号级持久状态。
                _notify_combo_issue({
                    "kind": "combo_failed",
                    "failed_code": str(outcome.failed_code or "source_unknown_error"),
                    "reason": (detail.strip("（）") or label)[:200],
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                })
                emit(stage="combo_failed", current=len(completed_combos), total=len(combos),
                     keyword=kw, city=display_city, failed_code=outcome.failed_code,
                     **({"page_progress": last_page_ratio} if page_progress_seen else {}),
                     message=f"组合失败：{label}{detail}")
        else:
            total_scraped += len(outcome.jobs)
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
                    return {
                        "ok": False,
                        "jobs": list(merged.values()),
                        "total_scraped": total_scraped,
                        "total_matched": len(merged),
                        "combinations": len(combos),
                        "completed_combos": completed_combos,
                        "hard_stop": True,
                        "hard_stop_code": "internal_error",
                        "error": f"组合结果持久化失败（{type(exc).__name__}），任务已暂停",
                    }
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
                    pass

        # Delay between combinations (not after the last one).
        if idx < len(combos) - 1:
            if stop_event is not None and stop_event.is_set():
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
                    pass

    # 广搜策略：不做本地硬筛选，全量返回，筛选交给后续 AI 步骤。
    all_jobs = list(merged.values())

    # 哨兵第三层：所有非跳过组合全失败 → 中性提示，不冒充风控
    ran_combos = len(combos) - len(_skip)
    if failed_combos > 0 and total_scraped == 0 and ran_combos > 0:
        warning_message = (
            f"因登录失效跳过了全部 {login_skipped} 个组合，请确认登录态后重试"
            if login_skipped else "所有组合均失败，请检查浏览器登录、网络或平台提示后重试。")
        emit(stage="risk_warning", current=len(completed_combos), total=len(combos),
             message=warning_message)
        return {"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": len(combos),
                "completed_combos": completed_combos,
                "error": (f"因登录失效跳过了全部 {login_skipped} 个组合，请确认登录态后重试" if login_skipped
                          else "所有搜索组合均失败，请检查浏览器登录、网络或平台提示后重试")}

    # 有数据才关浏览器（任务完成）；全失败则保留窗口供用户排查/重试。
    if total_scraped > 0 and close_chrome_on_success:
        emit(stage="closing_chrome", current=len(completed_combos), total=len(combos),
             message="正在关闭调试浏览器…")
        _facade.close_debug_chrome(cdp_port)
    emit(stage="done", total_scraped=total_scraped, total_matched=len(all_jobs),
         message=f"完成：抓取 {total_scraped} 条，去重 {len(all_jobs)} 条")

    return {"ok": True, "jobs": all_jobs, "total_scraped": total_scraped,
            "total_matched": len(all_jobs), "combinations": len(combos),
            "completed_combos": completed_combos,
            "error": ""}
