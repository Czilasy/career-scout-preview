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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
