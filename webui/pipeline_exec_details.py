"""fetch_job_details：批量详情抓取（021 B7 自 pipeline_exec.py 搬运）。"""

from __future__ import annotations

import random
import time

from webui.pipeline_exec_settings import _PIPELINE_OPERATION_ERRORS
from webui.pipeline_exec_status import (
    _classify_detail_batch_exception,
    failed_code_label,
    taxonomy_reason,
)
from webui.browser_recovery import BrowserRecovery
from webui.error_registry import SYSTEMIC_BLOCK_CODES as _HARD_STOP_CODES
from webui.error_registry import resolve_code




def fetch_job_details(jobs, source, *, artifact_dir=None, progress=None,
                      stop_event=None, completed_job_ids=None,
                      execution_config=None,
                      measurement_callback=None,
                      emit_terminal_events=True,
                      guard=None, batch_key_prefix=None,
                      simulation_mode=None):
    """对一批岗位批量抓 JD（调用方需先确保 Chrome 就绪）。

    Spec 007 ⑧：改用 fetch_details_batch（≤5 一批）走 --enable-parallel 常驻 tab 池，
    替代旧的逐条 fetch_detail。单条失败不中断（该岗位 jd 留空，前端可保留按需加载兜底）。
    ``progress(done, total)`` 按累计完成数回报。

    ``stop_event``: 可选取消信号，每批前检查，命中即停（剩余岗位 jd 留空）。
    ``completed_job_ids``: 可选，已抓过 JD 的 job_id 集合（断点续抓），跳过不重复抓，
    其 jd 保留原值。

    ``execution_config``: SPEC011 T006 — 可选的不可变 ExecutionConfigSnapshot。
    提供时使用冻结的 detail_* 字段，不读取 advanced_settings.json。
    未提供时回退到运行时读取（向后兼容）。

    ``guard`` / ``batch_key_prefix``: 022 卡死防护 — 传入 PipelineGuard 时对该函数
    内的每个抓取批次做 300s 无产出卡死判定、自动重抓（最多 3 次）、第 3 次失败分流
    收场。batch_key_prefix 用于生成跨 chunk 唯一的批次键（run_jd_stage 传 task_id+chunk）。

    返回 {"jobs": 带 jd 的岗位列表, "hard_stop": bool, "hard_stop_code": str|None,
           "stopped": bool, "fetched": int}：
    - hard_stop=True：批内出现源级硬信号（登录失效/验证码/限流/IP 风控），已停止
      后续批次（继续抓只会抓空气还装完成），调用方应停并向用户上报。
      hard_stop_code 为具体触发的 failed_code（对应 _FAILED_CODE_LABELS）。
    - stopped=True：用户取消导致提前停止。
    - fetched：本次实际抓到 JD 的条数。
    - stall_divert：卡死 3 次失败后的分流结果（"environment" | "sporadic" | None），
      调用方据此暂停（环境级）或跳过进待确认（偶发）后继续。
    - stall_code / stall_attempts：环境级分流时的失败码与实际尝试次数。
    """
    from webui import pipeline_exec as _facade
    import os
    if artifact_dir is None:
        artifact_dir = os.path.join(os.path.expanduser("~"), ".career-scout", "job-result")
    os.makedirs(artifact_dir, exist_ok=True)
    total = len(jobs)
    if total == 0:
        return {"jobs": [], "hard_stop": False, "hard_stop_code": None,
                "stopped": False, "fetched": 0}
    if execution_config is not None:
        # SPEC011 T006: 使用冻结配置快照，不读 JSON
        BATCH_SIZE = int(execution_config.detail_batch_size)
        _detail_interval = float(execution_config.detail_interval)
        _detail_reset_every = int(execution_config.detail_reset_every)
        _detail_batch_cooldown = float(execution_config.detail_batch_cooldown)
        _detail_tab_pool_size = int(execution_config.detail_tab_pool_size)
    else:
        BATCH_SIZE = int(_facade.load_advanced_settings().get("detail_batch_size") or 5)
        _adv = _facade.load_advanced_settings()
        _detail_interval = float(_adv.get("detail_interval") or 8)
        _detail_reset_every = int(_adv.get("detail_reset_every") or 3)
        _detail_batch_cooldown = float(_adv.get("detail_batch_cooldown") or 30)
        _detail_tab_pool_size = int(_adv.get("detail_tab_pool_size") or 5)
    done_ids = {str(x) for x in completed_job_ids} if completed_job_ids else set()
    # 预先为每个 job 计算稳定 job_id（与 fetch_details_batch 内部 key 一致），
    # 缺 job_id 的 job 填充 idx{idx} 兜底，确保 batch 返回的 outcome 能映射回原 job。
    indexed_jobs = []
    for idx, job in enumerate(jobs):
        if not isinstance(job, dict):
            indexed_jobs.append((idx, f"idx{idx}", {}))
            continue
        jid = str(job.get("platform_job_id") or job.get("job_id") or job.get("id") or "").strip()
        if not jid:
            jid = f"idx{idx}"
        indexed_jobs.append((idx, jid, dict(job)))
    jd_by_idx = {}
    jd_fail_by_idx: dict[int, str] = {}
    jd_fail_reason_by_idx: dict[int, str] = {}
    jd_fail_evidence_by_idx: dict[int, str] = {}
    done = 0
    fetched = 0
    hard_stop = False
    hard_stop_code: str | None = None
    stopped = False
    # 源级硬信号集合：命中任何一个都意味着继续抓只会抓空气，必须截停并上报用户。
    # 016：与组合层共用注册表派生的 SYSTEMIC_BLOCK_CODES，不再维护第二份清单
    # （JD 抓取阶段不调 AI，ai_* 码实际不会出现）。
    _jd_hard_stop_codes = _HARD_STOP_CODES

    def _jd_is_hard_stop(code: object) -> bool:
        """硬停判定先归一别名码（历史码/测试夹具可能仍带旧码）。"""
        code = str(code or "")
        return bool(code) and resolve_code(code) in _jd_hard_stop_codes
    # 022：环境探测回调（第 3 次卡死时复用 source.preflight + ensure_chrome_ready）。
    def _make_env_probe(src):
        def _probe():
            from webui.pipeline_exec import ensure_chrome_ready
            _port = getattr(src, "cdp_port", None)
            _chrome_ok, _chrome_err = ensure_chrome_ready(_port)
            if not _chrome_ok:
                return False, "source_cdp_unavailable", f"调试浏览器未就绪：{_chrome_err}"
            try:
                _outcome = src.preflight()
            except Exception as exc:
                return False, "internal_error", f"环境探测异常：{type(exc).__name__}"
            if _outcome is None or not _outcome.ok:
                _code = str(getattr(_outcome, "failed_code", "") or "source_blocked")
                return False, _code, "环境检查未通过"
            return True, "", ""
        return _probe

    stall_divert: str | None = None
    stall_code = ""
    stall_attempts = 0
    for batch_start in range(0, len(indexed_jobs), BATCH_SIZE):
        if stop_event is not None and stop_event.is_set():
            stopped = True
            break
        # 批次间冷却：防 BOSS session 级反爬（code:37），首批不等
        if batch_start > 0:
            cooldown = _detail_batch_cooldown + random.uniform(-5, 5)
            print(f"[fetch_jd] 批次间冷却 {cooldown:.0f}s（防 code:37）...")
            _t0_cooldown = time.time()
            time.sleep(max(cooldown, 5))
            # T018: 记录 wait 事件（冷却时间计入总耗时）
            if measurement_callback is not None and emit_terminal_events:
                try:
                    measurement_callback("wait", "detail",
                                         int((time.time() - _t0_cooldown) * 1000),
                                         counts={"batch_index": batch_start // BATCH_SIZE})
                except Exception:
                    pass
        batch = indexed_jobs[batch_start:batch_start + BATCH_SIZE]
        batch_jobs = [job for _, _, job in batch]
        batch_path = os.path.join(
            artifact_dir, f"pipeline_batch_{batch_start}_{time.time_ns()}.json"
        )
        # 022：批次键跨 chunk 唯一（run_jd_stage 传 task_id+chunk_start 前缀）。
        batch_key = f"{batch_key_prefix or 'jd'}:{batch_start}"
        attempt = 1
        while True:
            if stop_event is not None and stop_event.is_set():
                stopped = True
                break
            batch_exception_code: str | None = None
            _t0_batch = time.time()

            # 批内条级进度：智联串行逐条抓取时由 source 逐条回调（on_item_done），
            # 否则一批 15 条要十几分钟，前端进度条一直停在 0。BOSS 子进程模式
            # 在批返回时一次性回调（幂等），不改变原有批量语义。
            batch_done_before = done

            def _item_progress(n: int, _base: int = batch_done_before, _total: int = total) -> None:
                if progress is None:
                    return
                try:
                    progress(min(_base + n, _total), _total)
                except Exception:
                    pass

            recovery = BrowserRecovery(
                cdp_port=getattr(source, "cdp_port", None),
                platform=getattr(source, "platform", ""),
            )

            def _fetch_batch(job_list, output_path, *, with_progress=True):
                try:
                    results = source.fetch_details_batch(
                        job_list,
                        detail_output_path=output_path,
                        max_batch_size=BATCH_SIZE,
                        gap_min=_detail_interval,
                        gap_max=_detail_interval + 7,
                        reset_every=_detail_reset_every,
                        tab_pool_size=_detail_tab_pool_size,
                        on_item_done=_item_progress if with_progress else None,
                        simulation_mode=simulation_mode,
                    )
                    return results, None
                except _PIPELINE_OPERATION_ERRORS as exc:
                    # 批调用本身抛错时没有逐岗位 outcome 可供后续分类；这属于源/编排
                    # 级故障，不能伪装成一批空结果继续推进。
                    return {}, _classify_detail_batch_exception(exc)

            # 022：批次登记 + 挂 spawn 钩子（登记子进程供卡死 kill）+ 心跳透传
            _executor = None
            if guard is not None:
                guard.begin_batch(
                    batch_key, task_id=batch_key_prefix or "",
                    attempt=attempt, env_probe=_make_env_probe(source),
                )
                _executor = getattr(source, "_executor", None)
                if _executor is not None:
                    try:
                        _executor.on_spawn = guard.spawn_hook(batch_key)
                        _executor.on_output_probe = (
                            lambda text, _k=batch_key: guard.touch(_k)
                        )
                    except Exception:
                        _executor = None
            outcomes, batch_exception_code = _fetch_batch(batch_jobs, batch_path)
            if _executor is not None:
                try:
                    _executor.on_spawn = None
                    _executor.on_output_probe = None
                except Exception:
                    pass
            if guard is not None and guard.should_retry(batch_key):
                # 卡死：等 3~5s 后整批重抓（接受重复抓取，冻结需求 3）
                _delay = guard.next_retry_delay()
                print(f"[fetch_jd] 批次 {batch_key} 判定卡死，"
                      f"{_delay:.0f}s 后重抓（第 {attempt + 1} 次）...")
                time.sleep(_delay)
                attempt += 1
                continue
            if guard is not None and guard.should_giveup(batch_key):
                # 第 3 次仍卡死：分流收场（环境级由调用方暂停 / 偶发跳过进待确认）
                stall_divert = guard.divert_result(batch_key)
                stall_code = guard.stall_code(batch_key)
                stall_attempts = attempt
                _giveup_code = (
                    stall_code if stall_divert == "environment"
                    else "detail_timeout"
                )
                _giveup_reason = (
                    f"该批连续 {attempt} 次无响应"
                    + ("，检测到环境问题" if stall_divert == "environment"
                       else "，已跳过该批，可在待确认中补抓")
                )
                for idx, jid, _ in batch:
                    jd_fail_by_idx[idx] = _giveup_code
                    jd_fail_reason_by_idx[idx] = _giveup_reason
                    jd_by_idx[idx] = ""
                    done += 1
                    if progress is not None:
                        try:
                            progress(done, total)
                        except Exception:
                            pass
                guard.complete_batch(batch_key)
                break
            if guard is not None:
                guard.complete_batch(batch_key)
                guard.touch(batch_key)  # 批次结果返回也是产出
            if batch_exception_code is not None and not recovery.is_browser_lost(batch_exception_code):
                hard_stop = True
                hard_stop_code = batch_exception_code
            _cdp_lost = (
                recovery.is_browser_lost(batch_exception_code)
                or any(
                    outcome is not None and recovery.is_browser_lost(outcome.failed_code)
                    for outcome in outcomes.values()
                )
            )
            _other_hard = (
                _jd_is_hard_stop(batch_exception_code)
                and not recovery.is_browser_lost(batch_exception_code)
            ) or any(
                outcome is not None
                and outcome.failed_code in _jd_hard_stop_codes
                and not recovery.is_browser_lost(outcome.failed_code)
                for outcome in outcomes.values()
            )
            if _cdp_lost and not _other_hard:
                retry_entries = (
                    list(batch)
                    if batch_exception_code
                    else [
                        entry for entry in batch
                        if (outcome := outcomes.get(entry[1])) is not None
                        and recovery.is_browser_lost(outcome.failed_code)
                    ]
                )
                if retry_entries:
                    restart_ok, restart_err = recovery.try_restart()
                    if restart_ok:
                        retry_jobs = [entry[2] for entry in retry_entries]
                        retry_outcomes, retry_exception = _fetch_batch(
                            retry_jobs, batch_path, with_progress=False,
                        )
                        outcomes.update(retry_outcomes)
                        if retry_exception is None:
                            batch_exception_code = None
                        else:
                            batch_exception_code = retry_exception
                        remaining_hard = [
                            outcome.failed_code
                            for outcome in outcomes.values()
                            if outcome is not None
                            and _jd_is_hard_stop(outcome.failed_code)
                        ]
                        if batch_exception_code is not None or remaining_hard:
                            # 自动重启后的重试仍异常（含 cdp_unavailable）或仍命中
                            # 源级硬信号：同一失联事件不再循环重启，直接暂停。
                            hard_stop = True
                            hard_stop_code = (
                                batch_exception_code
                                if batch_exception_code is not None
                                else remaining_hard[0]
                            )
                        else:
                            recovery.mark_progress()
                    else:
                        hard_stop = True
                        hard_stop_code = "source_cdp_unavailable"
            # T018: 记录 request 事件（批次请求时长）
            if measurement_callback is not None:
                try:
                    measurement_callback("request", "detail",
                                         int((time.time() - _t0_batch) * 1000),
                                         counts={"batch_size": len(batch)},
                                         error_code=batch_exception_code)
                except Exception:
                    pass
            for idx, jid, _ in batch:
                outcome = outcomes.get(jid)
                jd = ""
                if outcome is not None and outcome.ok and isinstance(outcome.detail, dict):
                    jd = str(outcome.detail.get("jd", "")).strip()
                elif outcome is not None and _jd_is_hard_stop(outcome.failed_code):
                    # 源级硬信号：停后续批次并上报（别继续抓空气还装完成）
                    hard_stop = True
                    hard_stop_code = outcome.failed_code
                if not jd and batch_exception_code:
                    jd_fail_by_idx[idx] = batch_exception_code
                    jd_fail_reason_by_idx[idx] = taxonomy_reason(
                        batch_exception_code, getattr(source, "platform", ""),
                        fallback="抓取失败",
                    )
                elif not jd and outcome is not None and outcome.failed_code:
                    jd_fail_by_idx[idx] = outcome.failed_code
                    jd_fail_reason_by_idx[idx] = (
                        outcome.failed_reason
                        or failed_code_label(
                            outcome.failed_code, getattr(source, "platform", "")
                        ) or "岗位详情抓取失败"
                    )
                    jd_fail_evidence_by_idx[idx] = str(getattr(outcome, "safe_log", "") or "")
                jd_by_idx[idx] = jd
                if jd:
                    fetched += 1
                # T018: 记录 item_terminal 事件（SC-007 终态守恒）
                if measurement_callback is not None:
                    try:
                        _status = "success" if jd else "failed"
                        measurement_callback("item_terminal", "detail", 0,
                                             counts={"item_index": idx, "status": _status,
                                                     "input_count": total})
                    except Exception:
                        pass
                done += 1
                if progress is not None:
                    try:
                        progress(done, total)
                    except Exception:
                        pass
            # T018: 记录 batch 事件
            if measurement_callback is not None:
                try:
                    _batch_fetched = sum(1 for _, jid, _ in batch if outcomes.get(jid) and outcomes[jid].ok)
                    measurement_callback("batch", "detail", 0,
                                         counts={"input_count": len(batch),
                                                 "output_count": _batch_fetched,
                                                 "batch_index": batch_start // BATCH_SIZE})
                except Exception:
                    pass
            if hard_stop:
                break
            # 正常完成（未卡死、未硬停）：退出本批重抓循环
            break
        if stopped:
            break
        if hard_stop:
            break
        if stall_divert == "environment":
            # 环境级卡死：停止后续批次，交调用方走暂停收场
            break
    enriched = []
    for idx, job in enumerate(jobs):
        e = dict(job) if isinstance(job, dict) else {}
        jid = str(e.get("platform_job_id") or e.get("job_id") or e.get("id") or "")
        if jid and jid in done_ids and str(e.get("jd", "")).strip():
            # 断点续抓：已抓过的岗位保留原 JD，不重复抓也不覆盖
            enriched.append(e)
            continue
        e["jd"] = jd_by_idx.get(idx, "")
        if not e["jd"] and idx in jd_fail_by_idx:
            e["jd_failed_code"] = jd_fail_by_idx[idx]
            e["jd_failed_reason"] = jd_fail_reason_by_idx[idx]
            evidence = jd_fail_evidence_by_idx.get(idx, "")
            if evidence:
                e["jd_failed_evidence"] = evidence
        enriched.append(e)
    return {"jobs": enriched, "hard_stop": hard_stop,
            "hard_stop_code": hard_stop_code,
            "stopped": stopped, "fetched": fetched,
            "stall_divert": stall_divert, "stall_code": stall_code,
            "stall_attempts": stall_attempts}
