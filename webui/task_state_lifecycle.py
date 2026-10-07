"""047 US5/C3：只读 closure 投影。

从现有原因、绑定、结果与 live 请求推导用户可感知的收尾事实：

- ``finish`` pending：明确 ``user_finished`` claim，结果尚未提交绑定 ——
  显示「正在结束保存」，不声称成功；
- ``finish`` committed：精确 run 正常收尾、Track 终结且结果已绑定；
- ``stop``：既有停止请求/取消事实，不暗示保存成功；
- 缺正面证据为 ``None``；``process_restart`` / ``operator_stop`` 不冒充
  ``user_finished``。

本模块只读，不新增表、不写库。
"""

from __future__ import annotations

_TERMINAL_TRACK_STATUSES = frozenset({"done", "succeeded", "failed", "stopped", "cancelled"})


def project_closure(run: dict | None, *, track: dict | None = None, live: dict | None = None) -> dict | None:
    """Return the closure projection or None when facts are not positive."""
    run = run if isinstance(run, dict) else {}
    track = track if isinstance(track, dict) else None
    run_status = str(run.get("status") or "")
    error_code = str(run.get("error_code") or "")
    interruption_kind = str(run.get("interruption_kind") or "")
    track_status = str((track or {}).get("status") or "")

    # finish committed：精确 run 以 user_finished 正常收尾，且 Track 终结、
    # 结果已绑定（或该轨本身没有结果概念时 Track 终结即可）。
    if (
        run_status == "interrupted"
        and error_code == "user_finished"
        and interruption_kind == "user_finished"
    ):
        if track is None:
            return {"kind": "finish", "phase": "committed"}
        result_bound = str((track or {}).get("result_run_id") or "").strip()
        if track_status == "stopped" and result_bound:
            return {"kind": "finish", "phase": "committed"}
        if track_status in _TERMINAL_TRACK_STATUSES and result_bound:
            return {"kind": "finish", "phase": "committed"}
        # claim 已建立但尚未绑定结果：收尾进行中。
        return {"kind": "finish", "phase": "pending"}

    # stop：既有取消事实（user_cancelled 是显式取消；Track stopped/cancelled
    # 且 run 已中断也归为停止收尾）。不暗示保存成功。
    if run_status == "interrupted" and interruption_kind == "user_cancelled":
        return {"kind": "stop", "phase": "committed"}
    if run_status == "interrupted" and error_code == "user_cancelled":
        return {"kind": "stop", "phase": "committed"}

    return None


def closure_for_run(ctx, run_id: str, run: dict | None, live: dict | None) -> dict | None:
    """Resolve one run's Track closure without inventing facts.

    047 C3：closure 是只读推导（原因/绑定/结果/live），写权仍在各自存储
    边界。绑定解析失败不阻断状态读取。
    """
    from webui.flow_task_coordinator import resolve_flow_binding

    track = None
    try:
        binding = resolve_flow_binding(ctx, run_id)
    except Exception:  # noqa: BLE001 - identity errors must not break status reads
        binding = None
    if isinstance(binding, dict):
        track_row = binding.get("track_row")
        if track_row is not None:
            track = dict(track_row)
    return project_closure(run, track=track, live=live)


def authoritative_run(store, run: dict | None, run_id: str) -> dict | None:
    """Return the lifecycle-owned execution row for user-visible reads.

    047 C1：partial 表示结束但不完整。旧全局投影保持 033 v2 的剥离语义，
    由 047 生命周期域（``store_run_lifecycle.get_screening_run_outcome``）
    读回权威执行原因，保证 DB、live task 与界面看到同一条具体失败事实。
    """
    if run is None or str(run.get("status") or "") != "partial":
        return run
    reader = getattr(store, "get_screening_run_outcome", None)
    if not callable(reader):
        return run
    lifecycle_run = reader(str(run.get("id") or run_id))
    return lifecycle_run if lifecycle_run is not None else run


__all__ = ["project_closure", "closure_for_run", "authoritative_run"]
