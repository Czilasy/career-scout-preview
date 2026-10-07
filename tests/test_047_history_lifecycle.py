"""047 US6：已结束历史删除资格的红测（临时库，不碰正式历史）。

契约 C6/data-model：
- 可证明的旧 queued search 残留 → 可删（Track 终结 + 同 id screening 终结 +
  精确归属 + 无 actual active task/子任务 + 无 retry/finish pending）；
- 真正 queued/running/paused、证据不明的 interrupted、pending、共享 run、
  跨画像 → 保护；
- 删除单轨不伤兄弟；最后一轨删除才清外壳；
- GET 只读投影 can_delete/delete_block_reason，DELETE 必须重判。
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from webui.store import TaskStore
from webui.run_cleanup import HistoryDeletionBlocked


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s047-history-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile = "p1"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'p1', '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (self.profile,),
            )

    def tearDown(self):
        self.temp.cleanup()

    def make_flow(self, *, selection="all"):
        return self.store.create_flow(
            profile_id=self.profile, selection=selection, start_key=f"h-{selection}",
            confirmed_filters={"boss": {}, "zhilian": {}} if selection == "all" else {selection: {}},
        )

    def seed_finished_track(self, flow, platform, *, with_result=False, status="failed"):
        track = next(t for t in flow["tracks"] if t["platform"] == platform)
        scrape_id = f"{platform}-scrape"
        screen_id = f"{platform}-screen"
        self.store.create_screening_run(
            scrape_id, profile_id=self.profile,
            execution_params={"flow_id": flow["id"], "track_id": track["id"], "platform": platform},
        )
        self.store.create_screening_run(
            screen_id, profile_id=self.profile,
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"], "platform": platform,
                "scrape_task_id": scrape_id,
            },
        )
        self.store.update_screening_run(
            scrape_id, status="running", current_stage="scrape",
        )
        self.store.update_screening_run(
            scrape_id, status="failed", current_stage="scrape",
            error_code="scrape_failed", error_reason="平台抓取失败",
        )
        self.store.create_scrape_search_run(
            scrape_id, self.profile, platform=platform, flow_id=flow["id"], track_id=track["id"],
        )
        self.store.bind_flow_track_runs(
            flow["id"], platform, profile_id=self.profile, scrape_run_id=scrape_id,
        )
        self.store.update_screening_run(screen_id, status="running", current_stage="ai_rough")
        self.store.update_flow_track(
            flow["id"], platform, profile_id=self.profile, status="running",
            stage="ai", screen_run_id=screen_id,
        )
        result_run_id = None
        if with_result:
            result_run_id = self.store.save_pipeline_result(
                {"platform": platform, "jobs": [{"platform_job_id": f"{platform}-1", "verdict": "match"}], "dropped": []},
                {"platform": platform},
                profile_id=self.profile,
                execution_params={"platform": platform, "flow_id": flow["id"], "scrape_task_id": scrape_id},
            )
            self.store.bind_flow_track_runs(
                flow["id"], platform, profile_id=self.profile, result_run_id=result_run_id,
            )
        self.store.update_screening_run(
            screen_id, status="failed" if status == "failed" else "interrupted",
            error_code="flow_ai_start_failed" if status == "failed" else "user_cancelled",
        )
        self.store.update_flow_track(
            flow["id"], platform, profile_id=self.profile, status=status, stage="ai",
            error_code="flow_ai_start_failed" if status == "failed" else None,
        )
        # 制造「确定的旧 queue 残留」：search 账本仍停在 queued。
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE search_runs SET status = 'queued', error_code = NULL WHERE id = ?",
                (scrape_id,),
            )
        return track, scrape_id, screen_id, result_run_id


class HistoryEligibilityTests(_Fixture):
    def test_finished_track_with_stale_queued_search_is_deletable(self):
        flow = self.make_flow()
        track, scrape_id, screen_id, result_id = self.seed_finished_track(
            flow, "boss", with_result=True,
        )
        eligibility = self.store.analyze_history_deletion(
            result_id, profile_id=self.profile,
        )
        self.assertTrue(eligibility["can_delete"], eligibility)
        self.assertIsNone(eligibility["delete_block_reason"])

    def test_zero_job_failed_track_is_deletable_by_track_id(self):
        flow = self.make_flow()
        track, _scrape_id, _screen_id, _result = self.seed_finished_track(flow, "boss")
        eligibility = self.store.analyze_history_deletion(
            track["id"], profile_id=self.profile,
        )
        self.assertTrue(eligibility["can_delete"], eligibility)

    def test_truly_running_cousin_is_protected(self):
        flow = self.make_flow()
        track, scrape_id, screen_id, result_id = self.seed_finished_track(
            flow, "boss", with_result=True,
        )
        sibling = next(t for t in flow["tracks"] if t["platform"] == "zhilian")
        sibling_run = "zhilian-active"
        self.store.create_screening_run(
            sibling_run, profile_id=self.profile,
            execution_params={"flow_id": flow["id"], "track_id": sibling["id"], "platform": "zhilian"},
        )
        self.store.bind_flow_track_runs(
            flow["id"], "zhilian", profile_id=self.profile, screen_run_id=sibling_run,
        )
        self.store.update_screening_run(sibling_run, status="running", current_stage="ai_rough")
        self.store.update_flow_track(
            flow["id"], "zhilian", profile_id=self.profile, status="running", stage="ai",
        )
        eligibility = self.store.analyze_history_deletion(result_id, profile_id=self.profile)
        # 兄弟活动不妨碍删已结束的这条轨
        self.assertTrue(eligibility["can_delete"], eligibility)
        with self.assertRaises(HistoryDeletionBlocked):
            self.store.delete_run_closure(sibling_run, profile_id=self.profile)

    def test_paused_track_is_protected(self):
        flow = self.make_flow()
        track, _scrape, screen_id, result_id = self.seed_finished_track(
            flow, "boss", with_result=True,
        )
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE flow_tracks SET status = 'paused', stage = 'ai' WHERE id = ?",
                (track["id"],),
            )
        eligibility = self.store.analyze_history_deletion(result_id, profile_id=self.profile)
        self.assertFalse(eligibility["can_delete"])
        self.assertTrue(eligibility["delete_block_reason"])
        with self.assertRaises(HistoryDeletionBlocked):
            self.store.delete_run_closure(result_id, profile_id=self.profile)

    def test_unexplained_interrupted_track_is_protected(self):
        flow = self.make_flow()
        track, _scrape, screen_id, result_id = self.seed_finished_track(
            flow, "boss", with_result=True,
        )
        # Track 已终结（failed），但同轨 AI run 的中断原因不明 → 证据不足，拒绝。
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET status = 'interrupted', error_code = ? WHERE id = ?",
                ("browser_unavailable", screen_id),
            )
        eligibility = self.store.analyze_history_deletion(result_id, profile_id=self.profile)
        self.assertFalse(eligibility["can_delete"], eligibility)
        self.assertTrue(eligibility["delete_block_reason"])

    def test_cross_profile_target_is_refused(self):
        flow = self.make_flow()
        track, _scrape, _screen, result_id = self.seed_finished_track(
            flow, "boss", with_result=True,
        )
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES ('p2', 'p2', '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
            )
        eligibility = self.store.analyze_history_deletion(result_id, profile_id="p2")
        self.assertFalse(eligibility["can_delete"], eligibility)

    def test_shared_run_ownership_is_protected(self):
        first = self.make_flow(selection="all")
        track, scrape_id, screen_id, result_id = self.seed_finished_track(
            first, "boss", with_result=True,
        )
        second = self.make_flow(selection="all")
        # 另一个 Flow 的 Track 也引用同一条 run → 共享归属，必须拒绝（防御性）。
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE flow_tracks SET screen_run_id = ? WHERE flow_id = ? AND platform = 'zhilian'",
                (screen_id, second["id"]),
            )
        eligibility = self.store.analyze_history_deletion(result_id, profile_id=self.profile)
        self.assertFalse(eligibility["can_delete"], eligibility)

    def test_delete_removes_stale_queue_only_for_proven_closure(self):
        flow = self.make_flow()
        track, scrape_id, _screen, result_id = self.seed_finished_track(
            flow, "boss", with_result=True,
        )
        sibling = next(t for t in flow["tracks"] if t["platform"] == "zhilian")
        deleted = self.store.delete_run_closure(result_id, profile_id=self.profile)
        self.assertTrue(deleted)
        with self.store._connection() as conn:
            gone = conn.execute(
                "SELECT 1 FROM screening_runs WHERE id = ?", (result_id,)
            ).fetchone()
            scrape_gone = conn.execute(
                "SELECT 1 FROM search_runs WHERE id = ?", (scrape_id,)
            ).fetchone()
            remaining = conn.execute(
                "SELECT COUNT(*) AS n FROM flow_tracks WHERE flow_id = ?", (flow["id"],)
            ).fetchone()["n"]
        self.assertIsNone(gone)
        self.assertIsNone(scrape_gone)
        self.assertEqual(remaining, 1)
        with self.store._connection() as conn:
            sibling_row = conn.execute(
                "SELECT status FROM flow_tracks WHERE id = ?", (sibling["id"],)
            ).fetchone()
        self.assertEqual(sibling_row["status"], "queued")




class PendingJobHistoryDeletionTests(_Fixture):
    """复核 P1-6：已停止/失败且有待确认岗位的历史仍可删，兄弟结果保留。

    ``screening_pending_results`` 是岗位数据内容，不是活体任务；已确定
    结束的 Track 不因待确认岗位被拒删。用户岗位资产按现有范围处理。
    """

    def _seed_stopped_with_pending_and_sibling(self):
        flow = self.make_flow()
        track, scrape_id, screen_id, result_id = self.seed_finished_track(
            flow, "boss", with_result=True, status="failed",
        )
        self.store.insert_pending_result(
            scrape_id, "pending-job-1",
            failure_stage="ai_timeout", failed_code="source_status_unclear",
            platform="boss",
        )
        self.store.insert_pending_result(
            screen_id, "pending-job-2",
            failure_stage="ai_timeout", failed_code="source_status_unclear",
            platform="boss",
        )
        return flow, track, scrape_id, screen_id, result_id

    def test_stopped_history_with_pending_jobs_is_deletable(self):
        flow, track, scrape_id, screen_id, result_id = (
            self._seed_stopped_with_pending_and_sibling()
        )
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE flow_tracks SET status = 'stopped', stage = 'scrape' WHERE id = ?",
                (track["id"],),
            )
        eligibility = self.store.analyze_history_deletion(
            result_id, profile_id=self.profile,
        )
        self.assertTrue(
            eligibility["can_delete"],
            f"已停止历史不因待确认岗位被拒删: {eligibility}",
        )
        self.assertIsNone(eligibility["delete_block_reason"])

    def test_failed_history_with_pending_jobs_is_deletable_and_keeps_sibling(self):
        flow, track, scrape_id, screen_id, result_id = (
            self._seed_stopped_with_pending_and_sibling()
        )
        sibling = next(t for t in flow["tracks"] if t["platform"] == "zhilian")
        self.assertTrue(self.store.delete_run_closure(
            result_id, profile_id=self.profile,
        ))
        with self.store._connection() as conn:
            gone = conn.execute(
                "SELECT 1 FROM screening_runs WHERE id = ?", (result_id,),
            ).fetchone()
            pending_rows = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results "
                "WHERE run_id IN (?, ?)", (scrape_id, screen_id),
            ).fetchone()["n"]
            remaining = conn.execute(
                "SELECT status FROM flow_tracks WHERE id = ?", (sibling["id"],),
            ).fetchone()
        self.assertIsNone(gone)
        self.assertEqual(pending_rows, 0, "删除闭包应带走该轨待确认数据")
        self.assertIsNotNone(remaining, "兄弟轨道必须保留")
        self.assertEqual(remaining["status"], "queued")

    def test_truly_active_task_still_protected_with_pending_jobs(self):
        flow, track, scrape_id, screen_id, result_id = (
            self._seed_stopped_with_pending_and_sibling()
        )
        # 真正 active 的 worker 任务仍必须保护，不因本改动放开。
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE flow_tracks SET status = 'running', stage = 'scrape' WHERE id = ?",
                (track["id"],),
            )
        eligibility = self.store.analyze_history_deletion(
            scrape_id, profile_id=self.profile,
        )
        self.assertFalse(eligibility["can_delete"])
        self.assertTrue(eligibility["delete_block_reason"])



class CrossStoryIntegrationTests(_Fixture):
    """T054：跨故事闭环——立即结束保存 → 旧回调 → 历史删除；retry 新 run → 旧回调不覆盖。"""

    def _seed_running_track_with_result(self, flow, platform="boss"):
        track = next(t for t in flow["tracks"] if t["platform"] == platform)
        scrape_id = f"{platform}-cross-scrape"
        self.store.create_screening_run(
            scrape_id, profile_id=self.profile,
            execution_params={"flow_id": flow["id"], "track_id": track["id"], "platform": platform},
        )
        self.store.create_scrape_search_run(
            scrape_id, self.profile, platform=platform,
            flow_id=flow["id"], track_id=track["id"],
        )
        self.store.bind_flow_track_runs(
            flow["id"], platform, profile_id=self.profile, scrape_run_id=scrape_id,
        )
        self.store.update_screening_run(scrape_id, status="running", current_stage="scrape")
        self.store.update_flow_track(
            flow["id"], platform, profile_id=self.profile,
            status="running", stage="scrape",
        )
        return track, scrape_id

    def _insert_result_snapshot(self, flow, track, run_id):
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO screening_runs "
                "(id, status, frozen_filters_json, source_count, match_count, mismatch_count, "
                "created_at, updated_at, record_kind, profile_id, execution_params_json) "
                "VALUES (?, 'succeeded', '{}', 0, 0, 0, "
                "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'result_snapshot', ?, ?)",
                (
                    run_id, self.profile,
                    '{"flow_id": "%s", "track_id": "%s", "platform": "%s"}'
                    % (flow["id"], track["id"], track["platform"]),
                ),
            )
        self.store.bind_flow_track_runs(
            flow["id"], track["platform"], profile_id=self.profile, result_run_id=run_id,
        )
        return run_id

    def test_immediate_finish_late_callback_then_history_delete(self):
        flow = self.make_flow()
        track, scrape_id = self._seed_running_track_with_result(flow)
        # 立即结束保存：claim 之后结果还没绑定。
        claim = self.store.claim_flow_finish(
            flow["id"], "boss", profile_id=self.profile, task_run_id=scrape_id,
        )
        self.assertTrue(claim.get("claimed", True))
        # 旧 worker 迟到失败：不得改写正在收尾的 Track。
        blocked = self.store.close_flow_task_state_atomic(
            flow["id"], "boss", self.profile,
            task_run_id=scrape_id, scrape_run_id=scrape_id,
            status="failed", error_code="scrape_failed",
            error_reason="late worker failure after finish",
        )
        boss = next(t for t in blocked["tracks"] if t["platform"] == "boss")
        self.assertEqual(boss["status"], "running")

        # 收尾提交绑定结果后，这一轮是「已结束且带结果」的历史。
        result_id = self._insert_result_snapshot(flow, track, "cross-result")
        finished = self.store.finish_flow_task_atomic(
            flow["id"], "boss", profile_id=self.profile,
            task_run_id=scrape_id, result_run_id=result_id,
        )
        boss = next(t for t in finished["tracks"] if t["platform"] == "boss")
        self.assertEqual(boss["result_run_id"], result_id)

        # 历史删除资格与删除都必须通过：迟到的旧回调不影响已结束事实。
        eligibility = self.store.analyze_history_deletion(
            result_id, profile_id=self.profile,
        )
        self.assertTrue(eligibility["can_delete"], eligibility)
        self.assertIsNone(eligibility["delete_block_reason"])
        self.assertTrue(self.store.delete_run_closure(result_id, profile_id=self.profile))
        with self.store._connection() as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) AS n FROM flow_tracks WHERE flow_id = ?", (flow["id"],)
            ).fetchone()["n"]
        self.assertEqual(remaining, 1, "只删目标轨，兄弟保留")

    def test_retry_new_attempt_then_old_run_callback_cannot_overwrite_result(self):
        flow = self.make_flow()
        track, old_run = self._seed_running_track_with_result(flow)
        self.store.update_screening_run(old_run, status="failed", error_code="scrape_failed")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile,
            status="failed", stage="scrape", error_code="scrape_failed",
        )
        current = next(t for t in self.store.get_flow(flow["id"], profile_id=self.profile)["tracks"]
                       if t["id"] == track["id"])
        claim = self.store.claim_flow_track_retry(
            flow["id"], "boss", profile_id=self.profile,
            expected_run_id=old_run, expected_updated_at=current["updated_at"],
        )
        new_run = claim["new_run_id"]
        # 新尝试发布结果；随后旧 run 的迟到回调不得覆盖绑定或结果。
        result_id = self._insert_result_snapshot(flow, track, "cross-retry-result")
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile, result_run_id=result_id,
        )
        late = self.store.close_flow_task_state_atomic(
            flow["id"], "boss", self.profile,
            task_run_id=old_run, scrape_run_id=old_run,
            status="failed", error_code="scrape_failed",
            error_reason="late callback from first attempt",
        )
        boss = next(t for t in late["tracks"] if t["platform"] == "boss")
        self.assertEqual(boss["scrape_run_id"], new_run)
        self.assertEqual(boss["result_run_id"], result_id)
        self.assertEqual(self.store.get_screening_run(old_run)["status"], "failed")
        # 迟到回调留审计且不制造活体 worker
        events = self.store.list_task_events(old_run)
        self.assertTrue(any(e.get("type") == "late_callback" for e in events))


if __name__ == "__main__":
    unittest.main()