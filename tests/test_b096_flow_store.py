import pathlib
import sqlite3
import tempfile
import unittest
import json

from webui.store import TaskStore
from webui.store import DiscoveryStoreConflictError


class B096FlowStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-flow-")
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        self.profile_one = self._create_profile("profile-one")
        self.profile_two = self._create_profile("profile-two")

    def tearDown(self):
        self.temp.cleanup()

    def _create_profile(self, profile_id):
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, ?, '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (profile_id, profile_id),
            )
        return profile_id

    def _finish_flow(self, flow):
        for track in flow["tracks"]:
            self.store.update_flow_track(
                flow["id"],
                track["platform"],
                profile_id=flow["profile_id"],
                status="done",
                stage="complete",
            )

    def test_all_creates_one_flow_with_two_atomic_platform_tracks(self):
        flow = self.store.create_flow(
            profile_id=self.profile_one,
            selection="all",
            start_key="all-1",
            confirmed_filters={
                "boss": {"keywords": ["python"]},
                "zhilian": {"keywords": ["python", "sql"]},
            },
        )

        self.assertEqual(flow["selection"], "all")
        self.assertEqual(flow["profile_id"], self.profile_one)
        self.assertEqual(
            {track["platform"] for track in flow["tracks"]},
            {"boss", "zhilian"},
        )
        with self.store._connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM flow_tracks WHERE flow_id = ?",
                    (flow["id"],),
                ).fetchone()[0],
                2,
            )

        self._finish_flow(flow)
        before = self.store.list_flows(self.profile_one, include_legacy=False)
        with self.assertRaises(ValueError):
            self.store.create_flow(
                profile_id=self.profile_one,
                selection="all",
                start_key="invalid-platforms",
                confirmed_filters={"boss": {}, "third": {}},
            )
        after = self.store.list_flows(self.profile_one, include_legacy=False)
        self.assertEqual([item["id"] for item in before], [item["id"] for item in after])

    def test_single_platform_creates_one_real_track_after_previous_flow_finishes(self):
        first = self.store.create_flow(
            profile_id=self.profile_one,
            selection="all",
            start_key="all-1",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        self._finish_flow(first)

        single = self.store.create_flow(
            profile_id=self.profile_one,
            selection="boss",
            start_key="boss-1",
            confirmed_filters={"boss": {"city": "北京"}},
        )
        self.assertEqual(single["selection"], "boss")
        self.assertEqual(len(single["tracks"]), 1)
        self.assertEqual(single["tracks"][0]["platform"], "boss")
        self.assertNotEqual(single["id"], first["id"])

    def test_start_key_is_idempotent_but_profile_isolation_is_preserved(self):
        flow = self.store.create_flow(
            profile_id=self.profile_one,
            selection="all",
            start_key="retry-1",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        retried = self.store.create_flow(
            profile_id=self.profile_one,
            selection="all",
            start_key="retry-1",
            confirmed_filters={"boss": {"changed": True}, "zhilian": {}},
        )
        self.assertEqual(retried["id"], flow["id"])
        self.assertEqual(
            [track["id"] for track in retried["tracks"]],
            [track["id"] for track in flow["tracks"]],
        )

        other = self.store.create_flow(
            profile_id=self.profile_two,
            selection="all",
            start_key="retry-1",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        self.assertNotEqual(other["id"], flow["id"])
        with self.assertRaises(KeyError):
            self.store.get_flow(flow["id"], profile_id=self.profile_two)

    def test_active_flow_blocks_any_new_selection_until_all_tracks_are_terminal(self):
        self.store.create_flow(
            profile_id=self.profile_one,
            selection="boss",
            start_key="boss-active",
            confirmed_filters={"boss": {}},
        )
        with self.assertRaises(ValueError):
            self.store.create_flow(
                profile_id=self.profile_one,
                selection="zhilian",
                start_key="zhilian-blocked",
                confirmed_filters={"zhilian": {}},
            )

    def test_legacy_screening_run_is_read_as_virtual_single_track_flow(self):
        screening = self.store.create_screening_run(
            "legacy-screen",
            profile_id=self.profile_one,
            execution_params={"platform": "boss"},
        )
        self.assertEqual(screening["id"], "legacy-screen")
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET record_kind = 'result_snapshot', status = 'done', "
                "created_at = '2026-01-01T00:00:00Z', updated_at = '2026-01-01T00:00:00Z' "
                "WHERE id = ?",
                ("legacy-screen",),
            )

        flows = self.store.list_flows(self.profile_one, include_legacy=True)
        legacy = next(item for item in flows if item["id"] == "legacy-screen")
        self.assertTrue(legacy["legacy"])
        self.assertEqual(legacy["selection"], "boss")
        self.assertEqual(len(legacy["tracks"]), 1)
        self.assertEqual(legacy["tracks"][0]["platform"], "boss")
        self.assertEqual(legacy["tracks"][0]["screen_run_id"], "legacy-screen")

    def test_run_binding_enforces_profile_and_platform_ownership(self):
        flow = self.store.create_flow(
            profile_id=self.profile_one,
            selection="all",
            start_key="binding-1",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        scrape = self.store.create_search_run(self.profile_one, {}, "ai")
        boss_track_id = next(
            track["id"] for track in flow["tracks"] if track["platform"] == "boss"
        )
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE search_runs SET profile_snapshot_json = ? WHERE id = ?",
                (json.dumps({"flow_id": flow["id"], "track_id": boss_track_id}), scrape["id"]),
            )
        screen = self.store.create_screening_run(
            "screen-boss",
            profile_id=self.profile_one,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": boss_track_id,
            },
        )
        bound = self.store.bind_flow_track_runs(
            flow["id"],
            "boss",
            profile_id=self.profile_one,
            scrape_run_id=scrape["id"],
            screen_run_id=screen["id"],
            result_run_id=screen["id"],
        )
        boss = next(track for track in bound["tracks"] if track["platform"] == "boss")
        zhilian = next(track for track in bound["tracks"] if track["platform"] == "zhilian")
        self.assertEqual(boss["scrape_run_id"], scrape["id"])
        self.assertEqual(boss["screen_run_id"], screen["id"])
        self.assertIsNone(zhilian["screen_run_id"])

        other_screen = self.store.create_screening_run(
            "screen-other",
            profile_id=self.profile_two,
            execution_params={"platform": "boss"},
        )
        with self.assertRaises(ValueError):
            self.store.bind_flow_track_runs(
                flow["id"],
                "boss",
                profile_id=self.profile_one,
                screen_run_id=other_screen["id"],
            )
        current = self.store.get_flow(flow["id"], profile_id=self.profile_one)
        self.assertEqual(
            next(track for track in current["tracks"] if track["platform"] == "boss")["screen_run_id"],
            screen["id"],
        )

    def test_track_status_and_failure_are_scoped_to_one_platform(self):
        flow = self.store.create_flow(
            profile_id=self.profile_one,
            selection="all",
            start_key="status-1",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        updated = self.store.update_flow_track(
            flow["id"],
            "boss",
            profile_id=self.profile_one,
            status="failed",
            stage="screen",
            error_code="screen_submit_failed",
            reason="AI unavailable",
        )
        boss = next(track for track in updated["tracks"] if track["platform"] == "boss")
        zhilian = next(track for track in updated["tracks"] if track["platform"] == "zhilian")
        self.assertEqual(boss["status"], "failed")
        self.assertEqual(boss["error_code"], "screen_submit_failed")
        self.assertEqual(zhilian["status"], "queued")
        self.assertIsNone(zhilian["error_code"])

    def test_flow_results_keep_scraped_jobs_visible_when_failed_track_has_no_ai_snapshot(self):
        flow = self.store.create_flow(
            profile_id=self.profile_one,
            selection="all",
            start_key="results-partial-1",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        scrape_run_id = self.store.create_search_run(
            self.profile_one, {}, "ai"
        )["id"]
        self.store.create_screening_run(
            scrape_run_id,
            profile_id=self.profile_one,
            execution_params={"platform": "boss", "flow_id": flow["id"]},
        )
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO scrape_run_jobs "
                "(run_id, platform_job_id, combo_key, job_payload_json, scraped_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    scrape_run_id,
                    "boss-job-1",
                    "default",
                    json.dumps({
                        "platform": "boss",
                        "platform_job_id": "boss-job-1",
                        "title": "Python 工程师",
                        "company": "示例公司",
                    }, ensure_ascii=False),
                    "2026-01-01T00:00:00Z",
                ),
            )
        self.store.bind_flow_track_runs(
            flow["id"],
            "boss",
            profile_id=self.profile_one,
            screen_run_id=scrape_run_id,
        )
        self.store.update_flow_track(
            flow["id"],
            "boss",
            profile_id=self.profile_one,
            status="failed",
            stage="screen",
            error_code="ai_unavailable",
            reason="AI unavailable",
        )

        payload = self.store.get_flow_results(flow["id"], profile_id=self.profile_one)

        boss = next(track for track in payload["tracks"] if track["platform"] == "boss")
        zhilian = next(track for track in payload["tracks"] if track["platform"] == "zhilian")
        self.assertEqual(boss["status"], "failed")
        self.assertFalse(boss["ai_screened"])
        self.assertEqual(boss["screened_count"], 0)
        self.assertEqual(boss["message"], "未完成 AI 筛选")
        self.assertEqual([job["platform_job_id"] for job in boss["jobs"]], ["boss-job-1"])
        self.assertEqual(payload["screened_count"], 0)
        self.assertEqual(zhilian["jobs"], [])

    def test_flow_results_use_only_bound_snapshot_and_deduplicate_within_track(self):
        flow = self.store.create_flow(
            profile_id=self.profile_one,
            selection="all",
            start_key="results-snapshot-1",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        result_run_id = self.store.save_pipeline_result(
            {
                "platform": "boss",
                "jobs": [
                    {"platform_job_id": "boss-job-1", "title": "A", "verdict": "match"},
                    {"platform_job_id": "boss-job-1", "title": "A duplicate", "verdict": "match"},
                ],
                "dropped": [],
                "total_scraped": 2,
            },
            {"platform": "boss"},
            profile_id=self.profile_one,
            execution_params={"platform": "boss", "flow_id": flow["id"]},
        )
        self.store.bind_flow_track_runs(
            flow["id"],
            "boss",
            profile_id=self.profile_one,
            result_run_id=result_run_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_one,
            status="done", stage="complete",
        )

        payload = self.store.get_flow_results(flow["id"], profile_id=self.profile_one)

        boss = next(track for track in payload["tracks"] if track["platform"] == "boss")
        self.assertEqual([job["platform_job_id"] for job in boss["jobs"]], ["boss-job-1"])
        self.assertTrue(boss["ai_screened"])
        self.assertEqual(boss["screened_count"], 1)
        self.assertEqual(payload["screened_count"], 1)

    def test_finish_flow_task_binds_partial_snapshot_and_releases_new_round_gate(self):
        flow = self.store.create_flow(
            profile_id=self.profile_one,
            selection="boss",
            start_key="finish-flow-task",
            confirmed_filters={"boss": {}},
        )
        track = next(item for item in flow["tracks"] if item["platform"] == "boss")
        task = self.store.create_screening_run(
            "finish-flow-task-run",
            profile_id=self.profile_one,
            execution_params={
                "platform": "boss",
                "flow_id": flow["id"],
                "track_id": track["id"],
            },
        )
        self.store.update_screening_run(task["id"], status="running")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_one,
            status="running", stage="ai", screen_run_id=task["id"],
        )
        snapshot = self.store.save_pipeline_result(
            {
                "platform": "boss",
                "jobs": [{"platform_job_id": "boss-partial", "verdict": "uncertain"}],
                "dropped": [],
                "total_scraped": 1,
            },
            {"platform": "boss"},
            profile_id=self.profile_one,
            status="partial",
            execution_params={
                "platform": "boss",
                "flow_id": flow["id"],
                "track_id": track["id"],
                "scrape_task_id": task["id"],
            },
        )

        finished = self.store.finish_flow_task_atomic(
            flow["id"],
            "boss",
            profile_id=self.profile_one,
            task_run_id=task["id"],
            result_run_id=snapshot,
        )

        boss = next(item for item in finished["tracks"] if item["platform"] == "boss")
        self.assertEqual(boss["result_run_id"], snapshot)
        self.assertEqual(boss["stage"], "complete")
        self.assertIn(boss["status"], {"stopped", "done", "succeeded"})
        self.assertFalse(self.store.flow_has_active_tracks(self.profile_one))
        self.assertEqual(
            self.store.get_flow_results(flow["id"], profile_id=self.profile_one)
            ["tracks"][0]["jobs"][0]["platform_job_id"],
            "boss-partial",
        )

    def test_finish_does_not_overwrite_failed_track(self):
        flow = self.store.create_flow(
            profile_id=self.profile_one, selection="boss",
            start_key="finish-failed-track", confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run = self.store.create_screening_run(
            "finish-failed-track-run", profile_id=self.profile_one,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_screening_run(run["id"], status="running")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_one,
            status="failed", stage="ai", error_code="source_login_required",
            reason="登录失败",
        )
        snapshot = self.store.save_pipeline_result(
            {"platform": "boss", "jobs": [{"platform_job_id": "late"}], "dropped": []},
            {"platform": "boss"}, profile_id=self.profile_one, status="partial",
            execution_params={"platform": "boss", "flow_id": flow["id"], "track_id": track["id"]},
        )
        with self.assertRaises(DiscoveryStoreConflictError):
            self.store.finish_flow_task_atomic(
                flow["id"], "boss", profile_id=self.profile_one,
                task_run_id=run["id"], result_run_id=snapshot,
            )
        current = self.store.get_flow(flow["id"], profile_id=self.profile_one)["tracks"][0]
        self.assertEqual(current["status"], "failed")
        self.assertIsNone(current["result_run_id"])

    def test_finish_duplicate_with_same_snapshot_is_idempotent(self):
        flow = self.store.create_flow(
            profile_id=self.profile_one, selection="boss",
            start_key="finish-idempotent", confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run = self.store.create_screening_run(
            "finish-idempotent-run", profile_id=self.profile_one,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_screening_run(run["id"], status="running")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_one,
            status="running", stage="ai", screen_run_id=run["id"],
        )
        snapshot = self.store.save_pipeline_result(
            {"platform": "boss", "jobs": [{"platform_job_id": "same"}], "dropped": []},
            {"platform": "boss"}, profile_id=self.profile_one, status="partial",
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
                "scrape_task_id": run["id"],
            },
        )
        first = self.store.finish_flow_task_atomic(
            flow["id"], "boss", profile_id=self.profile_one,
            task_run_id=run["id"], result_run_id=snapshot,
        )
        second = self.store.finish_flow_task_atomic(
            flow["id"], "boss", profile_id=self.profile_one,
            task_run_id=run["id"], result_run_id=snapshot,
        )
        self.assertEqual(first["tracks"][0]["result_run_id"], snapshot)
        self.assertEqual(second["tracks"][0]["result_run_id"], snapshot)
        self.assertEqual(second["tracks"][0]["status"], "stopped")

    def test_finish_does_not_overwrite_failed_task_run(self):
        flow = self.store.create_flow(
            profile_id=self.profile_one, selection="boss",
            start_key="finish-failed-run", confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run = self.store.create_screening_run(
            "finish-failed-run-id", profile_id=self.profile_one,
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_screening_run(run["id"], status="running")
        self.store.update_screening_run(
            run["id"], status="failed", error_code="source_login_required",
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_one,
            status="running", stage="ai", screen_run_id=run["id"],
        )
        snapshot = self.store.save_pipeline_result(
            {"platform": "boss", "jobs": [{"platform_job_id": "late-failed"}], "dropped": []},
            {"platform": "boss"}, profile_id=self.profile_one, status="partial",
            execution_params={
                "platform": "boss", "flow_id": flow["id"], "track_id": track["id"],
                "scrape_task_id": run["id"],
            },
        )
        with self.assertRaises(DiscoveryStoreConflictError):
            self.store.finish_flow_task_atomic(
                flow["id"], "boss", profile_id=self.profile_one,
                task_run_id=run["id"], result_run_id=snapshot,
            )
        self.assertEqual(self.store.get_screening_run(run["id"])["status"], "failed")
        current = self.store.get_flow(flow["id"], profile_id=self.profile_one)["tracks"][0]
        self.assertEqual(current["status"], "running")
        self.assertIsNone(current["result_run_id"])

    def test_flow_finish_claim_is_reversible_before_snapshot_publication(self):
        flow = self.store.create_flow(
            profile_id=self.profile_one,
            selection="boss",
            start_key="finish-claim-reversible",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        run = self.store.create_screening_run(
            "finish-claim-reversible-run",
            profile_id=self.profile_one,
            execution_params={
                "platform": "boss",
                "flow_id": flow["id"],
                "track_id": track["id"],
            },
        )
        self.store.update_screening_run(run["id"], status="running")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_one,
            status="running", stage="ai", screen_run_id=run["id"],
        )

        claim = self.store.claim_flow_finish(
            flow["id"], "boss", profile_id=self.profile_one,
            task_run_id=run["id"],
        )
        self.assertTrue(claim["claimed"])
        claimed = self.store.get_screening_run(run["id"])
        self.assertEqual(claimed["status"], "interrupted")
        self.assertEqual(claimed["error_code"], "user_finished")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id=self.profile_one)
            ["tracks"][0]["status"],
            "running",
        )

        self.assertTrue(self.store.restore_flow_finish_claim(claim))
        restored = self.store.get_screening_run(run["id"])
        self.assertEqual(restored["status"], "running")
        self.assertIsNone(restored["error_code"])
        self.assertNotEqual(restored.get("interruption_kind"), "user_finished")


class B096FlowEnvelopeStatusTests(unittest.TestCase):
    """流程外壳状态必须把「还没开始」与「重启后被中断」分开如实报告。

    外壳谎报 queued 时，前端会把这条流程当成「有活人在跑」，用户既不能开新一轮
    也没有别的出口；这里锁死树干自身的归类口径，不看任何平台。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-flow-envelope-")
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        self.profile_id = "profile-envelope"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, ?, '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (self.profile_id, self.profile_id),
            )

    def tearDown(self):
        self.temp.cleanup()

    def _flow(self, start_key):
        return self.store.create_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key=start_key,
            confirmed_filters={"boss": {}, "zhilian": {}},
        )

    def _finish_track(self, flow, platform):
        """一条轨道真正跑完并留下结果轮。"""
        run = self.store.create_screening_run(
            f"envelope-{flow['id']}-{platform}-result",
            profile_id=self.profile_id,
            execution_params={"flow_id": flow["id"]},
        )
        return self.store.update_flow_track(
            flow["id"], platform,
            profile_id=self.profile_id,
            status="done", stage="complete", result_run_id=run["id"],
        )

    def _interrupt_track_through_restart(self, flow, platform):
        """按进程重启的真实口径把轨道落成 interrupted。

        直接写 interrupted 会绕过 transition 表，产线里这个状态只来自启动归一：
        绑定的 run 先被归一成 interrupted，再由 reconcile 把轨道对齐到同一事实。
        """
        track = next(t for t in flow["tracks"] if t["platform"] == platform)
        self.store.update_flow_track(
            flow["id"], platform,
            profile_id=self.profile_id, status="running", stage="ai",
        )
        self.store.create_screening_run(
            f"envelope-{flow['id']}-{platform}-screen",
            profile_id=self.profile_id,
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"],
            },
        )
        self.store.update_flow_track(
            flow["id"], platform,
            profile_id=self.profile_id,
            screen_run_id=f"envelope-{flow['id']}-{platform}-screen",
        )
        # 重新打开同一个库 = 进程重启。
        restarted = TaskStore(self.db_path)
        interrupted = next(
            t for t in restarted.get_flow(
                flow["id"], profile_id=self.profile_id,
            )["tracks"] if t["platform"] == platform
        )
        self.assertEqual(interrupted["status"], "interrupted")
        return restarted

    def test_interrupted_track_without_a_pending_track_is_not_reported_as_queued(self):
        flow = self._flow("envelope-interrupted")
        self._finish_track(flow, "boss")
        restarted = self._interrupt_track_through_restart(flow, "zhilian")

        current = restarted.get_current_flow(self.profile_id)
        self.assertEqual(
            {track["status"] for track in current["tracks"]},
            {"done", "interrupted"},
        )
        self.assertEqual(current["status"], "interrupted")
        self.assertEqual(
            restarted.get_flow(flow["id"], profile_id=self.profile_id)["status"],
            "interrupted",
        )

    def test_pending_track_still_reports_queued_envelope(self):
        flow = self._flow("envelope-queued")
        self._finish_track(flow, "boss")

        current = self.store.get_current_flow(self.profile_id)
        self.assertEqual(
            {track["status"] for track in current["tracks"]},
            {"done", "queued"},
        )
        self.assertEqual(current["status"], "queued")

    def test_running_track_outranks_an_interrupted_sibling(self):
        flow = self._flow("envelope-running")
        self.store.update_flow_track(
            flow["id"], "boss",
            profile_id=self.profile_id, status="running", stage="scrape",
        )
        restarted = self._interrupt_track_through_restart(flow, "zhilian")

        current = restarted.get_current_flow(self.profile_id)
        self.assertEqual(
            {track["status"] for track in current["tracks"]},
            {"running", "interrupted"},
        )
        self.assertEqual(current["status"], "running")


if __name__ == "__main__":
    unittest.main()
