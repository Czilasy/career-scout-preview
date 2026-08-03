"""tasks001 T005 — JobSource Protocol / SourceOutcome / FakeJobSource 合同测试。

验证 ``webui/source.py`` 符合 ``contracts/job-source.md`` 合同：
- JobSource Protocol 结构化校验（platform + preflight + fetch_*）；
- SourceOutcome 普通/空/失败三态与 empty_evidence 校验；
- FakeJobSource 携带 platform、显式 cdp_port、preflight 和 batch；
- BossCdpSource 携带 platform 和显式 cdp_port；
- SAFE_FAILURE_CODES 覆盖错误矩阵全部稳定码。
"""
import unittest

from webui.source import (
    BossCdpSource,
    FakeJobSource,
    JobSource,
    SAFE_FAILURE_CODES,
    SourceCircuitBreaker,
    SourceOutcome,
)


# ===========================================================================
# JobSource Protocol 结构化校验
# ===========================================================================
class JobSourceProtocolTests(unittest.TestCase):
    """JobSource Protocol（runtime_checkable）结构化校验。"""

    def test_fake_job_source_is_job_source(self):
        source = FakeJobSource()
        self.assertIsInstance(source, JobSource)

    def test_boss_cdp_source_is_job_source(self):
        source = BossCdpSource.__new__(BossCdpSource)
        # 只校验结构，不触发 __init__ 中的依赖加载。
        source.platform = "boss"
        self.assertIsInstance(source, JobSource)

    def test_protocol_declares_platform_attribute(self):
        """Protocol 必须声明 platform 属性。"""
        self.assertIn("platform", JobSource.__annotations__)

    def test_protocol_declares_four_methods(self):
        """Protocol 必须声明 preflight/fetch_list/fetch_detail/fetch_details_batch。"""
        for method in ("preflight", "fetch_list", "fetch_detail", "fetch_details_batch"):
            self.assertTrue(hasattr(JobSource, method), f"Protocol 缺少方法: {method}")


# ===========================================================================
# SourceOutcome 三态合同
# ===========================================================================
class SourceOutcomeSuccessTests(unittest.TestCase):

    def test_success_with_jobs(self):
        outcome = SourceOutcome.success(jobs=[{"job_id": "a"}], safe_log="ok")
        self.assertTrue(outcome.ok)
        self.assertEqual(len(outcome.jobs), 1)
        self.assertFalse(outcome.empty_result)
        self.assertIsNone(outcome.empty_evidence)
        self.assertIsNone(outcome.failed_code)

    def test_success_empty_jobs_without_empty_flag_is_not_empty_result(self):
        """ok=True + jobs=[] 但 empty_result=False 是合同允许的普通成功（零岗位）。

        合同只要求 ok=True + jobs=[] + empty_result=True 时必须带 evidence；
        普通 success(jobs=[]) 不自动变成 empty_result。
        """
        outcome = SourceOutcome.success(jobs=[])
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.jobs, [])
        self.assertFalse(outcome.empty_result)
        self.assertIsNone(outcome.empty_evidence)


class SourceOutcomeEmptySuccessTests(unittest.TestCase):

    def test_empty_success_with_valid_evidence(self):
        evidence = {
            "kind": "explicit_empty_state",
            "fixture_version": "zhilian-list-v1",
            "marker": "normalized-empty-state",
        }
        outcome = SourceOutcome.empty_success(empty_evidence=evidence, safe_log="empty")
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.jobs, [])
        self.assertTrue(outcome.empty_result)
        self.assertEqual(outcome.empty_evidence, evidence)
        self.assertIsNone(outcome.failed_code)

    def test_empty_success_rejects_none_evidence(self):
        with self.assertRaises(ValueError):
            SourceOutcome.empty_success(empty_evidence=None)

    def test_empty_success_rejects_empty_evidence(self):
        with self.assertRaises(ValueError):
            SourceOutcome.empty_success(empty_evidence={})

    def test_empty_success_rejects_missing_kind(self):
        with self.assertRaises(ValueError):
            SourceOutcome.empty_success(empty_evidence={
                "fixture_version": "v1",
                "marker": "m",
            })

    def test_empty_success_rejects_missing_fixture_version(self):
        with self.assertRaises(ValueError):
            SourceOutcome.empty_success(empty_evidence={
                "kind": "explicit_empty_state",
                "marker": "m",
            })

    def test_empty_success_rejects_missing_marker(self):
        with self.assertRaises(ValueError):
            SourceOutcome.empty_success(empty_evidence={
                "kind": "explicit_empty_state",
                "fixture_version": "v1",
            })

    def test_empty_success_rejects_blank_marker(self):
        with self.assertRaises(ValueError):
            SourceOutcome.empty_success(empty_evidence={
                "kind": "explicit_empty_state",
                "fixture_version": "v1",
                "marker": "",
            })


class SourceOutcomeFailureTests(unittest.TestCase):

    def test_failure_with_safe_code(self):
        outcome = SourceOutcome.failure(
            failed_code="source_login_required",
            safe_log="login_required",
            failed_reason="需要登录",
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.jobs, [])
        self.assertFalse(outcome.empty_result)
        self.assertIsNone(outcome.empty_evidence)
        self.assertEqual(outcome.failed_code, "source_login_required")
        self.assertEqual(outcome.failed_reason, "需要登录")

    def test_failure_to_dict_contains_all_fields(self):
        outcome = SourceOutcome.failure(failed_code="source_blocked", safe_log="blocked")
        data = outcome.to_dict()
        for key in ("ok", "job_count", "has_detail", "empty_result",
                     "empty_evidence", "failed_code", "safe_log", "failed_reason"):
            self.assertIn(key, data)
        self.assertFalse(data["ok"])
        self.assertEqual(data["failed_code"], "source_blocked")


# ===========================================================================
# SAFE_FAILURE_CODES 覆盖错误矩阵
# ===========================================================================
class SafeFailureCodesTests(unittest.TestCase):

    _REQUIRED_CODES = frozenset({
        "source_cdp_unavailable",
        "source_login_required",
        "source_unreachable",
        "source_blocked",
        "source_not_found",
        "source_invalid_output",
        "source_input_drift",
        "source_timeout",
        "source_unknown_error",
        "source_verification_required",
        "source_rate_limited",
    })

    def test_all_required_codes_present(self):
        for code in self._REQUIRED_CODES:
            self.assertIn(code, SAFE_FAILURE_CODES, f"缺少安全失败码: {code}")

    def test_circuit_breaker_signal_codes_subset_of_safe_codes(self):
        for code in SourceCircuitBreaker.SIGNAL_CODES:
            self.assertIn(code, SAFE_FAILURE_CODES)


# ===========================================================================
# FakeJobSource 平台、端口与 preflight
# ===========================================================================
class FakeJobSourcePlatformTests(unittest.TestCase):

    def test_default_platform_is_boss(self):
        source = FakeJobSource()
        self.assertEqual(source.platform, "boss")

    def test_custom_platform_zhilian(self):
        source = FakeJobSource(platform="zhilian")
        self.assertEqual(source.platform, "zhilian")

    def test_default_cdp_port_is_9222(self):
        source = FakeJobSource()
        self.assertEqual(source.cdp_port, 9222)

    def test_custom_cdp_port(self):
        source = FakeJobSource(cdp_port=9223)
        self.assertEqual(source.cdp_port, 9223)

    def test_cdp_port_rejects_non_positive(self):
        with self.assertRaises((ValueError, TypeError)):
            FakeJobSource(cdp_port=0)

    def test_platform_is_string(self):
        source = FakeJobSource(platform="zhilian")
        self.assertIsInstance(source.platform, str)


class FakeJobSourcePreflightTests(unittest.TestCase):

    def test_preflight_success_by_default(self):
        source = FakeJobSource()
        outcome = source.preflight()
        self.assertTrue(outcome.ok)
        self.assertIsNone(outcome.failed_code)
        self.assertEqual(source.preflight_calls, 1)

    def test_preflight_failure_when_configured(self):
        source = FakeJobSource(preflight_failure="source_login_required")
        outcome = source.preflight()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_login_required")

    def test_preflight_safe_log_contains_platform_and_port(self):
        source = FakeJobSource(platform="zhilian", cdp_port=9223)
        outcome = source.preflight()
        self.assertIn("zhilian", outcome.safe_log)
        self.assertIn("9223", outcome.safe_log)

    def test_preflight_failure_log_contains_platform_and_port(self):
        source = FakeJobSource(
            platform="zhilian", cdp_port=9223,
            preflight_failure="source_login_required",
        )
        outcome = source.preflight()
        self.assertIn("zhilian", outcome.safe_log)
        self.assertIn("9223", outcome.safe_log)


# ===========================================================================
# FakeJobSource fetch_details_batch
# ===========================================================================
class FakeJobSourceBatchTests(unittest.TestCase):

    def test_batch_returns_outcome_per_job(self):
        detail_jobs = {
            "j1": {"jd": "detail-1"},
            "j2": {"jd": "detail-2"},
        }
        source = FakeJobSource(detail_jobs=detail_jobs)
        jobs = [
            {"job_id": "j1", "source_url": "https://example.com/1"},
            {"job_id": "j2", "source_url": "https://example.com/2"},
        ]
        results = source.fetch_details_batch(jobs)
        self.assertEqual(set(results.keys()), {"j1", "j2"})
        self.assertTrue(results["j1"].ok)
        self.assertTrue(results["j2"].ok)

    def test_batch_single_failure_does_not_block_others(self):
        detail_jobs = {"j1": {"jd": "detail-1"}}
        source = FakeJobSource(
            detail_jobs=detail_jobs,
            detail_failures={"j2"},
        )
        jobs = [
            {"job_id": "j1", "source_url": "https://example.com/1"},
            {"job_id": "j2", "source_url": "https://example.com/2"},
        ]
        results = source.fetch_details_batch(jobs)
        self.assertTrue(results["j1"].ok)
        self.assertFalse(results["j2"].ok)
        self.assertEqual(results["j2"].failed_code, "source_blocked")

    def test_batch_invalid_job_gets_failure(self):
        source = FakeJobSource()
        results = source.fetch_details_batch(["not-a-dict"])
        self.assertIn("idx0", results)
        self.assertFalse(results["idx0"].ok)


# ===========================================================================
# BossCdpSource 平台与端口
# ===========================================================================
class BossCdpSourcePlatformTests(unittest.TestCase):

    def test_class_attribute_platform_is_boss(self):
        self.assertEqual(BossCdpSource.platform, "boss")

    def test_instance_inherits_platform(self):
        """BossCdpSource 实例的 platform 属性继承类属性 'boss'。"""
        # 不调用 __init__ 以避免加载 scraper 依赖。
        source = BossCdpSource.__new__(BossCdpSource)
        self.assertEqual(source.platform, "boss")

    def test_constructor_accepts_cdp_port(self):
        """__init__ 签名包含 cdp_port 参数。"""
        import inspect
        sig = inspect.signature(BossCdpSource.__init__)
        self.assertIn("cdp_port", sig.parameters)


if __name__ == "__main__":
    unittest.main()
