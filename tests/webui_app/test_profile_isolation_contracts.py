"""Spec041 画像隔离合同测试（A/B 两画像 × BOSS/智联两平台 × 旧/新两轮）。

覆盖：
- 历史列表/详情/删除/归档必须按画像归属隔离，跨画像读写按不存在处理；
- 无归属（NULL/空）的老数据（画像功能之前的轮次）对所有画像可见、可归档、
  可删除——用户拍板的老数据保留口径；
- /api/latest-running-task 的内存任务与 DB 候选都必须按画像过滤
  （任务侧不放宽：无归属旧任务不接回，避免接回陈旧任务），
  以及简历分析后台任务携带画像身份、完成后不串到其它画像；
- /api/latest-pipeline-result 的最新单轮读取按画像过滤。

这些是"所有入口共享同一套页面状态、所有操作按画像隔离"的后端合同，
不作为浏览器端到端验收的替代。
"""

import io
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from webui.app import create_app


def _result_payload(platform: str, job_id: str) -> dict:
    return {
        "ok": True,
        "jobs": [{
            "platform": platform,
            "platform_job_id": job_id,
            "job_id": job_id,
            "title": "后端工程师",
            "verdict": "match",
        }],
        "dropped": [],
        "total_scraped": 1,
        "total_kept": 1,
        "total_matched": 1,
        "total_dropped": 0,
        "profile_summary": f"{platform} 画像",
    }


class ProfileIsolationContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]
        self.tasks = self.app.config["PIPELINE_TASKS"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    # -- helpers ---------------------------------------------------------

    def _save_round(self, profile_id: str, platform: str, job_id: str) -> str:
        return self.store.save_pipeline_result(
            _result_payload(platform, job_id),
            {"platform": platform, "keyword": "Python 后端", "city": ["上海"]},
            status="done",
            profile_id=profile_id,
        )

    def _archived_at(self, run_id: str):
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT archived_at FROM screening_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()
        return row["archived_at"] if row is not None else None

    def _seed_paused_run(self, run_id: str, profile_id: str, platform: str = "boss"):
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at, profile_id) "
                "VALUES (?, ?, 'paused', 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL, ?)",
                (str(run_id), platform, str(profile_id)),
            )
        return run_id

    def _register_memory_task(self, task_id: str, profile_id: str, *, status: str = "running"):
        self.tasks[task_id] = {
            "kind": "resume_analysis",
            "status": status,
            "progress": {"message": "正在分析简历…"},
            "logs": [],
            "result": None,
            "error": "",
            "started_at": 1,
            "finished_at": None,
            "stop_event": threading.Event(),
            "platform": "boss",
            "profile_id": profile_id,
        }

    # -- 历史列表/详情/删除/归档 -----------------------------------------

    def test_history_list_is_scoped_to_profile_and_platform(self):
        """A/B 两画像各自只看到自己的轮次；平台过滤与画像过滤叠加生效。"""
        run_a_boss = self._save_round("profile-a", "boss", "a-boss")
        run_a_zhilian = self._save_round("profile-a", "zhilian", "a-zhilian")
        run_b_boss = self._save_round("profile-b", "boss", "b-boss")

        items_a = self.client.get(
            "/api/result-history?profile_id=profile-a").get_json()["items"]
        self.assertEqual({item["run_id"] for item in items_a}, {run_a_boss, run_a_zhilian})

        items_b = self.client.get(
            "/api/result-history?profile_id=profile-b").get_json()["items"]
        self.assertEqual({item["run_id"] for item in items_b}, {run_b_boss})

        items_a_boss = self.client.get(
            "/api/result-history?profile_id=profile-a&platform=boss").get_json()["items"]
        self.assertEqual({item["run_id"] for item in items_a_boss}, {run_a_boss})

        items_unknown = self.client.get(
            "/api/result-history?profile_id=profile-missing").get_json()["items"]
        self.assertEqual(items_unknown, [])

    def test_history_detail_and_delete_reject_cross_profile_access(self):
        """跨画像读/删按不存在处理，且被拒后数据保持原样。"""
        run_b = self._save_round("profile-b", "boss", "b-job")

        detail_cross = self.client.get(
            f"/api/result-history/{run_b}?profile_id=profile-a")
        self.assertEqual(detail_cross.status_code, 404)
        self.assertEqual(detail_cross.get_json()["error"], "round_not_found")

        delete_cross = self.client.delete(
            f"/api/result-history/{run_b}?profile_id=profile-a")
        self.assertEqual(delete_cross.status_code, 404)
        self.assertIsNotNone(self.store.get_screening_run(run_b))

        detail_own = self.client.get(
            f"/api/result-history/{run_b}?profile_id=profile-b")
        self.assertEqual(detail_own.status_code, 200)
        self.assertEqual(detail_own.get_json()["source_run_id"], run_b)

        delete_own = self.client.delete(
            f"/api/result-history/{run_b}?profile_id=profile-b")
        self.assertEqual(delete_own.status_code, 200)
        self.assertEqual(delete_own.get_json()["deleted"], True)
        self.assertIsNone(self.store.get_screening_run(run_b))

    def test_archive_latest_only_touches_current_profile(self):
        """归档只动当前画像的未归档轮，另一画像的轮保持未归档。"""
        run_a = self._save_round("profile-a", "boss", "a-job")
        run_b = self._save_round("profile-b", "boss", "b-job")

        resp = self.client.post(
            "/api/result-history/archive-latest", json={"profile_id": "profile-a"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["archived_run_ids"], [run_a])

        self.assertIsNotNone(self._archived_at(run_a))
        self.assertIsNone(self._archived_at(run_b))

    # -- latest-running-task 画像隔离 -------------------------------------

    def test_latest_running_task_memory_scope_by_profile(self):
        """内存任务按画像返回：A 查只拿 A，C 查拿不到任何任务。"""
        self._register_memory_task("resume-analysis-a", "profile-a")
        self._register_memory_task("resume-analysis-b", "profile-b")

        got_a = self.client.get(
            "/api/latest-running-task?profile_id=profile-a").get_json()
        self.assertTrue(got_a["has_task"])
        self.assertEqual(got_a["task_id"], "resume-analysis-a")
        self.assertEqual(got_a["profile_id"], "profile-a")
        self.assertEqual(got_a["kind"], "resume_analysis")

        got_b = self.client.get(
            "/api/latest-running-task?profile_id=profile-b").get_json()
        self.assertEqual(got_b["task_id"], "resume-analysis-b")

        got_missing = self.client.get(
            "/api/latest-running-task?profile_id=profile-missing").get_json()
        self.assertFalse(got_missing.get("has_task"))

    def test_latest_running_task_db_candidate_scope_by_profile(self):
        """DB 里的暂停任务同样按画像隔离，不把别人的暂停任务接回。"""
        self._seed_paused_run("paused-a", "profile-a")
        self._seed_paused_run("paused-b", "profile-b", platform="zhilian")

        got_a = self.client.get(
            "/api/latest-running-task?profile_id=profile-a").get_json()
        self.assertTrue(got_a["has_task"])
        self.assertEqual(got_a["task_id"], "paused-a")
        self.assertEqual(got_a["platform"], "boss")

        got_b = self.client.get(
            "/api/latest-running-task?profile_id=profile-b").get_json()
        self.assertEqual(got_b["task_id"], "paused-b")
        self.assertEqual(got_b["platform"], "zhilian")

        got_missing = self.client.get(
            "/api/latest-running-task?profile_id=profile-missing").get_json()
        self.assertFalse(got_missing.get("has_task"))

    # -- 结果单轮读取 -----------------------------------------------------

    def test_latest_pipeline_result_is_scoped_to_profile(self):
        """最新单轮读取按画像过滤：A 只拿 A 的最近一轮，陌生画像拿不到。"""
        run_a = self._save_round("profile-a", "boss", "a-latest")
        self._save_round("profile-b", "boss", "b-latest")

        data_a = self.client.get(
            "/api/latest-pipeline-result?profile_id=profile-a").get_json()
        self.assertTrue(data_a["has_result"])
        self.assertEqual(data_a["source_run_id"], run_a)

        data_missing = self.client.get(
            "/api/latest-pipeline-result?profile_id=profile-missing").get_json()
        self.assertFalse(data_missing["has_result"])

    # -- 简历分析后台任务身份 ---------------------------------------------

    def test_background_resume_analysis_carries_profile_and_stays_isolated(self):
        """后台分析任务带画像身份：只有本画像能接回，完成后也不串台。"""
        started = threading.Event()
        release = threading.Event()
        fields = {
            "keyword": [{"word": "Python 后端", "recommended": True}],
            "city": [], "salary": [], "experience": [], "degree": [],
            "industry": [], "scale": [], "stage": [], "profile_summary": "画像",
        }

        def analyze(*_args, **_kwargs):
            started.set()
            release.wait(2)
            return fields

        with mock.patch.object(self.store, "get_ai_settings", return_value={
            "is_configured": True, "endpoint_url": "https://api.example.com", "model": "test",
        }), mock.patch.object(self.store, "get_credential_ref", return_value="ref"), \
                mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.analyze_resume_to_fields", side_effect=analyze):
            resp = self.client.post(
                "/api/analyze-resume",
                data={
                    "file": (io.BytesIO(b"resume"), "resume.txt"),
                    "platform": "boss", "background": "true", "profile_id": "profile-a",
                },
                content_type="multipart/form-data",
            )
            self.assertEqual(resp.status_code, 202)
            task_id = resp.get_json()["task_id"]
            self.assertTrue(started.wait(1))

            live_a = self.client.get(
                "/api/latest-running-task?profile_id=profile-a").get_json()
            self.assertEqual(live_a["task_id"], task_id)
            live_other = self.client.get(
                "/api/latest-running-task?profile_id=profile-b").get_json()
            self.assertFalse(live_other.get("has_task"))

            release.set()
            deadline = time.time() + 2
            state = {}
            while time.time() < deadline:
                state = self.client.get(f"/api/task-state/{task_id}").get_json()
                if state.get("status") == "done":
                    break
                time.sleep(0.01)
            self.assertEqual(state.get("status"), "done")

            live_other_after = self.client.get(
                "/api/latest-running-task?profile_id=profile-b").get_json()
            self.assertFalse(live_other_after.get("has_task"))
            live_a_after = self.client.get(
                "/api/latest-running-task?profile_id=profile-a").get_json()
            self.assertEqual(live_a_after["task_id"], task_id)

    # -- task-state 画像归属 ----------------------------------------------

    def test_task_state_memory_task_is_scoped_to_profile(self):
        """内存任务按画像读：本画像 200，别的画像按不存在处理，不泄漏任务内容。"""
        self._register_memory_task("resume-analysis-iso", "profile-a")

        own = self.client.get(
            "/api/task-state/resume-analysis-iso?profile_id=profile-a")
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.get_json()["profile_id"], "profile-a")

        cross = self.client.get(
            "/api/task-state/resume-analysis-iso?profile_id=profile-b")
        self.assertEqual(cross.status_code, 404)
        self.assertEqual(cross.get_json()["error"], "run_not_found")

        # 不带画像的查询保留为兼容口径（产品前端一律携带画像）。
        without_profile = self.client.get("/api/task-state/resume-analysis-iso")
        self.assertEqual(without_profile.status_code, 200)

    def test_task_state_db_round_is_scoped_to_profile(self):
        """DB 结果轮同样按画像校验：跨画像 404，本画像可读。"""
        run_b = self._save_round("profile-b", "boss", "b-state")

        cross = self.client.get(f"/api/task-state/{run_b}?profile_id=profile-a")
        self.assertEqual(cross.status_code, 404)
        self.assertEqual(cross.get_json()["error"], "run_not_found")

        own = self.client.get(f"/api/task-state/{run_b}?profile_id=profile-b")
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.get_json()["run_id"], run_b)

    def test_task_state_legacy_unowned_round_stays_visible(self):
        """无归属老任务对所有画像可见——与无归属历史结果同一兼容口径。"""
        legacy = self.store.save_pipeline_result(
            _result_payload("boss", "legacy-state-job"),
            {"platform": "boss", "keyword": "Python 后端", "city": ["上海"]},
            status="done",
        )

        got = self.client.get(f"/api/task-state/{legacy}?profile_id=profile-a")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.get_json()["run_id"], legacy)

    # -- 无归属老数据（画像功能之前的轮次）---------------------------------

    def test_legacy_unowned_rounds_stay_visible_and_manageable(self):
        """无归属老数据对所有画像可见、可归档、可删除；有归属的轮次隔离不变。

        用户拍板：老数据要留在历史里能看见（它产生于画像功能之前，无法归属）。
        """
        legacy = self.store.save_pipeline_result(
            _result_payload("boss", "legacy-job"),
            {"platform": "boss", "keyword": "Python 后端", "city": ["上海"]},
            status="done",
        )
        run_a = self._save_round("profile-a", "boss", "a-job")

        # 两个画像的历史列表都能看到老数据；严格归属的轮次互不串台
        items_a = self.client.get(
            "/api/result-history?profile_id=profile-a").get_json()["items"]
        self.assertEqual({item["run_id"] for item in items_a}, {legacy, run_a})
        items_b = self.client.get(
            "/api/result-history?profile_id=profile-b").get_json()["items"]
        self.assertEqual({item["run_id"] for item in items_b}, {legacy})

        # 详情可读（老数据对任意画像开放）
        detail = self.client.get(
            f"/api/result-history/{legacy}?profile_id=profile-b")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["source_run_id"], legacy)

        # 最新结果读取同样能看到老数据
        latest = self.client.get(
            "/api/latest-pipeline-result?profile_id=profile-b").get_json()
        self.assertTrue(latest["has_result"])
        self.assertEqual(latest["source_run_id"], legacy)

        # 归档：当前画像的轮次与无归属老数据一并收走，别的画像的轮次不动
        archived = self.client.post(
            "/api/result-history/archive-latest", json={"profile_id": "profile-b"},
        ).get_json()["archived_run_ids"]
        self.assertEqual(set(archived), {legacy})
        self.assertIsNotNone(self._archived_at(legacy))
        self.assertIsNone(self._archived_at(run_a))

        # 删除：老数据可被任一画像删除；有归属的轮次仍拒绝跨画像删除
        delete_legacy = self.client.delete(
            f"/api/result-history/{legacy}?profile_id=profile-a")
        self.assertEqual(delete_legacy.status_code, 200)
        self.assertIsNone(self.store.get_screening_run(legacy))

        delete_cross = self.client.delete(
            f"/api/result-history/{run_a}?profile_id=profile-b")
        self.assertEqual(delete_cross.status_code, 404)
        self.assertIsNotNone(self.store.get_screening_run(run_a))


if __name__ == "__main__":
    unittest.main()
