# -*- coding: utf-8 -*-

"""详情抓取核心与并发 worker（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

import json
import os
import random
import time
from scripts.boss.cdp_session import CDPSession
from scripts.boss.constants import CDP_ABOUT_BLANK, CDP_CMD_ADD_SCRIPT_ON_NEW_DOC, CDP_CMD_ATTACH_TARGET, CDP_CMD_CLOSE_TARGET, CDP_CMD_CREATE_TARGET, CDP_CMD_PAGE_NAVIGATE, DEFAULT_CDP_PORT, EXTRACT_DETAIL_JS, HIDDEN_DEFINE_JS, MSG_USER_CANCELLED_SCRAPE, _READINESS_PROBE_JS, _VISIBILITY_STATE_JS
from scripts.boss.detail_parse import build_detail_url, extract_job_description
from scripts.boss.exceptions import DetailExtractionError, DetailLoginRequiredError, DetailRateLimitedError, DetailVerificationRequiredError, RequestLimitExceededError, RiskControlError, SearchCancelled
from scripts.boss.output import default_output_path, write_detail_csv, write_json_atomic
from scripts.boss.rate_limit import begin_request_run
from scripts.boss_cdp_signals import detail_page_hint
import sys as _sys
def _facade():
    return _sys.modules.get("scripts.boss_cdp_raw")

# ============================================================
# 抓取详情
# ============================================================
def build_detail_record(job, extracted):
    link = job.get("job_link", "")
    return {
        "job_id": job.get("job_id", ""),
        "title": job.get("title", ""),
        "company": job.get("boss_name", ""),
        "salary": job.get("salary", ""),
        "salary_source": job.get("salary_source", ""),
        "location": job.get("location", ""),
        "tags_list": job.get("tags", ""),
        "job_link": link,
        "link": link,
        "skill_tags": extracted.get("tags", []),
        "jd": extracted.get("jd", ""),
    }


def _default_scrape_sleeper(seconds, label=None):
    """Default sleeper delegating to ``time.sleep``.

    The ``label`` argument is accepted so that tests and contract checks
    can distinguish kinds of waits (readiness, inter-job gap, etc.).
    """
    time.sleep(seconds)


def _wait_for_detail_readiness(ws, sid, *, sleeper, timeout_seconds, max_retries):
    """Poll page readiness with a bounded wait and at most one scroll retry.

    The readiness probe is a small JS expression that returns ``"ready"``
    when ``document.readyState`` is complete and the body has meaningful
    text. When the probe returns anything else (including ``None``), we
    sleep briefly (counted against ``timeout_seconds``) and, if retries
    remain, perform a single controlled scroll before re-probing.

    Returns when the page is ready or when the retry/budget is exhausted.
    Exhaustion is not fatal — extraction proceeds and the existing
    ``DetailLoginRequiredError`` / ``DetailExtractionError`` paths handle
    invalid pages.
    """
    remaining_budget = float(timeout_seconds)
    retries = 0
    while True:
        value = ws.eval_js(_READINESS_PROBE_JS, sid)
        if value == "ready":
            return
        if retries >= max_retries:
            return
        # Single controlled scroll, then a short wait counted against budget.
        ws.eval_js("window.scrollBy(0, 300)", sid)
        retries += 1
        wait = min(2.0, remaining_budget) if remaining_budget > 0 else 0.0
        if wait > 0:
            sleeper(wait, label="readiness_wait")
            remaining_budget -= wait


def _emit_detail_safe_event(event_callback, job, status, safe_code, started_at,
                             safe_hint=""):
    """Emit one terminal safe event for a detail job.

    The payload deliberately excludes JD body, prompts, outputs and
    credential-shaped fields (encrypt_*_id, security_id). It carries only
    producer kind, terminal status, job identity (job_link), duration and
    a safe code.
    """
    if event_callback is None:
        return
    duration_ms = int((time.time() - started_at) * 1000)
    event = {
        "kind": "detail",
        "status": status,
        "job_id": job.get("job_link", ""),
        "duration_ms": duration_ms,
        "safe_code": safe_code,
    }
    if safe_hint:
        event["safe_hint"] = str(safe_hint)[:160]
    event_callback(event)


def _emit_runtime_safe_event(event_callback, event, *, safe_code="ok", safe_hint=""):
    """Emit a safe non-job runtime event for orchestration-side auditing."""
    if event_callback is None:
        return
    payload = {"kind": "runtime", "event": event, "safe_code": safe_code}
    if safe_hint:
        payload["safe_hint"] = str(safe_hint)[:160]
    event_callback(payload)


def _scrape_one_detail(ws, job, global_idx, total, results, output_path, *,
                       sleeper, event_callback, readiness_timeout_seconds,
                       max_readiness_retries, inter_job_gap_range,
                       is_last_in_run, trailing_wait):
    """Scrape a single detail page within a reused CDP session.

    Emits exactly one terminal safe event via ``event_callback`` (when
    provided) and appends the built detail record to ``results`` on
    success. Returns ``True`` on success, ``False`` on isolated failure.
    Re-raises ``RuntimeError`` for login-wall truncation so the caller
    can stop the run before persisting truncated data.

    账号限流（``DetailRateLimitedError``）不是单岗位独立失败：整个运行
    立即停止（raise ``RiskControlError``），且限流页不关闭——把它留在
    屏幕上给用户看，避免继续开新页面撞限流形成开关页循环。

    The inter-job gap is slept via ``sleeper(label="inter_job_gap")``
    for every non-terminal-excepted job (success or isolated failure)
    unless this is the last job in the run and ``trailing_wait`` is
    False. This preserves rate-limit protection between jobs even when
    one JD fails validation.
    """
    title = job.get("title", "")
    company = job.get("boss_name", "")
    print(f"[{global_idx + 1}/{total}] {company} - {title}")

    _facade().incr_request()

    # 后台创建标签页，不抢占前台焦点，避免抓取时反复弹窗
    r = ws.send(CDP_CMD_CREATE_TARGET, {"url": CDP_ABOUT_BLANK, "background": True})
    tid = r["result"]["targetId"]
    r = ws.send(CDP_CMD_ATTACH_TARGET, {"targetId": tid, "flatten": True})
    sid = r["result"]["sessionId"]

    # background 标签页 document.hidden=true、visibilityState=hidden，
    # BOSS直聘据此判定为非真人浏览而拒绝渲染/重定向到登录页。
    # 在导航前注入，覆盖可见性属性为 visible，骗过 visibility 反爬。
    ws.send(CDP_CMD_ADD_SCRIPT_ON_NEW_DOC, {
        "source": (
            HIDDEN_DEFINE_JS +
            _VISIBILITY_STATE_JS +
            "Object.defineProperty(document, \'webkitHidden\', {get: () => false});"
            "Object.defineProperty(document, 'webkitVisibilityState', {get: () => 'visible'});"
        )
    }, sid)

    detail_url = build_detail_url(job)
    ws.send(CDP_CMD_PAGE_NAVIGATE, {"url": detail_url}, sid)
    print("  加载页面...")

    started_at = time.time()
    _wait_for_detail_readiness(
        ws, sid,
        sleeper=sleeper,
        timeout_seconds=readiness_timeout_seconds,
        max_retries=max_readiness_retries,
    )

    print("  提取 JD...")
    val = ws.eval_js(EXTRACT_DETAIL_JS, sid)
    try:
        d = json.loads(val) if isinstance(val, str) else {"jd": "", "tags": []}
    except (ValueError, TypeError):
        d = {"jd": "", "tags": []}

    skip_gap = False
    detail_hint = detail_page_hint(d.get("url"))
    try:
        try:
            if detail_hint:
                raise DetailExtractionError(detail_hint)
            d["jd"] = extract_job_description(d)
        except DetailLoginRequiredError as exc:
            ws.send(CDP_CMD_CLOSE_TARGET, {"targetId": tid})
            _emit_detail_safe_event(
                event_callback, job, "unavailable",
                "source_login_required", started_at,
            )
            # Run is stopping — do not sleep the inter-job gap.
            skip_gap = True
            raise RuntimeError(
                "BOSS detail login expired; stopped before writing truncated JD data"
            ) from exc
        except DetailRateLimitedError as exc:
            print(f"  ⚠ 账号/操作频繁被限流: {exc}")
            # 限流是账号级阻断：立即停止整个运行，不再开新页面。
            # 限流页不关闭（无 closeTarget），留在屏幕上给用户看。
            _emit_detail_safe_event(
                event_callback, job, "failed",
                "source_rate_limited", started_at, safe_hint=str(exc),
            )
            # Run is stopping — do not sleep the inter-job gap.
            skip_gap = True
            raise RiskControlError(
                f"BOSS 账号/操作频繁被限流：{exc}",
                code="source_rate_limited",
                scraped_count=len(results), output_path=output_path or "",
            ) from exc
        except DetailExtractionError as exc:
            print(f"  跳过无效详情页: {exc}")
            ws.send(CDP_CMD_CLOSE_TARGET, {"targetId": tid})
            _emit_detail_safe_event(
                event_callback, job, "failed",
                "source_invalid_output", started_at,
                safe_hint=detail_hint or str(exc),
            )
            return False

        detail = build_detail_record(job, d)
        results.append(detail)

        if d.get("tags"):
            print(f"  技能: {', '.join(d['tags'])}")
        print(f"  JD: {len(d.get('jd', ''))} 字 ({time.time() - started_at:.0f}s)")

        # 每抓完一个详情就写入，异常退出也能保留
        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            write_json_atomic(output_path, results)

        ws.send(CDP_CMD_CLOSE_TARGET, {"targetId": tid})
        _emit_detail_safe_event(
            event_callback, job, "completed", "ok", started_at,
        )
        return True
    finally:
        # Inter-job gap is rate-limit protection between jobs. It applies
        # to successful and isolated-failure jobs alike. It is skipped
        # for the last job in the run (unless trailing_wait=True) and
        # for the login-wall case where the whole run is stopping.
        if not skip_gap and (not is_last_in_run or trailing_wait):
            gap = random.uniform(inter_job_gap_range[0], inter_job_gap_range[1])
            print(f"  等待 {gap:.1f}s 后抓下一个...\n")
            sleeper(gap, label="inter_job_gap")


def _scrape_detail_on_tab(ws, sid, job, global_idx, total, *,
                          sleeper, event_callback, readiness_timeout_seconds,
                          max_readiness_retries, results_lock, results,
                          output_path, tab_label):
    """在已 attach 的常驻 tab 上抓一个详情（复用 tab，不开/关 target）。

    spec 007 ⑧：与 ``_scrape_one_detail`` 的区别——
    - 不 createTarget/attach（tab 已由 ``_tab_worker`` 建池）
    - 不 closeTarget（抓完留给下一个 job 复用）
    - ``results.append`` + ``write_json_atomic`` + ``incr_request`` 在 ``results_lock`` 内
    - 日志带 ``tab_label`` 前缀，多路汇总进进度框不混乱

    返回 True=成功，False=isolated failure，"login_required"=登录墙（触发降级）。
    """
    title = job.get("title", "")
    company = job.get("boss_name", "")
    print(f"[{tab_label}] [{global_idx + 1}/{total}] {company} - {title}")

    # incr_request 操作全局 _request_counter，非线程安全，加锁
    with results_lock:
        _facade().incr_request()

    detail_url = build_detail_url(job)
    ws.send(CDP_CMD_PAGE_NAVIGATE, {"url": detail_url}, sid)
    print(f"[{tab_label}]   加载页面...")

    started_at = time.time()
    _wait_for_detail_readiness(
        ws, sid,
        sleeper=sleeper,
        timeout_seconds=readiness_timeout_seconds,
        max_retries=max_readiness_retries,
    )

    print(f"[{tab_label}]   提取 JD...")
    val = ws.eval_js(EXTRACT_DETAIL_JS, sid)
    try:
        d = json.loads(val) if isinstance(val, str) else {"jd": "", "tags": []}
    except (ValueError, TypeError):
        d = {"jd": "", "tags": []}

    detail_hint = detail_page_hint(d.get("url"))
    try:
        if detail_hint:
            raise DetailExtractionError(detail_hint)
        d["jd"] = extract_job_description(d)
    except DetailLoginRequiredError:
        _emit_detail_safe_event(
            event_callback, job, "unavailable", "source_login_required", started_at,
        )
        print(f"[{tab_label}]   ⚠ 登录墙，触发降级")
        return "login_required"
    except DetailVerificationRequiredError:
        _emit_detail_safe_event(
            event_callback, job, "failed", "source_verification_required", started_at,
        )
        print(f"[{tab_label}]   ⚠ 详情页验证码/滑块拦截")
        return False
    except DetailRateLimitedError as exc:
        _emit_detail_safe_event(
            event_callback, job, "failed", "source_rate_limited", started_at,
            safe_hint=str(exc),
        )
        print(f"[{tab_label}]   ⚠ 账号/操作频繁被限流")
        # 账号级阻断：由 _tab_worker 触发降级停工；限流页留在原地不关闭
        return "rate_limited"
    except DetailExtractionError as exc:
        print(f"[{tab_label}]   跳过无效详情页: {exc}")
        _emit_detail_safe_event(
            event_callback, job, "failed", "source_invalid_output", started_at,
            safe_hint=detail_hint or str(exc),
        )
        return False

    detail = build_detail_record(job, d)
    # results.append + write_json_atomic 必须在同一锁内，避免并发写盘竞态
    with results_lock:
        results.append(detail)
        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            write_json_atomic(output_path, results)

    if d.get("tags"):
        print(f"[{tab_label}]   技能: {', '.join(d['tags'])}")
    print(f"[{tab_label}]   JD: {len(d.get('jd', ''))} 字 ({time.time() - started_at:.0f}s)")

    _emit_detail_safe_event(event_callback, job, "completed", "ok", started_at)
    return True


def _reset_detail_session(ws, sid, sleeper, tab_label, event_callback=None):
    """重置详情抓取的 session 计数器（防 code:37 环境异常）。

    与列表翻页相同的策略：导航回 BOSS 首页 + 等待 + 滚动，
    让 BOSS 的 session 级请求计数归零，避免连续自动化访问触发拦截。
    """
    print(f"[{tab_label}] ⟳ session 重置：导航回首页...")
    _emit_runtime_safe_event(
        event_callback, "detail_session_reset", safe_hint="详情抓取 session 重置",
    )
    ws.send(CDP_CMD_PAGE_NAVIGATE, {"url": "https://www.zhipin.com/"}, sid)
    sleeper(random.uniform(5, 8), label="session_reset_wait")
    # 模拟真人滚动
    ws.eval_js("window.scrollBy(0, 300); void(0);", sid)
    sleeper(random.uniform(2, 4), label="session_reset_scroll")
    ws.eval_js("window.scrollBy(0, -200); void(0);", sid)
    sleeper(random.uniform(1, 2), label="session_reset_scroll2")
    print(f"[{tab_label}] ⟳ session 重置完成")


def _tab_worker(cdp_port, session_factory, work_queue, total, *,
                sleeper, event_callback, readiness_timeout_seconds,
                max_readiness_retries, inter_job_gap_range, stagger_range,
                tab_id, results_lock, results, output_path, degrade_event,
                trailing_wait, reset_every=3, degrade_reason=None,
                cancel_event=None, on_poll=None, worker_errors=None):
    """常驻 tab 工作线程：建池 → 错峰启动 → 循环领任务抓详情 → 补位节奏 → 关池。

    spec 007 ⑧：每个 tab 配一条独立工作线程 + 独立 CDP 会话（CDPSession 是
    WebSocket 连接，不能多线程共享）。线程安全通过 ``results_lock`` 保护共享
   状态（results/output_path/incr_request），``degrade_event`` 用于 login 墙降级。
    """
    tab_label = f"tab{tab_id + 1}"
    ws = session_factory(cdp_port)
    tid = None
    keep_tab_open = False  # 限流停工时限流页留在屏幕上，不关闭
    try:
        # 建池：createTarget + attach + visibility 注入（后台反爬）
        r = ws.send(CDP_CMD_CREATE_TARGET, {"url": CDP_ABOUT_BLANK, "background": True})
        tid = r["result"]["targetId"]
        r = ws.send(CDP_CMD_ATTACH_TARGET, {"targetId": tid, "flatten": True})
        sid = r["result"]["sessionId"]
        ws.send(CDP_CMD_ADD_SCRIPT_ON_NEW_DOC, {
            "source": (
                HIDDEN_DEFINE_JS +
                _VISIBILITY_STATE_JS +
                "Object.defineProperty(document, \'webkitHidden\', {get: () => false});"
                "Object.defineProperty(document, 'webkitVisibilityState', {get: () => 'visible'});"
            )
        }, sid)

        # 错峰启动：首批第 1 个立即导航，之后每个等随机 stagger_range 再开始
        if tab_id > 0:
            stagger = random.uniform(stagger_range[0], stagger_range[1])
            print(f"[{tab_label}] 错峰等待 {stagger:.1f}s 后开始")
            sleeper(stagger, label="stagger")

        jobs_done_on_tab = 0  # 本 tab 累计抓取数，用于触发 session 重置
        while not degrade_event.is_set():
            # programmatic 取消检查点：置位后通知所有线程停（on_poll 在并行下
            # 由调用方保证线程安全；CLI 不传，零影响）。
            if cancel_event is not None and cancel_event.is_set():
                degrade_event.set()
                break
            if on_poll is not None:
                on_poll()
            try:
                job, global_idx = work_queue.get_nowait()
            except Exception:
                break  # queue.Empty：队列空，退出
            is_last = global_idx == total - 1
            result = _scrape_detail_on_tab(
                ws, sid, job, global_idx, total,
                sleeper=sleeper, event_callback=event_callback,
                readiness_timeout_seconds=readiness_timeout_seconds,
                max_readiness_retries=max_readiness_retries,
                results_lock=results_lock, results=results,
                output_path=output_path, tab_label=tab_label,
            )
            if result == "login_required":
                # 登录墙：设置降级事件，其他线程看到后停止领新任务
                degrade_event.set()
                print(f"[{tab_label}] 登录墙触发降级，停止领新任务")
                break
            if result == "rate_limited":
                # 账号限流：全体停工；本 tab 停在限流页不关闭，
                # 不再开新页面撞限流（避免页面开关循环）
                if degrade_reason is not None:
                    degrade_reason["reason"] = "rate_limited"
                degrade_event.set()
                keep_tab_open = True
                print(f"[{tab_label}] 账号限流，停止抓取；限流页保留在屏幕上")
                break
            jobs_done_on_tab += 1
            # 每抓 reset_every 个详情重置一次 session（同列表翻页防 code:37 策略）：
            # 导航回 BOSS 首页 + 滚动，重置 BOSS 的 session 级请求计数器。
            if jobs_done_on_tab % reset_every == 0 and not is_last:
                _reset_detail_session(
                    ws, sid, sleeper, tab_label, event_callback=event_callback,
                )
            # 补位节奏：宁慢求稳，抓完空出来也等随机间隔再喂下一个
            if not is_last or trailing_wait:
                gap = random.uniform(inter_job_gap_range[0], inter_job_gap_range[1])
                print(f"[{tab_label}]   等待 {gap:.1f}s 后抓下一个...")
                sleeper(gap, label="inter_job_gap")
    except BaseException as exc:
        if worker_errors is not None:
            with results_lock:
                worker_errors.append(exc)
        else:
            raise
    finally:
        # 结束一次性关 tab + 关会话（限流停工时限流页保留不关）
        if tid is not None and not keep_tab_open:
            try:
                ws.send(CDP_CMD_CLOSE_TARGET, {"targetId": tid})
            except Exception:
                pass
        ws.close()
        print(f"[{tab_label}] 已关闭")


def scrape_details(list_data, max_details=None, output_path=None,
                   cdp_port=DEFAULT_CDP_PORT, fmt="json", *,
                   batch_size=5, session_factory=None, sleeper=None,
                   event_callback=None, readiness_timeout_seconds=12,
                   max_readiness_retries=1, inter_job_gap_range=(8, 15),
                   trailing_wait=False,
                   enable_parallel=False, tab_pool_size=5,
                   stagger_range=(5, 10), reset_every=3,
                   cancel_event=None, on_poll=None):
    """抓取岗位详情页并返回结构化结果。

    Policy v2 keyword-only parameters (feature 005) +
    spec 007 ⑧ 并行化（常驻 tab 池 + 工作线程 + 错峰/补位/降级）：

    - ``batch_size``: 串行模式每批最多 5 个候选岗位（默认 5，上限 5）。
    - ``session_factory``: 返回 CDP 会话的可调用对象，默认 ``CDPSession``。
      测试可通过它注入 fake 会话；CLI 调用不传该参数时走真实 ``CDPSession``。
    - ``sleeper``: ``sleeper(seconds, label=None)`` 用于所有受控等待，
      默认委托 ``time.sleep``。``label`` 用于测试区分 readiness_wait /
      inter_job_gap 等不同等待类型。
    - ``event_callback``: 每个岗位处理完成时回调一次，收到只含安全字段
      (kind/status/job_id/duration_ms/safe_code) 的 terminal 事件，
      不含 JD 正文、凭据或 PII。
    - ``readiness_timeout_seconds``: readiness 总等待预算，默认 12 秒。
    - ``max_readiness_retries``: 首次未就绪时最多进行 N 次受控滚动重试，
      默认 1。
    - ``inter_job_gap_range``: 同批次岗位间等待秒数范围，默认 (8, 15)。
    - ``trailing_wait``: 运行最后一项之后是否再等待一次 gap，默认 False。
    - ``enable_parallel``: spec 007 ⑧，默认 False 走原串行路径（保持向后兼容
      与 005 合约）；True 走常驻 tab 池并行（webui 调用处显式传 True）。
    - ``tab_pool_size``: 常驻 tab 数，默认 5，上限 10。
    - ``stagger_range``: 错峰启动间隔范围秒，默认 (5, 10)。

    实现要点：
    - 串行：每批最多 5 个候选；每批复用一个 CDP 会话，逐岗位开 target。
    - 并行（⑧）：N 个常驻 tab 各配一条工作线程 + 独立 CDP 会话；进队列前
      打乱 JD 列表；错峰启动 + 补位节奏；登录墙触发降级事件。
    - readiness-driven 提取：先探针，未就绪仅一次受控滚动重试。
    - 每个岗位发出且仅发出一个 terminal safe event。
    - 运行最后一项之后不再等待 gap（除非 trailing_wait=True）。
    """
    if not isinstance(batch_size, int) or batch_size < 1 or batch_size > 5:
        raise ValueError(
            f"batch_size must be an integer between 1 and 5, got {batch_size!r}"
        )
    if session_factory is None:
        session_factory = CDPSession
    if sleeper is None:
        sleeper = _default_scrape_sleeper
    if not inter_job_gap_range or len(inter_job_gap_range) != 2:
        raise ValueError("inter_job_gap_range must be a (min, max) pair")
    gap_lo, gap_hi = inter_job_gap_range
    if gap_lo < 0 or gap_hi < gap_lo:
        raise ValueError(
            f"inter_job_gap_range invalid: {inter_job_gap_range!r}"
        )
    if not isinstance(tab_pool_size, int) or tab_pool_size < 1 or tab_pool_size > 10:
        raise ValueError(
            f"tab_pool_size must be an integer between 1 and 10, got {tab_pool_size!r}"
        )
    if not stagger_range or len(stagger_range) != 2:
        raise ValueError("stagger_range must be a (min, max) pair")
    stg_lo, stg_hi = stagger_range
    if stg_lo < 0 or stg_hi < stg_lo:
        raise ValueError(f"stagger_range invalid: {stagger_range!r}")

    if not _facade()._run_active:
        begin_request_run()
    raw_jobs = list_data.get("jobs", [])
    if max_details:
        raw_jobs = raw_jobs[:max_details]
    if not output_path:
        output_path = default_output_path("details")

    # 按 job_link 去重，保持原始顺序
    seen_links = set()
    unique_jobs = []
    for job in raw_jobs:
        link = job.get("job_link", "")
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        unique_jobs.append(job)

    total = len(unique_jobs)
    results = []

    if enable_parallel and total > 0:
        # spec 007 ⑧：常驻 tab 池并行抓取
        print(f"\n=== 抓取岗位详情 ({total} 个, {tab_pool_size} tab 并行) ===\n")
        import queue as _queue_mod
        import threading
        results_lock = threading.Lock()
        worker_errors: list[BaseException] = []
        degrade_event = threading.Event()
        # 降级原因共享标记：区分登录墙降级与账号限流停工（限流需退出码 10）
        degrade_reason: dict[str, str] = {}
        work_queue = _queue_mod.Queue()
        # 随机顺序：进队列前打乱 JD 列表，请求顺序不可预测
        shuffled = unique_jobs[:]
        random.shuffle(shuffled)
        for idx, job in enumerate(shuffled):
            work_queue.put((job, idx))
        # 启动 N 个工作线程
        threads = []
        for tab_id in range(tab_pool_size):
            t = threading.Thread(
                target=_tab_worker,
                args=(cdp_port, session_factory, work_queue, total),
                kwargs={
                    "sleeper": sleeper,
                    "event_callback": event_callback,
                    "readiness_timeout_seconds": readiness_timeout_seconds,
                    "max_readiness_retries": max_readiness_retries,
                    "inter_job_gap_range": inter_job_gap_range,
                    "stagger_range": stagger_range,
                    "tab_id": tab_id,
                    "results_lock": results_lock,
                    "results": results,
                    "output_path": output_path,
                    "degrade_event": degrade_event,
                    "degrade_reason": degrade_reason,
                    "trailing_wait": trailing_wait,
                    "reset_every": reset_every,
                    "cancel_event": cancel_event,
                    "on_poll": on_poll,
                    "worker_errors": worker_errors,
                },
                name=f"detail-tab{tab_id + 1}",
                daemon=True,
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        # B050：worker 异常必须透出到主流程，不能静默死亡后把空批次当成功。
        if worker_errors:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            write_json_atomic(output_path, results)
            limit_error = next(
                (exc for exc in worker_errors if isinstance(exc, RequestLimitExceededError)),
                None,
            )
            raise limit_error if limit_error is not None else worker_errors[0]
        # programmatic 取消：线程退出后 flush 已抓 results 并抛 SearchCancelled
        if cancel_event is not None and cancel_event.is_set():
            write_json_atomic(output_path, results)
            raise SearchCancelled(MSG_USER_CANCELLED_SCRAPE)
        if degrade_reason.get("reason") == "rate_limited":
            # 账号限流：醒目报错 + 退出码 10（webui 据此分类
            # source_rate_limited 并停掉整个任务）；限流页已留在屏幕上。
            raise RiskControlError(
                "BOSS 账号/操作频繁被限流，已停止抓取（限流页保留在浏览器中）",
                code="source_rate_limited",
                scraped_count=len(results), output_path=output_path or "",
            )
        if degrade_event.is_set():
            print("\n⚠ 检测到登录墙，已降级停止；已抓取结果保留。")
    else:
        # 串行路径（enable_parallel=False 或 total=0 时的降级/测试用）
        print(f"\n=== 抓取岗位详情 ({total} 个, 每批 {batch_size}, 串行) ===\n")
        for batch_start in range(0, total, batch_size):
            batch = unique_jobs[batch_start:batch_start + batch_size]
            batch_idx = batch_start // batch_size
            print(f"--- 批次 {batch_idx + 1} ({len(batch)} 个岗位) ---")

            ws = session_factory(cdp_port)
            try:
                for i, job in enumerate(batch):
                    global_idx = batch_start + i
                    # programmatic 取消/轮询检查点（与 scrape_list 逐页检查点同语义）；
                    # CLI 不传 cancel_event/on_poll，行为与现状完全一致。
                    if cancel_event is not None and cancel_event.is_set():
                        write_json_atomic(output_path, results)
                        raise SearchCancelled(MSG_USER_CANCELLED_SCRAPE)
                    if on_poll is not None:
                        on_poll()
                    is_last_in_run = global_idx == total - 1
                    _scrape_one_detail(
                        ws, job, global_idx, total, results, output_path,
                        sleeper=sleeper,
                        event_callback=event_callback,
                        readiness_timeout_seconds=readiness_timeout_seconds,
                        max_readiness_retries=max_readiness_retries,
                        inter_job_gap_range=inter_job_gap_range,
                        is_last_in_run=is_last_in_run,
                        trailing_wait=trailing_wait,
                    )
            finally:
                ws.close()

    # 最终保存（dirname 为空时回退到当前目录，与循环内/其它写文件处保持一致）
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    write_json_atomic(output_path, results)
    print(f"\n详情已保存: {output_path}")

    if fmt == "csv":
        csv_path = output_path.rsplit(".", 1)[0] + ".csv"
        write_detail_csv(csv_path, results)
    return results
