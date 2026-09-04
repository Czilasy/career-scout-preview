"""多账号轮询分摊调度域（Spec 038 B091）。

职责分层（宪法 III：本域纯调度 + IO 编排，R1/R2 执行域只接线）：

- 纯调度：``RotationQueue`` 旋转队列、``plan_round_robin`` 段规划，可脱离
  文件系统/浏览器单测。
- IO 编排：``ListRobin``（R1 按页子范围 + 账号切换 + hash 重算）、
  ``DetailRobin``（R2 按条配额推进 + 撞墙换号），source 克隆与浏览器 profile
  切换经门面运行时取用，保住既有 patch 面。
- 账号簿侧：``rate_limited`` 持久化（撞墙写、成功清），经
  ``pipeline_exec_accounts`` 落盘。

引用方向（单向）：``pipeline_exec_search``/``pipeline_exec_details``
→ 本域 → ``resume_identity``（取号范围限定预选池，US2 接线点）。
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Callable

from webui.logging_setup import get_logger
from webui.account_round_robin_observability import RoundRobinWhitebox

_logger = get_logger(__name__)

# 默认每轮配额取范围中值（FR-004/FR-005/A9）。
DEFAULT_R1_QUOTA = 25
DEFAULT_R2_QUOTA = 150
R1_QUOTA_MIN, R1_QUOTA_MAX = 1, 50
R2_QUOTA_MIN, R2_QUOTA_MAX = 1, 300

# ---------------------------------------------------------------------------
# 纯调度：旋转队列与段规划（无 IO，可单测）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PoolEntry:
    """预选账号 + 该账号每轮配额。"""
    account_id: str
    quota: int

    def __post_init__(self) -> None:
        if not str(self.account_id or "").strip():
            raise ValueError("account_id 必须非空")
        # 不在此校验 quota 范围（构造时宽松，clamp 在读配额处统一）；
        # quota<=0 会在 RotationQueue.reserve 时被当 1 处理。

@dataclass(frozen=True)
class Segment:
    """一段轮询份额：[start, start+count) 由 account_id 抓。"""
    account_id: str
    start: int
    count: int

@dataclass(frozen=True)
class DetailAllocation:
    """一次 R2 请求被分给一个账号的岗位片段及尚未领取的尾部。"""
    account_id: str
    entries: list[Any]
    tail: list[Any]


def plan_round_robin(total: int, entries: list[PoolEntry]) -> list[Segment]:
    """把 ``total`` 个单位按轮询分摊为段（纯逻辑）。

    语义（FR-003/007/008）：
    - 每轮每账号最多抓其 ``quota`` 个单位；抓完轮转到队尾，配额下一轮重置；
    - 多轮覆盖任意总量（自动回到 1 号再来一轮）；
    - 末轮不足配额时由下一个账号抓完零头自然结束（不设余数规则）；
    - 单账号多轮：``entries`` 只有一个账号时该账号可连续多轮。

    ``total<=0`` 或 ``entries`` 为空 → 返回空列表。
    """
    if total <= 0 or not entries:
        return []
    # 复制可变状态：剩余配额随轮转推进，轮转后重置为原始 quota（≥1）。
    remaining = [max(1, int(e.quota)) for e in entries]
    order = list(entries)
    cursor = 0  # 下一个待分配单位
    segments: list[Segment] = []
    # 防御：单轮总配额为 0（全部 quota<=0 → clamp 成 1，不会发生），仍兜底限次。
    guard = 0
    while cursor < total and guard < total * (len(order) + 1) + 8:
        guard += 1
        idx = 0
        # 找队首可用（remaining>0）；全 0 时全部重置（新一轮）。
        while idx < len(order) and remaining[idx] <= 0:
            idx += 1
        if idx >= len(order):
            remaining = [max(1, int(e.quota)) for e in order]
            idx = 0
        take = min(remaining[idx], total - cursor)
        segments.append(Segment(order[idx].account_id, cursor, take))
        cursor += take
        remaining[idx] -= take
        # 该账号本轮配额耗尽 → 轮转：移到队尾并重置配额（下一轮生效）。
        if remaining[idx] <= 0:
            e = order.pop(idx)
            r = remaining.pop(idx)
            order.append(e)
            remaining.append(max(1, int(e.quota)))
            _ = r  # 旧 remaining 已迁出
    return segments


class RotationQueue:
    """运行时旋转队列：``reserve(n)`` 取号、``block_head`` 撞墙移出。

    与 ``plan_round_robin`` 同语义，但面向总量未知（R1 翻页）的增量取号：
    队首预留 ``min(n, 剩余配额)`` 个单位；耗尽则轮转到队尾重置配额；
    撞墙调 ``block_head`` 把队首移出队列（不再轮转）。队列空=全撞完。
    """

    def __init__(self, entries: list[PoolEntry]):
        if not entries:
            raise ValueError("RotationQueue 需至少一个账号")
        self._entries: list[PoolEntry] = list(entries)
        self._remaining: list[int] = [max(1, int(e.quota)) for e in entries]
        self._blocked: list[PoolEntry] = []
        self._round = 1
        self._round_seen: set[str] = set()
        self._next_round_pending = False
        self._last_round = 1
        self._last_remaining = 0

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def head_account(self) -> str | None:
        return self._entries[0].account_id if self._entries else None

    @property
    def blocked_accounts(self) -> list[str]:
        return [e.account_id for e in self._blocked]

    def reserve(self, n: int) -> tuple[PoolEntry | None, int]:
        """队首预留至多 n 个单位；返回 ``(队首账号, 实际取数)``。

        队空返回 ``(None, 0)``。取数耗尽本轮配额时自动轮转（队尾重置配额）。
        """
        if not self._entries:
            return None, 0
        take = max(1, min(int(n) if n and n > 0 else 1, self._remaining[0]))
        entry = self._entries[0]
        if self._next_round_pending and entry.account_id in self._round_seen:
            self._round += 1
            self._round_seen.clear()
        self._next_round_pending = False
        self._round_seen.add(entry.account_id)
        self._last_round = self._round
        self._remaining[0] -= take
        self._last_remaining = max(0, self._remaining[0])
        if self._remaining[0] <= 0:
            e = self._entries.pop(0)
            r = self._remaining.pop(0)
            self._entries.append(e)
            self._remaining.append(max(1, int(e.quota)))
            self._next_round_pending = True
            _ = r
        return entry, take

    @property
    def last_round(self) -> int:
        return self._last_round

    @property
    def last_remaining(self) -> int:
        return self._last_remaining

    def block_head(self) -> PoolEntry | None:
        """队首撞墙：移出队列（不再轮转）。返回被移除账号，队空返回 None。"""
        return self.block_account(self.head_account)

    def block_account(self, account_id: str | None) -> PoolEntry | None:
        """移出指定账号，即使它已因预留耗尽而不再是队首。"""
        wanted = str(account_id or "")
        for index, entry in enumerate(self._entries):
            if entry.account_id != wanted:
                continue
            removed = self._entries.pop(index)
            self._remaining.pop(index)
            self._blocked.append(removed)
            return removed
        return None

    def has_alternative(self) -> bool:
        """当前是否还有别的账号可换（撞墙换号是否有意义）。"""
        return len(self._entries) > 1


# ---------------------------------------------------------------------------
# 撞墙判定（复用 error_registry；排除浏览器失联——它走 BrowserRecovery）
# ---------------------------------------------------------------------------

_BROWSER_LOST_CODES = frozenset({"cdp_unavailable", "source_cdp_unavailable"})


def is_wall_code(failed_code: object) -> bool:
    """硬阻断即撞墙（验证码/限流/IP 风控等），但不含浏览器失联。

    浏览器失联由编排层 ``BrowserRecovery`` 重启兜底，不经账号切换处理；
    其余系统性硬信号视为该账号被平台风控盯上 → 撞墙换号。
    """
    code = str(failed_code or "").strip()
    if not code or code in _BROWSER_LOST_CODES:
        return False
    try:
        from webui.error_registry import resolve_code, SYSTEMIC_BLOCK_CODES
        return resolve_code(code) in SYSTEMIC_BLOCK_CODES
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 账号簿侧：读池 + rate_limited 持久化（best-effort）
# ---------------------------------------------------------------------------

def _clamp_quota(value: object, lo: int, hi: int, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _normalize_pool(raw: object, default_order: int) -> dict:
    """把账号簿里的 pool 字段归一为 {selected, order, r1_quota, r2_quota}。

    缺字段一律补默认（FR-015/016/017/018：默认全选、按书序、配额取中值）。
    """
    if not isinstance(raw, dict):
        raw = {}
    selected = raw.get("selected")
    return {
        "selected": bool(selected) if selected is not None else True,
        "order": int(raw.get("order") if raw.get("order") is not None else default_order),
        "r1_quota": _clamp_quota(raw.get("r1_quota"), R1_QUOTA_MIN, R1_QUOTA_MAX, DEFAULT_R1_QUOTA),
        "r2_quota": _clamp_quota(raw.get("r2_quota"), R2_QUOTA_MIN, R2_QUOTA_MAX, DEFAULT_R2_QUOTA),
    }


def load_pool_entries(role: str, *, accounts_path: object = None) -> list[PoolEntry]:
    """读账号簿，返回按勾选顺序的预选池（role='R1'/'R2' 选配额字段）。

    FR-015/017：默认全选；FR-007：按勾选顺序。仅返回 selected=True 的账号。
    """
    from webui.pipeline_exec_accounts import load_browser_accounts
    accounts = load_browser_accounts(accounts_path)
    keyed: list[tuple[int, str, int]] = []
    fallback_order = 0
    for aid, item in accounts.items():
        pool = _normalize_pool(item.get("pool"), fallback_order)
        fallback_order += 1
        if not pool["selected"]:
            continue
        quota = pool["r1_quota"] if role == "R1" else pool["r2_quota"]
        keyed.append((pool["order"], str(aid), int(quota)))
    keyed.sort(key=lambda t: (t[0], t[1]))
    return [PoolEntry(aid, q) for _, aid, q in keyed]


def mark_account_rate_limited(account_id: str, *, accounts_path: object = None) -> None:
    """撞墙账号写 rate_limited=True（FR-014）。best-effort，失败仅记日志。"""
    if not account_id:
        return
    try:
        from webui.pipeline_exec_accounts import set_account_rate_limited
        set_account_rate_limited(account_id, rate_limited=True, path=accounts_path)
    except Exception:
        _logger.debug("mark_account_rate_limited 失败（best-effort 忽略）", exc_info=True)


def clear_account_rate_limited(account_id: str, *, accounts_path: object = None) -> None:
    """该账号成功使用后清 rate_limited（自愈语义）。best-effort。"""
    if not account_id:
        return
    try:
        from webui.pipeline_exec_accounts import set_account_rate_limited
        set_account_rate_limited(account_id, rate_limited=False, path=accounts_path)
    except Exception:
        _logger.debug("clear_account_rate_limited 失败（best-effort 忽略）", exc_info=True)


# ---------------------------------------------------------------------------
# IO 编排：浏览器 profile 切换 + source 克隆
# ---------------------------------------------------------------------------

def _switch_browser_account(account_id: str, platform: str, cdp_port: object) -> bool:
    """绑定全局 profile 到目标账号并确保 Chrome 就绪（同端口换 profile）。

    复用既有 ``set_active_cdp_data_dir`` + 门面 ``ensure_chrome_ready``：
    后者检测到端口 Chrome 的 user-data-dir 与新 profile 不符时自动关旧开新。
    返回是否就绪。
    """
    from webui.pipeline_exec_accounts import (
        resolve_browser_account, set_active_cdp_data_dir,
    )
    from webui import pipeline_exec as _facade
    profile = resolve_browser_account(account_id) or ""
    if not profile:
        return False
    if str(platform or "boss") == "zhilian":
        from webui.platforms import derive_zhilian_profile_dir
        profile = derive_zhilian_profile_dir(profile)
    set_active_cdp_data_dir(profile)
    port = int(cdp_port) if cdp_port else None
    ok, _err = _facade.ensure_chrome_ready(port, minimize_after_launch=True)
    return bool(ok)

def clone_source(source: Any, account_id: str, *, run_id: str = "") -> Any:
    """克隆 source 用于另一个账号（同平台/同端口，新 browser_account）。

    BOSS/智联两平台构造参数差异由平台分支吸收；测试替身无 ``platform``
    形态时回到该分支兜底。克隆携带原 source 的 cancel_event（已由编排层
    包 ImmediateOnlyCancelEvent，切换后同语义）。
    """
    platform = str(getattr(source, "platform", "boss") or "boss")
    cancel_event = getattr(source, "cancel_event", None)
    if platform == "zhilian":
        from webui.source import ZhilianCdpSource
        return ZhilianCdpSource(
            browser_account=str(account_id),
            cdp_port=int(getattr(source, "cdp_port", 9223) or 9223),
            profile_key=f"zhilian:{account_id}",
            breaker=None,
            preflight_runner=getattr(source, "_preflight_runner", None),
            list_runner=getattr(source, "_list_runner", None),
            detail_runner=getattr(source, "_detail_runner", None),
            batch_detail_runner=getattr(source, "_batch_detail_runner", None),
            run_id=str(run_id or getattr(source, "run_id", "") or ""),
            cancel_event=cancel_event,
        )
    cls = type(source)
    kwargs: dict[str, Any] = {
        "browser_account": str(account_id),
        "run_id": str(run_id or getattr(source, "run_id", "") or ""),
        "cancel_event": cancel_event,
    }
    cdp_port = getattr(source, "cdp_port", None)
    if cdp_port is not None:
        kwargs["cdp_port"] = int(cdp_port)
    try:
        parameters = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        parameters = {}
    aliases = {
        "executor": "_executor",
        "runner": "_runner",
    }
    for name, parameter in parameters.items():
        if name in {"self", "browser_account", "run_id", "cancel_event", "cdp_port"}:
            continue
        if parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY):
            continue
        attr = aliases.get(name, name)
        if not hasattr(source, attr):
            continue
        if name == "runner" and getattr(source, "_use_default_runner", False):
            continue
        value = getattr(source, attr)
        if name == "breaker":
            value = None
        if name == "executor" and value is not None:
            from webui.process_executor import ScraperExecutor
            value = ScraperExecutor(
                max_output_bytes=getattr(value, "max_output_bytes", 1_000_000),
                poll_seconds=getattr(value, "poll_seconds", 0.05),
            )
        if name == "env" and isinstance(value, dict) and run_id:
            value = {
                **value,
                "CAREER_SCOUT_CORRELATION_ID": str(run_id),
                "CAREER_SCOUT_TASK_ID": str(run_id),
            }
        if value is not None:
            kwargs[name] = value
    return cls(**kwargs)

# ---------------------------------------------------------------------------
# R1：列表轮询分摊编排
# ---------------------------------------------------------------------------

def _recompute_input_hash(plan_item: dict, subrange_target: int) -> str:
    """子范围 fetch_list 的 input_hash 重算（target_pages 随子范围变）。

    BOSS：``_combo_hash(keyword, city, pages, source_filters)``；
    智联：``_zhilian_input_hash({platform, keyword, city, target_pages, route_city_code})``。
    """
    platform = str(plan_item.get("platform") or "boss")
    keyword = str(plan_item.get("keyword") or "")
    if platform == "zhilian":
        from webui.source import _zhilian_input_hash
        return _zhilian_input_hash({
            "platform": "zhilian",
            "keyword": keyword,
            "city": plan_item.get("city") or {},
            "target_pages": int(subrange_target),
            "route_city_code": str(plan_item.get("route_city_code") or ""),
        })
    from webui.pipeline_exec_artifacts import _combo_hash
    return _combo_hash(keyword, str(plan_item.get("city") or ""),
                      int(subrange_target),
                      plan_item.get("source_filters") or {})

class ListRobin:
    """R1 列表轮询分摊编排：把一个 combo 按配额拆成子范围跨账号抓。

    - 单 combo 内按页拆子范围（start_page..subrange_target），每账号每轮抓
      其 R1 配额页就换下一个；总量不够自动多轮覆盖。
    - 撞墙（硬阻断）触发顺次换预选账号，剩余份额接力（同 start 重抓）；
      全撞完返回失败 outcome 交既有暂停路径收场（FR-013）。
    - 成功使用账号时清 rate_limited（自愈）；撞墙时写 rate_limited（FR-014）。
    """

    def __init__(self, source: Any, entries: list[PoolEntry], *, run_id: str = "",
                 switch_event_store: Any = None):
        if not entries:
            raise ValueError("ListRobin 需至少一个预选账号")
        self._queue = RotationQueue(entries)
        self._run_id = str(run_id or getattr(source, "run_id", "") or "")
        self._platform = str(getattr(source, "platform", "boss") or "boss")
        self._cdp_port = getattr(source, "cdp_port", None)
        self._whitebox = RoundRobinWhitebox(
            switch_event_store, self._run_id, phase="R1",
            platform=self._platform, entries=entries,
        )
        self._pending_switch_reason = None
        # 当前账号的 source 缓存：首个账号直接用传入 source（避免无谓克隆）。
        self._sources: dict[str, Any] = {}
        head = self._queue.head_account
        self._active_account = head
        if head is not None:
            self._sources[head] = source
        self._progress: dict[str, int] = {}  # combo_key → 已抓到的下一页

    def _source_for(self, account_id: str, template: Any) -> Any:
        """取该账号的 source：每次跨账号使用前重新绑定 profile。"""
        src = self._sources.get(account_id)
        if self._active_account != account_id:
            from_account = str(self._active_account or "")
            reason = str(self._pending_switch_reason or "quota")
            if not _switch_browser_account(account_id, self._platform, self._cdp_port):
                self._whitebox.switch(
                    from_account=from_account, to_account=account_id,
                    reason=reason, result="failed",
                )
                return None
            self._active_account = account_id
            self._whitebox.switch(
                from_account=from_account, to_account=account_id,
                reason=reason, result="succeeded",
            )
            self._pending_switch_reason = None
        if src is not None:
            return src
        src = clone_source(template, account_id, run_id=self._run_id)
        self._sources[account_id] = src
        return src

    def fetch_list(self, source: Any, plan_item: dict, *,
                   on_page_completed: Callable[[dict], None] | None = None) -> Any:
        """对一个 combo做跨账号轮询分摊；返回单个 SourceOutcome。

        成功时返回末段子范围 outcome（BOSS 输出文件累计全量岗位；智联 runner
        按 existing_jobs 合并），调用方按既有路径 merge/落库。
        """
        combo_key = str(plan_item.get("combo_key")
                         or f"{plan_item.get('keyword', '')}|{plan_item.get('city', '')}")
        plan_start = max(1, int(plan_item.get("start_page") or 1))
        start = max(plan_start, int(self._progress.get(combo_key, 1)))
        target = max(1, int(plan_item.get("target_pages") or 1))
        existing_jobs = list(plan_item.get("existing_jobs") or [])
        last_outcome = None
        while start <= target:
            entry, take = self._queue.reserve(target - start + 1)
            if entry is None:
                break  # 队空（全撞完）——交下面失败路径
            sub_end = start + take - 1
            self._whitebox.allocation(
                entry.account_id, round_no=self._queue.last_round, count=take,
                remaining=self._queue.last_remaining,
                start_page=start, end_page=sub_end,
            )
            sub_plan = dict(plan_item)
            sub_plan["start_page"] = start
            sub_plan["target_pages"] = sub_end
            sub_plan["input_hash"] = _recompute_input_hash(plan_item, sub_end)
            # 智联：把已抓岗位接力给 runner 做去重合并
            if existing_jobs:
                sub_plan["existing_jobs"] = list(existing_jobs)
            src = self._source_for(entry.account_id, source)
            if src is None:
                # profile/Chrome 绑定失败是环境问题，不得误标平台限流。
                self._queue.block_account(entry.account_id)
                self._pending_switch_reason = "binding_failure"
                if not self._queue.head_account:
                    return _source_unavailable()
                continue

            def _page_event(event: dict | None) -> None:
                nonlocal start, existing_jobs
                event = dict(event or {})
                checkpoint = int(event.get("resume_page") or 0)
                if checkpoint > start:
                    start = min(target + 1, checkpoint)
                    self._progress[combo_key] = start
                snapshot = event.get("jobs_snapshot")
                if isinstance(snapshot, list) and snapshot:
                    existing_jobs = list(snapshot)
                if on_page_completed is not None:
                    on_page_completed(event)

            outcome = src.fetch_list(sub_plan, on_page_completed=_page_event)
            if getattr(outcome, "ok", False):
                start = sub_end + 1
                self._progress[combo_key] = start
                clear_account_rate_limited(entry.account_id)
                # 累积已抓岗位供后续子范围去重（BOSS 输出文件自带累计，
                # 智联需显式接力）
                jobs = getattr(outcome, "jobs", None)
                if isinstance(jobs, list) and jobs:
                    existing_jobs = list(jobs)
                last_outcome = outcome
                continue
            # 失败：撞墙→顺次换预选账号；浏览器失联/软失败→原样上交
            if is_wall_code(getattr(outcome, "failed_code", "")):
                mark_account_rate_limited(entry.account_id)
                self._queue.block_account(entry.account_id)
                next_account = self._queue.head_account
                self._whitebox.handoff(
                    blocked_account=entry.account_id,
                    blocked_reason=str(getattr(outcome, "failed_code", "") or "wall"),
                    to_account=str(next_account or ""),
                    remaining=max(0, target - start + 1),
                    result="queued" if next_account else "paused",
                )
                self._pending_switch_reason = "wall"
                if self._queue.head_account is None:
                    return outcome  # 全撞完
                continue  # 同 start 重抓，剩余份额接力
            return outcome  # 软失败/浏览器失联：交既有 BrowserRecovery/hard_stop
        if last_outcome is not None:
            return last_outcome
        # 未抓任何东西（target<start 或首轮即空）→ 交回原 source 的等价空成功
        return _passthrough(source, plan_item, on_page_completed)

def _source_unavailable():
    """所有候选账号都无法绑定 profile 时交给既有环境暂停路径。"""
    from webui.source_breaker import SourceOutcome
    return SourceOutcome.failure(
        failed_code="source_cdp_unavailable",
        safe_log="round_robin_account_binding_failed",
    )

def _passthrough(source: Any, plan_item: dict, on_page_completed):
    """轮询未实际推进（如 target<start）时的等价透传。"""
    # 正常路径下 plan_item 起止合理时不会落到此；保留兜底以保契约稳定。
    return source.fetch_list(plan_item, on_page_completed=on_page_completed)


# ---------------------------------------------------------------------------
# R2：详情轮询分摊编排
# ---------------------------------------------------------------------------

class DetailRobin:
    """R2 详情轮询分摊：按批推进配额，撞墙换号接力本批未抓项。

    - ``current_source()``：取当前队首账号的 source；队首变更时切换 profile
      并克隆（首次=传入 source 原样）。
    - ``advance(n)``：一批成功后记 n 个单位推进配额，耗尽则轮转。
    - ``switch_next()``：撞墙时移出当前队首并切下一个；全撞完返回 False
      （交既有 ``hard_stop``→暂停路径，FR-013）。
    """

    def __init__(self, source: Any, entries: list[PoolEntry], *, run_id: str = "",
                 on_account_switch: Callable[[str, str], None] | None = None,
                 switch_event_store: Any = None):
        if not entries:
            raise ValueError("DetailRobin 需至少一个预选账号")
        self._queue = RotationQueue(entries)
        self._run_id = str(run_id or getattr(source, "run_id", "") or "")
        self._platform = str(getattr(source, "platform", "boss") or "boss")
        self._cdp_port = getattr(source, "cdp_port", None)
        self._on_account_switch = on_account_switch
        self._whitebox = RoundRobinWhitebox(
            switch_event_store, self._run_id, phase="R2",
            platform=self._platform, entries=entries,
        )
        self._pending_switch_reason = None
        self._sources: dict[str, Any] = {}
        head = self._queue.head_account
        self._active_account = head
        if head is not None:
            self._sources[head] = source

    def source_for(self, account_id: str | None = None) -> Any:
        """返回指定账号的 source，并确保进程级 profile 已绑定该账号。"""
        account_id = str(account_id or self._queue.head_account or "")
        if not account_id:
            return None
        src = self._sources.get(account_id)
        if self._active_account != account_id:
            from_account = str(self._active_account or "")
            reason = str(self._pending_switch_reason or "quota")
            if not _switch_browser_account(account_id, self._platform, self._cdp_port):
                self._whitebox.switch(
                    from_account=from_account, to_account=account_id,
                    reason=reason, result="failed",
                )
                return None
            self._active_account = account_id
            self._whitebox.switch(
                from_account=from_account, to_account=account_id,
                reason=reason, result="succeeded",
            )
            self._pending_switch_reason = None
        if src is not None:
            return src
        # 取一个模板（任一已缓存 source）用于克隆同参数
        template = next(iter(self._sources.values()))
        src = clone_source(template, account_id, run_id=self._run_id)
        self._sources[account_id] = src
        return src

    def current_source(self) -> Any:
        """返回当前队首账号的 source；队空返回 None。"""
        return self.source_for()

    def reserve(self, n: int) -> tuple[PoolEntry | None, int]:
        """为一次详情请求预留当前账号配额。"""
        return self._queue.reserve(n)

    def allocate(self, pending_entries: list[Any]) -> DetailAllocation | None:
        """按当前账号配额从待抓队列领取一次实际请求。"""
        entry, take = self.reserve(len(pending_entries))
        if entry is None or take <= 0:
            return None
        self._whitebox.allocation(
            entry.account_id, round_no=self._queue.last_round, count=take,
            remaining=self._queue.last_remaining,
            pending_remaining=max(0, len(pending_entries) - take),
        )
        return DetailAllocation(
            account_id=entry.account_id,
            entries=list(pending_entries[:take]),
            tail=list(pending_entries[take:]),
        )

    def advance(self, n: int) -> None:
        """一批成功后推进配额；耗尽则轮转，跨账号累计扣完 n。"""
        remaining = int(n) if n and n > 0 else 0
        while remaining > 0 and self._queue.head_account is not None:
            _, taken = self._queue.reserve(remaining)
            remaining -= taken

    def mark_success(self, account_id: str | None = None) -> None:
        """成功使用后清除该账号的限流标记（自愈）。"""
        clear_account_rate_limited(
            str(account_id or self._queue.head_account or "")
        )

    def switch_next(self, account_id: str | None = None,
                    *, handoff_count: int = 0) -> bool:
        """撞墙换号：当前队首移出并切下一个。全撞完返回 False。"""
        failed_account = str(account_id or self._queue.head_account or "")
        if not failed_account:
            return False
        mark_account_rate_limited(failed_account)
        self._queue.block_account(failed_account)
        next_account = self._queue.head_account
        self._pending_switch_reason = "wall"
        if next_account and self._on_account_switch is not None:
            try:
                self._on_account_switch(failed_account, next_account)
            except Exception:
                _logger.debug("账号轮询切换留痕失败（不阻断主流程）", exc_info=True)
        self._whitebox.handoff(
            blocked_account=failed_account,
            to_account=str(next_account or ""),
            remaining=max(0, int(handoff_count or 0)),
            result="queued" if next_account else "paused", blocked_reason="wall",
            source_attempt_id=getattr(self, "_pending_attempt_id", None),
        )
        return next_account is not None

    def skip_account(self, account_id: str | None = None) -> bool:
        """移除无法绑定运行时 source 的账号，不将其误记为平台限流。"""
        wanted = str(account_id or self._queue.head_account or "")
        if not wanted:
            return False
        self._queue.block_account(wanted)
        return self._queue.head_account is not None

    def retry_after_binding_failure(
            self, allocation: DetailAllocation) -> list[Any] | None:
        """账号 profile 绑定失败后跳过该账号，返回交给下一个的待抓项。"""
        if not self.skip_account(allocation.account_id):
            return None
        return list(allocation.entries) + list(allocation.tail)

    @staticmethod
    def keep_unfinished(
            allocation: DetailAllocation, entries: list[Any],
    ) -> DetailAllocation:
        """将守护重试收窄到未完成项，保留尚未领取的账号配额尾部。"""
        return DetailAllocation(
            account_id=allocation.account_id,
            entries=list(entries),
            tail=list(allocation.tail),
        )

    def retry_after_wall(
            self, allocation: DetailAllocation, outcomes: dict,
            batch_exception_code: object, *, outcome_key: Callable[[Any], Any],
    ) -> tuple[bool, list[Any] | None]:
        """处理一段 R2 撞墙，返回 ``(是否接管, 下一账号待抓项)``。

        已成功或软失败的岗位不在下一段中；批调用自身撞墙且无条级结果时，
        当前分段全部接力。无可换账号时仍返回已接管、但待抓项为 ``None``，
        让调用方保留既有暂停结果。
        """
        if not is_wall_code(batch_exception_code):
            wall_entries = [
                item for item in allocation.entries
                if (outcome := outcomes.get(outcome_key(item))) is not None
                and is_wall_code(getattr(outcome, "failed_code", ""))
            ]
        else:
            wall_entries = list(allocation.entries)
        if not wall_entries:
            return False, None
        if not self.switch_next(allocation.account_id,
                                handoff_count=len(wall_entries) + len(allocation.tail)):
            return True, None
        return True, wall_entries + list(allocation.tail)

    @property
    def has_account(self) -> bool:
        return self._queue.head_account is not None

    @property
    def blocked_accounts(self) -> list[str]:
        return self._queue.blocked_accounts


# ---------------------------------------------------------------------------
# 工厂入口：接线层调用（engagement 规则见模块 docstring）
# ---------------------------------------------------------------------------

def _source_account(source: Any) -> str:
    return str(getattr(source, "browser_account", "") or "").strip()


def _engaged_entries(source: Any, role: str) -> list[PoolEntry]:
    """满足 engagement 规则时返回预选池，否则空列表（legacy 单源行为）。

    规则：池内 ≥2 个选中账号 且 当前 source 的 browser_account 属于选中池。
    ——保护既有测试替身（无 browser_account 或不在池）零行为变更。
    """
    src_account = _source_account(source)
    if not src_account:
        return []
    entries = load_pool_entries(role)
    if len(entries) < 2:
        return []
    if src_account not in [e.account_id for e in entries]:
        return []
    # 把 source 账号轮到队首（避免任务启动即无谓重启 Chrome）；
    # 轮转顺序仍按勾选顺序循环。
    idx = next((i for i, e in enumerate(entries) if e.account_id == src_account), 0)
    if idx:
        entries = entries[idx:] + entries[:idx]
    return entries


def make_list_robin(source: Any, *, run_id: str = "",
                    switch_event_store: Any = None) -> ListRobin | None:
    """R1 接线入口：返回 ListRobin 或 None（legacy 透传）。"""
    entries = _engaged_entries(source, "R1")
    if not entries:
        return None
    return ListRobin(source, entries, run_id=run_id,
                     switch_event_store=switch_event_store)


def make_detail_robin(source: Any, *, run_id: str = "",
                      on_account_switch: Callable[[str, str], None] | None = None,
                      switch_event_store: Any = None) -> DetailRobin | None:
    """R2 接线入口：返回 DetailRobin 或 None（legacy 透传）。"""
    entries = _engaged_entries(source, "R2")
    if not entries:
        return None
    return DetailRobin(
        source, entries, run_id=run_id,
        on_account_switch=on_account_switch,
        switch_event_store=switch_event_store,
    )
