"""033 V2 白箱结论规则的红绿测试。"""

from __future__ import annotations

import copy
import unittest

from webui.whitebox_rules import reduce_conclusion


def _plan(*keys: str) -> dict:
    return {"stages": ["scrape_list"], "units": [{"unit_key": key, "required": True} for key in keys]}


def _unit(key: str, status: str = "succeeded", *, evidence: bool = True,
         output: int = 1, explicit_empty: bool = False, degraded: bool = False,
         error_code: str | None = None) -> dict:
    return {
        "unit_key": key,
        "status": status,
        "evidence_complete": evidence,
        "unit_unique_count": output,
        "returned_total_count": output,
        "explicit_empty": explicit_empty,
        "degraded": degraded,
        "error_code": error_code,
        "error_reason": "测试原因" if error_code else None,
    }


class WhiteboxRuleTests(unittest.TestCase):
    def test_all_required_units_with_results_are_succeeded(self):
        result = reduce_conclusion(_plan("a", "b"), [_unit("a"), _unit("b")])
        self.assertEqual(result["conclusion"], "succeeded")
        self.assertTrue(result["evidence_complete"])

    def test_all_units_explicitly_empty_are_empty(self):
        result = reduce_conclusion(
            _plan("a"), [_unit("a", output=0, explicit_empty=True)]
        )
        self.assertEqual(result["conclusion"], "empty")

    def test_zero_output_without_empty_evidence_is_unverifiable(self):
        result = reduce_conclusion(_plan("a"), [_unit("a", output=0)])
        self.assertEqual(result["conclusion"], "unverifiable")
        self.assertEqual(result["primary_code"], "empty_evidence_missing")

    def test_missing_required_evidence_cannot_be_success(self):
        result = reduce_conclusion(
            _plan("a", "b"), [_unit("a"), _unit("b", evidence=False)]
        )
        self.assertEqual(result["conclusion"], "unverifiable")

    def test_existing_whitebox_incomplete_event_cannot_be_success(self):
        result = reduce_conclusion(
            _plan("a"),
            [_unit("a")],
            events=[{
                "event_type": "whitebox_incomplete",
                "payload": {"reason": "主库写入失败"},
            }],
        )
        self.assertEqual(result["conclusion"], "unverifiable")
        self.assertEqual(result["primary_code"], "whitebox_incomplete")

    def test_explicit_failed_lifecycle_wins_over_missing_other_units(self):
        result = reduce_conclusion(
            _plan("a", "b"),
            [_unit("a", "failed", output=1, error_code="ai_persist_failed")],
            lifecycle_end="failed",
        )
        self.assertEqual(result["conclusion"], "failed")
        self.assertEqual(result["primary_code"], "ai_persist_failed")

    def test_failed_lifecycle_without_failed_units_is_not_failed(self):
        """B098：单元全成功时，失败的收尾标记不得产出「执行失败」的矛盾结论。

        回归：重抓曾有 11 个单元全部成功、0 失败、0 未知，结论却是
        「执行失败 · 任务完成证据不足」。
        """
        result = reduce_conclusion(
            _plan("a", "b"), [_unit("a"), _unit("b")], lifecycle_end="failed"
        )
        self.assertEqual(result["conclusion"], "succeeded")
        self.assertEqual(result["summary"]["failed_units"], 0)
        self.assertEqual(result["summary"]["unknown_units"], 0)

    def test_failed_unit_with_results_is_partial(self):
        result = reduce_conclusion(
            _plan("a", "b"), [_unit("a"), _unit("b", "failed", output=0, error_code="combo_failed")]
        )
        self.assertEqual(result["conclusion"], "partial")
        self.assertEqual(result["primary_code"], "combo_failed")

    def test_all_failed_without_results_is_failed(self):
        result = reduce_conclusion(
            _plan("a"), [_unit("a", "failed", output=0, error_code="source_blocked")]
        )
        self.assertEqual(result["conclusion"], "failed")

    def test_cancelled_is_interrupted(self):
        result = reduce_conclusion(
            _plan("a"), [_unit("a", "interrupted", output=1)], lifecycle_end="cancelled"
        )
        self.assertEqual(result["conclusion"], "interrupted")

    def test_degraded_is_independent_from_complete_conclusion(self):
        result = reduce_conclusion(
            _plan("a"), [_unit("a", degraded=True)]
        )
        self.assertEqual(result["conclusion"], "succeeded")
        self.assertTrue(result["degraded"])

    def test_low_result_count_does_not_change_conclusion(self):
        result = reduce_conclusion(_plan("a"), [_unit("a", output=1)])
        self.assertEqual(result["conclusion"], "succeeded")
        self.assertEqual(result["summary"]["run_unique_count"], 1)

    def test_reduction_is_idempotent(self):
        plan = _plan("a")
        units = [_unit("a")]
        snapshot = copy.deepcopy(units)
        first = reduce_conclusion(plan, units)
        second = reduce_conclusion(plan, units)
        self.assertEqual(second, first, "重复归约结果必须一致")
        self.assertEqual(units, snapshot, "归约不得就地修改输入单元")


if __name__ == "__main__":
    unittest.main()
