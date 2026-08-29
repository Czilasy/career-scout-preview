"""028 采集层测试（B081 第 7 类：招聘者上次活跃）。

覆盖：
- Boss 名片活跃文本截获（实测值域 2026-08-29：18 详情页 7 种文本 + 无名片形态）
- build_detail_record 产物键
- 智联 staff 字段合并（scripts/zhilian/detail_fields，超标文件分流模块）
- fetch_job_details 详情链路 extra.recruiter_activity 合并（含失败岗位不写键）
- store.update_job_extra 原语（合并语义/缺行/空 patch）
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from scripts.boss.detail_parse import extract_recruiter_activity_text
from scripts.boss.detail_scrape import build_detail_record
from scripts.zhilian.detail_fields import (
    STAFF_CONST_JS,
    STAFF_FIELD_JS,
    merge_staff_fields,
)
from webui.recruiter_activity import normalize_detail_activity
from webui.source_breaker import SourceOutcome
from webui.store import TaskStore


def _boss_page(card_lines):
    """构造 Boss 详情页 page_text 形态：正文 → 名片区块 → 竞争力分析标记。"""
    lines = [
        "BOSS直聘", "登录 注册",
        "职位描述",
        "岗位职责：负责服务端开发", "任职要求：3 年以上经验",
        "",
    ]
    lines.extend(card_lines)
    lines.extend(["竞争力分析", "简历竞争力超越同行 80%", "BOSS 安全提示"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Boss 名片截获
# ---------------------------------------------------------------------------
class BossCardExtractTests(unittest.TestCase):
    def test_known_card_forms(self):
        cases = (
            "在线", "刚刚活跃", "今日活跃", "昨日活跃",
            "3日内活跃", "2周内活跃", "3月内活跃", "半年前活跃",
        )
        for activity in cases:
            page = _boss_page(["龚先生", activity, "宇晶机器", "·", "招聘专员"])
            self.assertEqual(
                extract_recruiter_activity_text(page), activity, activity,
            )

    def test_card_without_activity_line_returns_empty(self):
        """实测 18 例中 1 例：裸名片「姓名|头衔」无 · 分隔 → 未知兜底。"""
        page = _boss_page(["马若涵", "资深招聘顾问"])
        self.assertEqual(extract_recruiter_activity_text(page), "")

    def test_card_without_activity_but_with_dot_returns_empty(self):
        page = _boss_page(["龚先生", "宇晶机器", "·", "招聘专员"])
        self.assertEqual(extract_recruiter_activity_text(page), "")

    def test_no_card_returns_empty(self):
        self.assertEqual(extract_recruiter_activity_text(_boss_page([])), "")

    def test_empty_and_invalid_inputs(self):
        self.assertEqual(extract_recruiter_activity_text(""), "")
        self.assertEqual(extract_recruiter_activity_text(None), "")
        self.assertEqual(extract_recruiter_activity_text(123), "")


class BossDetailRecordTests(unittest.TestCase):
    def test_record_carries_activity_text(self):
        page = _boss_page(["龚先生", "刚刚活跃", "宇晶机器", "·", "招聘专员"])
        record = build_detail_record(
            {"job_link": "https://www.zhipin.com/job_detail/x.html"},
            {"jd": "JD", "tags": [], "page_text": page},
        )
        self.assertEqual(record["recruiter_activity_text"], "刚刚活跃")

    def test_record_without_page_text_defaults_empty(self):
        record = build_detail_record({}, {"jd": "JD", "tags": []})
        self.assertEqual(record["recruiter_activity_text"], "")


# ---------------------------------------------------------------------------
# 智联 staff 合并（scripts/zhilian/detail_fields）
# ---------------------------------------------------------------------------
class ZhilianStaffMergeTests(unittest.TestCase):
    def test_js_constants_shape(self):
        self.assertIn("staff", STAFF_CONST_JS)
        self.assertIn("lastOnlineTime", STAFF_CONST_JS)
        self.assertIn("staffLastOnlineMs", STAFF_FIELD_JS)
        self.assertIn("recruiter_activity_text", STAFF_FIELD_JS)

    def test_merge_valid_ms_and_text(self):
        merged = merge_staff_fields(
            {"jd": "x"},
            {"staffLastOnlineMs": 1756000000000, "recruiter_activity_text": "今日活跃"},
        )
        self.assertEqual(merged["recruiter_last_online_ms"], 1756000000000)
        self.assertEqual(merged["recruiter_activity_text"], "今日活跃")

    def test_merge_invalid_ms_keeps_text_only(self):
        for bad in (0, None, "abc", -5):
            merged = merge_staff_fields(
                {}, {"staffLastOnlineMs": bad, "recruiter_activity_text": "今日活跃"},
            )
            self.assertNotIn("recruiter_last_online_ms", merged, repr(bad))
            self.assertEqual(merged["recruiter_activity_text"], "今日活跃")

    def test_merge_missing_everything_noop(self):
        merged = merge_staff_fields({"jd": "x"}, {})
        self.assertNotIn("recruiter_last_online_ms", merged)
        self.assertNotIn("recruiter_activity_text", merged)

    def test_merge_non_dict_value_noop(self):
        self.assertEqual(merge_staff_fields({}, None), {})

    def test_merged_payload_normalizes_to_known_fact(self):
        merged = merge_staff_fields(
            {}, {"staffLastOnlineMs": time.time() * 1000 - 10 * 86400 * 1000,
                 "recruiter_activity_text": "今日活跃"},
        )
        fact = normalize_detail_activity("zhilian", merged)
        self.assertTrue(fact["known"])
        self.assertEqual(fact["source"], "zhilian")
        self.assertAlmostEqual(fact["age_lower_days"], 10.0, delta=0.01)


# ---------------------------------------------------------------------------
# 详情链路合并（fetch_job_details → job["extra"]）
# ---------------------------------------------------------------------------
class _FakeSource:
    def __init__(self, outcomes, platform="boss"):
        self._outcomes = outcomes
        self.platform = platform
        self.cdp_port = 9222 if platform == "boss" else 9223

    def preflight(self):
        return SimpleNamespace(ok=True)

    def fetch_details_batch(self, jobs, **kwargs):
        return dict(self._outcomes)


def _exec_config():
    return SimpleNamespace(
        detail_batch_size=5, detail_interval=0,
        detail_reset_every=0, detail_batch_cooldown=0,
        detail_tab_pool_size=1,
    )


class PipelineDetailExtraMergeTests(unittest.TestCase):
    def _run(self, jobs, outcomes, platform="boss"):
        from webui.pipeline_exec_details import fetch_job_details
        source = _FakeSource(outcomes, platform=platform)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                        return_value=(True, "")):
            return fetch_job_details(
                jobs, source, artifact_dir=tmp,
                execution_config=_exec_config(),
            )

    def test_activity_merged_into_extra(self):
        jobs = [{"platform_job_id": "1", "job_id": "1", "title": "A"}]
        outcomes = {"1": SourceOutcome.success(
            detail={"jd": "JD", "recruiter_activity_text": "半年前活跃"},
        )}
        result = self._run(jobs, outcomes)
        fact = result["jobs"][0]["extra"]["recruiter_activity"]
        self.assertTrue(fact["known"])
        self.assertEqual(fact["source"], "boss")
        self.assertEqual(fact["age_lower_days"], 180)
        self.assertIsNone(fact["age_upper_days"])
        self.assertEqual(result["jobs"][0]["jd"], "JD")

    def test_activity_absent_leaves_extra_untouched(self):
        jobs = [{"platform_job_id": "1", "job_id": "1", "title": "A"}]
        outcomes = {"1": SourceOutcome.success(detail={"jd": "JD"})}
        result = self._run(jobs, outcomes)
        extra = result["jobs"][0].get("extra")
        self.assertTrue(not isinstance(extra, dict) or "recruiter_activity" not in extra)

    def test_failed_detail_has_no_activity_fact(self):
        jobs = [{"platform_job_id": "1", "job_id": "1", "title": "A"}]
        outcomes = {"1": SourceOutcome.failure(
            failed_code="source_timeout", safe_log="timeout",
        )}
        result = self._run(jobs, outcomes)
        job = result["jobs"][0]
        extra = job.get("extra")
        self.assertTrue(not isinstance(extra, dict) or "recruiter_activity" not in extra)

    def test_zhilian_ms_fact_merged(self):
        jobs = [{"platform_job_id": "z1", "job_id": "z1", "title": "B"}]
        detail = merge_staff_fields(
            {"jd": "JD"},
            {"staffLastOnlineMs": time.time() * 1000 - 10 * 86400 * 1000,
             "recruiter_activity_text": "今日活跃"},
        )
        outcomes = {"z1": SourceOutcome.success(detail=detail)}
        result = self._run(jobs, outcomes, platform="zhilian")
        fact = result["jobs"][0]["extra"]["recruiter_activity"]
        self.assertTrue(fact["known"])
        self.assertEqual(fact["source"], "zhilian")
        self.assertAlmostEqual(fact["age_lower_days"], 10.0, delta=0.01)


class _RecordingStore:
    """028 B084：记录 update_job_extra 调用的最小替身。"""

    def __init__(self, raise_on_update=False):
        self.calls = []
        self.raise_on_update = raise_on_update

    def update_job_extra(self, platform, platform_job_id, patch):
        if self.raise_on_update:
            raise RuntimeError("store down")
        self.calls.append((platform, platform_job_id, patch))


class PipelineDetailStorePersistTests(unittest.TestCase):
    """fetch_job_details(store=...) → update_job_extra 岗位目录持久化（028 B084）。"""

    def _run(self, jobs, outcomes, platform="boss", store=None):
        from webui.pipeline_exec_details import fetch_job_details
        source = _FakeSource(outcomes, platform=platform)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                        return_value=(True, "")):
            return fetch_job_details(
                jobs, source, artifact_dir=tmp,
                execution_config=_exec_config(), store=store,
            )

    def test_store_receives_normalized_fact(self):
        store = _RecordingStore()
        jobs = [{"platform_job_id": "1", "job_id": "1", "title": "A"}]
        outcomes = {"1": SourceOutcome.success(
            detail={"jd": "JD", "recruiter_activity_text": "半年前活跃"},
        )}
        self._run(jobs, outcomes, store=store)
        self.assertEqual(len(store.calls), 1)
        platform, job_id, patch = store.calls[0]
        self.assertEqual(platform, "boss")
        self.assertEqual(job_id, "1")
        self.assertTrue(patch["recruiter_activity"]["known"])

    def test_store_failure_does_not_break_fetch(self):
        store = _RecordingStore(raise_on_update=True)
        jobs = [{"platform_job_id": "1", "job_id": "1", "title": "A"}]
        outcomes = {"1": SourceOutcome.success(
            detail={"jd": "JD", "recruiter_activity_text": "半年前活跃"},
        )}
        result = self._run(jobs, outcomes, store=store)
        self.assertEqual(result["jobs"][0]["jd"], "JD")
        fact = result["jobs"][0]["extra"]["recruiter_activity"]
        self.assertTrue(fact["known"], "落库失败不影响内存链路事实")

    def test_store_none_keeps_legacy_behavior(self):
        jobs = [{"platform_job_id": "1", "job_id": "1", "title": "A"}]
        outcomes = {"1": SourceOutcome.success(
            detail={"jd": "JD", "recruiter_activity_text": "刚刚活跃"},
        )}
        result = self._run(jobs, outcomes, store=None)
        self.assertEqual(result["jobs"][0]["jd"], "JD")

    def test_failed_detail_skips_store(self):
        store = _RecordingStore()
        jobs = [{"platform_job_id": "1", "job_id": "1", "title": "A"}]
        outcomes = {"1": SourceOutcome.failure(
            failed_code="source_timeout", safe_log="timeout",
        )}
        self._run(jobs, outcomes, store=store)
        self.assertEqual(store.calls, [])


# ---------------------------------------------------------------------------
# store.update_job_extra 原语（028 B081）
# ---------------------------------------------------------------------------
class UpdateJobExtraTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")

    def tearDown(self):
        self.temp.cleanup()

    def _seed(self):
        result = self.store.upsert_job(
            platform="boss", platform_job_id="abc",
            canonical_url="https://www.zhipin.com/job_detail/abc.html",
            title="岗位",
            extra={"welfare_list": ["五险"]},
        )
        self.assertTrue(result["ok"])
        return result["job_id"]

    def test_merge_preserves_existing_keys(self):
        job_id = self._seed()
        self.assertTrue(self.store.update_job_extra(
            "boss", "abc", {"recruiter_activity": {"known": True}},
        ))
        saved = json.loads(self.store.get_job(job_id)["extra_json"])
        self.assertEqual(saved["welfare_list"], ["五险"])
        self.assertEqual(saved["recruiter_activity"], {"known": True})

    def test_missing_row_returns_false(self):
        self.assertFalse(self.store.update_job_extra(
            "boss", "nope", {"recruiter_activity": {}},
        ))

    def test_empty_patch_returns_false(self):
        self._seed()
        self.assertFalse(self.store.update_job_extra("boss", "abc", {}))
        self.assertFalse(self.store.update_job_extra("boss", "abc", None))

    def test_corrupted_extra_json_recovers(self):
        job_id = self._seed()
        with self.store._connection() as conn:
            conn.execute("UPDATE jobs SET extra_json = ? WHERE id = ?",
                         ("not-json", job_id))
        self.assertTrue(self.store.update_job_extra(
            "boss", "abc", {"recruiter_activity": {"known": False}},
        ))
        saved = json.loads(self.store.get_job(job_id)["extra_json"])
        self.assertEqual(saved["recruiter_activity"], {"known": False})


if __name__ == "__main__":
    unittest.main()
