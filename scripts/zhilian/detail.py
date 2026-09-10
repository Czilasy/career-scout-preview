# -*- coding: utf-8 -*-
"""智联详情域（031 B6 自 scripts/zhilian_cdp_raw.py 物理搬运）。

单条详情提取（``fetch_detail`` / ``_scrape_detail_on_ws``）与 T313 tab 池
并行批量抓取（``scrape_details_batch`` / ``_detail_tab_worker``），以及对齐
BOSS 语义的会话重置与默认等待器。

fetch_detail: ("ok", detail) / (signal, {})
scrape_details_batch: (per_item, degrade_signal)
"""

from __future__ import annotations

import json
import random
import sys
import time
from typing import Any

from scripts.zhilian.cdp import (
    DEFAULT_CDP_PORT,
    _BODY_TEXT_JS,
    _DETAIL_READY_JS,
    _LOCATION_HREF_JS,
    _VISIBILITY_OVERRIDE_JS,
    _ZHILIAN_LOGIN_PROBE_URL,
    _close_background_tab,
    _connect,
    _create_background_tab,
    _evaluate,
    _navigate,
    _send,
    _wait_expression,
)
from scripts.zhilian.detail_fields import (
    STAFF_CONST_JS,
    STAFF_FIELD_JS,
    merge_staff_fields,
)
# 详情提取复用列表域的 canonical URL 构造与风险信号判定（zhilian 域内
# 横向引用，不回溯门面）。
from scripts.zhilian.search import _canonical_job_url, _risk_signal
from webui.logging_setup import get_logger

_logger = get_logger(__name__)


# 平台级信号：任一命中即全体停工（对齐 BOSS 登录墙/限流降级语义）。
_DEGRADE_SIGNALS = frozenset({
    "login_required", "verification", "rate_limited", "blocked",
})

# 批次未跑完（worker 异常退出等）且没有任何平台级信号时的降级信号。
# 上层据此如实报「抓取中断」，不再兜底成「暂时无法确认平台状态」——
# 后者会把「我们没抓」误述成「平台状态不明」，现场无法归因。
_WORKER_INCOMPLETE = "worker_incomplete"


def _safe_print(text: str) -> None:
    """打印进度行；打印失败绝不抛出（进度输出是可选项）。

    实测根因：worker 线程的进度行曾含 GBK 无法表示的符号（``⟳``），在中文
    Windows 控制台下 ``print`` 直接抛 UnicodeEncodeError，标签线程当场死亡，
    该批剩余岗位无人抓取且不留任何失败码（被静默计入待确认）。任何输出失败
    都不得影响抓取正确性，故此处兜底为「可编码文本 + 再失败即忽略」。
    """
    try:
        print(text)
        return
    except Exception:
        # 控制台无法编码该字符：留痕后降级输出，绝不向上抛（进度输出是可选项）
        _logger.debug("进度行打印失败，改用可编码降级输出", exc_info=True)

    try:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = str(text).encode(encoding, "replace").decode(encoding, "replace")
        sys.stdout.write(safe + "\n")
    except Exception:
        _logger.debug("进度输出失败（忽略）", exc_info=True)


def _scrape_detail_on_ws(
    ws: Any, job: dict, *, sleeper: Any = None,
) -> tuple[str, dict]:
    """在已连接的 page WS 上抓取单岗位详情（导航+提取+校验+构建）。

    单条 ``fetch_detail`` 与并行 tab worker 共用（T313）；``sleeper``
    透传给就绪探针，测试可注入替身记录等待序列。返回 ``(signal, detail)``：
    - "ok"：detail 为结构化记录
    - 平台级：login_required / verification / rate_limited / blocked
    - 单条失败：not_found / invalid_output / timeout / unreachable
    """
    job_id = str(job.get("platform_job_id") or "").strip()
    canonical = str(job.get("canonical_url") or _canonical_job_url(job_id)).strip()
    if not job_id or not canonical:
        return "invalid_output", {}
    _navigate(ws, canonical)
    ready = _wait_expression(
        ws,
        _DETAIL_READY_JS,
        timeout=30,
        sleeper=sleeper,
    )
    if not ready:
        if sleeper is not None:
            sleeper(random.uniform(2, 4), label="detail_retry_wait")
        else:
            time.sleep(random.uniform(2, 4))
        _navigate(ws, canonical)
        ready = _wait_expression(
            ws,
            _DETAIL_READY_JS,
            timeout=25,
            sleeper=sleeper,
        )
    if not ready:
        body = str(_evaluate(ws, _BODY_TEXT_JS) or "")
        signal = _risk_signal(body, str(_evaluate(ws, _LOCATION_HREF_JS) or ""))
        return signal or "not_found", {}
    value = _evaluate(ws, (
        "(()=>{const s=window.__INITIAL_STATE__||{};"
        "const p=((s.jobDetail||{}).detailedPosition)||{};"
        "const c=((s.jobDetail||{}).detailedCompany)||{};"
        "const clean=(p.jobDesc||'').replace(/<br\\s*\\/?>/gi,'\\n').replace(/<[^>]+>/g,'').trim();"
        + STAFF_CONST_JS +
        "return {number:p.number||p.positionNumber||'',name:p.name||'',salary:p.salary||'',"
        "workingExp:p.workingExp||'',education:p.education||'',"
        "workCity:p.workCity||'',cityDistrict:p.cityDistrict||'',"
        "companyName:p.companyName||'',companySize:c.companySize||'',"
        "industry:c.industryName||'',jd:clean,positionStatus:p.positionStatus||0,"
        "jobStatus:p.jobStatus||0," + STAFF_FIELD_JS + "};})()"
    ))
    if not isinstance(value, dict):
        return "invalid_output", {}
    detail_id = str(value.get("number") or "").strip()
    jd = str(value.get("jd") or "").strip()
    if detail_id and detail_id != job_id:
        return "invalid_output", {}
    if not jd:
        if str(value.get("positionStatus") or "") in ("4", "5", "6") or str(value.get("jobStatus") or "") in ("4", "5", "6"):
            return "not_found", {}
        return "invalid_output", {}
    detail = {
        "platform": "zhilian",
        "platform_job_id": detail_id or job_id,
        "title": str(value.get("name") or job.get("title") or "").strip(),
        "company": str(value.get("companyName") or job.get("company") or "").strip(),
        "salary": str(value.get("salary") or job.get("salary") or "").strip(),
        "location": " ".join(part for part in (
            str(value.get("workCity") or "").strip(),
            str(value.get("cityDistrict") or "").strip(),
        ) if part),
        "experience": str(value.get("workingExp") or job.get("experience") or "").strip(),
        "degree": str(value.get("education") or job.get("degree") or "").strip(),
        "jd": jd,
        "canonical_url": _canonical_job_url(detail_id or job_id),
        "source_url": _canonical_job_url(detail_id or job_id),
        "extra": dict(job.get("extra") or {}),
    }
    # 028 B081：staff 活跃字段（文本+lastOnlineTime 毫秒），逻辑在 detail_fields
    return "ok", merge_staff_fields(detail, value)


def fetch_detail(job: dict, *, detail_output_path: str | None = None) -> tuple[str | None, dict]:
    del detail_output_path  # 兼容 source 接口；智联 in-process 直接返回结果
    job_id = str(job.get("platform_job_id") or "").strip()
    canonical = str(job.get("canonical_url") or _canonical_job_url(job_id)).strip()
    if not job_id or not canonical:
        return "invalid_output", {}
    port = DEFAULT_CDP_PORT if not job.get("cdp_port") else int(job["cdp_port"])
    try:
        ws = _connect(port)
    except Exception:
        return "cdp_unavailable", {}

    try:
        return _scrape_detail_on_ws(ws, job)
    except TimeoutError:
        return "timeout", {}
    except Exception:
        return "unreachable", {}
    finally:
        try:
            ws.close()
        except Exception:
            _logger.debug("CDP 会话关闭失败（best-effort 忽略）", exc_info=True)


# ---------------------------------------------------------------------------
# T313: 并行 tab 池抓取（对齐 BOSS scrape_details 并行分支）
# ---------------------------------------------------------------------------

def _default_sleeper(seconds: float, label: str | None = None) -> None:
    """默认 sleeper：兼容 ``label`` 关键字（对齐 BOSS ``_default_scrape_sleeper``）。

    worker 里以 ``sleeper(x, label=...)`` 调用，而 ``time.sleep`` 不接受
    关键字参数，直接当默认值会在线程里抛 TypeError 杀掉 worker。
    """
    time.sleep(seconds)


def _reset_detail_session(ws: Any, sleeper: Any, tab_label: str) -> None:
    """导航回智联首页重置详情抓取上下文（对齐 BOSS ``_reset_detail_session``）。

    防御性措施：每抓 ``reset_every`` 条导航回首页 + 等待 + 滚动，打散请求
    序列，降低连续详情页访问触发 EdgeOne/限流的风险。智联无 BOSS code:37
    式 session 计数依据，真实效果由 tab=2 实跑核验。
    """
    _safe_print(f"[{tab_label}] session 重置：导航回首页...")
    _navigate(ws, _ZHILIAN_LOGIN_PROBE_URL)
    sleeper(random.uniform(3, 5), label="session_reset_wait")
    _evaluate(ws, "window.scrollBy(0, 300); void(0);")
    sleeper(random.uniform(1.5, 2.5), label="session_reset_scroll")
    _evaluate(ws, "window.scrollBy(0, -200); void(0);")
    sleeper(random.uniform(1, 1.5), label="session_reset_scroll2")
    _safe_print(f"[{tab_label}] session 重置完成")


def _detail_tab_worker(cdp_port: int, connector: Any, work_queue: Any,
                       total: int, *, sleeper: Any,
                       inter_job_gap_range: tuple[float, float],
                       stagger_range: tuple[float, float], tab_id: int,
                       reset_every: int, degrade_event: Any,
                       degrade_reason: dict[str, str], results_lock: Any,
                       results: dict[int, tuple[str, dict]],
                       event_callback: Any = None,
                       cancel_event: Any = None,
                       worker_errors: Any = None) -> None:
    """常驻 tab 工作线程：建池 → 错峰启动 → 循环领任务抓详情 → 重置 → 关池。

    与 BOSS ``_tab_worker`` 同构，连接走智联 page 级 WS（无 sessionId）：
    - ``connector(cdp_port)`` 建 background tab，返回 ``(ws, target_id)``
    - 每条 ``(signal, detail)`` 在 ``results_lock`` 保护下写入
      ``results[orig_idx]``，主线程 join 后按原顺序聚合
    - 平台级 signal 置 ``degrade_event`` 全体停工；单条失败
      （not_found/invalid_output/timeout/unreachable）不中断
    - ``cancel_event``（025 立即停止）：循环头检查点，置位即停工退出；
      与 degrade 同粒度（下一条边界生效），已抓结果保留
    - ``worker_errors``（对齐 BOSS B050）：线程级意外异常收集到调用方列表，
      绝不静默死亡——静默死亡会让该批剩余岗位以「无原因」计入待确认
    """
    tab_label = f"tab{tab_id + 1}"
    ws = None
    target_id = None
    try:
        ws, target_id = connector(cdp_port)
    except Exception:
        _safe_print(f"[{tab_label}] 建池失败（CDP 不可达）")
        degrade_reason["reason"] = "cdp_unavailable"
        degrade_event.set()
        return
    try:
        _send(ws, "Page.addScriptToEvaluateOnNewDocument", {
            "source": _VISIBILITY_OVERRIDE_JS,
        })
        # 错峰启动：首批第 1 个立即开始，之后每个等随机 stagger 再领任务
        if tab_id > 0:
            stagger = random.uniform(stagger_range[0], stagger_range[1])
            _safe_print(f"[{tab_label}] 错峰等待 {stagger:.1f}s 后开始")
            sleeper(stagger, label="stagger")
        jobs_done_on_tab = 0
        while not degrade_event.is_set():
            # 025 立即停止检查点：置位即停工，剩余任务由调用方按 skipped 占位
            if cancel_event is not None and cancel_event.is_set():
                break
            try:
                job, seq, orig_idx = work_queue.get_nowait()
            except Exception:
                break  # 队列空，退出
            is_last = seq == total - 1
            # 单条异常不杀 worker：CDP 命令超时/求值异常映射为单条失败继续
            try:
                signal, detail = _scrape_detail_on_ws(ws, job, sleeper=sleeper)
            except TimeoutError:
                signal, detail = "timeout", {}
            except Exception:
                signal, detail = "unreachable", {}
            signal = str(signal or "invalid_output")
            with results_lock:
                results[orig_idx] = (signal, dict(detail or {}))
            # 026：逐条完成回调（仅作卡死防护心跳，in-process 无子进程 stdout）。
            # 调用方传 on_item_done 时每条实时触发；异常一律吞掉不中断抓取。
            if event_callback is not None:
                try:
                    event_callback()
                except Exception:
                    _logger.debug("事件回调执行失败（不阻断抓取）", exc_info=True)

            if signal in _DEGRADE_SIGNALS:
                _safe_print(f"[{tab_label}] 命中平台级信号 {signal}，触发降级停工")
                degrade_reason["reason"] = signal
                degrade_event.set()
                break
            jobs_done_on_tab += 1
            # 每抓 reset_every 个详情导航回首页重置一次（对齐 BOSS 语义）
            if jobs_done_on_tab % reset_every == 0 and not is_last:
                try:
                    _reset_detail_session(ws, sleeper, tab_label)
                except Exception:
                    # 重置只是「打散请求频率」的防御动作：失败只降级记录，
                    # 绝不允许把整条标签线程连带报废——线程一死，本批剩余
                    # 岗位就再也无人抓取，且不留失败码（历史故障形态）。
                    _logger.warning(
                        "详情会话重置失败，跳过本次重置继续抓取 tab=%s",
                        tab_label, exc_info=True,
                    )
            # 补位节奏：抓完等随机间隔再领下一个
            if not is_last:
                gap = random.uniform(inter_job_gap_range[0], inter_job_gap_range[1])
                _safe_print(f"[{tab_label}]   等待 {gap:.1f}s 后抓下一个...")
                sleeper(gap, label="inter_job_gap")
    except BaseException as exc:  # noqa: BLE001 — 对齐 BOSS：异常必须留痕
        # 线程级意外异常不得静默：收集给调用方决定成败语义（BOSS B050 同构）。
        # 不置 degrade_event：其余标签可能仍健康，继续把队列剩余任务抓完。
        if worker_errors is not None:
            with results_lock:
                worker_errors.append(exc)
        else:
            raise
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                _logger.debug("CDP 会话关闭失败（best-effort 忽略）", exc_info=True)

        if target_id is not None:
            _close_background_tab(cdp_port, target_id)
        _safe_print(f"[{tab_label}] 已关闭")


def scrape_details_batch(list_data, max_details=None, output_path=None,
                         cdp_port=DEFAULT_CDP_PORT, *,
                         tab_pool_size=5, inter_job_gap_range=(2, 9),
                         stagger_range=(3, 8), reset_every=4,
                         event_callback=None, sleeper=None, connector=None,
                         cancel_event=None):
    """tab 池并行抓取岗位详情；返回 ``(per_item, degrade_signal)``。

    - ``per_item``：按输入顺序的 ``[(signal, detail), ...]``，每条独立成败；
      degrade 停工后未处理的任务以 ``("skipped", {})`` 占位，由调用方映射
      ``source_blocked``。
    - ``degrade_signal``：平台级信号（login_required/verification/
      rate_limited/blocked/cdp_unavailable）或 ``worker_incomplete``（批次
      未跑完且无平台级信号）或 None，用于调用方推进熔断器与归类失败原因。
    - ``tab_pool_size``：常驻 tab 数，1-10；``reset_every``：每抓 N 条导航回
      首页重置；``inter_job_gap_range``/``stagger_range``：条间间隔与错峰
      启动范围；``sleeper``/``connector``：测试注入点（等待替身 / 建池替身）。
    - ``cancel_event``：可选取消信号（025 立即停止）。置位后 worker 在下一条
      边界停工退出，剩余任务按 skipped 占位；不回写、不影响已抓结果。
    - 标签线程意外退出时，未出结果的任务会**补跑一轮**（用户取消、平台级
      降级除外）：线程死亡不该导致丢批，只有补跑仍拿不到才报 ``worker_incomplete``。

    ``output_path``/``event_callback`` 为兼容参数：智联 in-process 直接返回
    结果，不写盘、不产出事件文件（与 ``fetch_detail`` 现状一致）。单条失败
    不中断整体；平台级 signal 触发全体停工，已抓结果保留。
    """
    del output_path  # 兼容参数：直接返回结果，不写盘
    import queue as _queue_mod
    import threading

    if not isinstance(tab_pool_size, int) or tab_pool_size < 1 or tab_pool_size > 10:
        raise ValueError(
            f"tab_pool_size must be an integer between 1 and 10, got {tab_pool_size!r}"
        )
    if not isinstance(reset_every, int) or reset_every < 1:
        raise ValueError(f"reset_every must be an integer >= 1, got {reset_every!r}")
    if not inter_job_gap_range or len(inter_job_gap_range) != 2:
        raise ValueError("inter_job_gap_range must be a (min, max) pair")
    if inter_job_gap_range[0] < 0 or inter_job_gap_range[1] < inter_job_gap_range[0]:
        raise ValueError(f"inter_job_gap_range invalid: {inter_job_gap_range!r}")
    if not stagger_range or len(stagger_range) != 2:
        raise ValueError("stagger_range must be a (min, max) pair")
    if stagger_range[0] < 0 or stagger_range[1] < stagger_range[0]:
        raise ValueError(f"stagger_range invalid: {stagger_range!r}")
    if sleeper is None:
        sleeper = _default_sleeper
    if connector is None:
        connector = _create_background_tab

    raw_jobs = list_data.get("jobs", []) if isinstance(list_data, dict) else list_data
    if max_details:
        raw_jobs = raw_jobs[:max_details]
    # 按 canonical_url 去重，保持原始顺序
    seen_links = set()
    unique_jobs = []
    for job in raw_jobs:
        url = str(job.get("canonical_url") or "").strip()
        if not url or url in seen_links:
            continue
        seen_links.add(url)
        unique_jobs.append(job)

    total = len(unique_jobs)
    if total == 0:
        return [], None

    results_lock = threading.Lock()
    degrade_event = threading.Event()
    degrade_reason: dict[str, str] = {}
    results: dict[int, tuple[str, dict]] = {}
    worker_errors: list[BaseException] = []

    def _spawn_pool(pending: list[tuple[int, dict]]) -> list[BaseException]:
        """为一批任务开一轮 tab 池并 join，返回本轮 worker 异常。

        ``pending`` 是 ``[(orig_idx, job), ...]``；``orig_idx`` 仍是该任务在
        ``unique_jobs`` 中的位置，因此补跑轮次的结果能直接并回 ``results``。
        """
        if not pending:
            return []
        errors: list[BaseException] = []
        work_queue = _queue_mod.Queue()
        # 随机顺序进队列（请求顺序不可预测），但保留原始下标用于结果聚合
        shuffled = list(pending)
        random.shuffle(shuffled)
        for seq, (orig_idx, job) in enumerate(shuffled):
            work_queue.put((job, seq, orig_idx))
        threads = []
        for tab_id in range(tab_pool_size):
            t = threading.Thread(
                target=_detail_tab_worker,
                args=(cdp_port, connector, work_queue, len(shuffled)),
                kwargs={
                    "sleeper": sleeper,
                    "inter_job_gap_range": inter_job_gap_range,
                    "stagger_range": stagger_range,
                    "tab_id": tab_id,
                    "reset_every": reset_every,
                    "degrade_event": degrade_event,
                    "degrade_reason": degrade_reason,
                    "results_lock": results_lock,
                    "results": results,
                    "event_callback": event_callback,
                    "cancel_event": cancel_event,
                    "worker_errors": errors,
                },
                name=f"zhilian-detail-tab{tab_id + 1}",
                daemon=True,
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        return errors

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    worker_errors.extend(_spawn_pool(list(enumerate(unique_jobs))))

    # ---- 修因（而不只是归类）：线程意外死亡不得丢批 ----
    # 旧行为：标签线程一死，它领走/未领的任务永久消失，整批剩余岗位直接进
    # 「待确认」（历史每批丢 11 条）。这里对未出结果的任务补跑一轮：能救回来
    # 的先救回来，只有补跑仍拿不到时才算真的中断。
    if not degrade_reason.get("reason") and not _cancelled():
        missing = [idx for idx in range(total) if idx not in results]
        if missing:
            _logger.warning(
                "详情批次有 %d/%d 条未出结果，补跑一轮（标签线程异常时防丢批）",
                len(missing), total,
            )
            worker_errors.extend(
                _spawn_pool([(idx, unique_jobs[idx]) for idx in missing])
            )

    per_item = [
        results[idx] if idx in results else ("skipped", {})
        for idx in range(total)
    ]
    # worker 异常必须留痕（BOSS B050 同构）：先前标签线程静默死亡会让整批
    # 剩余岗位以「无原因」进待确认，事后无法归因。补跑已救回全部任务时降级
    # 为告警——「死过但没丢」与「死过且丢了」是两回事。
    if worker_errors:
        _log = _logger.error if len(results) < total else _logger.warning
        _log(
            "智联详情标签线程异常退出 %d 个（已抓 %d/%d）：%r",
            len(worker_errors), len(results), total, worker_errors[0],
        )
    signal = degrade_reason.get("reason")
    if signal is None and len(results) < total:
        if cancel_event is not None and cancel_event.is_set():
            # 用户取消：剩余任务由上层按取消语义处理，不冒充抓取失败。
            signal = None
        else:
            # 批次没跑完又没有平台级信号：如实报「抓取中断」，避免上层兜底成
            # 「暂时无法确认平台状态」——那是「平台状态不明」，与「我们没抓」
            # 是两件事，混在一起现场无法归因（历史 110 条待确认即由此而来）。
            signal = _WORKER_INCOMPLETE
    return per_item, signal
