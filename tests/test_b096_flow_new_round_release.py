"""中断轮的「开始新一轮」出路合同测试。

场景取自一条真实死胡同流程：一条轨道已经出结果（done + 结果快照），另一条被进程
重启打断（interrupted，没有任何活体 worker）。前端把流程外壳状态并入了「有活人在
跑」的判定，所以外壳一旦谎报「排队中」，新一轮入口就永久锁死。

这里锁死服务端事实：走完既有取消契约之后，外壳不再属于活动状态（前端得以开始新
一轮），而已完成轨道的结果轮一条不少。浏览器边界用桩隔离，不碰真实 Chrome。
"""

import pathlib
import tempfile
import unittest
from unittest import mock

from webui.app import create_app
from webui.frozen_browser_identity import FrozenBrowserCleanupResult
from webui.store import TaskStore


# useDiscoveryTasks.cancelActiveTasksForNewRound 容忍的取消回执：这些都不会中止
# 「开始新一轮」；其它失败（尤其 503）会把链路卡死。
TOLERATED_CANCEL_ERRORS = {
    "already_finished", "run_not_found", "task_not_active", "not_paused",
    "browser_cleanup_failed",
}
# isActiveParallelFlow / canResetNewRound 的活动状态口径。
ACTIVE_FLOW_STATUSES = {"queued", "running", "paused", "interrupted"}
RESETTABLE_FLOW_STATUSES = {"paused", "interrupted"}


class InterruptedFlowNewRoundReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-release-")
        root = pathlib.Path(self.temp.name)
        self.db_path = root / "state" / "webui.db"
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(self.db_path),
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]
        self.profile_id = "release-profile"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, ?, '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (self.profile_id, self.profile_id),
            )

    def tearDown(self):
        self.temp.cleanup()

    def _current_flow(self):
        response = self.client.get(
            f"/api/flows/current?profile_id={self.profile_id}"
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["flow"]

    def _seed_scrape_run(self, *, platform, flow_id, track_id):
        """抓取段：search_runs 与 screening_runs 共用同一个任务 id（与产线一致）。

        真实抓取会把 Flow/Track/platform 身份冻在 profile_snapshot_json 里，
        取消契约在事务内会再核一遍身份，缺了就报「属于别的 Flow」。
        """
        created = self.store.create_search_run(
            self.profile_id,
            {
                "flow_id": flow_id, "track_id": track_id,
                "platform": platform, "scope_digest": "release-digest",
            },
            "default",
        )
        run_id = created["id"]
        self.store.update_search_run(run_id, status="running")
        self._seed_screening_run(
            run_id, platform=platform, flow_id=flow_id, track_id=track_id,
        )
        return run_id

    def _seed_screening_run(self, run_id, *, platform, flow_id, track_id):
        self.store.create_screening_run(
            run_id,
            profile_id=self.profile_id,
            execution_params={
                "platform": platform, "flow_id": flow_id, "track_id": track_id,
            },
        )
        self.store.update_screening_run(run_id, status="running")
        return run_id

    def _seed_dead_ended_flow(self):
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key="release-dead-ended",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        tracks = {track["platform"]: track for track in flow["tracks"]}

        finished_scrape = self._seed_scrape_run(
            platform="boss", flow_id=flow["id"], track_id=tracks["boss"]["id"],
        )
        finished_screen = self._seed_screening_run(
            "release-finished-screen",
            platform="boss", flow_id=flow["id"], track_id=tracks["boss"]["id"],
        )
        self.store.update_screening_run(finished_scrape, status="succeeded")
        self.store.update_screening_run(finished_screen, status="succeeded")

        # 抓取段的 search 投影从未定稿：重启归一会把它落成 interrupted，
        # 与真实库里那条流程完全一致。
        stalled_scrape = self._seed_scrape_run(
            platform="zhilian", flow_id=flow["id"], track_id=tracks["zhilian"]["id"],
        )
        stalled_screen = self._seed_screening_run(
            "release-stalled-screen",
            platform="zhilian", flow_id=flow["id"], track_id=tracks["zhilian"]["id"],
        )
        self.store.update_screening_run(stalled_scrape, status="succeeded")

        snapshot = self.store.save_pipeline_result(
            {
                "platform": "boss",
                "jobs": [{"platform_job_id": "release-kept", "verdict": "match"}],
                "dropped": [],
                "total_scraped": 1,
            },
            {"platform": "boss"},
            profile_id=self.profile_id,
            status="done",
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": tracks["boss"]["id"],
                "scrape_task_id": finished_scrape,
            },
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="ai",
            scrape_run_id=finished_scrape, screen_run_id=finished_screen,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="done", stage="complete", result_run_id=snapshot,
        )
        self.store.update_flow_track(
            flow["id"], "zhilian", profile_id=self.profile_id,
            status="running", stage="ai",
            scrape_run_id=stalled_scrape, screen_run_id=stalled_screen,
        )

        # 新建 TaskStore = 进程重启：run 投影归一成 interrupted，轨道对齐同一事实。
        TaskStore(self.db_path)
        current = self._current_flow()
        statuses = {track["platform"]: track for track in current["tracks"]}
        self.assertEqual(statuses["boss"]["status"], "done")
        self.assertEqual(statuses["boss"]["result_run_id"], snapshot)
        self.assertEqual(statuses["zhilian"]["status"], "interrupted")
        return current, {
            "boss": [finished_scrape, finished_screen],
            "zhilian": [stalled_scrape, stalled_screen],
        }

    def _cancel(self, run_id):
        with mock.patch(
            "webui.frozen_browser_identity.close_frozen_run_browser",
            return_value=FrozenBrowserCleanupResult(ok=True, message="浏览器边界已隔离"),
        ):
            return self.client.post(f"/api/task/cancel/{run_id}")

    def test_cancel_contract_releases_an_interrupted_round_without_losing_finished_results(self):
        flow, track_run_ids = self._seed_dead_ended_flow()
        # 外壳如实说「已中断」：它仍是活动状态，但属于可解锁新一轮的那一档。
        self.assertEqual(flow["status"], "interrupted")
        self.assertIn(flow["status"], ACTIVE_FLOW_STATUSES)
        self.assertIn(flow["status"], RESETTABLE_FLOW_STATUSES)

        sent_ids = [*track_run_ids["boss"], *track_run_ids["zhilian"]]
        for run_id in sent_ids:
            response = self._cancel(run_id)
            body = response.get_json() or {}
            self.assertNotEqual(
                response.status_code, 503,
                f"{run_id} 的取消回执把新一轮卡死：{body}",
            )
            if response.status_code != 200:
                self.assertIn(
                    body.get("error"), TOLERATED_CANCEL_ERRORS,
                    f"{run_id} 返回了前端会中止重置的错误：{body}",
                )

        released = self._current_flow()
        self.assertNotIn(
            released["status"], ACTIVE_FLOW_STATUSES,
            "取消契约走完后的外壳仍被当成有活人在跑",
        )
        self.assertEqual(released["status"], "stopped")
        tracks = {track["platform"]: track for track in released["tracks"]}
        self.assertEqual(tracks["boss"]["status"], "done")
        self.assertEqual(tracks["zhilian"]["status"], "cancelled")
        self.assertFalse(self.store.flow_has_active_tracks(self.profile_id))

        # 已完成轨道的结果轮不受影响。
        self.assertEqual(
            tracks["boss"]["result_run_id"],
            next(
                track["result_run_id"] for track in flow["tracks"]
                if track["platform"] == "boss"
            ),
        )
        snapshot_run = self.store.get_screening_run(tracks["boss"]["result_run_id"])
        self.assertEqual(snapshot_run["record_kind"], "result_snapshot")
        self.assertEqual(
            self.store.get_flow_results(flow["id"], profile_id=self.profile_id)
            ["tracks"][0]["jobs"][0]["platform_job_id"],
            "release-kept",
        )


if __name__ == "__main__":
    unittest.main()
