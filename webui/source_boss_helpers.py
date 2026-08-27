"""BOSS source 域共享助手（021 拆分自 webui/source.py）。

input_hash 计算与字段归一化、失败分类（结构化失败行权威）、退出原因、
登录事实回写、脱敏日志助手。被 source_boss_cdp / source_boss_cdp_detail /
source_zhilian_cdp / source_fake 共享；不依赖任何 adapter。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from scripts.boss_cdp_signals import FAILURE_LINE_PREFIX, parse_failure_line
from webui.error_registry import ERROR_USER_MESSAGES, resolve_code


# Valid filter fields passable to the scraper CLI (excludes city which is positional).
SCRAPER_FILTER_FIELDS = ("salary", "experience", "degree", "industry", "scale", "stage")
SCRAPER_FILTER_FIELDS = ("salary", "experience", "degree", "industry", "scale", "stage", "multiBusinessDistrict")


# ---------------------------------------------------------------------------
# Helpers (no PII / JD body leakage)
# ---------------------------------------------------------------------------

def _input_hash(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _normalize_job_fields(job: dict) -> dict:
    """Normalize BOSS-specific field names to the unified JobSource interface.

    The BOSS scraper (scripts/boss_cdp_raw.py) returns jobs with field names
    matching the BOSS API: ``encrypt_job_id``, ``job_link``, ``boss_name``.
    Downstream code (fetch_detail, _persist_jobs, build_snapshot) expects the
    unified names: ``job_id``, ``source_url``, ``company``.

    This function returns a *copy* of the job dict with the unified fields
    populated when they are missing but the BOSS-specific alias is present.
    Original BOSS-specific fields are preserved for diagnostic/compatibility.
    A job that already has the unified fields (e.g. from FakeJobSource) is
    returned unchanged (still copied, to avoid mutating caller's dict).
    """
    if not isinstance(job, dict):
        return {}
    normalized = dict(job)
    # job_id: prefer existing, fall back to encrypt_job_id
    if not normalized.get("job_id"):
        alt = normalized.get("encrypt_job_id") or normalized.get("encryptJobId")
        if alt:
            normalized["job_id"] = str(alt)
    # source_url: prefer existing, fall back to job_link / url
    if not normalized.get("source_url"):
        alt = normalized.get("job_link") or normalized.get("url")
        if alt:
            normalized["source_url"] = str(alt)
    # company: prefer existing, fall back to boss_name / brand_name
    if not normalized.get("company"):
        alt = normalized.get("boss_name") or normalized.get("brand_name")
        if alt:
            normalized["company"] = str(alt)
    # welfare: BOSS 列表福利标签（"五险一金 | 双休"）→ extra.welfare_list
    # （specs/004: 归一化层补齐，extra 全链路已持久化；缺失/空时不写键，不编造）
    raw_welfare = normalized.get("welfare")
    if isinstance(raw_welfare, str):
        items = [part.strip() for part in raw_welfare.split("|") if part.strip()]
        if items:
            extra = normalized.get("extra")
            if not isinstance(extra, dict):
                extra = {}
                normalized["extra"] = extra
            extra["welfare_list"] = items
    return normalized
def _safe_tail(text: str, *, max_chars: int = 300) -> str:
    """Return last ``max_chars`` characters, stripped of newlines.

    Used only for safe log lines; the captured subprocess output never
    includes resume text or credentials because the scraper does not
    receive them. We still truncate to keep logs bounded.
    """
    if not text:
        return ""
    tail = text[-max_chars:].replace("\n", " ").replace("\r", " ").strip()
    return tail


# scraper 退出码 → 用户可读原因
_EXIT_REASONS = {
    1: "登录态失效或环境异常",
    2: "连不上调试浏览器（Chrome 未启动或端口不通）",
    3: "抓取参数错误（CLI 参数校验失败）",
    4: "结果文件写入失败",
    10: "触发风控/限流（验证码、连续空页或 HTTP 拦截）",
}

# 退出码 1（登录态失效或环境异常）缺失败行时的兜底：只认高置信短语，
# 避免正文里单个“登录/login/cookie”字眼把正常页面误判成登录失效（B027 回归）。
# 016：限流/验证码不再靠输出全文关键词分类，判定收敛到脚本侧实锤分档。
_LOGIN_REQUIRED_HI_CONFIDENCE_KEYWORDS = (
    "401",
    "登录态失效", "登录失效", "登录已失效", "请先登录", "未登录",
    "未检测到 boss直聘登录状态",
    "cookie 失效", "cookie已失效", "cookie 已失效",
)


def _format_inprocess_failure(code: str, hint: str) -> str:
    """in-process 模式的失败输出：一行结构化失败行 + 可读原因。"""
    return f"{FAILURE_LINE_PREFIX} code={code} hint={str(hint or '')[:120]}"


def _classify_failed_code(returncode: int, captured: str) -> str:
    """以脚本输出的结构化失败行为唯一权威分类来源（016-error-module-rework）。

    失败行格式见 scripts/boss_cdp_signals.py（``__CAREERSCOUT_FAILED__``）。
    缺行时按退出码粗分兜底：2/3/11 精确；10 一律 source_status_unclear
    （旧版全文关键词扫描会把岗位标题/薪资里的"429/滑块"等词误判成
    限流/验证码，已删除）；1 只认高置信登录短语。

    退出码含义（boss_cdp_raw.py）：
      1  — 登录态失效或环境异常
      2  — 连不上调试浏览器（CDPUnavailableError）
      3  — 抓取参数错误（CLI 参数校验失败）
      10 — 实锤受限/验证码/登录失效，或无法确认状态（RiskControlError）
      11 — 单次抓取运行请求数达到上限（RequestLimitExceededError）
    """
    parsed = parse_failure_line(captured)
    if parsed is not None:
        return resolve_code(parsed[0])
    if returncode == 2:
        return "source_cdp_unavailable"
    if returncode == 3:
        return "source_invalid_output"
    if returncode == 11:
        return "source_request_limit_exceeded"
    if returncode == 10:
        return "source_status_unclear"
    if returncode == 1:
        text = (captured or "").lower()
        if any(kw in text for kw in _LOGIN_REQUIRED_HI_CONFIDENCE_KEYWORDS):
            return "source_login_required"
        return "source_unknown_error"
    return "source_unknown_error"


def _record_risk_signals(account, platform, failed_code, captured, run_id=""):
    """抓取失败时的登录事实回写（016-error-module-rework 后的收敛语义）。

    - 仅 source_login_required → 登录缓存写 not_logged_in（登录与否是事实，可缓存）；
    - 受限类错误码不再写任何持久状态：无 restricted 缓存、无冷却
      （受限是瞬态且判定只在当次任务内生效，避免跨任务假拦截）。
    account 为空时跳过（CLI 直连场景不记录账号维度）。
    """
    if not account:
        return
    from scripts.login_state_cache import write_login_state
    if failed_code == "source_login_required":
        write_login_state(account, platform, "not_logged_in")


def _record_success_signal(account, platform):
    """列表抓取持续拿到明文工资 → 登录缓存写 logged_in（D3 信号回写）。"""
    if not account:
        return
    from scripts.login_state_cache import write_login_state
    write_login_state(account, platform, "logged_in")


def _exit_reason(returncode: int, captured: str) -> str:
    """从失败行或输出尾部提取一句用户可读的失败原因。"""
    parsed = parse_failure_line(captured)
    if parsed is not None:
        code, hint = parsed
        label = ERROR_USER_MESSAGES.get(resolve_code(code), resolve_code(code))
        return f"{label}｜{hint}" if hint else label
    base = _EXIT_REASONS.get(returncode, f"scraper 异常退出（code={returncode}）")
    tail = _safe_tail(captured, max_chars=150)
    if tail:
        return f"{base}｜{tail}"
    return base


def _safe_host(url: str) -> str:
    """Return only the hostname of a URL for log lines, never the path."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc or ""
    except (ValueError, TypeError):
        return ""
