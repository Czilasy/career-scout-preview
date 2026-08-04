import pathlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from webui.job_feedback import (
    ACTIONS,
    REMINDER_THRESHOLD_HOURS,
    JobFeedbackError,
    JobFeedbackService,
    parse_rfc3339_utc,
)
from webui.store import TaskStore


UTC = timezone.utc
NOW = datetime(2026, 8, 5, 0, 0, tzinfo=UTC)


class JobFeedbackTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile = self.store.create_profile("反馈画像")
        self.service = JobFeedbackService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def create_job(self, *, platform="boss", platform_job_id=None, url=None, title="岗位"):
        platform_job_id = platform_job_id or f"{platform}-id-{title}"
        url = url or (
            f"https://www.zhipin.com/job_detail/{platform_job_id}.html"
            if platform == "boss"
            else f"https://www.zhaopin.com/jobdetail/{platform_job_id}.htm"
        )
        result = self.store.upsert_job(
            platform=platform,
            platform_job_id=platform_job_id,
            canonical_url=url,
            title=title,
            company="公司",
            salary="20K",
            location="上海",
            jd="Python JD",
        )
        self.assertTrue(result["ok"], result)
        return result["job_id"]

    def action(self, job_id, action, request_id, **kwargs):
        return self.service.execute_action(
            request_id=request_id,
            profile_id=self.profile["id"],
            job={"job_id": job_id},
            action=action,
            now=kwargs.pop("now", NOW),
            **kwargs,
        )


class JobFeedbackDomainTests(JobFeedbackTestCase):
    def test_action_allowlist_and_time_parser(self):
        self.assertEqual(
            set(ACTIONS),
            {
                "mark_read", "mark_applied", "correct_applied_at",
                "follow_up", "mark_stale", "restore_applied",
                "correct_status",
            },
        )
        self.assertEqual(
            parse_rfc3339_utc("2026-08-04T18:00:00+08:00").isoformat(),
            "2026-08-04T10:00:00+00:00",
        )
        with self.assertRaises(JobFeedbackError) as ctx:
            parse_rfc3339_utc("2026-08-05T00:00:00")
        self.assertEqual(ctx.exception.code, "applied_at_invalid")
        for invalid in (
            "2026-08-05 00:00:00+00:00", "2026-08-05T00:00:00z",
            "2026-08-05", "1785888000",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(JobFeedbackError):
                    parse_rfc3339_utc(invalid)

    def test_action_matrix_defaults_corrects_and_restores_times(self):
        job_id = self.create_job()
        applied = self.action(job_id, "mark_applied", "matrix-apply")
        self.assertEqual(applied["state"]["applied_at"], NOW.isoformat())

        corrected = self.action(
            job_id, "correct_applied_at", "matrix-time",
            applied_at="2026-07-01T08:00:00+08:00",
        )
        self.assertEqual(corrected["state"]["applied_at"], "2026-07-01T00:00:00+00:00")

        read = self.action(
            job_id, "correct_status", "matrix-read", target_status="read"
        )
        self.assertEqual(read["state"]["status"], "read")
        reapplied = self.action(
            job_id, "correct_status", "matrix-reapply", target_status="applied"
        )
        self.assertEqual(reapplied["state"]["status"], "applied")
        self.assertEqual(reapplied["state"]["applied_at"], "2026-07-01T00:00:00+00:00")

        empty_job = self.create_job(platform_job_id="matrix-empty")
        with self.assertRaises(JobFeedbackError) as ctx:
            self.action(
                empty_job, "correct_status", "matrix-missing-time",
                target_status="applied",
            )
        self.assertEqual(ctx.exception.code, "applied_at_required")
        with self.assertRaises(JobFeedbackError) as ctx:
            self.action(
                empty_job, "correct_status", "matrix-unrelated-time",
                target_status="read", applied_at="2026-07-01T00:00:00Z",
            )
        self.assertEqual(ctx.exception.code, "invalid_action_payload")

    def test_lifecycle_actions_persist_snapshot_events_and_revision(self):
        job_id = self.create_job()

        read = self.action(job_id, "mark_read", "r-read")
        self.assertTrue(read["changed"])
        self.assertEqual(read["state"]["status"], "read")

        applied = self.action(
            job_id, "mark_applied", "r-applied",
            applied_at="2026-07-01T00:00:00Z",
        )
        self.assertEqual(applied["state"]["status"], "applied")
        self.assertEqual(applied["state"]["applied_at"], "2026-07-01T00:00:00+00:00")

        follow_up = self.action(job_id, "follow_up", "r-follow")
        self.assertEqual(follow_up["state"]["status"], "applied")
        self.assertEqual(follow_up["state"]["last_follow_up_at"], NOW.isoformat())
        self.assertEqual(follow_up["state"]["revision"], 3)

        stale = self.action(job_id, "mark_stale", "r-stale")
        self.assertEqual(stale["state"]["status"], "stale")
        restored = self.action(job_id, "restore_applied", "r-restore")
        self.assertEqual(restored["state"]["status"], "applied")
        self.assertEqual(restored["state"]["applied_at"], "2026-07-01T00:00:00+00:00")
        self.assertEqual(restored["state"]["last_follow_up_at"], NOW.isoformat())

        events = self.service.list_events(self.profile["id"], job_id)
        self.assertEqual([event["action"] for event in events], [
            "mark_read", "mark_applied", "follow_up", "mark_stale",
            "restore_applied",
        ])

    def test_invalid_and_future_times_do_not_change_snapshot(self):
        job_id = self.create_job()
        self.action(job_id, "mark_applied", "r-applied", applied_at="2026-07-01T00:00:00Z")
        before = self.service.get_state(self.profile["id"], job_id, now=NOW)

        for request_id, value, code in (
            ("r-future", "2026-08-05T00:00:01Z", "applied_at_in_future"),
            ("r-naive", "2026-08-05T00:00:00", "applied_at_invalid"),
        ):
            with self.assertRaises(JobFeedbackError) as ctx:
                self.action(job_id, "correct_applied_at", request_id, applied_at=value)
            self.assertEqual(ctx.exception.code, code)

        self.assertEqual(self.service.get_state(self.profile["id"], job_id, now=NOW), before)
        self.assertEqual(len(self.service.list_events(self.profile["id"], job_id)), 1)

    def test_same_request_replays_and_different_payload_conflicts(self):
        job_id = self.create_job()
        first = self.action(job_id, "mark_read", "same")
        replay = self.action(job_id, "mark_read", "same")
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["event_id"], first["event_id"])
        self.assertEqual(len(self.service.list_events(self.profile["id"], job_id)), 1)

        with self.assertRaises(JobFeedbackError) as ctx:
            self.action(job_id, "mark_stale", "same")
        self.assertEqual(ctx.exception.code, "idempotency_conflict")

    def test_new_request_for_same_state_writes_receipt_without_event(self):
        job_id = self.create_job()
        first = self.action(job_id, "mark_read", "read-1")
        no_op = self.action(job_id, "mark_read", "read-2")

        self.assertTrue(first["changed"])
        self.assertFalse(no_op["changed"])
        self.assertIsNone(no_op["event_id"])
        with self.store._connection() as conn:
            event_count = conn.execute(
                "SELECT COUNT(*) FROM profile_job_events"
            ).fetchone()[0]
            receipts = conn.execute(
                "SELECT request_id, changed, event_id FROM profile_job_command_receipts ORDER BY created_at, request_id"
            ).fetchall()
        self.assertEqual(event_count, 1)
        self.assertEqual(len(receipts), 2)
        self.assertEqual(int(receipts[1]["changed"]), 0)
        self.assertIsNone(receipts[1]["event_id"])

    def test_concurrent_same_request_commits_one_event_and_one_receipt(self):
        job_id = self.create_job()

        def submit():
            return self.action(job_id, "mark_read", "concurrent-request")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))

        self.assertEqual(sorted(result["replayed"] for result in results), [False, True])
        self.assertEqual({result["event_id"] for result in results}, {results[0]["event_id"]})
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM profile_job_events").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM profile_job_command_receipts").fetchone()[0], 1)

    def test_new_follow_up_request_is_a_new_real_action(self):
        job_id = self.create_job()
        self.action(job_id, "mark_applied", "apply", applied_at="2026-07-01T00:00:00Z")
        first = self.action(job_id, "follow_up", "follow-1", now=NOW)
        second_now = NOW + timedelta(seconds=1)
        second = self.action(job_id, "follow_up", "follow-2", now=second_now)
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertEqual(second["state"]["last_follow_up_at"], second_now.isoformat())
        self.assertEqual(len(self.service.list_events(self.profile["id"], job_id)), 3)

    def test_transaction_rolls_back_snapshot_event_and_receipt(self):
        job_id = self.create_job()
        with patch("webui.job_feedback._insert_lifecycle_event", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                self.action(job_id, "mark_read", "rollback")

        with self.store._connection() as conn:
            snapshot = conn.execute(
                "SELECT status FROM profile_jobs WHERE profile_id=? AND job_id=?",
                (self.profile["id"], job_id),
            ).fetchone()
            events = conn.execute("SELECT COUNT(*) FROM profile_job_events").fetchone()[0]
            receipts = conn.execute("SELECT COUNT(*) FROM profile_job_command_receipts").fetchone()[0]
        self.assertIsNone(snapshot)
        self.assertEqual(events, 0)
        self.assertEqual(receipts, 0)

    def test_receipt_failure_rolls_back_snapshot_and_event(self):
        job_id = self.create_job(platform_job_id="receipt-rollback")
        with patch(
            "webui.job_feedback._insert_command_receipt",
            side_effect=RuntimeError("receipt failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "receipt failure"):
                self.action(job_id, "mark_read", "receipt-rollback")

        with self.store._connection() as conn:
            snapshot = conn.execute(
                "SELECT status FROM profile_jobs WHERE profile_id=? AND job_id=?",
                (self.profile["id"], job_id),
            ).fetchone()
            self.assertIsNone(snapshot)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM profile_job_events").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM profile_job_command_receipts").fetchone()[0], 0)

    def test_new_job_identity_rolls_back_when_event_insert_fails(self):
        job = {
            "platform": "boss",
            "platform_job_id": "new-rollback",
            "canonical_url": "https://www.zhipin.com/job_detail/new-rollback.html",
        }
        with patch(
            "webui.job_feedback._insert_lifecycle_event",
            side_effect=RuntimeError("event failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "event failure"):
                self.service.execute_action(
                    request_id="new-rollback", profile_id=self.profile["id"],
                    job=job, action="mark_read", now=NOW,
                )

        with self.store._connection() as conn:
            self.assertIsNone(conn.execute(
                "SELECT id FROM jobs WHERE platform='boss' AND platform_job_id='new-rollback'"
            ).fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM profile_jobs").fetchone()[0], 0)

    def test_authoritative_triple_creates_job_and_identity_conflict_is_atomic(self):
        job = {
            "platform": "zhilian",
            "platform_job_id": "triple-1",
            "canonical_url": "https://www.zhaopin.com/jobdetail/triple-1.htm",
            "title": "智联岗位",
            "company": "公司",
            "jd": "JD",
        }
        created = self.service.execute_action(
            request_id="triple-create", profile_id=self.profile["id"],
            job=job, action="mark_read", now=NOW,
        )
        internal_id = created["state"]["job_id"]
        with self.assertRaises(JobFeedbackError) as ctx:
            self.service.execute_action(
                request_id="identity-conflict", profile_id=self.profile["id"],
                job={
                    "job_id": internal_id,
                    "platform": "zhilian",
                    "platform_job_id": "different",
                    "canonical_url": job["canonical_url"],
                },
                action="mark_stale", now=NOW,
            )
        self.assertEqual(ctx.exception.code, "job_identity_conflict")
        self.assertEqual(self.service.get_state(self.profile["id"], internal_id)["status"], "read")
        self.assertEqual(len(self.service.list_events(self.profile["id"], internal_id)), 1)

    def test_lifecycle_actions_do_not_mutate_preference_feedback(self):
        job_id = self.create_job()
        self.store.link_profile_job(self.profile["id"], job_id, None, None, status="new")
        self.store.create_feedback(
            self.profile["id"], job_id, None, "interested", reason="role"
        )
        before = self.store.list_feedback(self.profile["id"], job_id)

        self.action(job_id, "mark_applied", "feedback-isolation", applied_at="2026-07-01T00:00:00Z")

        self.assertEqual(self.store.list_feedback(self.profile["id"], job_id), before)
        self.assertEqual(self.service.get_state(self.profile["id"], job_id)["status"], "applied")


class ReminderProjectionTests(JobFeedbackTestCase):
    def link_applied(self, job_id, applied_at, follow_up=None, profile_id=None):
        profile_id = profile_id or self.profile["id"]
        self.store.link_profile_job(profile_id, job_id, None, None, status="applied")
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE profile_jobs SET applied_at=?, last_follow_up_at=? WHERE profile_id=? AND job_id=?",
                (applied_at, follow_up, profile_id, job_id),
            )

    def test_threshold_follow_up_precedence_and_invalid_values(self):
        exact = (NOW - timedelta(hours=REMINDER_THRESHOLD_HOURS)).isoformat()
        before = (NOW - timedelta(hours=REMINDER_THRESHOLD_HOURS) + timedelta(seconds=1)).isoformat()
        job_exact = self.create_job(platform_job_id="exact")
        job_before = self.create_job(platform_job_id="before")
        job_followed = self.create_job(platform_job_id="followed")
        job_invalid_follow = self.create_job(platform_job_id="invalid-follow")
        self.link_applied(job_exact, exact)
        self.link_applied(job_before, before)
        self.link_applied(
            job_followed, NOW - timedelta(days=60),
            follow_up=(NOW - timedelta(hours=REMINDER_THRESHOLD_HOURS) + timedelta(seconds=1)).isoformat(),
        )
        self.link_applied(job_invalid_follow, NOW - timedelta(days=60), follow_up="not-a-time")

        projection = self.service.list_reminders(self.profile["id"], now=NOW, limit=100)
        ids = [item["job_id"] for item in projection["items"]]
        self.assertEqual(projection["total"], 1)
        self.assertEqual(ids, [job_exact])
        self.assertEqual(projection["threshold_hours"], 720)

    def test_state_projection_requires_valid_applied_at_even_with_valid_follow_up(self):
        job_id = self.create_job(platform_job_id="invalid-applied-state")
        self.link_applied(
            job_id, "not-a-time",
            follow_up=(NOW - timedelta(hours=REMINDER_THRESHOLD_HOURS)).isoformat(),
        )

        state = self.service.get_state(self.profile["id"], job_id, now=NOW)

        self.assertFalse(state["reminder"]["eligible"])
        self.assertIsNone(state["reminder"]["baseline_at"])
        self.assertEqual(
            self.service.list_reminders(self.profile["id"], now=NOW)["total"], 0
        )

    def test_profiles_and_platforms_are_mixed_without_platform_filter(self):
        profile_b = self.store.create_profile("另一个画像")
        boss_job = self.create_job(platform="boss", platform_job_id="boss-reminder")
        zhilian_job = self.create_job(platform="zhilian", platform_job_id="zhilian-reminder")
        other_job = self.create_job(platform="boss", platform_job_id="other-profile")
        old = (NOW - timedelta(hours=REMINDER_THRESHOLD_HOURS)).isoformat()
        self.link_applied(boss_job, old)
        self.link_applied(zhilian_job, old)
        self.link_applied(other_job, old, profile_id=profile_b["id"])

        projection = self.service.list_reminders(self.profile["id"], now=NOW)
        self.assertEqual(projection["total"], 2)
        self.assertEqual({item["platform"] for item in projection["items"]}, {"boss", "zhilian"})
        self.assertNotIn(other_job, {item["job_id"] for item in projection["items"]})

    def test_count_is_full_and_items_are_capped_and_stably_sorted(self):
        old = NOW - timedelta(hours=REMINDER_THRESHOLD_HOURS) - timedelta(seconds=1)
        job_ids = []
        for index in range(101):
            job_id = self.create_job(platform_job_id=f"many-{index:03d}")
            self.link_applied(job_id, (old - timedelta(seconds=index)).isoformat())
            job_ids.append(job_id)

        projection = self.service.list_reminders(self.profile["id"], now=NOW, limit=100)
        self.assertEqual(projection["total"], 101)
        self.assertEqual(len(projection["items"]), 100)
        self.assertEqual(
            [item["job_id"] for item in projection["items"]],
            job_ids[::-1][:100],
        )


if __name__ == "__main__":
    unittest.main()
