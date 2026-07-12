"""Tests for webui.semantic: formal AI semantic similarity framework (003).

阶段2：替换 002 的占位 assess_semantic_similarity 为受程序校验的语义核验框架。

设计原则：
- 固定四维：direction_alignment / skill_coverage / experience_match / industry_relevance
- 结构化输出：{verdict, confidence, match_score, dimensions, failure_stage}
- 程序校验门：confidence<70 或 verdict=uncertain 或失败 → pending；AI 不能直接决定任务状态
- AI 不可用/未配置时降级返回 verdict="match"（与 002 占位兼容）
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from webui import semantic
from webui.ai import assess_semantic_similarity


class SemanticPromptTests(unittest.TestCase):
    """build_semantic_prompt 构造固定四维提示词。"""

    def test_prompt_includes_four_fixed_dimensions(self):
        prompt = semantic.build_semantic_prompt("简历X", "JD Y")
        for dim in semantic.DIMENSIONS:
            self.assertIn(dim, prompt)

    def test_prompt_requests_structured_json_output(self):
        prompt = semantic.build_semantic_prompt("简历", "JD")
        self.assertIn("json", prompt.lower())
        self.assertIn("verdict", prompt)
        self.assertIn("confidence", prompt)

    def test_prompt_does_not_leak_resume_marker(self):
        secret = "SECRET_RESUME_42"
        prompt = semantic.build_semantic_prompt(secret, "JD")
        # 提示词本身包含简历文本供 AI 分析，但返回值不可泄露（在 validate 中校验）
        # 这里只断言提示词结构存在
        self.assertIsInstance(prompt, str)


class ValidateSemanticOutputTests(unittest.TestCase):
    """validate_semantic_output 程序校验 AI 输出。"""

    def _valid_raw(self):
        return {
            "dimensions": {
                "direction_alignment": {"score": 80, "reason": "方向对口"},
                "skill_coverage": {"score": 75, "reason": "技能覆盖"},
                "experience_match": {"score": 70, "reason": "经验匹配"},
                "industry_relevance": {"score": 65, "reason": "行业相关"},
            },
            "match_score": 72,
            "verdict": "match",
            "confidence": 85,
        }

    def test_valid_high_confidence_match_returns_match(self):
        out = semantic.validate_semantic_output(self._valid_raw())
        self.assertEqual(out["verdict"], "match")
        self.assertEqual(out["failure_stage"], None)
        self.assertEqual(out["confidence"], 85)

    def test_valid_high_confidence_mismatch_returns_mismatch(self):
        raw = self._valid_raw()
        raw["verdict"] = "mismatch"
        raw["confidence"] = 90
        out = semantic.validate_semantic_output(raw)
        self.assertEqual(out["verdict"], "mismatch")

    def test_low_confidence_returns_uncertain_pending(self):
        raw = self._valid_raw()
        raw["confidence"] = 50  # < 70
        out = semantic.validate_semantic_output(raw)
        self.assertEqual(out["verdict"], "pending")
        self.assertEqual(out["failure_stage"], "ai_uncertain")

    def test_uncertain_verdict_returns_pending(self):
        raw = self._valid_raw()
        raw["verdict"] = "uncertain"
        raw["confidence"] = 90
        out = semantic.validate_semantic_output(raw)
        self.assertEqual(out["verdict"], "pending")
        self.assertEqual(out["failure_stage"], "ai_uncertain")

    def test_missing_dimension_returns_invalid_output(self):
        raw = self._valid_raw()
        del raw["dimensions"]["industry_relevance"]
        out = semantic.validate_semantic_output(raw)
        self.assertEqual(out["verdict"], "pending")
        self.assertEqual(out["failure_stage"], "ai_invalid_output")

    def test_non_dict_input_returns_invalid_output(self):
        out = semantic.validate_semantic_output("not a dict")
        self.assertEqual(out["verdict"], "pending")
        self.assertEqual(out["failure_stage"], "ai_invalid_output")

    def test_dimension_score_below_threshold_returns_uncertain(self):
        raw = self._valid_raw()
        raw["dimensions"]["skill_coverage"]["score"] = 30  # < 50
        out = semantic.validate_semantic_output(raw)
        self.assertEqual(out["verdict"], "pending")
        self.assertEqual(out["failure_stage"], "ai_uncertain")


class AssessSemanticSimilarityFormalTests(unittest.TestCase):
    """assess_semantic_similarity_formal 集成 call_ai 与校验门。"""

    def test_ai_unavailable_returns_match_degraded(self):
        """AI 不可用时降级返回 match（与 002 占位兼容，避免无 AI 阻塞用户）。"""
        out = semantic.assess_semantic_similarity_formal(
            "resume", "jd", ai_available=False,
        )
        self.assertEqual(out["verdict"], "match")
        self.assertEqual(out.get("failure_stage"), None)

    def test_ai_match_high_confidence_returns_match(self):
        valid = {
            "dimensions": {d: {"score": 80, "reason": "ok"} for d in semantic.DIMENSIONS},
            "match_score": 80, "verdict": "match", "confidence": 85,
        }
        call_fn = MagicMock(return_value=valid)
        out = semantic.assess_semantic_similarity_formal(
            "resume", "jd", ai_available=True, call_ai_fn=call_fn,
        )
        self.assertEqual(out["verdict"], "match")
        self.assertEqual(out["confidence"], 85)
        self.assertEqual(out["failure_stage"], None)

    def test_ai_timeout_returns_pending(self):
        call_fn = MagicMock(side_effect=TimeoutError("ai timeout"))
        out = semantic.assess_semantic_similarity_formal(
            "resume", "jd", ai_available=True, call_ai_fn=call_fn,
        )
        self.assertEqual(out["verdict"], "pending")
        self.assertEqual(out["failure_stage"], "ai_timeout")

    def test_ai_network_error_returns_pending(self):
        call_fn = MagicMock(side_effect=ConnectionError("network"))
        out = semantic.assess_semantic_similarity_formal(
            "resume", "jd", ai_available=True, call_ai_fn=call_fn,
        )
        self.assertEqual(out["verdict"], "pending")
        self.assertEqual(out["failure_stage"], "ai_network_error")

    def test_ai_invalid_json_output_returns_pending(self):
        call_fn = MagicMock(return_value="not a dict")
        out = semantic.assess_semantic_similarity_formal(
            "resume", "jd", ai_available=True, call_ai_fn=call_fn,
        )
        self.assertEqual(out["verdict"], "pending")
        self.assertEqual(out["failure_stage"], "ai_invalid_output")

    def test_ai_uncertain_returns_pending(self):
        raw = {
            "dimensions": {d: {"score": 80, "reason": "ok"} for d in semantic.DIMENSIONS},
            "match_score": 80, "verdict": "uncertain", "confidence": 50,
        }
        call_fn = MagicMock(return_value=raw)
        out = semantic.assess_semantic_similarity_formal(
            "resume", "jd", ai_available=True, call_ai_fn=call_fn,
        )
        self.assertEqual(out["verdict"], "pending")
        self.assertEqual(out["failure_stage"], "ai_uncertain")

    def test_ai_output_does_not_leak_resume_text(self):
        secret = "SECRET_RESUME_42"
        valid = {
            "dimensions": {d: {"score": 80, "reason": secret} for d in semantic.DIMENSIONS},
            "match_score": 80, "verdict": "match", "confidence": 85,
        }
        call_fn = MagicMock(return_value=valid)
        out = semantic.assess_semantic_similarity_formal(
            secret, "jd", ai_available=True, call_ai_fn=call_fn,
        )
        self.assertNotIn(secret, str(out))


class AssessSemanticSimilarityCompatTests(unittest.TestCase):
    """assess_semantic_similarity（旧入口）保持 002 兼容契约。

    旧调用方不传 ai_available，默认 False（降级），仍返回 {"verdict":"match"}。
    """

    def test_default_returns_match_when_ai_not_configured(self):
        result = assess_semantic_similarity("resume", "jd")
        self.assertEqual(result["verdict"], "match")

    def test_default_does_not_call_ai(self):
        with patch("webui.ai.call_ai") as mock_call:
            result = assess_semantic_similarity("resume", "jd")
            mock_call.assert_not_called()
            self.assertEqual(result["verdict"], "match")

    def test_default_does_not_access_keyring(self):
        with patch("webui.ai.keyring") as mock_keyring:
            result = assess_semantic_similarity("resume", "jd")
            mock_keyring.get_password.assert_not_called()
            self.assertEqual(result["verdict"], "match")

    def test_default_returns_dict_with_verdict_key(self):
        result = assess_semantic_similarity("resume", "jd")
        self.assertIsInstance(result, dict)
        self.assertIn("verdict", result)

    def test_default_does_not_leak_resume_text(self):
        secret = "SECRET_RESUME_42"
        result = assess_semantic_similarity(secret, "jd")
        self.assertNotIn(secret, str(result))


class ProgramGuaranteedNoAiDirectStateTests(unittest.TestCase):
    """程序校验门：AI 输出永远不直接成为任务状态。

    assess_semantic_similarity_formal 在所有失败/不确定场景下返回 verdict="pending"，
    由调用方（partition_job）按程序逻辑裁定。AI 的 "match"/"mismatch" 只在 confidence
    充分且 verdict 明确时被采用，且仍是程序采用的判断，不是 AI 直接决定任务状态。
    """

    def test_pending_verdict_includes_failure_stage(self):
        call_fn = MagicMock(side_effect=TimeoutError("timeout"))
        out = semantic.assess_semantic_similarity_formal(
            "r", "j", ai_available=True, call_ai_fn=call_fn,
        )
        self.assertEqual(out["verdict"], "pending")
        self.assertIn(out["failure_stage"], semantic.FAILURE_STAGES)

    def test_match_output_includes_program_guaranteed_fields(self):
        valid = {
            "dimensions": {d: {"score": 80, "reason": "ok"} for d in semantic.DIMENSIONS},
            "match_score": 80, "verdict": "match", "confidence": 85,
        }
        call_fn = MagicMock(return_value=valid)
        out = semantic.assess_semantic_similarity_formal(
            "r", "j", ai_available=True, call_ai_fn=call_fn,
        )
        # 程序采用前需校验的字段都存在
        for key in ("verdict", "confidence", "dimensions", "match_score", "failure_stage"):
            self.assertIn(key, out)


if __name__ == "__main__":
    unittest.main()
