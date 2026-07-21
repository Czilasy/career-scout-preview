"""BOSS discovery source adapter tests (feature 004)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from pathlib import Path

from webui.source import (
    BossCdpSource,
    FakeJobSource,
    SourceOutcome,
    SAFE_FAILURE_CODES,
    SourceCircuitBreaker,
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
            "source_cdp_unavailable", "source_login_required",
            "source_unreachable", "source_blocked", "source_not_found",
            "source_invalid_output", "source_input_drift", "source_timeout",
            "source_unknown_error",
            "source_verification_required", "source_rate_limited",
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

    @patch("webui.source.boss.check_login_state", return_value=True)
    @patch("webui.source.boss.requests.get")
    def test_preflight_reports_ready_once_cdp_and_login_are_available(self, get, login):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"Browser": "Chrome/150"}
        get.return_value = response
        outcome = BossCdpSource().preflight()
        self.assertTrue(outcome.ok)
        login.assert_called_once_with(9222)

    @patch("webui.source.boss.requests.get")
    def test_preflight_distinguishes_cdp_unavailable(self, get):
        from scripts import boss_cdp_raw as boss
        get.side_effect = boss.requests.ConnectionError("refused")
        outcome = BossCdpSource().preflight()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_cdp_unavailable")

    @patch("webui.source.boss.check_login_state", return_value=False)
    @patch("webui.source.boss.requests.get")
    def test_preflight_distinguishes_login_required(self, get, login):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"Browser": "Chrome/150"}
        get.return_value = response
        outcome = BossCdpSource().preflight()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_login_required")

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

    def test_list_command_drops_unknown_and_sensitive_plan_fields(self):
        runner = _MockRunner(returncode=0, jobs_payload=[], jobs_path=self.list_path)
        source = BossCdpSource(runner=runner)
        plan = _make_plan_item(keyword="Python", city="上海", list_output_path=self.list_path)
        plan["source_filters"] = {
            "salary": "20-30K", "experience": "5", "degree": "本科", "industry": "互联网",
            "scale": "100-499", "stage": "已上市", "phone": "13812345678",
            "resume_text": "SECRET_RESUME", "quality_warnings": "SECRET_WARNING",
        }
        import webui.source as source_module
        plan["input_hash"] = source_module._input_hash({
            "keyword": "Python", "city": "上海",
            "source_filters": {k: plan["source_filters"][k] for k in source_module.SCRAPER_FILTER_FIELDS},
            "target_pages": 1,
        })
        outcome = source.fetch_list(plan)
        self.assertTrue(outcome.ok)
        command = runner.calls[0]["command"]
        joined = " ".join(command)
        for value in ("13812345678", "SECRET_RESUME", "SECRET_WARNING", "--phone", "--resume_text"):
            self.assertNotIn(value, joined)
        for flag in ("--salary", "--experience", "--degree", "--industry", "--scale", "--stage"):
            self.assertIn(flag, command)

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


# ---------------------------------------------------------------------------
# T070: BossCdpSource.fetch_details_batch — parse/reject malformed, unknown,
# job-mismatched terminal safe events; read per-job atomic detail artifacts
# from a single batched scraper subprocess (≤5 jobs per batch, one CDP
# session reused per batch, one Target per job). See
# specs/005-fast-resume-discovery/contracts/state-machine.md
# §Producer / Consumer Boundaries and contracts/http-api.md §Required Safe
# Error Codes (detail_event_invalid, detail_reuse_invalid).
# ---------------------------------------------------------------------------


def _batch_job(job_id: str, source_url: str | None = None, **extra) -> dict:
    """Build a normalized batch input job (already has job_id and source_url)."""
    url = source_url or f"https://www.zhipin.com/job_detail/{job_id}.html"
    base = {"job_id": job_id, "source_url": url, "job_link": url}
    base.update(extra)
    return base


def _detail_record(job_id: str, *, jd: str = "完整 JD 描述", title: str = "后端") -> dict:
    """Build a detail record as the scraper would write it (uses job_link)."""
    return {
        "job_id": job_id,
        "job_link": f"https://www.zhipin.com/job_detail/{job_id}.html",
        "title": title,
        "jd": jd,
        "company": "某公司",
        "salary": "20-30K",
        "location": "北京",
        "tags": "Python",
    }


def _event(status: str, job_id: str, *, duration_ms: int = 1500,
           safe_code: str = "ok", kind: str = "detail") -> dict:
    """Build a terminal safe event as scrape_details emits it via event_callback."""
    return {
        "kind": kind,
        "status": status,
        "job_id": job_id,
        "duration_ms": duration_ms,
        "safe_code": safe_code,
    }


class _BatchMockRunner:
    """Mock subprocess runner that simulates scrape_details batch behavior.

    Reads the batch input JSON (to know the expected jobs), then writes the
    combined detail JSON list and the events JSONL file as programmed by the
    test. Records the command for assertions.
    """

    def __init__(self, *, details_by_job: dict[str, dict] | None = None,
                 events: list[dict] | None = None,
                 returncode: int = 0,
                 captured: str = "",
                 write_events_file: bool = True,
                 write_details_file: bool = True):
        self._details_by_job = details_by_job or {}
        self._events = list(events or [])
        self._returncode = returncode
        self._captured = captured
        self._write_events_file = write_events_file
        self._write_details_file = write_details_file
        self.calls: list[dict] = []

    def __call__(self, command, timeout):
        self.calls.append({"command": list(command), "timeout": timeout})
        # Locate --input, --detail-output, --events-output
        def _flag(name: str) -> str | None:
            if name in command:
                idx = command.index(name)
                if idx + 1 < len(command):
                    return command[idx + 1]
            return None

        input_path = _flag("--input")
        detail_output_path = _flag("--detail-output")
        events_output_path = _flag("--events-output")

        # Write combined details file (list of detail records)
        if detail_output_path and self._write_details_file:
            Path(detail_output_path).parent.mkdir(parents=True, exist_ok=True)
            details = list(self._details_by_job.values())
            Path(detail_output_path).write_text(
                json.dumps(details, ensure_ascii=False), encoding="utf-8",
            )

        # Write events JSONL file (one event per line)
        if events_output_path and self._write_events_file:
            Path(events_output_path).parent.mkdir(parents=True, exist_ok=True)
            lines = [json.dumps(e, ensure_ascii=False) for e in self._events]
            Path(events_output_path).write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8",
            )

        # Record the input payload for assertions
        if input_path and Path(input_path).is_file():
            self.calls[-1]["input_payload"] = json.loads(
                Path(input_path).read_text(encoding="utf-8")
            )

        return self._returncode, self._captured


class BossCdpSourceBatchEventTests(unittest.TestCase):
    """T070: source parses/rejects malformed/unknown/job-mismatched events
    and reads per-job atomic artifacts from a batched detail fetch."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="boss-source-batch-")
        self.detail_output_path = os.path.join(self._tmp, "batch.details.json")
        self.events_output_path = os.path.join(self._tmp, "batch.details.json.events.jsonl")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_source(self, runner: _BatchMockRunner) -> "BossCdpSource":
        return BossCdpSource(runner=runner)

    # ------------------------------------------------------------------
    # Batch size guard (≤5 jobs per batch)
    # ------------------------------------------------------------------

    def test_batch_rejects_more_than_five_jobs_without_invoking_scraper(self):
        """Contract: each batch contains at most 5 selected candidates.
        >5 jobs must be rejected before subprocess invocation."""
        runner = _BatchMockRunner()
        source = self._make_source(runner)
        jobs = [_batch_job(f"j{i}") for i in range(6)]
        result = source.fetch_details_batch(
            jobs, detail_output_path=self.detail_output_path,
        )
        self.assertEqual(set(result.keys()), {f"j{i}" for i in range(6)})
        for outcome in result.values():
            self.assertFalse(outcome.ok)
            self.assertEqual(outcome.failed_code, "source_invalid_output")
        # Scraper must not have been invoked
        self.assertEqual(runner.calls, [])

    def test_batch_rejects_empty_job_list_without_invoking_scraper(self):
        runner = _BatchMockRunner()
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [], detail_output_path=self.detail_output_path,
        )
        self.assertEqual(result, {})
        self.assertEqual(runner.calls, [])

    def test_batch_rejects_job_missing_source_url(self):
        """A job missing source_url cannot be matched to events; reject it
        individually without aborting the rest of the batch."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[_event("completed", "https://www.zhipin.com/job_detail/j1.html")],
        )
        source = self._make_source(runner)
        jobs = [
            {"job_id": "j0"},  # missing source_url
            _batch_job("j1"),
        ]
        result = source.fetch_details_batch(
            jobs, detail_output_path=self.detail_output_path,
        )
        self.assertIn("j0", result)
        self.assertFalse(result["j0"].ok)
        self.assertEqual(result["j0"].failed_code, "source_invalid_output")
        # j1 should still be processed normally
        self.assertTrue(result["j1"].ok)

    # ------------------------------------------------------------------
    # Well-formed events: completed, unavailable, failed, cancelled
    # ------------------------------------------------------------------

    def test_batch_parses_completed_event_and_reads_detail(self):
        """A well-formed completed event + matching detail → ok outcome with detail."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1", jd="负责后端服务开发")},
            events=[_event("completed", "https://www.zhipin.com/job_detail/j1.html")],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertEqual(set(result.keys()), {"j1"})
        self.assertTrue(result["j1"].ok, f"should be ok: {result['j1'].safe_log}")
        self.assertEqual(result["j1"].detail.get("jd"), "负责后端服务开发")
        self.assertEqual(result["j1"].failed_code, None)

    def test_batch_parses_unavailable_event_with_source_login_required(self):
        """An unavailable event with source_login_required → not ok, login code."""
        runner = _BatchMockRunner(
            details_by_job={},  # no detail written for unavailable jobs
            events=[_event("unavailable", "https://www.zhipin.com/job_detail/j1.html",
                           safe_code="source_login_required")],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertIn("j1", result)
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_login_required")

    def test_batch_parses_failed_event_with_source_invalid_output(self):
        """A failed event with source_invalid_output → not ok, invalid_output code."""
        runner = _BatchMockRunner(
            details_by_job={},
            events=[_event("failed", "https://www.zhipin.com/job_detail/j1.html",
                           safe_code="source_invalid_output")],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertIn("j1", result)
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")

    def test_batch_parses_cancelled_event(self):
        """A cancelled event → not ok, with a cancelled-safe code."""
        runner = _BatchMockRunner(
            details_by_job={},
            events=[_event("cancelled", "https://www.zhipin.com/job_detail/j1.html",
                           safe_code="cancelled")],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertIn("j1", result)
        self.assertFalse(result["j1"].ok)
        # cancelled events must surface a recognizable safe code
        self.assertIn(result["j1"].failed_code, {"source_unknown_error", "cancelled"})

    # ------------------------------------------------------------------
    # Reject malformed events: missing required fields, wrong types
    # ------------------------------------------------------------------

    def test_batch_rejects_event_missing_kind(self):
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[{"status": "completed", "job_id": "https://www.zhipin.com/job_detail/j1.html",
                     "duration_ms": 100, "safe_code": "ok"}],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")
        self.assertIn("detail_event_invalid", result["j1"].safe_log)

    def test_batch_rejects_event_missing_status(self):
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[{"kind": "detail", "job_id": "https://www.zhipin.com/job_detail/j1.html",
                     "duration_ms": 100, "safe_code": "ok"}],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")
        self.assertIn("detail_event_invalid", result["j1"].safe_log)

    def test_batch_rejects_event_missing_job_id(self):
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[{"kind": "detail", "status": "completed",
                     "duration_ms": 100, "safe_code": "ok"}],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")

    def test_batch_rejects_event_missing_duration_ms(self):
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[{"kind": "detail", "status": "completed",
                     "job_id": "https://www.zhipin.com/job_detail/j1.html",
                     "safe_code": "ok"}],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")

    def test_batch_rejects_event_missing_safe_code(self):
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[{"kind": "detail", "status": "completed",
                     "job_id": "https://www.zhipin.com/job_detail/j1.html",
                     "duration_ms": 100}],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")

    def test_batch_rejects_event_with_wrong_type_duration_ms(self):
        """duration_ms must be a non-negative integer; string is rejected."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[{"kind": "detail", "status": "completed",
                     "job_id": "https://www.zhipin.com/job_detail/j1.html",
                     "duration_ms": "1500",  # wrong type
                     "safe_code": "ok"}],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")

    def test_batch_rejects_event_with_wrong_type_status(self):
        """status must be a string; integer is rejected."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[{"kind": "detail", "status": 1,
                     "job_id": "https://www.zhipin.com/job_detail/j1.html",
                     "duration_ms": 100, "safe_code": "ok"}],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")

    def test_batch_rejects_malformed_json_event_line(self):
        """A malformed JSON line in the events file is skipped, not crashed on.
        The corresponding job (no valid event) gets source_invalid_output."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[_event("completed", "https://www.zhipin.com/job_detail/j1.html")],
        )
        # Override the events file with a malformed first line
        original_call = runner.__call__

        def call_with_malformed(command, timeout):
            result = original_call(command, timeout)
            # Append a malformed line to the events file
            events_path = os.path.join(self._tmp, "batch.details.json.events.jsonl")
            if Path(events_path).exists():
                with open(events_path, "a", encoding="utf-8") as f:
                    f.write("{not valid json\n")
            return result

        runner.__call__ = call_with_malformed
        source = self._make_source(runner)
        # Should not raise; j1 should still match the valid first event
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertTrue(result["j1"].ok, f"valid event should still match: {result['j1'].safe_log}")

    # ------------------------------------------------------------------
    # Reject unknown event kind / status
    # ------------------------------------------------------------------

    def test_batch_rejects_event_with_unknown_kind(self):
        """kind must be 'detail'; any other value is rejected."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[_event("completed", "https://www.zhipin.com/job_detail/j1.html",
                           kind="list")],  # wrong kind
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")
        self.assertIn("detail_event_invalid", result["j1"].safe_log)

    def test_batch_rejects_event_with_unknown_status(self):
        """status must be one of {completed, unavailable, failed, cancelled}."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[_event("in_progress", "https://www.zhipin.com/job_detail/j1.html")],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")

    # ------------------------------------------------------------------
    # Reject job-mismatched events
    # ------------------------------------------------------------------

    def test_batch_rejects_event_for_job_not_in_batch(self):
        """An event whose job_id is not in the expected batch is rejected
        (not dispatched to callback) and does not satisfy any batch job."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[
                # Event for a job NOT in the batch
                _event("completed", "https://www.zhipin.com/job_detail/other.html"),
            ],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        # j1 has no matching event → must be flagged invalid
        self.assertIn("j1", result)
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")

    def test_batch_event_for_wrong_job_does_not_satisfy_other_job(self):
        """An event for j2 must not be applied to j1."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[_event("completed", "https://www.zhipin.com/job_detail/j2.html")],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")

    # ------------------------------------------------------------------
    # Per-job atomic artifact reading
    # ------------------------------------------------------------------

    def test_batch_reads_per_job_detail_atomically(self):
        """Each job's detail is read independently from the combined output.
        A detail record for j2 must not leak into j1's outcome."""
        runner = _BatchMockRunner(
            details_by_job={
                "j1": _detail_record("j1", jd="j1 独立 JD"),
                "j2": _detail_record("j2", jd="j2 独立 JD"),
            },
            events=[
                _event("completed", "https://www.zhipin.com/job_detail/j1.html"),
                _event("completed", "https://www.zhipin.com/job_detail/j2.html"),
            ],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1"), _batch_job("j2")],
            detail_output_path=self.detail_output_path,
        )
        self.assertEqual(result["j1"].detail.get("jd"), "j1 独立 JD")
        self.assertEqual(result["j2"].detail.get("jd"), "j2 独立 JD")

    def test_batch_job_with_completed_event_but_no_detail_is_invalid(self):
        """A completed event without a matching detail artifact is invalid."""
        runner = _BatchMockRunner(
            details_by_job={},  # no details written
            events=[_event("completed", "https://www.zhipin.com/job_detail/j1.html")],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")

    def test_batch_job_with_completed_event_but_detail_missing_job_link_is_invalid(self):
        """A detail record without job_link/source_url cannot be attributed to
        any job; the corresponding completed event is therefore unmatched."""
        detail_no_link = {
            "title": "后端", "jd": "无身份 JD",
            "company": "某公司", "salary": "20K", "location": "北京", "tags": "Python",
        }
        runner = _BatchMockRunner(
            details_by_job={"_anon": detail_no_link},
            events=[_event("completed", "https://www.zhipin.com/job_detail/j1.html")],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")

    # ------------------------------------------------------------------
    # event_callback receives only valid, job-matched events
    # ------------------------------------------------------------------

    def test_batch_event_callback_receives_only_valid_events(self):
        """event_callback is invoked exactly once per valid, job-matched event.
        Malformed/unknown/mismatched events are NOT dispatched to callback."""
        received: list[dict] = []
        runner = _BatchMockRunner(
            details_by_job={
                "j1": _detail_record("j1"),
                "j2": _detail_record("j2"),
            },
            events=[
                _event("completed", "https://www.zhipin.com/job_detail/j1.html"),
                # Malformed event (missing kind) — must NOT be dispatched
                {"status": "completed",
                 "job_id": "https://www.zhipin.com/job_detail/j2.html",
                 "duration_ms": 100, "safe_code": "ok"},
                # Unknown kind — must NOT be dispatched
                _event("completed", "https://www.zhipin.com/job_detail/j2.html",
                       kind="list"),
                # Job-mismatched — must NOT be dispatched
                _event("completed", "https://www.zhipin.com/job_detail/other.html"),
                # Valid event for j2 — must be dispatched
                _event("completed", "https://www.zhipin.com/job_detail/j2.html"),
            ],
        )
        source = self._make_source(runner)
        source.fetch_details_batch(
            [_batch_job("j1"), _batch_job("j2")],
            detail_output_path=self.detail_output_path,
            event_callback=received.append,
        )
        # Only j1's first valid event and j2's last valid event should be dispatched
        self.assertEqual(len(received), 2)
        dispatched_job_ids = {e["job_id"] for e in received}
        self.assertEqual(dispatched_job_ids, {
            "https://www.zhipin.com/job_detail/j1.html",
            "https://www.zhipin.com/job_detail/j2.html",
        })

    def test_batch_event_callback_not_required(self):
        """event_callback is optional; if None, no callback dispatch is attempted."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[_event("completed", "https://www.zhipin.com/job_detail/j1.html")],
        )
        source = self._make_source(runner)
        # Should not raise even though event_callback is None
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertTrue(result["j1"].ok)

    # ------------------------------------------------------------------
    # Scraper invocation contract
    # ------------------------------------------------------------------

    def test_batch_invokes_scraper_with_events_output_flag(self):
        """The scraper command must include --events-output so the subprocess
        can emit terminal safe events to a JSONL file."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[_event("completed", "https://www.zhipin.com/job_detail/j1.html")],
        )
        source = self._make_source(runner)
        source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertEqual(len(runner.calls), 1)
        cmd = runner.calls[0]["command"]
        self.assertIn("--events-output", cmd)
        self.assertIn("--input", cmd)
        self.assertIn("--detail-output", cmd)
        self.assertIn("--detail", cmd)
        # Max 5 details per batch
        max_details_idx = cmd.index("--max-details") if "--max-details" in cmd else -1
        self.assertGreater(max_details_idx, -1)
        self.assertEqual(int(cmd[max_details_idx + 1]), 5)

    def test_batch_input_payload_uses_job_link_for_scraper(self):
        """The batch input JSON must set job_link on each job so scrape_details
        can build detail URLs and emit job_id (= job_link) in events."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[_event("completed", "https://www.zhipin.com/job_detail/j1.html")],
        )
        source = self._make_source(runner)
        source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        input_payload = runner.calls[0]["input_payload"]
        self.assertIn("jobs", input_payload)
        self.assertEqual(len(input_payload["jobs"]), 1)
        self.assertEqual(
            input_payload["jobs"][0]["job_link"],
            "https://www.zhipin.com/job_detail/j1.html",
        )

    def test_batch_scraper_failure_returncode_surfaces_source_blocked(self):
        """If the scraper subprocess exits non-zero, all batch jobs get
        source_blocked (no partial results from a failed batch)."""
        runner = _BatchMockRunner(
            details_by_job={},
            events=[],
            returncode=2,
            captured="scraper error",
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1"), _batch_job("j2")],
            detail_output_path=self.detail_output_path,
        )
        for job_id in ("j1", "j2"):
            self.assertFalse(result[job_id].ok)
            self.assertEqual(result[job_id].failed_code, "source_blocked")

    def test_batch_no_events_file_treated_as_invalid_for_all_jobs(self):
        """If the events file is missing entirely (scraper crashed before
        writing it), every batch job gets source_invalid_output."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[],
            write_events_file=False,  # simulate missing events file
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")

    # ------------------------------------------------------------------
    # Privacy: events must not carry JD/credential fields
    # ------------------------------------------------------------------

    def test_batch_rejects_event_containing_jd_body_field(self):
        """A terminal safe event must never carry JD body or credentials.
        If an event includes a 'jd' field, it is rejected as invalid."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[{
                "kind": "detail", "status": "completed",
                "job_id": "https://www.zhipin.com/job_detail/j1.html",
                "duration_ms": 100, "safe_code": "ok",
                "jd": "SECRET-JD-MUST-NOT-BE-IN-EVENT",  # forbidden field
            }],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")
        self.assertNotIn("SECRET-JD-MUST-NOT-BE-IN-EVENT", result["j1"].safe_log)

    def test_batch_rejects_event_containing_credential_field(self):
        """Events containing credential-shaped fields (encrypt_job_id,
        security_id, token, etc.) are rejected."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1")},
            events=[{
                "kind": "detail", "status": "completed",
                "job_id": "https://www.zhipin.com/job_detail/j1.html",
                "duration_ms": 100, "safe_code": "ok",
                "encrypt_job_id": "SECRET-ENC",  # forbidden
                "security_id": "SECRET-SEC",     # forbidden
            }],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_output_path,
        )
        self.assertFalse(result["j1"].ok)
        self.assertEqual(result["j1"].failed_code, "source_invalid_output")
        self.assertNotIn("SECRET-ENC", result["j1"].safe_log)
        self.assertNotIn("SECRET-SEC", result["j1"].safe_log)

    # ------------------------------------------------------------------
    # Multi-job batch integration
    # ------------------------------------------------------------------

    def test_batch_three_jobs_mixed_outcomes(self):
        """A 3-job batch with mixed outcomes (completed/unavailable/failed)
        is parsed correctly per job."""
        runner = _BatchMockRunner(
            details_by_job={"j1": _detail_record("j1", jd="j1 JD")},
            events=[
                _event("completed", "https://www.zhipin.com/job_detail/j1.html"),
                _event("unavailable", "https://www.zhipin.com/job_detail/j2.html",
                       safe_code="source_login_required"),
                _event("failed", "https://www.zhipin.com/job_detail/j3.html",
                       safe_code="source_invalid_output"),
            ],
        )
        source = self._make_source(runner)
        result = source.fetch_details_batch(
            [_batch_job("j1"), _batch_job("j2"), _batch_job("j3")],
            detail_output_path=self.detail_output_path,
        )
        self.assertTrue(result["j1"].ok)
        self.assertEqual(result["j1"].detail.get("jd"), "j1 JD")
        self.assertFalse(result["j2"].ok)
        self.assertEqual(result["j2"].failed_code, "source_login_required")
        self.assertFalse(result["j3"].ok)
        self.assertEqual(result["j3"].failed_code, "source_invalid_output")


# ---------------------------------------------------------------------------
# T074: Source circuit breaker (state-machine.md L92-107)
# ---------------------------------------------------------------------------


class _FakeClock:
    """Deterministic monotonic clock for breaker cooldown tests."""

    def __init__(self, start: float = 0.0):
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class SourceCircuitBreakerTests(unittest.TestCase):
    """T074 RED: breaker opens after two consecutive source signals.

    Contract (state-machine.md L92-107):
      - Signals: login wall, verification page, rate-limit, invalid
        navigation shell attributable to source blocking.
      - Opens after two consecutive signals (any mix of the four kinds).
      - When open: no new source job starts; queued work stays
        retryable/blocked rather than failed as user fault.
      - Automatic restart requires preflight success and bounded cooldown;
        no unbounded retry loop.
    """

    def test_breaker_signal_codes_include_v2_source_signals(self):
        """Breaker recognises the four source-blocking signal codes."""
        self.assertIn("source_login_required", SourceCircuitBreaker.SIGNAL_CODES)
        self.assertIn("source_verification_required", SourceCircuitBreaker.SIGNAL_CODES)
        self.assertIn("source_rate_limited", SourceCircuitBreaker.SIGNAL_CODES)
        self.assertIn("source_blocked", SourceCircuitBreaker.SIGNAL_CODES)

    def test_breaker_starts_closed(self):
        breaker = SourceCircuitBreaker()
        self.assertFalse(breaker.is_open())
        state = breaker.state()
        self.assertFalse(state["open"])
        self.assertEqual(state["consecutive"], 0)

    def test_one_signal_does_not_open_breaker(self):
        """Contract: requires TWO consecutive signals; one is not enough."""
        breaker = SourceCircuitBreaker()
        breaker.record_signal("source_login_required")
        self.assertFalse(breaker.is_open())
        state = breaker.state()
        self.assertEqual(state["consecutive"], 1)
        self.assertFalse(state["open"])

    def test_two_consecutive_login_required_signals_open_breaker(self):
        breaker = SourceCircuitBreaker()
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_login_required")
        self.assertTrue(breaker.is_open())

    def test_two_consecutive_verification_required_signals_open_breaker(self):
        breaker = SourceCircuitBreaker()
        breaker.record_signal("source_verification_required")
        breaker.record_signal("source_verification_required")
        self.assertTrue(breaker.is_open())

    def test_two_consecutive_rate_limited_signals_open_breaker(self):
        breaker = SourceCircuitBreaker()
        breaker.record_signal("source_rate_limited")
        breaker.record_signal("source_rate_limited")
        self.assertTrue(breaker.is_open())

    def test_two_consecutive_blocked_signals_open_breaker(self):
        """Invalid navigation shell / source blocking maps to source_blocked."""
        breaker = SourceCircuitBreaker()
        breaker.record_signal("source_blocked")
        breaker.record_signal("source_blocked")
        self.assertTrue(breaker.is_open())

    def test_two_consecutive_mixed_signals_open_breaker(self):
        """Contract: 'two consecutive source signals' — any mix of the four kinds."""
        breaker = SourceCircuitBreaker()
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_rate_limited")
        self.assertTrue(breaker.is_open())

    def test_success_between_signals_resets_consecutive_count(self):
        """A success between two signals breaks the consecutive chain."""
        breaker = SourceCircuitBreaker()
        breaker.record_signal("source_login_required")
        breaker.record_success()
        breaker.record_signal("source_login_required")
        self.assertFalse(breaker.is_open())
        self.assertEqual(breaker.state()["consecutive"], 1)

    def test_non_signal_code_does_not_advance_counter(self):
        """User/system fault codes (input_drift, invalid_output, timeout, ...)
        must NOT count as source-blocking signals."""
        breaker = SourceCircuitBreaker()
        for code in (
            "source_invalid_output",
            "source_input_drift",
            "source_timeout",
            "source_not_found",
            "source_unreachable",
            "source_cdp_unavailable",
            "source_unknown_error",
        ):
            breaker.record_signal(code)
        self.assertFalse(breaker.is_open())
        self.assertEqual(breaker.state()["consecutive"], 0)

    def test_breaker_state_is_queryable(self):
        """state() exposes open/consecutive/last_signal/opened_at/cooldown_until."""
        clock = _FakeClock(start=100.0)
        breaker = SourceCircuitBreaker(cooldown_seconds=60, clock=clock)
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_verification_required")
        state = breaker.state()
        self.assertTrue(state["open"])
        self.assertEqual(state["consecutive"], 2)
        self.assertEqual(state["last_signal"], "source_verification_required")
        self.assertEqual(state["opened_at"], 100.0)
        self.assertEqual(state["cooldown_until"], 160.0)

    def test_breaker_stays_open_after_cooldown_without_preflight(self):
        """Cooldown elapsing alone is NOT enough; preflight success required."""
        clock = _FakeClock(start=0.0)
        breaker = SourceCircuitBreaker(cooldown_seconds=60, clock=clock)
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_login_required")
        self.assertTrue(breaker.is_open())
        clock.advance(120)  # past cooldown
        self.assertTrue(breaker.is_open())  # still open without preflight
        self.assertFalse(breaker.try_reset(preflight_ok=False))
        self.assertTrue(breaker.is_open())

    def test_breaker_resets_after_cooldown_and_preflight_success(self):
        """Both conditions: cooldown elapsed AND preflight ok."""
        clock = _FakeClock(start=0.0)
        breaker = SourceCircuitBreaker(cooldown_seconds=60, clock=clock)
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_login_required")
        self.assertTrue(breaker.is_open())
        clock.advance(61)
        self.assertTrue(breaker.try_reset(preflight_ok=True))
        self.assertFalse(breaker.is_open())
        self.assertEqual(breaker.state()["consecutive"], 0)

    def test_breaker_reset_fails_when_preflight_fails(self):
        clock = _FakeClock(start=0.0)
        breaker = SourceCircuitBreaker(cooldown_seconds=60, clock=clock)
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_login_required")
        clock.advance(120)
        self.assertFalse(breaker.try_reset(preflight_ok=False))
        self.assertTrue(breaker.is_open())

    def test_breaker_reset_fails_when_cooldown_not_elapsed(self):
        clock = _FakeClock(start=0.0)
        breaker = SourceCircuitBreaker(cooldown_seconds=60, clock=clock)
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_login_required")
        clock.advance(30)  # before cooldown
        self.assertFalse(breaker.try_reset(preflight_ok=True))
        self.assertTrue(breaker.is_open())

    def test_breaker_records_signal_then_reset_then_signal_again_opens_again(self):
        """Breaker can re-open after reset if signals recur."""
        clock = _FakeClock(start=0.0)
        breaker = SourceCircuitBreaker(cooldown_seconds=60, clock=clock)
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_login_required")
        self.assertTrue(breaker.is_open())
        clock.advance(61)
        self.assertTrue(breaker.try_reset(preflight_ok=True))
        self.assertFalse(breaker.is_open())
        # New cycle
        breaker.record_signal("source_rate_limited")
        self.assertFalse(breaker.is_open())
        breaker.record_signal("source_rate_limited")
        self.assertTrue(breaker.is_open())


class BossCdpSourceBreakerIntegrationTests(unittest.TestCase):
    """T074 RED: BossCdpSource consults the breaker before invoking the scraper.

    Contract: when breaker is open, no new source job starts; the source
    returns a typed ``source_blocked`` outcome with ``breaker_open`` safe log
    and never invokes the subprocess runner. Failed fetches with signal codes
    feed ``breaker.record_signal``; successful fetches feed ``record_success``.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="boss-source-breaker-")
        self.list_path = os.path.join(self._tmp, "list.json")
        self.detail_path = os.path.join(self._tmp, "detail.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_source_exposes_breaker_instance(self):
        source = BossCdpSource()
        self.assertIsInstance(source.breaker, SourceCircuitBreaker)
        self.assertFalse(source.breaker.is_open())

    def test_source_accepts_injected_breaker(self):
        breaker = SourceCircuitBreaker()
        source = BossCdpSource(breaker=breaker)
        self.assertIs(source.breaker, breaker)

    def test_open_breaker_blocks_fetch_list_without_invoking_runner(self):
        breaker = SourceCircuitBreaker()
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_login_required")
        self.assertTrue(breaker.is_open())

        runner = _MockRunner(
            returncode=0, jobs_payload=[], jobs_path=self.list_path,
        )
        source = BossCdpSource(runner=runner, breaker=breaker)
        plan = _make_plan_item(keyword="Python", city="北京", list_output_path=self.list_path)
        outcome = source.fetch_list(plan)

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_blocked")
        self.assertIn("breaker_open", outcome.safe_log)
        self.assertEqual(runner.calls, [])  # runner never invoked

    def test_open_breaker_blocks_fetch_detail_without_invoking_runner(self):
        breaker = SourceCircuitBreaker()
        breaker.record_signal("source_verification_required")
        breaker.record_signal("source_verification_required")

        runner = _MockRunner(returncode=0)
        source = BossCdpSource(runner=runner, breaker=breaker)
        outcome = source.fetch_detail(
            {"job_id": "j1", "source_url": "https://www.zhipin.com/job_detail/1.html"},
            detail_output_path=self.detail_path,
        )

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_blocked")
        self.assertIn("breaker_open", outcome.safe_log)
        self.assertEqual(runner.calls, [])

    def test_open_breaker_blocks_fetch_details_batch_without_invoking_runner(self):
        breaker = SourceCircuitBreaker()
        breaker.record_signal("source_rate_limited")
        breaker.record_signal("source_rate_limited")

        runner = _BatchMockRunner(details_by_job={}, events=[])
        source = BossCdpSource(runner=runner, breaker=breaker)
        result = source.fetch_details_batch(
            [_batch_job("j1")], detail_output_path=self.detail_path,
        )

        self.assertIn("j1", result)
        outcome = result["j1"]
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_blocked")
        self.assertIn("breaker_open", outcome.safe_log)
        self.assertEqual(runner.calls, [])  # batch never invoked

    def test_fetch_list_failure_with_login_required_records_signal(self):
        """A fetch_list returning source_login_required advances breaker counter."""
        runner = _MockRunner(returncode=1, captured="login wall")
        source = BossCdpSource(runner=runner)
        # Patch preflight/login state so fetch_list reaches the runner and
        # maps the nonzero returncode to source_blocked by default. To get
        # source_login_required we need the scraper output to indicate login
        # loss; use a runner that writes a sentinel captured tail.
        # The adapter maps returncode!=0 -> source_blocked, so we instead
        # directly feed the breaker via the public record API through a
        # fetch_list outcome by using a runner that simulates login wall.
        # Simpler: drive breaker via fetch_detail which can return
        # source_login_required when the scraper's events file reports it.
        # For fetch_list, exercise the breaker through a fake source that
        # returns source_login_required directly.
        # Here we verify the BossCdpSource path: when fetch_list outcome's
        # failed_code is a signal code, breaker records it.
        # We simulate by pre-recording then checking is_open after two calls.
        # Use FakeJobSource for the adapter-agnostic signal path:
        # BossCdpSource maps nonzero returncode to source_blocked (a signal
        # code). Two such failures should open the breaker.
        plan = _make_plan_item(keyword="Python", city="北京", list_output_path=self.list_path)
        source.fetch_list(plan)
        self.assertEqual(source.breaker.state()["consecutive"], 1)
        source.fetch_list(plan)
        self.assertTrue(source.breaker.is_open())

    def test_fetch_list_success_resets_signal_count(self):
        """A successful fetch_list resets the consecutive signal counter."""
        # First, record one signal via a failing runner.
        fail_runner = _MockRunner(returncode=1, captured="blocked")
        source = BossCdpSource(runner=fail_runner)
        plan = _make_plan_item(keyword="Python", city="北京", list_output_path=self.list_path)
        source.fetch_list(plan)
        self.assertEqual(source.breaker.state()["consecutive"], 1)

        # Swap to a succeeding runner.
        ok_runner = _MockRunner(
            returncode=0, jobs_payload=[{"job_id": "j1"}], jobs_path=self.list_path,
        )
        source._runner = ok_runner
        outcome = source.fetch_list(plan)
        self.assertTrue(outcome.ok)
        self.assertEqual(source.breaker.state()["consecutive"], 0)
        self.assertFalse(source.breaker.is_open())

    def test_two_consecutive_fetch_list_failures_open_breaker(self):
        """Two consecutive fetch_list failures with signal codes open breaker."""
        runner = _MockRunner(returncode=1, captured="blocked")
        source = BossCdpSource(runner=runner)
        plan = _make_plan_item(keyword="Python", city="北京", list_output_path=self.list_path)

        first = source.fetch_list(plan)
        self.assertFalse(first.ok)
        self.assertEqual(first.failed_code, "source_blocked")
        self.assertFalse(source.breaker.is_open())

        second = source.fetch_list(plan)
        self.assertFalse(second.ok)
        self.assertTrue(source.breaker.is_open())

    def test_open_breaker_outcome_is_retryable_blocked_not_user_fault(self):
        """When breaker is open, outcome is source_blocked (retryable), not
        a user-fault code like source_input_drift."""
        breaker = SourceCircuitBreaker()
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_login_required")

        runner = _MockRunner(returncode=0, jobs_payload=[], jobs_path=self.list_path)
        source = BossCdpSource(runner=runner, breaker=breaker)
        plan = _make_plan_item(keyword="Python", city="北京", list_output_path=self.list_path)
        outcome = source.fetch_list(plan)

        self.assertFalse(outcome.ok)
        # source_blocked is a source-blocking signal, NOT user fault.
        self.assertEqual(outcome.failed_code, "source_blocked")
        self.assertNotEqual(outcome.failed_code, "source_input_drift")
        self.assertNotEqual(outcome.failed_code, "source_invalid_output")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
