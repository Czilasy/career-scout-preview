"""Task 005 contract tests: lifecycle/reminder/event/advice HTTP API.

Runs against an isolated Flask app + route registrar fixture; it never
depends on or registers routes in ``webui/app.py`` (Task 008 owns that).
"""

import json
import pathlib
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from flask import Flask

from webui.job_feedback import REMINDER_THRESHOLD_HOURS, JobFeedbackService
from webui.job_feedback_api import (
    create_job_feedback_blueprint,
    register_job_feedback_routes,
)
from webui.store import TaskStore

UTC = timezone.utc
PAST_APPLIED = (datetime.now(UTC) - timedelta(days=40)).isoformat()
RECENT_APPLIED = (datetime.now(UTC) - timedelta(days=1)).isoformat()
FUTURE_APPLIED = (datetime.now(UTC) + timedelta(hours=2)).isoformat()


def _new_uuid():
    return str(uuid.uuid4())


class JobFeedbackApiTestCase(unittest.TestCase):
    """Isolated registrar fixture: real TaskStore + bare Flask app."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.service = JobFeedbackService(self.store)
        self.profile = self.store.create_profile("API 画像")
        self.other_profile = self.store.create_profile("隔离画像")
        self._captured_ai_messages = []
        self.app = Flask(__name__)
        self.app.json.sort_keys = False
        register_job_feedback_routes(
            self.app,
            SimpleNamespace(store=self.store),
            advice_provider=self._advice_provider,
            ai_credentials_provider=self._ai_credentials,
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    # -- fixture knobs ----------------------------------------------------

    def _advice_provider(self, endpoint, key, messages, model=""):
        self._captured_ai_messages.extend(messages)
        return {"action": "follow_up", "reason": "建议主动跟进进展"}

    def _ai_credentials(self):
        settings = {
            "endpoint_url": "https://ai.example/v1",
            "model": "test-model",
            "is_configured": True,
        }
        return settings, "ai.example", "test-key", "test-model"

    # -- helpers ----------------------------------------------------------

    def create_job(self, *, platform="boss", platform_job_id=None, url=None,
                   title="岗位", jd="Python JD"):
        platform_job_id = platform_job_id or f"{platform}-id-{_new_uuid()[:8]}"
        url = url or (
            f"https://www.zhipin.com/job_detail/{platform_job_id}.html"
            if platform == "boss"
            else f"https://www.zhaopin.com/jobdetail/{platform_job_id}.htm"
        )
        result = self.store.upsert_job(
            platform=platform, platform_job_id=platform_job_id,
            canonical_url=url, title=title, company="公司", salary="20K",
            location="上海", jd=jd,
        )
        self.assertTrue(result["ok"], result)
        return result["job_id"], platform_job_id, url

    def post_action(self, job, action, *, request_id=None, profile_id=None,
                    applied_at=None, target_status=None):
        payload = {
            "request_id": request_id or _new_uuid(),
            "profile_id": profile_id or self.profile["id"],
            "job": job,
            "action": action,
            "applied_at": applied_at,
            "target_status": target_status,
        }
        return self.client.post("/api/profile-jobs/actions", json=payload)

    def mark_overdue(self, job_id, *, applied_at=None):
        self.service.execute_action(
            request_id=_new_uuid(), profile_id=self.profile["id"],
            job={"job_id": job_id}, action="mark_applied",
            applied_at=applied_at or PAST_APPLIED,
        )

    def counts(self):
        with self.store._connection() as conn:
            return {
                table: conn.execute(
                    f"SELECT COUNT(*) AS c FROM {table}"
                ).fetchone()["c"]
                for table in (
                    "jobs", "profile_jobs", "profile_job_events",
                    "profile_job_command_receipts", "feedback_events",
                )
            }

    def assert_error_body(self, response, status, error_code):
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(set(body.keys()), {"ok", "error_code", "user_message", "details"})
        self.assertFalse(body["ok"])
        self.assertEqual(body["error_code"], error_code)
        self.assertIsInstance(body["user_message"], str)
        self.assertTrue(body["user_message"])
        return body


class StateReadTests(JobFeedbackApiTestCase):
    """T038: state reads are side-effect-free and never mark jobs read."""

    def test_state_returns_snapshot_with_revision_and_reminder(self):
        job_id, platform_job_id, url = self.create_job()
        self.post_action({"job_id": job_id}, "mark_applied", applied_at=PAST_APPLIED)
        before = self.counts()

        response = self.client.get(
            "/api/profile-jobs/state",
            query_string={"profile_id": self.profile["id"], "job_id": job_id},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["exists"])
        state = body["state"]
        self.assertEqual(state["job_id"], job_id)
        self.assertEqual(state["status"], "applied")
        self.assertEqual(state["revision"], 1)
        self.assertTrue(state["reminder"]["eligible"])
        self.assertEqual(
            state["reminder"]["elapsed_days"],
            state["reminder"]["elapsed_seconds"] // 86400,
        )
        self.assertEqual(self.counts(), before)

    def test_view_state_does_not_mark_read_and_no_writes(self):
        job_id, _, _ = self.create_job()
        self.post_action({"job_id": job_id}, "mark_read")
        before = self.counts()
        for _ in range(3):
            response = self.client.get(
                "/api/profile-jobs/state",
                query_string={"profile_id": self.profile["id"], "job_id": job_id},
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.service.get_state(self.profile["id"], job_id)["status"], "read"
        )
        self.assertEqual(self.counts(), before)

    def test_state_resolves_existing_job_by_triple_without_creating(self):
        job_id, platform_job_id, url = self.create_job(platform="zhilian")
        before = self.counts()
        response = self.client.get(
            "/api/profile-jobs/state",
            query_string={
                "profile_id": self.profile["id"], "platform": "zhilian",
                "platform_job_id": platform_job_id, "canonical_url": url,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["exists"])
        self.assertEqual(body["job_id"], job_id)
        self.assertEqual(self.counts(), before)

    def test_state_errors_are_stable(self):
        job_id, _, _ = self.create_job()
        self.assert_error_body(
            self.client.get("/api/profile-jobs/state", query_string={"job_id": job_id}),
            400, "invalid_request",
        )
        self.assert_error_body(
            self.client.get(
                "/api/profile-jobs/state",
                query_string={"profile_id": "missing", "job_id": job_id},
            ),
            404, "not_found",
        )
        self.assert_error_body(
            self.client.get(
                "/api/profile-jobs/state",
                query_string={"profile_id": self.profile["id"], "job_id": "missing"},
            ),
            404, "not_found",
        )
        self.assert_error_body(
            self.client.get(
                "/api/profile-jobs/state",
                query_string={
                    "profile_id": self.profile["id"], "platform": "boss",
                    "platform_job_id": "only-two",
                },
            ),
            422, "job_identity_incomplete",
        )

    def test_state_triple_url_mismatch(self):
        _, platform_job_id, _ = self.create_job(platform="boss")
        before = self.counts()
        self.assert_error_body(
            self.client.get(
                "/api/profile-jobs/state",
                query_string={
                    "profile_id": self.profile["id"], "platform": "boss",
                    "platform_job_id": platform_job_id,
                    "canonical_url": f"https://www.zhaopin.com/jobdetail/{platform_job_id}.htm",
                },
            ),
            422, "platform_url_mismatch",
        )
        self.assertEqual(self.counts(), before)


class EventReadTests(JobFeedbackApiTestCase):
    """T038: event reads are ordered, paginated and preference-free."""

    def test_events_exclude_preference_events_and_paginate(self):
        job_id, _, _ = self.create_job()
        self.post_action({"job_id": job_id}, "mark_read")
        self.post_action({"job_id": job_id}, "mark_applied", applied_at=PAST_APPLIED)
        self.post_action({"job_id": job_id}, "follow_up")
        self.store.create_feedback(self.profile["id"], job_id, "run-x", "interested")
        before = self.counts()

        response = self.client.get(
            f"/api/profile-jobs/{self.profile['id']}/{job_id}/events",
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["events"]), 3)
        self.assertEqual(
            [event["action"] for event in body["events"]],
            ["mark_read", "mark_applied", "follow_up"],
        )
        sequences = [event["sequence"] for event in body["events"]]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(body["next_after_sequence"], sequences[-1])
        for event in body["events"]:
            self.assertNotIn("request_id", event)
            self.assertNotIn("request_fingerprint", event)
        self.assertEqual(self.counts(), before)

        page_one = self.client.get(
            f"/api/profile-jobs/{self.profile['id']}/{job_id}/events",
            query_string={"limit": 2},
        ).get_json()
        self.assertEqual(len(page_one["events"]), 2)
        page_two = self.client.get(
            f"/api/profile-jobs/{self.profile['id']}/{job_id}/events",
            query_string={"after_sequence": page_one["next_after_sequence"], "limit": 2},
        ).get_json()
        self.assertEqual(len(page_two["events"]), 1)
        self.assertGreater(
            page_two["events"][0]["sequence"], page_one["next_after_sequence"],
        )

    def test_events_limit_bounds_and_not_found(self):
        job_id, _, _ = self.create_job()
        self.assert_error_body(
            self.client.get(
                f"/api/profile-jobs/{self.profile['id']}/{job_id}/events",
                query_string={"limit": 201},
            ),
            400, "invalid_limit",
        )
        self.assert_error_body(
            self.client.get(
                f"/api/profile-jobs/{self.profile['id']}/{job_id}/events",
                query_string={"limit": "abc"},
            ),
            400, "invalid_limit",
        )
        self.assert_error_body(
            self.client.get(
                f"/api/profile-jobs/{self.profile['id']}/{job_id}/events",
                query_string={"after_sequence": "xyz"},
            ),
            400, "invalid_request",
        )
        self.assert_error_body(
            self.client.get(
                f"/api/profile-jobs/{self.profile['id']}/missing-job/events",
            ),
            404, "job_not_found",
        )
        self.assert_error_body(
            self.client.get(f"/api/profile-jobs/missing/{job_id}/events"),
            404, "profile_not_found",
        )


class ActionTests(JobFeedbackApiTestCase):
    """T039: seven actions, first-time triple, replay/conflict, rollback."""

    def test_all_seven_actions_via_http(self):
        job_id, _, _ = self.create_job()
        pid = self.profile["id"]
        job = {"job_id": job_id}

        applied = self.post_action(job, "mark_applied", applied_at=PAST_APPLIED)
        self.assertEqual(applied.status_code, 200, applied.get_data(as_text=True))
        self.assertEqual(applied.get_json()["state"]["status"], "applied")

        read = self.post_action(job, "mark_read")
        self.assertEqual(read.get_json()["state"]["status"], "read")

        corrected_status = self.post_action(job, "correct_status", target_status="applied")
        self.assertEqual(corrected_status.get_json()["state"]["status"], "applied")
        self.assertEqual(
            corrected_status.get_json()["state"]["applied_at"], PAST_APPLIED,
        )

        corrected_time = self.post_action(
            job, "correct_applied_at",
            applied_at=(datetime.now(UTC) - timedelta(days=50)).isoformat(),
        )
        self.assertEqual(corrected_time.status_code, 200)

        follow = self.post_action(job, "follow_up")
        follow_body = follow.get_json()
        self.assertEqual(follow_body["state"]["status"], "applied")
        self.assertTrue(follow_body["state"]["last_follow_up_at"])

        stale = self.post_action(job, "mark_stale")
        self.assertEqual(stale.get_json()["state"]["status"], "stale")

        restored = self.post_action(job, "restore_applied")
        restored_state = restored.get_json()["state"]
        self.assertEqual(restored_state["status"], "applied")
        self.assertTrue(restored_state["last_follow_up_at"])

        events = self.client.get(f"/api/profile-jobs/{pid}/{job_id}/events").get_json()
        self.assertEqual(len(events["events"]), 7)

    def test_first_time_triple_creates_job_atomically(self):
        platform_job_id = f"boss-new-{_new_uuid()[:8]}"
        url = f"https://www.zhipin.com/job_detail/{platform_job_id}.html"
        before = self.counts()
        response = self.post_action(
            {
                "platform": "boss", "platform_job_id": platform_job_id,
                "canonical_url": url, "title": "新岗位", "company": "新公司",
                "salary": "20-30K", "location": "上海", "jd": "新 JD",
            },
            "mark_applied", applied_at=PAST_APPLIED,
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        internal_id = body["state"]["job_id"]
        self.assertNotEqual(internal_id, platform_job_id)
        stored = self.store.get_job(internal_id)
        self.assertEqual(stored["platform"], "boss")
        self.assertEqual(stored["platform_job_id"], platform_job_id)
        self.assertEqual(body["state"]["status"], "applied")
        after = self.counts()
        self.assertEqual(after["jobs"], before["jobs"] + 1)
        self.assertEqual(after["profile_jobs"], before["profile_jobs"] + 1)
        self.assertEqual(after["profile_job_events"], before["profile_job_events"] + 1)
        self.assertEqual(
            after["profile_job_command_receipts"],
            before["profile_job_command_receipts"] + 1,
        )

    def test_replay_same_payload_returns_original_receipt(self):
        job_id, _, _ = self.create_job()
        request_id = _new_uuid()
        job = {"job_id": job_id}
        first = self.post_action(job, "mark_applied", request_id=request_id,
                                 applied_at=PAST_APPLIED)
        self.assertEqual(first.status_code, 200)
        before = self.counts()
        second = self.post_action(job, "mark_applied", request_id=request_id,
                                  applied_at=PAST_APPLIED)
        self.assertEqual(second.status_code, 200)
        body = second.get_json()
        self.assertTrue(body["replayed"])
        self.assertTrue(body["changed"])
        self.assertEqual(body["event_id"], first.get_json()["event_id"])
        self.assertEqual(
            body["event_sequence"], first.get_json()["event_sequence"],
        )
        self.assertEqual(self.counts(), before)

    def test_replay_noop_command_keeps_null_event_fields(self):
        job_id, _, _ = self.create_job()
        self.post_action({"job_id": job_id}, "mark_read")
        request_id = _new_uuid()
        first = self.post_action({"job_id": job_id}, "mark_read", request_id=request_id)
        self.assertEqual(first.status_code, 200)
        first_body = first.get_json()
        self.assertFalse(first_body["changed"])
        self.assertIsNone(first_body["event_id"])
        self.assertIsNone(first_body["event_sequence"])
        replayed = self.post_action({"job_id": job_id}, "mark_read", request_id=request_id)
        replayed_body = replayed.get_json()
        self.assertTrue(replayed_body["replayed"])
        self.assertFalse(replayed_body["changed"])
        self.assertIsNone(replayed_body["event_id"])

    def test_request_id_conflict_returns_409_without_side_effects(self):
        job_id, _, _ = self.create_job()
        request_id = _new_uuid()
        first = self.post_action({"job_id": job_id}, "mark_read", request_id=request_id)
        self.assertEqual(first.status_code, 200)
        before = self.counts()
        conflict = self.post_action({"job_id": job_id}, "mark_stale", request_id=request_id)
        self.assert_error_body(conflict, 409, "idempotency_conflict")
        self.assertEqual(self.counts(), before)
        self.assertEqual(
            self.service.get_state(self.profile["id"], job_id)["status"], "read",
        )

    def test_failed_event_insert_rolls_back_everything(self):
        job_id, _, _ = self.create_job()
        self.post_action({"job_id": job_id}, "mark_read")
        before = self.counts()
        with patch(
            "webui.job_feedback._insert_lifecycle_event",
            side_effect=sqlite3.OperationalError("boom"),
        ):
            response = self.post_action({"job_id": job_id}, "mark_stale")
        self.assert_error_body(response, 500, "persistence_failed")
        self.assertNotIn("boom", response.get_data(as_text=True))
        self.assertNotIn("Traceback", response.get_data(as_text=True))
        self.assertEqual(self.counts(), before)
        self.assertEqual(
            self.service.get_state(self.profile["id"], job_id)["status"], "read",
        )

    def test_stable_error_codes_for_domain_failures(self):
        job_id, platform_job_id, url = self.create_job()
        job = {"job_id": job_id}
        before = self.counts()

        self.assert_error_body(
            self.post_action(job, "launch_rocket"), 400, "invalid_action",
        )
        self.assert_error_body(
            self.post_action(job, "correct_applied_at"),
            400, "invalid_action_payload",
        )
        self.assert_error_body(
            self.post_action(job, "mark_read", applied_at=PAST_APPLIED),
            400, "invalid_action_payload",
        )
        payload = {
            "profile_id": self.profile["id"], "job": job, "action": "mark_read",
        }
        self.assert_error_body(
            self.client.post("/api/profile-jobs/actions", json=payload),
            400, "invalid_action_payload",
        )
        self.assert_error_body(
            self.post_action(job, "mark_applied", applied_at=FUTURE_APPLIED),
            422, "applied_at_in_future",
        )
        self.assert_error_body(
            self.post_action(job, "mark_applied", applied_at="2026-08-01T00:00:00"),
            422, "applied_at_invalid",
        )
        self.assert_error_body(
            self.post_action({"job_id": "missing"}, "mark_read"),
            404, "job_not_found",
        )
        self.assert_error_body(
            self.post_action(job, "mark_read", profile_id="missing"),
            404, "profile_not_found",
        )
        self.assert_error_body(
            self.post_action({"platform": "boss"}, "mark_read"),
            422, "job_identity_incomplete",
        )
        self.assert_error_body(
            self.post_action(
                {
                    "platform": "boss", "platform_job_id": platform_job_id,
                    "canonical_url": f"https://www.zhaopin.com/jobdetail/{platform_job_id}.htm",
                },
                "mark_read",
            ),
            422, "platform_url_mismatch",
        )
        self.assert_error_body(
            self.post_action(
                {"job_id": job_id, "platform": "boss",
                 "platform_job_id": "other-id", "canonical_url": url},
                "mark_read",
            ),
            409, "job_identity_conflict",
        )
        self.assert_error_body(
            self.post_action(job, "follow_up"),
            409, "state_precondition_failed",
        )
        self.assertEqual(self.counts(), before)


class ReminderTests(JobFeedbackApiTestCase):
    """T041/T043: shared projection, isolation, cap 100, no platform filter."""

    def _seed_mixed_overdue(self):
        boss_id, boss_pid, boss_url = self.create_job(platform="boss", title="BOSS 岗位")
        zhilian_id, zl_pid, zl_url = self.create_job(platform="zhilian", title="智联岗位")
        older = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        self.mark_overdue(boss_id, applied_at=older)
        self.mark_overdue(zhilian_id)
        return boss_id, zhilian_id

    def test_count_and_list_mix_boss_and_zhilian_without_platform_filter(self):
        boss_id, zhilian_id = self._seed_mixed_overdue()
        before = self.counts()

        count = self.client.get(
            "/api/job-reminders/count",
            query_string={"profile_id": self.profile["id"]},
        )
        self.assertEqual(count.status_code, 200)
        count_body = count.get_json()
        self.assertEqual(count_body["total"], 2)
        self.assertEqual(count_body["threshold_hours"], REMINDER_THRESHOLD_HOURS)

        listed = self.client.get(
            "/api/job-reminders",
            query_string={"profile_id": self.profile["id"]},
        ).get_json()
        self.assertEqual(listed["total"], 2)
        platforms = {item["platform"] for item in listed["items"]}
        self.assertEqual(platforms, {"boss", "zhilian"})
        baselines = [item["baseline_at"] for item in listed["items"]]
        self.assertEqual(baselines, sorted(baselines))

        # Supplying a platform parameter must not filter or change anything.
        filtered_count = self.client.get(
            "/api/job-reminders/count",
            query_string={"profile_id": self.profile["id"], "platform": "boss"},
        ).get_json()
        filtered_list = self.client.get(
            "/api/job-reminders",
            query_string={"profile_id": self.profile["id"], "platform": "boss"},
        ).get_json()
        self.assertEqual(filtered_count["total"], 2)
        self.assertEqual(filtered_list["total"], 2)
        self.assertEqual(len(filtered_list["items"]), 2)
        self.assertEqual(self.counts(), before)

    def test_list_caps_at_100_with_accurate_total(self):
        for index in range(105):
            job_id, _, _ = self.create_job(
                platform="zhilian" if index % 2 else "boss",
                platform_job_id=f"bulk-{index}",
            )
            self.mark_overdue(job_id)
        body = self.client.get(
            "/api/job-reminders",
            query_string={"profile_id": self.profile["id"]},
        ).get_json()
        self.assertEqual(body["total"], 105)
        self.assertEqual(len(body["items"]), 100)

    def test_limit_validation_and_profile_isolation(self):
        self._seed_mixed_overdue()
        # Seed an overdue job in another profile: it must stay invisible.
        other_job, _, _ = self.create_job(platform="boss", platform_job_id="other-profile")
        self.service.execute_action(
            request_id=_new_uuid(), profile_id=self.other_profile["id"],
            job={"job_id": other_job}, action="mark_applied",
            applied_at=PAST_APPLIED,
        )
        body = self.client.get(
            "/api/job-reminders",
            query_string={"profile_id": self.profile["id"]},
        ).get_json()
        self.assertEqual(body["total"], 2)
        for item in body["items"]:
            self.assertNotEqual(item["job_id"], other_job)

        self.assert_error_body(
            self.client.get(
                "/api/job-reminders",
                query_string={"profile_id": self.profile["id"], "limit": 101},
            ),
            400, "invalid_limit",
        )
        self.assert_error_body(
            self.client.get(
                "/api/job-reminders",
                query_string={"profile_id": self.profile["id"], "limit": "abc"},
            ),
            400, "invalid_limit",
        )
        self.assert_error_body(
            self.client.get("/api/job-reminders/count"),
            400, "invalid_request",
        )
        self.assert_error_body(
            self.client.get(
                "/api/job-reminders/count", query_string={"profile_id": "missing"},
            ),
            404, "profile_not_found",
        )

    def test_invalid_follow_up_time_excluded_not_fallen_back(self):
        job_id, _, _ = self.create_job()
        self.mark_overdue(job_id)
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE profile_jobs SET last_follow_up_at=? "
                "WHERE profile_id=? AND job_id=?",
                ("corrupted-not-a-time", self.profile["id"], job_id),
            )
        body = self.client.get(
            "/api/job-reminders",
            query_string={"profile_id": self.profile["id"]},
        ).get_json()
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["items"], [])


class AdviceTests(JobFeedbackApiTestCase):
    """T042: read-only advice with server re-read and rule fallback."""

    def test_advice_success_reads_server_facts_only(self):
        job_id, _, _ = self.create_job(jd="服务端 JD 内容")
        self.mark_overdue(job_id)
        before = self.counts()
        response = self.client.post(
            f"/api/profile-jobs/{self.profile['id']}/{job_id}/advice",
            json={"jd": "客户端伪造 JD", "platform": "boss", "elapsed_days": 1},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(set(body.keys()), {"ok", "action", "reason", "source"})
        self.assertEqual(body["action"], "follow_up")
        self.assertEqual(body["source"], "ai")
        # AI input must carry the server-side JD, never client junk.
        user_message = json.loads(self._captured_ai_messages[-1]["content"])
        self.assertEqual(user_message["jd"], "服务端 JD 内容")
        self.assertNotIn("platform", user_message)
        self.assertGreaterEqual(user_message["elapsed_days"], 39)
        self.assertEqual(self.counts(), before)

    def test_advice_provider_failure_falls_back_to_rule(self):
        job_id, _, _ = self.create_job(jd="有 JD")
        self.mark_overdue(job_id)

        def _broken(endpoint, key, messages, model=""):
            raise TimeoutError("ai timeout")

        app = Flask(__name__)
        register_job_feedback_routes(
            app, SimpleNamespace(store=self.store), advice_provider=_broken,
            ai_credentials_provider=self._ai_credentials,
        )
        body = app.test_client().post(
            f"/api/profile-jobs/{self.profile['id']}/{job_id}/advice",
        ).get_json()
        self.assertEqual(body["action"], "follow_up")
        self.assertEqual(body["source"], "rule")

    def test_advice_missing_jd_reviews_without_ai_call(self):
        job_id, _, _ = self.create_job(jd="")
        self.mark_overdue(job_id)
        response = self.client.post(
            f"/api/profile-jobs/{self.profile['id']}/{job_id}/advice",
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["action"], "review")
        self.assertEqual(body["source"], "rule")
        self.assertEqual(self._captured_ai_messages, [])

    def test_advice_not_eligible_returns_409_and_zero_side_effects(self):
        job_id, _, _ = self.create_job()
        self.post_action({"job_id": job_id}, "mark_applied", applied_at=RECENT_APPLIED)
        before = self.counts()
        self.assert_error_body(
            self.client.post(
                f"/api/profile-jobs/{self.profile['id']}/{job_id}/advice",
            ),
            409, "reminder_not_eligible",
        )
        self.assertEqual(self.counts(), before)

    def test_advice_unknown_job_or_profile(self):
        self.assert_error_body(
            self.client.post(
                f"/api/profile-jobs/{self.profile['id']}/missing/advice",
            ),
            404, "not_found",
        )
        job_id, _, _ = self.create_job()
        self.assert_error_body(
            self.client.post(f"/api/profile-jobs/missing/{job_id}/advice"),
            404, "not_found",
        )

    def test_advice_unconfigured_ai_uses_rule_and_never_leaks_credentials(self):
        job_id, _, _ = self.create_job(jd="有 JD")
        self.mark_overdue(job_id)
        app = Flask(__name__)
        register_job_feedback_routes(app, SimpleNamespace(store=self.store))  # real store credentials path
        response = app.test_client().post(
            f"/api/profile-jobs/{self.profile['id']}/{job_id}/advice",
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["source"], "rule")
        text = response.get_data(as_text=True)
        self.assertNotIn("endpoint", text)
        self.assertNotIn("api_key", text)
        self.assertNotIn("credential", text)


class RegistrarShapeTests(JobFeedbackApiTestCase):
    """T037: registrar stays independent from webui/app.py."""

    def test_blueprint_factory_returns_blueprint(self):
        blueprint = create_job_feedback_blueprint(self.store)
        self.assertEqual(blueprint.name, "job_feedback_api")
        rules = {
            "/api/profile-jobs/state",
            "/api/profile-jobs/actions",
            "/api/profile-jobs/<profile_id>/<job_id>/events",
            "/api/job-reminders/count",
            "/api/job-reminders",
            "/api/profile-jobs/<profile_id>/<job_id>/advice",
        }
        app = Flask(__name__)
        app.register_blueprint(blueprint)
        paths = {str(rule) for rule in app.url_map.iter_rules()}
        self.assertTrue(rules.issubset(paths), rules - paths)


if __name__ == "__main__":
    unittest.main()
