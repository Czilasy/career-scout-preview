"""Tests for e2e_real_boss prerequisite checks (T091/T092 enhancement).

Covers the tri-state boss_login (true/false/unknown) and offline Cookies
diagnosis when CDP is down. Does NOT hit any real service — all external
access is mocked or pointed at temp dirs.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
E2E_PATH = PROJECT_ROOT / "tests" / "fixtures" / "discovery" / "e2e_real_boss.py"

# Load the e2e module by path (it lives under tests/fixtures, not a package).
_spec = importlib.util.spec_from_file_location("e2e_real_boss", E2E_PATH)
e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e2e)


def _make_cookie_file(profile_dir: Path, *, size: int = 28672, mtime: datetime | None = None):
    """Create a fake Chrome Cookies file inside a profile dir."""
    network_dir = profile_dir / "Default" / "Network"
    network_dir.mkdir(parents=True, exist_ok=True)
    cookie_file = network_dir / "Cookies"
    cookie_file.write_bytes(b"\x00" * size)
    if mtime is not None:
        ts = mtime.timestamp()
        os.utime(cookie_file, (ts, ts))
    return cookie_file


class CheckPrerequisitesTriStateTests(unittest.TestCase):
    """boss_login must be tri-state: true / false / unknown."""

    def test_cdp_down_returns_boss_login_unknown(self):
        """When CDP is unreachable, boss_login must be 'unknown' (not False).

        Previously the code returned False, which semantically means
        'confirmed not logged in'. The correct value is 'unknown' —
        we cannot verify login state without CDP.
        """
        with mock.patch("requests.get", side_effect=ConnectionError("refused")):
            result = e2e._check_prerequisites()
        self.assertFalse(result["cdp"])
        self.assertEqual(result["boss_login"], "unknown",
                         "CDP down must yield boss_login='unknown', not False")
        # Must include offline diagnosis info
        self.assertIn("boss_login_note", result,
                      "Offline Cookies diagnosis must be provided when CDP is down")

    def test_cdp_up_with_zhipin_tab_returns_true(self):
        """When CDP is up and probe confirms login, boss_login is True.
        Tab list is no longer used as the login signal — we always probe."""
        fake_version_resp = mock.Mock(status_code=200)
        fake_version_resp.json.return_value = {"Browser": "Chrome"}
        fake_tabs_resp = mock.Mock(status_code=200)
        fake_tabs_resp.json.return_value = [
            {"url": "https://www.zhipin.com/web/geek/job-recommend"}
        ]
        with mock.patch("requests.get",
                        side_effect=[fake_version_resp, fake_tabs_resp]):
            with mock.patch.object(e2e, "_probe_login_via_cdp", return_value=True):
                result = e2e._check_prerequisites()
        self.assertTrue(result["cdp"])
        self.assertTrue(result["boss_login"])
        self.assertNotIn("boss_login_note", result)

    def test_cdp_up_probe_not_logged_in_returns_false(self):
        """When CDP is up and probe confirms NOT logged in, boss_login is False."""
        fake_version_resp = mock.Mock(status_code=200)
        fake_version_resp.json.return_value = {"Browser": "Chrome"}
        fake_tabs_resp = mock.Mock(status_code=200)
        fake_tabs_resp.json.return_value = [
            {"url": "https://www.zhipin.com/web/geek/job-recommend"}
        ]
        with mock.patch("requests.get",
                        side_effect=[fake_version_resp, fake_tabs_resp]):
            with mock.patch.object(e2e, "_probe_login_via_cdp", return_value=False):
                result = e2e._check_prerequisites()
        self.assertTrue(result["cdp"])
        self.assertFalse(result["boss_login"])

    def test_cdp_up_no_zhipin_tab_but_logged_in_returns_true(self):
        """CDP up, no zhipin tab in tab list, but active probing via CDP
        (navigate + check plaintext salary) confirms logged-in.

        This is the real-world case: user logged in earlier, then navigated
        away or closed the zhipin tab. The tab-list check alone would
        incorrectly report False; we must probe via CDP to be accurate.
        """
        fake_version_resp = mock.Mock(status_code=200)
        fake_version_resp.json.return_value = {"Browser": "Chrome"}
        fake_tabs_resp = mock.Mock(status_code=200)
        fake_tabs_resp.json.return_value = [
            {"url": "https://www.google.com/"}
        ]
        with mock.patch("requests.get",
                        side_effect=[fake_version_resp, fake_tabs_resp]):
            with mock.patch.object(e2e, "_probe_login_via_cdp", return_value=True):
                result = e2e._check_prerequisites()
        self.assertTrue(result["cdp"])
        self.assertTrue(result["boss_login"],
                        "Active CDP probe confirming login must yield True even without a zhipin tab")


class OfflineCookiesDiagnosisTests(unittest.TestCase):
    """When CDP is down, an offline Cookies diagnosis must be appended."""

    def test_cookies_file_present_includes_size_and_mtime(self):
        """If Cookies file exists in the default user-data-dir, the note
        must report its size and last-modified time."""
        with tempfile.TemporaryDirectory(prefix="boss-e2e-test-") as tmp:
            fake_profile = Path(tmp) / "chrome-profile"
            _make_cookie_file(
                fake_profile,
                size=12345,
                mtime=datetime(2026, 7, 14, 16, 20, 59),
            )
            with mock.patch("requests.get", side_effect=ConnectionError("refused")):
                with mock.patch.object(e2e, "_default_cdp_data_dir",
                                       return_value=str(fake_profile)):
                    result = e2e._check_prerequisites()
        self.assertEqual(result["boss_login"], "unknown")
        note = result.get("boss_login_note", "")
        self.assertIn("Cookies file exists", note)
        self.assertIn("12345", note)
        self.assertIn("2026", note)
        # Hint that login state may still be valid
        self.assertIn("may still be valid", note.lower())

    def test_cookies_file_missing_reports_no_credentials(self):
        """If no Cookies file exists in the user-data-dir, the note must
        say so clearly — login state likely not persisted."""
        with tempfile.TemporaryDirectory(prefix="boss-e2e-test-") as tmp:
            fake_profile = Path(tmp) / "chrome-profile"
            # Create the profile dir but no Cookies file
            (fake_profile / "Default").mkdir(parents=True)
            with mock.patch("requests.get", side_effect=ConnectionError("refused")):
                with mock.patch.object(e2e, "_default_cdp_data_dir",
                                       return_value=str(fake_profile)):
                    result = e2e._check_prerequisites()
        self.assertEqual(result["boss_login"], "unknown")
        note = result.get("boss_login_note", "")
        self.assertIn("No Cookies file", note)
        self.assertIn("not persisted", note.lower())

    def test_user_data_dir_missing_reports_dir_absent(self):
        """If the user-data-dir itself doesn't exist, the note must say so."""
        with tempfile.TemporaryDirectory(prefix="boss-e2e-test-") as tmp:
            nonexistent = Path(tmp) / "does-not-exist"
            with mock.patch("requests.get", side_effect=ConnectionError("refused")):
                with mock.patch.object(e2e, "_default_cdp_data_dir",
                                       return_value=str(nonexistent)):
                    result = e2e._check_prerequisites()
        self.assertEqual(result["boss_login"], "unknown")
        note = result.get("boss_login_note", "")
        self.assertIn("user-data-dir not found", note.lower())


class AiCredentialsCheckTests(unittest.TestCase):
    """ai_credentials check must use the real store+keyring API.

    Previously the code called ai_service.load_settings(), which does not
    exist — hasattr() returned False, settings was always {}, and the check
    always reported 'No AI API key configured' even when a key was saved.
    """

    def test_configured_key_detected_via_store_and_keyring(self):
        """When store.get_ai_settings().is_configured is True AND
        ai.retrieve_api_key(cred_ref) returns a non-empty key,
        ai_credentials must be True."""
        fake_settings = {
            "is_configured": True,
            "endpoint_url": "https://opencode.ai/zen/v1",
            "model": "deepseek-v4-flash-free",
        }

        with tempfile.TemporaryDirectory(prefix="boss-e2e-test-") as tmp:
            fake_profile = Path(tmp) / "chrome-profile"
            (fake_profile / "Default").mkdir(parents=True)

            # CDP down so we only exercise the ai_credentials branch.
            with mock.patch("requests.get", side_effect=ConnectionError("refused")):
                with mock.patch.object(e2e, "_default_cdp_data_dir",
                                       return_value=str(fake_profile)):
                    with mock.patch.object(e2e, "_load_ai_settings",
                                           return_value=(fake_settings, "opencode.ai")):
                        with mock.patch.object(e2e, "_retrieve_api_key",
                                               return_value="sk-4abc1234def83kV"):
                            result = e2e._check_prerequisites()
        self.assertTrue(result["ai_credentials"],
                        "Configured key must be detected; previously always False "
                        "because load_settings() does not exist")

    def test_no_key_configured_reports_missing(self):
        """When store reports is_configured=False, ai_credentials is False."""
        fake_settings = {
            "is_configured": False,
            "endpoint_url": "",
            "model": "",
        }
        with tempfile.TemporaryDirectory(prefix="boss-e2e-test-") as tmp:
            fake_profile = Path(tmp) / "chrome-profile"
            (fake_profile / "Default").mkdir(parents=True)
            with mock.patch("requests.get", side_effect=ConnectionError("refused")):
                with mock.patch.object(e2e, "_default_cdp_data_dir",
                                       return_value=str(fake_profile)):
                    with mock.patch.object(e2e, "_load_ai_settings",
                                           return_value=(fake_settings, "")):
                        with mock.patch.object(e2e, "_retrieve_api_key",
                                               return_value=""):
                            result = e2e._check_prerequisites()
        self.assertFalse(result["ai_credentials"])

    def test_settings_configured_but_keyring_empty_reports_missing(self):
        """Edge case: store says configured but keyring lost the key.
        ai_credentials must be False (not crash)."""
        fake_settings = {
            "is_configured": True,
            "endpoint_url": "https://opencode.ai/zen/v1",
            "model": "deepseek-v4-flash-free",
        }
        with tempfile.TemporaryDirectory(prefix="boss-e2e-test-") as tmp:
            fake_profile = Path(tmp) / "chrome-profile"
            (fake_profile / "Default").mkdir(parents=True)
            with mock.patch("requests.get", side_effect=ConnectionError("refused")):
                with mock.patch.object(e2e, "_default_cdp_data_dir",
                                       return_value=str(fake_profile)):
                    with mock.patch.object(e2e, "_load_ai_settings",
                                           return_value=(fake_settings, "opencode.ai")):
                        with mock.patch.object(e2e, "_retrieve_api_key",
                                               return_value=""):
                            result = e2e._check_prerequisites()
        self.assertFalse(result["ai_credentials"],
                         "keyring lost key → ai_credentials must be False")


class MainBlockedOutputTests(unittest.TestCase):
    """The 'blocked' branch of main() must print the tri-state correctly."""

    def test_blocked_report_records_unknown_not_false(self):
        """When prerequisites fail, the saved JSON report must record
        boss_login='unknown' (not False) when CDP is down."""
        with tempfile.TemporaryDirectory(prefix="boss-e2e-test-") as tmp:
            fake_profile = Path(tmp) / "chrome-profile"
            (fake_profile / "Default").mkdir(parents=True)
            fake_result_path = Path(tmp) / "e2e_real_boss_result.json"

            # Save and restore FIXTURE_DIR so main() writes into tmp.
            original_fixture_dir = e2e.FIXTURE_DIR
            e2e.FIXTURE_DIR = Path(tmp)
            try:
                with mock.patch("requests.get", side_effect=ConnectionError("refused")):
                    with mock.patch.object(e2e, "_default_cdp_data_dir",
                                           return_value=str(fake_profile)):
                        backend = mock.Mock()
                        backend.is_ready.return_value = True
                        rc = e2e.main(browser_backend=backend)
                # Assert inside the tmp dir context — the dir is cleaned up on exit.
                self.assertEqual(rc, 1)
                self.assertTrue(fake_result_path.exists(),
                                "main() must write e2e_real_boss_result.json on blocked branch")
                with fake_result_path.open(encoding="utf-8") as fh:
                    import json
                    saved = json.load(fh)
            finally:
                e2e.FIXTURE_DIR = original_fixture_dir
        self.assertEqual(saved["status"], "blocked")
        prereqs = saved.get("prerequisites", {})
        self.assertEqual(prereqs.get("boss_login"), "unknown",
                         "Saved blocked report must record boss_login='unknown'")


class ManagedBrowserLifecycleTests(unittest.TestCase):
    """The E2E owns and closes only the dedicated browser it starts."""

    def test_browser_started_by_run_is_closed_and_reported(self):
        class FakeBackend:
            def __init__(self):
                self.ready = False
                self.start_calls = 0
                self.close_calls = 0

            def is_ready(self):
                return self.ready

            def start(self):
                self.start_calls += 1
                self.ready = True
                return True

            def close(self):
                self.close_calls += 1
                self.ready = False
                return True

        backend = FakeBackend()
        browser = e2e.ManagedCdpBrowser(backend)

        with browser:
            self.assertTrue(backend.ready)

        self.assertEqual(backend.start_calls, 1)
        self.assertEqual(backend.close_calls, 1)
        self.assertEqual(browser.report(), {
            "mode": "started_by_e2e",
            "close_status": "closed",
        })

    def test_preexisting_browser_is_reused_and_left_open(self):
        backend = mock.Mock()
        backend.is_ready.return_value = True
        browser = e2e.ManagedCdpBrowser(backend)

        with browser:
            pass

        backend.start.assert_not_called()
        backend.close.assert_not_called()
        self.assertEqual(browser.report(), {
            "mode": "reused_existing",
            "close_status": "not_requested",
        })

    def test_main_records_owned_browser_cleanup_when_prerequisites_block(self):
        class FakeBackend:
            def __init__(self):
                self.ready = False
                self.close_calls = 0

            def is_ready(self):
                return self.ready

            def start(self):
                self.ready = True
                return True

            def close(self):
                self.close_calls += 1
                self.ready = False
                return True

        backend = FakeBackend()
        blocked = {
            "cdp": True,
            "boss_login": False,
            "ai_credentials": True,
            "errors": ["BOSS login probe returned not-logged-in"],
        }

        with tempfile.TemporaryDirectory(prefix="boss-e2e-test-") as tmp:
            original_fixture_dir = e2e.FIXTURE_DIR
            e2e.FIXTURE_DIR = Path(tmp)
            try:
                with mock.patch.object(e2e, "_check_prerequisites", return_value=blocked):
                    rc = e2e.main(browser_backend=backend)
                with (Path(tmp) / "e2e_real_boss_result.json").open(encoding="utf-8") as fh:
                    import json
                    saved = json.load(fh)
            finally:
                e2e.FIXTURE_DIR = original_fixture_dir

        self.assertEqual(rc, 1)
        self.assertEqual(backend.close_calls, 1)
        self.assertEqual(saved["browser_lifecycle"], {
            "mode": "started_by_e2e",
            "close_status": "closed",
        })

    def test_owned_browser_is_closed_when_e2e_body_raises(self):
        backend = mock.Mock()
        backend.is_ready.return_value = False
        backend.start.return_value = True
        backend.close.return_value = True
        browser = e2e.ManagedCdpBrowser(backend)

        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            with browser:
                raise RuntimeError("synthetic failure")

        backend.close.assert_called_once_with()
        self.assertEqual(browser.report()["close_status"], "closed")

    def test_failed_owned_browser_close_is_visible_in_report(self):
        backend = mock.Mock()
        backend.is_ready.return_value = False
        backend.start.return_value = True
        backend.close.return_value = False
        browser = e2e.ManagedCdpBrowser(backend)

        with browser:
            pass

        self.assertEqual(browser.report(), {
            "mode": "started_by_e2e",
            "close_status": "close_failed",
        })

    def test_main_explicit_close_flag_closes_reused_browser(self):
        backend = mock.Mock()
        backend.is_ready.return_value = True
        backend.close.return_value = True
        blocked = {
            "cdp": True,
            "boss_login": False,
            "ai_credentials": True,
            "errors": ["BOSS login probe returned not-logged-in"],
        }

        with tempfile.TemporaryDirectory(prefix="boss-e2e-test-") as tmp:
            original_fixture_dir = e2e.FIXTURE_DIR
            e2e.FIXTURE_DIR = Path(tmp)
            try:
                with mock.patch.object(e2e, "_check_prerequisites", return_value=blocked), \
                        mock.patch.object(sys, "argv", ["e2e_real_boss.py", "--close-browser-after"]):
                    rc = e2e.main(browser_backend=backend)
                with (Path(tmp) / "e2e_real_boss_result.json").open(encoding="utf-8") as fh:
                    import json
                    saved = json.load(fh)
            finally:
                e2e.FIXTURE_DIR = original_fixture_dir

        self.assertEqual(rc, 1)
        backend.close.assert_called_once_with()
        self.assertEqual(saved["browser_lifecycle"], {
            "mode": "reused_existing",
            "close_status": "closed",
        })

    def test_requested_browser_close_failure_makes_command_fail_loudly(self):
        backend = mock.Mock()
        backend.is_ready.return_value = True
        backend.close.return_value = False
        ready = {
            "cdp": True,
            "boss_login": True,
            "ai_credentials": True,
            "errors": [],
        }

        with tempfile.TemporaryDirectory(prefix="boss-e2e-test-") as tmp:
            original_fixture_dir = e2e.FIXTURE_DIR
            e2e.FIXTURE_DIR = Path(tmp)
            try:
                with mock.patch.object(e2e, "_check_prerequisites", return_value=ready), \
                        mock.patch.object(e2e, "_run_live_provider_smoke", return_value={"status": "passed"}), \
                        mock.patch.object(e2e, "_run_e2e_with_market_retries", return_value={"status": "completed"}), \
                        mock.patch.object(sys, "argv", ["e2e_real_boss.py", "--close-browser-after"]):
                    rc = e2e.main(browser_backend=backend)
                with (Path(tmp) / "e2e_real_boss_result.json").open(encoding="utf-8") as fh:
                    import json
                    saved = json.load(fh)
            finally:
                e2e.FIXTURE_DIR = original_fixture_dir

        self.assertEqual(rc, 1)
        self.assertEqual(saved["browser_lifecycle"]["close_status"], "close_failed")
        self.assertIn("browser_close_failed", saved["operational_blockers"])


class T133AcceptanceGateTests(unittest.TestCase):
    """A real E2E report may be completed only when every T133 gate has evidence."""

    @staticmethod
    def _passing_report():
        return {
            "status": "running",
            "execution_mode": "http_routes",
            "provider_factory_mode": "application_composition_root",
            "counts": {
                "source_count": 2,
                "detail_count": 1,
                "evaluated_count": 2,
            },
            "feedback": {
                "status": "ok",
                "job_id": "real-job-id",
                "feedback_id": "feedback-id",
            },
            "cancel_test": {
                "status": "ok",
                "cancel_stage": "fetching_lists",
                "cancelled_unfinished_count": 1,
                "new_work_started_after_cancel": 0,
            },
            "resume_test": {
                "status": "ok",
                "created_via_http": True,
                "interruption_stage": "fetching_lists",
                "unfinished_before_resume": 1,
                "resubmitted_unfinished": 1,
                "duplicate_completed_count": 0,
            },
            "interrupt_points": [
                {"stage": "fetching_lists", "kind": "controlled_restart"},
            ],
        }

    def test_passing_report_has_no_blockers(self):
        blockers = e2e._validate_t133_report(self._passing_report())
        self.assertEqual(blockers, [])

    def test_zero_real_detail_and_evaluation_are_blockers(self):
        report = self._passing_report()
        report["counts"]["detail_count"] = 0
        report["counts"]["evaluated_count"] = 0
        blockers = e2e._validate_t133_report(report)
        self.assertIn("real_detail_missing", blockers)
        self.assertIn("real_evaluation_missing", blockers)

    def test_feedback_requires_a_real_job_id(self):
        report = self._passing_report()
        report["feedback"]["job_id"] = ""
        blockers = e2e._validate_t133_report(report)
        self.assertIn("feedback_job_missing", blockers)

    def test_cancel_must_happen_during_list_or_detail_and_stop_new_work(self):
        report = self._passing_report()
        report["cancel_test"].update({
            "cancel_stage": "created",
            "cancelled_unfinished_count": 0,
            "new_work_started_after_cancel": 2,
        })
        blockers = e2e._validate_t133_report(report)
        self.assertIn("cancel_stage_unverified", blockers)
        self.assertIn("cancel_did_not_stop_unfinished_work", blockers)

    def test_resume_requires_http_created_interruption_and_no_duplicate_completion(self):
        report = self._passing_report()
        report["resume_test"].update({
            "created_via_http": False,
            "unfinished_before_resume": 0,
            "resubmitted_unfinished": 0,
            "duplicate_completed_count": 1,
        })
        blockers = e2e._validate_t133_report(report)
        self.assertIn("resume_not_from_http_run", blockers)
        self.assertIn("resume_unfinished_work_not_resubmitted", blockers)
        self.assertIn("resume_repeated_completed_work", blockers)

    def test_private_provider_override_cannot_satisfy_http_composition_gate(self):
        report = self._passing_report()
        report["provider_factory_mode"] = "private_override"
        blockers = e2e._validate_t133_report(report)
        self.assertIn("provider_composition_bypassed", blockers)

    def test_finalize_downgrades_missing_real_detail_to_blocked(self):
        report = self._passing_report()
        report["counts"]["detail_count"] = 0
        finalized = e2e._finalize_t133_report(report)
        self.assertEqual(finalized["status"], "blocked")
        self.assertIn("real_detail_missing", finalized["blockers"])

    def test_finalize_marks_completed_only_when_every_gate_passes(self):
        finalized = e2e._finalize_t133_report(self._passing_report())
        self.assertEqual(finalized["status"], "completed")
        self.assertEqual(finalized["blockers"], [])


class T133MarketRetryTests(unittest.TestCase):
    """零列表结果必须换合理样本重试，不能静默宣称通过。"""

    def test_zero_list_attempt_retries_with_alternate_resume_and_selects_pass(self):
        calls = []

        def fake_runner(resume_name):
            calls.append(resume_name)
            if len(calls) == 1:
                return {"status": "blocked", "resume": resume_name,
                        "counts": {"source_count": 0}, "blockers": ["real_list_missing"]}
            return {"status": "completed", "resume": resume_name,
                    "counts": {"source_count": 1}, "blockers": []}

        report = e2e._run_e2e_with_market_retries(
            "resume_cross_family.txt", runner=fake_runner,
        )

        self.assertEqual(report["status"], "completed")
        self.assertEqual(calls, ["resume_cross_family.txt", "resume_single_path.txt"])
        self.assertEqual(len(report["market_attempts"]), 2)
        self.assertEqual(report["selected_resume"], "resume_single_path.txt")

    def test_all_zero_list_attempts_record_explicit_market_blocker(self):
        def fake_runner(resume_name):
            return {"status": "blocked", "resume": resume_name,
                    "counts": {"source_count": 0}, "blockers": ["real_list_missing"]}

        report = e2e._run_e2e_with_market_retries(
            "resume_cross_family.txt", runner=fake_runner,
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(len(report["market_attempts"]), 4)
        self.assertIn("真实搜索在测试时段返回 0 结果", report["market_search_blocker"])
        self.assertIn("real_list_missing", report["blockers"])

    def test_non_market_blocker_does_not_repeat_external_calls(self):
        calls = []

        def fake_runner(resume_name):
            calls.append(resume_name)
            return {"status": "blocked", "resume": resume_name,
                    "counts": {"source_count": 2}, "blockers": ["real_detail_missing"]}

        report = e2e._run_e2e_with_market_retries(
            "resume_cross_family.txt", runner=fake_runner,
        )

        self.assertEqual(calls, ["resume_cross_family.txt"])
        self.assertEqual(report["blockers"], ["real_detail_missing"])


if __name__ == "__main__":
    unittest.main()
