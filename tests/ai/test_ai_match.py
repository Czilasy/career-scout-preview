"""webui.ai 匹配排序与投影合同测试（027 自 tests/test_ai.py 拆出）。"""

from __future__ import annotations
import json
import unittest
from unittest.mock import patch, MagicMock
import requests
from tests.test_workbench_fixtures import (
    sample_ai_resume_response,
    sample_ai_rank_response,
    sample_ai_preference_response,
    sample_resume_text,
)

from tests.ai.harness import _mock_chat_response


# ---------------------------------------------------------------------------
# rank_jds — batch limits and unknown job_id rejection
# ---------------------------------------------------------------------------


class ResumePlatformProjectionTests(unittest.TestCase):
    """简历分析按平台 schema 投影：BOSS stage / 智联 company_nature。"""

    def test_boss_accepts_stage_and_drops_company_nature(self):
        from webui.ai import _validate_unified_fields
        fields = _validate_unified_fields({
            "keyword": ["Python"], "city": "上海",
            "salary": "20-50K", "experience": "3-5年", "degree": "本科",
            "industry": "互联网", "scale": "100-499人", "stage": "B轮",
            "company_nature": "国企",
        }, "boss")
        self.assertEqual(fields["stage"], ["804"])
        self.assertEqual(fields["experience"], ["105"])
        self.assertNotIn("company_nature", fields)

    def test_zhilian_accepts_company_nature_and_drops_stage(self):
        from webui.ai import _validate_unified_fields
        fields = _validate_unified_fields({
            "keyword": ["Python"], "city": "上海",
            "experience": "3-5年", "degree": "本科", "company_nature": "国企",
            "stage": "B轮", "salary": "not-a-real-salary",
        }, "zhilian")
        self.assertEqual(fields["experience"], ["0305"])
        self.assertEqual(fields["degree"], ["4"])
        self.assertEqual(fields["company_nature"], ["1"])
        self.assertNotIn("stage", fields)
        self.assertEqual(fields["salary"], [])

    def test_sentinel_and_invalid_values_are_dropped(self):
        from webui.ai import _validate_unified_fields
        fields = _validate_unified_fields({
            "keyword": ["Python"], "city": ["上海", "不存在城市"],
            "experience": ["不限", "3-5年", "999"], "stage": ["0", "B轮", "bad"],
        }, "boss")
        self.assertEqual(fields["city"], ["上海"])
        self.assertEqual(fields["experience"], ["105"])
        self.assertEqual(fields["stage"], ["804"])

    def test_prompt_lists_platform_specific_filter_fields(self):
        from webui.ai import _build_field_options_prompt
        boss_prompt = _build_field_options_prompt("boss")
        self.assertIn("stage", boss_prompt)
        self.assertNotIn("company_nature", boss_prompt)
        zhilian_prompt = _build_field_options_prompt("zhilian")
        self.assertIn("company_nature", zhilian_prompt)
        self.assertNotIn("stage", zhilian_prompt)


class RankJdsTests(unittest.TestCase):
    """JD ranking with batch limits and unknown job_id rejection."""

    @staticmethod
    def _echo_mock():
        """Return a side_effect that echoes back the job_ids it receives."""
        def side_effect(*args, **kwargs):
            payload = kwargs.get("json", {})
            user_content = payload["messages"][-1]["content"]
            parsed = json.loads(user_content)
            batch_ids = [j["job_id"] for j in parsed["jobs"]]
            return _mock_chat_response({"ranked_job_ids": batch_ids})
        return side_effect

    @patch("webui.ai.requests.post")
    def test_single_batch_under_limit(self, mock_post):
        from webui.ai import rank_jds

        jobs = [
            {"job_id": "job-001", "title": "Python", "jd": "desc1"},
            {"job_id": "job-002", "title": "Go", "jd": "desc2"},
        ]
        mock_post.return_value = _mock_chat_response({"ranked_job_ids": ["job-002", "job-001"]})

        result = rank_jds({}, jobs, "https://api.example.com", "key")

        self.assertEqual(result, ["job-002", "job-001"])
        self.assertEqual(mock_post.call_count, 1)

    @patch("webui.ai.requests.post")
    def test_batches_at_most_10_jobs_per_call(self, mock_post):
        from webui.ai import rank_jds

        jobs = [
            {"job_id": f"job-{i:03d}", "title": f"Job {i}", "jd": f"描述 {i}"}
            for i in range(25)
        ]
        mock_post.side_effect = self._echo_mock()

        result = rank_jds({}, jobs, "https://api.example.com", "key")

        # 25 jobs / 10 per batch = 3 batches
        self.assertEqual(mock_post.call_count, 3)
        # Each call must have at most 10 jobs
        for call in mock_post.call_args_list:
            payload = call.kwargs["json"]
            user_content = payload["messages"][-1]["content"]
            parsed = json.loads(user_content)
            self.assertLessEqual(len(parsed["jobs"]), 10)
        # All 25 job_ids should be in the result
        self.assertEqual(len(result), 25)

    @patch("webui.ai.requests.post")
    def test_exactly_10_jobs_uses_single_batch(self, mock_post):
        from webui.ai import rank_jds

        jobs = [
            {"job_id": f"job-{i:03d}", "title": f"Job {i}", "jd": "desc"}
            for i in range(10)
        ]
        mock_post.side_effect = self._echo_mock()

        result = rank_jds({}, jobs, "https://api.example.com", "key")

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(len(result), 10)

    @patch("webui.ai.requests.post")
    def test_empty_jobs_returns_empty_without_call(self, mock_post):
        from webui.ai import rank_jds

        result = rank_jds({}, [], "https://api.example.com", "key")

        self.assertEqual(result, [])
        mock_post.assert_not_called()

    @patch("webui.ai.requests.post")
    def test_rejects_unknown_job_id_from_ai(self, mock_post):
        from webui.ai import rank_jds

        jobs = [
            {"job_id": "job-001", "title": "Python", "jd": "desc"},
            {"job_id": "job-002", "title": "Go", "jd": "desc"},
        ]
        # AI returns an id that was not in the input
        mock_post.return_value = _mock_chat_response(
            {"ranked_job_ids": ["job-001", "unknown-job"]}
        )

        with self.assertRaises(ValueError):
            rank_jds({}, jobs, "https://api.example.com", "key")


# ---------------------------------------------------------------------------
# parse_resume — end-to-end with mocked AI
# ---------------------------------------------------------------------------


class ParseResumeTests(unittest.TestCase):
    """End-to-end resume parsing with mocked AI responses."""

    @patch("webui.ai.requests.post")
    def test_successful_parse_returns_validated_fields(self, mock_post):
        from webui.ai import parse_resume

        expected = sample_ai_resume_response()
        mock_post.return_value = _mock_chat_response(expected)

        result = parse_resume(
            sample_resume_text(),
            "https://api.example.com/v1/chat/completions",
            "secret-key",
        )

        self.assertEqual(result["profile_name"], expected["profile_name"])
        self.assertEqual(result["city"], expected["city"])
        self.assertEqual(result["roles"], expected["roles"])
        self.assertEqual(result["skills"], expected["skills"])
        self.assertEqual(result["keywords"], expected["keywords"])

    @patch("webui.ai.requests.post")
    def test_parse_rejects_invalid_ai_output(self, mock_post):
        from webui.ai import parse_resume

        # Missing required fields
        mock_post.return_value = _mock_chat_response({"profile_name": "x"})

        with self.assertRaises(ValueError):
            parse_resume(
                sample_resume_text(),
                "https://api.example.com/v1/chat/completions",
                "secret-key",
            )


# ---------------------------------------------------------------------------
# update_preference — end-to-end with mocked AI
# ---------------------------------------------------------------------------


class UpdatePreferenceTests(unittest.TestCase):
    """End-to-end preference update with mocked AI responses."""

    @patch("webui.ai.requests.post")
    def test_successful_update_returns_validated_preference(self, mock_post):
        from webui.ai import update_preference

        expected = sample_ai_preference_response()
        mock_post.return_value = _mock_chat_response(expected)

        result = update_preference(
            {"name": "Python 后端"},
            [{"action": "interested", "job_id": "job-001"}],
            "https://api.example.com/v1/chat/completions",
            "secret-key",
        )

        self.assertEqual(result["positive_terms"], expected["positive_terms"])
        self.assertEqual(result["negative_terms"], expected["negative_terms"])
        self.assertEqual(result["keyword_weights"], expected["keyword_weights"])

    @patch("webui.ai.requests.post")
    def test_update_rejects_invalid_ai_output(self, mock_post):
        from webui.ai import update_preference

        # Missing required fields
        mock_post.return_value = _mock_chat_response({"positive_terms": ["Python"]})

        with self.assertRaises(ValueError):
            update_preference(
                {"name": "Python 后端"},
                [],
                "https://api.example.com/v1/chat/completions",
                "secret-key",
            )


class MatchJdsFailurePolicyTests(unittest.TestCase):
    """Stage B failures must stay reviewable instead of becoming matches."""

    def test_missing_batch_result_uses_manifest_bounded_single_item_retry(self):
        from webui.ai import match_jds

        jobs = [
            {"job_id": "job-001", "title": "Python", "jd": "Python"},
            {"job_id": "job-002", "title": "Agent", "jd": "Agent"},
        ]
        responses = [
            {"results": [{"i": 0, "match": True, "reason": "匹配"}]},
            {"results": [{"i": 0, "match": False, "reason": "不匹配"}]},
        ]
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        with patch("webui.ai.call_ai", side_effect=responses) as call:
            result = match_jds(
                jobs, "画像", "https://x", "key", batch_size=2,
                concurrency=1, measurement_callback=capture,
                missing_result_retry_budget=1,
            )

        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["verdicts"]["job-001"]["verdict"], "match")
        self.assertEqual(
            result["verdicts"]["job-002"]["verdict"], "not_match"
        )
        terminals = [event for event in events
                     if event["event_type"] == "item_terminal"]
        self.assertEqual(len(terminals), 2)
        self.assertEqual(
            sorted(event["counts"]["item_index"] for event in terminals),
            [0, 1],
        )

    def test_ai_transport_failure_marks_jobs_uncertain(self):
        from webui.ai import AISecurityError, ERROR_NETWORK, match_jds

        jobs = [{
            "job_id": "job-001",
            "title": "Python 后端工程师",
            "salary": "20-30K",
            "location": "上海",
            "jd": "负责 Python 服务开发",
        }]

        with patch(
            "webui.ai.call_ai",
            side_effect=AISecurityError(ERROR_NETWORK),
        ):
            result = match_jds(
                jobs,
                "5 年 Python 后端经验",
                "https://api.example.com/v1/chat/completions",
                "secret-key",
            )

        verdict = result["verdicts"]["job-001"]
        self.assertEqual(verdict["verdict"], "uncertain")
        self.assertIn("待人工确认", verdict["reason"])

    def test_transport_failure_emits_uncertain_terminals_without_batch_retry(self):
        """传输失败批次直接按 uncertain 落库并发终态，不再末尾补一轮。"""
        from webui.ai import AISecurityError, ERROR_NETWORK, match_jds

        jobs = [
            {"job_id": f"job-{i}", "title": "岗位", "jd": "JD"}
            for i in range(4)
        ]
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        with patch(
            "webui.ai.call_ai",
            side_effect=AISecurityError(ERROR_NETWORK),
        ):
            result = match_jds(
                jobs, "画像", "https://x", "key", batch_size=4,
                concurrency=1, measurement_callback=capture,
            )

        terminals = [e for e in events if e["event_type"] == "item_terminal"]
        retries = [e for e in events if e["event_type"] == "retry"]
        self.assertEqual(len(terminals), 4)
        self.assertEqual(retries, [])
        self.assertEqual(
            sorted(e["counts"]["item_index"] for e in terminals),
            [0, 1, 2, 3],
        )
        self.assertEqual(
            [result["verdicts"][f"job-{i}"]["verdict"] for i in range(4)],
            ["uncertain"] * 4,
        )

    def test_systemic_failure_emits_one_terminal_per_remaining_item(self):
        from webui.ai import AISecurityError, ERROR_NETWORK, match_jds

        jobs = [
            {"job_id": f"job-{i}", "title": "岗位", "jd": "JD"}
            for i in range(3)
        ]
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        with patch("webui.ai.call_ai", side_effect=AISecurityError(ERROR_NETWORK)):
            with self.assertRaises(AISecurityError):
                match_jds(
                    jobs, "画像", "https://x", "key", batch_size=2,
                    concurrency=1, measurement_callback=capture,
                    raise_on_systemic=True,
                )

        terminals = [e for e in events if e["event_type"] == "item_terminal"]
        self.assertEqual(
            sorted(e["counts"]["item_index"] for e in terminals), [0, 1, 2]
        )

    def test_transport_failure_does_not_spend_missing_result_retry_budget(self):
        from webui.ai import AISecurityError, ERROR_NETWORK, match_jds

        jobs = [
            {"job_id": "j0", "title": "岗位0", "jd": "JD0"},
            {"job_id": "j1", "title": "岗位1", "jd": "JD1"},
        ]
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        responses = [
            AISecurityError(ERROR_NETWORK),
            {"results": [{"i": 0, "match": True, "reason": "匹配"}]},
            AISecurityError(ERROR_NETWORK),
        ]
        with patch("webui.ai.call_ai", side_effect=responses):
            result = match_jds(
                jobs, "画像", "https://x", "key", batch_size=2,
                concurrency=1, measurement_callback=capture,
                missing_result_retry_budget=1,
            )

        self.assertEqual(result["verdicts"]["j0"]["verdict"], "uncertain")
        self.assertEqual(result["verdicts"]["j1"]["verdict"], "uncertain")
        terminals = [e for e in events if e["event_type"] == "item_terminal"]
        self.assertEqual(
            sorted(e["counts"]["item_index"] for e in terminals), [0, 1]
        )

    def test_consecutive_batch_failures_open_circuit(self):
        from webui.ai import (
            AISecurityError, ERROR_INVALID, ERROR_SERVER, match_jds,
        )

        jobs = [
            {"job_id": f"j{i}", "title": f"岗位{i}", "jd": f"JD{i}"}
            for i in range(6)
        ]
        with patch(
            "webui.ai.call_ai",
            side_effect=AISecurityError(ERROR_INVALID),
        ):
            with self.assertRaises(AISecurityError) as cm:
                match_jds(
                    jobs, "画像", "https://x", "key", batch_size=2,
                    concurrency=1, raise_on_systemic=True,
                )

        exc = cm.exception
        self.assertEqual(exc.error_code, ERROR_SERVER)
        self.assertEqual(exc.diagnostics.get("failure_phase"), "circuit_open")
        self.assertEqual(exc.diagnostics.get("consecutive_failures"), 3)

    def test_successful_batch_resets_circuit(self):
        from webui.ai import AISecurityError, ERROR_INVALID, match_jds

        jobs = [
            {"job_id": f"j{i}", "title": f"岗位{i}", "jd": f"JD{i}"}
            for i in range(6)
        ]
        responses = [
            AISecurityError(ERROR_INVALID),
            AISecurityError(ERROR_INVALID),
            {"results": [
                {"i": 0, "match": True, "reason": "匹配"},
                {"i": 1, "match": False, "reason": "不匹配"},
            ]},
        ]
        with patch("webui.ai.call_ai", side_effect=responses):
            result = match_jds(
                jobs, "画像", "https://x", "key", batch_size=2,
                concurrency=1, raise_on_systemic=True,
            )

        self.assertEqual(result["verdicts"]["j0"]["verdict"], "uncertain")
        self.assertEqual(result["verdicts"]["j2"]["verdict"], "uncertain")
        self.assertEqual(result["verdicts"]["j4"]["verdict"], "match")
        self.assertEqual(result["verdicts"]["j5"]["verdict"], "not_match")

    def test_circuit_inactive_without_raise_on_systemic(self):
        from webui.ai import AISecurityError, ERROR_INVALID, match_jds

        jobs = [
            {"job_id": f"j{i}", "title": f"岗位{i}", "jd": f"JD{i}"}
            for i in range(6)
        ]
        with patch(
            "webui.ai.call_ai",
            side_effect=AISecurityError(ERROR_INVALID),
        ):
            result = match_jds(
                jobs, "画像", "https://x", "key", batch_size=2,
                concurrency=1,
            )

        self.assertEqual(len(result["verdicts"]), 6)
        self.assertTrue(
            all(v["verdict"] == "uncertain" for v in result["verdicts"].values())
        )

    @patch("webui.ai.time.sleep")
    def test_single_invalid_response_retries_once_then_matches(self, _mock_sleep):
        from webui.ai import AISecurityError, ERROR_INVALID, match_jds

        jobs = [{"job_id": "j0", "title": "岗位0", "jd": "JD0"}]
        responses = [
            AISecurityError(ERROR_INVALID),
            {"results": [{"i": 0, "match": True, "reason": "匹配"}]},
        ]
        with patch("webui.ai.call_ai", side_effect=responses) as call:
            result = match_jds(jobs, "画像", "https://x", "key")

        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["verdicts"]["j0"]["verdict"], "match")

    @patch("webui.ai.time.sleep")
    def test_single_invalid_response_retries_once_then_uncertain(self, _mock_sleep):
        from webui.ai import AISecurityError, ERROR_INVALID, match_jds

        jobs = [{"job_id": "j0", "title": "岗位0", "jd": "JD0"}]
        with patch(
            "webui.ai.call_ai",
            side_effect=AISecurityError(ERROR_INVALID),
        ) as call:
            result = match_jds(jobs, "画像", "https://x", "key")

        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["verdicts"]["j0"]["verdict"], "uncertain")
        self.assertIn("待人工确认", result["verdicts"]["j0"]["reason"])

    @patch("webui.ai.time.sleep")
    def test_batch_invalid_response_does_not_retry(self, _mock_sleep):
        from webui.ai import AISecurityError, ERROR_INVALID, match_jds

        jobs = [
            {"job_id": "j0", "title": "岗位0", "jd": "JD0"},
            {"job_id": "j1", "title": "岗位1", "jd": "JD1"},
        ]
        with patch(
            "webui.ai.call_ai",
            side_effect=AISecurityError(ERROR_INVALID),
        ) as call:
            result = match_jds(jobs, "画像", "https://x", "key", batch_size=2)

        self.assertEqual(call.call_count, 1)
        self.assertEqual(result["verdicts"]["j0"]["verdict"], "uncertain")
        self.assertEqual(result["verdicts"]["j1"]["verdict"], "uncertain")

    def test_salary_hard_rule_filters_out_of_range_without_ai(self):
        from webui.ai import match_jds

        jobs = [
            {"job_id": "out", "title": "高薪岗", "salary": "22-30K", "jd": "JD"},
            {"job_id": "overlap", "title": "重叠岗", "salary": "15-25K", "jd": "JD"},
            {"job_id": "daily", "title": "日薪实习", "salary": "400-500元/天", "jd": "JD"},
            {"job_id": "unknown", "title": "面议岗", "salary": "面议", "jd": "JD"},
        ]
        criteria = {"salary": ["403", "404", "405"]}  # 3-5K/5-10K/10-20K
        with patch("webui.ai.call_ai", return_value={"results": [
            {"i": 0, "match": True, "reason": "匹配"},
            {"i": 1, "match": True, "reason": "匹配"},
            {"i": 2, "match": True, "reason": "匹配"},
        ]}) as call:
            result = match_jds(
                jobs, "画像", "https://x", "key",
                batch_size=10, concurrency=1, criteria=criteria,
            )

        self.assertEqual(result["verdicts"]["out"]["verdict"], "not_match")
        self.assertIn("不在筛选范围", result["verdicts"]["out"]["reason"])
        self.assertEqual(result["verdicts"]["overlap"]["verdict"], "match")
        self.assertEqual(result["verdicts"]["daily"]["verdict"], "match")
        self.assertEqual(result["verdicts"]["unknown"]["verdict"], "match")
        self.assertEqual(call.call_count, 1)

    def test_salary_hard_rule_all_out_of_range_skips_ai(self):
        from webui.ai import match_jds

        jobs = [{"job_id": "j1", "title": "高薪岗", "salary": "30-40K", "jd": "JD"}]
        with patch("webui.ai.call_ai") as call:
            result = match_jds(
                jobs, "画像", "https://x", "key",
                batch_size=10, concurrency=1, criteria={"salary": ["403"]},
            )

        self.assertEqual(result["verdicts"]["j1"]["verdict"], "not_match")
        call.assert_not_called()

    def test_structured_experience_degree_hard_filter_without_ai(self):
        """已选经验/学历是硬约束：结构化标签冲突直接 not_match，不交给 AI 自判。"""
        from webui.ai import match_jds

        jobs = [
            {"job_id": "exp3", "title": "3年岗", "salary": "10-15K",
             "tags": "3-5年 | 本科", "jd": "JD"},
            {"job_id": "master", "title": "硕士岗", "salary": "10-15K",
             "tags": "1-3年 | 硕士", "jd": "JD"},
            {"job_id": "ok", "title": "合适岗", "salary": "10-15K",
             "tags": "1-3年 | 本科", "jd": "JD"},
            {"job_id": "unknown", "title": "未标岗", "salary": "10-15K",
             "tags": "", "jd": "JD"},
        ]
        criteria = {"experience": ["101", "103", "104"], "degree": ["202", "203"]}
        with patch("webui.ai.call_ai", return_value={"results": [
            {"i": 0, "match": True, "reason": "合适"},
            {"i": 1, "match": True, "reason": "合适"},
        ]}) as call:
            result = match_jds(
                jobs, "画像", "https://x", "key",
                batch_size=10, concurrency=1, criteria=criteria,
            )

        self.assertEqual(result["verdicts"]["exp3"]["verdict"], "not_match")
        self.assertIn("经验", result["verdicts"]["exp3"]["reason"])
        self.assertEqual(result["verdicts"]["master"]["verdict"], "not_match")
        self.assertIn("学历", result["verdicts"]["master"]["reason"])
        self.assertEqual(result["verdicts"]["ok"]["verdict"], "match")
        self.assertEqual(result["verdicts"]["unknown"]["verdict"], "match")
        self.assertEqual(call.call_count, 1)

    def test_combined_school_freshman_experience_label_is_hard_filtered(self):
        """BOSS 的"在校/应届"合并标签：未选在校/应届时按硬冲突剔除。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "campus", "title": "在校/应届岗", "salary": "10-15K",
                "tags": "在校/应届 | 本科", "jd": "JD"}]
        with patch("webui.ai.call_ai") as call:
            result = match_jds(
                jobs, "画像", "https://x", "key", batch_size=10, concurrency=1,
                criteria={"experience": ["101", "103", "104"],
                          "degree": ["202", "203"]},
            )

        self.assertEqual(result["verdicts"]["campus"]["verdict"], "not_match")
        call.assert_not_called()

    def test_combined_experience_label_kept_when_school_selected(self):
        """选了在校生时，"在校/应届"合并标签不得被硬筛误杀。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "campus", "title": "在校/应届岗", "salary": "10-15K",
                "tags": "在校/应届 | 本科", "jd": "JD"}]
        with patch("webui.ai.call_ai", return_value={"results": [
            {"i": 0, "match": True, "reason": "合适"},
        ]}) as call:
            result = match_jds(
                jobs, "画像", "https://x", "key", batch_size=10, concurrency=1,
                criteria={"experience": ["108"], "degree": ["203"]},
            )

        self.assertEqual(result["verdicts"]["campus"]["verdict"], "match")
        self.assertEqual(call.call_count, 1)


class MatchJdsResumeAndTruncationTests(unittest.TestCase):
    """match_jds 断点续筛（completed_verdicts）与截断拆半重跑。"""

    def _jobs(self, n):
        return [{
            "job_id": f"job-{i:03d}",
            "title": f"岗位{i}",
            "salary": "20-30K",
            "location": "上海",
            "jd": "负责后端开发",
        } for i in range(n)]

    def test_completed_verdicts_are_skipped_and_merged(self):
        from webui.ai import match_jds

        done = {"job-000": {"verdict": "match", "reason": "已筛过"}}
        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": False, "reason": "不合适", "caveats": []}]
        }) as call:
            result = match_jds(
                self._jobs(2), "画像", "https://x", "key",
                batch_size=10, completed_verdicts=done)

        self.assertEqual(call.call_count, 1)  # 只剩 job-001 要筛
        self.assertEqual(result["verdicts"]["job-000"]["verdict"], "match")  # 原样并入
        self.assertEqual(result["verdicts"]["job-001"]["verdict"], "not_match")

    def test_completed_verdicts_none_keeps_legacy_behavior(self):
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [
                {"i": 0, "match": True, "reason": "合适"},
                {"i": 1, "match": True, "reason": "合适"},
            ]
        }) as call:
            result = match_jds(self._jobs(2), "画像", "https://x", "key", batch_size=10)

        self.assertEqual(call.call_count, 1)
        self.assertEqual(len(result["verdicts"]), 2)

    def test_truncated_batch_is_split_until_single(self):
        from webui.ai import AISecurityError, ERROR_TRUNCATED, match_jds

        calls = []

        def fake_call_ai(endpoint, key, messages, **kw):
            payload = json.loads(messages[-1]["content"])
            calls.append(len(payload))
            if len(payload) > 1:
                raise AISecurityError(ERROR_TRUNCATED)
            return {"results": [{"i": 0, "match": True, "reason": "合适", "caveats": []}]}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            result = match_jds(self._jobs(3), "画像", "https://x", "key", batch_size=3)

        # 3 条一批被截断 → 拆 1+2 → 2 还截 → 再拆 1+1：全部单条成功
        self.assertEqual(
            [result["verdicts"][f"job-{i:03d}"]["verdict"] for i in range(3)],
            ["match", "match", "match"])
        self.assertEqual(sorted(calls), [1, 1, 1, 2, 3])

    def test_on_batch_done_serial_reports_each_batch(self):
        from webui.ai import match_jds

        snapshots = []
        with patch("webui.ai.call_ai", return_value={
            "results": [
                {"i": 0, "match": True, "reason": "合适", "caveats": []},
                {"i": 1, "match": False, "reason": "不合适", "caveats": []},
            ]
        }):
            result = match_jds(
                self._jobs(4), "画像", "https://x", "key",
                batch_size=2, concurrency=1,
                on_batch_done=lambda verdicts, done: snapshots.append(
                    (dict(verdicts), sorted(done))
                ),
            )

        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0][1], ["job-000", "job-001"])
        self.assertEqual(snapshots[1][1], ["job-000", "job-001", "job-002", "job-003"])
        self.assertEqual(len(result["verdicts"]), 4)

    def test_on_batch_done_concurrent_reports_cumulative_snapshot(self):
        from webui.ai import match_jds

        snapshots = []
        with patch("webui.ai.call_ai", return_value={
            "results": [
                {"i": 0, "match": True, "reason": "合适", "caveats": []},
                {"i": 1, "match": True, "reason": "合适", "caveats": []},
            ]
        }):
            result = match_jds(
                self._jobs(4), "画像", "https://x", "key",
                batch_size=2, concurrency=2,
                on_batch_done=lambda verdicts, done: snapshots.append(
                    (dict(verdicts), set(done))
                ),
            )

        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[-1][1], {"job-000", "job-001", "job-002", "job-003"})
        self.assertEqual(len(result["verdicts"]), 4)

    def test_on_batch_done_failure_raises_checkpoint_error(self):
        from webui.ai import AICheckpointError, match_jds

        def boom(_verdicts, _done):
            raise RuntimeError("checkpoint rejected")

        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "合适", "caveats": []}]
        }):
            with self.assertRaises(AICheckpointError):
                match_jds(
                    self._jobs(1), "画像", "https://x", "key",
                    batch_size=1, on_batch_done=boom,
                )


class MatchJdsFlagsTests(unittest.TestCase):
    """精筛 flags（B033 靠谱判定）：结构化解析、分级判定、高危强制 not_match。"""

    def _jobs(self, n):
        return [{
            "job_id": f"job-{i:03d}",
            "title": f"岗位{i}",
            "salary": "20-30K",
            "location": "上海",
            "jd": "负责后端开发",
        } for i in range(n)]

    def test_high_flag_forces_not_match_with_prefix(self):
        """命中高危特征：即使 match=true 也强制 not_match，reason 以\"疑似骗局：\"开头。"""
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [{
                "i": 0, "match": True, "reason": "技能契合",
                "caveats": [],
                "flags": [{"code": "C1", "level": "high", "reason": "要求先交培训费"}],
            }]
        }):
            result = match_jds(self._jobs(1), "画像", "https://x", "key", batch_size=10)

        verdict = result["verdicts"]["job-000"]
        self.assertEqual(verdict["verdict"], "not_match")
        self.assertTrue(verdict["reason"].startswith("疑似骗局："))
        self.assertEqual(verdict["flags"][0]["level"], "high")

    def test_high_flag_terminal_event_uses_final_not_match_status(self):
        """高危命中后终态事件与最终 verdict 一致，不再上报原始 AI match。"""
        from webui.ai import match_jds

        events = []

        def capture(event_type, **kwargs):
            events.append((event_type, kwargs))

        with patch("webui.ai.call_ai", return_value={
            "results": [{
                "i": 0, "match": True, "reason": "技能契合",
                "caveats": [],
                "flags": [{"code": "C1", "level": "high", "reason": "要求先交培训费"}],
            }]
        }):
            result = match_jds(
                self._jobs(1), "画像", "https://x", "key",
                batch_size=10, measurement_callback=capture,
            )

        self.assertEqual(result["verdicts"]["job-000"]["verdict"], "not_match")
        terminals = [
            kwargs["counts"].get("status")
            for event_type, kwargs in events if event_type == "item_terminal"
        ]
        self.assertEqual(terminals, ["not_match"])

    def test_high_flag_without_reason_gets_default_prefix(self):
        """高危命中但 AI 未写 reason：reason 补为\"疑似骗局：命中高危可疑特征\"。"""
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [{
                "i": 0, "match": False, "reason": "",
                "flags": [{"code": "E1", "level": "high", "reason": "标题与JD不符"}],
            }]
        }):
            result = match_jds(self._jobs(1), "画像", "https://x", "key", batch_size=10)

        verdict = result["verdicts"]["job-000"]
        self.assertEqual(verdict["verdict"], "not_match")
        self.assertTrue(verdict["reason"].startswith("疑似骗局："))

    def test_single_medium_flag_degrades_to_caveats(self):
        """仅命中 1 条中危：不输出 flags，降级为 caveats 文本。"""
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [{
                "i": 0, "match": True, "reason": "合适",
                "flags": [{"code": "B1", "level": "medium", "reason": "标题含无责底薪"}],
            }]
        }):
            result = match_jds(self._jobs(1), "画像", "https://x", "key", batch_size=10)

        verdict = result["verdicts"]["job-000"]
        self.assertEqual(verdict["flags"], [])
        self.assertTrue(any("需留意" in c for c in verdict["caveats"]))

    def test_two_medium_flags_are_output(self):
        """命中 ≥2 条中危：输出 flags，不改 match 判定。"""
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [{
                "i": 0, "match": True, "reason": "合适",
                "flags": [
                    {"code": "B1", "level": "medium", "reason": "标题含无责底薪"},
                    {"code": "F3", "level": "medium", "reason": "试用期未写明"},
                ],
            }]
        }):
            result = match_jds(self._jobs(1), "画像", "https://x", "key", batch_size=10)

        verdict = result["verdicts"]["job-000"]
        self.assertEqual(verdict["verdict"], "match")
        self.assertEqual(len(verdict["flags"]), 2)
        self.assertTrue(all(f["level"] == "medium" for f in verdict["flags"]))

    def test_flags_missing_field_does_not_break(self):
        """老模型不输出 flags 字段：不报错，flags 为空列表。"""
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "合适"}]
        }):
            result = match_jds(self._jobs(1), "画像", "https://x", "key", batch_size=10)

        self.assertEqual(result["verdicts"]["job-000"]["flags"], [])

    def test_dirty_flag_items_are_dropped(self):
        """旧字符串格式/非法 level/空 reason 的 flags 项丢弃，合法项保留。"""
        from webui.ai import match_jds

        with patch("webui.ai.call_ai", return_value={
            "results": [{
                "i": 0, "match": True, "reason": "合适",
                "flags": [
                    "需留意：疑似中介",  # 旧字符串格式
                    {"code": "X1", "level": "weird", "reason": "非法级别"},
                    {"code": "A1", "level": "medium", "reason": ""},  # 空 reason
                    {"code": "F1", "level": "high", "reason": "JD留个人微信"},
                ],
            }]
        }):
            result = match_jds(self._jobs(1), "画像", "https://x", "key", batch_size=10)

        verdict = result["verdicts"]["job-000"]
        self.assertEqual([f["code"] for f in verdict["flags"]], ["F1"])
        self.assertEqual(verdict["verdict"], "not_match")


class ScreenJobsTruncationTests(unittest.TestCase):
    """screen_jobs 粗筛：截断拆半重跑，单条仍失败则该条保留（防错杀）。"""

    def test_truncated_batch_is_split(self):
        from webui.ai import AISecurityError, ERROR_TRUNCATED, screen_jobs

        jobs = [{
            "job_id": f"j{i}", "title": f"岗位{i}", "salary": "",
            "location": "", "job_labels": "", "company_scale": "",
        } for i in range(3)]

        def fake_call_ai(endpoint, key, messages, **kw):
            lines = messages[-1]["content"].strip().split("\n")
            if len(lines) > 1:
                raise AISecurityError(ERROR_TRUNCATED)
            return {"dropped": []}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            result = screen_jobs(jobs, {"profile_summary": "画像"},
                                 "https://x", "key", batch_size=3, concurrency=1)

        self.assertEqual(sorted(result["kept"]), ["j0", "j1", "j2"])
        self.assertEqual(result["dropped"], [])

    def test_screen_jobs_hard_drops_conflicts_before_ai(self):
        """粗筛阶段结构化硬筛先于 AI：经验/学历冲突直接剔除。"""
        from webui.ai import screen_jobs

        jobs = [
            {"job_id": "exp3", "title": "3年岗", "salary": "10-15K",
             "job_labels": "3-5年 | 本科"},
            {"job_id": "master", "title": "硕士岗", "salary": "10-15K",
             "job_labels": "1-3年 | 硕士"},
            {"job_id": "ok", "title": "合适岗", "salary": "10-15K",
             "job_labels": "1-3年 | 本科"},
        ]
        criteria = {"experience": ["101", "103", "104"], "degree": ["202", "203"]}
        with patch("webui.ai.call_ai", return_value={"dropped": []}) as call:
            result = screen_jobs(
                jobs, criteria, "https://x", "key", batch_size=1)

        self.assertEqual([d["job_id"] for d in result["dropped"]], ["exp3", "master"])
        self.assertEqual(result["kept"], ["ok"])
        call.assert_called_once()

    def test_screen_jobs_accepts_label_criteria_without_hard_dropping(self):
        """硬筛兼容中文标签筛选值：匹配岗位不得被误杀。"""
        from webui.ai import screen_jobs

        jobs = [{"job_id": "ok", "title": "合适岗", "salary": "15-25K",
                "job_labels": "本科"}]
        with patch("webui.ai.call_ai", return_value={"dropped": []}) as call:
            result = screen_jobs(
                jobs, {"degree": ["本科"]}, "https://x", "key", batch_size=1)

        self.assertEqual(result["kept"], ["ok"])
        call.assert_called_once()


class AIScreeningPromptPolicyTests(unittest.TestCase):
    """粗筛/精筛提示词：候选人方向为锚，硬性条件才排除，显式放宽才覆盖默认。"""

    def test_match_jds_system_prompt_uses_candidate_direction_as_anchor(self):
        """精筛以候选人主业方向为锚：跨链路默认不匹配，显式放宽才覆盖。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "job-001", "title": "AI产品客户成功", "jd": "服务AI客户"}]
        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "技能可迁移", "caveats": []}]
        }) as call:
            match_jds(jobs, "AI应用开发，只找正儿八经的应用开发", "https://x", "key", batch_size=1)

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("判断是参考不是法律", prompt)
        self.assertIn("匹配从宽只适用于候选人没有约束的维度", prompt)
        self.assertIn("以候选人自己的主业方向为锚", prompt)
        self.assertIn("明显跨链路的岗位默认 match=false", prompt)
        self.assertIn("用户明确写'不限/都可以/接受xx'", prompt)
        self.assertIn("以 JD 主责为准", prompt)
        self.assertIn("不得把 AI 已识别出的方向冲突、硬性不满足只写进 caveats 后仍判 match", prompt)
        self.assertNotIn("本身不得作为 match=false 的理由", prompt)
        self.assertNotIn("行业、类别、技能不完全一致不排除", prompt)

    def test_screen_jobs_system_prompt_ignores_job_category_for_dropping(self):
        from webui.ai import screen_jobs

        jobs = [{"job_id": "job-001", "title": "教学讲师", "salary": "10-15K", "location": "东莞"}]
        with patch("webui.ai.call_ai", return_value={"dropped": []}) as call:
            screen_jobs(jobs, {"profile_summary": "AI应用开发"}, "https://x", "key", batch_size=1)

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("岗位名称或类别（如客服、讲师、销售、内容制作、运营等）不得单独作为剔除理由", prompt)
        self.assertIn("字段为空或未列出 = 不限", prompt)
        self.assertIn("不得按该维度剔除", prompt)

    def test_screen_jobs_prompt_has_profile_widening_rule(self):
        """初筛 prompt 含求职画像放宽规则（B033，初筛判定逻辑本体不动）。"""
        from webui.ai import screen_jobs

        jobs = [{"job_id": "job-001", "title": "教学讲师", "salary": "10-15K", "location": "东莞"}]
        with patch("webui.ai.call_ai", return_value={"dropped": []}) as call:
            screen_jobs(
                jobs,
                {"profile_summary": "3年经验，东莞、深圳都可以", "city": ["东莞"]},
                "https://x", "key", batch_size=1)

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("求职画像放宽", prompt)
        self.assertIn("以画像表述为准放宽对应判断", prompt)
        self.assertIn("候选人画像（仅用于放宽，不作为硬条件）：3年经验，东莞、深圳都可以", prompt)

    def test_match_jds_prompt_three_channels(self):
        """精筛 prompt 层级：六类字段 > 求职画像 > 隐藏画像字段；未体现不得推断。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "job-001", "title": "AI教学", "jd": "负责AI课程教学"}]
        criteria = {"city": ["东莞"], "degree": ["4"], "salary": ["406"]}
        facts = {
            "core_skills": ["Python"],
            "projects": [{"name": "订单系统", "role": "后端"}],
            "job_type": "全职",
            "languages": ["英语"],
        }
        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "合适", "caveats": []}]
        }) as call:
            match_jds(
                jobs, "3年Python后端，AI相关行业都可以", "https://x", "key",
                batch_size=1, criteria=criteria, profile_facts=facts)

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("【第一层·筛选条件】", prompt)
        self.assertIn("最高优先级，绝对硬约束", prompt)
        self.assertIn("AI相关行业都可以", prompt)
        self.assertIn("【第二层·求职画像】", prompt)
        self.assertIn("【第三层·隐藏画像字段】", prompt)
        self.assertIn("核心技能：Python", prompt)
        self.assertIn("默认匹配，不得写'候选人未知'", prompt)
        self.assertIn("以意愿为准", prompt)
        # 特征清单已并入 prompt，且 flags 为必填字段
        self.assertIn("岗位靠谱判定", prompt)
        self.assertIn("C1（高危）培训收费", prompt)
        self.assertIn("flags 为必填字段，无命中输出空数组", prompt)
        self.assertIn("疑似骗局：", prompt)

    def test_match_jds_prompt_salary_filter_is_hard_rule(self):
        """薪资筛选是硬规则，精筛 prompt 明确禁止 AI 再按薪资范围互相矛盾地拒绝。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "job-001", "title": "后端", "salary": "15-25K", "jd": "负责后端"}]
        with patch("webui.ai.call_ai", return_value={"results": [
            {"i": 0, "match": True, "reason": "合适", "caveats": []}
        ]}) as call:
            match_jds(
                jobs, "画像", "https://x", "key", batch_size=1,
                criteria={"salary": ["405"]})

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("薪资筛选区间已由系统硬性核对", prompt)

    def test_match_jds_prompt_marks_selected_filters_as_hard(self):
        """已选筛选条件在精筛 prompt 中明确为硬约束。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "job-001", "title": "后端", "salary": "15-25K", "jd": "负责后端"}]
        with patch("webui.ai.call_ai", return_value={"results": [
            {"i": 0, "match": True, "reason": "合适", "caveats": []}
        ]}) as call:
            match_jds(
                jobs, "画像", "https://x", "key", batch_size=1,
                criteria={"salary": ["405"], "experience": ["104"], "degree": ["203"]})

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("已确认的筛选条件（薪资/经验/学历/规模/融资/行业）是硬约束", prompt)
        self.assertIn("不得只写 caveats 后仍判 match", prompt)

    def test_match_jds_prompt_includes_structured_tags(self):
        """精筛输入必须带结构化标签，AI 才能看见经验/学历字段。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "job-001", "title": "后端", "salary": "15-25K",
                "location": "东莞", "tags": "1-3年 | 本科", "jd": "负责后端"}]
        with patch("webui.ai.call_ai", return_value={"results": [
            {"i": 0, "match": True, "reason": "合适", "caveats": []}
        ]}) as call:
            match_jds(jobs, "画像", "https://x", "key", batch_size=1)

        user_content = call.call_args.args[2][1]["content"]
        self.assertIn("1-3年 | 本科", user_content)

    def test_match_jds_prompt_without_facts_falls_back(self):
        """老轮无画像事实/筛选条件：退化两通道，不报错。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "job-001", "title": "后端", "jd": "负责后端"}]
        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "合适", "caveats": []}]
        }) as call:
            match_jds(jobs, "画像", "https://x", "key", batch_size=1)

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("（无画像事实，按未体现处理）", prompt)
        self.assertIn("（无明确标准，宽松判断）", prompt)

    def test_match_jds_prompt_marks_unconfirmed_preferences(self):
        """B062：精筛信息包不再含第四层默认偏好，主观维度放松 + caveats 提醒。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "job-001", "title": "后端", "jd": "负责后端"}]
        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "合适", "caveats": []}]
        }) as call:
            match_jds(jobs, "3年Python后端", "https://x", "key", batch_size=1)

        prompt = call.call_args.args[2][0]["content"]
        # 第四层整段移除：不得再出现硬默认措辞。
        self.assertNotIn("【第四层·默认偏好】", prompt)
        self.assertNotIn("只找全职，兼职/外包/按单结算不考虑", prompt)
        self.assertNotIn("不接受996", prompt)
        # 新语义：主观偏好按第三层事实 + 最大接受度，JD 更苛刻时进 caveats。
        self.assertIn("主观偏好", prompt)
        self.assertIn("最大接受度", prompt)
        self.assertIn("标记\"（默认）\"", prompt)
        self.assertIn("不得判不匹配", prompt)
        self.assertIn("实习/兼职与全职冲突", prompt)
        self.assertIn("技术栈硬冲突", prompt)
        self.assertIn("hard_ok", prompt)
        self.assertNotIn("fulltime_ok", prompt)

    def test_match_jds_prompt_jd_hard_requirement_example(self):
        """精筛 prompt 明确 JD 正文硬要求优先于标题/标签，并给出漏判示例。"""
        from webui.ai import match_jds

        jobs = [{"job_id": "job-001", "title": "AI工程师", "salary": "15-18K", "jd": "负责AI开发"}]
        with patch("webui.ai.call_ai", return_value={"results": [
            {"i": 0, "match": True, "reason": "合适", "caveats": []}
        ]}) as call:
            match_jds(jobs, "3年Python后端", "https://x", "key", batch_size=1,
                      criteria={"experience": ["104"]})

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("JD 正文硬要求优先于标题和标签", prompt)
        self.assertIn("必须具备 Python 3年以上生产环境开发经验", prompt)
        self.assertIn("统招公办本科", prompt)
        self.assertIn("2-3年及以上", prompt)
        self.assertIn("硬性要求与已选条件冲突时", prompt)


class FlagFeaturesTests(unittest.TestCase):
    """flag_features 特征清单与分级判定边界（B033 T004 Checkpoint）。"""

    def test_feature_list_has_20_items_with_valid_levels(self):
        from webui.flag_features import FLAG_FEATURES, VALID_FLAG_LEVELS
        self.assertEqual(len(FLAG_FEATURES), 20)
        codes = [f["code"] for f in FLAG_FEATURES]
        self.assertEqual(len(set(codes)), 20)  # code 唯一
        for item in FLAG_FEATURES:
            self.assertIn(item["level"], VALID_FLAG_LEVELS)
            self.assertTrue(item["name"])
            self.assertTrue(item["basis"])

    def test_single_medium_degrades_to_caveats(self):
        from webui.flag_features import clean_flags, decide_flags
        flags = clean_flags([{"code": "B1", "level": "medium", "reason": "标题含无责底薪"}])
        self.assertEqual(decide_flags(flags), {
            "flags": [], "caveats": ["需留意：销售话术标题：标题含无责底薪"]})

    def test_d2_day_rate_is_medium_not_high(self):
        from webui.flag_features import FLAG_FEATURES_BY_CODE
        d2 = FLAG_FEATURES_BY_CODE["D2"]
        self.assertEqual(d2["level"], "medium")
        self.assertIn("元/天", d2["basis"])
        self.assertIn("不单独作为异常", d2["basis"])

    def test_clean_flags_downgrades_d2_day_rate_to_medium(self):
        from webui.flag_features import clean_flags, decide_flags
        flags = clean_flags([{"code": "D2", "level": "high", "reason": "薪资450-600元/天"}])
        self.assertEqual(flags[0]["level"], "medium")
        decided = decide_flags(flags)
        self.assertEqual(decided["flags"], [])
        self.assertEqual(len(decided["caveats"]), 1)

    def test_two_medium_output_flags(self):
        from webui.flag_features import clean_flags, decide_flags
        flags = clean_flags([
            {"code": "B1", "level": "medium", "reason": "a"},
            {"code": "F3", "level": "medium", "reason": "b"},
        ])
        decided = decide_flags(flags)
        self.assertEqual(len(decided["flags"]), 2)
        self.assertEqual(decided["caveats"], [])

    def test_one_high_output_flags(self):
        from webui.flag_features import clean_flags, decide_flags
        flags = clean_flags([{"code": "C1", "level": "high", "reason": "收培训费"}])
        decided = decide_flags(flags)
        self.assertEqual(len(decided["flags"]), 1)
        self.assertEqual(decided["caveats"], [])

    def test_empty_input(self):
        from webui.flag_features import clean_flags, decide_flags
        self.assertEqual(decide_flags(clean_flags([])), {"flags": [], "caveats": []})
        self.assertEqual(decide_flags(clean_flags(None)), {"flags": [], "caveats": []})

    def test_clean_flags_drops_invalid_items(self):
        from webui.flag_features import clean_flags
        cleaned = clean_flags([
            "旧格式文本",
            {"code": "X", "level": "weird", "reason": "非法级别"},
            {"code": "X", "level": "medium", "reason": ""},
            {"code": "F1", "level": "high", "reason": "留个人微信"},
            {"code": "UNKNOWN", "level": "medium", "reason": "清单外但结构合法"},
        ])
        self.assertEqual([f["code"] for f in cleaned], ["F1", "UNKNOWN"])

    def test_prompt_text_renders_all_features(self):
        from webui.flag_features import build_features_prompt_text
        text = build_features_prompt_text()
        self.assertEqual(text.count("\n- "), 19)
        self.assertIn("C1（高危）培训收费", text)
        self.assertIn("A1（中危）劳务派遣/外包包装", text)
        self.assertNotIn("常年挂着", text)


class ProfileFactsTests(unittest.TestCase):
    """画像事实提取与宽松验证（B033 T005/T006）。"""

    def test_validate_profile_facts_keeps_valid_items(self):
        from webui.profile_facts import validate_profile_facts
        facts = validate_profile_facts({
            "core_skills": ["Python", "Django"],
            "projects": [{"name": "订单系统", "role": "后端", "stack": "Django", "summary": "订单模块"}],
            "job_type": "全职",
            "degree": "本科",
            "degree_type": "统招",
            "languages": ["英语"],
            "week_off": "双休",
        })
        self.assertEqual(facts["core_skills"], ["Python", "Django"])
        self.assertEqual(facts["projects"][0]["name"], "订单系统")
        self.assertEqual(facts["job_type"], "全职")
        self.assertEqual(facts["degree_type"], "统招")
        self.assertEqual(facts["languages"], ["英语"])
        self.assertEqual(facts["week_off"], "双休")

    def test_validate_profile_facts_drops_invalid_items(self):
        from webui.profile_facts import validate_profile_facts
        facts = validate_profile_facts({
            "core_skills": ["Python", 123, "", "  "],
            "projects": [
                {"name": "好项目", "role": "后端"},
                {"stack": "无name的项目"},
                "不是对象",
            ],
            "job_type": "不限",  # 非法枚举
            "languages": [None, "英语"],
        })
        self.assertEqual(facts["core_skills"], ["Python"])
        self.assertEqual([p["name"] for p in facts["projects"]], ["好项目"])
        self.assertNotIn("job_type", facts)
        self.assertEqual(facts["languages"], ["英语"])

    def test_validate_profile_facts_missing_fields(self):
        from webui.profile_facts import validate_profile_facts
        self.assertEqual(validate_profile_facts(None), {})
        self.assertEqual(validate_profile_facts("not-a-dict"), {})
        facts = validate_profile_facts({"job_type": "未体现"})
        self.assertEqual(facts, {"job_type": "未体现"})
        # B062：degree_type 未显式输出时不注入「统招」，统一由描述层 flex 呈现。
        self.assertNotIn("degree_type", validate_profile_facts({"degree": "本科"}))

    def test_validate_profile_facts_keeps_explicit_degree_only(self):
        from webui.profile_facts import validate_profile_facts
        facts = validate_profile_facts({"degree": "本科"})
        self.assertEqual(facts["degree"], "本科")
        self.assertNotIn("degree", validate_profile_facts({"degree": ""}))
        self.assertNotIn("degree", validate_profile_facts({"degree": 123}))

    def test_validate_profile_facts_degree_type_default(self):
        """B062：degree_type 非统招只认明确标志，专升本不当作非统招。"""
        from webui.profile_facts import validate_profile_facts, normalize_degree_type
        self.assertEqual(normalize_degree_type(None), "统招")
        self.assertEqual(normalize_degree_type(""), "统招")
        self.assertEqual(normalize_degree_type("专升本"), "统招")
        self.assertEqual(normalize_degree_type("先专后本"), "统招")
        self.assertEqual(normalize_degree_type("自考本科"), "非统招")
        self.assertEqual(normalize_degree_type("函授"), "非统招")
        self.assertEqual(
            validate_profile_facts({"degree_type": "自考"})["degree_type"], "非统招")

    def test_analyze_resume_extracts_profile_facts(self):
        from webui.ai import analyze_resume_to_fields

        payload = {
            "keyword": [{"word": "Python", "recommended": True}],
            "city": "上海",
            "profile_summary": "3年Python后端经验，本科学历，期望上海15-25K，技能Python/Django，做过后端订单系统。",
            "profile_facts": {
                "core_skills": ["Python", "Django"],
                "projects": [{"name": "订单系统", "role": "后端开发"}],
                "job_type": "全职",
                "languages": ["英语"],
            },
        }
        with patch("webui.ai.call_ai", return_value=payload), \
                patch("webui.ai._resume_bytes_to_text", return_value="3年Python后端"):
            result = analyze_resume_to_fields(b"resume", "txt", "https://x", "key")

        self.assertEqual(result["profile_facts"]["core_skills"], ["Python", "Django"])
        self.assertEqual(result["profile_facts"]["job_type"], "全职")

    def test_analyze_resume_missing_profile_facts(self):
        """老端点不返回 profile_facts：返回空 dict，不报错。"""
        from webui.ai import analyze_resume_to_fields

        payload = {"keyword": [{"word": "Python", "recommended": True}],
                   "city": "上海", "profile_summary": "3年Python后端"}
        with patch("webui.ai.call_ai", return_value=payload), \
                patch("webui.ai._resume_bytes_to_text", return_value="3年Python后端"):
            result = analyze_resume_to_fields(b"resume", "txt", "https://x", "key")

        self.assertEqual(result["profile_facts"], {})

    def test_analyze_resume_missing_keyword_falls_back_to_core_skills(self):
        """AI 漏返 keyword 时，用简历核心技能兜底，确认页不会空着。"""
        from webui.ai import analyze_resume_to_fields

        payload = {
            "profile_summary": "3年Python后端经验",
            "profile_facts": {"core_skills": ["Python", "Django", "FastAPI"]},
        }
        with patch("webui.ai.call_ai", return_value=payload), \
                patch("webui.ai._resume_bytes_to_text", return_value="3年Python后端"):
            result = analyze_resume_to_fields(b"resume", "txt", "https://x", "key")

        self.assertEqual(
            [item["word"] for item in result["keyword"]],
            ["Python", "Django", "FastAPI"])
        self.assertTrue(
            all(item["recommended"] is False for item in result["keyword"]))

    def test_analyze_resume_normalizes_dot_slash_facts_key(self):
        """模型把键写成 ./profile_facts 时也能识别：画像恢复，缺 keyword 走兜底。"""
        from webui.ai import analyze_resume_to_fields

        payload = {
            "./profile_facts": {"core_skills": ["Python", "Django"]},
            "profile_summary": "3年Python后端经验",
        }
        with patch("webui.ai.call_ai", return_value=payload), \
                patch("webui.ai._resume_bytes_to_text", return_value="3年Python后端"):
            result = analyze_resume_to_fields(b"resume", "txt", "https://x", "key")

        self.assertEqual(
            result["profile_facts"]["core_skills"], ["Python", "Django"])
        self.assertEqual(
            [item["word"] for item in result["keyword"]], ["Python", "Django"])

    def test_analyze_resume_no_keyword_no_skills_raises(self):
        """无 keyword 且无技能可兜底时，报错提示重试，不静默进第二页。"""
        from webui.ai import analyze_resume_to_fields

        payload = {"profile_summary": "3年Python后端经验"}
        with patch("webui.ai.call_ai", return_value=payload), \
                patch("webui.ai._resume_bytes_to_text", return_value="3年Python后端"):
            with self.assertRaises(ValueError):
                analyze_resume_to_fields(b"resume", "txt", "https://x", "key")

    def test_analyze_resume_does_not_return_ai_city(self):
        """AI 不代填城市；用户未选择时由执行层按全国兜底。"""
        from webui.ai import analyze_resume_to_fields

        payload = {
            "keyword": [{"word": "Python", "recommended": True}],
            "city": "全国",
            "profile_summary": "Python 后端",
        }
        with patch("webui.ai.call_ai", return_value=payload), \
                patch("webui.ai._resume_bytes_to_text", return_value="Python 后端"):
            result = analyze_resume_to_fields(b"resume", "txt", "https://x", "key")

        self.assertEqual(result["city"], [])

    def test_analyze_resume_prompt_contains_facts_rules(self):
        from webui.ai import analyze_resume_to_fields

        with patch("webui.ai.call_ai", return_value={
            "keyword": [{"word": "Python", "recommended": True}], "city": "", "profile_summary": "s",
        }) as call, patch("webui.ai._resume_bytes_to_text", return_value="简历"):
            analyze_resume_to_fields(b"resume", "txt", "https://x", "key")

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("profile_facts", prompt)
        self.assertIn("core_skills", prompt)
        self.assertIn("job_type", prompt)
        self.assertIn("未体现", prompt)
        self.assertIn("自然语言", prompt)
        self.assertIn("简历里明确写了就填，没写的字段留空", prompt)
        self.assertIn("不输出城市", prompt)
        self.assertIn("简历写了什么就写什么，没写的不补", prompt)
        self.assertIn("projects：只列简历明确写出的项目/工作/实习经历", prompt)
        self.assertIn("degree_type", prompt)
        self.assertIn("week_off", prompt)
        self.assertIn("overtime", prompt)
        self.assertNotIn("事实清单式", prompt)
        self.assertNotIn("第一句写工作年限", prompt)
        self.assertNotIn("禁止评价性概括", prompt)

    def test_analyze_resume_prompt_contains_preference_fill_rules(self):
        """B062：简历分析提示词含新字段填写说明书，不再塞旧硬默认偏好。"""
        from webui.ai import analyze_resume_to_fields

        with patch("webui.ai.call_ai", return_value={
            "keyword": [{"word": "Python", "recommended": True}], "city": "", "profile_summary": "s",
        }) as call, patch("webui.ai._resume_bytes_to_text", return_value="简历"):
            analyze_resume_to_fields(b"resume", "txt", "https://x", "key")

        prompt = call.call_args.args[2][0]["content"]
        self.assertIn("最终总共5-10句", prompt)
        self.assertIn("随机挑1-3个自然补充", prompt)
        self.assertIn("不一次全塞", prompt)
        # 旧硬默认偏好已随第四层移除，改为字段填写说明书驱动：
        self.assertNotIn("只找全职，兼职/外包/按单结算不考虑", prompt)
        self.assertNotIn("不接受996", prompt)
        self.assertIn("逐字段填写说明书", prompt)
        self.assertIn("degree_type：", prompt)
        self.assertIn("week_off：", prompt)
        self.assertIn("overtime：", prompt)
        self.assertIn("默认\"统招\"", prompt)
        self.assertIn("画像里已有该偏好就不重复", prompt)
        self.assertIn("degree", prompt)
        self.assertIn("项目经历只写项目方向、个人角色和所用技术栈", prompt)
        self.assertIn("summary 只写简历明确给出的职责或成果一句话", prompt)


class AIMeasurementEventTests(unittest.TestCase):
    """T016 RED: AI 阶段测量事件 — 请求时长、重试、批次计数、敏感字段拒绝。

    覆盖 FR-030、SC-006、SC-007、data-model.md 2.9。
    screen_jobs/match_jds 必须通过 measurement sink 记录：
    - 每次请求的 attempt、duration、error_code；
    - 退避和截断拆分；
    - 批次输入输出数量；
    - 不保存密钥、原始简历或敏感响应。
    """

    def test_screen_jobs_emits_measurement_events(self):
        """screen_jobs 必须通过 measurement_callback 发射事件。"""
        from webui import ai
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        jobs = [{"job_id": f"j{i}", "title": "T", "company": "C",
                 "salary": "10K", "city": "上海", "jd": "safe"}
                for i in range(3)]

        def fake_call_ai(messages, url, api_key, **kw):
            return {"kept": ["j0", "j1", "j2"], "dropped": []}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            ai.screen_jobs(
                jobs, {"profile_summary": "画像"}, "https://x", "key",
                batch_size=3, concurrency=1,
                measurement_callback=capture,
            )

        event_types = [e["event_type"] for e in events]
        # 必须至少有 request 事件和 batch 事件
        self.assertTrue(any(et in event_types for et in ("request", "batch", "item_terminal")),
                        f"必须发射测量事件，实际: {event_types}")

    def test_screen_jobs_measurement_excludes_api_key(self):
        """SC-006: 测量事件不得包含 api_key。"""
        from webui import ai
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        jobs = [{"job_id": "j0", "title": "T", "company": "C",
                 "salary": "10K", "city": "上海", "jd": "safe"}]

        def fake_call_ai(messages, url, api_key, **kw):
            return {"kept": ["j0"], "dropped": []}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            ai.screen_jobs(
                jobs, {"profile_summary": "画像"}, "https://x",
                "sk-secret-key-12345",
                batch_size=1, concurrency=1,
                measurement_callback=capture,
            )

        for ev in events:
            payload = json.dumps(ev, ensure_ascii=False)
            self.assertNotIn("sk-secret-key-12345", payload,
                              "测量事件不得包含 API key")
            self.assertNotIn("api_key", payload.lower(),
                              "测量事件不得出现 api_key 字段名")

    def test_screen_jobs_measurement_excludes_resume_text(self):
        """SC-006: 测量事件不得包含原始简历文本。"""
        from webui import ai
        events = []
        sensitive_resume = "张三 13800001111 身份证110xxx"

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        jobs = [{"job_id": "j0", "title": "T", "company": "C",
                 "salary": "10K", "city": "上海",
                 "jd": "safe", "resume": sensitive_resume}]

        def fake_call_ai(messages, url, api_key, **kw):
            return {"kept": ["j0"], "dropped": []}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            ai.screen_jobs(
                jobs, {"profile_summary": "画像"}, "https://x", "key",
                batch_size=1, concurrency=1,
                measurement_callback=capture,
            )

        for ev in events:
            payload = json.dumps(ev, ensure_ascii=False)
            self.assertNotIn(sensitive_resume, payload,
                              "测量事件不得包含原始简历文本")

    def test_match_jds_emits_measurement_events(self):
        """match_jds 必须通过 measurement_callback 发射事件。"""
        from webui import ai
        events = []

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        jds = [{"job_id": f"j{i}", "title": "T", "jd": "safe"} for i in range(3)]

        def fake_call_ai(messages, url, api_key, **kw):
            return {"results": [
                {"i": 0, "match": True, "reason": "匹配"},
                {"i": 1, "match": False, "reason": "不匹配"},
                {"i": 2, "match": True, "reason": "匹配"},
            ]}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            ai.match_jds(
                jds, "候选人画像", "https://x", "key",
                batch_size=3, concurrency=1,
                measurement_callback=capture,
            )

        [e["event_type"] for e in events]
        self.assertGreater(len(events), 0, "match_jds 必须发射测量事件")

    def test_match_jds_terminal_events_use_global_indices_across_batches(self):
        """精筛分批后终态索引仍对应原始输入，不能在每批从零开始。"""
        from webui import ai
        events = []
        jobs = [{"job_id": f"j{i}", "title": "T", "jd": "safe"}
                for i in range(5)]

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        def fake_call_ai(messages, url, api_key, **kw):
            return {"results": [
                {"i": 0, "match": True, "reason": "匹配"},
                {"i": 1, "match": False, "reason": "不匹配"},
            ]}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            ai.match_jds(
                jobs, "画像", "https://x", "key", batch_size=2,
                concurrency=1, measurement_callback=capture,
            )

        terminals = [event for event in events
                     if event["event_type"] == "item_terminal"]
        self.assertEqual(
            [event["counts"]["item_index"] for event in terminals],
            [0, 1, 2, 3, 4],
        )
        self.assertTrue(
            all(event["counts"]["input_count"] == 5 for event in terminals)
        )

    def test_match_jds_preserves_runner_assigned_indices_after_rough_filter(self):
        """精筛必须保留粗筛前的原始索引，避免与 dropped 项碰撞。"""
        from webui import ai
        events = []
        jobs = [
            {"job_id": "j1", "title": "T", "jd": "safe",
             "_tuning_measurement_index": 1},
            {"job_id": "j4", "title": "T", "jd": "safe",
             "_tuning_measurement_index": 4},
        ]

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        with patch("webui.ai.call_ai", return_value={"results": [
            {"i": 0, "match": True, "reason": "匹配"},
            {"i": 1, "match": False, "reason": "不匹配"},
        ]}):
            ai.match_jds(
                jobs, "画像", "https://x", "key", batch_size=2,
                measurement_callback=capture, measurement_input_count=5,
            )

        terminals = [event for event in events
                     if event["event_type"] == "item_terminal"]
        self.assertEqual(
            [event["counts"]["item_index"] for event in terminals], [1, 4]
        )
        self.assertTrue(
            all(event["counts"]["input_count"] == 5 for event in terminals)
        )

    def test_screen_jobs_can_emit_only_final_dropped_terminals(self):
        """端到端粗筛保留项仍会进入精筛，只有 dropped 才是最终终态。"""
        from webui import ai
        events = []
        jobs = [{"job_id": f"j{i}", "title": "T", "salary": "10K",
                 "location": "上海"} for i in range(5)]

        def capture(event_type, **fields):
            events.append({"event_type": event_type, **fields})

        def fake_call_ai(messages, url, api_key, **kw):
            return {"dropped": [{"i": 0, "reason": "城市不符"}]}

        with patch("webui.ai.call_ai", side_effect=fake_call_ai):
            ai.screen_jobs(
                jobs, {"profile_summary": "画像"}, "https://x", "key",
                batch_size=2, concurrency=1, measurement_callback=capture,
                emit_kept_terminal=False,
            )

        terminals = [event for event in events
                     if event["event_type"] == "item_terminal"]
        self.assertEqual(
            [event["counts"]["item_index"] for event in terminals],
            [0, 2, 4],
        )
        self.assertTrue(
            all(event["counts"]["status"] == "dropped" for event in terminals)
        )
        self.assertTrue(
            all(event["counts"]["input_count"] == 5 for event in terminals)
        )


class MatchJdsDetailAdmissionTests(unittest.TestCase):
    def test_empty_jd_becomes_uncertain_without_ai_request(self):
        from webui import ai

        jobs = [
            {"job_id": "ready", "title": "后端", "jd": "负责服务开发"},
            {
                "job_id": "missing",
                "title": "未知岗位",
                "jd": "   ",
                "jd_failed_code": "detail_timeout",
                "jd_failed_reason": "详情页超时",
            },
        ]
        with patch("webui.ai.call_ai", return_value={
            "results": [{"i": 0, "match": True, "reason": "匹配", "caveats": [], "flags": []}],
        }) as call_ai:
            result = ai.match_jds(jobs, "候选人画像", "https://example.test", "key")

        payload = json.loads(call_ai.call_args.args[2][1]["content"])
        self.assertEqual([item["i"] for item in payload], [0])
        self.assertEqual(payload[0]["jd"], "负责服务开发")
        self.assertEqual(result["verdicts"]["ready"]["verdict"], "match")
        self.assertEqual(result["verdicts"]["missing"], {
            "verdict": "uncertain",
            "reason": "未抓到 JD（详情页超时），无法精筛",
        })


if __name__ == "__main__":
    unittest.main()
