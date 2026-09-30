import json
import pathlib
import tempfile
import unittest

from flask import Flask

from webui.result_history import ResultHistoryService
from webui.result_history_api import register_result_history_routes
from webui.store import TaskStore


class B096FlowHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-history-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile_id = "profile-history"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'history', '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (self.profile_id,),
            )

    def tearDown(self):
        self.temp.cleanup()

    def _snapshot(self, flow, platform, title):
        run_id = self.store.save_pipeline_result(
            {
                "platform": platform,
                "jobs": [{"platform_job_id": f"{platform}-{title}", "title": title, "verdict": "match"}],
                "dropped": [],
                "total_scraped": 1,
            },
            {"platform": platform},
            profile_id=self.profile_id,
            execution_params={"platform": platform, "flow_id": flow["id"]},
        )
        self.store.bind_flow_track_runs(
            flow["id"], platform, profile_id=self.profile_id, result_run_id=run_id,
        )
        self.store.update_flow_track(
            flow["id"], platform, profile_id=self.profile_id,
            status="done", stage="complete",
        )
        return run_id

    def test_history_has_one_outer_flow_and_two_inner_tracks_including_failed_empty_line(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="history-all-1",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        scrape_id = self.store.create_screening_run(
            "history-scrape-z", profile_id=self.profile_id,
            execution_params={"platform": "zhilian", "flow_id": flow["id"]},
        )["id"]
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO scrape_run_jobs "
                "(run_id, platform_job_id, combo_key, job_payload_json, scraped_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (scrape_id, "z-history-1", "default", json.dumps({
                    "platform": "zhilian", "platform_job_id": "z-history-1", "title": "Z"
                }), "2026-01-01T00:00:00Z"),
            )
        self.store.bind_flow_track_runs(
            flow["id"], "zhilian", profile_id=self.profile_id, screen_run_id=scrape_id,
        )
        self.store.update_flow_track(
            flow["id"], "zhilian", profile_id=self.profile_id,
            status="failed", stage="screen", error_code="ai_unavailable",
            reason="AI unavailable",
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="failed", stage="scrape", error_code="empty", reason="没有岗位",
        )

        history = ResultHistoryService(self.store).list_flow_history(self.profile_id)

        self.assertEqual(len(history), 1)
        item = history[0]
        self.assertEqual(item["flow_id"], flow["id"])
        self.assertEqual({track["platform"] for track in item["tracks"]}, {"boss", "zhilian"})
        zhilian = next(track for track in item["tracks"] if track["platform"] == "zhilian")
        boss = next(track for track in item["tracks"] if track["platform"] == "boss")
        self.assertEqual(zhilian["message"], "未完成 AI 筛选")
        self.assertEqual(zhilian["screened_count"], 0)
        self.assertEqual(zhilian["jobs"][0]["platform_job_id"], "z-history-1")
        self.assertEqual(boss["jobs"], [])
        self.assertEqual(boss["status"], "failed")

    def test_archive_flow_only_archives_bound_result_snapshots(self):
        first = self.store.create_flow(
            profile_id=self.profile_id, selection="boss", start_key="history-boss-1",
            confirmed_filters={"boss": {}},
        )
        first_run = self._snapshot(first, "boss", "first")
        second = self.store.create_flow(
            profile_id=self.profile_id, selection="zhilian", start_key="history-zhilian-1",
            confirmed_filters={"zhilian": {}},
        )
        second_run = self._snapshot(second, "zhilian", "second")

        archived = ResultHistoryService(self.store).archive_flow(first["id"], self.profile_id)

        self.assertEqual(archived, [first_run])
        with self.store._connection() as conn:
            first_archived = conn.execute(
                "SELECT archived_at FROM screening_runs WHERE id = ?", (first_run,)
            ).fetchone()[0]
            second_archived = conn.execute(
                "SELECT archived_at FROM screening_runs WHERE id = ?", (second_run,)
            ).fetchone()[0]
        self.assertIsNotNone(first_archived)
        self.assertIsNone(second_archived)

    def test_flow_history_http_projection_and_flow_scoped_archive(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id, selection="boss", start_key="history-http-1",
            confirmed_filters={"boss": {}},
        )
        run_id = self._snapshot(flow, "boss", "HTTP")
        app = Flask(__name__)
        register_result_history_routes(app, self.store)
        client = app.test_client()

        listing = client.get(
            f"/api/result-history/flows?profile_id={self.profile_id}"
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.get_json()["items"][0]["flow_id"], flow["id"])
        detail = client.get(
            f"/api/result-history/flows/{flow['id']}?profile_id={self.profile_id}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["item"]["tracks"][0]["platform"], "boss")

        archived = client.post(
            "/api/result-history/archive-latest",
            json={"profile_id": self.profile_id, "flow_id": flow["id"]},
        )
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.get_json()["archived_run_ids"], [run_id])

    def test_history_tracks_preserve_v2_condition_snapshots_and_isolate_flows(self):
        v2_snapshot = {
            "mapperVersion": "b096-v2-test", "snapshotVersion": 2,
            "unifiedValues": {"salary": ["10k-20k"]},
            "platformValues": {"boss": {"salary": ["10-20k"]}, "zhilian": {"salary": ["10K-15K"]}},
            "bossStage": ["full_time"], "zhilianCompanyNature": ["state_owned"],
        }
        flow = self.store.create_flow(
            profile_id=self.profile_id, selection="all", start_key="history-v2-1",
            confirmed_filters={"boss": v2_snapshot, "zhilian": v2_snapshot},
        )
        changed = {**v2_snapshot, "mapperVersion": "b096-v2-newer"}
        self._snapshot(flow, "zhilian", "V2z")
        self.store.update_flow_track(flow["id"], "boss", profile_id=self.profile_id, status="done", stage="complete")
        self.store.update_flow_track(flow["id"], "zhilian", profile_id=self.profile_id, status="done", stage="complete")
        second = self.store.create_flow(
            profile_id=self.profile_id, selection="all", start_key="history-v2-2",
            confirmed_filters={"boss": changed, "zhilian": changed},
        )
        self.store.update_flow_track(second["id"], "boss", profile_id=self.profile_id, status="failed", stage="scrape", error_code="empty", reason="没有岗位")
        self.store.update_flow_track(second["id"], "zhilian", profile_id=self.profile_id, status="failed", stage="scrape", error_code="empty", reason="没有岗位")
        self._snapshot(flow, "boss", "V2")
        history = ResultHistoryService(self.store).list_flow_history(self.profile_id)
        item = next(entry for entry in history if entry["flow_id"] == flow["id"])
        boss = next(track for track in item["tracks"] if track["platform"] == "boss")
        zhilian = next(track for track in item["tracks"] if track["platform"] == "zhilian")
        for track in (boss, zhilian):
            self.assertEqual(track["confirmed_filters_snapshot"]["snapshotVersion"], 2)
            self.assertEqual(track["confirmed_filters_snapshot"]["platformValues"]["boss"]["salary"], ["10-20k"])
            self.assertEqual(track["confirmed_filters_snapshot"]["bossStage"], ["full_time"])
        other = next(entry for entry in history if entry["flow_id"] != flow["id"])
        other_boss = next(track for track in other["tracks"] if track["platform"] == "boss")
        self.assertEqual(other_boss["confirmed_filters_snapshot"]["mapperVersion"], "b096-v2-newer")

    def test_history_tracks_support_v1_plain_field_snapshots_without_recoding(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id, selection="all", start_key="history-v1-1",
            confirmed_filters={"boss": {"salary": ["406"]}, "zhilian": {"salary": ["406"]}},
        )
        self._snapshot(flow, "zhilian", "V1")
        history = ResultHistoryService(self.store).list_flow_history(self.profile_id)
        item = next(entry for entry in history if entry["flow_id"] == flow["id"])
        zhilian = next(track for track in item["tracks"] if track["platform"] == "zhilian")
        self.assertEqual(zhilian["confirmed_filters_snapshot"], {"salary": ["406"]})

    def test_flow_history_separates_legacy_rounds_from_durable_flows(self):
        """历史抽屉按 ``legacy`` 分流：有 durable flow 身份的轮走 Flow 卡片，
        旧结果轮继续走平铺轮次列表（043 的删除轮次与查看日志挂在这条列表上）。
        标记丢失或旧轮被合成成“真实流程”，界面上就会再次顶掉整块旧能力。
        """
        legacy_run = self.store.save_pipeline_result(
            {
                "platform": "boss",
                "jobs": [{"platform_job_id": "legacy-1", "title": "旧轮岗位", "verdict": "match"}],
                "dropped": [],
                "total_scraped": 1,
            },
            {"platform": "boss"},
            profile_id=self.profile_id,
            execution_params={"platform": "boss"},
        )
        flow = self.store.create_flow(
            profile_id=self.profile_id, selection="boss", start_key="history-legacy-split",
            confirmed_filters={"boss": {}},
        )
        bound_run = self._snapshot(flow, "boss", "Durable")

        history = ResultHistoryService(self.store).list_flow_history(self.profile_id)
        by_id = {entry["flow_id"]: entry for entry in history}
        self.assertIn(legacy_run, by_id)
        self.assertTrue(by_id[legacy_run]["legacy"])
        self.assertIn(flow["id"], by_id)
        self.assertFalse(by_id[flow["id"]]["legacy"])
        # 真实流程已拥有结果轮的旧轮不得再合成一份 legacy Flow。
        self.assertNotIn(bound_run, by_id)

        # 两条来源都要能读到各自数据：平铺轮次列表继续拿到旧轮（删除/日志的
        # run id 与抓取任务 id），Flow 卡片继续拿到真实流程的内层轨道。
        rounds = ResultHistoryService(self.store).list_history(profile_id=self.profile_id)
        self.assertIn(legacy_run, [round["run_id"] for round in rounds])
        self.assertIn(bound_run, [round["run_id"] for round in rounds])
        durable = by_id[flow["id"]]
        self.assertEqual(
            [track["result_run_id"] for track in durable["tracks"]], [bound_run],
        )

    def test_deleting_a_flow_result_round_keeps_the_flow_readable_and_the_log_line_bound(
        self,
    ):
        """删掉结果轮之后流程列表必须仍然讲得清事实。

        ``DELETE FROM screening_runs`` 由 ``ON DELETE SET NULL`` 清空
        ``flow_tracks.result_run_id``，流程行与抓取任务线都留着：历史卡片据此
        判定「这一轮已经没有结果了，但日志仍可看」。这里钉住后端在删除后给出的
        形状，前端不得再按 ``result_run_id`` 是否存在一并收掉三个入口。
        """
        flow = self.store.create_flow(
            profile_id=self.profile_id, selection="boss", start_key="history-deleted-1",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        scrape = self.store.create_search_run(
            self.profile_id,
            {"platform": "boss", "flow_id": flow["id"], "track_id": track["id"]},
            "ai",
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id, scrape_run_id=scrape["id"],
        )
        run_id = self._snapshot(flow, "boss", "Deleted")
        service = ResultHistoryService(self.store)

        before = next(
            entry for entry in service.list_flow_history(self.profile_id)
            if entry["flow_id"] == flow["id"]
        )
        self.assertEqual(before["status"], "done")
        self.assertEqual([track["result_run_id"] for track in before["tracks"]], [run_id])

        self.assertTrue(service.delete_round(run_id, self.profile_id))

        after = next(
            entry for entry in service.list_flow_history(self.profile_id)
            if entry["flow_id"] == flow["id"]
        )
        deleted_track = after["tracks"][0]
        self.assertIsNone(deleted_track["result_run_id"])
        self.assertEqual(str(deleted_track["scrape_run_id"]), str(scrape["id"]))
        self.assertEqual(deleted_track["jobs"], [])
        self.assertEqual(deleted_track["dropped"], [])
        self.assertEqual(deleted_track["screened_count"], 0)
        # 平铺轮次列表不再给出被删轮；本轮不新增 Flow 删除能力，流程行仍在。
        rounds = service.list_history(profile_id=self.profile_id)
        self.assertNotIn(run_id, [round_["run_id"] for round_ in rounds])
        self.assertIn(flow["id"], [entry["flow_id"] for entry in service.list_flow_history(self.profile_id)])

    def test_archive_rejects_partial_flow_scope_instead_of_archiving_global_results(self):
        app = Flask(__name__)
        register_result_history_routes(app, self.store)
        client = app.test_client()
        for body in (
            {"flow_id": "flow-only"},
            {"profile_id": self.profile_id},
        ):
            response = client.post("/api/result-history/archive-latest", json=body)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.get_json()["error"], "flow_scope_required")


if __name__ == "__main__":
    unittest.main()
