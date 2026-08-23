"""AI 粗筛（screen_jobs）与 JD 精筛（match_jds）（021 B7 自 ai.py 搬运）。

call_ai 经 webui.ai 门面在调用时动态取用，保持 patch 面不变。
"""

from __future__ import annotations

import json
import time

from webui.ai_retry import FINE_SINGLE_INVALID_RESPONSE_DELAY_SECONDS
from webui.error_registry import ERROR_INVALID, ERROR_SERVER, ERROR_TRUNCATED, SYSTEMIC_AI_ERROR_CODES
from webui.flag_features import build_features_prompt_text, clean_flags, decide_flags
from webui.screening_jd_gate import has_usable_jd, missing_jd_verdict
from webui.profile_facts import build_profile_facts_description
from webui.ai_prompts import build_match_system_prompt

from webui.ai_client import FINE_BATCH_TIMEOUT, _AI_CHECKPOINT_FAILED
from webui.ai_errors import (
    AICheckpointError,
    AISecurityError,
    _emit_batch_event,
    _emit_item_terminal_event,
    _emit_retry_event,
    _measurement_item_index,
    user_facing_error,
)
from webui.ai_filters import (
    AI_CONSECUTIVE_FAILURE_LIMIT,
    MATCH_BATCH_SIZE,
    MATCH_CONCURRENCY,
    SCREEN_BATCH_SIZE,
    SCREEN_CONCURRENCY,
    _adv_setting,
    _build_criteria_description,
    _job_criteria_hard_mismatch,
)



def screen_jobs(jobs, criteria, endpoint_url, api_key, model="",
                batch_size=None, progress=None,
                concurrency=None, raise_on_systemic=False,
                completed_verdicts=None, on_batch_done=None,
                execution_config=None,
                measurement_callback=None, emit_kept_terminal=True,
                measurement_input_count=None, retry_limits=None,
                correlation_id: str = ""):
    """Stage A 粗筛：AI 逐条核对岗位列表字段，移除"明显"不符合的。

    ``jobs``: 脚本抓回的岗位列表（仅列表字段，无 JD）。
    ``criteria``: {"profile_summary": str, "city": [...], "degree": [...], ...}。
    ``concurrency``: 并发批次数，默认 1（串行）。spec 007 ⑥⑦：免费端点实测并发=1；
        换不限流端点可调大。>1 时用线程池并发提交批次，结果按批次顺序合并。

    ``execution_config``: SPEC011 T006 — 可选的不可变 ExecutionConfigSnapshot。
    提供时使用冻结的 ``screen_batch_size``/``screen_concurrency``，不读 JSON。

    学历向下兼容、实习/全职不符、城市不符、薪资严重偏低视为明显不符；
    拿不准的一律保留（宁可多留不可错杀）。AI 调用失败的批次全部保留。

    输入格式（spec 007 ⑥）：一行一个紧凑格式 ``i. 标题 | 薪资 | 城市 | 学历 | 规模``，
    省 JSON 包装省 token。输出格式：只列剔除名单
    ``{"dropped":[{"i":3,"reason":...}]}``，未列出的默认保留——防错杀、省输出 token、
    避免 50 条输出截断。

    切片6（FR-020/SC-006）：``raise_on_systemic=True`` 时，AI 命中限流/额度/密钥/
    网络等 systemic 错误立即抛 ``AISecurityError``，调用方应捕获并暂停整任务，
    而不是默认全部保留并继续。默认 False 保持向后兼容。

    返回 {"kept": [job_id...], "dropped": [{"job_id","title","reason"}...],
    "verdicts": {job_id: {"verdict","reason"}}}。
    """
    from webui import ai as _facade
    if batch_size is None:
        if execution_config is not None:
            batch_size = int(execution_config.screen_batch_size)
        else:
            batch_size = int(_adv_setting("screen_batch_size", SCREEN_BATCH_SIZE))
    if concurrency is None:
        if execution_config is not None:
            concurrency = int(execution_config.screen_concurrency)
        else:
            concurrency = int(_adv_setting("screen_concurrency", SCREEN_CONCURRENCY))
    kept, dropped, verdicts = [], [], {}
    measurement_indices = {id(job): index for index, job in enumerate(jobs)}
    terminal_input_count = (
        int(measurement_input_count)
        if measurement_input_count is not None else len(jobs)
    )
    completed_verdicts = completed_verdicts or {}
    completed_ids = {str(job_id) for job_id in completed_verdicts}
    verdicts.update(completed_verdicts)
    for job in jobs:
        job_id = str(job.get("job_id", ""))
        verdict = completed_verdicts.get(job_id) or {}
        if verdict.get("verdict") == "dropped":
            dropped.append({
                "job_id": job_id,
                "title": job.get("title", ""),
                "reason": verdict.get("reason", ""),
                "canonical_url": job.get("canonical_url", "")
                or job.get("source_url", "") or job.get("url", ""),
            })
    jobs_to_process = [
        job for job in jobs
        if str(job.get("job_id", "")) not in completed_ids
    ]
    hard_dropped = []
    for _idx, job in enumerate(jobs_to_process):
        _field, _reason = _job_criteria_hard_mismatch(job, criteria)
        if not _field:
            continue
        _job_id = str(job.get("job_id", ""))
        hard_dropped.append({
            "job_id": _job_id,
            "title": job.get("title", ""),
            "reason": _reason,
            "canonical_url": job.get("canonical_url", "")
            or job.get("source_url", "") or job.get("url", ""),
        })
        verdicts[_job_id] = {"verdict": "dropped", "reason": _reason}
        if measurement_callback is not None:
            _emit_item_terminal_event(
                measurement_callback, "rough",
                item_index=_measurement_item_index(job, _idx, measurement_indices),
                status="dropped",
                input_count=terminal_input_count,
            )
    if hard_dropped:
        dropped.extend(hard_dropped)
        _hard_ids = {item["job_id"] for item in hard_dropped}
        jobs_to_process = [
            job for job in jobs_to_process
            if str(job.get("job_id", "")) not in _hard_ids
        ]
    if not jobs:
        return {"kept": kept, "dropped": dropped, "verdicts": verdicts}

    criteria_desc = _build_criteria_description(criteria)
    system_prompt = (
        "你是求职初筛助手。只按候选人已确认的筛选字段，剔除【明显】不符的岗位。\n"
        f"{criteria_desc}\n\n"
        "判断规则（务必按常理，不要死板）：\n"
        "- 字段为空或未列出 = 不限，不得按该维度剔除；候选人画像只用于放宽，不能用来新增硬条件\n"
        "- 学历：已选学历为硬约束，岗位标签明确要求高于已选学历（如已选大专/本科而岗位硕士/博士）即剔除；未标学历保留\n"
        "- 求职类型：仅当岗位标题明确写'实习'且候选人画像明确写'全职'时，视为明显不符合；拿不准一律保留\n"
        "- 城市不判断（抓取阶段已保证城市）\n"
        "- 薪资：筛选区间为硬规则，岗位薪资与已选区间无重叠（高于或低于）即排除；'元/天'的实习计价综合判断\n"
        "- 经验：已选经验为硬约束，岗位标签明确经验下界高于已选范围（如已选1-3年而岗位3-5年）即剔除；未标经验保留\n"
        "- 已选择的筛选字段是硬约束：岗位标签明确列出的经验/学历/薪资/规模/融资/行业与已选条件冲突时，必须剔除；未选择或岗位未标明的字段不剔除\n"
        "- 岗位名称或类别（如客服、讲师、销售、内容制作、运营等）不得单独作为剔除理由\n"
        "- 求职画像放宽：候选人画像中明确表达放宽的维度（如\"东莞、深圳都可以\"\"不限\"\"接受兼职\"等）以画像表述为准放宽对应判断\n"
        "- 只排除【明显】不符合的；拿不准一律保留（宁可多留，不可错杀）\n\n"
        "输入格式：每行一个岗位，``序号. 标题 | 薪资 | 城市 | 学历 | 规模``。\n"
        "输出格式：只列出【要剔除】的岗位序号与理由，未列出的默认保留。严格输出JSON：\n"
        '{"dropped":[{"i":3,"reason":"经验5-10年>候选1-3年"},...]}\n'
        "i 为岗位序号。\n"
        "reason 必须具体，仅当字段已确认时使用「字段名+岗位值+比较符+候选人值」格式，禁止笼统表述。\n"
        "示例（仅当对应字段已确认时使用）：\n"
        '  经验已确认且岗位下界高于候选人上界：reason="经验5-10年>候选1-3年"\n'
        '  学历已确认且岗位要求高于候选人：reason="学历硕士>候选本科"\n'
        '  求职类型已确认且岗位为实习/全职冲突：reason="实习岗≠全职"\n'
        '  薪资已确认且岗位薪资明显低于期望：reason="薪资3-5K<期望8-10K"\n'
        "禁止使用「经验过高」「不符合」「不匹配」等笼统词汇。\n"
        "reason 限25字内。若无任何剔除，输出 {\"dropped\":[]}。"
    )

    # 切批
    batches = []
    for start in range(0, len(jobs_to_process), batch_size):
        batches.append(jobs_to_process[start:start + batch_size])

    def _process_batch(batch):
        """处理单个批次，返回 (batch_dropped, batch_verdicts)。

        batch_dropped: [{"job_id","title","reason"}...]
        batch_verdicts: {job_id: {"verdict","reason"}}
        默认全保留；AI 返回的 dropped 名单扣掉。
        """
        # 紧凑文本输入：i. 标题 | 薪资 | 城市 | 学历 | 规模
        lines = []
        for idx, job in enumerate(batch):
            parts = [
                job.get("title", ""),
                job.get("salary", ""),
                job.get("location", ""),
                job.get("job_labels", "") or "",  # 学历/经验标签
                job.get("company_scale", "") or "",
            ]
            lines.append(f"{idx}. " + " | ".join(str(p) for p in parts if p))
        user_content = "\n".join(lines)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        _t0 = time.time()
        _req_error_code = None
        try:
            data = _facade.call_ai(
                endpoint_url, api_key, messages, model=model,
                measurement_callback=measurement_callback,
                measurement_stage="rough",
                retry_limits=retry_limits,
                correlation_id=correlation_id,
            )
            raw_dropped = data.get("dropped", []) if isinstance(data, dict) else []
            dropped_list = raw_dropped if isinstance(raw_dropped, list) else []
            by_i = {r.get("i"): r for r in dropped_list if isinstance(r, dict)}
        except AISecurityError as exc:
            # 切片6：systemic 错误（限流/额度/密钥/网络）立即抛，让调用方暂停
            if raise_on_systemic and exc.error_code in SYSTEMIC_AI_ERROR_CODES:
                _req_error_code = exc.error_code
                raise
            if exc.error_code == ERROR_TRUNCATED and len(batch) > 1:
                # 返回被截断：拆半重跑这批，还截断就继续拆（到单条为止）
                _req_error_code = exc.error_code
                _emit_retry_event(
                    measurement_callback, "rough", 0,
                    metadata={"truncated_split": 1},
                )
                mid = len(batch) // 2
                d1, v1 = _process_batch(batch[:mid])
                d2, v2 = _process_batch(batch[mid:])
                v1.update(v2)
                return d1 + d2, v1
            _req_error_code = exc.error_code
            by_i = {}  # 调用失败：该批全部保留，防错杀

        b_dropped, b_verdicts = [], {}
        for idx, job in enumerate(batch):
            jid = str(job.get("job_id", ""))
            r = by_i.get(idx)
            if r:
                reason = str(r.get("reason", "")).strip()
                b_dropped.append({
                    "job_id": jid,
                    "title": job.get("title", ""),
                    "reason": reason,
                    "canonical_url": job.get("canonical_url", "") or job.get("source_url", "") or job.get("url", "") or "",
                })
                b_verdicts[jid] = {"verdict": "dropped", "reason": reason}
            else:
                b_verdicts[jid] = {"verdict": "kept", "reason": ""}
            if r or emit_kept_terminal:
                _emit_item_terminal_event(
                    measurement_callback, "rough",
                    item_index=_measurement_item_index(
                        job, idx, measurement_indices),
                    status="dropped" if r else "kept",
                    input_count=terminal_input_count,
                )
        # 批次事件：记录输入/输出数量
        _emit_batch_event(measurement_callback, "rough",
                          input_count=len(batch),
                          output_count=len(b_dropped),
                          error_code=_req_error_code)
        return b_dropped, b_verdicts

    if concurrency <= 1:
        # 串行（默认，免费端点并发=1）
        processed = 0
        for batch in batches:
            b_dropped, b_verdicts = _process_batch(batch)
            dropped.extend(b_dropped)
            verdicts.update(b_verdicts)
            completed_ids.update(b_verdicts)
            if on_batch_done is not None:
                try:
                    on_batch_done(dict(b_verdicts), list(completed_ids))
                except Exception as exc:
                    raise AICheckpointError(_AI_CHECKPOINT_FAILED) from exc
            processed += len(batch)
            if progress is not None:
                try:
                    progress(min(processed, len(jobs)), len(jobs))
                except Exception:
                    pass
    else:
        # 并发（换不限流端点时启用）
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed
        lock = threading.Lock()
        processed_counter = [0]

        def _safe_progress(n_done):
            if progress is None:
                return
            with lock:
                processed_counter[0] += n_done
                cur = min(processed_counter[0], len(jobs))
            try:
                progress(cur, len(jobs))
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_process_batch, batch): batch for batch in batches}
            for fut in as_completed(futures):
                b_dropped, b_verdicts = fut.result()
                with lock:
                    dropped.extend(b_dropped)
                    verdicts.update(b_verdicts)
                    completed_ids.update(b_verdicts)
                    completed_snapshot = list(completed_ids)
                if on_batch_done is not None:
                    try:
                        on_batch_done(dict(b_verdicts), completed_snapshot)
                    except Exception as exc:
                        raise AICheckpointError(_AI_CHECKPOINT_FAILED) from exc
                _safe_progress(len(futures[fut]))

    kept = [str(j.get("job_id", "")) for j in jobs
            if str(j.get("job_id", "")) not in {d["job_id"] for d in dropped}]
    return {"kept": kept, "dropped": dropped, "verdicts": verdicts}




def match_jds(jobs_with_jd, profile_summary, endpoint_url, api_key, model="",
              criteria=None, profile_facts=None,
              batch_size=None, progress=None, completed_verdicts=None,
              concurrency=None, raise_on_systemic=False,
              execution_config=None,
              on_batch_done=None,
              measurement_callback=None, measurement_input_count=None,
              missing_result_retry_budget=0, retry_limits=None,
              correlation_id: str = ""):
    """Stage B 精筛：AI 逐条对比岗位 JD 与候选人画像，判 match/not_match。

    ``jobs_with_jd``: [{"job_id","title","salary","location","jd"}...]。
    ``profile_summary``: 求职画像（用户可编辑，优先级低于已选六类字段，只能放宽未选择维度）。
    ``criteria``: 可选，筛选条件 dict（学历/经验/薪资/城市等，作硬性基线）。
    ``profile_facts``: 可选，画像事实 dict（core_skills/projects/job_type/languages，
        缺失维度按"未体现"处理，不得推断）。
    返回 {"verdicts": {job_id: {"verdict": "match"/"not_match", "reason",
    "caveats", "flags"}}}。
    AI 调用失败或漏回结果的岗位标记为 uncertain，保留给用户人工确认，
    不能把未完成的判定伪装成已匹配。
    传输层失败的批次不额外整批重试，失败项直接按 uncertain 落库，
    用户可在结果页对 uncertain 岗位补抓/重判。

    ``completed_verdicts``: 可选，已完成的判定 {job_id: verdict}（断点续筛）。
    这些岗位跳过不重复调用 AI，原样并入返回；默认 None 时行为与之前一致。

    ``concurrency``: 并发批次数，默认 1（串行）。spec 007 ⑥⑦：免费端点实测并发=1；
        换不限流端点可调大。>1 时用线程池并发提交批次，结果按完成顺序合并。

    ``execution_config``: SPEC011 T006 — 可选的不可变 ExecutionConfigSnapshot。
    提供时使用冻结的 ``match_batch_size``/``match_concurrency``，不读 JSON。

    ``on_batch_done``: 可选回调 (batch_verdicts, completed_job_ids)，每批判定落库后
    调用；回调抛异常会转成 AICheckpointError，防止内存进度领先于可恢复进度。

    切片6（FR-020/SC-008）：``raise_on_systemic=True`` 时，AI 命中限流/额度/密钥/
    网络等 systemic 错误立即抛 ``AISecurityError``，调用方应捕获并暂停整任务，
    而不是批量变 uncertain 后完成。默认 False 保持向后兼容。

    熔断：``raise_on_systemic=True`` 时，连续 AI_CONSECUTIVE_FAILURE_LIMIT 个批次
    全部无有效判定（空响应/截断/无效 JSON 等非 systemic 失败）也会抛
    ``AISecurityError(server_error, failure_phase=circuit_open)`` 触发暂停，
    防止端点整体劣化时拆半递归放大请求、长期空转。
    """
    from webui import ai as _facade
    if batch_size is None:
        if execution_config is not None:
            batch_size = int(execution_config.match_batch_size)
        else:
            batch_size = int(_adv_setting("match_batch_size", MATCH_BATCH_SIZE))
    if concurrency is None:
        if execution_config is not None:
            concurrency = int(execution_config.match_concurrency)
        else:
            concurrency = int(_adv_setting("match_concurrency", MATCH_CONCURRENCY))
    verdicts = {}
    measurement_indices = {
        id(job): index for index, job in enumerate(jobs_with_jd)
    }
    import threading
    terminal_lock = threading.Lock()
    emitted_terminal_indices: set[int] = set()

    def _emit_final_terminal(job: dict, fallback: int, status: str) -> None:
        item_index = _measurement_item_index(job, fallback, measurement_indices)
        with terminal_lock:
            if item_index in emitted_terminal_indices:
                return
            emitted_terminal_indices.add(item_index)
        _emit_item_terminal_event(
            measurement_callback, "fine", item_index=item_index,
            status=status, input_count=terminal_input_count,
        )
    if completed_verdicts:
        done_ids = {str(k) for k in completed_verdicts}
        verdicts.update(completed_verdicts)
        jobs_with_jd = [j for j in jobs_with_jd
                        if str(j.get("job_id", "")) not in done_ids]
    if not jobs_with_jd:
        return {"verdicts": verdicts}
    # 熔断：连续整批 AI 无有效判定（空响应/截断/无效 JSON 等非 systemic 失败）
    # 达到 AI_CONSECUTIVE_FAILURE_LIMIT 即判定端点系统性故障，抛 server_error
    # 让调用方暂停整任务，避免故障时拆半递归放大请求、长期空转。
    # 仅 raise_on_systemic=True 时生效；False 保持原“标 uncertain 继续”行为。
    _circuit_state = {"consecutive": 0}
    _circuit_lock = threading.Lock()

    def _circuit_after_batch(batch_verdicts: dict) -> None:
        """整批全部 uncertain（AI 未给出任何有效判定）→ 连续失败计数，否则清零。"""
        all_uncertain = bool(batch_verdicts) and all(
            isinstance(v, dict) and v.get("verdict") == "uncertain"
            for v in batch_verdicts.values()
        )
        should_open = False
        with _circuit_lock:
            if all_uncertain:
                _circuit_state["consecutive"] += 1
            else:
                _circuit_state["consecutive"] = 0
            should_open = (
                raise_on_systemic
                and _circuit_state["consecutive"] >= AI_CONSECUTIVE_FAILURE_LIMIT
            )
        if should_open:
            raise AISecurityError(
                ERROR_SERVER,
                {
                    "failure_phase": "circuit_open",
                    "consecutive_failures": _circuit_state["consecutive"],
                    "limit": AI_CONSECUTIVE_FAILURE_LIMIT,
                },
            )
    terminal_input_count = (
        int(measurement_input_count)
        if measurement_input_count is not None else len(jobs_with_jd)
    )
    eligible_jobs = []
    for _idx, _job in enumerate(jobs_with_jd):
        if has_usable_jd(_job):
            eligible_jobs.append(_job)
            continue
        verdicts[str(_job.get("job_id", ""))] = missing_jd_verdict(_job)
        _emit_final_terminal(_job, _idx, "uncertain")
    jobs_with_jd = eligible_jobs
    if not jobs_with_jd:
        return {"verdicts": verdicts}
    # 已选筛选字段是硬约束：结构化标签/JD 明确值与筛选条件冲突时直接 not_match，
    # 不交给 AI 各批自判；字段未知或未标明的岗位保留给 AI 判断。
    _hard_kept = []
    for _idx, _job in enumerate(jobs_with_jd):
        _field, _reason = _job_criteria_hard_mismatch(_job, criteria)
        if _field:
            verdicts[str(_job.get("job_id", ""))] = {
                "verdict": "not_match", "reason": _reason,
            }
            _emit_final_terminal(_job, _idx, "not_match")
        else:
            _hard_kept.append(_job)
    jobs_with_jd = _hard_kept
    if not jobs_with_jd:
        return {"verdicts": verdicts}
    missing_retry_budget = [max(0, int(missing_result_retry_budget))]
    summary = (profile_summary or "").strip() or "（无候选人画像）"
    criteria_desc = ""
    if criteria:
        criteria_desc = _build_criteria_description(
            {k: v for k, v in criteria.items() if k != "profile_summary"}
        )
    criteria_desc = criteria_desc or "（无明确标准，宽松判断）"
    facts_desc = build_profile_facts_description(profile_facts)
    system_prompt = build_match_system_prompt(
        criteria_desc=criteria_desc,
        profile_summary=summary,
        facts_desc=facts_desc,
        features_prompt_text=build_features_prompt_text(),
    )
    def _match_one_batch(batch, _invalid_retried=False):
        """单批精筛，返回 {jid: verdict}。

        返回被截断（ERROR_TRUNCATED）时拆半重跑，还截断就继续拆到单条；
        单条仍失败才标 uncertain（不伪装成已匹配）。

        整批因网络/超时/限流失败时，本批每项直接标 uncertain 并发终态，
        不再末尾补一轮；用户可在结果页对 uncertain 岗位重抓。
        """
        batch_desc = [
            {
                "i": idx,
                "title": job.get("title", ""),
                "salary": job.get("salary", ""),
                "location": job.get("location", ""),
                "tags": job.get("tags") or job.get("job_labels") or job.get("tags_list") or "",
                "jd": str(job.get("jd", ""))[:1500],
            }
            for idx, job in enumerate(batch)
        ]
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(batch_desc, ensure_ascii=False)},
        ]
        fail_reason = ""
        _t0 = time.time()
        _req_error_code = None
        try:
            data = _facade.call_ai(
                endpoint_url, api_key, messages, model=model,
                timeout=FINE_BATCH_TIMEOUT,
                measurement_callback=measurement_callback,
                measurement_stage="fine",
                retry_limits=retry_limits,
                correlation_id=correlation_id,
            )
            raw_results = data.get("results", []) if isinstance(data, dict) else []
            results = raw_results if isinstance(raw_results, list) else []
            by_i = {r.get("i"): r for r in results if isinstance(r, dict)}
        except AISecurityError as exc:
            # 切片6：systemic 错误立即抛，让调用方暂停（不批量变 uncertain 后完成）
            if raise_on_systemic and exc.error_code in SYSTEMIC_AI_ERROR_CODES:
                _req_error_code = exc.error_code
                for fallback, pending_job in enumerate(jobs_with_jd):
                    _emit_final_terminal(pending_job, fallback, "uncertain")
                raise
            if exc.error_code == ERROR_TRUNCATED and len(batch) > 1:
                _req_error_code = exc.error_code
                _emit_retry_event(
                    measurement_callback, "fine", 0,
                    metadata={"truncated_split": 1},
                )
                mid = len(batch) // 2
                sub = _match_one_batch(batch[:mid], _invalid_retried)
                sub.update(_match_one_batch(batch[mid:], _invalid_retried))
                return sub
            if (exc.error_code == ERROR_INVALID and len(batch) == 1
                    and not _invalid_retried):
                time.sleep(FINE_SINGLE_INVALID_RESPONSE_DELAY_SECONDS)
                _emit_retry_event(
                    measurement_callback, "fine",
                    int(FINE_SINGLE_INVALID_RESPONSE_DELAY_SECONDS * 1000),
                    metadata={"invalid_response_retry": 1},
                )
                return _match_one_batch(batch, _invalid_retried=True)
            _req_error_code = exc.error_code
            by_i = None
            fail_reason = user_facing_error(exc.error_code)
        batch_verdicts = {}
        for idx, job in enumerate(batch):
            jid = str(job.get("job_id", ""))
            if by_i is None:
                batch_verdicts[jid] = {
                    "verdict": "uncertain",
                    "reason": f"{fail_reason}，待人工确认" if fail_reason else "AI 精筛失败，待人工确认",
                }
                _emit_final_terminal(job, idx, "uncertain")
                continue
            r = by_i.get(idx)
            if not isinstance(r, dict) or not isinstance(r.get("match"), bool):
                if missing_retry_budget[0] > 0:
                    missing_retry_budget[0] -= 1
                    _emit_retry_event(measurement_callback, "fine", 0)
                    retried = _match_one_batch([job])
                    batch_verdicts.update(retried)
                    continue
                batch_verdicts[jid] = {
                    "verdict": "uncertain",
                    "reason": "AI 未返回该岗位判定，待人工确认",
                }
                _emit_final_terminal(job, idx, "uncertain")
                continue
            match = r["match"]
            reason = str(r.get("reason", "")).strip()
            caveats = [str(c).strip() for c in r.get("caveats") or []
                       if isinstance(c, str) and c.strip()]
            # flags 结构化解析：清洗（code/level/reason 校验）+ 分级判定
            # （高危≥1 或 中危≥2 → 输出 flags；中危仅 1 条 → 降级 caveats）
            decided = decide_flags(clean_flags(r.get("flags")))
            flags = decided["flags"]
            caveats.extend(decided["caveats"])
            verdict = "match" if match else "not_match"
            # 高危命中强制 not_match，reason 以"疑似骗局："开头
            if any(f.get("level") == "high" for f in flags):
                verdict = "not_match"
                reason = reason or "命中高危可疑特征"
                if not reason.startswith("疑似骗局："):
                    reason = "疑似骗局：" + reason
            batch_verdicts[jid] = {
                "verdict": verdict,
                "reason": reason,
                "caveats": caveats,
                "flags": flags,
            }
            _emit_final_terminal(job, idx, verdict)
        _emit_batch_event(measurement_callback, "fine",
                          input_count=len(batch),
                          output_count=len(batch_verdicts),
                          error_code=_req_error_code)
        return batch_verdicts

    batches = []
    for start in range(0, len(jobs_with_jd), batch_size):
        batches.append(jobs_with_jd[start:start + batch_size])

    if concurrency <= 1 or len(batches) <= 1:
        # 串行（默认，免费端点并发=1）
        processed = 0
        for batch in batches:
            batch_verdicts = _match_one_batch(batch)
            verdicts.update(batch_verdicts)
            _circuit_after_batch(batch_verdicts)
            if on_batch_done is not None:
                try:
                    on_batch_done(dict(batch_verdicts), list(verdicts.keys()))
                except Exception as exc:
                    raise AICheckpointError(_AI_CHECKPOINT_FAILED) from exc
            processed += len(batch)
            if progress is not None:
                try:
                    progress(min(processed, len(jobs_with_jd)), len(jobs_with_jd))
                except Exception:
                    pass
    else:
        # 并发（换不限流端点时启用）
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed
        lock = threading.Lock()
        processed_counter = [0]

        def _safe_progress(n_done):
            if progress is None:
                return
            with lock:
                processed_counter[0] += n_done
                cur = min(processed_counter[0], len(jobs_with_jd))
            try:
                progress(cur, len(jobs_with_jd))
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_match_one_batch, batch): batch for batch in batches}
            for fut in as_completed(futures):
                batch_verdicts = fut.result()
                with lock:
                    verdicts.update(batch_verdicts)
                    completed_snapshot = list(verdicts.keys())
                _circuit_after_batch(batch_verdicts)
                if on_batch_done is not None:
                    try:
                        on_batch_done(dict(batch_verdicts), completed_snapshot)
                    except Exception as exc:
                        raise AICheckpointError(_AI_CHECKPOINT_FAILED) from exc
                _safe_progress(len(futures[fut]))


    return {"verdicts": verdicts}
