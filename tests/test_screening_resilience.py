"""Formal integration tests for screening resilience rules (spec 003)."""

from __future__ import annotations

import unittest
from unittest import mock
import json
import pathlib
import sys
import tempfile

from tests.test_screening_fixtures import sample_screening_job
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
    def test_pending_failure_stage_is_exposed_only_as_safe_code(self, assess):
        assess.return_value = {"verdict": "pending", "failure_stage": "ai_timeout"}
        job = sample_screening_job(job_id="p1")
        result = partition_jobs([job], {}, "resume")
        self.assertEqual(result["pending"], [job])
        self.assertEqual(result["pending_failures"], {"p1": "ai_timeout"})


class InterruptedFetchPreservationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
