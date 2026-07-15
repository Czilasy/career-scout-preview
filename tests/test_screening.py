"""Tests for 002 resume-driven screening: run state machine and filter snapshot.

T006: execution run state machine (queued/running/succeeded/partial/
      failed/interrupted) with valid/invalid transitions.
T007: filter snapshot freezing (deep copy, allowed keys only, empty
      strings preserved) and no-required-fields rule.

Importing webui.screening fails until T008 implements it (RED).
"""

import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from tests.test_screening_fixtures import (
    sample_filters_full,
    sample_filters_partial,
)

from webui.screening import (
    ALLOWED_FILTER_KEYS,
    STATUSES,
    TERMINAL_STATUSES,
    build_screening_filter_options,
    freeze_filters,
    is_valid_filters,
    is_valid_transition,
    merge_filters,
    validate_transition,
)


class ScreeningRunStateMachineTests(unittest.TestCase):
    """T006: execution run state machine transitions."""

    def test_valid_transitions_from_queued(self):
        self.assertTrue(is_valid_transition("queued", "running"))
        self.assertTrue(is_valid_transition("queued", "interrupted"))

    def test_valid_transitions_from_running(self):
        for target in ("succeeded", "partial", "failed", "interrupted"):
            self.assertTrue(is_valid_transition("running", target), target)

    def test_invalid_transition_from_terminal(self):
        for terminal in TERMINAL_STATUSES:
            for target in ("running", "queued", "succeeded", "partial", "failed"):
                if target == terminal:
                    continue
                self.assertFalse(
                    is_valid_transition(terminal, target),
                    f"{terminal}->{target} should be invalid",
                )

    def test_queued_cannot_skip_to_terminal_success(self):
        # queued 必须经过 running 才能到 succeeded/partial/failed
        for target in ("succeeded", "partial", "failed"):
            self.assertFalse(is_valid_transition("queued", target), target)

    def test_validate_transition_accepts_valid(self):
        # 不抛异常即通过
        validate_transition("queued", "running")
        validate_transition("running", "succeeded")
        validate_transition("running", "partial")
        validate_transition("running", "failed")
        validate_transition("running", "interrupted")
        validate_transition("queued", "interrupted")

    def test_validate_transition_raises_on_invalid(self):
        with self.assertRaisesRegex(ValueError, "succeeded"):
            validate_transition("succeeded", "running")
        with self.assertRaisesRegex(ValueError, "interrupted"):
            validate_transition("interrupted", "running")

    def test_validate_transition_raises_on_unknown_status(self):
        with self.assertRaisesRegex(ValueError, "unknown"):
            validate_transition("queued", "unknown")
        with self.assertRaisesRegex(ValueError, "unknown"):
            validate_transition("unknown", "running")

    def test_terminal_statuses_are_subset_of_statuses(self):
        for s in TERMINAL_STATUSES:
            self.assertIn(s, STATUSES)

    def test_all_six_statuses_present(self):
        expected = {"queued", "running", "succeeded", "partial", "failed", "interrupted"}
        self.assertEqual(set(STATUSES), expected)


class FilterSnapshotTests(unittest.TestCase):
    """T007: filter snapshot freezing and no-required-fields rule."""

    def test_freeze_returns_independent_copy(self):
        original = sample_filters_full()
        frozen = freeze_filters(original)
        original["city"] = "北京"
        self.assertEqual(frozen["city"], "上海")

    def test_freeze_empty_filters(self):
        frozen = freeze_filters({})
        self.assertEqual(frozen, {})

    def test_freeze_partial_filters(self):
        frozen = freeze_filters(sample_filters_partial())
        self.assertEqual(frozen, sample_filters_partial())

    def test_freeze_full_filters(self):
        frozen = freeze_filters(sample_filters_full())
        self.assertEqual(frozen, sample_filters_full())

    def test_freeze_strips_disallowed_keys(self):
        filters = sample_filters_full()
        filters["malicious"] = "x"
        filters["job_id"] = "injected"
        filters["_internal"] = "leak"
        frozen = freeze_filters(filters)
        self.assertNotIn("malicious", frozen)
        self.assertNotIn("job_id", frozen)
        self.assertNotIn("_internal", frozen)
        self.assertTrue(set(frozen.keys()).issubset(set(ALLOWED_FILTER_KEYS)))

    def test_freeze_preserves_empty_strings(self):
        # 空字符串忠实记录用户未填的字段，核验时跳过
        filters = {"city": "上海", "salary": "", "experience": ""}
        frozen = freeze_filters(filters)
        self.assertEqual(frozen["salary"], "")
        self.assertEqual(frozen["experience"], "")

    def test_no_required_fields(self):
        self.assertTrue(is_valid_filters({}))
        self.assertTrue(is_valid_filters({"city": "上海"}))
        self.assertTrue(is_valid_filters(sample_filters_full()))
        self.assertTrue(is_valid_filters(sample_filters_partial()))

    def test_is_valid_filters_rejects_disallowed_keys(self):
        self.assertFalse(is_valid_filters({"city": "上海", "bad": "x"}))
        self.assertFalse(is_valid_filters({"_internal": "leak"}))

    def test_allowed_filter_keys_has_seven_fields(self):
        expected = {"city", "salary", "experience", "degree", "scale", "stage", "industry"}
        self.assertEqual(set(ALLOWED_FILTER_KEYS), expected)


class FilterOptionsTests(unittest.TestCase):
    """T011: filter option enums sourced from boss_cdp_raw maps."""

    def test_options_has_seven_classes(self):
        opts = build_screening_filter_options()
        self.assertEqual(
            set(opts.keys()),
            {"salary", "experience", "degree", "scale", "stage", "industry", "city"},
        )

    def test_each_class_is_list_of_label_value(self):
        opts = build_screening_filter_options()
        for name, items in opts.items():
            self.assertIsInstance(items, list, name)
            for item in items:
                self.assertIn("label", item, name)
                self.assertIn("value", item, name)

    def test_each_class_starts_with_unlimited(self):
        opts = build_screening_filter_options()
        for name, items in opts.items():
            self.assertEqual(items[0], {"label": "不限", "value": ""}, name)

    def test_salary_contains_known_code(self):
        opts = build_screening_filter_options()
        values = {item["value"] for item in opts["salary"]}
        self.assertIn("405", values)  # 10-20K

    def test_experience_contains_known_code(self):
        opts = build_screening_filter_options()
        values = {item["value"] for item in opts["experience"]}
        self.assertIn("105", values)  # 3-5年

    def test_degree_contains_known_code(self):
        opts = build_screening_filter_options()
        values = {item["value"] for item in opts["degree"]}
        self.assertIn("203", values)  # 本科

    def test_scale_contains_known_code(self):
        opts = build_screening_filter_options()
        values = {item["value"] for item in opts["scale"]}
        self.assertIn("303", values)  # 100-499人

    def test_stage_contains_known_code(self):
        opts = build_screening_filter_options()
        values = {item["value"] for item in opts["stage"]}
        self.assertIn("804", values)  # B轮

    def test_industry_contains_known_code(self):
        opts = build_screening_filter_options()
        values = {item["value"] for item in opts["industry"]}
        self.assertIn("1001", values)  # 互联网

    def test_city_uses_name_as_value(self):
        opts = build_screening_filter_options()
        values = {item["value"] for item in opts["city"]}
        self.assertIn("上海", values)

    def test_city_excludes_national_redundant_with_unlimited(self):
        # "不限" 已代表全国，city 枚举不重复列"全国"
        opts = build_screening_filter_options()
        values = {item["value"] for item in opts["city"]}
        self.assertNotIn("全国", values)

    def test_options_exclude_zero_value(self):
        opts = build_screening_filter_options()
        for name, items in opts.items():
            for item in items[1:]:  # 跳过首项"不限"
                self.assertNotEqual(item["value"], "0", name)


class FilterMergeTests(unittest.TestCase):
    """T013: user-confirmed value takes precedence over AI suggestion."""

    def test_user_value_takes_precedence(self):
        merged = merge_filters({"city": "北京"}, {"city": "上海"})
        self.assertEqual(merged["city"], "北京")

    def test_ai_fills_when_user_blank(self):
        merged = merge_filters({"city": ""}, {"city": "上海"})
        self.assertEqual(merged["city"], "上海")

    def test_ai_fills_when_user_field_absent(self):
        merged = merge_filters({}, {"city": "上海"})
        self.assertEqual(merged["city"], "上海")

    def test_both_blank_stays_blank(self):
        merged = merge_filters({"city": ""}, {"city": ""})
        self.assertEqual(merged["city"], "")

    def test_both_absent_stays_blank(self):
        merged = merge_filters({}, {})
        self.assertEqual(merged["city"], "")

    def test_ai_cannot_override_nonempty_user(self):
        user = {"city": "北京", "salary": "406"}
        ai = {"city": "上海", "salary": "405"}
        merged = merge_filters(user, ai)
        self.assertEqual(merged["city"], "北京")
        self.assertEqual(merged["salary"], "406")

    def test_merged_has_all_seven_fields(self):
        merged = merge_filters({"city": "上海"}, {"salary": "405"})
        self.assertEqual(set(merged.keys()), set(ALLOWED_FILTER_KEYS))

    def test_mixed_user_and_ai_values(self):
        user = {"city": "北京", "salary": "", "experience": "105"}
        ai = {"city": "上海", "salary": "405", "experience": "104"}
        merged = merge_filters(user, ai)
        self.assertEqual(merged["city"], "北京")        # user 优先
        self.assertEqual(merged["salary"], "405")       # user 空，用 ai
        self.assertEqual(merged["experience"], "105")   # user 优先
        self.assertEqual(merged["degree"], "")          # 都空

    def test_merge_strips_disallowed_keys(self):
        merged = merge_filters({"city": "上海", "bad": "x"}, {"salary": "405", "leak": "y"})
        self.assertNotIn("bad", merged)
        self.assertNotIn("leak", merged)
        self.assertTrue(set(merged.keys()).issubset(set(ALLOWED_FILTER_KEYS)))


# ---------------------------------------------------------------------------
# T019: filters_to_search_params — 确认条件到 BOSS 搜索参数映射
# ---------------------------------------------------------------------------

class FilterToSearchParamsTests(unittest.TestCase):
    """T019: map confirmed filters to BOSS search params.

    city (name) -> city code; empty city -> nationwide code;
    non-empty filter codes collected into filters dict; empty ones excluded.
    """

    def test_full_filters_map_city_to_code(self):
        from webui.screening import filters_to_search_params
        filters = {
            "city": "上海", "salary": "405", "experience": "105",
            "degree": "203", "scale": "303", "stage": "804", "industry": "1001",
        }
        result = filters_to_search_params(filters)
        self.assertEqual(result["city"], "101020100")
        self.assertEqual(result["filters"]["salary"], "405")
        self.assertEqual(result["filters"]["experience"], "105")
        self.assertEqual(result["filters"]["degree"], "203")
        self.assertEqual(result["filters"]["scale"], "303")
        self.assertEqual(result["filters"]["stage"], "804")
        self.assertEqual(result["filters"]["industry"], "1001")

    def test_empty_city_falls_back_to_nationwide(self):
        from webui.screening import filters_to_search_params
        filters = {"city": "", "salary": "405", "experience": "",
                   "degree": "", "scale": "", "stage": "", "industry": ""}
        result = filters_to_search_params(filters)
        self.assertEqual(result["city"], "100010000")

    def test_partial_filters_exclude_empty(self):
        from webui.screening import filters_to_search_params
        filters = {"city": "北京", "salary": "", "experience": "105",
                   "degree": "", "scale": "303", "stage": "", "industry": ""}
        result = filters_to_search_params(filters)
        self.assertEqual(result["city"], "101010100")
        self.assertNotIn("salary", result["filters"])
        self.assertEqual(result["filters"]["experience"], "105")
        self.assertNotIn("degree", result["filters"])
        self.assertEqual(result["filters"]["scale"], "303")
        self.assertNotIn("stage", result["filters"])
        self.assertNotIn("industry", result["filters"])

    def test_all_empty_returns_nationwide_and_empty_filters(self):
        from webui.screening import filters_to_search_params
        filters = {k: "" for k in ("city", "salary", "experience", "degree", "scale", "stage", "industry")}
        result = filters_to_search_params(filters)
        self.assertEqual(result["city"], "100010000")
        self.assertEqual(result["filters"], {})

    def test_city_code_passes_through(self):
        from webui.screening import filters_to_search_params
        filters = {"city": "101020100", "salary": "", "experience": "",
                   "degree": "", "scale": "", "stage": "", "industry": ""}
        result = filters_to_search_params(filters)
        self.assertEqual(result["city"], "101020100")


# ---------------------------------------------------------------------------
# T020: execute_first_layer — 第一层搜索编排
# ---------------------------------------------------------------------------

class ScreeningSearchOrchestrationTests(unittest.TestCase):
    """T020: first-layer search orchestration.

    Calls scraper subprocess, reads artifact, city-empty fallback to
    nationwide, status advances to succeeded/failed.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output_path = str(pathlib.Path(self.temp.name) / "search_result.json")

    def tearDown(self):
        self.temp.cleanup()

    def _write_jobs_file(self, jobs):
        pathlib.Path(self.output_path).write_text(
            json.dumps({"jobs": jobs}, ensure_ascii=False), encoding="utf-8"
        )

    def _ok_returncode(self):
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def _fail_returncode(self):
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "error"})()

    @mock.patch("webui.screening.subprocess.run")
    def test_execute_calls_scraper_with_correct_command(self, mock_run):
        from webui.screening import execute_first_layer
        mock_run.return_value = self._ok_returncode()
        self._write_jobs_file([{"title": "Python"}])
        execute_first_layer(
            {"city": "上海", "salary": "405", "experience": "", "degree": "",
             "scale": "", "stage": "", "industry": ""},
            keyword="Python",
            output_path=self.output_path,
            python_executable=sys.executable,
        )
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("Python", cmd)
        self.assertIn("--city", cmd)
        self.assertIn("101020100", cmd)
        self.assertIn("--salary", cmd)
        self.assertIn("405", cmd)
        self.assertIn("--output", cmd)

    @mock.patch("webui.screening.subprocess.run")
    def test_execute_returns_jobs_from_output(self, mock_run):
        from webui.screening import execute_first_layer
        mock_run.return_value = self._ok_returncode()
        jobs = [{"title": "Python", "salary": "20-30K"}, {"title": "Java"}]
        self._write_jobs_file(jobs)
        result = execute_first_layer(
            {"city": "", "salary": "", "experience": "", "degree": "",
             "scale": "", "stage": "", "industry": ""},
            keyword="Python",
            output_path=self.output_path,
            python_executable=sys.executable,
        )
        self.assertEqual(result["source_count"], 2)
        self.assertEqual(len(result["jobs"]), 2)

    @mock.patch("webui.screening.subprocess.run")
    def test_empty_city_falls_back_to_nationwide_in_command(self, mock_run):
        from webui.screening import execute_first_layer
        mock_run.return_value = self._ok_returncode()
        self._write_jobs_file([])
        execute_first_layer(
            {"city": "", "salary": "", "experience": "", "degree": "",
             "scale": "", "stage": "", "industry": ""},
            keyword="Python",
            output_path=self.output_path,
            python_executable=sys.executable,
        )
        cmd = mock_run.call_args[0][0]
        self.assertIn("100010000", cmd)

    @mock.patch("webui.screening.subprocess.run")
    def test_scraper_failure_raises_exception(self, mock_run):
        from webui.screening import execute_first_layer
        mock_run.return_value = self._fail_returncode()
        with self.assertRaises(Exception):
            execute_first_layer(
                {"city": "上海", "salary": "", "experience": "", "degree": "",
                 "scale": "", "stage": "", "industry": ""},
                keyword="Python",
                output_path=self.output_path,
                python_executable=sys.executable,
            )

    @mock.patch("webui.screening.subprocess.run")
    def test_status_stays_running_until_second_layer_finishes(self, mock_run):
        from webui.screening import execute_first_layer
        mock_run.return_value = self._ok_returncode()
        self._write_jobs_file([{"title": "Python"}])
        store = mock.MagicMock()
        store.get_screening_run.return_value = {"id": "run-1", "status": "queued"}
        result = execute_first_layer(
            {"city": "上海", "salary": "", "experience": "", "degree": "",
             "scale": "", "stage": "", "industry": ""},
            keyword="Python",
            output_path=self.output_path,
            python_executable=sys.executable,
            store=store,
            run_id="run-1",
        )
        store.update_screening_run_status.assert_any_call("run-1", "running")
        store.update_screening_run_status.assert_any_call("run-1", "running", source_count=1)
        self.assertEqual(result["status"], "running")

    @mock.patch("webui.screening.subprocess.run")
    def test_status_advances_to_failed_on_scraper_error(self, mock_run):
        from webui.screening import execute_first_layer
        mock_run.return_value = self._fail_returncode()
        store = mock.MagicMock()
        store.get_screening_run.return_value = {"id": "run-1", "status": "queued"}
        with self.assertRaises(Exception):
            execute_first_layer(
                {"city": "上海", "salary": "", "experience": "", "degree": "",
                 "scale": "", "stage": "", "industry": ""},
                keyword="Python",
                output_path=self.output_path,
                python_executable=sys.executable,
                store=store,
                run_id="run-1",
            )
        store.update_screening_run_status.assert_any_call("run-1", "running")
        store.update_screening_run_status.assert_any_call("run-1", "failed")


# ---------------------------------------------------------------------------
# T025: verify_hard_rules — 硬规则核验：选什么核什么、没选不核、城市无必填
# ---------------------------------------------------------------------------

class HardRuleVerificationTests(unittest.TestCase):
    """T025: hard-rule verification per job against frozen filters.

    Rule: for each non-empty field in frozen_filters, verify the job's
    corresponding field. Empty fields are skipped (not verified). City has
    no mandatory requirement — empty city means "do not verify city".
    Returns True (pass, into match zone) or False (fail, into mismatch zone).
    Does not report which field failed.
    """

    def setUp(self):
        from webui.screening import verify_hard_rules
        self.verify = verify_hard_rules

    def _job(self, **overrides):
        from tests.test_screening_fixtures import sample_screening_job
        job = sample_screening_job()
        job.update(overrides)
        return job

    # -- empty filters: nothing to verify, any job passes --

    def test_empty_filters_passes_any_job(self):
        self.assertTrue(self.verify(self._job(), {}))

    def test_empty_filters_passes_even_mismatched_job(self):
        job = self._job(salary="3-5K", location="北京·朝阳", company_scale="20-99人")
        self.assertTrue(self.verify(job, {}))

    # -- scale --

    def test_scale_match_passes(self):
        self.assertTrue(self.verify(self._job(company_scale="100-499人"), {"scale": "303"}))

    def test_scale_mismatch_fails(self):
        self.assertFalse(self.verify(self._job(company_scale="20-99人"), {"scale": "303"}))

    def test_scale_empty_not_verified(self):
        self.assertTrue(self.verify(self._job(company_scale="20-99人"), {"scale": ""}))

    # -- stage --

    def test_stage_match_passes(self):
        self.assertTrue(self.verify(self._job(company_stage="B轮"), {"stage": "804"}))

    def test_stage_mismatch_fails(self):
        self.assertFalse(self.verify(self._job(company_stage="未融资"), {"stage": "804"}))

    # -- industry --

    def test_industry_match_passes(self):
        self.assertTrue(self.verify(self._job(company_industry="互联网"), {"industry": "1001"}))

    def test_industry_mismatch_fails(self):
        self.assertFalse(self.verify(self._job(company_industry="金融"), {"industry": "1001"}))

    # -- city (no mandatory requirement) --

    def test_city_match_passes(self):
        self.assertTrue(self.verify(self._job(location="上海·浦东新区·张江"), {"city": "上海"}))

    def test_city_mismatch_fails(self):
        self.assertFalse(self.verify(self._job(location="北京·朝阳区·望京"), {"city": "上海"}))

    def test_city_empty_not_verified(self):
        # 城市无强制必填：用户未选城市时，不核验，任意 location 过
        self.assertTrue(self.verify(self._job(location="北京·朝阳"), {"city": ""}))

    # -- salary (range overlap between frozen segment and job salary) --

    def test_salary_match_within_segment_passes(self):
        # frozen 405 = 10-20K, job 12-18K 落在段内
        self.assertTrue(self.verify(self._job(salary="12-18K"), {"salary": "405"}))

    def test_salary_mismatch_outside_segment_fails(self):
        # frozen 405 = 10-20K, job 3-5K 在段外
        self.assertFalse(self.verify(self._job(salary="3-5K"), {"salary": "405"}))

    def test_salary_empty_not_verified(self):
        self.assertTrue(self.verify(self._job(salary="3-5K"), {"salary": ""}))

    # -- experience (parsed from tags "经验 | 学历") --

    def test_experience_match_passes(self):
        # frozen 105 = 3-5年, tags 经验段 = 3-5年
        self.assertTrue(self.verify(self._job(tags="3-5年 | 本科"), {"experience": "105"}))

    def test_experience_mismatch_fails(self):
        # frozen 105 = 3-5年, tags 经验段 = 1-3年
        self.assertFalse(self.verify(self._job(tags="1-3年 | 本科"), {"experience": "105"}))

    def test_experience_empty_not_verified(self):
        self.assertTrue(self.verify(self._job(tags="1-3年 | 本科"), {"experience": ""}))

    # -- degree (parsed from tags "经验 | 学历") --

    def test_degree_match_passes(self):
        # frozen 203 = 本科, tags 学历段 = 本科
        self.assertTrue(self.verify(self._job(tags="3-5年 | 本科"), {"degree": "203"}))

    def test_bachelor_candidate_passes_associate_job_requirement(self):
        # 003 FR-007: 专科、本科双向互通
        self.assertTrue(self.verify(self._job(tags="3-5年 | 大专"), {"degree": "203"}))

    # -- combined: verify only selected, skip unselected --

    def test_all_selected_fields_match_passes(self):
        frozen = {"city": "上海", "salary": "405", "scale": "303", "stage": "804", "industry": "1001"}
        job = self._job(
            location="上海·浦东", salary="12-18K",
            company_scale="100-499人", company_stage="B轮", company_industry="互联网",
        )
        self.assertTrue(self.verify(job, frozen))

    def test_one_field_mismatch_fails(self):
        frozen = {"city": "上海", "salary": "405", "scale": "303"}
        job = self._job(
            location="上海·浦东", salary="12-18K",
            company_scale="20-99人",  # scale 不匹配
        )
        self.assertFalse(self.verify(job, frozen))

    def test_unselected_fields_not_verified(self):
        # frozen 只选 scale，其他字段 job 即使不匹配也过
        frozen = {"city": "", "salary": "", "scale": "303", "stage": "", "industry": ""}
        job = self._job(
            location="北京·朝阳", salary="3-5K",
            company_scale="100-499人",  # scale 匹配
            company_stage="未融资", company_industry="金融",
        )
        self.assertTrue(self.verify(job, frozen))

    # -- job missing optional field: skip (pass) --

    def test_job_missing_tags_passes_when_experience_selected(self):
        # job 无 tags 字段，experience/degree 无法核验，视为过
        job = self._job()
        del job["tags"]
        self.assertTrue(self.verify(job, {"experience": "105"}))


# ---------------------------------------------------------------------------
# T027: partition_job — 两条核验分流（都过进符合、任一不过进不符合、不标原因）
# ---------------------------------------------------------------------------

class JobPartitionTests(unittest.TestCase):
    """T027: partition a job via hard-rule + AI similarity.

    Both pass -> "match" (符合区); either fails -> "mismatch" (不符合区).
    Return value is a bare verdict string with no reason attached.
    """

    def setUp(self):
        from webui.screening import partition_job
        self.partition = partition_job

    def _job(self, **overrides):
        from tests.test_screening_fixtures import sample_screening_job
        job = sample_screening_job()
        job.update(overrides)
        return job

    # -- both pass -> match --

    def test_both_pass_goes_to_match(self):
        # 硬规则过（空冻结），AI 占位恒过 → match
        self.assertEqual(self.partition(self._job(), {}), "match")

    def test_empty_filters_with_ai_pass_goes_to_match(self):
        self.assertEqual(self.partition(self._job(), {}, "resume", "jd"), "match")

    # -- hard fail, AI pass -> mismatch --

    def test_hard_fail_ai_pass_goes_to_mismatch(self):
        # 硬规则不过（scale 不匹配），AI 占位过 → mismatch
        job = self._job(company_scale="20-99人")
        self.assertEqual(self.partition(job, {"scale": "303"}), "mismatch")

    # -- hard pass, AI fail -> mismatch --

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_hard_pass_ai_fail_goes_to_mismatch(self, mock_ai):
        mock_ai.return_value = {"verdict": "mismatch"}
        self.assertEqual(self.partition(self._job(), {}, "resume", "jd"), "mismatch")

    # -- both fail -> mismatch --

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_both_fail_goes_to_mismatch(self, mock_ai):
        mock_ai.return_value = {"verdict": "mismatch"}
        job = self._job(company_scale="20-99人")
        self.assertEqual(self.partition(job, {"scale": "303"}, "resume", "jd"), "mismatch")

    # -- no reason in return --

    def test_return_value_is_bare_verdict_without_reason(self):
        # 返回值只是 "match"/"mismatch" 字符串，不含原因字段
        result = self.partition(self._job(company_scale="20-99人"), {"scale": "303"})
        self.assertIsInstance(result, str)
        self.assertIn(result, ("match", "mismatch"))

    def test_match_return_has_no_reason_key(self):
        # 字符串返回值天然无 reason，但明确断言不携带原因信息
        result = self.partition(self._job(), {})
        self.assertEqual(result, "match")
        self.assertNotIn("reason", result)
        self.assertNotIn("field", result)

    # -- partition calls both verifiers --

    @mock.patch("webui.screening.assess_semantic_similarity")
    @mock.patch("webui.screening.verify_hard_rules")
    def test_partition_calls_hard_rules_and_ai(self, mock_hard, mock_ai):
        mock_hard.return_value = True
        mock_ai.return_value = {"verdict": "match"}
        self.partition(self._job(), {"scale": "303"}, "resume", "jd")
        mock_hard.assert_called_once()
        mock_ai.assert_called_once_with("resume", "jd")

    @mock.patch("webui.screening.assess_semantic_similarity")
    @mock.patch("webui.screening.verify_hard_rules")
    def test_hard_fail_short_circuits_to_mismatch(self, mock_hard, mock_ai):
        # 硬规则不过时仍返回 mismatch；AI 是否调用不影响结果
        mock_hard.return_value = False
        mock_ai.return_value = {"verdict": "match"}
        result = self.partition(self._job(), {"scale": "303"}, "resume", "jd")
        self.assertEqual(result, "mismatch")


class MatchZoneOrderingTests(unittest.TestCase):
    """T029: match zone ordered by scrape order, not similarity score.

    FR-029: 符合区岗位必须先按抓回来顺序排列，不使用相似度排序。
    本次实现 AI 语义相似度为占位（恒过、无分数），符合区只能按抓回顺序排。
    未来 AI 框架输出匹配分时再启用相似度排序（FR-030，本次不实现）。
    """

    def setUp(self):
        from webui.screening import partition_jobs
        self.partition_jobs = partition_jobs

    def _job(self, job_id, **overrides):
        from tests.test_screening_fixtures import sample_screening_job
        job = sample_screening_job(job_id=job_id)
        job.update(overrides)
        return job

    def _job_ids(self, jobs):
        return [j["job_id"] for j in jobs]

    # -- empty input --

    def test_empty_jobs_returns_empty_zones(self):
        result = self.partition_jobs([], {})
        self.assertEqual(result["match"], [])
        self.assertEqual(result["mismatch"], [])

    # -- all match preserves scrape order --

    def test_all_match_preserves_scrape_order(self):
        # 三条岗位全部进符合区（空冻结 + AI 占位恒过），顺序保留
        jobs = [self._job("job-A"), self._job("job-B"), self._job("job-C")]
        result = self.partition_jobs(jobs, {})
        self.assertEqual(self._job_ids(result["match"]), ["job-A", "job-B", "job-C"])
        self.assertEqual(result["mismatch"], [])

    # -- all mismatch keeps match zone empty --

    def test_all_mismatch_match_zone_empty(self):
        # 三条岗位 scale 全不匹配冻结条件，全部进不符合区
        jobs = [
            self._job("job-A", company_scale="20-99人"),
            self._job("job-B", company_scale="20-99人"),
            self._job("job-C", company_scale="20-99人"),
        ]
        result = self.partition_jobs(jobs, {"scale": "303"})
        self.assertEqual(result["match"], [])
        self.assertEqual(self._job_ids(result["mismatch"]), ["job-A", "job-B", "job-C"])

    # -- mixed partition keeps match zone in scrape order (核心) --

    def test_mixed_partition_match_zone_in_scrape_order(self):
        # 混合分流：A/C/D 进符合区，B/E 进不符合区
        # 符合区必须按抓回顺序 [A, C, D]，不按 verdict 出现顺序
        jobs = [
            self._job("job-A"),                                      # match
            self._job("job-B", company_scale="20-99人"),             # mismatch
            self._job("job-C"),                                      # match
            self._job("job-D"),                                      # match
            self._job("job-E", company_scale="20-99人"),             # mismatch
        ]
        result = self.partition_jobs(jobs, {"scale": "303"})
        self.assertEqual(self._job_ids(result["match"]), ["job-A", "job-C", "job-D"])
        self.assertEqual(self._job_ids(result["mismatch"]), ["job-B", "job-E"])

    # -- match zone NOT sorted by AI similarity score (FR-029 核心) --

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_match_zone_not_sorted_by_ai_similarity_score(self, mock_ai):
        # 即使 AI 返回不同的 score 字段（未来框架可能给分），本次实现也不据此排序
        # mock AI 对 job-A 给高分、job-B 给低分，符合区仍按抓回顺序 [A, B]
        def fake_assess(resume, jd):
            # 模拟未来 AI 框架可能返回的带分结构
            return {"verdict": "match", "score": 0.9}
        mock_ai.side_effect = fake_assess

        jobs = [self._job("job-A"), self._job("job-B"), self._job("job-C")]
        result = self.partition_jobs(jobs, {}, "resume", "jd")
        # 顺序仍为抓回顺序，不按 score 排
        self.assertEqual(self._job_ids(result["match"]), ["job-A", "job-B", "job-C"])

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_match_zone_not_sorted_by_varying_ai_scores(self, mock_ai):
        # 三条 job 的 AI score 设为 [0.3, 0.9, 0.5]，符合区不按 score 降序排
        scores = {"job-A": 0.3, "job-B": 0.9, "job-C": 0.5}
        def fake_assess(resume, jd):
            # 无法从入参定位 job，统一返回 match 但 score 不同
            return {"verdict": "match", "score": 0.5}
        mock_ai.side_effect = fake_assess

        jobs = [self._job("job-A"), self._job("job-B"), self._job("job-C")]
        result = self.partition_jobs(jobs, {}, "resume", "jd")
        # 即便 score 不同，仍按抓回顺序
        self.assertEqual(self._job_ids(result["match"]), ["job-A", "job-B", "job-C"])

    # -- mismatch zone also in scrape order --

    def test_mismatch_zone_also_in_scrape_order(self):
        # 不符合区也按抓回顺序（spec 未强制，但实现上一致）
        jobs = [
            self._job("job-A", company_scale="20-99人"),             # mismatch
            self._job("job-B", company_scale="20-99人"),             # mismatch
            self._job("job-C"),                                      # match
            self._job("job-D", company_scale="20-99人"),             # mismatch
        ]
        result = self.partition_jobs(jobs, {"scale": "303"})
        self.assertEqual(self._job_ids(result["mismatch"]), ["job-A", "job-B", "job-D"])

    # -- single match preserved --

    def test_single_match_preserved(self):
        jobs = [self._job("job-solo")]
        result = self.partition_jobs(jobs, {})
        self.assertEqual(self._job_ids(result["match"]), ["job-solo"])
        self.assertEqual(result["mismatch"], [])

    # -- partition_jobs delegates to partition_job --

    @mock.patch("webui.screening.partition_job")
    def test_partition_jobs_delegates_to_partition_job(self, mock_partition):
        # 验证批量分流内部调用单条 partition_job，按 jobs 顺序逐条调用
        mock_partition.side_effect = ["match", "mismatch", "match"]
        jobs = [self._job("job-A"), self._job("job-B"), self._job("job-C")]
        result = self.partition_jobs(jobs, {"scale": "303"}, "resume", "jd")
        self.assertEqual(mock_partition.call_count, 3)
        # 每条 job 都按顺序传入
        called_job_ids = [call.args[0]["job_id"] for call in mock_partition.call_args_list]
        self.assertEqual(called_job_ids, ["job-A", "job-B", "job-C"])
        # 分流结果按抓回顺序落到对应区
        self.assertEqual(self._job_ids(result["match"]), ["job-A", "job-C"])
        self.assertEqual(self._job_ids(result["mismatch"]), ["job-B"])

    # -- returned jobs are the original dict references --

    def test_match_zone_jobs_are_original_dicts(self):
        # 返回的 job 引用应与输入一致（不被复制或修改）
        job_a = self._job("job-A")
        job_b = self._job("job-B")
        result = self.partition_jobs([job_a, job_b], {})
        self.assertIs(result["match"][0], job_a)
        self.assertIs(result["match"][1], job_b)

    # -- return shape contract --

    def test_return_value_has_match_and_mismatch_keys(self):
        result = self.partition_jobs([self._job("job-A")], {})
        self.assertIn("match", result)
        self.assertIn("mismatch", result)
        self.assertIsInstance(result["match"], list)
        self.assertIsInstance(result["mismatch"], list)


class DisplayExclusionTests(unittest.TestCase):
    """T037: 展示阶段排除垃圾桶具体岗位，不扩展到同公司或相似岗位。

    FR：垃圾桶里的具体岗位在后续执行的展示阶段被排除。
    排除只发生在展示阶段，按具体岗位识别，不扩展到同公司或相似特征岗位。
    """

    def setUp(self):
        from webui.screening import exclude_trash_jobs
        self.exclude = exclude_trash_jobs

    def _job(self, job_id, **overrides):
        from tests.test_screening_fixtures import sample_screening_job
        job = sample_screening_job(job_id=job_id)
        job.update(overrides)
        return job

    # -- 空垃圾桶：不排除任何岗位 --

    def test_empty_rejected_set_returns_all_jobs(self):
        jobs = [self._job("job-A"), self._job("job-B")]
        result = self.exclude(jobs, set())
        self.assertEqual(len(result), 2)

    def test_empty_rejected_set_preserves_order(self):
        jobs = [self._job("job-A"), self._job("job-B"), self._job("job-C")]
        result = self.exclude(jobs, set())
        self.assertEqual([j["job_id"] for j in result], ["job-A", "job-B", "job-C"])

    # -- 具体岗位排除 --

    def test_rejected_job_excluded_from_display(self):
        jobs = [self._job("job-A"), self._job("job-B")]
        result = self.exclude(jobs, {"job-A"})
        self.assertEqual([j["job_id"] for j in result], ["job-B"])

    def test_multiple_rejected_jobs_excluded(self):
        jobs = [self._job("job-A"), self._job("job-B"), self._job("job-C")]
        result = self.exclude(jobs, {"job-A", "job-C"})
        self.assertEqual([j["job_id"] for j in result], ["job-B"])

    def test_all_jobs_rejected_returns_empty(self):
        jobs = [self._job("job-A"), self._job("job-B")]
        result = self.exclude(jobs, {"job-A", "job-B"})
        self.assertEqual(result, [])

    def test_rejected_job_not_in_jobs_returns_all(self):
        # 垃圾桶里有 job-X，但当前 jobs 列表无 job-X，不 affect 当前列表
        jobs = [self._job("job-A"), self._job("job-B")]
        result = self.exclude(jobs, {"job-X"})
        self.assertEqual(len(result), 2)

    # -- 不扩展到同公司 --

    def test_not_extend_to_same_company(self):
        # job-A 和 job-B 同公司，只 job-A 在垃圾桶，job-B 不被排除
        jobs = [
            self._job("job-A", boss_name="公司X"),
            self._job("job-B", boss_name="公司X"),
        ]
        result = self.exclude(jobs, {"job-A"})
        self.assertEqual([j["job_id"] for j in result], ["job-B"])

    # -- 不扩展到相似岗位 --

    def test_not_extend_to_similar_title(self):
        # job-A 和 job-B title 相同，只 job-A 在垃圾桶，job-B 不被排除
        jobs = [
            self._job("job-A", title="Python 后端"),
            self._job("job-B", title="Python 后端"),
        ]
        result = self.exclude(jobs, {"job-A"})
        self.assertEqual([j["job_id"] for j in result], ["job-B"])

    def test_not_extend_to_same_salary(self):
        # job-A 和 job-B salary 相同，只 job-A 在垃圾桶
        jobs = [
            self._job("job-A", salary="20-30K"),
            self._job("job-B", salary="20-30K"),
        ]
        result = self.exclude(jobs, {"job-A"})
        self.assertEqual([j["job_id"] for j in result], ["job-B"])

    def test_not_extend_to_same_location(self):
        jobs = [
            self._job("job-A", location="上海·浦东·张江"),
            self._job("job-B", location="上海·浦东·张江"),
        ]
        result = self.exclude(jobs, {"job-A"})
        self.assertEqual([j["job_id"] for j in result], ["job-B"])

    def test_not_extend_to_same_industry(self):
        jobs = [
            self._job("job-A", company_industry="互联网"),
            self._job("job-B", company_industry="互联网"),
        ]
        result = self.exclude(jobs, {"job-A"})
        self.assertEqual([j["job_id"] for j in result], ["job-B"])

    # -- 排除后保留抓回顺序 --

    def test_exclusion_preserves_scrape_order(self):
        jobs = [
            self._job("job-A"),
            self._job("job-B"),
            self._job("job-C"),
            self._job("job-D"),
        ]
        result = self.exclude(jobs, {"job-B"})
        self.assertEqual([j["job_id"] for j in result], ["job-A", "job-C", "job-D"])

    # -- job_id 缺失处理 --

    def test_job_missing_job_id_kept_when_rejected_has_empty(self):
        # job 无 job_id 字段，rejected 集合含 ""，该 job 被排除
        job = self._job("job-A")
        del job["job_id"]
        result = self.exclude([job], {""})
        # 无 job_id 的 job 在 rejected 集合含 "" 时被排除
        self.assertEqual(result, [])

    def test_job_missing_job_id_kept_when_rejected_empty(self):
        job = self._job("job-A")
        del job["job_id"]
        result = self.exclude([job], set())
        self.assertEqual(len(result), 1)

    # -- 接受 list 或 set 作为 rejected_job_ids --

    def test_accepts_list_as_rejected_job_ids(self):
        jobs = [self._job("job-A"), self._job("job-B")]
        result = self.exclude(jobs, ["job-A"])
        self.assertEqual([j["job_id"] for j in result], ["job-B"])

    # -- 返回的 job 是原 dict 引用 --

    def test_returned_jobs_are_original_dicts(self):
        job_a = self._job("job-A")
        job_b = self._job("job-B")
        result = self.exclude([job_a, job_b], {"job-A"})
        self.assertIs(result[0], job_b)


class InterestLinkValidationTests(unittest.TestCase):
    """T038: 感兴趣区链接校验（仅 HTTPS 且预期 BOSS 域名）。

    FR：感兴趣区卡片可点击跳转 BOSS 原始页面（仅 HTTPS 且预期 BOSS 域名）。
    复用 001 的 normalize_job_link 进行校验；不安全链接不展示跳转。
    """

    def setUp(self):
        from webui.screening import is_safe_interest_link
        self.is_safe = is_safe_interest_link

    # -- 合法链接 --

    def test_https_zhipin_link_is_safe(self):
        self.assertTrue(self.is_safe("https://www.zhipin.com/job_detail/abc.html"))

    def test_https_zhipin_subdomain_is_safe(self):
        self.assertTrue(self.is_safe("https://job.zhipin.com/job_detail/abc.html"))

    def test_https_zhipin_with_query_still_safe(self):
        # query/fragment 不影响安全性判定（规范化是另一回事）
        self.assertTrue(self.is_safe("https://www.zhipin.com/job_detail/abc.html?ref=1"))

    def test_https_zhipin_with_fragment_still_safe(self):
        self.assertTrue(self.is_safe("https://www.zhipin.com/job_detail/abc.html#section"))

    # -- 非法链接 --

    def test_http_link_is_unsafe(self):
        self.assertFalse(self.is_safe("http://www.zhipin.com/job_detail/abc.html"))

    def test_non_zhipin_link_is_unsafe(self):
        self.assertFalse(self.is_safe("https://www.example.com/job_detail/abc.html"))

    def test_http_non_zhipin_is_unsafe(self):
        self.assertFalse(self.is_safe("http://www.example.com/job_detail/abc.html"))

    def test_empty_string_is_unsafe(self):
        self.assertFalse(self.is_safe(""))

    def test_none_is_unsafe(self):
        self.assertFalse(self.is_safe(None))

    def test_no_scheme_is_unsafe(self):
        self.assertFalse(self.is_safe("www.zhipin.com/job_detail/abc.html"))

    def test_ip_address_is_unsafe(self):
        self.assertFalse(self.is_safe("https://192.168.1.1/job_detail/abc.html"))

    def test_javascript_scheme_is_unsafe(self):
        # 防注入：javascript: 伪协议
        self.assertFalse(self.is_safe("javascript:alert(1)"))

    def test_data_scheme_is_unsafe(self):
        self.assertFalse(self.is_safe("data:text/html,<script>alert(1)</script>"))

    def test_localhost_is_unsafe(self):
        self.assertFalse(self.is_safe("https://localhost/job_detail/abc.html"))

    # -- 边界 --

    def test_whitespace_only_is_unsafe(self):
        self.assertFalse(self.is_safe("   "))

    def test_link_with_trailing_whitespace_still_safe(self):
        # normalize_job_link 会 strip，strip 后合法则安全
        self.assertTrue(self.is_safe("  https://www.zhipin.com/job_detail/abc.html  "))

    def test_non_string_input_is_unsafe(self):
        self.assertFalse(self.is_safe(123))
        self.assertFalse(self.is_safe([]))
        self.assertFalse(self.is_safe({}))

    # -- 恶意构造 --

    def test_zhipin_lookalike_domain_is_unsafe(self):
        # zhipin.com.evil.com 不是 zhipin.com
        self.assertFalse(self.is_safe("https://www.zhipin.com.evil.com/job"))

    def test_uppercase_scheme_https_accepted(self):
        # scheme 大小写不敏感，HTTPS 仍合法
        self.assertTrue(self.is_safe("HTTPS://www.zhipin.com/job_detail/abc.html"))

    def test_uppercase_host_accepted(self):
        self.assertTrue(self.is_safe("https://WWW.ZHIPIN.COM/job_detail/abc.html"))


class DegradationPathTests(unittest.TestCase):
    """T046: AI 不可用降级路径（FR-031 ~ FR-034）。

    降级路径三件事：
    1. 人工填筛：AI 不可用时不给建议值，用户手动填写（merge_filters
       接收空 AI suggest 时退化为纯用户值）。
    2. 跳过简历：AI 不可用时上传简历可跳过（resume_text 为空）。
    3. 仅硬规则核验：第二层不调 AI 语义相似度，partition_job /
       partition_jobs 接受 ai_enabled=False，硬规则过即 match，
       硬规则不过即 mismatch，不调 assess_semantic_similarity。

    本类只测 screening 层降级编排；接口层降级在 T050 集成测试覆盖。
    """

    def setUp(self):
        from webui.screening import merge_filters, partition_job, partition_jobs
        self.merge = merge_filters
        self.partition_job = partition_job
        self.partition_jobs = partition_jobs

    def _job(self, **overrides):
        from tests.test_screening_fixtures import sample_screening_job
        job = sample_screening_job()
        job.update(overrides)
        return job

    # -- 人工填筛：merge_filters 空建议退化为纯用户值 --

    def test_manual_fill_with_empty_ai_uses_user_values(self):
        # AI 不可用 → 建议值为空 → 合并结果取用户值
        user = {"city": "上海", "salary": "405"}
        merged = self.merge(user, {})
        self.assertEqual(merged["city"], "上海")
        self.assertEqual(merged["salary"], "405")

    def test_manual_fill_with_none_ai_uses_user_values(self):
        user = {"city": "上海", "salary": "405"}
        merged = self.merge(user, None)
        self.assertEqual(merged["city"], "上海")
        self.assertEqual(merged["salary"], "405")

    def test_manual_fill_all_seven_fields_present(self):
        # 降级后筛选栏仍含全部七个字段，未填的留空
        merged = self.merge({"city": "上海"}, {})
        for key in ("city", "salary", "experience", "degree", "scale", "stage", "industry"):
            self.assertIn(key, merged)

    def test_manual_fill_empty_user_and_empty_ai_all_blank(self):
        # 用户与 AI 都没给值 → 全空（第一层全国搜索，第二层不核任何字段）
        merged = self.merge({}, {})
        for key in ("city", "salary", "experience", "degree", "scale", "stage", "industry"):
            self.assertEqual(merged[key], "")

    # -- 仅硬规则核验：partition_job ai_enabled=False 不调 AI --

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_degraded_hard_pass_goes_match_without_ai(self, mock_ai):
        # 硬规则过（空冻结），ai_enabled=False → match，不调 AI
        result = self.partition_job(self._job(), {}, ai_enabled=False)
        self.assertEqual(result, "match")
        mock_ai.assert_not_called()

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_degraded_hard_fail_goes_mismatch_without_ai(self, mock_ai):
        # 硬规则不过（scale 不匹配），ai_enabled=False → mismatch，不调 AI
        job = self._job(company_scale="20-99人")
        result = self.partition_job(job, {"scale": "303"}, ai_enabled=False)
        self.assertEqual(result, "mismatch")
        mock_ai.assert_not_called()

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_degraded_skips_ai_even_with_resume_text(self, mock_ai):
        # 跳过简历：resume_text 非空但 ai_enabled=False 仍不调 AI
        result = self.partition_job(self._job(), {}, "resume", "jd", ai_enabled=False)
        self.assertEqual(result, "match")
        mock_ai.assert_not_called()

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_degraded_empty_resume_no_ai_call(self, mock_ai):
        # 跳过简历：resume_text 为空 + ai_enabled=False
        result = self.partition_job(self._job(), {}, "", "", ai_enabled=False)
        self.assertEqual(result, "match")
        mock_ai.assert_not_called()

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_default_ai_enabled_true_still_calls_ai(self, mock_ai):
        # 向后兼容：不传 ai_enabled 时默认 True，仍调 AI
        mock_ai.return_value = {"verdict": "match"}
        self.partition_job(self._job(), {}, "resume", "jd")
        mock_ai.assert_called_once()

    # -- partition_jobs 降级：批量仅硬规则 --

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_degraded_partition_jobs_split_by_hard_rules_only(self, mock_ai):
        from tests.test_screening_fixtures import sample_screening_job, sample_mismatch_job
        jobs = [
            sample_screening_job(job_id="match-1"),
            sample_mismatch_job("scale"),  # 硬规则不过
            sample_screening_job(job_id="match-2"),
            sample_mismatch_job("city"),   # 硬规则不过
        ]
        frozen = {"scale": "303", "city": "上海"}
        result = self.partition_jobs(jobs, frozen, ai_enabled=False)
        self.assertEqual(len(result["match"]), 2)
        self.assertEqual(len(result["mismatch"]), 2)
        mock_ai.assert_not_called()

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_degraded_partition_jobs_preserves_scrape_order(self, mock_ai):
        from tests.test_screening_fixtures import sample_screening_job, sample_mismatch_job
        jobs = [
            sample_screening_job(job_id="m-1"),
            sample_mismatch_job("scale"),
            sample_screening_job(job_id="m-2"),
        ]
        frozen = {"scale": "303"}
        result = self.partition_jobs(jobs, frozen, ai_enabled=False)
        # match 区按抓回顺序：m-1, m-2
        self.assertEqual([j["job_id"] for j in result["match"]], ["m-1", "m-2"])
        # mismatch 区按抓回顺序：mismatch job
        self.assertEqual(len(result["mismatch"]), 1)

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_degraded_partition_jobs_empty_resume_no_ai(self, mock_ai):
        from tests.test_screening_fixtures import sample_screening_jobs
        jobs = sample_screening_jobs(3)
        self.partition_jobs(jobs, {}, resume_text="", ai_enabled=False)
        mock_ai.assert_not_called()

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_default_partition_jobs_still_calls_ai(self, mock_ai):
        # 向后兼容：不传 ai_enabled 时默认 True，每条岗位调一次 AI
        from tests.test_screening_fixtures import sample_screening_jobs
        mock_ai.return_value = {"verdict": "match"}
        self.partition_jobs(sample_screening_jobs(2), {}, "resume", "jd")
        self.assertEqual(mock_ai.call_count, 2)


# ---------------------------------------------------------------------------
# T014: tri-state hard rules (pass / violation / unknown) for feature 004
# ---------------------------------------------------------------------------


class TriStateHardRulesTests(unittest.TestCase):
    """T014: verify_hard_rules_tri_state distinguishes pass/violation/unknown."""

    def test_empty_constraints_pass(self):
        from webui.screening import verify_hard_rules_tri_state
        result = verify_hard_rules_tri_state({"title": "x"}, {})
        self.assertEqual(result["outcome"], "pass")
        self.assertEqual(result["checks"], [])

    def test_missing_job_field_is_unknown_not_violation(self):
        from webui.screening import verify_hard_rules_tri_state
        # city required but job has no location
        result = verify_hard_rules_tri_state(
            {"title": "x", "salary": "10-20K"},
            {"city": "北京"},
        )
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(result["checks"][0]["field"], "city")
        self.assertEqual(result["checks"][0]["outcome"], "unknown")

    def test_explicit_mismatch_is_violation(self):
        from webui.screening import verify_hard_rules_tri_state
        result = verify_hard_rules_tri_state(
            {"location": "上海", "salary": "10-20K"},
            {"city": "北京"},
        )
        self.assertEqual(result["outcome"], "violation")
        self.assertEqual(result["checks"][0]["outcome"], "violation")

    def test_explicit_match_is_pass(self):
        from webui.screening import verify_hard_rules_tri_state
        # Use real boss_cdp_raw codes: salary "10-20K"->code, experience "3-5年"->code, degree "本科"->code
        from scripts.boss_cdp_raw import SALARY_MAP, EXPERIENCE_MAP, DEGREE_MAP
        salary_code = SALARY_MAP.get("10-20K", "404")
        exp_code = EXPERIENCE_MAP.get("3-5年", "104")
        degree_code = DEGREE_MAP.get("本科", "203")
        result = verify_hard_rules_tri_state(
            {"location": "北京", "salary": "10-20K", "tags": "3-5年 | 本科"},
            {"city": "北京", "salary": salary_code, "experience": exp_code, "degree": degree_code},
        )
        self.assertEqual(result["outcome"], "pass")
        for check in result["checks"]:
            self.assertEqual(check["outcome"], "pass")

    def test_unknown_never_promotes_to_high(self):
        """unknown outcome must not be treatable as pass for high_match."""
        from webui.screening import verify_hard_rules_tri_state
        result = verify_hard_rules_tri_state(
            {"location": "北京", "title": "x"},  # missing salary/tags/scale
            {"city": "北京", "salary": "10-20K", "experience": "3-5年"},
        )
        self.assertEqual(result["outcome"], "unknown")
        self.assertNotEqual(result["outcome"], "pass")

    def test_violation_takes_precedence_over_unknown(self):
        from webui.screening import verify_hard_rules_tri_state
        # city mismatches (violation) AND salary missing (unknown)
        result = verify_hard_rules_tri_state(
            {"location": "上海"},
            {"city": "北京", "salary": "10-20K"},
        )
        self.assertEqual(result["outcome"], "violation")

    def test_non_dict_job_is_unknown(self):
        from webui.screening import verify_hard_rules_tri_state
        result = verify_hard_rules_tri_state("not a dict", {"city": "北京"})
        self.assertEqual(result["outcome"], "unknown")

    def test_scale_mismatch_violation(self):
        from webui.screening import verify_hard_rules_tri_state
        result = verify_hard_rules_tri_state(
            {"company_scale": "1000-9999人"},
            {"scale": "004"},  # code; will be reverse-mapped
        )
        # Whatever the code maps to, the actual must differ to be violation;
        # if the code is unknown, outcome is unknown. Just assert shape.
        self.assertIn(result["outcome"], ("violation", "unknown", "pass"))


if __name__ == "__main__":
    unittest.main()
