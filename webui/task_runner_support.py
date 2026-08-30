"""任务运行支撑域（031 B6 自 webui/task_runners.py 物理搬运）。

承载 TaskRunner / WorkbenchRunner 共用的模块级助手：stdout 转日志缓冲、
硬停与风控原因分类、产物读取与时间解析、载荷组装与 key 脱敏、路径常量。

本模块不 import task_runners / workbench_runner（引用方向单向向下）。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import boss_cdp_raw as boss
from scripts.boss_cdp_signals import parse_failure_line
from webui.error_registry import resolve_code
from webui.logging_setup import get_logger

_logger = get_logger(__name__)


_MSG_USER_CANCELLED_TASK = "用户取消任务"


def _has_unlock_signal(text: str) -> bool:
    """高置信解封时间信号：完整未来时间点或明确解封/解锁时间文案。"""
    if not text:
        return False
    try:
        if boss.parse_unlock_time(text) is not None:
            return True
    except Exception:
        _logger.debug("解封时间解析失败，回退关键词判定", exc_info=True)

    lowered = str(text).lower()
    return any(kw in lowered for kw in ("解封时间", "解封后", "解封于", "解锁时间"))


def _classify_scrape_block(err_msg: str) -> str:
    """hard_stop_code 缺失时的兜底：只解析结构化失败行，不再全文猜码。

    016-error-module-rework：run_search 硬停时总携带 hard_stop_code；
    本函数仅防御性兜底，输出全文关键词扫描路径已删除（岗位文案里的
    "429/滑块"等词曾把软失败误判成限流硬停）。
    """
    if not err_msg:
        return ""
    parsed = parse_failure_line(err_msg)
    if parsed is not None:
        return resolve_code(parsed[0])
    return ""


# 风控异常 reason → 安全失败码（合同 inprocess-runner §3 的防御性兜底）。
# 016：RiskControlError 自带 code 后本表仅在异常对象缺码时使用；
# 顺序敏感：限流优先于验证码，避免"频繁 + 滑块"文案被误判为验证码。
_RISK_CONTROL_REASON_PATTERNS = (
    ("source_login_required", (
        "登录已失效", "登录过期", "未登录", "登 录 失效", "登 录 已失效", "请先登录", "wt2", "401", "login expired",
    )),
    ("source_rate_limited", (
        "操作频繁", "频繁访问", "访问频繁", "稍后再试", "访问受限", "异常流量", "账号受限", "限流",
        "rate limit", "too many", "429", "http 403", "http 412", "http 418",
        "403 forbidden", "412 precondition", "418 im a teapot",
    )),
    ("source_verification_required", (
        "验证码", "滑块", "滑动验证", "captcha", "slider", "geetest",
    )),
)


def _classify_risk_control_reason(reason: str) -> str:
    """把 RiskControlError.reason 文本映射到安全失败码；未命中返回 source_unknown_error。

    用于 in_process 模式异常映射（合同 §3 表 RiskControlError 行）。
    子进程模式按退出码 10 单独分类，不走本函数。
    """
    if not reason:
        return "source_unknown_error"
    if _has_unlock_signal(reason):
        return "source_rate_limited"
    text = str(reason).lower()
    for code, keywords in _RISK_CONTROL_REASON_PATTERNS:
        for kw in keywords:
            if kw.lower() in text:
                return code
    return "source_unknown_error"


class _StdoutToLogBuffer(boss._ThreadAwareStdout):
    """捕获任务线程 print 输出并按行转发到 store.append_log（合同 §2.2）。

    供 setup_chrome 等「无 on_log 参数的库式函数」使用：以 buffer 自身
    作上下文管理器（带守卫恢复）把既有 print 按行转发，不修改既有
    print 语句；其他线程的输出转发回真 stdout，避免日志串线。
    行格式与子进程模式 stdout 完全一致。
    """

    def __init__(self, store, task_id):
        super().__init__()
        self._store = store
        self._task_id = task_id
        self._buf = []

    def write(self, text):
        if not text:
            return 0
        if threading.get_ident() != self._tid:
            if self._fallback is not None:
                try:
                    self._fallback.write(text)
                except Exception:
                    _logger.debug("降级输出通道写入失败（忽略）", exc_info=True)

            return len(text)
        parts = text.splitlines(keepends=True)
        for part in parts:
            self._buf.append(part)
            if part.endswith("\n") or part.endswith("\r"):
                line = "".join(self._buf).rstrip("\r\n")
                if line.strip():
                    self._store.append_log(self._task_id, line)
                self._buf = []
        return len(text)

    def flush(self):
        if threading.get_ident() != self._tid:
            super().flush()
            return
        if self._buf:
            line = "".join(self._buf).rstrip("\r\n")
            if line.strip():
                self._store.append_log(self._task_id, line)
            self._buf = []


SCRAPER = PROJECT_ROOT / "scripts" / "boss_cdp_raw.py"
DEFAULT_STATE_DIR = Path(
    os.environ.get("CAREER_SCOUT_STATE_DIR")
    or os.environ.get("BOSS_WEBUI_STATE_DIR")
    or os.path.expanduser("~/.career-scout/webui")
)


def _theme_path() -> Path:
    """主题偏好文件：与登录态/冷却等同级放 ~/.career-scout/theme.json。

    不用 DEFAULT_STATE_DIR（webui 子目录）：主题属于用户偏好，与桌面窗口
    状态（desktop_window.json）同级，便于用户直接查看与备份。
    """
    return Path(os.path.expanduser("~/.career-scout")) / "theme.json"


_FINE_VERDICTS = frozenset({"match", "not_match", "mismatch", "uncertain"})


def _split_resume_verdicts(verdicts: dict) -> tuple[dict, dict]:
    """Split stored verdicts into fine-screen and rough-screen verdicts."""
    fine = {}
    rough = {}
    for job_id, verdict in (verdicts or {}).items():
        value = verdict if isinstance(verdict, dict) else {"verdict": str(verdict)}
        target = fine if str(value.get("verdict") or "") in _FINE_VERDICTS else rough
        target[str(job_id)] = value
    return fine, rough


def _resume_dropped_from_verdicts(raw_jobs, verdicts: dict) -> list[dict]:
    """Reconstruct previously dropped jobs when a resume skips rough screening."""
    dropped = []
    for job in raw_jobs or []:
        if not isinstance(job, dict):
            continue
        jid = str(job.get("job_id") or "")
        verdict = verdicts.get(jid) or {}
        if isinstance(verdict, dict) and str(verdict.get("verdict") or "") == "dropped":
            dropped.append({
                "job_id": jid,
                "title": job.get("title") or "",
                "reason": verdict.get("reason") or "粗筛移除",
                "canonical_url": job.get("source_url") or job.get("job_link") or "",
            })
    return dropped


def _iso_epoch_ms(value):
    """Convert an ISO timestamp string (or epoch ms int) to epoch milliseconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return int(parsed.timestamp() * 1000)


def _optional_positive_int(value, field, *, maximum=None):
    """Parse a user-controlled optional execution limit without coercion surprises."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是正整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是正整数") from None
    if str(value).strip() != str(parsed) or parsed < 1:
        raise ValueError(f"{field} 必须是正整数")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field} 不能超过 {maximum}")
    return parsed


def _env(correlation_id: str = ""):
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    if correlation_id:
        environment["CAREER_SCOUT_CORRELATION_ID"] = str(correlation_id)
    return environment


def _read_json(path, default):
    path = Path(path) if path else None
    if not path or not path.is_file():
        return default
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return default


def _request_hostname(host):
    host = str(host or "").lower()
    if host.startswith("[") and "]" in host:
        return host[1:host.index("]")]
    return host.split(":", 1)[0]


def _task_payload(store, task_id):
    task = store.get_task(task_id)
    list_payload = _read_json(task.get("output_path"), {})
    if not isinstance(list_payload, dict):
        list_payload = {}
    jobs = list_payload.get("jobs") if isinstance(list_payload.get("jobs"), list) else []
    details = _read_json(task.get("detail_output_path"), [])
    if not isinstance(details, list):
        details = []
    return task, list_payload, jobs, details


def _mask_key(key: str) -> str:
    """打码 API key：保留前4后4字符，中间星号。短 key 全星号。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]
