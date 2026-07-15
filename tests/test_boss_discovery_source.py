"""BOSS discovery source adapter tests (feature 004)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from pathlib import Path

from webui.source import (
    BossCdpSource,
    FakeJobSource,
    SourceOutcome,
    SAFE_FAILURE_CODES,
    _input_hash,
    _safe_tail,
    _safe_host,
)


class _RecordingExecutor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self.result


class BossSourceExecutorTests(unittest.TestCase):
    def test_default_runner_delegates_to_bounded_executor(self):
        executor = _RecordingExecutor(SimpleNamespace(
            returncode=0, output_tail="safe", failure_code=None,
        ))
        cancel_event = threading.Event()
        source = BossCdpSource(
            executor=executor, timeout_seconds=7, cancel_event=cancel_event,
        )

        result = source._default_run(["python", "scraper.py"], 7)

        self.assertEqual(result, (0, "safe"))
        self.assertEqual(executor.calls[0][1]["timeout_seconds"], 7)
        self.assertIs(executor.calls[0][1]["cancel_event"], cancel_event)

    def test_fetch_detail_rejects_non_boss_url_before_process_start(self):
        calls = []

        def runner(*args):
            calls.append(args)
            return 0, ""

        with tempfile.TemporaryDirectory() as tmp:
            source = BossCdpSource(runner=runner)
            outcome = source.fetch_detail(
                {"job_id": "evil", "source_url": "https://evil.example/job/1"},
                detail_output_path=str(Path(tmp) / "detail.json"),
            )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")
        self.assertEqual(calls, [])

    def test_artifact_reader_rejects_file_over_size_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobs.json"
            path.write_text(json.dumps({"jobs": [{"jd": "x" * 1000}]}), encoding="utf-8")
            source = BossCdpSource(max_artifact_bytes=100)
            self.assertIsNone(source._read_jobs(str(path)))


def _make_plan_item(*, keyword="Python", city="北京", input_hash=None, list_output_path=None,
                    target_pages=1, source_filters=None) -> dict:
    payload = {
        "keyword": keyword,
        "city": city,
        "source_filters": source_filters or {},
        "target_pages": target_pages,
    }
    return {
        **payload,
        "input_hash": input_hash if input_hash is not None else _input_hash(payload),
        "list_output_path": list_output_path or "",
    }


# ---------------------------------------------------------------------------
# T035: JobSource adapter (fake source) isolation and input_hash checks
# ---------------------------------------------------------------------------


class JobSourceAdapterTests(unittest.TestCase):
    """T035: list/detail isolation, input_hash verification, typed outcomes."""

    def test_fake_source_list_success(self):
        source = FakeJobSource(list_jobs={("Python", "北京"): [
            {"job_id": "j1", "title": "后端", "company": "A", "source_url": "https://x/1"},
            {"job_id": "j2", "title": "Python", "company": "B", "source_url": "https://x/2"},
        ]})
        plan = _make_plan_item(keyword="Python", city="北京")
        outcome = source.fetch_list(plan)
        self.assertTrue(outcome.ok)
        self.assertEqual(len(outcome.jobs), 2)
        self.assertEqual(outcome.failed_code, None)
        self.assertIn("job_count=2", outcome.safe_log)
        self.assertEqual(len(source.list_calls), 1)

    def test_fake_source_list_empty_returns_success(self):
        source = FakeJobSource(list_jobs={})
        plan = _make_plan_item(keyword="Python", city="北京")
        outcome = source.fetch_list(plan)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.jobs, [])

    def test_list_failure_isolates_other_queries(self):
        source = FakeJobSource(
            list_jobs={("Go", "上海"): [{"job_id": "j1", "title": "Go"}]},
            list_failures={("Python", "北京")},
        )
        bad_outcome = source.fetch_list(_make_plan_item(keyword="Python", city="北京"))
        good_outcome = source.fetch_list(_make_plan_item(keyword="Go", city="上海"))
        self.assertFalse(bad_outcome.ok)
        self.assertEqual(bad_outcome.failed_code, "source_blocked")
        self.assertTrue(good_outcome.ok)
        self.assertEqual(len(good_outcome.jobs), 1)

    def test_detail_failure_isolates_other_jobs(self):
        source = FakeJobSource(
            detail_jobs={"j1": {"title": "后端", "jd": "..."}.copy()},
            detail_failures={"j2"},
        )
        ok_outcome = source.fetch_detail({"job_id": "j1", "source_url": "https://x/1"})
        bad_outcome = source.fetch_detail({"job_id": "j2", "source_url": "https://x/2"})
        self.assertTrue(ok_outcome.ok)
        self.assertEqual(ok_outcome.detail.get("title"), "后端")
        self.assertFalse(bad_outcome.ok)
        self.assertEqual(bad_outcome.failed_code, "source_blocked")

    def test_input_hash_mismatch_returns_drift(self):
        source = FakeJobSource(list_jobs={("Python", "北京"): [{"job_id": "j1"}]})
        plan = _make_plan_item(keyword="Python", city="北京", input_hash="stale-hash-not-matching")
        outcome = source.fetch_list(plan)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_input_drift")
        self.assertIn("input_hash_mismatch", outcome.safe_log)

    def test_input_hash_correct_passes(self):
        source = FakeJobSource(list_jobs={("Python", "北京"): [{"job_id": "j1"}]})
        plan = _make_plan_item(keyword="Python", city="北京")
        outcome = source.fetch_list(plan)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.input_hash, plan["input_hash"])

    def test_typed_outcome_never_leaks_exception_text(self):
        source = FakeJobSource(list_failures={("Python", "北京")})
        outcome = source.fetch_list(_make_plan_item(keyword="Python", city="北京"))
        self.assertFalse(outcome.ok)
        # safe_log must contain only safe fields, no JD body or stack trace
        self.assertNotIn("Traceback", outcome.safe_log)
        self.assertNotIn("Exception", outcome.safe_log)

    def test_missing_keyword_returns_invalid_output(self):
        source = FakeJobSource()
        plan = _make_plan_item(keyword="", city="北京")
        plan["input_hash"] = ""  # also missing
        outcome = source.fetch_list(plan)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_missing_list_output_path_returns_invalid_output(self):
        """BossCdpSource (not FakeJobSource) requires list_output_path for subprocess invocation."""
        tmp = tempfile.mkdtemp(prefix="boss-source-")
        try:
            list_path = os.path.join(tmp, "list.json")
            runner = _MockRunner(returncode=0, jobs_payload=[], jobs_path=list_path)
            source = BossCdpSource(runner=runner)
            plan = _make_plan_item(keyword="Python", city="北京")
            plan["list_output_path"] = ""
            outcome = source.fetch_list(plan)
            self.assertFalse(outcome.ok)
            self.assertEqual(outcome.failed_code, "source_invalid_output")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_plan_item_not_dict_returns_invalid_output(self):
        source = FakeJobSource()
        outcome = source.fetch_list("not-a-dict")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_safe_failure_codes_set_is_complete(self):
        expected = {
            "source_unreachable", "source_blocked", "source_not_found",
            "source_invalid_output", "source_input_drift", "source_timeout",
            "source_unknown_error",
        }
        self.assertEqual(set(SAFE_FAILURE_CODES), expected)

    def test_safe_tail_truncates_and_strips_newlines(self):
        text = "line1\nline2\n" + "x" * 200 + "\nfinal"
        tail = _safe_tail(text, max_chars=50)
        self.assertLessEqual(len(tail), 50)
        self.assertNotIn("\n", tail)

    def test_safe_host_returns_netloc_only(self):
        self.assertEqual(_safe_host("https://www.zhipin.com/job/123"), "www.zhipin.com")
        self.assertEqual(_safe_host(""), "")
        self.assertEqual(_safe_host("not a url"), "")

    def test_source_outcome_to_dict_safe_fields_only(self):
        outcome = SourceOutcome.success(jobs=[{"job_id": "j1"}], safe_log="ok")
        d = outcome.to_dict()
        self.assertEqual(d["ok"], True)
        self.assertEqual(d["job_count"], 1)
        self.assertEqual(d["has_detail"], False)
        self.assertIsNone(d["failed_code"])
        # No raw jobs or detail in to_dict (only counts)
        self.assertNotIn("jobs", d)
        self.assertNotIn("detail", d)

    def test_detail_missing_url_returns_invalid_output(self):
        source = FakeJobSource()
        outcome = source.fetch_detail({"job_id": "j1"})
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")


# ---------------------------------------------------------------------------
# BossCdpSource with mocked subprocess (no real scraper invocation)
# ---------------------------------------------------------------------------


class _MockRunner:
    """Mock subprocess runner that returns scripted returncode + captured output."""

    def __init__(self, *, returncode=0, captured="", jobs_payload=None, detail_payload=None,
                 jobs_path=None, detail_path=None):
        self.returncode = returncode
        self.captured = captured
        self.jobs_payload = jobs_payload
        self.detail_payload = detail_payload
        self.jobs_path = jobs_path
        self.detail_path = detail_path
        self.calls = []

    def __call__(self, command, timeout):
        self.calls.append({"command": list(command), "timeout": timeout})
        # Write scripted output if a path was provided in command
        if self.jobs_payload is not None and self.jobs_path:
            Path(self.jobs_path).write_text(json.dumps({"jobs": self.jobs_payload}, ensure_ascii=False), encoding="utf-8")
        if self.detail_payload is not None and self.detail_path:
            Path(self.detail_path).write_text(json.dumps(self.detail_payload, ensure_ascii=False), encoding="utf-8")
        return self.returncode, self.captured


class BossCdpSourceMockedTests(unittest.TestCase):
    """BossCdpSource unit tests with mocked subprocess runner."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="boss-source-")
        self.list_path = os.path.join(self._tmp, "list.json")
        self.detail_path = os.path.join(self._tmp, "detail.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_source(self, **runner_kwargs):
        runner = _MockRunner(jobs_path=self.list_path, **runner_kwargs)
        return BossCdpSource(runner=runner), runner

    def test_list_success_with_mock_runner(self):
        runner = _MockRunner(
            returncode=0,
            jobs_payload=[{"job_id": "j1", "title": "后端"}],
            jobs_path=self.list_path,
        )
        source = BossCdpSource(runner=runner)
        plan = _make_plan_item(keyword="Python", city="北京", list_output_path=self.list_path)
        outcome = source.fetch_list(plan)
        self.assertTrue(outcome.ok)
        self.assertEqual(len(outcome.jobs), 1)
        # Verify command shape
        cmd = runner.calls[0]["command"]
        self.assertIn("--keyword", cmd)
        self.assertIn("Python", cmd)
        self.assertIn("--city", cmd)

    def test_list_failure_nonzero_returncode(self):
        runner = _MockRunner(returncode=2, captured="some error output", jobs_path=self.list_path)
        source = BossCdpSource(runner=runner)
        plan = _make_plan_item(keyword="Python", city="北京", list_output_path=self.list_path)
        outcome = source.fetch_list(plan)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_blocked")
        self.assertIn("returncode=2", outcome.safe_log)

    def test_list_timeout(self):
        def runner_timeout(command, timeout):
            raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)
        source = BossCdpSource(runner=runner_timeout)
        plan = _make_plan_item(keyword="Python", city="北京", list_output_path=self.list_path)
        outcome = source.fetch_list(plan)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_timeout")

    def test_list_scraper_not_found(self):
        def runner_fnf(command, timeout):
            raise FileNotFoundError("python")
        source = BossCdpSource(runner=runner_fnf)
        plan = _make_plan_item(keyword="Python", city="北京", list_output_path=self.list_path)
        outcome = source.fetch_list(plan)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_unreachable")

    def test_list_invalid_output_no_file(self):
        runner = _MockRunner(returncode=0, captured="")
        source = BossCdpSource(runner=runner)
        plan = _make_plan_item(keyword="Python", city="北京", list_output_path=self.list_path)
        outcome = source.fetch_list(plan)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_list_invalid_output_bad_json(self):
        Path(self.list_path).write_text("not json", encoding="utf-8")
        runner = _MockRunner(returncode=0, captured="")
        source = BossCdpSource(runner=runner)
        plan = _make_plan_item(keyword="Python", city="北京", list_output_path=self.list_path)
        outcome = source.fetch_list(plan)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_list_input_hash_drift(self):
        runner = _MockRunner(
            returncode=0,
            jobs_payload=[{"job_id": "j1"}],
            jobs_path=self.list_path,
        )
        source = BossCdpSource(runner=runner)
        plan = _make_plan_item(keyword="Python", city="北京", list_output_path=self.list_path,
                               input_hash="wrong-hash")
        outcome = source.fetch_list(plan)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_input_drift")

    def test_detail_success_with_mock_runner(self):
        captured = {}

        def runner(command, timeout):
            captured["command"] = list(command)
            input_path = command[command.index("--input") + 1]
            captured["input_payload"] = json.loads(
                Path(input_path).read_text(encoding="utf-8")
            )
            Path(self.detail_path).write_text(
                json.dumps([{"title": "后端工程师", "jd": "负责..."}], ensure_ascii=False),
                encoding="utf-8",
            )
            return 0, ""

        source = BossCdpSource(runner=runner)
        outcome = source.fetch_detail(
            {"job_id": "j1", "source_url": "https://www.zhipin.com/job/123"},
            detail_output_path=self.detail_path,
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.detail.get("title"), "后端工程师")
        cmd = captured["command"]
        self.assertIn("--input", cmd)
        self.assertNotIn("--detail-url", cmd)
        self.assertIn("--detail-output", cmd)
        self.assertEqual(captured["input_payload"]["jobs"][0]["job_link"],
                         "https://www.zhipin.com/job/123")

    def test_detail_failure_nonzero_returncode(self):
        runner = _MockRunner(returncode=1, captured="detail error", detail_path=self.detail_path)
        source = BossCdpSource(runner=runner)
        outcome = source.fetch_detail(
            {"job_id": "j1", "source_url": "https://www.zhipin.com/job/123"},
            detail_output_path=self.detail_path,
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_blocked")

    def test_command_includes_source_filters(self):
        runner = _MockRunner(returncode=0, jobs_payload=[], jobs_path=self.list_path)
        source = BossCdpSource(runner=runner)
        plan = _make_plan_item(
            keyword="Python", city="北京", list_output_path=self.list_path,
            source_filters={"scale": "100-499", "salary": "20-30K"},
        )
        source.fetch_list(plan)
        cmd = runner.calls[0]["command"]
        self.assertIn("--scale", cmd)
        self.assertIn("100-499", cmd)
        self.assertIn("--salary", cmd)
        self.assertIn("20-30K", cmd)

    # ------------------------------------------------------------------
    # T133: BOSS scraper returns encrypt_job_id/job_link/boss_name fields,
    # but fetch_detail expects job_id/source_url/company. fetch_list must
    # normalize BOSS-specific field names to the unified JobSource interface
    # so downstream detail fetch can succeed.
    # ------------------------------------------------------------------

    def test_list_normalizes_boss_field_names(self):
        """RED (T133): BOSS scraper returns encrypt_job_id/job_link/boss_name;
        fetch_list must normalize to job_id/source_url/company so fetch_detail
        can read them without field-name mismatch."""
        runner = _MockRunner(
            returncode=0,
            jobs_payload=[{
                "title": "Java 后端",
                "salary": "20-30K",
                "boss_name": "某公司",
                "encrypt_job_id": "abc123",
                "job_link": "https://www.zhipin.com/job_detail/abc123.html",
                "location": "北京·朝阳·望京",
                "tags": "5-10年 | 本科",
            }],
            jobs_path=self.list_path,
        )
        source = BossCdpSource(runner=runner)
        plan = _make_plan_item(keyword="Java", city="北京", list_output_path=self.list_path)
        outcome = source.fetch_list(plan)
        self.assertTrue(outcome.ok, f"fetch_list should succeed: {outcome.failed_code}")
        self.assertEqual(len(outcome.jobs), 1)
        job = outcome.jobs[0]
        # Normalized fields must be present and non-empty
        self.assertEqual(job.get("job_id"), "abc123",
                         "encrypt_job_id must be normalized to job_id")
        self.assertEqual(job.get("source_url"), "https://www.zhipin.com/job_detail/abc123.html",
                         "job_link must be normalized to source_url")
        self.assertEqual(job.get("company"), "某公司",
                         "boss_name must be normalized to company")

    def test_normalized_jobs_can_fetch_detail(self):
        """RED (T133): After normalization, fetch_detail should accept the
        job dict without failing on missing source_url/job_id."""
        runner = _MockRunner(
            returncode=0,
            jobs_payload=[{
                "title": "Java 后端",
                "boss_name": "某公司",
                "encrypt_job_id": "abc123",
                "job_link": "https://www.zhipin.com/job_detail/abc123.html",
            }],
            jobs_path=self.list_path,
        )
        source = BossCdpSource(runner=runner)
        plan = _make_plan_item(keyword="Java", city="北京", list_output_path=self.list_path)
        list_outcome = source.fetch_list(plan)
        self.assertTrue(list_outcome.ok)
        job = list_outcome.jobs[0]
        # Now fetch_detail should NOT fail with source_invalid_output
        detail_runner = _MockRunner(
            returncode=0,
            detail_payload={"title": "Java 后端", "jd": "完整 JD"},
            detail_path=self.detail_path,
        )
        detail_source = BossCdpSource(runner=detail_runner)
        detail_outcome = detail_source.fetch_detail(job, detail_output_path=self.detail_path)
        self.assertTrue(detail_outcome.ok,
                        f"fetch_detail should succeed after normalization: "
                        f"{detail_outcome.failed_code} {detail_outcome.safe_log}")


# ---------------------------------------------------------------------------
# T039/T040: build_snapshot completeness, missing_fields, source_status
# ---------------------------------------------------------------------------


class JobSnapshotBuildTests(unittest.TestCase):
    """T039/T040: build_snapshot completeness, missing_fields, source_status."""

    def _job(self, **overrides):
        base = {"job_id": "j1", "title": "后端", "company": "A", "salary": "20k",
                "location": "北京", "tags": "Python", "jd": "负责..."}
        base.update(overrides)
        return base

    def test_complete_snapshot(self):
        from webui.discovery import build_snapshot
        snap = build_snapshot(self._job(), {"jd": "完整描述"})
        self.assertEqual(snap["completeness"], "complete")
        self.assertEqual(snap["missing_fields"], [])
        self.assertEqual(snap["source_status"], "active")

    def test_partial_snapshot_missing_jd(self):
        from webui.discovery import build_snapshot
        job = self._job(jd="")
        snap = build_snapshot(job, {})
        self.assertEqual(snap["completeness"], "partial")
        self.assertIn("jd", snap["missing_fields"])

    def test_unavailable_only_title(self):
        from webui.discovery import build_snapshot
        job = {"job_id": "j1", "title": "后端"}
        snap = build_snapshot(job, {})
        self.assertEqual(snap["completeness"], "unavailable")
        self.assertEqual(snap["source_status"], "unreachable")
        self.assertIn("company", snap["missing_fields"])
        self.assertIn("jd", snap["missing_fields"])

    def test_expired_snapshot(self):
        from webui.discovery import build_snapshot
        snap = build_snapshot(self._job(), {"expired": True})
        self.assertEqual(snap["completeness"], "expired")
        self.assertEqual(snap["source_status"], "closed")

    def test_content_hash_stable(self):
        from webui.discovery import build_snapshot
        snap1 = build_snapshot(self._job(), {})
        snap2 = build_snapshot(self._job(), {})
        self.assertEqual(snap1["content_hash"], snap2["content_hash"])

    def test_content_hash_changes_with_jd(self):
        from webui.discovery import build_snapshot
        snap1 = build_snapshot(self._job(jd="jd1"), {})
        snap2 = build_snapshot(self._job(jd="jd2"), {})
        self.assertNotEqual(snap1["content_hash"], snap2["content_hash"])

    def test_only_title_does_not_become_high_match(self):
        """Spec: only-title snapshot cannot route to high_match in assessment."""
        from webui.discovery import build_snapshot, assess_job_direction
        snap = build_snapshot({"job_id": "j1", "title": "后端"}, {})
        direction = {"id": "d1", "name": "后端", "evidence_refs": [], "analysis_evidence_ids": []}
        ai_proposal = {
            "dimensions": {dim: {"score": 95, "candidate_evidence_refs": [], "job_evidence_refs": []} for dim in ("capability", "experience", "environment", "stability")},
            "match_score": 95,
            "confidence": 90,
            "gaps": [],
            "proposed_band": "high",
        }
        result = assess_job_direction(snap, direction, ai_proposal, hard_constraints={})
        self.assertNotEqual(result["category"], "high_match")
        self.assertEqual(result["category"], "needs_review")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
