"""026 B080：重抓补抓到的 JD 真实进入 AI 精筛。

对缺 JD 岗位重抓时，补抓成功的 JD 必须写入传给 ``match_jds`` 的岗位对象，
精筛才能基于完整 JD 给出正常判定；补抓失败的岗位保持"未抓到 JD"语义，
且成功岗位不受失败岗位影响（spec FR-009/FR-010/FR-011）。
"""

from __future__ import annotations

import threading
import unittest
from unittest import mock

from webui.runners.recrawl_task import run_recrawl_task

TARGET_URL = "https://www.zhipin.com/job_detail/026b080.html"


def _make_task():
    return {
        "kind": "recrawl", "status": "queued", "progress": {},
        "logs": [], "result": None, "error": "",
        "started_at": 0, "finished_at": None,
        "stop_event": threading.Event(),
    }


class _FakeStore:
    """recrawl_task 用到的 store 最小替身（只读内存）。"""

    def __init__(self, jobs):
        self._jobs = jobs

    def load_latest_pipeline_result(self, run_id):
        return {"result": {"jobs": self._jobs}}

    def get_screening_run(self, run_id):
        return {}

    def get_ai_settings(self):
        return {"endpoint_url": "http://ai.local", "model": "gpt"}

    def get_credential_ref(self):
        return "cred-ref"

    def get_advanced_config_state(self):
        return {}

    def save_checkpoint(self, *args, **kwargs):
        pass

    def append_task_event(self, *args, **kwargs):
        pass

    def append_task_events(self, *args, **kwargs):
        pass

    def save_recrawl_jd_and_checkpoint(self, *args, **kwargs):
        pass

    def load_screening_verdicts(self, task_id):
        return {}

    def save_verdict_and_checkpoint_atomic(self, *args, **kwargs):
        pass

    def recount_pipeline_result(self, run_id):
        return {}


class _FakeCtx:
    """recrawl_task 运行所需的 ctx 最小替身。"""

    def __init__(self, jobs, task_id):
        self.lock = threading.RLock()
        self.tasks = {task_id: _make_task()}
        self.threading = threading
        self.store = _FakeStore(jobs)
        self.operational_errors = (Exception,)
        self.ai_service = mock.Mock()
        self.ai_service.retrieve_api_key.return_value = "sk-test"
        self.app = mock.Mock()
        self.app.config = {"RESULT_DIR": "tmp"}
        self.screen_stage_messages = {}
        self.event_stage_names = {}
        self._stopped = []

    def is_user_finished(self, task_id):
        return False

    def release_worker_resume_claims(self, task):
        pass

    def recrawl_job_key(self, job):
        return str(job.get("job_id") or job.get("platform_job_id") or "")

    def recrawl_overall_percent(self, stage, current, total):
        return 0

    def activate_task_browser(self, *args, **kwargs):
        pass

    def make_cdp_source(self, *args, **kwargs):
        return mock.Mock()

    def schedule_pipeline_task_cleanup(self, task_id):
        pass

    def record_pause_failure(self, *args, **kwargs):
        pass

    def write_run(self, *args, **kwargs):
        pass

    def persist_jd_job_failures(self, *args, **kwargs):
        pass

    def load_legacy_advanced_settings(self):
        return {"match_batch_size": 4}


class RecrawlJdPassThroughTests(unittest.TestCase):
    """重抓补抓的 JD 必须写入 AI 精筛输入（spec FR-009）。"""

    def _run(self, targets, detail_jobs, match_verdict="match"):
        task_id = "recrawl-b080"
        ctx = _FakeCtx(targets, task_id)
        match_calls = []

        def fake_match_jds(chunk, profile_summary, endpoint, api_key, **kwargs):
            match_calls.append(list(chunk))
            return {
                "verdicts": {
                    str(j.get("job_id")): {
                        "verdict": match_verdict, "reason": "OK", "caveats": [],
                    }
                    for j in chunk
                }
            }

        with mock.patch("webui.ai.match_jds", side_effect=fake_match_jds), \
                mock.patch(
                    "webui.pipeline_exec.ensure_chrome_ready",
                    return_value=(True, None),
                ), \
                mock.patch(
                    "webui.pipeline_exec.fetch_job_details",
                    return_value={"jobs": detail_jobs},
                ), \
                mock.patch(
                    "webui.pipeline_exec.failed_code_label",
                    return_value="抓取失败",
                ), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("webui.result_rounds.apply_recrawl_writeback"):
            run_recrawl_task(
                ctx, task_id,
                job_ids=[str(t.get("job_id")) for t in targets],
                profile_summary="3 年 Python 后端，熟悉 FastAPI",
                source_run_id="src-b080",
            )
        return ctx, match_calls

    def test_fetched_jd_is_passed_into_ai_screening(self):
        """T021：补抓成功 → 精筛收到的岗位对象携带补抓 JD（FR-009）。"""
        targets = [{"job_id": "J1", "jd": "", "source_url": TARGET_URL}]
        _, match_calls = self._run(
            targets,
            detail_jobs=[{"job_id": "J1", "jd": "补抓到的完整 JD 文本"}],
        )
        self.assertEqual(len(match_calls), 1)
        judged = match_calls[0][0]
        self.assertEqual(judged.get("job_id"), "J1")
        self.assertEqual(judged.get("jd"), "补抓到的完整 JD 文本")

    def test_failed_recrawl_keeps_missing_jd_verdict_path(self):
        """T022：补抓确实失败（JD 仍空）→ 不进入 AI 精筛、任务保留为 paused
        （不误判成功；spec FR-010 边界）。"""
        targets = [{"job_id": "J1", "jd": "", "source_url": TARGET_URL}]
        ctx, match_calls = self._run(targets, detail_jobs=[])
        self.assertEqual(match_calls, [])
        task = ctx.tasks["recrawl-b080"]
        self.assertEqual(task.get("status"), "paused")

    def test_partial_fetch_only_judges_fetched_jobs(self):
        """T023：多岗位重抓，补抓成功的带 JD 独立进精筛，失败的不受影响（FR-011）。"""
        targets = [
            {"job_id": "J1", "jd": "", "source_url": TARGET_URL},
            {"job_id": "J2", "jd": "", "source_url": TARGET_URL + "2"},
        ]
        _, match_calls = self._run(
            targets,
            detail_jobs=[{"job_id": "J1", "jd": "JD1 文本"}],
        )
        self.assertEqual(len(match_calls), 1)
        judged = match_calls[0][0]
        self.assertEqual(judged.get("job_id"), "J1")
        self.assertEqual(judged.get("jd"), "JD1 文本")
        self.assertEqual(len(match_calls[0]), 1)


class RecrawlActivityFactTests(unittest.TestCase):
    """028 B084：重抓补抓的招聘者活跃事实必须写入精筛输入并回写结果行。"""

    _FACT = {
        "source": "boss", "text": "半年前活跃", "last_online_ms": None,
        "age_lower_days": 180.0, "age_upper_days": None, "known": True,
    }

    def _run(self, targets, detail_jobs):
        task_id = "recrawl-b084"
        ctx = _FakeCtx(targets, task_id)
        match_calls = []
        save_calls = []

        def fake_match_jds(chunk, profile_summary, endpoint, api_key, **kwargs):
            match_calls.append(list(chunk))
            return {
                "verdicts": {
                    str(j.get("job_id")): {
                        "verdict": "match", "reason": "OK", "caveats": [],
                    }
                    for j in chunk
                }
            }

        def fake_save_recrawl(source_run_id, recrawl_run_id, jd_by_job,
                              completed_job_ids, **kwargs):
            save_calls.append(kwargs)

        store = ctx.store
        store.save_recrawl_jd_and_checkpoint = fake_save_recrawl

        with mock.patch("webui.ai.match_jds", side_effect=fake_match_jds), \
                mock.patch(
                    "webui.pipeline_exec.ensure_chrome_ready",
                    return_value=(True, None),
                ), \
                mock.patch(
                    "webui.pipeline_exec.fetch_job_details",
                    return_value={"jobs": detail_jobs},
                ), \
                mock.patch(
                    "webui.pipeline_exec.failed_code_label",
                    return_value="抓取失败",
                ), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("webui.result_rounds.apply_recrawl_writeback"):
            run_recrawl_task(
                ctx, task_id,
                job_ids=[str(t.get("job_id")) for t in targets],
                profile_summary="3 年 Python 后端",
                source_run_id="src-b084",
            )
        return match_calls, save_calls

    def test_activity_fact_reaches_ai_input_and_writeback(self):
        """补抓详情带活跃事实 → 精筛输入与结果行回写都携带（028 FR-003）。"""
        targets = [{"job_id": "J1", "jd": "", "source_url": TARGET_URL}]
        match_calls, save_calls = self._run(
            targets,
            detail_jobs=[{
                "job_id": "J1", "jd": "补抓 JD",
                "extra": {"recruiter_activity": self._FACT},
            }],
        )
        judged = match_calls[0][0]
        self.assertEqual(
            judged.get("extra", {}).get("recruiter_activity"), self._FACT,
        )
        self.assertEqual(len(save_calls), 1)
        self.assertEqual(save_calls[0].get("extra_by_job", {}).get("J1"), self._FACT)

    def test_no_fact_keeps_writeback_compatible(self):
        """无活跃事实的重抓走原路径：extra_by_job 为空、精筛输入无该键。"""
        targets = [{"job_id": "J1", "jd": "", "source_url": TARGET_URL}]
        match_calls, save_calls = self._run(
            targets,
            detail_jobs=[{"job_id": "J1", "jd": "补抓 JD"}],
        )
        self.assertNotIn("extra", match_calls[0][0])
        self.assertEqual(save_calls[0].get("extra_by_job", {}), {})


if __name__ == "__main__":
    unittest.main()
