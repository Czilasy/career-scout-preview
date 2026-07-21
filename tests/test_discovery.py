"""Unit tests for webui.discovery and webui.semantic (feature 004)."""

from __future__ import annotations

import json
import unittest

from webui import semantic


def _valid_job_assessment():
    return {
        "dimensions": {
            "direction_alignment": {"score": 80, "candidate_evidence_refs": ["e1"], "job_evidence_refs": ["title"]},
            "skill_coverage": {"score": 75, "candidate_evidence_refs": ["e1"], "job_evidence_refs": ["tags"]},
            "experience_match": {"score": 70, "candidate_evidence_refs": ["e2"], "job_evidence_refs": ["jd"]},
            "industry_relevance": {"score": 65, "candidate_evidence_refs": ["e1"], "job_evidence_refs": ["company"]},
        },
        "match_score": 78,
        "confidence": 85,
        "gaps": [
            {"text": "缺少大数据经验", "job_evidence_refs": ["jd"]},
        ],
        "proposed_band": "high",
    }


class DiscoveryPolicyV2Tests(unittest.TestCase):
    """T012/T013: fixed v2 limits and non-mutating v1 compatibility."""

    def test_v2_constants_match_the_frozen_policy(self):
        from webui.discovery import DiscoveryPolicyV2

        policy = DiscoveryPolicyV2()
        self.assertEqual(policy.policy_version, "discovery_v2")
        self.assertEqual(policy.default_detail_budget, 15)
        self.assertEqual(policy.min_detail_budget, 12)
        self.assertEqual(policy.max_detail_budget, 20)
        self.assertEqual(policy.max_detail_batch_size, 5)
        self.assertLessEqual(policy.max_detail_batch_size, 5)
        self.assertEqual(policy.default_source_concurrency, 1)
        self.assertEqual(policy.max_source_concurrency, 2)
        self.assertEqual(policy.detail_ttl_hours, 12)
        self.assertEqual(policy.poll_interval_seconds, 3)

    def test_v2_budget_accepts_only_the_frozen_range(self):
        from webui.discovery import DiscoveryPolicyV2

        policy = DiscoveryPolicyV2()
        for value in (12, 15, 20):
            self.assertEqual(policy.validate_detail_budget(value), value)
        for value in (11, 21, True, 12.5, "15"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    policy.validate_detail_budget(value)
        self.assertEqual(policy.validate_detail_budget(None), 15)

    def test_policy_resolution_keeps_v1_history_unchanged(self):
        from webui.discovery import DiscoveryPolicyV1Adapter, policy_for_run

        historical_run = {
            "id": "legacy-run",
            "policy_version": "v1",
            "detail_budget": 60,
            "input_hash": "legacy-hash",
        }
        before = dict(historical_run)
        policy = policy_for_run(historical_run)

        self.assertIsInstance(policy, DiscoveryPolicyV1Adapter)
        self.assertEqual(policy.policy_version, "v1")
        self.assertEqual(policy.default_detail_budget, 60)
        self.assertEqual(policy.max_detail_budget, 200)
        self.assertEqual(historical_run, before)

    def test_missing_legacy_policy_uses_v1_but_005_uses_discovery_v2(self):
        from webui.discovery import (
            DiscoveryPolicyV1Adapter,
            DiscoveryPolicyV2,
            policy_for_run,
            resolve_discovery_policy,
        )

        self.assertIsInstance(resolve_discovery_policy(None), DiscoveryPolicyV1Adapter)
        self.assertIsInstance(resolve_discovery_policy("v1"), DiscoveryPolicyV1Adapter)
        self.assertIsInstance(resolve_discovery_policy("discovery_v2"), DiscoveryPolicyV2)
        self.assertIsInstance(
            policy_for_run({"policy_version": "discovery_v2"}),
            DiscoveryPolicyV2,
        )
        with self.assertRaises(ValueError):
            resolve_discovery_policy("discovery_v3")


class JobAssessmentContractTests(unittest.TestCase):
    """T012: job-direction assessment AI output contract v1 validation."""

    def setUp(self):
        self.analysis_evidence = {"e1", "e2", "e3"}
        self.direction_evidence = {"e1", "e2"}
        self.snapshot_fields = {"title", "tags", "jd", "company"}

    def test_valid_assessment_returns_sanitized(self):
        result = semantic.validate_job_assessment(
            _valid_job_assessment(),
            self.analysis_evidence,
            self.direction_evidence,
            self.snapshot_fields,
        )
        self.assertEqual(result["contract_version"], "v1")
        self.assertEqual(result["match_score"], 78)
        self.assertEqual(result["confidence"], 85)
        self.assertEqual(result["proposed_band"], "high")
        self.assertIsNone(result["failure_stage"])

    def test_non_dict_returns_needs_review(self):
        result = semantic.validate_job_assessment(
            "not a dict",
            self.analysis_evidence,
            self.direction_evidence,
            self.snapshot_fields,
        )
        self.assertEqual(result["category"], "needs_review")
        self.assertEqual(result["failure_stage"], "ai_invalid_output")

    def test_missing_dimension_returns_needs_review(self):
        data = _valid_job_assessment()
        del data["dimensions"]["skill_coverage"]
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        self.assertEqual(result["category"], "needs_review")

    def test_extra_dimension_returns_needs_review(self):
        data = _valid_job_assessment()
        data["dimensions"]["extra_dim"] = {"score": 50}
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        self.assertEqual(result["category"], "needs_review")

    def test_bool_score_rejected(self):
        data = _valid_job_assessment()
        data["dimensions"]["direction_alignment"]["score"] = True
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        self.assertEqual(result["category"], "needs_review")

    def test_fractional_score_rejected(self):
        data = _valid_job_assessment()
        data["match_score"] = 78.5
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        self.assertEqual(result["category"], "needs_review")
        self.assertEqual(result["failure_stage"], "ai_invalid_output")

    def test_out_of_range_score_rejected(self):
        data = _valid_job_assessment()
        data["match_score"] = 150
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        self.assertEqual(result["category"], "needs_review")

    def test_candidate_evidence_must_belong_to_direction(self):
        data = _valid_job_assessment()
        # e3 is in analysis but not in direction -> must be rejected
        data["dimensions"]["direction_alignment"]["candidate_evidence_refs"] = ["e3"]
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        self.assertEqual(result["category"], "needs_review")

    def test_unknown_candidate_evidence_rejected(self):
        data = _valid_job_assessment()
        data["dimensions"]["direction_alignment"]["candidate_evidence_refs"] = ["eX"]
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        self.assertEqual(result["category"], "needs_review")

    def test_invalid_candidate_evidence_reports_reference_failure(self):
        data = _valid_job_assessment()
        data["dimensions"]["direction_alignment"]["candidate_evidence_refs"] = ["e3"]
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        self.assertEqual(result["category"], "needs_review")
        self.assertEqual(result["failure_stage"], "evidence_reference_invalid")

    def test_unknown_job_evidence_rejected(self):
        data = _valid_job_assessment()
        data["dimensions"]["direction_alignment"]["job_evidence_refs"] = ["unknown_field"]
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        self.assertEqual(result["category"], "needs_review")

    def test_low_confidence_returns_needs_review(self):
        data = _valid_job_assessment()
        data["confidence"] = 50
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        self.assertEqual(result["category"], "needs_review")
        self.assertEqual(result["failure_stage"], "ai_uncertain")
        # Still preserves scores for debugging
        self.assertEqual(result["confidence"], 50)

    def test_invalid_proposed_band_rejected(self):
        data = _valid_job_assessment()
        data["proposed_band"] = "perfect"
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        self.assertEqual(result["category"], "needs_review")

    def test_proposed_band_is_advisory(self):
        """proposed_band is advisory; program derives category from policy."""
        data = _valid_job_assessment()
        data["proposed_band"] = "unsuitable"
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        # Valid contract, high confidence -> category is None (caller decides)
        self.assertIsNone(result["category"])
        self.assertEqual(result["proposed_band"], "unsuitable")

    def test_gap_job_evidence_validated(self):
        data = _valid_job_assessment()
        data["gaps"].append({"text": "gap", "job_evidence_refs": ["nonexistent"]})
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        self.assertEqual(result["category"], "needs_review")

    def test_no_raw_model_text_persisted(self):
        data = _valid_job_assessment()
        data["model_raw_text"] = "sensitive raw response"
        result = semantic.validate_job_assessment(
            data, self.analysis_evidence, self.direction_evidence, self.snapshot_fields,
        )
        # Unknown fields are not echoed back
        self.assertNotIn("model_raw_text", result)


class SearchPlanCompilationTests(unittest.TestCase):
    """T031: search plan compilation."""

    def test_deduplicates_terms_and_preserves_direction_attribution(self):
        from webui.discovery import compile_search_plan

        confirmation = {
            "enabled_directions": [
                {"id": "d1", "name": "后端", "search_terms": ["Python", "平台工程"]},
                {"id": "d2", "name": "数据", "search_terms": ["Python", "数据开发"]},
            ],
            "hard_constraints": {"city": "北京"},
            "safe_limits": {"max_details": 17},
        }
        plan = compile_search_plan(confirmation)

        self.assertEqual([item["term"] for item in plan["items"]], ["Python", "平台工程", "数据开发"])
        shared = next(item for item in plan["items"] if item["term"] == "Python")
        self.assertEqual(shared["direction_ids"], ["d1", "d2"])
        self.assertEqual(shared["hard_constraints"], {"city": "北京"})
        self.assertEqual(plan["detail_budget"], 17)
        self.assertEqual(len(plan["input_hash"]), 64)

    def test_enforces_per_direction_and_global_limits_with_every_direction_covered(self):
        from webui.discovery import compile_search_plan

        directions = [
            {"id": f"d{index}", "name": f"方向{index}",
             "search_terms": [f"词{index}-{term}" for term in range(5)]}
            for index in range(4)
        ]
        plan = compile_search_plan({"enabled_directions": directions})

        self.assertLessEqual(len(plan["items"]), 12)
        for direction in directions:
            attributed = [
                item for item in plan["items"]
                if direction["id"] in item["direction_ids"]
            ]
            self.assertGreaterEqual(len(attributed), 1)
            self.assertLessEqual(len(attributed), 3)

    def test_input_hash_changes_when_search_boundary_changes(self):
        from webui.discovery import compile_search_plan

        base = {"enabled_directions": [{"id": "d1", "search_terms": ["Python"]}]}
        first = compile_search_plan(base)
        second = compile_search_plan({**base, "hard_constraints": {"city": "北京"}})

        self.assertNotEqual(first["input_hash"], second["input_hash"])

    def test_scraper_inputs_are_allowlisted_from_confirmation_only(self):
        from webui.discovery import compile_search_plan
        confirmation = {
            "enabled_directions": [{"id": "d1", "search_terms": ["Python", "13812345678", ""]}],
            "hard_constraints": {
                "city": "上海", "salary": "20-30K", "experience": "5", "degree": "本科",
                "industry": "互联网", "scale": "100-499", "stage": "已上市",
                "phone": "13812345678", "full_name": "张三", "resume_text": "SECRET_RESUME",
                "quality_warnings": [{"code": "x", "path": "resume"}], "unconfirmed_role": "Java",
            },
            "safe_limits": {"max_details": 9999, "max_pages": 99, "raw_limit": 999},
            "evidence": [{"safe_excerpt": "SECRET_EXCERPT"}],
            "unconfirmed_suggestions": {"city": "北京"},
        }
        plan = compile_search_plan(confirmation)
        self.assertEqual(plan["items"][0]["term"], "Python")
        self.assertEqual(set(plan["hard_constraints"]), {"city", "salary", "experience", "degree", "industry", "scale", "stage"})
        self.assertEqual(plan["detail_budget"], 200)
        self.assertEqual(plan["safe_limits"], {"max_details": 200, "max_pages": 10})
        serialized = json.dumps(plan, ensure_ascii=False)
        for secret in ("13812345678", "张三", "SECRET_RESUME", "SECRET_EXCERPT", "Java", "quality_warnings"):
            self.assertNotIn(secret, serialized)

    def test_empty_confirmed_filters_are_omitted(self):
        from webui.discovery import compile_search_plan
        plan = compile_search_plan({
            "enabled_directions": [{"id": "d1", "search_terms": ["Python"]}],
            "hard_constraints": {"city": "", "salary": None, "industry": []},
        })
        self.assertEqual(plan["hard_constraints"], {})

    def test_bounded_max_pages_reaches_materialized_source_item(self):
        from webui.discovery import compile_search_plan
        from webui.discovery_runner import DiscoveryRunner
        compiled = compile_search_plan({
            "enabled_directions": [{"id": "d1", "search_terms": ["Python"]}],
            "safe_limits": {"max_pages": 99},
        })
        items = DiscoveryRunner.__new__(DiscoveryRunner)._materialize_plan_items(compiled, "run")
        self.assertEqual(items[0]["target_pages"], 10)


class SearchPlanV2ContractTests(unittest.TestCase):
    """T035: multi-direction search plan compilation contract for policy v2."""

    def test_direction_without_search_terms_falls_back_to_name(self):
        """用户无需输入关键词：方向无 search_terms 时自动使用方向名称。"""
        from webui.discovery import compile_search_plan

        confirmation = {
            "enabled_directions": [
                {"id": "d1", "name": "Go后端开发", "search_terms": []},
                {"id": "d2", "name": "云原生架构"},
            ],
        }
        plan = compile_search_plan(confirmation)
        terms = [item["term"] for item in plan["items"]]
        self.assertIn("Go后端开发", terms)
        self.assertIn("云原生架构", terms)
        self.assertEqual(len(plan["items"]), 2)

    def test_global_cap_is_twelve_with_five_directions(self):
        """5 方向 × 3 词 = 15 候选项，全局上限截断为 ≤12。"""
        from webui.discovery import compile_search_plan

        directions = [
            {"id": f"d{i}", "name": f"方向{i}",
             "search_terms": [f"term-{i}-{j}" for j in range(3)]}
            for i in range(5)
        ]
        plan = compile_search_plan({"enabled_directions": directions})
        self.assertLessEqual(len(plan["items"]), 12)

    def test_per_direction_floor_under_global_cap_pressure(self):
        """全局上限压力下每个启用方向仍至少分配 1 个搜索项。"""
        from webui.discovery import compile_search_plan

        directions = [
            {"id": f"d{i}", "name": f"方向{i}",
             "search_terms": [f"unique-{i}-{j}" for j in range(3)]}
            for i in range(5)
        ]
        plan = compile_search_plan({"enabled_directions": directions})
        covered = {did for item in plan["items"] for did in item["direction_ids"]}
        for direction in directions:
            self.assertIn(direction["id"], covered)

    def test_input_hash_is_deterministic_for_same_input(self):
        """相同输入产生相同 input_hash（稳定、可重放）。"""
        from webui.discovery import compile_search_plan

        confirmation = {
            "enabled_directions": [
                {"id": "d1", "name": "后端", "search_terms": ["Python", "Go"]},
                {"id": "d2", "name": "数据", "search_terms": ["Python", "Spark"]},
            ],
            "hard_constraints": {"city": "上海"},
        }
        first = compile_search_plan(confirmation)
        second = compile_search_plan(confirmation)
        self.assertEqual(first["input_hash"], second["input_hash"])
        self.assertEqual(len(first["input_hash"]), 64)

    def test_shared_term_merging_preserves_all_direction_attributions(self):
        """共享词合并后保留所有方向归属。"""
        from webui.discovery import compile_search_plan

        confirmation = {
            "enabled_directions": [
                {"id": "d1", "search_terms": ["Python", "后端"]},
                {"id": "d2", "search_terms": ["Python", "数据"]},
                {"id": "d3", "search_terms": ["Python", "AI"]},
            ],
        }
        plan = compile_search_plan(confirmation)
        python_item = next(item for item in plan["items"] if item["term"] == "Python")
        self.assertEqual(sorted(python_item["direction_ids"]), ["d1", "d2", "d3"])
        # Python only appears once in items
        python_items = [item for item in plan["items"] if item["term"] == "Python"]
        self.assertEqual(len(python_items), 1)

    def test_no_enabled_directions_raises_input_incomplete(self):
        """无启用方向时抛出 input_incomplete。"""
        from webui.discovery import compile_search_plan, DiscoveryError

        with self.assertRaises(DiscoveryError) as ctx:
            compile_search_plan({"enabled_directions": []})
        self.assertEqual(ctx.exception.error_code, "input_incomplete")


class ListCandidatePrecheckTests(unittest.TestCase):
    """T039: list-field tri-state hard constraint precheck before detail budget."""

    def _precheck(self, list_fields, hard_constraints):
        from webui.discovery import precheck_list_candidate
        return precheck_list_candidate(list_fields, hard_constraints)

    def test_salary_below_min_is_violation(self):
        """岗位薪资上限低于用户最低薪资 → violation。"""
        result = self._precheck(
            {"salary": "10-15K", "location": "上海"},
            {"min_salary": {"amount": 20, "unit": "K", "pay_period": "month", "source": "user_confirmed"}},
        )
        self.assertEqual(result["outcome"], "violation")
        salary_check = next(c for c in result["checks"] if c["field"] == "min_salary")
        self.assertEqual(salary_check["outcome"], "violation")

    def test_salary_above_min_is_pass(self):
        """岗位薪资下限≥用户最低薪资 → pass。"""
        result = self._precheck(
            {"salary": "25-35K"},
            {"min_salary": {"amount": 20, "unit": "K", "pay_period": "month", "source": "user_confirmed"}},
        )
        self.assertEqual(result["outcome"], "pass")

    def test_salary_unparseable_is_unknown_not_pass(self):
        """面议/不可解析薪资 → unknown，不冒充 pass。"""
        result = self._precheck(
            {"salary": "面议"},
            {"min_salary": {"amount": 20, "unit": "K", "pay_period": "month", "source": "user_confirmed"}},
        )
        self.assertEqual(result["outcome"], "unknown")

    def test_salary_missing_is_unknown(self):
        """薪资字段缺失 → unknown。"""
        result = self._precheck(
            {"title": "后端开发"},
            {"min_salary": {"amount": 20, "unit": "K", "pay_period": "month", "source": "user_confirmed"}},
        )
        self.assertEqual(result["outcome"], "unknown")

    def test_city_mismatch_is_violation(self):
        """城市不匹配 → violation。"""
        result = self._precheck(
            {"location": "北京·海淀", "salary": "30K"},
            {"city": "上海"},
        )
        self.assertEqual(result["outcome"], "violation")

    def test_city_match_is_pass(self):
        """城市匹配 → pass。"""
        result = self._precheck(
            {"location": "上海·浦东"},
            {"city": "上海"},
        )
        self.assertEqual(result["outcome"], "pass")

    def test_city_missing_is_unknown(self):
        """城市字段缺失 → unknown。"""
        result = self._precheck(
            {"title": "远程开发"},
            {"city": "上海"},
        )
        self.assertEqual(result["outcome"], "unknown")

    def test_no_constraints_is_pass(self):
        """无硬约束 → pass（无需验证）。"""
        result = self._precheck({"salary": "10K", "location": "北京"}, {})
        self.assertEqual(result["outcome"], "pass")

    def test_violation_forces_excluded_not_selected(self):
        """violation 结果的候选不得被 selected。"""
        result = self._precheck(
            {"salary": "8-12K"},
            {"min_salary": {"amount": 20, "unit": "K", "pay_period": "month", "source": "user_confirmed"}},
        )
        self.assertEqual(result["outcome"], "violation")
        self.assertTrue(result.get("exclude", False))

    def test_unknown_does_not_force_exclusion(self):
        """unknown 不强制排除，但标记需要后续确认。"""
        result = self._precheck(
            {"salary": "面议"},
            {"min_salary": {"amount": 20, "unit": "K", "pay_period": "month", "source": "user_confirmed"}},
        )
        self.assertEqual(result["outcome"], "unknown")
        self.assertFalse(result.get("exclude", False))

    def test_invalid_source_excluded_without_budget(self):
        """无有效来源链接 → 排除且不耗预算。"""
        from webui.discovery import precheck_list_candidate
        result = precheck_list_candidate(
            {"title": "后端"}, {},
            source_status="invalid",
        )
        self.assertEqual(result["outcome"], "violation")
        self.assertTrue(result["exclude"])
        self.assertEqual(result["reason"], "invalid_source")

    def test_closed_job_excluded_without_budget(self):
        """已关闭岗位 → 排除且不耗预算。"""
        from webui.discovery import precheck_list_candidate
        result = precheck_list_candidate(
            {"title": "后端"}, {},
            source_status="closed",
        )
        self.assertEqual(result["outcome"], "violation")
        self.assertTrue(result["exclude"])
        self.assertEqual(result["reason"], "source_closed")

    def test_feedback_excluded_without_budget(self):
        """用户反馈排除 → 不耗预算。"""
        from webui.discovery import precheck_list_candidate
        result = precheck_list_candidate(
            {"title": "后端"}, {},
            feedback_excluded=True,
        )
        self.assertEqual(result["outcome"], "violation")
        self.assertTrue(result["exclude"])
        self.assertEqual(result["reason"], "feedback_excluded")

    def test_n_salary_and_daily_salary_are_unknown(self):
        """N薪和日薪格式无法直接比较月薪下限 → unknown。"""
        for salary_str in ("200/天", "15-20K·16薪"):
            result = self._precheck(
                {"salary": salary_str},
                {"min_salary": {"amount": 20, "unit": "K", "pay_period": "month", "source": "user_confirmed"}},
            )
            self.assertIn(result["outcome"], ("unknown", "pass"),
                          f"{salary_str} should not be violation")


class JobSnapshotTests(unittest.TestCase):
    """T039: job detail snapshot completeness. (Implemented in Phase 4)"""

    def test_source_status_uses_contract_enum(self):
        """CD-1: source_status must use active/unknown/closed/unreachable per data-model."""
        from webui.discovery import build_snapshot, SNAPSHOT_SOURCE_STATUS
        self.assertEqual(set(SNAPSHOT_SOURCE_STATUS), {"active", "unknown", "closed", "unreachable"})
        # complete snapshot -> active
        snap = build_snapshot(
            {"job_id": "j1", "title": "后端", "company": "ACME", "jd": "Python"},
            {"tags": "Python"},
        )
        self.assertEqual(snap["source_status"], "active")
        # unavailable -> unreachable
        snap2 = build_snapshot({"job_id": "j2", "title": "", "company": "", "jd": ""}, {})
        self.assertEqual(snap2["source_status"], "unreachable")
        # expired -> closed
        snap3 = build_snapshot(
            {"job_id": "j3", "title": "后端", "company": "ACME", "jd": "Python"},
            {"expired": True},
        )
        self.assertEqual(snap3["source_status"], "closed")


class AssessmentPolicyTests(unittest.TestCase):
    """T041/T042: tri-state hard rules + semantic assessment -> category."""

    def test_hard_rule_violation_overrides_high_ai_score(self):
        from webui.discovery import assess_job_direction

        result = assess_job_direction(
            {
                "completeness": "complete",
                "fields": {"title": "后端工程师", "company": "ACME", "jd": "Python", "location": "上海"},
            },
            {"evidence_refs": ["e1"], "analysis_evidence_ids": ["e1"]},
            _valid_job_assessment(),
            hard_constraints={"city": "北京"},
        )

        self.assertEqual(result["hard_rule_outcome"], "violation")
        self.assertEqual(result["category"], "not_suitable")
        self.assertIsNone(result["ai_assessment"])

    def test_unknown_hard_rule_is_needs_review(self):
        from webui.discovery import assess_job_direction

        result = assess_job_direction(
            {"completeness": "complete", "fields": {"title": "后端工程师", "company": "ACME", "jd": "Python"}},
            {"evidence_refs": ["e1"], "analysis_evidence_ids": ["e1"]},
            _valid_job_assessment(),
            hard_constraints={"city": "北京"},
        )

        self.assertEqual(result["hard_rule_outcome"], "unknown")
        self.assertEqual(result["category"], "needs_review")

    def test_valid_ai_bands_map_to_independent_categories(self):
        from webui.discovery import assess_job_direction

        snapshot = {
            "completeness": "complete",
            "fields": {"title": "后端工程师", "company": "ACME", "jd": "Python", "tags": "Python"},
        }
        direction = {"evidence_refs": ["e1", "e2"], "analysis_evidence_ids": ["e1", "e2"]}
        for band, expected in (
            ("high", "high_match"),
            ("adjacent", "adjacent_match"),
            ("growth", "growth_match"),
            ("unsuitable", "not_suitable"),
        ):
            with self.subTest(band=band):
                proposal = _valid_job_assessment()
                proposal["proposed_band"] = band
                result = assess_job_direction(snapshot, direction, proposal)
                self.assertEqual(result["category"], expected)

    def test_missing_detail_low_confidence_and_ai_unavailable_are_needs_review(self):
        from webui.discovery import assess_job_direction

        direction = {"evidence_refs": ["e1"], "analysis_evidence_ids": ["e1"]}
        unavailable = assess_job_direction(
            {"completeness": "unavailable", "fields": {}}, direction,
            _valid_job_assessment(),
        )
        no_ai = assess_job_direction(
            {"completeness": "complete", "fields": {"title": "后端", "company": "A", "jd": "Python"}},
            direction, None,
        )
        low = _valid_job_assessment()
        low["confidence"] = 20
        low_confidence = assess_job_direction(
            {"completeness": "complete", "fields": {"title": "后端", "company": "A", "jd": "Python", "tags": "Python"}},
            direction, low,
        )

        self.assertEqual(unavailable["category"], "needs_review")
        self.assertEqual(no_ai["category"], "needs_review")
        self.assertEqual(low_confidence["category"], "needs_review")

    def test_entry_level_job_cannot_be_high_match_for_experienced_candidate(self):
        from webui.discovery import assess_job_direction

        result = assess_job_direction(
            {
                "completeness": "complete",
                "fields": {
                    "title": "大数据开发实习生",
                    "company": "ACME",
                    "jd": "面向在校生，参与数据开发项目",
                    "tags": "数据开发",
                },
            },
            {"evidence_refs": ["e1", "e2"], "analysis_evidence_ids": ["e1", "e2"]},
            _valid_job_assessment(),
            candidate_profile={"experience_level": "5年全职工作经验"},
        )

        self.assertEqual(result["category"], "needs_review")
        self.assertEqual(result["reason"], "experience_level_conflict")
        self.assertEqual(result["ai_assessment"]["match_score"], 78)


class PortfolioAssemblyTests(unittest.TestCase):
    """T043: portfolio assembly and diversity. (Phase 4)"""

    @staticmethod
    def _assessment(*, direction_id="d1", job_id="j1", company="ACME",
                    category="high_match", hard_outcome="pass",
                    completeness="complete", gaps=None, failure_code=None):
        return {
            "direction_id": direction_id,
            "job_id": job_id,
            "company": company,
            "title": "资深工程师",
            "category": category,
            "hard_outcome": hard_outcome,
            "snapshot_completeness": completeness,
            "match_score": 86,
            "confidence": 88,
            "dimensions": {
                "direction_alignment": {
                    "score": 85,
                    "candidate_evidence_refs": ["resume-e1"],
                    "job_evidence_refs": ["title", "jd"],
                },
                "skill_coverage": {
                    "score": 82,
                    "candidate_evidence_refs": ["resume-e2"],
                    "job_evidence_refs": ["tags"],
                },
            },
            "gaps": list(gaps or []),
            "failure_code": failure_code,
        }

    def test_high_match_requires_hard_pass_complete_detail_and_two_sided_evidence(self):
        """High recommendations are guarded again when persisted rows are assembled."""
        from webui.discovery import build_portfolio

        valid = self._assessment(job_id="valid", company="Valid")
        hard_unknown = self._assessment(job_id="hard", company="Hard", hard_outcome="unknown")
        partial = self._assessment(job_id="partial", company="Partial", completeness="partial")
        no_job_evidence = self._assessment(job_id="evidence", company="Evidence")
        no_job_evidence["dimensions"]["direction_alignment"]["job_evidence_refs"] = []
        no_job_evidence["dimensions"]["skill_coverage"]["job_evidence_refs"] = []

        portfolio = build_portfolio(
            "run-high-guard",
            [valid, hard_unknown, partial, no_job_evidence],
            [{"id": "d1", "name": "后端"}],
        )
        by_job = {item["job_id"]: item for item in portfolio["items"]}

        self.assertEqual(by_job["valid"]["category"], "high_match")
        self.assertEqual(by_job["hard"]["category"], "needs_review")
        self.assertEqual(by_job["partial"]["category"], "needs_review")
        self.assertEqual(by_job["evidence"]["category"], "needs_review")

    def test_adjacent_and_growth_explanations_expose_required_semantics(self):
        from webui.discovery import build_portfolio

        adjacent = self._assessment(
            direction_id="d1", job_id="adjacent", company="Adjacent",
            category="adjacent_match",
            gaps=[{"text": "行业场景不同", "job_evidence_refs": ["jd"]}],
        )
        growth = self._assessment(
            direction_id="d2", job_id="growth", company="Growth",
            category="growth_match",
            gaps=[{"text": "需要补足分布式系统经验", "job_evidence_refs": ["jd"]}],
        )

        portfolio = build_portfolio(
            "run-explanations", [adjacent, growth],
            [{"id": "d1", "name": "平台"}, {"id": "d2", "name": "架构"}],
        )
        by_job = {item["job_id"]: item for item in portfolio["items"]}

        self.assertTrue(by_job["adjacent"]["explanation"]["transferable"])
        self.assertTrue(by_job["adjacent"]["explanation"]["differences"])
        self.assertEqual(
            by_job["growth"]["explanation"]["gaps"][0]["text"],
            "需要补足分布式系统经验",
        )

    def test_recommended_results_cover_two_directions_and_keep_review_separate(self):
        from webui.discovery import build_portfolio

        assessments = [
            self._assessment(direction_id="d1", job_id="high", company="A"),
            self._assessment(direction_id="d2", job_id="adj", company="B", category="adjacent_match"),
            self._assessment(direction_id="d1", job_id="review", company="C", category="needs_review"),
            self._assessment(direction_id="d2", job_id="bad", company="D", category="not_suitable"),
        ]
        portfolio = build_portfolio(
            "run-diversity", assessments,
            [{"id": "d1", "name": "后端"}, {"id": "d2", "name": "数据"}],
        )

        recommended = [i for i in portfolio["items"] if i["category"] in {
            "high_match", "adjacent_match", "growth_match",
        }]
        self.assertEqual({i["direction_id"] for i in recommended}, {"d1", "d2"})
        self.assertEqual(portfolio["counts"]["needs_review"], 1)
        self.assertEqual(portfolio["counts"]["not_suitable"], 1)

    def test_no_qualified_result_reasons_are_distinct(self):
        from webui.discovery import build_portfolio

        assessments = [
            self._assessment(direction_id="hard", job_id="h", company="H",
                             category="not_suitable", hard_outcome="violation"),
            self._assessment(direction_id="detail", job_id="d", company="D",
                             category="needs_review", completeness="partial"),
            self._assessment(direction_id="match", job_id="m", company="M",
                             category="not_suitable"),
            self._assessment(direction_id="failure", job_id="f", company="F",
                             category="needs_review", failure_code="ai_timeout"),
        ]
        directions = [
            {"id": "none", "name": "未搜到"},
            {"id": "hard", "name": "硬约束"},
            {"id": "detail", "name": "详情不足"},
            {"id": "match", "name": "匹配不足"},
            {"id": "failure", "name": "执行失败"},
        ]

        result = build_portfolio("run-reasons", assessments, directions)["directions"]

        self.assertEqual(result["none"]["reason"], "not_found")
        self.assertEqual(result["hard"]["reason"], "hard_constraints_excluded")
        self.assertEqual(result["detail"]["reason"], "insufficient_detail")
        self.assertEqual(result["match"]["reason"], "insufficient_match")
        self.assertEqual(result["failure"]["reason"], "execution_failed")

    def test_high_match_kept_over_needs_review_same_company(self):
        """HI-1: same company, high_match must beat needs_review regardless of input order."""
        from webui.discovery import build_portfolio, CATEGORY_PRIORITY
        directions = [{"id": "d1", "name": "后端"}]
        # Two assessments for same company+direction, needs_review first
        assessments = [
            {
                "direction_id": "d1", "category": "needs_review", "company": "ACME",
                "job_id": "j_low", "ai_assessment": {"match_score": 30},
            },
            {
                "direction_id": "d1", "category": "high_match", "company": "ACME",
                "job_id": "j_high", "hard_outcome": "pass", "snapshot_completeness": "complete",
                "match_score": 85,
                "dimensions": {"skills": {"score": 85, "candidate_evidence_refs": ["e1"],
                                             "job_evidence_refs": ["jd"]}},
            },
        ]
        portfolio = build_portfolio("run-1", assessments, directions)
        companies_in_items = [it["company"] for it in portfolio["items"]]
        # high_match must be the one kept, not needs_review
        kept = [it for it in portfolio["items"] if it["company"] == "ACME"]
        self.assertEqual(len(kept), 1, "同公司同方向应去重为1条")
        self.assertEqual(kept[0]["category"], "high_match", "应保留 high_match 而非 needs_review")

    def test_keeps_categories_separate_and_reports_every_direction(self):
        from webui.discovery import build_portfolio

        directions = [{"id": "d1", "name": "后端"}, {"id": "d2", "name": "数据"}, {"id": "d3", "name": "安全"}]
        assessments = [
            self._assessment(direction_id="d1", category="adjacent_match", company="A", job_id="j1"),
            self._assessment(
                direction_id="d2", category="growth_match", company="B", job_id="j2",
                gaps=[{"text": "经验缺口", "job_evidence_refs": ["jd"]}],
            ),
            self._assessment(direction_id="d2", category="not_suitable", company="C", job_id="j3"),
        ]

        portfolio = build_portfolio("run-2", assessments, directions)

        self.assertEqual(portfolio["counts"]["adjacent_match"], 1)
        self.assertEqual(portfolio["counts"]["growth_match"], 1)
        self.assertEqual(portfolio["counts"]["not_suitable"], 1)
        self.assertEqual(portfolio["directions"]["d3"]["reason"], "not_found")
        self.assertEqual({item["direction_id"] for item in portfolio["items"]}, {"d1", "d2"})


class SafeExplanationTests(unittest.TestCase):
    """T045: safe explanation generation."""

    def test_redacts_pii_and_never_echoes_raw_model_or_resume_text(self):
        from webui.discovery import build_safe_explanation

        assessment = {
            "hard_rule_outcome": "pass",
            "resume_text": "完整简历正文不应出现",
            "ai_assessment": {
                "model_raw_text": "原始模型响应不应出现",
                "dimensions": {
                    "skill_coverage": {
                        "score": 80,
                        "candidate_evidence_refs": ["e1"],
                        "job_evidence_refs": ["tags"],
                    },
                },
                "gaps": [{"text": "联系 test@example.com 或 13800138000", "job_evidence_refs": ["jd"]}],
            },
        }
        explanation = build_safe_explanation(assessment)
        serialized = str(explanation)

        self.assertNotIn("test@example.com", serialized)
        self.assertNotIn("13800138000", serialized)
        self.assertNotIn("完整简历正文不应出现", serialized)
        self.assertNotIn("原始模型响应不应出现", serialized)
        self.assertEqual(explanation["dimensions"][0]["candidate_evidence_refs"], ["e1"])


class ConfirmDirectionsTests(unittest.TestCase):
    """T026: confirm_directions freezes immutable version."""

    def test_freezes_only_explicit_constraints_and_selected_directions(self):
        from webui.discovery import confirm_directions

        class FakeStore:
            def __init__(self):
                self.payload = None

            def get_analysis(self, analysis_id):
                return {"id": analysis_id, "status": "ready", "profile_id": "p1", "resume_id": "r1"}

            def list_directions(self, analysis_id):
                return [{"id": "d1"}, {"id": "d2"}]

            def create_confirmation(self, **payload):
                self.payload = payload
                return {"id": "c1", **payload}

        store = FakeStore()
        confirmation = confirm_directions(
            store,
            "a1",
            ["d2"],
            hard_constraints={"city": "北京", "salary": "", "company": None},
            soft_preferences={"industry": "AI"},
            safe_limits={"max_details": 20},
        )

        self.assertEqual(confirmation["hard_constraints"], {"city": "北京"})
        self.assertEqual(confirmation["directions"], [{
            "direction_id": "d2", "enabled": True, "user_added": False, "user_label": None,
        }])
        self.assertEqual(store.payload["analysis_id"], "a1")


class RunCompletionTests(unittest.TestCase):
    """T066: partial success and no-result-success calculation."""

    def test_usable_result_with_failed_branch_is_partial(self):
        from webui.discovery import calculate_run_completion

        result = calculate_run_completion(
            {"status": "assembling"},
            [{"status": "completed"}, {"status": "failed"}],
            [{"category": "high_match"}],
        )
        self.assertEqual(result, {"status": "partial", "reason": "some_branches_blocked", "usable_count": 1})

    def test_completed_search_with_only_unsuitable_results_is_successful_no_result(self):
        from webui.discovery import calculate_run_completion

        result = calculate_run_completion(
            {"status": "assembling"},
            [{"status": "completed"}],
            [{"category": "not_suitable"}],
        )
        self.assertEqual(result, {"status": "succeeded", "reason": "no_usable_results", "usable_count": 0})

    def test_blocked_branch_without_usable_result_cannot_succeed(self):
        from webui.discovery import calculate_run_completion

        result = calculate_run_completion(
            {"status": "assembling"},
            [{"status": "completed"}, {"status": "failed"}],
            [{"category": "not_suitable"}],
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "some_branches_blocked_no_usable")


# ---------------------------------------------------------------------------
# T059/T060/T061: Canonical recommendation projector
# ---------------------------------------------------------------------------


def _snapshot(job_id="job-1", *, title="Python 后端工程师", company="示例公司",
              salary="20-35K", location="上海", jd="负责后端服务开发", tags=None,
              source_url="https://www.zhipin.com/job_detail/abc.html",
              source_status="active", fetched_at="2026-07-20T12:01:30Z",
              completeness="complete", reused=False):
    return {
        "id": f"snap-{job_id}",
        "job_id": job_id,
        "fields": {
            "title": title, "company": company, "salary": salary,
            "location": location, "jd": jd, "tags": tags or [],
        },
        "source_url": source_url,
        "source_status": source_status,
        "fetched_at": fetched_at,
        "completeness": completeness,
        "reused": reused,
    }


def _assessment_row(job_id="job-1", direction_id="dir-1", *,
                    category="high_match", hard_outcome="pass",
                    match_score=85, confidence=88, completeness="complete",
                    gaps=None, failure_code=None,
                    candidate_evidence_refs=None, job_evidence_refs=None):
    ce = candidate_evidence_refs if candidate_evidence_refs is not None else ["e1"]
    je = job_evidence_refs if job_evidence_refs is not None else ["title", "jd"]
    return {
        "id": f"as-{job_id}-{direction_id}",
        "job_id": job_id,
        "direction_id": direction_id,
        "category": category,
        "hard_outcome": hard_outcome,
        "match_score": match_score,
        "confidence": confidence,
        "snapshot_completeness": completeness,
        "dimensions": {
            "direction_alignment": {"score": 80, "candidate_evidence_refs": ce, "job_evidence_refs": je},
            "skill_coverage": {"score": 75, "candidate_evidence_refs": ce, "job_evidence_refs": je},
            "experience_match": {"score": 70, "candidate_evidence_refs": ce, "job_evidence_refs": je},
            "industry_relevance": {"score": 65, "candidate_evidence_refs": ce, "job_evidence_refs": je},
        },
        "gaps": list(gaps or []),
        "failure_code": failure_code,
    }


_DIRECTIONS = [
    {"id": "dir-1", "name": "Python 后端", "type": "core"},
    {"id": "dir-2", "name": "互联网行业迁移", "type": "adjacent"},
]


class ClassificationGuardTests(unittest.TestCase):
    """T059: hard violation always unsuitable; hard unknown cannot high_match;
    soft preference only sorts; growth requires explicit gap."""

    def test_hard_violation_always_unsuitable_regardless_of_ai_score(self):
        from webui.discovery import project_recommendations

        snap = _snapshot("job-violation")
        # AI says high_match but hard rule violated
        assess = _assessment_row("job-violation", "dir-1",
                                 category="high_match", hard_outcome="violation",
                                 match_score=99, confidence=99)
        items = project_recommendations("run-1", [snap], [assess], _DIRECTIONS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["category"], "not_suitable")

    def test_hard_unknown_cannot_be_high_match(self):
        from webui.discovery import project_recommendations

        snap = _snapshot("job-unknown")
        assess = _assessment_row("job-unknown", "dir-1",
                                 category="high_match", hard_outcome="unknown",
                                 match_score=95, confidence=95)
        items = project_recommendations("run-1", [snap], [assess], _DIRECTIONS)
        self.assertEqual(len(items), 1)
        self.assertNotEqual(items[0]["category"], "high_match")
        self.assertEqual(items[0]["category"], "needs_review")

    def test_soft_preference_only_affects_sort_not_category(self):
        from webui.discovery import project_recommendations

        snap_a = _snapshot("job-a", company="A")
        snap_b = _snapshot("job-b", company="B")
        # Both high_match, same score/confidence/completeness; B has soft pref
        assess_a = _assessment_row("job-a", "dir-1", match_score=85, confidence=88)
        assess_b = _assessment_row("job-b", "dir-1", match_score=85, confidence=88)
        items = project_recommendations(
            "run-1", [snap_a, snap_b], [assess_a, assess_b], _DIRECTIONS,
            soft_preferences={"preferred_companies": ["B"]},
        )
        # B should sort first due to soft preference, but both remain high_match
        self.assertEqual(items[0]["job_id"], "job-b")
        self.assertEqual(items[1]["job_id"], "job-a")
        self.assertEqual(items[0]["category"], "high_match")
        self.assertEqual(items[1]["category"], "high_match")

    def test_growth_category_requires_at_least_one_explicit_gap(self):
        from webui.discovery import project_recommendations

        snap = _snapshot("job-growth")
        # AI proposes growth but no gaps supplied → must degrade
        assess_no_gap = _assessment_row("job-growth", "dir-1",
                                        category="growth_match", gaps=[])
        items = project_recommendations("run-1", [snap], [assess_no_gap], _DIRECTIONS)
        self.assertEqual(len(items), 1)
        self.assertNotEqual(items[0]["category"], "growth_match")

        # With explicit gap → growth_match is valid
        assess_with_gap = _assessment_row(
            "job-growth", "dir-1", category="growth_match",
            gaps=[{"text": "需要补足分布式经验", "job_evidence_refs": ["jd"]}],
        )
        items2 = project_recommendations("run-1", [snap], [assess_with_gap], _DIRECTIONS)
        self.assertEqual(items2[0]["category"], "growth_match")


class CanonicalProjectorSortTests(unittest.TestCase):
    """T060: canonical sort tuple and multi-direction filter."""

    def test_sort_by_category_then_score_then_confidence_then_completeness_then_job_id(self):
        from webui.discovery import project_recommendations

        snaps = [
            _snapshot("job-z", company="Z"),
            _snapshot("job-a", company="A"),
            _snapshot("job-m", company="M"),
            _snapshot("job-high", company="H"),
        ]
        assess_rows = [
            # adjacent_match, score 90
            _assessment_row("job-z", "dir-1", category="adjacent_match", match_score=90, confidence=80),
            # high_match, score 70
            _assessment_row("job-a", "dir-1", category="high_match", match_score=70, confidence=80),
            # high_match, score 90, lower confidence
            _assessment_row("job-m", "dir-1", category="high_match", match_score=90, confidence=70),
            # high_match, score 90, higher confidence
            _assessment_row("job-high", "dir-1", category="high_match", match_score=90, confidence=95),
        ]
        items = project_recommendations("run-1", snaps, assess_rows, _DIRECTIONS)
        job_order = [it["job_id"] for it in items]
        # high_match before adjacent; within high: score desc, then confidence desc
        self.assertEqual(job_order, ["job-high", "job-m", "job-a", "job-z"])

    def test_stable_sort_by_job_id_when_all_components_equal(self):
        from webui.discovery import project_recommendations

        snaps = [_snapshot(f"job-{c}", company=c) for c in ("C", "A", "B")]
        assess_rows = [
            _assessment_row(f"job-{c}", "dir-1", match_score=85, confidence=88)
            for c in ("C", "A", "B")
        ]
        items = project_recommendations("run-1", snaps, assess_rows, _DIRECTIONS)
        self.assertEqual([it["job_id"] for it in items], ["job-A", "job-B", "job-C"])

    def test_repeated_projection_is_deterministic(self):
        from webui.discovery import project_recommendations

        snaps = [_snapshot(f"job-{i}", company=f"C{i}") for i in range(5)]
        assess_rows = [
            _assessment_row(f"job-{i}", "dir-1", match_score=80 + i, confidence=85)
            for i in range(5)
        ]
        first = [it["job_id"] for it in project_recommendations("run-1", snaps, assess_rows, _DIRECTIONS)]
        second = [it["job_id"] for it in project_recommendations("run-1", snaps, assess_rows, _DIRECTIONS)]
        self.assertEqual(first, second)

    def test_direction_filter_returns_all_assessments_for_matching_jobs(self):
        from webui.discovery import project_recommendations

        snap = _snapshot("job-multi")
        assess_rows = [
            _assessment_row("job-multi", "dir-1", category="high_match", match_score=90),
            _assessment_row("job-multi", "dir-2", category="adjacent_match", match_score=75),
        ]
        # Filter by dir-2: job should appear, and both assessments returned
        items = project_recommendations(
            "run-1", [snap], assess_rows, _DIRECTIONS,
            direction_filter="dir-2",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["job_id"], "job-multi")
        self.assertEqual(len(items[0]["assessments"]), 2)
        self.assertIn("dir-1", items[0]["matched_direction_ids"])
        self.assertIn("dir-2", items[0]["matched_direction_ids"])

    def test_category_filter_applies_to_primary_assessment(self):
        from webui.discovery import project_recommendations

        snap = _snapshot("job-cat")
        assess_rows = [
            _assessment_row("job-cat", "dir-1", category="high_match", match_score=90),
            _assessment_row("job-cat", "dir-2", category="adjacent_match", match_score=75),
        ]
        # Primary is high_match (best); filtering adjacent_match should still include it
        items = project_recommendations(
            "run-1", [snap], assess_rows, _DIRECTIONS,
            category_filter="high_match",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["category"], "high_match")

        # Filtering not_suitable should exclude it
        items2 = project_recommendations(
            "run-1", [snap], assess_rows, _DIRECTIONS,
            category_filter="not_suitable",
        )
        self.assertEqual(len(items2), 0)


class RecommendationResultFieldTests(unittest.TestCase):
    """T061: formal result fields — company/title/salary/location/JD,
    source/status/fetched_at, positive evidence, gap status, bilateral refs."""

    def test_result_contains_all_required_job_fields(self):
        from webui.discovery import project_recommendations

        snap = _snapshot(
            "job-fields", title="资深后端", company="科技公司",
            salary="30-50K", location="北京", jd="负责核心系统架构设计",
            source_url="https://www.zhipin.com/job_detail/xyz.html",
            source_status="active", fetched_at="2026-07-20T13:00:00Z",
        )
        assess = _assessment_row("job-fields", "dir-1")
        items = project_recommendations("run-1", [snap], [assess], _DIRECTIONS)
        item = items[0]

        self.assertEqual(item["company"], "科技公司")
        self.assertEqual(item["title"], "资深后端")
        self.assertEqual(item["salary"], "30-50K")
        self.assertEqual(item["location"], "北京")
        # JD or excerpt must be present
        self.assertTrue(item.get("jd") or item.get("jd_excerpt"))
        self.assertEqual(item["source_url"], "https://www.zhipin.com/job_detail/xyz.html")
        self.assertEqual(item["source_status"], "active")
        self.assertEqual(item["fetched_at"], "2026-07-20T13:00:00Z")

    def test_result_contains_positive_evidence_and_bilateral_refs(self):
        from webui.discovery import project_recommendations

        snap = _snapshot("job-evidence")
        assess = _assessment_row(
            "job-evidence", "dir-1",
            candidate_evidence_refs=["e1", "e2"],
            job_evidence_refs=["title", "jd"],
        )
        items = project_recommendations("run-1", [snap], [assess], _DIRECTIONS)
        explanation = items[0]["explanation"]

        # Must have at least one positive item
        self.assertTrue(len(explanation["positive"]) >= 1)
        # Bilateral refs: both candidate and job refs present
        self.assertTrue(len(explanation["candidate_evidence_refs"]) >= 1)
        self.assertTrue(len(explanation["job_evidence_refs"]) >= 1)

    def test_result_shows_gap_status_when_gaps_exist(self):
        from webui.discovery import project_recommendations

        snap = _snapshot("job-gap")
        assess = _assessment_row(
            "job-gap", "dir-1", category="growth_match",
            gaps=[{"text": "缺少大数据经验", "job_evidence_refs": ["jd"]}],
        )
        items = project_recommendations("run-1", [snap], [assess], _DIRECTIONS)
        explanation = items[0]["explanation"]
        self.assertTrue(len(explanation["gaps"]) >= 1)
        self.assertEqual(explanation["gaps"][0]["text"], "缺少大数据经验")

    def test_recommendation_id_and_rank_are_present(self):
        from webui.discovery import project_recommendations

        snap = _snapshot("job-id")
        assess = _assessment_row("job-id", "dir-1")
        items = project_recommendations("run-1", [snap], [assess], _DIRECTIONS)
        item = items[0]
        self.assertEqual(item["recommendation_id"], "run-1:job-id")
        self.assertEqual(item["rank"], 1)

    def test_primary_assessment_is_most_favorable_valid_direction(self):
        from webui.discovery import project_recommendations

        snap = _snapshot("job-primary")
        assess_rows = [
            _assessment_row("job-primary", "dir-1", category="adjacent_match", match_score=75),
            _assessment_row("job-primary", "dir-2", category="high_match", match_score=90),
        ]
        items = project_recommendations("run-1", [snap], assess_rows, _DIRECTIONS)
        item = items[0]
        # Primary should be the high_match (most favorable)
        self.assertEqual(item["category"], "high_match")
        self.assertEqual(item["primary_assessment"]["direction_id"], "dir-2")


class SuccessCriteriaVerificationTests(unittest.TestCase):
    """T065: SC-005, SC-006, SC-007 verification.

    SC-005: 硬约束违规推荐=0
    SC-006: 重复加载排序一致
    SC-007: 正式结果字段和解释覆盖率=100%
    """

    def test_sc_005_hard_violation_never_in_recommended(self):
        """SC-005: 明确违反硬约束的岗位进入推荐组合的比例为 0%。"""
        from webui.discovery import project_recommendations

        snaps = [
            _snapshot("job-v1"), _snapshot("job-v2"), _snapshot("job-ok"),
        ]
        assess_rows = [
            _assessment_row("job-v1", "dir-1", category="high_match",
                            hard_outcome="violation", match_score=99),
            _assessment_row("job-v2", "dir-1", category="adjacent_match",
                            hard_outcome="violation", match_score=95),
            _assessment_row("job-ok", "dir-1", category="high_match",
                            hard_outcome="pass", match_score=80),
        ]
        items = project_recommendations("run-1", snaps, assess_rows, _DIRECTIONS)
        recommended = [
            it for it in items
            if it["category"] in ("high_match", "adjacent_match", "growth_match")
        ]
        violation_in_recommended = [
            it for it in recommended
            if any(
                a.get("hard_outcome") == "violation"
                for a in it.get("assessments", [])
            )
        ]
        self.assertEqual(len(violation_in_recommended), 0,
                         "SC-005: 硬约束违规岗位不得进入推荐组合")

    def test_sc_006_repeated_projection_identical_order(self):
        """SC-006: 固定结果集重复加载时，推荐排序完全一致。"""
        from webui.discovery import project_recommendations

        snaps = [_snapshot(f"job-{i}", company=f"C{i}") for i in range(10)]
        assess_rows = [
            _assessment_row(f"job-{i}", "dir-1",
                            category=("high_match" if i % 3 == 0 else
                                      "adjacent_match" if i % 3 == 1 else "growth_match"),
                            match_score=70 + i, confidence=80 + (i % 5),
                            gaps=[{"text": "gap", "job_evidence_refs": ["jd"]}] if i % 3 == 2 else [])
            for i in range(10)
        ]
        first = [it["job_id"] for it in project_recommendations("run-1", snaps, assess_rows, _DIRECTIONS)]
        second = [it["job_id"] for it in project_recommendations("run-1", snaps, assess_rows, _DIRECTIONS)]
        third = [it["job_id"] for it in project_recommendations("run-1", snaps, assess_rows, _DIRECTIONS)]
        self.assertEqual(first, second, "SC-006: 第二次加载排序必须一致")
        self.assertEqual(second, third, "SC-006: 第三次加载排序必须一致")

    def test_sc_007_all_formal_results_have_required_fields_and_explanation(self):
        """SC-007: 100% 正式推荐包含公司、岗位、JD、来源、抓取时间、正向依据和差距状态。"""
        from webui.discovery import project_recommendations

        snaps = [
            _snapshot("job-1", title="后端工程师", company="A公司", salary="20-40K",
                      location="上海", jd="负责后端开发", source_url="https://www.zhipin.com/job_detail/1.html",
                      source_status="active", fetched_at="2026-07-20T12:00:00Z"),
            _snapshot("job-2", title="数据工程师", company="B公司", salary="25-45K",
                      location="北京", jd="负责数据管道", source_url="https://www.zhipin.com/job_detail/2.html",
                      source_status="active", fetched_at="2026-07-20T12:01:00Z"),
        ]
        assess_rows = [
            _assessment_row("job-1", "dir-1", category="high_match",
                            candidate_evidence_refs=["e1"], job_evidence_refs=["title", "jd"]),
            _assessment_row("job-2", "dir-1", category="adjacent_match",
                            candidate_evidence_refs=["e2"], job_evidence_refs=["title"],
                            gaps=[{"text": "缺少大数据经验", "job_evidence_refs": ["jd"]}]),
        ]
        items = project_recommendations("run-1", snaps, assess_rows, _DIRECTIONS)
        self.assertGreaterEqual(len(items), 2)
        for item in items:
            # Required fields
            self.assertTrue(item.get("company"), f"SC-007: {item['job_id']} 缺少 company")
            self.assertTrue(item.get("title"), f"SC-007: {item['job_id']} 缺少 title")
            self.assertTrue(item.get("jd") or item.get("jd_excerpt"),
                            f"SC-007: {item['job_id']} 缺少 JD")
            self.assertTrue(item.get("source_url"), f"SC-007: {item['job_id']} 缺少 source_url")
            self.assertIn("fetched_at", item, f"SC-007: {item['job_id']} 缺少 fetched_at")
            # Explanation coverage
            expl = item.get("explanation", {})
            self.assertIn("positive", expl, f"SC-007: {item['job_id']} 缺少 positive")
            self.assertIn("gaps", expl, f"SC-007: {item['job_id']} 缺少 gaps")
            self.assertIn("candidate_evidence_refs", expl,
                          f"SC-007: {item['job_id']} 缺少 candidate_evidence_refs")
            self.assertIn("job_evidence_refs", expl,
                          f"SC-007: {item['job_id']} 缺少 job_evidence_refs")
            # At least one positive item
            self.assertGreaterEqual(len(expl["positive"]), 1,
                                    f"SC-007: {item['job_id']} 正向依据为空")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
