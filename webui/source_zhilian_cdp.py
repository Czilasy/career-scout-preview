"""智联 CDP source adapter（021 拆分自 webui/source.py，tasks004）。

ZhilianCdpSource：构造校验、preflight 登录/DOM 判定、真实 API 列表、
__INITIAL_STATE__ 详情抓取与批量熔断复用；signal 映射常量与输入校验
助手同文件。默认 CLI runner 见 source_zhilian_defaults。
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections.abc import Callable
from typing import Any

from webui.source_breaker import SourceCircuitBreaker, SourceOutcome
from webui.source_boss_helpers import _safe_host
from webui.source_zhilian_defaults import (
    _default_zhilian_batch_detail_runner,
    _default_zhilian_detail_runner,
    _default_zhilian_list_runner,
    _default_zhilian_preflight_runner,
    _zhilian_failed_reason,
)


# ===========================================================================
# tasks004 — ZhilianCdpSource adapter（2026-08-04 真实页面核验后启用）
#
#   - 构造校验（T301/T302）：显式冻结端口、profile_key 边界、不回退 BOSS；
#   - preflight（T303）：zhilian_cdp_raw.preflight 真实登录/DOM 判定；
#   - fetch_list（T306/T307/T308）：真实 API 列表、统一字段、空结果证据；
#   - fetch_detail（T311）：真实 __INITIAL_STATE__ JD，不伪造正文；
#   - fetch_details_batch（T312）：熔断器复用；
#   - 日志安全（T304）：只含平台/阶段/计数/ID/URL host，脱敏。
# ===========================================================================

# 智联 CDP 冻结端口（与 BOSS 9222 隔离，避免 profile/平台边界泄漏）。
ZHILIAN_DEFAULT_CDP_PORT = 9223

# BOSS 默认端口，智联 adapter 构造时显式拒绝，避免隐式回退 BOSS 登录空间。
_BOSS_DEFAULT_CDP_PORT = 9222

# 智联平台岗位 URL host allowlist（与 webui/platforms.py 注册规则一致）。
_ZHILIAN_HOST_ALLOWLIST = frozenset({
    "www.zhaopin.com",
    "zhaopin.com",
    "m.zhaopin.com",
    "jobs.zhaopin.com",
    "fe-api.zhaopin.com",
    "i.zhaopin.com",
})

# AI 筛选字段黑名单：不得进入 adapter 列表参数（contracts/job-source.md）。
_ZHILIAN_AI_FILTER_KEYS = frozenset({
    "source_filters", "filters", "screening_fields",
    "salary", "experience", "degree", "industry",
    "scale", "stage", "company_nature",
})

# zhilian_cdp_raw.preflight signal → SAFE_FAILURE_CODES 映射。
_ZHILIAN_PREFLIGHT_SIGNAL_MAP = {
    "ok": None,
    "cdp_unavailable": "source_cdp_unavailable",
    "login_required": "source_login_required",
    "verification": "source_verification_required",
    "rate_limited": "source_rate_limited",
    "blocked": "source_blocked",
    "unreachable": "source_unreachable",
    "timeout": "source_timeout",
}

# 智联 preflight signal → 登录缓存事实态（016：受限不落缓存，反向映射只认两态）
_SIGNAL_TO_STATE = {
    "ok": "logged_in",
    "login_required": "not_logged_in",
    "cdp_unavailable": "unknown",
    "unreachable": "unknown",
    "timeout": "unknown",
}
_STATE_TO_SIGNAL = {
    "logged_in": "ok",
    "not_logged_in": "login_required",
}

# zhilian_cdp_raw.fetch_detail signal → SAFE_FAILURE_CODES 映射。
_ZHILIAN_DETAIL_SIGNAL_MAP = {
    "ok": None,
    "not_found": "source_not_found",
    "invalid_output": "source_invalid_output",
    "timeout": "source_timeout",
    "login_required": "source_login_required",
    "verification": "source_verification_required",
    "rate_limited": "source_rate_limited",
    "blocked": "source_blocked",
    "unreachable": "source_unreachable",
}

# zhilian_cdp_raw.fetch_list signal → SAFE_FAILURE_CODES 映射。
_ZHILIAN_LIST_SIGNAL_MAP = {
    "ok": None,
    "empty": None,  # 真实空结果走 empty_success 路径，由 marker fixture 解锁
    "login_required": "source_login_required",
    "verification": "source_verification_required",
    "rate_limited": "source_rate_limited",
    "blocked": "source_blocked",
    "unreachable": "source_unreachable",
    "timeout": "source_timeout",
    "invalid_output": "source_invalid_output",
}


def _zhilian_input_hash(payload: Any) -> str:
    """智联 input_hash：覆盖 platform/关键词/完整城市解析快照/页数。

    与 BOSS _input_hash 区别：智联 hash 必须包含 platform 字段和完整 city
    解析快照（name/platform_code/mapping_version），用于跨平台去重和
    缺城映射阻断校验。
    """
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _is_zhilian_host(url: str) -> bool:
    """URL host 是否在智联 allowlist 内（脱敏判定，不解析 path/query）。"""
    return _safe_host(url).lower() in _ZHILIAN_HOST_ALLOWLIST


def _zhilian_safe_log(*, stage: str, platform: str = "zhilian",
                     counts: dict | None = None, has_id: bool | None = None,
                     url_host: str | None = None, failed_code: str | None = None) -> str:
    """构造智联安全日志行：只含平台/阶段/计数/ID 是否存在/URL host/失败码。

    严禁包含 Cookie、JD 正文、页面正文、profile 路径、绝对路径、token 等。
    """
    parts = [f"platform={platform}", f"stage={stage}"]
    if failed_code:
        parts.append(f"failed_code={failed_code}")
    if counts:
        for key in sorted(counts):
            parts.append(f"{key}={counts[key]}")
    if has_id is not None:
        parts.append(f"has_id={'1' if has_id else '0'}")
    if url_host:
        # host 已经过 _safe_host 脱敏，不含 path/query。
        parts.append(f"url_host={url_host}")
    return " ".join(parts)


def _validate_zhilian_city_snapshot(city: Any) -> bool:
    """校验城市解析快照：必须是带 name/platform_code/mapping_version 的 dict。"""
    if not isinstance(city, dict):
        return False
    name = str(city.get("name") or "").strip()
    code = str(city.get("platform_code") or "").strip()
    if not name or not code:
        return False
    if "mapping_version" not in city:
        return False
    return True


def _validate_zhilian_plan_item(plan_item: Any) -> tuple[bool, str]:
    """校验 fetch_list 输入：dict、platform 一致、关键词、城市快照、页数、input_hash、无 AI filters。"""
    if not isinstance(plan_item, dict):
        return False, "plan_item_not_dict"
    if str(plan_item.get("platform") or "").strip() != "zhilian":
        return False, "platform_mismatch"
    keyword = str(plan_item.get("keyword") or "").strip()
    if not keyword:
        return False, "missing_keyword"
    city = plan_item.get("city")
    if not _validate_zhilian_city_snapshot(city):
        return False, "city_snapshot_invalid"
    target_pages = plan_item.get("target_pages")
    if not isinstance(target_pages, int) or isinstance(target_pages, bool) or target_pages <= 0:
        return False, "target_pages_invalid"
    input_hash = str(plan_item.get("input_hash") or "").strip()
    if not input_hash:
        return False, "missing_input_hash"
    # AI 筛选字段黑名单：任一出现即拒绝。
    for forbidden in _ZHILIAN_AI_FILTER_KEYS:
        if forbidden in plan_item:
            return False, f"ai_filter_present:{forbidden}"
    return True, "ok"


def _validate_zhilian_detail_input(job: Any) -> tuple[bool, str]:
    """校验 fetch_detail 输入：dict、platform 一致、platform_job_id、canonical_url、URL host。"""
    if not isinstance(job, dict):
        return False, "job_not_dict"
    if str(job.get("platform") or "").strip() != "zhilian":
        return False, "platform_mismatch"
    job_id = str(job.get("platform_job_id") or "").strip()
    if not job_id:
        return False, "missing_platform_job_id"
    canonical_url = str(job.get("canonical_url") or "").strip()
    if not canonical_url:
        return False, "missing_canonical_url"
    if not _is_zhilian_host(canonical_url):
        return False, "platform_url_mismatch"
    return True, "ok"


class ZhilianCdpSource:
    """智联 CDP adapter（tasks004，2026-08-04 真实页面核验后启用）。

    符合 ``JobSource`` Protocol（contracts/job-source.md）：携带 ``platform``
    和显式 ``cdp_port``，支持 ``preflight``、``fetch_list``、``fetch_detail``
    和 ``fetch_details_batch``。

    """

    platform: str = "zhilian"

    def __init__(
        self,
        *,
        browser_account: str,
        cdp_port: int,
        profile_key: str | None = None,
        breaker: SourceCircuitBreaker | None = None,
        preflight_runner: Callable[[int], str] | None = None,
        list_runner: Callable[[dict], tuple[str, list[dict]]] | None = None,
        detail_runner: Callable[[dict], tuple[str, dict]] | None = None,
        batch_detail_runner: Callable[[dict], tuple[list[tuple[str, dict]], str | None]] | None = None,
        run_id: str = "",
        cancel_event=None,
    ):
        if not browser_account or not str(browser_account).strip():
            raise ValueError("browser_account 必须非空")
        if not isinstance(cdp_port, int) or isinstance(cdp_port, bool) or cdp_port <= 0:
            raise ValueError("cdp_port 必须为正整数")
        if cdp_port == _BOSS_DEFAULT_CDP_PORT:
            # 显式拒绝 BOSS 默认端口，避免隐式回退 BOSS 登录空间。
            raise ValueError(
                f"智联 adapter 不得使用 BOSS 默认端口 {_BOSS_DEFAULT_CDP_PORT}，"
                f"请使用冻结端口 {ZHILIAN_DEFAULT_CDP_PORT}"
            )
        expected_profile_key = f"zhilian:{browser_account}"
        if profile_key is not None and profile_key != expected_profile_key:
            raise ValueError(
                f"profile_key 必须等于 {expected_profile_key!r}，"
                f"不得使用其它平台 profile_key"
            )
        self.browser_account = str(browser_account).strip()
        self.cdp_port = int(cdp_port)
        self.profile_key = expected_profile_key
        self.breaker = breaker or SourceCircuitBreaker()
        self.run_id = str(run_id or "").strip()
        # runner 注入：默认调用 zhilian_cdp_raw 的真实函数；测试通过替身绕过真实 CDP。
        # 测试通过注入替身绕过真实 CDP 调用。
        self._preflight_runner = preflight_runner or _default_zhilian_preflight_runner
        self._list_runner = list_runner or _default_zhilian_list_runner
        self._detail_runner = detail_runner or _default_zhilian_detail_runner
        self._batch_detail_runner = batch_detail_runner or _default_zhilian_batch_detail_runner
        # 025 立即停止：编排层（fetch_job_details）会以 ImmediateOnlyCancelEvent
        # 覆写此属性；批内检查点据此中断。graceful 时恒不置位，批照常跑完。
        self.cancel_event = cancel_event

    def _record_risk_signal(self, failed_code: str, reason: str = "") -> None:
        """高置信智联风控信号写 restricted 缓存与冷却，来源带 run_id。"""
        if failed_code not in ("source_rate_limited", "source_verification_required"):
            return
        # 经门面运行时取用：保住 patch("webui.source._record_risk_signals") 面
        from webui import source as _facade
        _facade._record_risk_signals(
            self.browser_account, self.platform, failed_code,
            reason or _zhilian_failed_reason(failed_code), run_id=self.run_id,
        )

    def _try_breaker_recovery(self) -> bool:
        """开闸且冷却期满时做一次 preflight 探测，通过才复位（020 US1）。

        冷却未满不做探测（避免空耗）；preflight 异常视为探测失败。
        """
        if not self.breaker.is_open() or not self.breaker.cooldown_elapsed():
            return False
        try:
            outcome = self.preflight()
        except Exception:
            return False
        return self.breaker.try_reset(outcome.ok)

    # ------------------------------------------------------------------
    # T301: 平台禁用门禁（新任务创建前由编排层调用）
    # ------------------------------------------------------------------
    @staticmethod
    def preflight_disabled_platform() -> SourceOutcome:
        """智联 enabled_for_new_tasks=False 时新任务创建前阻断。

        返回 ``platform_disabled`` 稳定错误码，不静默切换 BOSS。
        编排层在调用 ZhilianCdpSource 构造前应先调用本方法判断。
        """
        return SourceOutcome.failure(
            failed_code="platform_disabled",
            safe_log=_zhilian_safe_log(
                stage="preflight", failed_code="platform_disabled",
                counts={"platform_enabled": 0},
            ),
            failed_reason="智联平台当前禁用（enabled_for_new_tasks=False）",
        )

    # ------------------------------------------------------------------
    # T303: preflight 分类
    # ------------------------------------------------------------------
    def preflight(self) -> SourceOutcome:
        """检查智联冻结 CDP 端口、profile、登录态和平台可访问性。

        调用 ``_preflight_runner``（默认 zhilian_cdp_raw.preflight），按返回的
        signal 字符串映射到错误矩阵（登录/验证/限流/封禁/CDP/超时）。

        登录判定缓存优先（D3）：账号 × 平台 15 分钟 TTL 内命中直接复用
        DOM marker 探测结果，不反复导航搜索页。
        """
        cached = None
        if self.browser_account:
            from scripts.login_state_cache import read_cached_state
            cached = read_cached_state(self.browser_account, "zhilian")
        # 016：缓存只承载登录事实（logged_in/not_logged_in）；命中即复用，
        # 其余（含受限信号）一律真实探测，探测到的受限只在当次生效。
        if cached in ("logged_in", "not_logged_in"):
            return self._outcome_for_signal(_STATE_TO_SIGNAL[cached])
        try:
            signal = self._preflight_runner(self.cdp_port)
        except Exception:
            signal = "unreachable"
        signal = str(signal or "unreachable")
        state = _SIGNAL_TO_STATE.get(signal)
        if self.browser_account and state is not None:
            from scripts.login_state_cache import write_login_state
            write_login_state(self.browser_account, "zhilian", state)
        return self._outcome_for_signal(signal)

    def _outcome_for_signal(self, signal: str) -> SourceOutcome:
        """把智联 preflight signal 映射为统一 SourceOutcome。"""
        failed_code = _ZHILIAN_PREFLIGHT_SIGNAL_MAP.get(signal, "source_unknown_error")
        if failed_code is None:
            return SourceOutcome.success(
                safe_log=_zhilian_safe_log(
                    stage="preflight",
                    counts={"cdp_port": self.cdp_port, "ready": 1},
                ),
            )
        # 平台级 signal 推进熔断器（login/verification/rate_limited/blocked）。
        if failed_code in SourceCircuitBreaker.SIGNAL_CODES:
            self.breaker.record_signal(failed_code)
        self._record_risk_signal(failed_code, _zhilian_failed_reason(failed_code))
        return SourceOutcome.failure(
            failed_code=failed_code,
            safe_log=_zhilian_safe_log(
                stage="preflight", failed_code=failed_code,
                counts={"cdp_port": self.cdp_port},
            ),
            failed_reason=_zhilian_failed_reason(failed_code),
        )

    # ------------------------------------------------------------------
    # T306: fetch_list 输入校验
    # ------------------------------------------------------------------
    def fetch_list(
        self, plan_item: dict, *, on_page_completed: Callable[[dict], None] | None = None,
    ) -> SourceOutcome:
        """抓取智联岗位列表页。

        只接收关键词、规范城市解析快照和页数；真实 API 结果统一字段。
        runner 返回 empty signal 时必须携带 empty_evidence，否则按失败处理。
        ``on_page_completed``：每完成一页回调结构化页级事件。"""
        ok, reason = _validate_zhilian_plan_item(plan_item)
        if not ok:
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log=_zhilian_safe_log(
                    stage="list", failed_code="source_invalid_output",
                    counts={"reason": reason[:32]},
                ),
                failed_reason=f"输入校验失败: {reason}",
            )
        try:
            runner_item = dict(plan_item)
            runner_item["cdp_port"] = self.cdp_port
            runner_item["combo_key"] = str(plan_item.get("combo_key") or "") or ""
            runner_item["on_page_completed"] = on_page_completed
            runner_result = self._list_runner(runner_item)
            if isinstance(runner_result, tuple) and len(runner_result) == 2:
                signal, jobs = runner_result
                empty_evidence = None
            else:
                signal, jobs, empty_evidence = runner_result
        except Exception:
            return SourceOutcome.failure(
                failed_code="source_unknown_error",
                safe_log=_zhilian_safe_log(
                    stage="list", failed_code="source_unknown_error",
                ),
            )
        signal = str(signal or "invalid_output")
        if signal == "ok":
            # 字段归一化由 zhilian_cdp_raw._normalize_job 完成，这里透传。
            return SourceOutcome.success(
                jobs=list(jobs or []),
                safe_log=_zhilian_safe_log(
                    stage="list",
                    counts={"job_count": len(jobs or []),
                            "target_pages": plan_item.get("target_pages", 0)},
                ),
                input_hash=str(plan_item.get("input_hash") or ""),
            )
        if signal == "empty":
            if not isinstance(empty_evidence, dict) or not empty_evidence:
                return SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log=_zhilian_safe_log(
                        stage="list", failed_code="source_invalid_output",
                        counts={"reason": "empty_evidence_missing"},
                    ),
                    failed_reason="空结果证据缺失，不得返回 empty_success",
                )
            return SourceOutcome.empty_success(
                empty_evidence=empty_evidence,
                safe_log=_zhilian_safe_log(
                    stage="list", counts={"empty_result": 1},
                ),
                input_hash=str(plan_item.get("input_hash") or ""),
            )
        failed_code = _ZHILIAN_LIST_SIGNAL_MAP.get(signal, "source_unknown_error")
        if failed_code is None:
            failed_code = "source_unknown_error"
        if failed_code in SourceCircuitBreaker.SIGNAL_CODES:
            self.breaker.record_signal(failed_code)
        self._record_risk_signal(failed_code, _zhilian_failed_reason(failed_code))
        return SourceOutcome.failure(
            failed_code=failed_code,
            safe_log=_zhilian_safe_log(
                stage="list", failed_code=failed_code,
            ),
            failed_reason=_zhilian_failed_reason(failed_code),
        )

    # ------------------------------------------------------------------
    # T311: fetch_detail URL/平台/身份校验
    # ------------------------------------------------------------------
    def fetch_detail(
        self, job: dict, *, detail_output_path: str | None = None,
    ) -> SourceOutcome:
        """抓取智联单个岗位详情页。

        校验平台/URL/身份后调用真实 __INITIAL_STATE__ 抓取；
        无法取得真实 JD 时返回明确单项失败，不伪造正文。
        """
        ok, reason = _validate_zhilian_detail_input(job)
        if not ok:
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log=_zhilian_safe_log(
                    stage="detail", failed_code="source_invalid_output",
                    counts={"reason": reason[:32]},
                ),
                failed_reason=f"输入校验失败: {reason}",
            )
        try:
            runner_job = dict(job)
            runner_job["cdp_port"] = self.cdp_port
            signal, detail = self._detail_runner(runner_job, detail_output_path=detail_output_path)
        except Exception:
            return SourceOutcome.failure(
                failed_code="source_unknown_error",
                safe_log=_zhilian_safe_log(
                    stage="detail", failed_code="source_unknown_error",
                ),
            )
        signal = str(signal or "invalid_output")
        if signal == "ok":
            return SourceOutcome.success(
                detail=detail or {},
                safe_log=_zhilian_safe_log(
                    stage="detail",
                    counts={"has_detail": 1},
                    has_id=True,
                    url_host=_safe_host(str(job.get("canonical_url") or "")),
                ),
            )
        failed_code = _ZHILIAN_DETAIL_SIGNAL_MAP.get(signal, "source_unknown_error")
        if failed_code is None:
            failed_code = "source_unknown_error"
        if failed_code in SourceCircuitBreaker.SIGNAL_CODES:
            self.breaker.record_signal(failed_code)
        self._record_risk_signal(failed_code, _zhilian_failed_reason(failed_code))
        return SourceOutcome.failure(
            failed_code=failed_code,
            safe_log=_zhilian_safe_log(
                stage="detail", failed_code=failed_code,
                has_id=True,
                url_host=_safe_host(str(job.get("canonical_url") or "")),
            ),
            failed_reason=_zhilian_failed_reason(failed_code),
        )

    # ------------------------------------------------------------------
    # T312: fetch_details_batch 熔断器复用
    # ------------------------------------------------------------------
    def fetch_details_batch(
        self, jobs: list[dict], *, detail_output_path: str | None = None,
        on_item_done: Callable[[int], None] | None = None,
        **bounded_options,
    ) -> dict[str, SourceOutcome]:
        """批量抓取详情：单项异常继续，连续平台级 signal 触发熔断。

        熔断器打开后，后续岗位不再调用 runner，直接返回 source_blocked
        （可暂停 outcome，编排层可后续 retry）。

        ``on_item_done``：串行逐条抓取时每条完成后实时回调（1 起递增）；
        tab 池并行模式在批返回后按条回放（对齐 BOSS 子进程批返回语义），
        供编排层把进度回传给前端。

        高级设置 5 个 JD 参数经 pipeline_exec 统一映射后透传（与 BOSS 同一
        调用点）：gap_min/gap_max → 条间间隔；reset_every → 每抓 N 条导航回
        首页重置；tab_pool_size → 常驻 tab 数（1 走串行，>1 走并行池，
        钳制 1-10）。
        """
        results: dict[str, SourceOutcome] = {}
        gap_min = max(0.0, float(bounded_options.get("gap_min") or 0.0))
        gap_max = max(gap_min, float(bounded_options.get("gap_max") or gap_min))
        reset_every = max(1, int(bounded_options.get("reset_every") or 1))
        try:
            tab_pool_size = int(bounded_options.get("tab_pool_size") or 1)
        except (TypeError, ValueError):
            tab_pool_size = 1
        tab_pool_size = max(1, min(10, tab_pool_size))

        if tab_pool_size == 1:
            # 串行路径：复用现有逐条抓取（_detail_runner 替身），
            # gap_min/gap_max 作为条间间隔。
            for i, job in enumerate(jobs):
                # 025 立即停止检查点：置位即中断，剩余岗位不产出 outcome；
                # 上游（fetch_job_details）批返回后按停止语义作废整批。
                if self.cancel_event is not None and self.cancel_event.is_set():
                    break
                if not isinstance(job, dict):
                    results[f"idx{i}"] = SourceOutcome.failure(
                        failed_code="source_invalid_output",
                        safe_log=_zhilian_safe_log(
                            stage="batch", failed_code="source_invalid_output",
                            counts={"idx": i, "reason": "job_not_dict"},
                        ),
                    )
                    if on_item_done is not None:
                        try:
                            on_item_done(i + 1)
                        except Exception:
                            pass
                    continue
                job_id = str(job.get("platform_job_id") or "").strip()
                key = job_id or f"idx{i}"
                # 熔断器打开：不再调用 runner；失败码透传开闸信号（020 US1）。
                # 逐岗位门点只透传不复位，避免批内 N 次探测。
                if self.breaker.is_open():
                    results[key] = SourceOutcome.failure(
                        failed_code=self.breaker.open_failure_code(),
                        safe_log=_zhilian_safe_log(
                            stage="batch",
                            failed_code=self.breaker.open_failure_code(),
                            counts={"idx": i, "breaker_open": 1},
                        ),
                        failed_reason="熔断器已打开，连续平台级 signal 触发",
                    )
                    if on_item_done is not None:
                        try:
                            on_item_done(i + 1)
                        except Exception:
                            pass
                    continue
                results[key] = self.fetch_detail(job, detail_output_path=detail_output_path)
                if on_item_done is not None:
                    try:
                        on_item_done(i + 1)
                    except Exception:
                        pass
                if gap_min > 0 and i + 1 < len(jobs):
                    time.sleep(random.uniform(gap_min, gap_max))
            return results

        # 并行路径（tab_pool_size > 1）：常驻 tab 池，对齐 BOSS 并行分支。
        valid: list[tuple[int, str]] = []  # (i, key)
        for i, job in enumerate(jobs):
            if not isinstance(job, dict):
                results[f"idx{i}"] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log=_zhilian_safe_log(
                        stage="batch", failed_code="source_invalid_output",
                        counts={"idx": i, "reason": "job_not_dict"},
                    ),
                )
                if on_item_done is not None:
                    try:
                        on_item_done(i + 1)
                    except Exception:
                        pass
                continue
            job_id = str(job.get("platform_job_id") or "").strip()
            # 并行 runner 按 canonical_url 去重；缺失/重复会导致 per_item 与
            # valid 错位（结果张冠李戴），因此在此拦截（对齐 BOSS
            # job_missing_source_url / job_duplicate_in_batch 语义）。
            canonical = str(job.get("canonical_url") or "").strip()
            if not canonical:
                results[job_id or f"idx{i}"] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log=_zhilian_safe_log(
                        stage="batch", failed_code="source_invalid_output",
                        counts={"idx": i, "reason": "job_missing_canonical_url"},
                    ),
                )
                if on_item_done is not None:
                    try:
                        on_item_done(i + 1)
                    except Exception:
                        pass
                continue
            if any(str(jobs[j].get("canonical_url") or "").strip() == canonical
                   for j, _ in valid):
                results[job_id or f"idx{i}"] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log=_zhilian_safe_log(
                        stage="batch", failed_code="source_invalid_output",
                        counts={"idx": i, "reason": "job_duplicate_in_batch"},
                    ),
                )
                if on_item_done is not None:
                    try:
                        on_item_done(i + 1)
                    except Exception:
                        pass
                continue
            valid.append((i, job_id or f"idx{i}"))
        if not valid:
            return results
        # 熔断器已打开：整批不再调用 runner；先试恢复（冷却期满 + preflight
        # 通过才复位），仍开闸则失败码透传开闸信号（020 US1）。
        if self.breaker.is_open() and not self._try_breaker_recovery():
            for i, key in valid:
                results[key] = SourceOutcome.failure(
                    failed_code=self.breaker.open_failure_code(),
                    safe_log=_zhilian_safe_log(
                        stage="batch",
                        failed_code=self.breaker.open_failure_code(),
                        counts={"idx": i, "breaker_open": 1},
                    ),
                    failed_reason="熔断器已打开，连续平台级 signal 触发",
                )
                if on_item_done is not None:
                    try:
                        on_item_done(i + 1)
                    except Exception:
                        pass
            return results
        runner_jobs = []
        for i, _ in valid:
            runner_job = dict(jobs[i])
            runner_job["cdp_port"] = self.cdp_port
            runner_jobs.append(runner_job)
        try:
            per_item, degrade_signal = self._batch_detail_runner(
                {"jobs": runner_jobs},
                cdp_port=self.cdp_port,
                tab_pool_size=tab_pool_size,
                inter_job_gap_range=(gap_min, gap_max),
                reset_every=reset_every,
                event_callback=on_item_done,
                cancel_event=self.cancel_event,
            )
        except Exception:
            per_item, degrade_signal = [], "unreachable"
        recorded_batch_signals: set[str] = set()
        for idx, (i, key) in enumerate(valid):
            signal, detail = per_item[idx] if idx < len(per_item) else ("skipped", {})
            signal = str(signal or "invalid_output")
            if signal == "ok":
                results[key] = SourceOutcome.success(
                    detail=dict(detail or {}),
                    safe_log=_zhilian_safe_log(
                        stage="batch", counts={"has_detail": 1},
                        has_id=True,
                        url_host=_safe_host(str(jobs[i].get("canonical_url") or "")),
                    ),
                )
            elif signal == "skipped":
                # degrade 停工后未处理：CDP 建池失败归因环境、runner 异常归
                # 未知错误、其余风险降级归 source_blocked
                if degrade_signal == "cdp_unavailable":
                    skipped_code = "source_cdp_unavailable"
                elif degrade_signal == "unreachable":
                    skipped_code = "source_unknown_error"
                else:
                    skipped_code = "source_blocked"
                results[key] = SourceOutcome.failure(
                    failed_code=skipped_code,
                    safe_log=_zhilian_safe_log(
                        stage="batch", failed_code=skipped_code,
                        counts={"idx": i, "degraded": 1},
                    ),
                    failed_reason=_zhilian_failed_reason(skipped_code),
                )
            else:
                failed_code = _ZHILIAN_DETAIL_SIGNAL_MAP.get(signal, "source_unknown_error")
                if failed_code in SourceCircuitBreaker.SIGNAL_CODES:
                    self.breaker.record_signal(failed_code)
                if failed_code not in recorded_batch_signals:
                    recorded_batch_signals.add(failed_code)
                    self._record_risk_signal(failed_code, _zhilian_failed_reason(failed_code))
                results[key] = SourceOutcome.failure(
                    failed_code=failed_code,
                    safe_log=_zhilian_safe_log(
                        stage="batch", failed_code=failed_code,
                        counts={"idx": i, "signal": signal},
                        has_id=True,
                        url_host=_safe_host(str(jobs[i].get("canonical_url") or "")),
                    ),
                    failed_reason=_zhilian_failed_reason(failed_code),
                )
            if on_item_done is not None:
                try:
                    on_item_done(i + 1)
                except Exception:
                    pass
        return results
