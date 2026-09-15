"""043：流程清理服务（整条进出 / 无主兜底 / 定稿中间档 / 启动兜底）。

- 删除与淘汰一律按"流程闭包"整条执行（轮 + 根账本 + 派生记录 + 白箱 + 日志）；
- FR-022：流程定稿后只保留最新一次筛选/重抓中间档；
- FR-011：无主账本按每平台最近 N 条兜底；
- 启动兜底：存量回收（孤儿明细 + 无主超限）+ 自动备份 + 清单落盘（幂等、best-effort）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_RETENTION = 30
_OPERATIONAL_ERRORS = (sqlite3.Error, RuntimeError, OSError, ValueError)


def delete_run(store, run_id: str, profile_id: str | None = None) -> bool:
    """整条删除一条流程（用户删除入口；FR-020 活跃保护在 store 层拒绝）。"""
    target = str(run_id or "")
    if not target:
        return False
    return bool(store.delete_run_closure(target, profile_id=profile_id))


def prune_stale_intermediate_runs(store, run_id: str) -> list[str]:
    """043 FR-022：定稿流程更早的中间过程档清除（只保留最新一次）。

    仅当该流程已有定稿轮（succeeded/partial）时执行；存在未结束任务时
    store 层删除会拒绝（FR-020），此处不额外判断。
    """
    target = str(run_id or "")
    if not target:
        return []
    run = store.get_screening_run(target) or {}
    if str(run.get("status") or "") not in ("done", "partial", "succeeded"):
        return []
    params = run.get("execution_params") or {}
    root_task_id = str(params.get("scrape_task_id") or "")
    if not root_task_id:
        return []
    derived = store.list_derived_runs_for_source(root_task_id)
    if len(derived) <= 1:
        return []
    removed: list[str] = []
    for item in derived[1:]:
        if str(item.get("status") or "") in ("queued", "running", "paused"):
            continue
        # 局部删除：只清该中间档及其向下派生，不动根账本与结果轮。
        if store.delete_run_subtree(str(item.get("id") or "")):
            removed.append(str(item.get("id") or ""))
    return removed


def prune_after_finalize(store, run_id: str = "") -> dict[str, Any]:
    """落轮/结束保存后的清理编排（043 T011 触发点调用）。

    best-effort：失败只记录在返回值里，绝不抛给任务主流程（FR-014）。
    """
    result: dict[str, Any] = {"intermediate_removed": [], "unowned_removed": [], "error": ""}
    try:
        result["intermediate_removed"] = prune_stale_intermediate_runs(store, run_id)
    except _OPERATIONAL_ERRORS as exc:
        result["error"] = f"intermediate:{exc}"
    try:
        result["unowned_removed"] = store.prune_unowned_run_closures(DEFAULT_RETENTION)
    except _OPERATIONAL_ERRORS as exc:
        result["error"] = (result["error"] + f" unowned:{exc}").strip()
    return result


def _backup_database(store) -> Path | None:
    """一致性快照备份到数据目录 backups/（SQLite backup API；非文件复制）。"""
    db_path = Path(str(getattr(store, "db_path", "") or ""))
    if not db_path.is_file():
        return None
    dest_dir = db_path.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"webui-cleanup-{stamp}.sqlite"
    source = sqlite3.connect(str(db_path), timeout=10)
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    digest = hashlib.sha256()
    with open(dest, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    manifest = {
        "backup_file": dest.name,
        "size_bytes": dest.stat().st_size,
        "sha256": digest.hexdigest(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tool": "career-scout-run-cleanup-v1",
    }
    (dest_dir / f"webui-cleanup-{stamp}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return dest


def _write_report(store, report: dict[str, Any]) -> Path | None:
    """回收清单落盘（可供核对；不替代人工审批，自动执行）。"""
    db_path = Path(str(getattr(store, "db_path", "") or ""))
    if not db_path:
        return None
    dest_dir = db_path.parent / "reports"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"run-cleanup-{stamp}.json"
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def run_startup_cleanup(store, *, limit: int = DEFAULT_RETENTION) -> dict[str, Any]:
    """043：启动兜底（幂等）。有候选才先备份再执行；清单落盘可供核对。

    - 存量：父行已不存在的孤儿明细（已删轮/任务残留）+ 无主账本超限；
    - 执行前 MUST 先做一致性备份（FR-015）；无候选时不做备份、不落清单。
    """
    orphans = store.count_orphan_rows()
    orphan_total = sum(int(v or 0) for v in orphans.values())
    unowned_candidates = store.prune_unowned_run_closures(limit, dry_run=True)
    if orphan_total <= 0 and not unowned_candidates:
        return {"cleaned": False, "reason": "nothing_to_clean"}
    backup = _backup_database(store)
    removed = store.delete_orphan_rows()
    unowned_removed = store.prune_unowned_run_closures(limit)
    report: dict[str, Any] = {
        "cleaned": True,
        "backup_file": backup.name if backup else "",
        "orphans_before": orphans,
        "orphans_removed": removed,
        "unowned_removed": unowned_removed,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    report_file = _write_report(store, report)
    if report_file is not None:
        report["report_file"] = report_file.name
    return report
