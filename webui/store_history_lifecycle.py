"""047 US6：历史删除资格与旧 queue 局部处理。

同一个 ``BEGIN IMMEDIATE`` 连接内完成「读取资格 → 局部修正已证明旧 queue →
复查 → 删除」。GET 只读投影 ``analyze_history_deletion``，不写任何东西。

可证明的旧 queue 残留必须同时满足：
- 目标归属精确（画像/Track/平台一致）；
- Track 终结（done/succeeded/failed/stopped/cancelled，或带确定中断证据的
  interrupted）；
- 同 id 抓取 execution 已终结（succeeded/partial/failed/interrupted），
  且同 id search 账本只是 queued 残留；
- 该闭包（含 retry 旧尝试）没有任何 queued/running/paused task 或子 run、
  没有其它 Track 共享引用、没有 finish pending（interrupted+user_finished）。
缺任何证据就拒绝；真正 active 一律保护。
"""

from __future__ import annotations

import json

from webui.store_flow_core import _TERMINAL_TRACK_STATUSES

_ACTIVE_RUN_STATUSES = frozenset(("queued", "running"))
_TERMINAL_RUN_STATUSES = frozenset(
    (
        "succeeded", "partial", "failed", "interrupted",
        # 结果快照的既有终态词表（save_pipeline_result 系列）
        "done", "scraped_only", "completed", "completed_with_pending",
    )
)
_KNOWN_INTERRUPTED_KINDS = frozenset(
    ("user_finished", "user_cancelled", "user_stopped", "operator_stop")
)
_KNOWN_INTERRUPTED_CODES = frozenset(
    ("user_finished", "user_cancelled", "user_stopped", "operator_stop", "process_restart", "restart")
)
_ACTIVE_TASK_STATUSES = frozenset(("queued", "running", "paused", "pending"))
_WORKER_TASK_KINDS = frozenset(("scrape", "ai_screen", "recrawl"))


def _decode(raw):
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class HistoryDeletionEligibilityMixin:
    """Read-only eligibility projection + proven stale-queue repair."""

    # -- public read-only projection ---------------------------------------

    def analyze_history_deletion(self, target, *, profile_id=None) -> dict:
        """Return ``{can_delete, delete_block_reason}`` without writing."""
        target = str(target or "").strip()
        if not target:
            return self._history_eligibility(False, "找不到要删除的历史轮次")
        with self._connection() as conn:
            return self._history_eligibility_at(conn, target, profile_id=profile_id)

    def _history_eligibility_at(self, conn, target, *, profile_id=None) -> dict:
        track = conn.execute(
            "SELECT ft.*, f.profile_id AS flow_profile_id FROM flow_tracks ft "
            "JOIN flows f ON f.id = ft.flow_id "
            "WHERE ft.id = ? OR ft.result_run_id = ? OR ft.scrape_run_id = ? "
            "OR ft.screen_run_id = ?",
            (target, target, target, target),
        ).fetchone()
        row = conn.execute(
            "SELECT * FROM screening_runs WHERE id = ?", (target,)
        ).fetchone()
        search_row = conn.execute(
            "SELECT * FROM search_runs WHERE id = ?", (target,)
        ).fetchone()
        if track is None and row is None and search_row is None:
            return self._history_eligibility(False, "历史轮次不存在或已被删除")
        owner = ""
        if track is not None:
            owner = str(track["flow_profile_id"] or "")
        elif row is not None:
            owner = str(row["profile_id"] or "")
        elif search_row is not None:
            owner = str(search_row["profile_id"] or "")
        if profile_id and owner and owner != str(profile_id):
            return self._history_eligibility(False, "该历史轮次不属于当前画像")
        if track is None:
            # 兼容既有范围：未被任何 Track 引用的独立结果快照仍可按 result
            # id 删除（老轮次没有持久化 Flow/Track 归属）；非快照的空轨 id
            # 没有轨道锚点，按既有能力拒绝。
            if row is not None and str(row["record_kind"] or "") == "result_snapshot":
                status = str(row["status"] or "")
                if status in {"queued", "running", "paused"}:
                    return self._history_eligibility(False, "任务仍在进行中，暂时不能删除")
                if status not in _TERMINAL_RUN_STATUSES:
                    return self._history_eligibility(False, "该任务尚未结束，暂时不能删除")
                return self._history_eligibility(True, None)
            return self._history_eligibility(False, "找不到对应的平台运行线，无法删除")
        track_status = str(track["status"] or "")
        if track_status == "interrupted":
            if not self._interrupted_track_evidence_ok(conn, track):
                return self._history_eligibility(False, "任务中断原因不明，暂时不能删除")
        elif track_status not in _TERMINAL_TRACK_STATUSES:
            return self._history_eligibility(False, "请先结束或取消流程，再删除历史轮次")

        closure = self._history_closure_at(conn, track)
        run_ids = {
            str(track[key])
            for key in ("scrape_run_id", "screen_run_id", "result_run_id")
            if track[key]
        }
        run_ids.update(closure)
        if self._finish_claim_pending(conn, track, run_ids):
            return self._history_eligibility(False, "正在结束保存，完成后再删除")
        shared = self._shared_reference(conn, track, run_ids)
        if shared:
            return self._history_eligibility(
                False, "该历史轮次仍被其它平台运行线引用，无法删除",
            )
        for run_id in sorted(run_ids):
            screening = conn.execute(
                "SELECT * FROM screening_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if screening is not None:
                status = str(screening["status"] or "")
                if status in _ACTIVE_RUN_STATUSES:
                    return self._history_eligibility(False, "任务仍在进行中，暂时不能删除")
                if status == "interrupted" and not self._interrupted_run_evidence_ok(screening):
                    return self._history_eligibility(
                        False, "任务中断原因不明，暂时不能删除",
                    )
                if status not in _TERMINAL_RUN_STATUSES:
                    return self._history_eligibility(False, "该任务尚未结束，暂时不能删除")
            search = conn.execute(
                "SELECT * FROM search_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if search is not None:
                status = str(search["status"] or "")
                if status in _ACTIVE_RUN_STATUSES and not self._stale_queue_proven(
                    conn, track, run_id,
                ):
                    return self._history_eligibility(
                        False, "抓取记录仍在排队或运行，暂时不能删除",
                    )
            for task_row in conn.execute(
                "SELECT kind, status FROM tasks WHERE id = ?", (run_id,)
            ).fetchall():
                kind = str(task_row["kind"] or "")
                status = str(task_row["status"] or "")
                if kind in _WORKER_TASK_KINDS and status in _ACTIVE_TASK_STATUSES:
                    return self._history_eligibility(
                        False, "任务仍在进行中，暂时不能删除",
                    )
        # 047 复核 P1：待确认岗位是数据内容，不是活动任务。已结束的
        # failed/stopped/done 单轨不因存在待确认岗位被拒删；真正的
        # queued/running/paused 任务、重试记录与 finish 保存事务已由上面
        # 的 active/dispatch/finish-claim 检查保护，不受本改动影响。
        return self._history_eligibility(True, None)

    @staticmethod
    def _history_eligibility(can_delete: bool, reason) -> dict:
        if can_delete:
            return {"can_delete": True, "delete_block_reason": None}
        return {
            "can_delete": False,
            "delete_block_reason": str(reason or "当前历史轮次暂时不能删除"),
        }

    # -- DELETE transaction: repair proven stale queue, then re-analyze ---

    def repair_and_analyze_history_deletion(self, conn, target, *, profile_id=None) -> dict:
        """DELETE 事务内：局部修正已证明的旧 queue 后复查。

        只把「确定终结的同 id execution + Track 终结 + 精确归属」的 search
        账本从 queued 修正为 failed，绝不猜其它终态；修正前后在同一事务内，
        异常整体回滚。
        """
        eligibility = self._history_eligibility_at(
            conn, target, profile_id=profile_id,
        )
        if eligibility["can_delete"]:
            return eligibility
        track = conn.execute(
            "SELECT ft.* FROM flow_tracks ft "
            "WHERE ft.id = ? OR ft.result_run_id = ? OR ft.scrape_run_id = ? "
            "OR ft.screen_run_id = ?",
            (target, target, target, target),
        ).fetchone()
        if track is None:
            return eligibility
        track_status = str(track["status"] or "")
        if track_status != "interrupted" and track_status not in _TERMINAL_TRACK_STATUSES:
            return eligibility
        repaired = False
        for run_id in sorted(self._repair_candidates_at(conn, track, target)):
            search = conn.execute(
                "SELECT status FROM search_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if search is None or str(search["status"] or "") != "queued":
                continue
            if not self._stale_queue_proven(conn, track, run_id):
                continue
            conn.execute(
                "UPDATE search_runs SET status = 'failed', "
                "error_code = 'stale_queue_repaired' "
                "WHERE id = ? AND status = 'queued'",
                (run_id,),
            )
            repaired = True
        if not repaired:
            return eligibility
        return self._history_eligibility_at(conn, target, profile_id=profile_id)

    def _repair_candidates_at(self, conn, track, target) -> set[str]:
        """删除闭包候选：同轨 run + 该 Flow/平台名下已存在的 run。

        与 delete_run_closure 的 seeding 口径一致（结果快照只带 flow_id+
        scrape_task_id，没有 track_id），但资格只对确定终结的同 id execution
        成立时才修正队列。
        """
        candidates = self._history_closure_at(conn, track)
        candidates.add(str(target))
        for item in [*conn.execute("SELECT * FROM screening_runs").fetchall(),
                     *conn.execute("SELECT * FROM search_runs").fetchall()]:
            raw = (
                item["execution_params_json"]
                if "execution_params_json" in item.keys()
                else item["profile_snapshot_json"]
            )
            params = _decode(raw)
            platform = str(item["platform"] if "platform" in item.keys() else "").strip().lower()
            if (
                str(params.get("flow_id") or "") == str(track["flow_id"] or "")
                and (
                    str(params.get("track_id") or "") == str(track["id"] or "")
                    or platform == str(track["platform"] or "").strip().lower()
                )
            ):
                candidates.add(str(item["id"]))
        return candidates

    # -- evidence helpers --------------------------------------------------

    @staticmethod
    def _finish_claim_pending(conn, track, run_ids) -> bool:
        """finish pending = user_finished claim 尚未绑定结果。"""
        if str(track["result_run_id"] or ""):
            return False
        for run_id in sorted(run_ids):
            row = conn.execute(
                "SELECT status, error_code FROM screening_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                continue
            if (
                str(row["status"] or "") == "interrupted"
                and str(row["error_code"] or "") == "user_finished"
            ):
                return True
        return False

    @staticmethod
    def _interrupted_run_evidence_ok(screening) -> bool:
        code = str(screening["error_code"] or "")
        kind = ""
        if "interruption_kind" in screening.keys():
            kind = str(screening["interruption_kind"] or "")
        return code in _KNOWN_INTERRUPTED_CODES or kind in _KNOWN_INTERRUPTED_KINDS

    def _interrupted_track_evidence_ok(self, conn, track) -> bool:
        """带确定中断 evidence 的 Track（例如用户主动终止）不再要求 active。"""
        for key in ("screen_run_id", "scrape_run_id"):
            run_id = str(track[key] or "")
            if not run_id:
                continue
            row = conn.execute(
                "SELECT * FROM screening_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is not None and self._interrupted_run_evidence_ok(row):
                return True
            search = conn.execute(
                "SELECT error_code FROM search_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if search is not None and str(search["error_code"] or "") in _KNOWN_INTERRUPTED_CODES:
                return True
        return False

    @staticmethod
    def _shared_reference(conn, track, run_ids) -> bool:
        if not run_ids:
            return False
        for other in conn.execute("SELECT * FROM flow_tracks").fetchall():
            if str(other["id"]) == str(track["id"]):
                continue
            for key in ("scrape_run_id", "screen_run_id", "result_run_id"):
                value = str(other[key] or "")
                if value and value in run_ids:
                    return True
        return False

    @staticmethod
    def _stale_queue_proven(conn, track, run_id) -> bool:
        """同 id screening execution 有确定终结事实 + Track 归属精确。"""
        screening = conn.execute(
            "SELECT status, error_code, interruption_kind, execution_params_json "
            "FROM screening_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if screening is None:
            return False
        if str(screening["status"] or "") not in _TERMINAL_RUN_STATUSES:
            return False
        if str(screening["status"] or "") == "interrupted":
            code = str(screening["error_code"] or "")
            kind = str(
                screening["interruption_kind"]
                if "interruption_kind" in screening.keys() else ""
            )
            if code not in _KNOWN_INTERRUPTED_CODES and kind not in _KNOWN_INTERRUPTED_KINDS:
                return False
        params = _decode(screening["execution_params_json"])
        if str(params.get("flow_id") or "") != str(track["flow_id"] or ""):
            return False
        if str(params.get("track_id") or "") != str(track["id"] or ""):
            return False
        return True

    def _history_closure_at(self, conn, track) -> set[str]:
        """同轨闭包（含 retry 旧尝试），不越入兄弟或别的 Flow。"""
        collected = {
            str(track[key])
            for key in ("scrape_run_id", "screen_run_id", "result_run_id")
            if track[key]
        }
        rows = conn.execute("SELECT * FROM screening_runs").fetchall()
        search_rows = conn.execute("SELECT * FROM search_runs").fetchall()
        for item in [*rows, *search_rows]:
            raw = (
                item["execution_params_json"]
                if "execution_params_json" in item.keys()
                else item["profile_snapshot_json"]
            )
            params = _decode(raw)
            if (
                str(params.get("track_id") or "") == str(track["id"] or "")
                and str(params.get("flow_id") or "") == str(track["flow_id"] or "")
            ):
                collected.add(str(item["id"]))
        return collected


__all__ = ["HistoryDeletionEligibilityMixin"]