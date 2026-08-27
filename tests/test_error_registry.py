"""B043: unified error code registry invariants."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from webui.error_registry import (
    ALIAS_TO_CODE,
    ERROR_CODES,
    ERROR_TAXONOMY,
    INDEPENDENT_FAILURE_CODES,
    SAFE_FAILURE_CODES,
    SYSTEMIC_BLOCK_CODES,
    SYSTEMIC_AI_ERROR_CODES,
    UnknownErrorCode,
    validate_code,
)
from webui.error_registry import resolve_code


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_MIRROR = ROOT / "webui" / "src" / "errorCodes.ts"


class ErrorRegistryTests(unittest.TestCase):
    def test_codes_are_unique_and_fully_covered_by_sets(self):
        self.assertEqual(len(ERROR_CODES), len(set(ERROR_CODES)))
        self.assertTrue(SAFE_FAILURE_CODES <= ERROR_CODES)
        self.assertTrue(SYSTEMIC_BLOCK_CODES <= ERROR_CODES | set(ALIAS_TO_CODE))
        self.assertTrue(INDEPENDENT_FAILURE_CODES <= ERROR_CODES)
        self.assertTrue(SYSTEMIC_AI_ERROR_CODES <= ERROR_CODES)

    def test_block_set_is_derived_from_registry_marks(self):
        # 016-error-module-rework：阻断集合由 blocking+systemic 推导 + 历史别名闭包
        from webui.error_registry import REGISTRY, _derived_systemic_block_codes
        self.assertEqual(SYSTEMIC_BLOCK_CODES, _derived_systemic_block_codes())
        canonical = frozenset(
            code for code, entry in REGISTRY.items()
            if entry["blocking"] and entry["impact"] == "systemic"
        )
        self.assertTrue(canonical <= SYSTEMIC_BLOCK_CODES)
        # 双套码收敛：四个旧 taxonomy 码不再是正名，且新码就位
        for legacy in ("captcha_required", "login_expired", "ip_risk_control",
                       "cdp_unavailable"):
            self.assertNotIn(legacy, ERROR_CODES)
        self.assertIn("source_account_restricted", SYSTEMIC_BLOCK_CODES)
        self.assertNotIn("source_status_unclear", SYSTEMIC_BLOCK_CODES)
        self.assertIn("source_status_unclear", INDEPENDENT_FAILURE_CODES)
        self.assertEqual(
            INDEPENDENT_FAILURE_CODES,
            frozenset({"job_offline", "detail_timeout", "detail_invalid",
                       "ai_missing_job", "source_status_unclear"}),
        )
        self.assertEqual(len(SAFE_FAILURE_CODES), 15)
        self.assertTrue(set(ERROR_TAXONOMY) <= ERROR_CODES)

    def test_source_result_write_failed_is_registered(self):
        """T013：026 B079 结果文件写失败码已注册、文案与语义正确（FR-007）。"""
        from webui.error_registry import (
            FAILED_CODE_LABELS,
            INDEPENDENT_FAILURE_CODES,
            REGISTRY,
            resolve_code,
        )
        self.assertIn("source_result_write_failed", ERROR_CODES)
        entry = REGISTRY["source_result_write_failed"]
        self.assertEqual(entry["category"], "source")
        self.assertEqual(entry["user_message"], "结果文件写入失败")
        self.assertTrue(entry["retryable"])
        self.assertFalse(entry["blocking"])
        self.assertEqual(FAILED_CODE_LABELS["source_result_write_failed"],
                         "结果文件写入失败")
        self.assertEqual(resolve_code("source_result_write_failed"),
                         "source_result_write_failed")

    def test_legacy_taxonomy_codes_resolve_to_canonical(self):
        from webui.error_registry import ALIAS_TO_CODE
        self.assertEqual(resolve_code("captcha_required"),
                         "source_verification_required")
        self.assertEqual(resolve_code("login_expired"), "source_login_required")
        self.assertEqual(resolve_code("ip_risk_control"), "source_blocked")
        self.assertEqual(resolve_code("cdp_unavailable"), "source_cdp_unavailable")
        expected_legacy_aliases = {
            "captcha_required": "source_verification_required",
            "login_expired": "source_login_required",
            "ip_risk_control": "source_blocked",
            "cdp_unavailable": "source_cdp_unavailable",
        }
        for alias, target in expected_legacy_aliases.items():
            self.assertEqual(ALIAS_TO_CODE.get(alias), target)
        # AI 内部码保留自身大写名作为别名（既有行为）
        for alias, target in ALIAS_TO_CODE.items():
            if alias in expected_legacy_aliases:
                continue
            self.assertEqual(resolve_code(alias), target)

    def test_database_historical_codes_are_known(self):
        historical = {
            "ai_missing_job", "cdp_unavailable", "detail_invalid",
            "source_blocked", "source_invalid_output", "source_login_required",
            "source_rate_limited", "source_unknown_error", "source_unreachable",
            "source_verification_required", "internal_error", "resumed",
            "user_finished",
        }
        self.assertTrue(historical <= ERROR_CODES | set(ALIAS_TO_CODE))

    def test_unknown_code_fails_validation(self):
        with self.assertRaises(UnknownErrorCode):
            validate_code("not_a_real_code")
        with self.assertRaises(UnknownErrorCode):
            validate_code("")

    def test_runtime_unknown_code_warns_and_falls_back_to_internal(self):
        with self.assertLogs("career_scout.error_registry", level="WARNING") as logs:
            resolved = resolve_code("not_a_real_code")
        self.assertEqual(resolved, "internal_error")
        self.assertTrue(any("not in registry" in line for line in logs.output))
        self.assertEqual(resolve_code("source_timeout"), "source_timeout")

    def test_runtime_pipeline_helpers_normalize_unknown_code(self):
        from webui.pipeline_exec import failed_code_label, taxonomy_reason
        with self.assertLogs("career_scout.error_registry", level="WARNING"):
            self.assertEqual(
                failed_code_label("not_a_real_code"), "内部状态或持久化错误")
        with self.assertLogs("career_scout.error_registry", level="WARNING"):
            self.assertEqual(
                taxonomy_reason("not_a_real_code"), "内部状态或持久化错误")

    def test_frontend_mirror_matches_registry(self):
        text = FRONTEND_MIRROR.read_text(encoding="utf-8")
        array_match = re.search(r"ERROR_CODES\s*=\s*\[(.*?)\]\s*as const", text, re.S)
        self.assertIsNotNone(array_match)
        codes = {
            match.strip().strip('"').strip("'")
            for match in re.findall(r'"([^"]+)"', array_match.group(1))
        }
        self.assertEqual(codes, set(ERROR_CODES))


if __name__ == "__main__":
    unittest.main()
