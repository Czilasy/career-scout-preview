"""Unit tests for webui.candidate (feature 004)."""

from __future__ import annotations

import unittest
import ast
import inspect
import copy
from unittest import mock

from webui import candidate


RESUME = (
    "张三 高级后端开发工程师\n"
    "5年 Python 后端经验，熟悉 Django/Flask，主导风控系统设计与上线。\n"
    "熟练使用 MySQL/Redis/Kafka，负责团队 6 人技术管理。\n"
    "本科计算机科学与技术专业毕业。\n"
)


def _valid_response():
    return {
        "summary": {
            "headline": "高级后端开发工程师",
            "experience_level": "高级",
            "domains": ["后端", "风控"],
            "strengths": ["Python", "系统设计"],
        },
        "evidence": [
            {
                "client_ref": "e1",
                "type": "skill",
                "normalized_value": "Python",
                "safe_excerpt": "Python 后端",
                "source_quote": "Python 后端",
                "source_locator": {"start": 16, "end": 25},
                "assertion_type": "explicit",
                "confidence": 95,
            },
            {
                "client_ref": "e2",
                "type": "responsibility",
                "normalized_value": "风控系统设计",
                "safe_excerpt": "主导风控系统设计",
                "source_quote": "主导风控系统设计",
                "source_locator": {"start": 44, "end": 52},
                "assertion_type": "explicit",
                "confidence": 90,
            },
        ],
        "unknowns": [
            {"field": "current_city", "message": "简历未提及当前所在城市"},
        ],
        "directions": [
            {
                "client_ref": "d1",
                "name": "后端开发工程师",
                "type": "core",
                "rationale": "5年后端经验且主导系统设计",
                "evidence_refs": ["e1", "e2"],
                "gaps": [],
                "confidence": 92,
                "default_enabled": True,
                "search_terms": ["Python 后端", "后端开发"],
            },
        ],
    }


class CandidateAnalysisContractTests(unittest.TestCase):
    """T010: candidate analysis AI output contract v1 validation."""

    def test_valid_response_returns_sanitized(self):
        result = candidate.validate_candidate_analysis(_valid_response(), RESUME)
        self.assertEqual(result["summary"]["headline"], "高级后端开发工程师")
        self.assertEqual(result["contract_version"], "v2")
        self.assertEqual(len(result["evidence"]), 2)
        self.assertEqual(len(result["directions"]), 1)
        self.assertFalse(result["evidence"][0]["sensitive"])

    def test_missing_summary_raises(self):
        data = _valid_response()
        del data["summary"]
        with self.assertRaises(ValueError) as ctx:
            candidate.validate_candidate_analysis(data, RESUME)
        self.assertIn("summary", str(ctx.exception))

    def test_missing_evidence_raises(self):
        data = _valid_response()
        del data["evidence"]
        with self.assertRaises(ValueError):
            candidate.validate_candidate_analysis(data, RESUME)

    def test_missing_unknowns_raises(self):
        data = _valid_response()
        del data["unknowns"]
        with self.assertRaises(ValueError):
            candidate.validate_candidate_analysis(data, RESUME)

    def test_missing_directions_raises(self):
        data = _valid_response()
        del data["directions"]
        with self.assertRaises(ValueError):
            candidate.validate_candidate_analysis(data, RESUME)

    def test_out_of_range_confidence_raises(self):
        data = _valid_response()
        data["evidence"][0]["confidence"] = 150
        with self.assertRaises(ValueError):
            candidate.validate_candidate_analysis(data, RESUME)

    def test_bool_confidence_rejected(self):
        data = _valid_response()
        data["evidence"][0]["confidence"] = True
        with self.assertRaises(ValueError):
            candidate.validate_candidate_analysis(data, RESUME)

    def test_evidence_ref_must_resolve(self):
        data = _valid_response()
        data["directions"][0]["evidence_refs"] = ["e1", "eX"]
        with self.assertRaises(ValueError) as ctx:
            candidate.validate_candidate_analysis(data, RESUME)
        self.assertIn("evidence_ref", str(ctx.exception))

    def test_sensitive_evidence_rejected(self):
        data = _valid_response()
        data["evidence"][0]["normalized_value"] = "13912345678"
        with self.assertRaises(ValueError) as ctx:
            candidate.validate_candidate_analysis(data, RESUME)
        self.assertIn("sensitive", str(ctx.exception))

    def test_locator_out_of_range_rejected(self):
        data = _valid_response()
        # P7: v2 契约下 locator 越界检查 — end 超出 canonical 长度
        data["evidence"][0]["source_locator"] = {"start": 0, "end": 999999}
        # source_quote 不变（仍为 "Python 后端"），但 locator 越界先于 slice 检查
        with self.assertRaises(ValueError) as ctx:
            candidate.validate_candidate_analysis(data, RESUME)
        self.assertIn("out_of_range", str(ctx.exception))

    def test_too_many_directions_rejected(self):
        data = _valid_response()
        data["directions"] = [
            {
                "client_ref": f"d{i}",
                "name": f"方向{i}",
                "type": "adjacent",
                "rationale": "x",
                "evidence_refs": [],
                "gaps": [],
                "confidence": 50,
                "default_enabled": False,
                "search_terms": [f"term{i}"],
            }
            for i in range(6)
        ]
        with self.assertRaises(ValueError) as ctx:
            candidate.validate_candidate_analysis(data, RESUME)
        self.assertIn("too_many", str(ctx.exception))

    def test_too_many_search_terms_rejected(self):
        data = _valid_response()
        data["directions"][0]["search_terms"] = ["a", "b", "c", "d"]
        with self.assertRaises(ValueError) as ctx:
            candidate.validate_candidate_analysis(data, RESUME)
        self.assertIn("search_terms", str(ctx.exception))

    def test_default_enabled_without_evidence_rejected(self):
        data = _valid_response()
        data["directions"][0]["evidence_refs"] = []
        data["directions"][0]["default_enabled"] = True
        with self.assertRaises(ValueError):
            candidate.validate_candidate_analysis(data, RESUME)

    def test_default_enabled_low_confidence_rejected(self):
        data = _valid_response()
        data["directions"][0]["confidence"] = 30
        with self.assertRaises(ValueError):
            candidate.validate_candidate_analysis(data, RESUME)

    def test_invalid_evidence_type_rejected(self):
        data = _valid_response()
        data["evidence"][0]["type"] = "phone_number"
        with self.assertRaises(ValueError):
            candidate.validate_candidate_analysis(data, RESUME)

    def test_duplicate_evidence_ref_rejected(self):
        data = _valid_response()
        data["evidence"].append(dict(data["evidence"][0]))
        with self.assertRaises(ValueError):
            candidate.validate_candidate_analysis(data, RESUME)

    def test_invalid_unknown_field_rejected(self):
        data = _valid_response()
        data["unknowns"][0]["field"] = "phone"
        with self.assertRaises(ValueError):
            candidate.validate_candidate_analysis(data, RESUME)


class EvidenceNormalizationTests(unittest.TestCase):
    """T020: resume evidence normalization and dedup."""

    def test_merges_same_value_different_locators(self):
        items = [
            {
                "evidence_type": "skill",
                "normalized_value": "Python",
                "safe_excerpt": "Python 后端",
                "source_locator": {"start": 0, "end": 10},
                "assertion_type": "explicit",
                "confidence": 90,
            },
            {
                "evidence_type": "skill",
                "normalized_value": "python",
                "safe_excerpt": "Python 经验",
                "source_locator": {"start": 5, "end": 20},
                "assertion_type": "explicit",
                "confidence": 80,
            },
        ]
        result = candidate.normalize_evidence(items, RESUME)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["source_locators"]), 2)
        self.assertEqual(result[0]["confidence"], 90)

    def test_drops_sensitive_items(self):
        items = [
            {
                "evidence_type": "other",
                "normalized_value": "13912345678",
                "safe_excerpt": "phone",
                "source_locator": {"start": 0, "end": 5},
                "assertion_type": "explicit",
                "confidence": 90,
            },
        ]
        result = candidate.normalize_evidence(items, RESUME)
        self.assertEqual(result, [])

    def test_drops_locator_out_of_range(self):
        items = [
            {
                "evidence_type": "skill",
                "normalized_value": "Python",
                "safe_excerpt": "x",
                "source_locator": {"start": 0, "end": 999999},
                "assertion_type": "explicit",
                "confidence": 90,
            },
        ]
        result = candidate.normalize_evidence(items, RESUME)
        self.assertEqual(result, [])

    def test_invalid_assertion_type_defaults_to_explicit(self):
        items = [
            {
                "evidence_type": "skill",
                "normalized_value": "Python",
                "safe_excerpt": "x",
                "source_locator": {"start": 0, "end": 10},
                "assertion_type": "guess",
                "confidence": 90,
            },
        ]
        result = candidate.normalize_evidence(items, RESUME)
        self.assertEqual(result[0]["assertion_type"], "explicit")

    def test_redacts_sensitive_excerpt(self):
        items = [
            {
                "evidence_type": "skill",
                "normalized_value": "Python",
                "safe_excerpt": "联系 13912345678",
                "source_locator": {"start": 0, "end": 10},
                "assertion_type": "explicit",
                "confidence": 90,
            },
        ]
        result = candidate.normalize_evidence(items, RESUME)
        self.assertEqual(result[0]["safe_excerpt"], "[REDACTED]")


class DirectionMergeTests(unittest.TestCase):
    """T022: direction merge, evidence linkage, default-enabled gating."""

    def test_merges_synonymous_names(self):
        directions = [
            {
                "name": "后端开发",
                "type": "core",
                "rationale": "r1",
                "evidence_refs": ["e1"],
                "gaps": [],
                "confidence": 80,
                "default_enabled": True,
                "search_terms": ["Python"],
            },
            {
                "name": "backend",
                "type": "core",
                "rationale": "r2",
                "evidence_refs": ["e2"],
                "gaps": ["g1"],
                "confidence": 85,
                "default_enabled": False,
                "search_terms": ["后端"],
            },
        ]
        result = candidate.merge_directions(directions)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "后端开发工程师")
        self.assertEqual(set(result[0]["search_terms"]), {"Python", "后端"})
        self.assertEqual(result[0]["confidence"], 85)

    def test_enforce_policy_caps_at_five(self):
        directions = [
            {
                "name": f"方向{i}",
                "type": "adjacent",
                "rationale": "x",
                "evidence_refs": [f"e{i}"],
                "gaps": [],
                "confidence": 80,
                "default_enabled": True,
                "search_terms": [f"term{i}"],
            }
            for i in range(8)
        ]
        evidence = {f"e{i}": {} for i in range(8)}
        result = candidate.enforce_direction_policy(directions, evidence)
        self.assertEqual(len(result), candidate.MAX_DIRECTIONS)

    def test_enforce_policy_disables_without_evidence_link(self):
        directions = [
            {
                "name": "方向A",
                "type": "core",
                "rationale": "x",
                "evidence_refs": ["e1"],
                "gaps": [],
                "confidence": 90,
                "default_enabled": True,
                "search_terms": ["a"],
            },
        ]
        # evidence_by_id is empty -> evidence_refs filtered out -> disabled
        result = candidate.enforce_direction_policy(directions, {})
        self.assertFalse(result[0]["default_enabled"])

    def test_enforce_policy_keeps_default_enabled_with_evidence(self):
        directions = [
            {
                "name": "方向A",
                "type": "core",
                "rationale": "x",
                "evidence_refs": ["e1"],
                "gaps": [],
                "confidence": 90,
                "default_enabled": True,
                "search_terms": ["a"],
            },
        ]
        result = candidate.enforce_direction_policy(directions, {"e1": {}})
        self.assertTrue(result[0]["default_enabled"])

    def test_enforce_policy_disables_low_confidence(self):
        directions = [
            {
                "name": "方向A",
                "type": "adjacent",
                "rationale": "x",
                "evidence_refs": ["e1"],
                "gaps": [],
                "confidence": 40,
                "default_enabled": True,
                "search_terms": ["a"],
            },
        ]
        result = candidate.enforce_direction_policy(directions, {"e1": {}})
        self.assertFalse(result[0]["default_enabled"])


class RedactPiiTests(unittest.TestCase):
    """T049/T050: sensitive field redaction."""

    def test_redacts_phone(self):
        self.assertEqual(
            candidate.redact_pii("联系 13912345678"),
            "联系 [REDACTED]",
        )

    def test_redacts_email(self):
        self.assertEqual(
            candidate.redact_pii("邮箱 a@b.com"),
            "邮箱 [REDACTED]",
        )

    def test_redacts_id_card(self):
        self.assertEqual(
            candidate.redact_pii("身份证 110101199001011234"),
            "身份证 [REDACTED]",
        )

    def test_empty_text_returns_empty(self):
        self.assertEqual(candidate.redact_pii(""), "")

    def test_clean_text_unchanged(self):
        self.assertEqual(candidate.redact_pii("Python 后端"), "Python 后端")


class CanonicalizeResumeTextV2Tests(unittest.TestCase):
    """T103: canonicalize_resume_text_v2 规范化简历文本。

    v2 合同要求：程序规范化本次分析使用的简历文本，offsets 是 Unicode
    code-point indexes into this exact text。
    """

    def test_canonicalize_function_exists(self):
        self.assertTrue(callable(getattr(candidate, "canonicalize_resume_text_v2", None)))

    def test_canonicalize_returns_string(self):
        result = candidate.canonicalize_resume_text_v2("简历文本")
        self.assertIsInstance(result, str)

    def test_canonicalize_preserves_unicode_content(self):
        text = "张三 后端工程师\n5年 Python 经验"
        result = candidate.canonicalize_resume_text_v2(text)
        self.assertIn("张三", result)
        self.assertIn("Python", result)

    def test_canonicalize_normalizes_whitespace_consistently(self):
        """不同换行/空格组合应规范化为一致形式。"""
        a = candidate.canonicalize_resume_text_v2("Python\n后端")
        b = candidate.canonicalize_resume_text_v2("Python\r\n后端")
        self.assertEqual(a, b)

    def test_canonicalize_empty_returns_empty(self):
        self.assertEqual(candidate.canonicalize_resume_text_v2(""), "")


class ResolveEvidenceQuoteTests(unittest.TestCase):
    """T103: resolve_evidence_quote exact-quote 定位。

    v2 合同要求：
    - 程序查找唯一精确匹配并生成 Unicode code-point start/end。
    - 必须再次验证切片内容一致。
    - 未找到、重复歧义、敏感摘录或错误引用必须拒绝。
    - 不得使用 fuzzy matching 猜测来源。
    """

    def setUp(self):
        self.resume_text = (
            "姓名：李四\n"
            "联系方式：13800138000\n"
            "证件号：110101199001011234\n"
            "求职意向：后端开发工程师\n"
            "工作经历：负责订单服务设计与维护，使用 Python、Go。\n"
            "技能：Python、Go、Java\n"
        )
        self.canonical = candidate.canonicalize_resume_text_v2(self.resume_text)

    def test_resolve_function_exists(self):
        self.assertTrue(callable(getattr(candidate, "resolve_evidence_quote", None)))

    def test_valid_unique_quote_returns_correct_locator(self):
        locator = candidate.resolve_evidence_quote("订单服务设计与维护", self.canonical)
        self.assertIsInstance(locator, dict)
        self.assertIn("start", locator)
        self.assertIn("end", locator)

    def test_resolved_locator_slice_equals_quote(self):
        """核心：切片内容必须 == source_quote。"""
        quote = "订单服务设计与维护"
        locator = candidate.resolve_evidence_quote(quote, self.canonical)
        start, end = locator["start"], locator["end"]
        self.assertEqual(self.canonical[start:end], quote)

    def test_unicode_code_point_offset_correct(self):
        """start/end 是 Unicode code-point offset，不是 byte offset。"""
        quote = "后端开发工程师"
        locator = candidate.resolve_evidence_quote(quote, self.canonical)
        start, end = locator["start"], locator["end"]
        self.assertEqual(self.canonical[start:end], quote)
        # end - start 应等于 Unicode 字符数，不是字节数
        self.assertEqual(end - start, len(quote))

    def test_quote_not_found_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            candidate.resolve_evidence_quote("不存在的经历描述", self.canonical)
        self.assertIn("not_found", str(ctx.exception))

    def test_ambiguous_quote_rejected(self):
        """Python 在简历中出现两次，应拒绝。"""
        with self.assertRaises(ValueError) as ctx:
            candidate.resolve_evidence_quote("Python", self.canonical)
        self.assertIn("ambiguous", str(ctx.exception))

    def test_sensitive_phone_quote_rejected(self):
        """quote 含电话号码，应作为敏感证据拒绝。"""
        with self.assertRaises(ValueError) as ctx:
            candidate.resolve_evidence_quote("联系方式：13800138000", self.canonical)
        self.assertIn("sensitive", str(ctx.exception).lower())

    def test_sensitive_id_quote_rejected(self):
        """quote 含证件号，应作为敏感证据拒绝。"""
        with self.assertRaises(ValueError) as ctx:
            candidate.resolve_evidence_quote("证件号：110101199001011234", self.canonical)
        self.assertIn("sensitive", str(ctx.exception).lower())

    def test_empty_quote_rejected(self):
        with self.assertRaises(ValueError):
            candidate.resolve_evidence_quote("", self.canonical)

    def test_no_fuzzy_matching(self):
        """近似但不精确的 quote 必须被拒绝，不得 fuzzy 猜测。"""
        with self.assertRaises(ValueError):
            # 简历中是"订单服务设计与维护"，故意改成"订单服务设计维护"
            candidate.resolve_evidence_quote("订单服务设计维护", self.canonical)

    def test_locator_start_is_non_negative(self):
        locator = candidate.resolve_evidence_quote("求职意向", self.canonical)
        self.assertGreaterEqual(locator["start"], 0)
        self.assertGreater(locator["end"], locator["start"])


class CandidateContractV2ValidationTests(unittest.TestCase):
    """T107: 加强最终候选人合同校验。

    v2 响应必须满足：
    - source_quote 存在且 canonical_text[start:end] == source_quote
    - safe_excerpt 与 source_quote 对应（是 quote 的 redacted 版本）
    - direction evidence_refs 指向存在的 evidence
    """

    def setUp(self):
        self.resume_text = (
            "姓名：李四\n"
            "求职意向：后端开发工程师\n"
            "工作经历：负责订单服务设计与维护，使用 Python、Go。\n"
            "技能：Python、Go、Java\n"
        )
        self.canonical = candidate.canonicalize_resume_text_v2(self.resume_text)
        # 预解析一个合法 locator
        self.valid_locator = candidate.resolve_evidence_quote(
            "订单服务设计与维护", self.canonical,
        )

    def _valid_v2_response(self):
        return {
            "summary": {
                "headline": "后端开发工程师",
                "experience_level": "中级",
                "domains": ["后端"],
                "strengths": ["Python"],
            },
            "evidence": [
                {
                    "client_ref": "e1",
                    "type": "responsibility",
                    "normalized_value": "订单服务设计",
                    "source_quote": "订单服务设计与维护",
                    "source_locator": dict(self.valid_locator),
                    "safe_excerpt": "订单服务设计与维护",
                    "assertion_type": "explicit",
                    "confidence": 90,
                },
            ],
            "unknowns": [],
            "directions": [
                {
                    "client_ref": "d1",
                    "name": "后端开发工程师",
                    "type": "core",
                    "rationale": "后端服务设计经验",
                    "evidence_refs": ["e1"],
                    "gaps": [],
                    "confidence": 90,
                    "default_enabled": True,
                    "search_terms": ["后端开发"],
                },
            ],
        }

    def test_valid_v2_response_passes_validation(self):
        result = candidate.validate_candidate_analysis(
            self._valid_v2_response(), self.resume_text,
        )
        self.assertEqual(result["evidence"][0]["source_quote"], "订单服务设计与维护")

    def test_locator_slice_must_match_source_quote(self):
        """source_locator 切片必须 == source_quote，否则拒绝。"""
        response = self._valid_v2_response()
        # 故意篡改 locator 使切片不匹配
        response["evidence"][0]["source_locator"] = {"start": 0, "end": 5}
        with self.assertRaises(ValueError) as ctx:
            candidate.validate_candidate_analysis(response, self.resume_text)
        self.assertIn("locator", str(ctx.exception).lower())

    def test_ambiguous_source_quote_rejected_even_with_matching_locator(self):
        """重复摘录不能由模型任意指定其中一个位置。"""
        response = self._valid_v2_response()
        duplicated_resume = self.resume_text + "补充：订单服务设计与维护。\n"

        with self.assertRaises(ValueError) as ctx:
            candidate.validate_candidate_analysis(response, duplicated_resume)

        self.assertEqual(str(ctx.exception), "evidence_quote_ambiguous")

    def test_safe_excerpt_must_correspond_to_quote(self):
        """safe_excerpt 必须是 source_quote 的 redacted 版本，否则拒绝。"""
        response = self._valid_v2_response()
        # safe_excerpt 与 source_quote 完全不相关
        response["evidence"][0]["safe_excerpt"] = "完全无关的内容xyz"
        with self.assertRaises(ValueError) as ctx:
            candidate.validate_candidate_analysis(response, self.resume_text)
        self.assertIn("excerpt", str(ctx.exception).lower())

    def test_unknown_evidence_ref_rejected(self):
        """direction 引用不存在的 evidence → 拒绝。"""
        response = self._valid_v2_response()
        response["directions"][0]["evidence_refs"] = ["e1", "e99"]
        with self.assertRaises(ValueError) as ctx:
            candidate.validate_candidate_analysis(response, self.resume_text)
        self.assertIn("evidence_ref", str(ctx.exception).lower())

    def test_sensitive_source_quote_rejected(self):
        """source_quote 含敏感信息 → 拒绝。"""
        response = self._valid_v2_response()
        response["evidence"][0]["source_quote"] = "13912345678"
        response["evidence"][0]["safe_excerpt"] = "13912345678"
        response["evidence"][0]["source_locator"] = {"start": 0, "end": 11}
        with self.assertRaises(ValueError) as ctx:
            candidate.validate_candidate_analysis(
                response, "电话 13912345678 后端经验",
            )
        self.assertIn("sensitive", str(ctx.exception).lower())

    def test_empty_source_quote_rejected(self):
        """v2 响应 source_quote 为空字符串 → 拒绝。"""
        response = self._valid_v2_response()
        response["evidence"][0]["source_quote"] = ""
        with self.assertRaises(ValueError):
            candidate.validate_candidate_analysis(response, self.resume_text)

    def test_redacted_safe_excerpt_accepted(self):
        """safe_excerpt 是 source_quote 的 redacted 版本 → 通过。"""
        response = self._valid_v2_response()
        # source_quote 不含敏感信息，redact_pii 返回原文
        response["evidence"][0]["safe_excerpt"] = candidate.redact_pii(
            response["evidence"][0]["source_quote"]
        )
        result = candidate.validate_candidate_analysis(response, self.resume_text)
        self.assertEqual(result["evidence"][0]["safe_excerpt"], "订单服务设计与维护")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

class CandidateV3NormalizerTests(unittest.TestCase):
    def test_empty_v3_shape_and_fresh(self):
        a = candidate.build_empty_candidate_analysis()
        b = candidate.build_empty_candidate_analysis()
        self.assertEqual(a["contract_version"], "v3")
        self.assertEqual(a, b)
        a["summary"]["domains"].append("x")
        self.assertEqual(b["summary"]["domains"], [])

    def test_normalizer_quarantines_invalid_and_generates_locator(self):
        resume = "经历：Python后端经验\n"
        data = {"summary":{"headline":"后端","experience_level":"高级","domains":["服务"],"strengths":[]},
                "evidence":[{"client_ref":"e1","type":"skill","normalized_value":"Python","source_quote":"Python后端经验","assertion_type":"explicit","confidence":90,"source_locator":{"start":99,"end":100},"safe_excerpt":"bad"}],
                "directions":[{"client_ref":"d1","name":"后端","type":"core","evidence_refs":["e1","missing","missing"],"search_terms":["Python"],"default_enabled":True}],
                "identity":{"name":"张三"},"extra":1}
        out = candidate.normalize_candidate_analysis(data, resume)
        self.assertEqual(out["evidence"][0]["source_locator"], {"start":3,"end":13})
        self.assertEqual(out["evidence"][0]["safe_excerpt"], "Python后端经验")
        self.assertNotIn("identity", out)
        self.assertFalse(out["directions"][0]["default_enabled"])
        self.assertTrue(out["quality"]["warnings"])

    def _payload(self):
        return {"contract_version":"v3","summary":{"headline":"后端","experience_level":"高级","domains":["服务"],"strengths":["Python"]},"evidence":[{"client_ref":"e1","type":"skill","normalized_value":"Python","source_quote":"Python后端经验","assertion_type":"explicit","confidence":90}],"unknowns":[],"directions":[{"client_ref":"d1","name":"后端","type":"core","rationale":"经验","evidence_refs":["e1"],"gaps":[],"confidence":90,"default_enabled":True,"search_terms":["Python"]}],"quality":{"status":"complete","warnings":[]}}

    def test_v3_skeleton_has_exact_top_keys(self):
        self.assertEqual(set(candidate.build_empty_candidate_analysis()), {"contract_version","summary","evidence","unknowns","directions","quality"})

    def test_contract_constant_is_sole_complete_schema(self):
        c = candidate.CANDIDATE_ANALYSIS_V3_CONTRACT
        self.assertEqual(c["version"], "v3"); self.assertEqual(set(c["top"]), {"contract_version","summary","evidence","unknowns","directions","quality"})
        self.assertEqual(c["top"]["evidence"]["max"], 20); self.assertEqual(c["top"]["directions"]["max"], 5); self.assertEqual(c["direction"]["search_terms"]["max"], 3)
        self.assertEqual(set(c["warning_codes"]), {"invalid_type","invalid_enum","invalid_evidence","sensitive_value","unverified_field","missing_required","reference_invalid"})

    def test_wrong_contract_version_warns_and_keeps_v3(self):
        d=self._payload(); d["contract_version"]="v2"; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertEqual(o["contract_version"],"v3"); self.assertIn({"code":"invalid_enum","path":"contract_version"},o["quality"]["warnings"])

    def test_missing_each_top_key_warns_typed_empty(self):
        for key in ("contract_version","summary","evidence","unknowns","directions"):
            d=self._payload(); d.pop(key,None); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
            self.assertIn({"code":"missing_required","path":key},o["quality"]["warnings"]); self.assertIn(key,o)

    def test_missing_backend_owned_quality_is_not_a_provider_warning(self):
        d=self._payload(); d.pop("quality"); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertNotIn({"code":"missing_required","path":"quality"},o["quality"]["warnings"])

    def test_wrong_summary_types_warn(self):
        d=self._payload(); d["summary"]={"headline":1,"domains":["ok",2],"strengths":"x"}; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertIn({"code":"invalid_type","path":"summary.headline"},o["quality"]["warnings"]); self.assertEqual(o["summary"]["domains"],[])

    def test_unknown_enum_type_and_extra_warn(self):
        d=self._payload(); d["unknowns"]=[{"field":"phone","message":3,"x":"raw"}]; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertIn({"code":"invalid_enum","path":"unknowns[0].field"},o["quality"]["warnings"]); self.assertIn({"code":"unverified_field","path":"unknowns[0].extra"},o["quality"]["warnings"]); self.assertNotIn("'x'",str(o)); self.assertNotIn("raw",str(o))

    def test_invalid_evidence_assertion_confidence_dropped(self):
        d=self._payload(); d["evidence"][0].update({"assertion_type":"guess","confidence":"90"}); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertEqual(o["evidence"],[]); self.assertTrue(any(w["path"]=="evidence[0]" for w in o["quality"]["warnings"]))

    def test_sensitive_notfound_repeated_short_quotes_drop(self):
        d=self._payload(); d["evidence"][0]["source_quote"]="13912345678"; o=candidate.normalize_candidate_analysis(d,"电话13912345678")
        self.assertEqual(o["evidence"],[]); self.assertTrue(any(w["code"]=="sensitive_value" for w in o["quality"]["warnings"]))

    def test_unicode_nfc_quote_persists_canonical_locator(self):
        resume="技能：Cafe\u0301 后端"; d=self._payload(); d["evidence"][0]["source_quote"]="Cafe\u0301"; o=candidate.normalize_candidate_analysis(d,resume)
        self.assertEqual(o["evidence"][0]["source_quote"],"Café"); self.assertEqual(o["evidence"][0]["source_locator"],{"start":3,"end":7})

    def test_long_unique_context_quote_accepted(self):
        d=self._payload(); d["evidence"][0]["source_quote"]="Python后端经验"; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertEqual(len(o["evidence"]),1)

    def test_provider_locator_excerpt_ignored_generated(self):
        d=self._payload(); d["evidence"][0].update({"source_locator":{"start":99,"end":100},"safe_excerpt":"fake"}); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertEqual(o["evidence"][0]["safe_excerpt"],"Python后端经验"); self.assertNotEqual(o["evidence"][0]["source_locator"],{"start":99,"end":100})

    def test_duplicate_and_missing_refs_pruned_disable_direction(self):
        d=self._payload(); d["directions"][0]["evidence_refs"]=["e1","e1","missing"]; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertEqual(o["directions"][0]["evidence_refs"],["e1"]); self.assertFalse(o["directions"][0]["default_enabled"])

    def test_search_terms_limits_and_empty_manual_required(self):
        d=self._payload(); d["directions"][0]["search_terms"]=["a","b","c","d"]; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertEqual(o["directions"][0]["search_terms"],[]); self.assertEqual(o["quality"]["status"],"manual_required")

    def test_direction_invalid_fields_warn(self):
        d=self._payload(); d["directions"][0].update({"type":"bad","gaps":[1],"confidence":"x","default_enabled":"yes"}); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertTrue(any(w["path"].endswith(".type") and w["code"]=="invalid_enum" for w in o["quality"]["warnings"]))

    def test_mixed_payload_partial_preserves_valid_summary(self):
        d=self._payload(); d["evidence"].append({"client_ref":"bad","type":"x"}); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertEqual(o["summary"]["headline"],"后端"); self.assertEqual(o["quality"]["status"],"partial")

    def test_empty_and_summary_only_manual_required(self):
        self.assertEqual(candidate.normalize_candidate_analysis({},"" )["quality"]["status"],"manual_required")
        d={"contract_version":"v3","summary":{"headline":"x"}}; self.assertEqual(candidate.normalize_candidate_analysis(d,"" )["quality"]["status"],"manual_required")

    def test_warnings_closed_deduped_and_safe(self):
        d=self._payload(); d["x"]={"resume":"secret"}; d["summary"]["x"]=1; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        ws=o["quality"]["warnings"]; self.assertEqual(len(ws),len({(w["code"],w["path"]) for w in ws})); self.assertTrue(all(set(w)=={"code","path"} for w in ws)); self.assertNotIn("secret",str(o))

    def test_quality_wrong_types_warn_and_typed_empty(self):
        d=self._payload(); d["quality"]={"status":7,"warnings":"oops"}; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertIn({"code":"invalid_type","path":"quality.status"},o["quality"]["warnings"])

    def test_invalid_normalized_value_drops_evidence_item(self):
        d=self._payload(); d["evidence"][0]["normalized_value"]=99; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertEqual(o["evidence"],[])

    def test_sensitive_normalized_value_quarantines_evidence_item(self):
        d=self._payload(); d["evidence"][0]["normalized_value"]="13912345678"; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertEqual(o["evidence"],[]); self.assertIn({"code":"sensitive_value","path":"evidence[0]"},o["quality"]["warnings"]); self.assertNotIn("13912345678",str(o))

    def test_evidence_limit_twenty_one_warns_and_caps(self):
        d=self._payload(); base=d["evidence"][0]; d["evidence"]=[dict(base,client_ref=f"e{i}",source_quote=f"Python后端经验{i}") for i in range(21)]
        resume=" ".join(f"Python后端经验{i}" for i in range(21)); o=candidate.normalize_candidate_analysis(d,resume)
        self.assertLessEqual(len(o["evidence"]),20); self.assertTrue(any(w["path"]=="evidence" for w in o["quality"]["warnings"]))

    def test_empty_shape_literal_and_nested_freshness(self):
        expected={"contract_version":"v3","summary":{"headline":"","experience_level":"","domains":[],"strengths":[]},"evidence":[],"unknowns":[],"directions":[],"quality":{"status":"complete","warnings":[]}}
        a=candidate.build_empty_candidate_analysis(); b=candidate.build_empty_candidate_analysis(); self.assertEqual(a,expected)
        a["summary"]["domains"].append("x"); a["quality"]["warnings"].append({"code":"x","path":"y"}); self.assertEqual(b,expected)

    def test_schema_program_owned_output_fields_and_literal_enums(self):
        c=candidate.CANDIDATE_ANALYSIS_V3_CONTRACT; self.assertIn("source_quote",c["evidence"]); self.assertEqual(set(c["warning_codes"]),{"invalid_type","invalid_enum","invalid_evidence","sensitive_value","unverified_field","missing_required","reference_invalid"}); self.assertEqual(c["direction"]["search_terms"]["max"],3)

    def test_wrong_top_level_types_each_warn(self):
        for key in ("evidence","unknowns","directions","quality"):
            d=self._payload(); d[key]=None; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertTrue(any(w["path"]==key for w in o["quality"]["warnings"]))

    def test_quality_nested_invalid_status_warning_is_safe(self):
        d=self._payload(); d["quality"]={"status":"bogus","warnings":["raw",{"code":1,"path":2}],"secret":"resume"}; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertNotIn("secret",str(o)); self.assertTrue(all(set(w)=={"code","path"} for w in o["quality"]["warnings"])); self.assertTrue(any(w["path"]=="quality.extra" for w in o["quality"]["warnings"]))

    def test_unknown_message_type_warns_and_unknowns_cap(self):
        d=self._payload(); d["unknowns"]=[{"field":"other","message":1} for _ in range(21)]; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertLessEqual(len(o["unknowns"]),20); self.assertTrue(any(w["path"].endswith(".message") for w in o["quality"]["warnings"]))

    def test_evidence_confidence_bool_float_out_of_range_drop(self):
        for val in (True,1.5,101):
            d=self._payload(); d["evidence"][0]["confidence"]=val; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(o["evidence"],[])
    def test_missing_normalized_value_is_accepted_as_empty(self):
        d=self._payload(); d["evidence"][0].pop("normalized_value"); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(o["evidence"][0]["normalized_value"],"")

    def test_empty_builder_follows_all_contract_empties(self):
        c=candidate.CANDIDATE_ANALYSIS_V3_CONTRACT; top=c["top"]; saved={k:copy.deepcopy(top[k]["empty"]) for k in top if "empty" in top[k]}
        try:
            top["contract_version"]["empty"]="sentinel"; top["evidence"]["empty"]=["x"]; top["unknowns"]["empty"]=["x"]; top["directions"]["empty"]=["x"]; top["quality"]["empty"]={"status":"partial","warnings":[]}
            o=candidate.build_empty_candidate_analysis(); self.assertEqual(o["contract_version"],"sentinel"); self.assertEqual(o["evidence"],["x"]); self.assertEqual(o["quality"]["status"],"partial")
        finally:
            for k,v in saved.items(): top[k]["empty"]=v

    def test_provider_quality_malformed_fields_warn_and_partial(self):
        d=self._payload(); d["quality"]={"status":"bogus","warnings":"raw","extra":1}; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); ws=o["quality"]["warnings"]
        self.assertIn({"code":"invalid_enum","path":"quality.status"},ws); self.assertNotIn("raw",str(o)); self.assertTrue(any(w["path"]=="quality.extra" for w in ws)); self.assertEqual(o["quality"]["status"],"partial")

    def test_non_dict_evidence_members_quarantined_sibling_retained(self):
        d=self._payload(); d["evidence"]=[3,None,d["evidence"][0]]; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(len(o["evidence"]),1); self.assertIn({"code":"invalid_type","path":"evidence[0]"},o["quality"]["warnings"])

    def test_wrong_normalized_value_has_field_warning(self):
        d=self._payload(); d["evidence"][0]["normalized_value"]=3; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(o["evidence"],[]); self.assertIn({"code":"invalid_type","path":"evidence[0].normalized_value"},o["quality"]["warnings"])

    def test_direction_float_confidence_is_typed_zero_and_warns(self):
        d=self._payload(); d["directions"][0]["confidence"]=1.5; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(o["directions"][0]["confidence"],0); self.assertIn({"code":"invalid_type","path":"directions[0].confidence"},o["quality"]["warnings"]); self.assertNotEqual(o["quality"]["status"],"complete")

    def test_provider_warning_unknown_code_is_invalid_enum(self):
        d=self._payload(); d["quality"]={"status":"complete","warnings":[{"code":"raw","path":"quality"}]}; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertIn({"code":"invalid_enum","path":"quality.warnings[0].code"},o["quality"]["warnings"]); self.assertNotIn('"raw"',str(o)); self.assertEqual(o["quality"]["status"],"partial")

    def test_mixed_payload_keeps_clean_direction_and_disables_bad(self):
        d=self._payload(); d["evidence"].append({"client_ref":"bad","type":"bad","source_quote":"不存在","assertion_type":"explicit","confidence":90}); d["directions"].append({"client_ref":"d2","name":"干净","type":"adjacent","rationale":"r","evidence_refs":[],"gaps":[],"confidence":80,"default_enabled":False,"search_terms":["Go"]}); d["directions"][0]["evidence_refs"]=["e1","missing"]; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验 Go")
        self.assertEqual([e["client_ref"] for e in o["evidence"]],["e1"]); self.assertFalse(o["directions"][0]["default_enabled"]); self.assertEqual(o["directions"][1]["name"],"干净"); self.assertEqual(o["quality"]["status"],"partial")

    def test_duplicate_refs_warn_and_nonstring_refs_disable(self):
        d=self._payload(); d["directions"][0]["evidence_refs"]=["e1","e1"]; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertTrue(any(w["code"]=="reference_invalid" for w in o["quality"]["warnings"]))
        d=self._payload(); d["directions"][0]["evidence_refs"]=[1]; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertFalse(o["directions"][0]["default_enabled"])

    def test_search_terms_missing_empty_and_nonstring_disable(self):
        for terms in (None,[],[1]):
            d=self._payload(); d["directions"][0].pop("search_terms",None) if terms is None else d["directions"][0].__setitem__("search_terms",terms); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertFalse(o["directions"][0]["default_enabled"]); self.assertEqual(o["quality"]["status"],"manual_required")

    def test_invalid_gaps_default_and_float_confidence_not_persisted(self):
        d=self._payload(); d["directions"][0]["gaps"]=[1]; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(o["directions"][0]["gaps"],[])
        d=self._payload(); d["directions"][0]["default_enabled"]="yes"; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertFalse(o["directions"][0]["default_enabled"])
        d=self._payload(); d["directions"][0]["confidence"]=1.5; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertNotEqual(o["directions"][0]["confidence"],1.5)

    def test_directions_over_five_warn_and_cap(self):
        d=self._payload(); d["directions"]*=6; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertLessEqual(len(o["directions"]),5); self.assertTrue(any(w["path"]=="directions" for w in o["quality"]["warnings"]))

    def test_fully_shaped_empty_and_summary_only_manual_required(self):
        e={"contract_version":"v3","summary":{"headline":"","experience_level":"","domains":[],"strengths":[]},"evidence":[],"unknowns":[],"directions":[],"quality":{"status":"complete","warnings":[]}}; self.assertEqual(candidate.normalize_candidate_analysis(e,"")["quality"]["status"],"manual_required")
        s=dict(e); s["summary"]["headline"]="x"; self.assertEqual(candidate.normalize_candidate_analysis(s,"")["quality"]["status"],"manual_required")

    def test_clean_payload_is_complete(self):
        o=candidate.normalize_candidate_analysis(self._payload(),"经历：Python后端经验"); self.assertEqual(o["quality"],{"status":"complete","warnings":[]})

    def test_nested_extras_warn_and_not_persisted(self):
        d=self._payload(); d["evidence"][0]["source_context"]="raw"; d["directions"][0]["x"]="raw"; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertNotIn("source_context",o["evidence"][0]); self.assertNotIn("x",o["directions"][0])

    def test_contract_source_has_literal_v3_enums_not_legacy_names(self):
        src=inspect.getsource(candidate); node=ast.parse(src); assign=next(n for n in ast.walk(node) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="CANDIDATE_ANALYSIS_V3_CONTRACT" for t in n.targets)); text=ast.get_source_segment(src,assign)
        for legacy in ("EVIDENCE_TYPES","ASSERTION_TYPES","DIRECTION_TYPES","UNKNOWN_FIELDS","MAX_DIRECTIONS","MAX_SEARCH_TERMS"): self.assertNotIn(legacy,text)
        self.assertEqual(candidate.CANDIDATE_ANALYSIS_V3_CONTRACT["evidence"]["source_locator"]["type"],"object")
        self.assertEqual(candidate.CANDIDATE_ANALYSIS_V3_CONTRACT["evidence"]["safe_excerpt"]["type"],"string")

    def test_empty_shape_derives_contract_metadata(self):
        c=candidate.CANDIDATE_ANALYSIS_V3_CONTRACT; old=copy.deepcopy(c["top"]["summary"]["empty"]); c["top"]["summary"]["empty"]["headline"]="sentinel"
        try: self.assertEqual(candidate.build_empty_candidate_analysis()["summary"]["headline"],"sentinel")
        finally: c["top"]["summary"]["empty"]=old

    def test_provider_quality_is_ignored_and_warned(self):
        d=self._payload(); d["quality"]={"status":"bogus","warnings":[{"code":"raw","path":123},"x"],"extra":1}; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(o["quality"]["status"],"partial"); self.assertNotIn("raw",str(o))

    def test_not_found_and_repeated_short_quotes_drop_with_invalid_evidence(self):
        for quote,resume in (("不存在","经历：Python后端经验"),("Py","Py Py") ):
            d=self._payload(); d["evidence"][0]["source_quote"]=quote; o=candidate.normalize_candidate_analysis(d,resume); self.assertEqual(o["evidence"],[]); self.assertIn({"code":"invalid_evidence","path":"evidence[0]"},o["quality"]["warnings"])

    def test_missing_normalized_value_is_typed_empty_but_wrong_type_drops(self):
        d=self._payload(); d["evidence"][0].pop("normalized_value"); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(o["evidence"][0]["normalized_value"],"")
        d=self._payload(); d["evidence"][0]["normalized_value"]=3; self.assertEqual(candidate.normalize_candidate_analysis(d,"经历：Python后端经验")["evidence"],[])

    def test_missing_evidence_refs_disables_visible_direction(self):
        d=self._payload(); d["directions"][0].pop("evidence_refs"); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(len(o["directions"]),1); self.assertFalse(o["directions"][0]["default_enabled"]); self.assertTrue(any("evidence_refs" in w["path"] for w in o["quality"]["warnings"]))

    def test_clean_disabled_direction_still_complete(self):
        d=self._payload(); d["directions"][0]["default_enabled"]=False; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(o["quality"]["status"],"complete")

    def test_nested_extras_emit_warnings(self):
        d=self._payload(); d["evidence"][0]["source_context"]="raw-evidence"; d["directions"][0]["private_extra"]="raw-direction"; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); warnings=o["quality"]["warnings"]
        self.assertIn({"code":"unverified_field","path":"evidence[0].extra"},warnings); self.assertIn({"code":"unverified_field","path":"directions[0].extra"},warnings); self.assertNotIn("source_context",str(o)); self.assertNotIn("private_extra",str(o)); self.assertNotIn("raw-evidence",str(o)); self.assertNotIn("raw-direction",str(o))

    def test_contract_metadata_owns_all_approved_size_limits(self):
        c=candidate.CANDIDATE_ANALYSIS_V3_CONTRACT
        self.assertEqual(c["summary"]["headline"]["max_length"],200); self.assertEqual(c["summary"]["experience_level"]["max_length"],100)
        for field in ("domains","strengths"): self.assertEqual((c["summary"][field]["max"],c["summary"][field]["item_max_length"]),(20,200))
        self.assertEqual(c["top"]["evidence"]["max"],20); self.assertEqual(c["evidence"]["client_ref"]["max_length"],128); self.assertEqual(c["evidence"]["normalized_value"]["max_length"],500); self.assertEqual(c["evidence"]["source_quote"]["max_length"],2000)
        self.assertEqual(c["top"]["unknowns"]["max"],20); self.assertEqual(c["unknown"]["message"]["max_length"],500)
        self.assertEqual(c["top"]["directions"]["max"],5); self.assertEqual(c["direction"]["client_ref"]["max_length"],128); self.assertEqual(c["direction"]["name"]["max_length"],200); self.assertEqual(c["direction"]["rationale"]["max_length"],1000)
        self.assertEqual((c["direction"]["gaps"]["max"],c["direction"]["gaps"]["item_max_length"]),(20,300)); self.assertEqual((c["direction"]["search_terms"]["max"],c["direction"]["search_terms"]["item_max_length"]),(3,200))

    def test_oversized_summary_values_are_bounded_and_warned(self):
        marker="RAW-SUMMARY-"+"x"*220; d=self._payload(); d["summary"].update({"headline":marker,"experience_level":"y"*101,"domains":["ok"]*21,"strengths":["z"*201]}); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertEqual(o["summary"]["headline"],""); self.assertEqual(o["summary"]["experience_level"],""); self.assertLessEqual(len(o["summary"]["domains"]),20); self.assertEqual(o["summary"]["strengths"],[]); self.assertNotIn(marker,str(o)); self.assertTrue(any(w["path"].startswith("summary.") for w in o["quality"]["warnings"]))

    def test_oversized_evidence_strings_drop_and_list_caps_before_resolve(self):
        d=self._payload(); d["evidence"][0]["client_ref"]="R"*129; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(o["evidence"],[]); self.assertNotIn("R"*129,str(o))
        d=self._payload(); base=d["evidence"][0]; d["evidence"]=[dict(base,client_ref=f"e{i}") for i in range(25)]
        with mock.patch.object(candidate,"resolve_evidence_quote",return_value={"start":0,"end":10}) as resolver: o=candidate.normalize_candidate_analysis(d,"Python后端经验")
        self.assertLessEqual(resolver.call_count,20); self.assertLessEqual(len(o["evidence"]),20); self.assertTrue(any(w["path"]=="evidence" for w in o["quality"]["warnings"]))

    def test_oversized_unknown_message_is_typed_empty_and_safe(self):
        marker="RAW-UNKNOWN-"+"u"*510; d=self._payload(); d["unknowns"]=[{"field":"other","message":marker}]; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(o["unknowns"][0]["message"],""); self.assertNotIn(marker,str(o)); self.assertIn({"code":"invalid_type","path":"unknowns[0].message"},o["quality"]["warnings"])

    def test_oversized_direction_scalars_and_gaps_are_safe_draft(self):
        marker="RAW-DIRECTION-"+"n"*210; d=self._payload(); d["directions"][0].update({"client_ref":"c"*129,"name":marker,"rationale":"r"*1001,"gaps":["ok"]*21+["g"*301]}); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        direction=o["directions"][0]; self.assertEqual(direction["client_ref"],""); self.assertEqual(direction["name"],""); self.assertEqual(direction["rationale"],""); self.assertLessEqual(len(direction["gaps"]),20); self.assertFalse(direction["default_enabled"]); self.assertNotIn(marker,str(o))

    def test_oversized_search_term_is_removed_and_requires_manual_review(self):
        marker="RAW-TERM-"+"t"*201; d=self._payload(); d["directions"][0]["search_terms"]=[marker]; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(o["directions"][0]["search_terms"],[]); self.assertFalse(o["directions"][0]["default_enabled"]); self.assertEqual(o["quality"]["status"],"manual_required"); self.assertNotIn(marker,str(o))

    def test_bounded_direction_degradations_are_partial_not_manual(self):
        cases=(
            ("oversized_scalars",{"name":"n"*201,"rationale":"r"*1001}),
            ("oversized_gaps",{"gaps":[f"gap-{i}" for i in range(21)]}),
            ("lost_ref",{"evidence_refs":["e1","missing"]}),
        )
        for label,changes in cases:
            with self.subTest(label=label):
                d=self._payload(); d["directions"][0].update(changes); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
                self.assertEqual([e["client_ref"] for e in o["evidence"]],["e1"]); self.assertEqual(o["directions"][0]["search_terms"],["Python"]); self.assertFalse(o["directions"][0]["default_enabled"]); self.assertEqual(o["quality"]["status"],"partial")

    def test_oversized_optional_normalized_value_is_empty_and_evidence_retained(self):
        marker="RAW-NORMALIZED-"+"v"*501; d=self._payload(); d["evidence"][0]["normalized_value"]=marker; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertEqual(len(o["evidence"]),1); self.assertEqual(o["evidence"][0]["normalized_value"],""); self.assertIn({"code":"invalid_type","path":"evidence[0].normalized_value"},o["quality"]["warnings"]); self.assertNotIn(marker,str(o))

    def test_direction_with_no_search_terms_is_manual_required(self):
        d=self._payload(); d["directions"][0]["search_terms"]=[]; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertFalse(o["directions"][0]["default_enabled"]); self.assertEqual(o["quality"]["status"],"manual_required")

    def test_provider_extra_key_names_never_leak_into_warning_paths(self):
        raw_key="PII_张三_13912345678"; cases=(
            ("root","root.extra",lambda d:d.__setitem__(raw_key,"raw")),
            ("summary","summary.extra",lambda d:d["summary"].__setitem__(raw_key,"raw")),
            ("evidence","evidence[0].extra",lambda d:d["evidence"][0].__setitem__(raw_key,"raw")),
            ("unknown","unknowns[0].extra",lambda d:(d.__setitem__("unknowns",[{"field":"other","message":"safe"}]),d["unknowns"][0].__setitem__(raw_key,"raw"))),
            ("direction","directions[0].extra",lambda d:d["directions"][0].__setitem__(raw_key,"raw")),
            ("quality","quality.extra",lambda d:d["quality"].__setitem__(raw_key,"raw")),
        )
        for label,path,mutate in cases:
            with self.subTest(label=label):
                d=self._payload(); mutate(d); o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); rendered=str(o)
                self.assertNotIn(raw_key,rendered); self.assertNotIn("张三",rendered); self.assertNotIn("13912345678",rendered); self.assertIn({"code":"unverified_field","path":path},o["quality"]["warnings"])

    def test_missing_or_empty_direction_client_ref_is_visible_disabled_partial(self):
        for label,value in (("missing",None),("empty","")):
            with self.subTest(label=label):
                d=self._payload()
                if value is None: d["directions"][0].pop("client_ref")
                else: d["directions"][0]["client_ref"]=value
                o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验"); self.assertEqual(len(o["directions"]),1); self.assertEqual(o["directions"][0]["client_ref"],""); self.assertFalse(o["directions"][0]["default_enabled"]); self.assertIn({"code":"missing_required","path":"directions[0].client_ref"},o["quality"]["warnings"]); self.assertEqual(o["quality"]["status"],"partial")

    def test_provider_warning_empty_path_is_rejected_safely(self):
        d=self._payload(); d["quality"]["warnings"]=[{"code":"invalid_type","path":""}]; o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertTrue(any(w["path"]=="quality.warnings[0].path" and w["code"] in {"invalid_type","missing_required"} for w in o["quality"]["warnings"])); self.assertNotIn({"code":"invalid_type","path":""},o["quality"]["warnings"]); self.assertEqual(o["quality"]["status"],"partial")

    # T1.1 (RED): v3 契约允许 evidence 为空，但下游评估 v1 要求 evidence 引用。
    # 当 evidence 全空但有 confirmable direction 时，quality_status 必须降级为
    # manual_required 并加 missing_required:evidence warning，避免下游
    # allowed_candidate_refs=∅ 导致所有评估被 evidence_reference_invalid。
    def test_v3_empty_evidence_with_confirmable_direction_marks_manual_required(self):
        d=self._payload(); d["evidence"]=[]; d["directions"][0]["evidence_refs"]=[]
        o=candidate.normalize_candidate_analysis(d,"经历：Python后端经验")
        self.assertEqual(o["evidence"],[])
        self.assertEqual(o["quality"]["status"],"manual_required")
        self.assertIn({"code":"missing_required","path":"evidence"},o["quality"]["warnings"])

    # T1.2 (防回归): 有 evidence + 有 confirmable direction + 无 warning 时
    # quality_status 仍为 complete，确保 T1.1 修复不会误伤正常场景。
    def test_v3_non_empty_evidence_keeps_complete_status(self):
        o=candidate.normalize_candidate_analysis(self._payload(),"经历：Python后端经验")
        self.assertEqual(len(o["evidence"]),1)
        self.assertEqual(o["quality"]["status"],"complete")
        self.assertEqual(o["quality"]["warnings"],[])


