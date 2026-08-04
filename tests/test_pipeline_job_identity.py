"""Tests for the authoritative pipeline job identity resolver (Task 003).

Scope (specs/002-job-feedback-reminders/tasks003.md):

- Internal ``job_id`` and the full ``platform + platform_job_id +
  canonical_url`` triple are the only accepted identities.
- BOSS and zhilian share the same protocol; the platform only guards
  URL/identity validation.
- Missing triple fields, URL/platform mismatch, internal-id vs triple
  mismatch and dual-index conflicts must block with zero side effects.
- No guessing from UI platform, URL host, title, company, JD similarity
  or bare ``platform_job_id``.

The tests use an in-memory SQLite database plus a fake store that
implements the connection-aware upsert helper protocol, so they never
depend on the real app wiring (real assembly belongs to Task 008).
"""

import sqlite3
import unittest
import uuid

from webui.pipeline_job_identity import (
    JobIdentityConflictError,
    JobIdentityError,
    JobIdentityIncompleteError,
    JobNotFoundError,
    PlatformUrlMismatchError,
    normalize_display_fields,
    parse_identity_payload,
    project_safe_job,
    resolve_job_identity,
    resolve_job_identity_then,
)

BOSS_URL = "https://www.zhipin.com/job_detail/abc123.html"
BOSS_URL_OTHER = "https://www.zhipin.com/job_detail/zzz999.html"
ZHILIAN_URL = "https://www.zhaopin.com/jobdetail/xyz789.htm"
ZHILIAN_URL_OTHER = "https://www.zhaopin.com/jobdetail/qqq111.htm"


def _create_schema(conn):
    conn.executescript(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            canonical_url TEXT NOT NULL UNIQUE,
            source_url TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            salary TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            jd TEXT NOT NULL DEFAULT '',
            platform TEXT NOT NULL DEFAULT 'boss',
            platform_job_id TEXT,
            experience TEXT NOT NULL DEFAULT '',
            degree TEXT NOT NULL DEFAULT '',
            extra_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE UNIQUE INDEX idx_jobs_platform_job_id
            ON jobs(platform, platform_job_id)
            WHERE platform_job_id IS NOT NULL;
        CREATE TABLE profile_jobs (
            profile_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            UNIQUE(profile_id, job_id)
        );
        """
    )


class FakeJobStore:
    """Fake connection-aware dual-index upsert helper (Task 001 protocol).

    Mirrors the branch structure of ``TaskStore.upsert_job_with_connection``
    so the orchestration can be verified without the real store.
    """

    def __init__(self):
        self.calls = []

    def upsert_job_with_connection(
        self, conn, *, platform, platform_job_id, canonical_url,
        title="", company="", salary="", location="", jd="",
        experience="", degree="", extra=None, _validated_url=False,
    ):
        self.calls.append({
            "platform": platform,
            "platform_job_id": platform_job_id,
            "canonical_url": canonical_url,
            "_validated_url": _validated_url,
        })
        from webui.platforms import PlatformError, normalize_job_url

        url = canonical_url
        if not _validated_url:
            try:
                url = normalize_job_url(platform, canonical_url)
            except PlatformError:
                url = ""
            if not url:
                return {"ok": False, "job_id": None, "error_code": "platform_url_mismatch"}
        pid = platform_job_id if platform_job_id not in (None, "") else None

        by_pid = None
        if pid is not None:
            by_pid = conn.execute(
                "SELECT * FROM jobs WHERE platform=? AND platform_job_id=?",
                (platform, pid),
            ).fetchone()
        by_url = conn.execute(
            "SELECT * FROM jobs WHERE canonical_url=?", (url,),
        ).fetchone()

        display = {
            "title": title, "company": company, "salary": salary,
            "location": location, "jd": jd, "experience": experience,
            "degree": degree,
        }

        if by_url is not None and by_pid is None and by_url["platform"] != platform:
            return {"ok": False, "job_id": None, "error_code": "job_identity_conflict"}
        if by_pid is None and by_url is None:
            job_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO jobs (id, canonical_url, source_url, title, company,"
                " salary, location, jd, platform, platform_job_id, experience,"
                " degree) VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, url, display["title"], display["company"],
                 display["salary"], display["location"], display["jd"],
                 platform, pid, display["experience"], display["degree"]),
            )
            return {"ok": True, "job_id": job_id, "error_code": None}
        if by_pid is not None and by_url is None:
            other = conn.execute(
                "SELECT id FROM jobs WHERE canonical_url=? AND id<>?",
                (url, by_pid["id"]),
            ).fetchone()
            if other is not None:
                return {"ok": False, "job_id": None, "error_code": "job_identity_conflict"}
            conn.execute(
                "UPDATE jobs SET canonical_url=?, title=?, company=?, salary=?,"
                " location=?, jd=?, experience=?, degree=? WHERE id=?",
                (url, display["title"], display["company"], display["salary"],
                 display["location"], display["jd"], display["experience"],
                 display["degree"], by_pid["id"]),
            )
            return {"ok": True, "job_id": by_pid["id"], "error_code": None}
        if by_pid is None and by_url is not None:
            if by_url["platform_job_id"] not in (None, "") and by_url["platform_job_id"] != pid:
                return {"ok": False, "job_id": None, "error_code": "job_identity_conflict"}
            conn.execute(
                "UPDATE jobs SET platform_job_id=COALESCE(?, platform_job_id),"
                " title=?, company=?, salary=?, location=?, jd=?, experience=?,"
                " degree=? WHERE id=?",
                (pid, display["title"], display["company"], display["salary"],
                 display["location"], display["jd"], display["experience"],
                 display["degree"], by_url["id"]),
            )
            return {"ok": True, "job_id": by_url["id"], "error_code": None}
        if by_pid["id"] != by_url["id"]:
            return {"ok": False, "job_id": None, "error_code": "job_identity_conflict"}
        conn.execute(
            "UPDATE jobs SET title=?, company=?, salary=?, location=?, jd=?,"
            " experience=?, degree=? WHERE id=?",
            (display["title"], display["company"], display["salary"],
             display["location"], display["jd"], display["experience"],
             display["degree"], by_pid["id"]),
        )
        return {"ok": True, "job_id": by_pid["id"], "error_code": None}


class IdentityTestBase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        _create_schema(self.conn)
        self.store = FakeJobStore()

    def tearDown(self):
        self.conn.close()

    def insert_job(self, *, job_id=None, platform="boss", platform_job_id=None,
                   canonical_url=BOSS_URL, title="", company=""):
        job_id = job_id or uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO jobs (id, canonical_url, title, company, platform,"
            " platform_job_id) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, canonical_url, title, company, platform, platform_job_id),
        )
        return job_id

    def job_count(self):
        return self.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]

    def profile_job_rows(self):
        return self.conn.execute("SELECT * FROM profile_jobs").fetchall()

    def associate(self, resolved):
        """Simulated caller association write executed after identity resolution."""
        self.conn.execute(
            "INSERT INTO profile_jobs (profile_id, job_id) VALUES (?, ?)",
            ("profile-1", resolved.job_id),
        )
        return resolved.job_id


class InternalJobIdTests(IdentityTestBase):
    def test_resolve_existing_internal_id(self):
        job_id = self.insert_job(platform="boss", platform_job_id="boss-pid-1",
                                 canonical_url=BOSS_URL)
        resolved = resolve_job_identity(
            self.conn, self.store, parse_identity_payload({"job_id": job_id}),
        )
        self.assertEqual(resolved.job_id, job_id)
        self.assertEqual(resolved.platform, "boss")
        self.assertEqual(resolved.platform_job_id, "boss-pid-1")
        self.assertFalse(resolved.created)
        self.assertEqual(self.store.calls, [])

    def test_unknown_internal_id_is_not_found(self):
        with self.assertRaises(JobNotFoundError) as ctx:
            resolve_job_identity(
                self.conn, self.store,
                parse_identity_payload({"job_id": "missing-id"}),
            )
        self.assertEqual(ctx.exception.code, "job_not_found")
        self.assertEqual(self.job_count(), 0)

    def test_platform_job_id_is_not_accepted_as_internal_id(self):
        self.insert_job(platform="boss", platform_job_id="boss-pid-1",
                        canonical_url=BOSS_URL)
        # 裸平台 ID 绝不能被当作内部 job_id 解析。
        with self.assertRaises(JobNotFoundError):
            resolve_job_identity(
                self.conn, self.store,
                parse_identity_payload({"job_id": "boss-pid-1"}),
            )

    def test_internal_id_with_matching_triple_ok(self):
        job_id = self.insert_job(platform="zhilian", platform_job_id="zl-1",
                                 canonical_url=ZHILIAN_URL)
        resolved = resolve_job_identity(
            self.conn, self.store, parse_identity_payload({
                "job_id": job_id,
                "platform": "zhilian",
                "platform_job_id": "zl-1",
                "canonical_url": ZHILIAN_URL,
            }),
        )
        self.assertEqual(resolved.job_id, job_id)
        self.assertEqual(self.store.calls, [])

    def test_internal_id_with_partial_triple_incomplete(self):
        job_id = self.insert_job(platform="boss", platform_job_id="boss-pid-1",
                                 canonical_url=BOSS_URL)
        with self.assertRaises(JobIdentityIncompleteError):
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "job_id": job_id,
                    "platform": "boss",
                    "canonical_url": BOSS_URL,
                }),
            )

    def test_internal_id_with_conflicting_platform(self):
        job_id = self.insert_job(platform="boss", platform_job_id="boss-pid-1",
                                 canonical_url=BOSS_URL)
        with self.assertRaises(JobIdentityConflictError) as ctx:
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "job_id": job_id,
                    "platform": "zhilian",
                    "platform_job_id": "boss-pid-1",
                    "canonical_url": ZHILIAN_URL,
                }),
            )
        self.assertEqual(ctx.exception.code, "job_identity_conflict")

    def test_internal_id_with_conflicting_platform_job_id(self):
        job_id = self.insert_job(platform="boss", platform_job_id="boss-pid-1",
                                 canonical_url=BOSS_URL)
        with self.assertRaises(JobIdentityConflictError):
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "job_id": job_id,
                    "platform": "boss",
                    "platform_job_id": "boss-pid-OTHER",
                    "canonical_url": BOSS_URL,
                }),
            )

    def test_internal_id_with_conflicting_url(self):
        job_id = self.insert_job(platform="boss", platform_job_id="boss-pid-1",
                                 canonical_url=BOSS_URL)
        with self.assertRaises(JobIdentityConflictError):
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "job_id": job_id,
                    "platform": "boss",
                    "platform_job_id": "boss-pid-1",
                    "canonical_url": BOSS_URL_OTHER,
                }),
            )


class CompleteTripleTests(IdentityTestBase):
    def test_boss_triple_creates_internal_id(self):
        resolved = resolve_job_identity(
            self.conn, self.store, parse_identity_payload({
                "platform": "boss",
                "platform_job_id": "boss-pid-1",
                "canonical_url": BOSS_URL,
                "title": " Python 后端工程师 ",
                "company": "示例公司",
            }),
        )
        self.assertTrue(resolved.created)
        self.assertEqual(resolved.platform, "boss")
        self.assertEqual(resolved.platform_job_id, "boss-pid-1")
        self.assertEqual(resolved.canonical_url, BOSS_URL)
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE id=?", (resolved.job_id,),
        ).fetchone()
        # 展示字段规范化会去除首尾空白。
        self.assertEqual(row["title"], "Python 后端工程师")
        self.assertEqual(self.job_count(), 1)

    def test_zhilian_triple_uses_same_protocol(self):
        resolved = resolve_job_identity(
            self.conn, self.store, parse_identity_payload({
                "platform": "zhilian",
                "platform_job_id": "zl-pid-1",
                # http 链接必须按智联规则升级为 https 并规范化。
                "canonical_url": "http://jobs.zhaopin.com/zlpid1.htm",
            }),
        )
        self.assertTrue(resolved.created)
        self.assertEqual(resolved.platform, "zhilian")
        self.assertEqual(
            resolved.canonical_url,
            "https://www.zhaopin.com/jobdetail/zlpid1.htm",
        )

    def test_repeat_triple_reuses_internal_id(self):
        first = resolve_job_identity(
            self.conn, self.store, parse_identity_payload({
                "platform": "boss",
                "platform_job_id": "boss-pid-1",
                "canonical_url": BOSS_URL,
            }),
        )
        second = resolve_job_identity(
            self.conn, self.store, parse_identity_payload({
                "platform": "boss",
                "platform_job_id": "boss-pid-1",
                "canonical_url": BOSS_URL,
            }),
        )
        self.assertEqual(first.job_id, second.job_id)
        self.assertFalse(second.created)
        self.assertEqual(self.job_count(), 1)

    def test_display_fields_normalized(self):
        display = normalize_display_fields({
            "title": "  岗位  ",
            "company": None,
            "salary": 20000,
            "location": " 上海 ",
            "jd": "职责",
            "experience": "3-5年",
            "degree": "本科",
            "extra": None,
        })
        self.assertEqual(display.title, "岗位")
        self.assertEqual(display.company, "")
        self.assertEqual(display.salary, "20000")
        self.assertEqual(display.location, "上海")
        self.assertEqual(display.extra, {})


class IncompletenessTests(IdentityTestBase):
    def test_missing_platform_job_id_blocks(self):
        with self.assertRaises(JobIdentityIncompleteError) as ctx:
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "platform": "boss",
                    "canonical_url": BOSS_URL,
                }),
            )
        self.assertIn("platform_job_id", ctx.exception.details["missing_fields"])
        self.assertEqual(self.store.calls, [])
        self.assertEqual(self.job_count(), 0)

    def test_missing_canonical_url_blocks(self):
        with self.assertRaises(JobIdentityIncompleteError) as ctx:
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "platform": "boss",
                    "platform_job_id": "boss-pid-1",
                }),
            )
        self.assertIn("canonical_url", ctx.exception.details["missing_fields"])

    def test_missing_platform_blocks_even_if_host_implies_boss(self):
        # 只给 canonical_url 时禁止从 URL host 反推 platform。
        with self.assertRaises(JobIdentityIncompleteError) as ctx:
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "platform_job_id": "boss-pid-1",
                    "canonical_url": BOSS_URL,
                }),
            )
        self.assertIn("platform", ctx.exception.details["missing_fields"])
        self.assertEqual(self.store.calls, [])

    def test_ui_platform_hint_is_ignored(self):
        # payload 中任何 UI 当前平台提示都不得补全缺失 platform。
        with self.assertRaises(JobIdentityIncompleteError):
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "ui_platform": "boss",
                    "current_platform": "boss",
                    "platform_job_id": "boss-pid-1",
                    "canonical_url": BOSS_URL,
                }),
            )

    def test_empty_payload_blocks(self):
        with self.assertRaises(JobIdentityIncompleteError):
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({}),
            )

    def test_non_mapping_payload_blocks(self):
        with self.assertRaises(JobIdentityIncompleteError):
            parse_identity_payload("boss-pid-1")

    def test_non_string_identity_fields_block(self):
        with self.assertRaises(JobIdentityIncompleteError):
            parse_identity_payload({
                "platform": "boss",
                "platform_job_id": 12345,
                "canonical_url": BOSS_URL,
            })

    def test_unknown_platform_blocks(self):
        with self.assertRaises(JobIdentityError) as ctx:
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "platform": "linkedin",
                    "platform_job_id": "li-1",
                    "canonical_url": "https://www.linkedin.com/jobs/1",
                }),
            )
        # 平台枚举失败必须落在冻结合同错误码内。
        self.assertIn(ctx.exception.code, ("platform_url_mismatch", "job_identity_incomplete"))
        self.assertEqual(self.job_count(), 0)


class UrlMismatchTests(IdentityTestBase):
    def test_boss_platform_with_zhilian_url_blocks(self):
        with self.assertRaises(PlatformUrlMismatchError) as ctx:
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "platform": "boss",
                    "platform_job_id": "boss-pid-1",
                    "canonical_url": ZHILIAN_URL,
                }),
            )
        self.assertEqual(ctx.exception.code, "platform_url_mismatch")
        self.assertEqual(self.job_count(), 0)

    def test_zhilian_platform_with_boss_url_blocks(self):
        with self.assertRaises(PlatformUrlMismatchError):
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "platform": "zhilian",
                    "platform_job_id": "zl-pid-1",
                    "canonical_url": BOSS_URL,
                }),
            )

    def test_insecure_boss_url_blocks(self):
        with self.assertRaises(PlatformUrlMismatchError):
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "platform": "boss",
                    "platform_job_id": "boss-pid-1",
                    "canonical_url": "http://www.zhipin.com/job_detail/abc123.html",
                }),
            )

    def test_arbitrary_host_blocks(self):
        with self.assertRaises(PlatformUrlMismatchError):
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "platform": "boss",
                    "platform_job_id": "boss-pid-1",
                    "canonical_url": "https://evil.example.com/job_detail/abc.html",
                }),
            )


class DualIndexConflictTests(IdentityTestBase):
    def test_pid_matches_but_url_belongs_to_other_row(self):
        self.insert_job(platform="boss", platform_job_id="boss-pid-1",
                        canonical_url=BOSS_URL)
        self.insert_job(platform="boss", platform_job_id=None,
                        canonical_url=BOSS_URL_OTHER)
        before = self.job_count()
        with self.assertRaises(JobIdentityConflictError):
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "platform": "boss",
                    "platform_job_id": "boss-pid-1",
                    "canonical_url": BOSS_URL_OTHER,
                }),
            )
        self.assertEqual(self.job_count(), before)

    def test_pid_match_with_new_url_migrates_url(self):
        # Task 001 双索引语义：pid 命中且新 URL 不属于其它行时，
        # 允许同一岗位迁移到新规范 URL，不算冲突。
        job_id = self.insert_job(platform="boss", platform_job_id="boss-pid-1",
                                 canonical_url=BOSS_URL)
        resolved = resolve_job_identity(
            self.conn, self.store, parse_identity_payload({
                "platform": "boss",
                "platform_job_id": "boss-pid-1",
                "canonical_url": BOSS_URL_OTHER,
            }),
        )
        self.assertEqual(resolved.job_id, job_id)
        self.assertFalse(resolved.created)
        self.assertEqual(resolved.canonical_url, BOSS_URL_OTHER)
        self.assertEqual(self.job_count(), 1)

    def test_url_matches_but_pid_belongs_to_other_row(self):
        self.insert_job(platform="boss", platform_job_id="boss-pid-1",
                        canonical_url=BOSS_URL)
        with self.assertRaises(JobIdentityConflictError):
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "platform": "boss",
                    "platform_job_id": "boss-pid-OTHER",
                    "canonical_url": BOSS_URL,
                }),
            )

    def test_dual_index_hits_different_rows(self):
        self.insert_job(platform="boss", platform_job_id="boss-pid-1",
                        canonical_url=BOSS_URL)
        self.insert_job(platform="boss", platform_job_id="boss-pid-2",
                        canonical_url=BOSS_URL_OTHER)
        with self.assertRaises(JobIdentityConflictError):
            resolve_job_identity(
                self.conn, self.store, parse_identity_payload({
                    "platform": "boss",
                    "platform_job_id": "boss-pid-1",
                    "canonical_url": BOSS_URL_OTHER,
                }),
            )
        self.assertEqual(self.job_count(), 2)

    def test_conflict_has_zero_side_effects(self):
        self.insert_job(platform="boss", platform_job_id="boss-pid-1",
                        canonical_url=BOSS_URL)
        self.insert_job(platform="boss", platform_job_id=None,
                        canonical_url=BOSS_URL_OTHER)

        def fail_action(resolved):  # pragma: no cover - must never run
            raise AssertionError("身份冲突后不得执行关联写入")

        with self.assertRaises(JobIdentityConflictError):
            resolve_job_identity_then(
                self.conn, self.store,
                {
                    "platform": "boss",
                    "platform_job_id": "boss-pid-1",
                    "canonical_url": BOSS_URL_OTHER,
                },
                fail_action,
            )
        self.assertEqual(self.profile_job_rows(), [])


class NoMergingNoGuessingTests(IdentityTestBase):
    def test_same_bare_pid_across_platforms_not_merged(self):
        boss = resolve_job_identity(
            self.conn, self.store, parse_identity_payload({
                "platform": "boss",
                "platform_job_id": "shared-pid",
                "canonical_url": BOSS_URL,
            }),
        )
        zhilian = resolve_job_identity(
            self.conn, self.store, parse_identity_payload({
                "platform": "zhilian",
                "platform_job_id": "shared-pid",
                "canonical_url": ZHILIAN_URL,
            }),
        )
        self.assertNotEqual(boss.job_id, zhilian.job_id)
        self.assertEqual(self.job_count(), 2)

    def test_same_title_and_company_not_merged(self):
        boss = resolve_job_identity(
            self.conn, self.store, parse_identity_payload({
                "platform": "boss",
                "platform_job_id": "boss-pid-1",
                "canonical_url": BOSS_URL,
                "title": "Python 后端工程师",
                "company": "示例公司",
            }),
        )
        zhilian = resolve_job_identity(
            self.conn, self.store, parse_identity_payload({
                "platform": "zhilian",
                "platform_job_id": "zl-pid-1",
                "canonical_url": ZHILIAN_URL,
                "title": "Python 后端工程师",
                "company": "示例公司",
            }),
        )
        self.assertNotEqual(boss.job_id, zhilian.job_id)

    def test_bare_platform_job_id_only_blocks(self):
        with self.assertRaises(JobIdentityIncompleteError):
            resolve_job_identity(
                self.conn, self.store,
                parse_identity_payload({"platform_job_id": "boss-pid-1"}),
            )


class OrchestrationZeroSideEffectTests(IdentityTestBase):
    def test_success_runs_action_and_writes_association(self):
        job_id = resolve_job_identity_then(
            self.conn, self.store,
            {
                "platform": "boss",
                "platform_job_id": "boss-pid-1",
                "canonical_url": BOSS_URL,
            },
            self.associate,
        )
        rows = self.profile_job_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_id"], job_id)

    def test_incomplete_identity_blocks_action_and_writes(self):
        called = {"count": 0}

        def action(resolved):
            called["count"] += 1
            return self.associate(resolved)

        with self.assertRaises(JobIdentityIncompleteError):
            resolve_job_identity_then(
                self.conn, self.store,
                {"platform": "boss", "canonical_url": BOSS_URL},
                action,
            )
        self.assertEqual(called["count"], 0)
        self.assertEqual(self.profile_job_rows(), [])
        self.assertEqual(self.job_count(), 0)
        self.assertEqual(self.store.calls, [])

    def test_url_mismatch_blocks_action_and_writes(self):
        with self.assertRaises(PlatformUrlMismatchError):
            resolve_job_identity_then(
                self.conn, self.store,
                {
                    "platform": "boss",
                    "platform_job_id": "boss-pid-1",
                    "canonical_url": ZHILIAN_URL,
                },
                self.associate,
            )
        self.assertEqual(self.profile_job_rows(), [])
        self.assertEqual(self.job_count(), 0)

    def test_not_found_blocks_action_and_writes(self):
        with self.assertRaises(JobNotFoundError):
            resolve_job_identity_then(
                self.conn, self.store,
                {"job_id": "missing-id"},
                self.associate,
            )
        self.assertEqual(self.profile_job_rows(), [])


class SafeProjectionTests(IdentityTestBase):
    def test_project_safe_job_defaults(self):
        job_id = self.insert_job(
            platform="boss", platform_job_id="boss-pid-1",
            canonical_url=BOSS_URL, title="岗位", company="公司",
        )
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE id=?", (job_id,),
        ).fetchone()
        projected = project_safe_job(row)
        self.assertEqual(projected["job_id"], job_id)
        self.assertEqual(projected["platform"], "boss")
        self.assertEqual(projected["platform_job_id"], "boss-pid-1")
        self.assertEqual(projected["canonical_url"], BOSS_URL)
        self.assertTrue(projected["can_open"])
        self.assertNotIn("jd", projected)
        self.assertNotIn("extra_json", projected)

    def test_project_safe_job_invalid_url_not_openable(self):
        job_id = self.insert_job(
            platform="boss", platform_job_id="boss-pid-2",
            canonical_url="https://evil.example.com/x.html",
        )
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE id=?", (job_id,),
        ).fetchone()
        projected = project_safe_job(row)
        self.assertIsNone(projected["canonical_url"])
        self.assertFalse(projected["can_open"])


if __name__ == "__main__":
    unittest.main()
