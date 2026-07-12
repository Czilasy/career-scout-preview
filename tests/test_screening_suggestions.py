"""Cautious, confidence-gated AI screening suggestion tests."""

from __future__ import annotations

import unittest
from unittest import mock

from webui import ai


class CautiousSuggestionValidationTests(unittest.TestCase):
    def _payload(self):
        return {
            field: {"value": "", "confidence": 90}
            for field in ("city", "salary", "experience", "degree", "scale", "stage", "industry")
        } | {
            "city": {"value": "上海", "confidence": 90},
            "salary": {"value": "405", "confidence": 85},
        }

    def test_valid_enumerated_high_confidence_values_are_adopted(self):
        result = ai.validate_cautious_screening_suggestions(self._payload())
        self.assertEqual(result["values"]["city"], "上海")
        self.assertEqual(result["meta"]["city"]["status"], "ai_suggested")
        self.assertEqual(result["meta"]["city"]["confidence"], 90)

    def test_low_confidence_value_stays_blank_and_pending(self):
        payload = self._payload()
        payload["salary"]["confidence"] = 69
        result = ai.validate_cautious_screening_suggestions(payload)
        self.assertEqual(result["values"]["salary"], "")
        self.assertEqual(result["meta"]["salary"]["status"], "pending_confirmation")

    def test_non_enumerated_value_stays_blank(self):
        payload = self._payload()
        payload["salary"]["value"] = "999"
        result = ai.validate_cautious_screening_suggestions(payload)
        self.assertEqual(result["values"]["salary"], "")

    def test_user_confirmed_value_cannot_be_overwritten(self):
        payload = self._payload()
        payload["city"] = {"value": "北京", "confidence": 99}
        result = ai.validate_cautious_screening_suggestions(
            payload, confirmed_fields={"city": "上海"},
        )
        self.assertEqual(result["values"]["city"], "上海")
        self.assertEqual(result["meta"]["city"]["status"], "user_confirmed")

    def test_malformed_field_stays_blank_instead_of_guessing(self):
        payload = self._payload()
        payload["degree"] = "203"
        result = ai.validate_cautious_screening_suggestions(payload)
        self.assertEqual(result["values"]["degree"], "")
        self.assertEqual(result["meta"]["degree"]["status"], "pending_confirmation")

    def test_cautious_call_uses_zero_temperature(self):
        with mock.patch("webui.ai.call_ai", return_value=self._payload()) as call:
            ai.suggest_screening_filters_cautious("resume", "http://ep", "key")
        self.assertEqual(call.call_args.kwargs["temperature"], 0)


if __name__ == "__main__":
    unittest.main()
