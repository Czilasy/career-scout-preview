"""Unit tests for webui.candidate (feature 004)."""

from __future__ import annotations

import unittest

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
