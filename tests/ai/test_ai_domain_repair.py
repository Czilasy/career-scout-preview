"""B094 审查返修回归：临时库及外部边界桩，不是真实模型验收。"""

from __future__ import annotations

import json
import hashlib
import pathlib
import unittest
from unittest import mock

from tests.ai.test_ai_domain_recall import DomainRecallHarness, _domain_job
from tests.ai.test_ai_domain_context import BOSS_INTERNET_CODE, INTERNET_GROUP
from webui import ai_domain_policy as policy
from webui.error_registry import FAILED_CODE_LABELS
from webui.screen_flow import find_resumable_screen_run


class DomainRepairTests(DomainRecallHarness):
    def _source(self, *, damaged=False):
        return self._new_source(
            jobs=[_domain_job("job-1")],
            screening_fields={"industry": [BOSS_INTERNET_CODE]},
            track_snapshot={"snapshotVersion": 2, "junk": 1} if damaged else
            self._track_snapshot(groups=[INTERNET_GROUP], platform_labels=["互联网"]),
        )

    def _paused_run(self, scrape_id, fields, *, run_id="previous", labels=None):
        selection = policy.DomainSelection(
            labels=tuple(labels or [INTERNET_GROUP]),
            source_kind=policy.SOURCE_ORIGINAL_GROUP,
        )
        self.store.create_screening_run(
            run_id, frozen_filters=fields, source_count=1, profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "scrape_task_id": scrape_id,
                "profile_summary": "3 年 Python 后端，期望上海",
                "profile_facts": {"core_skills": ["Python"]},
                **policy.run_metadata(selection),
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status="paused")
        return run_id

    def _assert_damaged_snapshot_closure(self):
        ctx, flow, source, fields = self._source(damaged=True)
        ctx.release_worker_resume_claims = mock.Mock()
        ctx.schedule_pipeline_task_cleanup = mock.Mock()
        # 正式 API 在提交 worker 前已经创建本轮持久化行。
        self.store.create_screening_run(
            "damaged-ai", profile_id=self.profile_id, frozen_filters=fields,
            execution_params={"platform": "boss", "scrape_task_id": source,
                              "flow_id": flow["id"], "track_id": flow["tracks"][0]["id"]},
        )
        calls = self._run(ctx, "damaged-ai", source, fields)
        run = self.store.get_screening_run("damaged-ai")
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        expected = FAILED_CODE_LABELS["filter_snapshot_incompatible"]
        self.assertEqual(calls, [])
        self.assertEqual(ctx.tasks["damaged-ai"]["status"], "failed")
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "filter_snapshot_incompatible")
        self.assertEqual(run["error_reason"], expected)
        self.assertEqual(track["status"], "failed")
        self.assertEqual(track["reason"], expected)
        self.assertIn("筛选条件", expected)
        ctx.release_worker_resume_claims.assert_called()
        ctx.schedule_pipeline_task_cleanup.assert_called()

    def test_damaged_snapshot_closes_task_run_track_and_releases_claim(self):
        self._assert_damaged_snapshot_closure()

    def test_failure_audit_error_cannot_prevent_durable_snapshot_closure(self):
        with mock.patch("webui.whitebox.WhiteboxService.finalize", side_effect=RuntimeError("audit failed")):
            self._assert_damaged_snapshot_closure()

    def test_automatic_resume_skips_same_code_with_different_original_group(self):
        _ctx, _flow, source, fields = self._source()
        self._paused_run(source, fields, labels=["游戏/数字文娱"])
        candidate = find_resumable_screen_run(
            self.store, source, fields, "3 年 Python 后端，期望上海",
            {"core_skills": ["Python"]},
        )
        self.assertIsNone(candidate)

    def test_automatic_resume_preserves_compatible_original_group(self):
        _ctx, _flow, source, fields = self._source()
        previous = self._paused_run(source, fields)
        candidate = find_resumable_screen_run(
            self.store, source, fields, "3 年 Python 后端，期望上海",
            {"core_skills": ["Python"]},
        )
        self.assertEqual(candidate["id"], previous)

    def test_damaged_snapshot_is_left_for_worker_durable_failure(self):
        _ctx, _flow, source, fields = self._source(damaged=True)
        self._paused_run(source, fields)
        self.assertIsNone(find_resumable_screen_run(
            self.store, source, fields, "3 年 Python 后端，期望上海",
            {"core_skills": ["Python"]},
        ))

    def test_cached_jd_survives_fine_pause_and_same_run_restart(self):
        from webui.ai import AISecurityError, ERROR_NETWORK

        ctx, _flow, source, fields = self._source()
        previous = self._paused_run(source, fields)
        old_path = pathlib.Path(ctx.jd_checkpoint_path(str(self.root), previous))
        original = {"job-1": "完整的继承 JD：公司主营 AI 推理软件平台"}
        old_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
        with mock.patch("webui.ai.match_jds", side_effect=AISecurityError(ERROR_NETWORK)):
            self._run(ctx, "cached-ai", source, fields, resume_from_run_id=previous)
        self.assertEqual(self.store.get_screening_run("cached-ai")["status"], "paused")
        new_path = pathlib.Path(ctx.jd_checkpoint_path(str(self.root), "cached-ai"))
        self.assertTrue(new_path.exists(), "精筛暂停前继承资料必须已落盘")
        self.assertEqual(json.loads(new_path.read_text(encoding="utf-8")), original)
        self.assertTrue(old_path.exists(), "旧资料保持只读")
        # 正式继续入口先认领 paused -> running，再启动同 ID worker。
        self.store.update_screening_run("cached-ai", status="running")
        ctx.tasks["cached-ai"]["status"] = "queued"
        calls = self._run(ctx, "cached-ai", source, fields, resume_from_run_id="cached-ai")
        self.assertEqual(self.last_detail_calls, [])
        fine = [call["user"] for call in calls if call["stage"] == "fine"]
        self.assertTrue(fine)
        self.assertIn(original["job-1"], fine[0])

    def test_missing_context_never_licenses_domain_verdict_reuse(self):
        params = policy.run_metadata(policy.DomainSelection(
            labels=(INTERNET_GROUP,), source_kind=policy.SOURCE_ORIGINAL_GROUP,
        ))
        self.assertFalse(policy.is_verdict_state_compatible(
            params, screening_fields={"industry": [BOSS_INTERNET_CODE]},
        ))


class NoIndustryInputTests(unittest.TestCase):
    """无选择与 B094 实施前 HEAD 的实际输入比较，禁止新函数自比当旧基线。"""

    def test_domain_version_does_not_change_no_industry_resume(self):
        params = {policy.METADATA_VERSION_KEY: "older-domain-version",
                  policy.METADATA_SUMMARY_KEY: "old-summary"}
        self.assertTrue(policy.is_verdict_state_compatible(
            params, selection=policy.empty_selection(), screening_fields={},
        ))
        self.assertTrue(policy.is_verdict_state_compatible(params, screening_fields={}))

    def test_rough_prompt_equals_frozen_pre_b094_input_on_both_platforms(self):
        from webui.ai import screen_jobs

        expected = {
            "boss": "55ff88b6cf372dbdf7e2d95f9571ed631042f1140e989d856465dcd616186036",
            "zhilian": "d8a8c965d0373ac1741b73e426476c4d00c3be74039a19d993f20e968dbf0d46",
        }
        for platform, digest in expected.items():
            with self.subTest(platform=platform), mock.patch("webui.ai.call_ai", return_value={"dropped": []}) as call:
                screen_jobs([_domain_job("j1", platform=platform)], {}, "https://ai.invalid", "test",
                            batch_size=1, concurrency=1, platform=platform)
                prompt = call.call_args.args[2][0]["content"]
                self.assertEqual(hashlib.sha256(prompt.encode("utf-8")).hexdigest(), digest)

    def test_fine_prompt_equals_frozen_pre_b094_input(self):
        from webui.ai_prompts import build_match_system_prompt

        prompt = build_match_system_prompt(
            criteria_desc="（无明确标准，宽松判断）", profile_summary="Python后端",
            facts_desc="（无）", features_prompt_text="岗位靠谱特征清单",
            hard_fields_text="薪资/经验/学历/规模/融资/行业",
        )
        self.assertEqual(hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                         "1a061107a00980297b3a6acc07c5c444e21ae1a74446c4c165a53d95787cbdbb")

    def test_no_industry_fine_payload_keeps_legacy_fields_on_both_platforms(self):
        from webui.ai import match_jds

        for platform in ("boss", "zhilian"):
            with self.subTest(platform=platform), mock.patch("webui.ai.call_ai", return_value={
                "results": [{"i": 0, "match": True, "reason": "匹配", "caveats": [], "flags": []}],
            }) as call:
                job = _domain_job("j1", platform=platform, jd="J" * 1700)
                match_jds([job], "Python后端", "https://ai.invalid", "test",
                          criteria={}, platform=platform, batch_size=1, concurrency=1)
                item = json.loads(call.call_args.args[2][1]["content"])[0]
                expected = {"i", "title", "salary", "location", "tags", "jd"}
                if platform == "zhilian":
                    expected |= {"experience", "degree", "company_scale", "industry", "company_nature"}
                self.assertEqual(set(item), expected)
                self.assertEqual(item["jd"], "J" * 1500)


class LegacyDomainCallerTests(unittest.TestCase):
    """重抓/调优等旧调用只携带实际行业条件，也不得退回分类硬剔除。"""

    def test_selected_industry_without_internal_context_keeps_domain_input(self):
        from webui.ai import match_jds

        with mock.patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "匹配", "caveats": [], "flags": []}],
        }) as call:
            match_jds([_domain_job("j1", jd="J" * 1700)], "Python后端", "https://ai.invalid", "test",
                      criteria={"industry": [BOSS_INTERNET_CODE]}, platform="boss", batch_size=1, concurrency=1)
        system = call.call_args.args[2][0]["content"]
        item = json.loads(call.call_args.args[2][1]["content"])[0]
        self.assertIn("【领域条件】", system)
        self.assertIn("行业分类不是硬约束", system)
        self.assertEqual(item["jd"], "J" * 1700)
        self.assertEqual(item["company"], "示例科技")

    def test_explicit_empty_internal_selection_does_not_revive_industry(self):
        from webui.ai import match_jds

        with mock.patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "匹配", "caveats": [], "flags": []}],
        }) as call:
            match_jds([_domain_job("j1", jd="J" * 1700)], "Python后端", "https://ai.invalid", "test",
                      criteria={"industry": [BOSS_INTERNET_CODE], policy.CRITERIA_DOMAIN_KEY: {"labels": []}},
                      platform="boss", batch_size=1, concurrency=1)
        system = call.call_args.args[2][0]["content"]
        self.assertNotIn("【领域条件】", system)

    def test_rough_type_conflict_requires_title_and_profile_evidence(self):
        from webui.ai import screen_jobs

        jobs = [_domain_job("j1", title="销售助理（AI心理产品）"),
                _domain_job("j2", title="软件开发实习生")]
        for summary, expected in (("只找全职", ["j1"]), ("接受实习", ["j1", "j2"])):
            with self.subTest(summary=summary), mock.patch("webui.ai.call_ai", return_value={
                "dropped": [{"i": 0, "reason": "实习岗≠全职"}, {"i": 1, "reason": "实习岗≠全职"}],
            }):
                result = screen_jobs(jobs, {"industry": [BOSS_INTERNET_CODE], "profile_summary": summary},
                                     "https://ai.invalid", "test", platform="boss", batch_size=2, concurrency=1)
                self.assertEqual(result["kept"], expected)

    def test_rough_hard_reason_requires_selected_condition_and_job_conflict(self):
        from webui.ai import screen_jobs

        for platform in ("boss", "zhilian"):
            unrestricted = "0" if platform == "boss" else "-99"
            for fields in ({}, {"experience": [unrestricted]}, {"experience": ["1-3年"]}):
                with self.subTest(platform=platform, fields=fields), mock.patch("webui.ai.call_ai", return_value={
                    "dropped": [{"i": 0, "reason": "经验1-3年>候选0.9年"}],
                }):
                    result = screen_jobs([_domain_job("j1", platform=platform)],
                                         {**fields, "profile_summary": "系统计算：工作经验0.9年"},
                                         "https://ai.invalid", "test", platform=platform, concurrency=1)
                    self.assertEqual(result["kept"], ["j1"])

    def test_rough_cannot_invent_other_unselected_hard_dimensions(self):
        from webui.ai import screen_jobs

        for reason in ("学历本科>候选大专", "薪资15-25K<期望30K", "公司规模不符", "融资阶段不符",
                       "公司性质不符", "行业不相关", "领域不相关", "岗位类别新媒体运营≠AI应用开发",
                       "岗位方向不符", "不符合"):
            with self.subTest(reason=reason), mock.patch("webui.ai.call_ai", return_value={
                "dropped": [{"i": 0, "reason": reason}],
            }):
                result = screen_jobs([_domain_job("j1")], {"profile_summary": "不限条件，获取岗位详情后判断"},
                                     "https://ai.invalid", "test", platform="boss", concurrency=1)
                self.assertEqual(result["kept"], ["j1"])

    def test_confirmed_hard_conflict_is_still_dropped(self):
        from webui.ai import screen_jobs

        with mock.patch("webui.ai.call_ai", return_value={"dropped": []}):
            result = screen_jobs([_domain_job("j1", job_labels="3-5年 | 本科")],
                                 {"experience": ["1-3年"]}, "https://ai.invalid", "test", platform="boss")
        self.assertEqual(result["kept"], [])
        self.assertEqual([item["job_id"] for item in result["dropped"]], ["j1"])

    def test_fine_cannot_reject_by_nonexistent_selected_range(self):
        from webui.ai import match_jds

        for platform, fields in (("boss", {}), ("zhilian", {}),
                                 ("boss", {"experience": ["0"]}), ("zhilian", {"experience": ["-99"]})):
            for reason in ("薪资低于已选区间", "经验要求与已选区间冲突", "经验要求与筛选条件冲突"):
                invalid = {"results": [{"i": 0, "match": False, "reason": reason, "flags": []}]}
                corrected = {"results": [{"i": 0, "match": True, "reason": "AI应用开发匹配", "flags": []}]}
                for replies, expected, budget in (([invalid, corrected], "match", 0),
                                                  ([invalid, invalid], "uncertain", 0), ([invalid, invalid], "uncertain", 1)):
                    with self.subTest(platform=platform, reason=reason, expected=expected), mock.patch(
                        "webui.ai.call_ai", side_effect=replies,
                    ) as call:
                        result = match_jds([_domain_job("j1", jd="开发AI应用，Python")], "Python后端", "https://ai.invalid", "test",
                                           criteria=fields, platform=platform, concurrency=1,
                                           missing_result_retry_budget=budget)
                        self.assertEqual(result["verdicts"]["j1"]["verdict"], expected)
                        self.assertEqual(call.call_count, 2)
                        self.assertIn("不是已选区间", call.call_args.args[2][0]["content"])
                        from webui.ai_filters import _build_criteria_description
                        actual = _build_criteria_description(fields, platform) or "（无明确标准，宽松判断）"
                        self.assertIn(f"本轮第一层实际条件：{actual}", call.call_args.args[2][0]["content"])
                        if expected == "uncertain": self.assertIn("已选条件", result["verdicts"]["j1"]["reason"])

    def test_fine_real_jd_requirements_are_not_nonexistent_selected_range(self):
        from webui.ai import match_jds

        with mock.patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": False, "reason": "JD必须Java，与核心能力冲突", "flags": []}],
        }) as call:
            result = match_jds([_domain_job("j1", jd="要求必须掌握Java")], "Python后端", "https://ai.invalid", "test",
                               criteria={}, platform="boss", concurrency=1)
        self.assertEqual(result["verdicts"]["j1"]["verdict"], "not_match")
        self.assertEqual(call.call_count, 1)
