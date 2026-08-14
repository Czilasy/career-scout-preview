"""B043: unified error code registry invariants."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from webui.error_registry import (
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
        self.assertTrue(SYSTEMIC_BLOCK_CODES <= ERROR_CODES)
        self.assertTrue(INDEPENDENT_FAILURE_CODES <= ERROR_CODES)
        self.assertTrue(SYSTEMIC_AI_ERROR_CODES <= ERROR_CODES)

    def test_existing_sets_are_preserved(self):
        self.assertEqual(
            SYSTEMIC_BLOCK_CODES,
            frozenset({
                "captcha_required", "login_expired", "ai_rate_limited",
                "ai_quota_exhausted", "ai_key_invalid", "ai_network_error",
                "ip_risk_control", "cdp_unavailable", "internal_error",
                "source_verification_required", "source_login_required",
                "source_rate_limited", "source_blocked", "source_cdp_unavailable",
            }),
        )
        self.assertEqual(
            INDEPENDENT_FAILURE_CODES,
            frozenset({"job_offline", "detail_timeout", "detail_invalid", "ai_missing_job"}),
        )
        self.assertEqual(len(SAFE_FAILURE_CODES), 11)
        self.assertTrue(set(ERROR_TAXONOMY) <= ERROR_CODES)

    def test_database_historical_codes_are_known(self):
        historical = {
            "ai_missing_job", "cdp_unavailable", "detail_invalid",
            "source_blocked", "source_invalid_output", "source_login_required",
            "source_rate_limited", "source_unknown_error", "source_unreachable",
            "source_verification_required", "internal_error", "resumed",
            "user_finished",
        }
        self.assertTrue(historical <= ERROR_CODES)

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
