"""Formal integration tests for screening resilience rules (spec 003)."""

from __future__ import annotations

import unittest
from unittest import mock
import json
import pathlib
import sys
import tempfile

from tests.test_screening_fixtures import sample_screening_job
from scripts import boss_cdp_raw as boss_cdp_raw
from webui.screening import (
    execute_first_layer,
    partition_job,
    partition_jobs,
    verify_hard_rules_detailed,
)


class HardRuleResilienceTests(unittest.TestCase):
    def test_salary_ranges_pass_when_they_overlap_at_all(self):
        result = verify_hard_rules_detailed(
            sample_screening_job(salary="18-25K"), {"salary": "405"},
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["parse_failures"], [])

    def test_missing_salary_is_lenient_but_tracked(self):
        result = verify_hard_rules_detailed(
            sample_screening_job(salary=""), {"salary": "405"},
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["parse_failures"], ["salary"])

    def test_missing_city_is_lenient_but_tracked(self):
        result = verify_hard_rules_detailed(
            sample_screening_job(location=""), {"city": "上海"},
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["parse_failures"], ["city"])

    def test_master_candidate_passes_bachelor_job_requirement(self):
        result = verify_hard_rules_detailed(
            sample_screening_job(tags="3-5年 | 本科"), {"degree": "204"},
        )
        self.assertTrue(result["passed"])

    def test_associate_and_bachelor_are_bidirectionally_compatible(self):
        associate_for_bachelor = verify_hard_rules_detailed(
            sample_screening_job(tags="3-5年 | 本科"), {"degree": "202"},
        )
        bachelor_for_associate = verify_hard_rules_detailed(
            sample_screening_job(tags="3-5年 | 大专"), {"degree": "203"},
        )
        self.assertTrue(associate_for_bachelor["passed"])
        self.assertTrue(bachelor_for_associate["passed"])

    def test_bachelor_candidate_fails_master_job_requirement(self):
        result = verify_hard_rules_detailed(
            sample_screening_job(tags="3-5年 | 硕士"), {"degree": "203"},
        )
        self.assertFalse(result["passed"])

    def test_master_candidate_fails_doctorate_job_requirement(self):
        result = verify_hard_rules_detailed(
            sample_screening_job(tags="3-5年 | 博士"), {"degree": "204"},
        )
        self.assertFalse(result["passed"])

    def test_unparseable_degree_is_lenient_but_tracked(self):
        result = verify_hard_rules_detailed(
            sample_screening_job(tags="3-5年 | 学历不限"), {"degree": "203"},
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["parse_failures"], ["degree"])


class ThreeWayPartitionTests(unittest.TestCase):
    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_uncertain_semantic_result_goes_to_pending(self, assess):
        assess.return_value = {"verdict": "pending", "failure_stage": "ai_uncertain"}
        self.assertEqual(partition_job(sample_screening_job(), {}, "resume", "jd"), "pending")

    @mock.patch("webui.screening.partition_job")
    def test_one_job_exception_does_not_block_the_batch(self, partition):
        partition.side_effect = ["match", RuntimeError("boom"), "mismatch"]
        jobs = [sample_screening_job(job_id=f"j{i}") for i in range(3)]
        result = partition_jobs(jobs, {}, "resume")
        self.assertEqual([j["job_id"] for j in result["match"]], ["j0"])
        self.assertEqual([j["job_id"] for j in result["pending"]], ["j1"])
        self.assertEqual([j["job_id"] for j in result["mismatch"]], ["j2"])
        self.assertEqual(result["pending_failures"]["j1"], "verification_error")

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_unexpected_semantic_exception_does_not_block_the_batch(self, assess):
        assess.side_effect = OSError("temporary provider failure")
        jobs = [
            sample_screening_job(job_id="j0"),
            sample_screening_job(job_id="j1"),
        ]

        result = partition_jobs(jobs, {}, "resume")

        self.assertEqual(result["pending"], jobs)
        self.assertEqual(
            result["pending_failures"],
            {"j0": "verification_error", "j1": "verification_error"},
        )

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_pending_failure_stage_is_exposed_only_as_safe_code(self, assess):
        assess.return_value = {"verdict": "pending", "failure_stage": "ai_timeout"}
        job = sample_screening_job(job_id="p1")
        result = partition_jobs([job], {}, "resume")
        self.assertEqual(result["pending"], [job])
        self.assertEqual(result["pending_failures"], {"p1": "ai_timeout"})


class InterruptedFetchPreservationTests(unittest.TestCase):
    class _RunningProcess:
        def __init__(self):
            self.returncode = None
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    def test_explicit_cancel_terminates_process_and_marks_run_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "screening_r1.json"
            process = self._RunningProcess()
            store = mock.MagicMock()
            store.get_screening_run.return_value = {"status": "queued"}

            with mock.patch("webui.screening.subprocess.Popen", return_value=process):
                result = execute_first_layer(
                    {}, "Python", output_path=output,
                    python_executable=sys.executable,
                    store=store, run_id="r1", should_cancel=lambda: True,
                    timeout_seconds=30,
                )

        self.assertTrue(process.terminated)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["error_code"], "cancelled")
        store.update_screening_run_status.assert_any_call(
            "r1", "interrupted", source_count=0, source_cursor=0,
            error_code="cancelled",
        )

    def test_timeout_with_valid_partial_artifact_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "screening_r1.json"
            jobs = [sample_screening_job(job_id="saved-before-timeout")]
            output.write_text(
                json.dumps({"jobs": jobs}, ensure_ascii=False), encoding="utf-8",
            )
            process = self._RunningProcess()
            store = mock.MagicMock()
            store.get_screening_run.return_value = {"status": "queued"}

            with mock.patch("webui.screening.subprocess.Popen", return_value=process):
                result = execute_first_layer(
                    {}, "Python", output_path=output,
                    python_executable=sys.executable,
                    store=store, run_id="r1", timeout_seconds=0,
                )

        self.assertTrue(process.terminated)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["error_code"], "timeout")
        self.assertEqual(result["jobs"], jobs)
        store.update_screening_run_status.assert_any_call(
            "r1", "partial", source_count=1, source_cursor=1,
            error_code="timeout",
        )

    def test_timeout_without_artifact_marks_run_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            process = self._RunningProcess()
            store = mock.MagicMock()
            store.get_screening_run.return_value = {"status": "queued"}

            with mock.patch("webui.screening.subprocess.Popen", return_value=process):
                with self.assertRaisesRegex(RuntimeError, "抓取器执行超时"):
                    execute_first_layer(
                        {}, "Python",
                        output_path=pathlib.Path(tmp) / "screening_r1.json",
                        python_executable=sys.executable,
                        store=store, run_id="r1", timeout_seconds=0,
                    )

        store.update_screening_run_status.assert_any_call(
            "r1", "failed", error_code="timeout",
        )

    def test_first_layer_forwards_resume_start_page_to_scraper(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "screening_r1.json"
            output.write_text(json.dumps({"jobs": []}), encoding="utf-8")
            with mock.patch("webui.screening.subprocess.run") as run:
                run.return_value.returncode = 0
                execute_first_layer(
                    {}, "Python", output_path=output,
                    python_executable=sys.executable, start_page=3,
                )

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--start-page") + 1], "3")

    def test_successful_fetch_merges_run_scoped_detail_jd_into_jobs_and_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            output = root / "screening_r1.json"
            detail_output = root / "screening_r1_details.json"
            jobs = [
                sample_screening_job(job_id="with-jd"),
                sample_screening_job(job_id="without-jd"),
            ]

            def run_scraper(command, **_kwargs):
                output.write_text(
                    json.dumps({"jobs": jobs}, ensure_ascii=False),
                    encoding="utf-8",
                )
                detail_output.write_text(
                    json.dumps([
                        {"job_id": "with-jd", "jd": "完整职位描述 Python FastAPI"},
                    ], ensure_ascii=False),
                    encoding="utf-8",
                )
                self.assertIn("--detail-output", command)
                self.assertEqual(
                    pathlib.Path(command[command.index("--detail-output") + 1]),
                    detail_output,
                )
                return mock.Mock(returncode=0)

            with mock.patch("webui.screening.subprocess.run", side_effect=run_scraper):
                result = execute_first_layer(
                    {}, "Python", output_path=output,
                    detail_output_path=detail_output,
                    python_executable=sys.executable,
                )

            self.assertEqual(result["jobs"][0]["jd"], "完整职位描述 Python FastAPI")
            self.assertNotIn("jd", result["jobs"][1])
            persisted = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                persisted["jobs"][0]["jd"],
                "完整职位描述 Python FastAPI",
            )

    def test_nonzero_fetch_with_valid_partial_artifact_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "partial.json"
            jobs = [sample_screening_job(job_id="saved-1")]
            output.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False), encoding="utf-8")
            store = mock.MagicMock()
            store.get_screening_run.return_value = {"status": "queued"}
            with mock.patch("webui.screening.subprocess.run") as run:
                run.return_value.returncode = 1
                result = execute_first_layer(
                    {}, "Python", output_path=output,
                    python_executable=sys.executable, store=store, run_id="r1",
                )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["jobs"][0]["job_id"], "saved-1")
        store.update_screening_run_status.assert_any_call(
            "r1", "partial", source_count=1, source_cursor=1,
        )

    def test_invalid_success_artifact_marks_run_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "invalid.json"
            output.write_text("{not-json", encoding="utf-8")
            store = mock.MagicMock()
            store.get_screening_run.return_value = {"status": "queued"}
            with mock.patch("webui.screening.subprocess.run") as run:
                run.return_value.returncode = 0

                with self.assertRaises(RuntimeError, msg="invalid artifact must fail safely"):
                    execute_first_layer(
                        {}, "Python", output_path=output,
                        python_executable=sys.executable, store=store, run_id="r1",
                    )

            store.update_screening_run_status.assert_any_call("r1", "failed")


class ScraperPageCheckpointTests(unittest.TestCase):
    class _FakeCDP:
        def __init__(self):
            self.calls = []

        def send(self, method, _params=None, _session_id=None):
            self.calls.append((method, _params or {}))
            if method == "Target.createTarget":
                return {"result": {"targetId": "target-1"}}
            if method == "Target.attachToTarget":
                return {"result": {"sessionId": "session-1"}}
            return {"result": {}}

        def eval_js(self, _script, _session_id=None):
            return [{
                "title": "new job",
                "job_link": "https://www.zhipin.com/job_detail/new.html",
                "salary": "20-30K",
            }]

        def close(self):
            return None

    def test_resume_page_preserves_existing_jobs_and_updates_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "jobs.json"
            old_job = {
                "job_id": "old-id", "title": "old job",
                "job_link": "https://www.zhipin.com/job_detail/old.html",
            }
            output.write_text(json.dumps({
                "keyword": "Python", "last_completed_page": 1,
                "jobs": [old_job],
            }), encoding="utf-8")

            with mock.patch.object(boss_cdp_raw, "CDPSession", return_value=self._FakeCDP()):
                with mock.patch.object(boss_cdp_raw.time, "sleep"):
                    result = boss_cdp_raw.scrape_list(
                        "Python", "上海", 2, {}, str(output), start_page=2,
                    )

            persisted = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual([job["title"] for job in result["jobs"]], ["old job", "new job"])
        self.assertEqual(persisted["last_completed_page"], 2)
        self.assertEqual(len(persisted["jobs"]), 2)

    def test_empty_resume_page_still_advances_checkpoint(self):
        fake = self._FakeCDP()
        fake.eval_js = mock.Mock(return_value=[])
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "jobs.json"
            output.write_text(json.dumps({
                "keyword": "Python", "last_completed_page": 1, "jobs": [],
            }), encoding="utf-8")
            with mock.patch.object(boss_cdp_raw, "CDPSession", return_value=fake):
                with mock.patch.object(boss_cdp_raw.time, "sleep"):
                    boss_cdp_raw.scrape_list(
                        "Python", "上海", 2, {}, str(output), start_page=2,
                    )
            persisted = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(persisted["last_completed_page"], 2)

    def test_resume_start_page_navigates_to_boss_origin_before_api_fetch(self):
        fake = self._FakeCDP()
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "jobs.json"
            output.write_text(json.dumps({
                "keyword": "Python", "last_completed_page": 1, "jobs": [],
            }), encoding="utf-8")
            with mock.patch.object(boss_cdp_raw, "CDPSession", return_value=fake):
                with mock.patch.object(boss_cdp_raw.time, "sleep"):
                    with mock.patch.object(boss_cdp_raw.random, "randint", return_value=3):
                        boss_cdp_raw.scrape_list(
                            "Python", "上海", 2, {}, str(output), start_page=2,
                        )

        navigations = [params for method, params in fake.calls if method == "Page.navigate"]
        self.assertEqual(len(navigations), 1)
        self.assertIn("page=2", navigations[0]["url"])


if __name__ == "__main__":
    unittest.main()
