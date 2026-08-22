"""tasks001 T005 — JobSource Protocol / SourceOutcome / FakeJobSource 合同测试。

验证 ``webui/source.py`` 符合 ``contracts/job-source.md`` 合同：
- JobSource Protocol 结构化校验（platform + preflight + fetch_*）；
- SourceOutcome 普通/空/失败三态与 empty_evidence 校验；
- FakeJobSource 携带 platform、显式 cdp_port、preflight 和 batch；
- BossCdpSource 携带 platform 和显式 cdp_port；
- SAFE_FAILURE_CODES 覆盖错误矩阵全部稳定码。
"""
import pathlib
import tempfile
import unittest
from unittest import mock

from webui.source import (
    BossCdpSource,
    FakeJobSource,
    JobSource,
    PREFLIGHT_RETRY_DELAY_SECONDS,
    SAFE_FAILURE_CODES,
    SourceCircuitBreaker,
    SourceOutcome,
    _normalize_job_fields,
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


# ===========================================================================
# tasks004 T301 — ZhilianCdpSource 构造与安全运行配置校验
#
# 实施门禁说明（fixture_manifest.json blocked_facts）：
#   - list/detail/empty/login wall/edgeone/rate limit/block marker 全部未核验；
#   - company_nature options、非全国城市码未核验；
#   - can_run_real_tasks=false。
#
# 因此本任务只实现有证据覆盖的 adapter 骨架：构造校验、preflight 分类骨架、
# fetch_list 输入校验骨架、fetch_detail URL/平台校验骨架、熔断器复用、
# outcome 合同表达。真实页面 marker 检测、字段归一化、空结果判定保持占位，
# 在 marker 核验后才解锁（T305/T307/T308/T310/T311 真实分支）。
# ===========================================================================
from webui.source import ZhilianCdpSource


class ZhilianCdpSourceConstructionTests(unittest.TestCase):
    """T301/T302：智联 adapter 构造参数与安全运行配置校验。"""

    def test_class_attribute_platform_is_zhilian(self):
        self.assertEqual(ZhilianCdpSource.platform, "zhilian")

    def test_construction_requires_browser_account(self):
        """缺少 browser_account 时返回稳定错误，不回退 BOSS factory/活动账号/默认端口。"""
        with self.assertRaises((ValueError, TypeError)):
            ZhilianCdpSource(browser_account="", cdp_port=9223)

    def test_construction_requires_explicit_cdp_port(self):
        """智联必须显式接收冻结 CDP 端口（9223），不得隐式默认。"""
        # cdp_port 必须为正整数；0/负数拒绝。
        with self.assertRaises((ValueError, TypeError)):
            ZhilianCdpSource(browser_account="a", cdp_port=0)

    def test_construction_rejects_profile_key_mismatch(self):
        """profile_key 必须等于 'zhilian:<browser_account>'，不得使用 BOSS profile_key。"""
        with self.assertRaises(ValueError):
            ZhilianCdpSource(
                browser_account="a",
                cdp_port=9223,
                profile_key="boss:a",  # 错配
            )

    def test_construction_rejects_boss_default_port(self):
        """智联不得使用 BOSS 默认端口 9222（profile/platform 边界隔离）。"""
        # 构造允许传入任意正整数端口（受控切换场景），但 platform=boss 默认端口
        # 必须被显式拒绝，避免隐式回退 BOSS 登录空间。
        with self.assertRaises(ValueError):
            ZhilianCdpSource(
                browser_account="a",
                cdp_port=9222,  # BOSS 默认端口
            )

    def test_construction_rejects_platform_disabled(self):
        """智联 enabled_for_new_tasks=False 时新任务创建前阻断。

        返回 ``platform_disabled`` 稳定错误码，不静默切换 BOSS。
        """
        outcome = ZhilianCdpSource.preflight_disabled_platform()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "platform_disabled")

    def test_construction_does_not_fallback_to_boss_factory(self):
        """智联 adapter 不得回退 BOSS factory 或活动账号。"""
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        self.assertEqual(source.platform, "zhilian")
        self.assertEqual(source.cdp_port, 9223)
        self.assertNotEqual(source.platform, "boss")

    def test_construction_freezes_runtime_config(self):
        """构造冻结 platform/browser_account/cdp_port/profile_key，不读全局活动账号。"""
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            profile_key="zhilian:a",
        )
        self.assertEqual(source.browser_account, "a")
        self.assertEqual(source.cdp_port, 9223)
        self.assertEqual(source.profile_key, "zhilian:a")


class _LoginCacheIsolated(unittest.TestCase):
    """D3：preflight 缓存优先会读 login-state.json，使用 browser_account 的测试
    类必须指向临时文件，避免命中 ~/.career-scout/login-state.json 的真实残留。"""

    def setUp(self):
        self._state_tmp = tempfile.TemporaryDirectory()
        from scripts import login_state_cache as _cache
        _cache.set_login_state_path(
            pathlib.Path(self._state_tmp.name) / "login-state.json")

    def tearDown(self):
        from scripts import login_state_cache as _cache
        _cache.reset_login_state_path()
        self._state_tmp.cleanup()


class ZhilianCdpSourcePreflightTests(_LoginCacheIsolated):
    """T303：preflight 分类骨架（CDP 不可用、登录墙、EdgeOne/验证码、限流、封禁、连接失败、超时）。

    marker 检测需要真实页面 fixture（blocked_facts），本任务只实现分类逻辑骨架：
    adapter 调用 scripts/zhilian_cdp_raw.py 的 preflight 函数，按返回的稳定
    signal 映射到错误矩阵。真实 marker 检测函数保持占位（返回 None 表示未核验）。
    """

    def test_preflight_cdp_unavailable_returns_source_cdp_unavailable(self):
        """CDP 9223 不可用 → source_cdp_unavailable（平台级 paused）。"""
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            preflight_runner=lambda port: _fake_preflight(signal="cdp_unavailable"),
        )
        outcome = source.preflight()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_cdp_unavailable")

    def test_preflight_login_required_returns_source_login_required(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            preflight_runner=lambda port: _fake_preflight(signal="login_required"),
        )
        outcome = source.preflight()
        self.assertEqual(outcome.failed_code, "source_login_required")

    def test_preflight_edgeone_verification_returns_source_verification_required(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            preflight_runner=lambda port: _fake_preflight(signal="verification"),
        )
        outcome = source.preflight()
        self.assertEqual(outcome.failed_code, "source_verification_required")

    def test_preflight_rate_limited_returns_source_rate_limited(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            preflight_runner=lambda port: _fake_preflight(signal="rate_limited"),
        )
        outcome = source.preflight()
        self.assertEqual(outcome.failed_code, "source_rate_limited")

    def test_preflight_rate_limited_records_run_id(self):
        with mock.patch("webui.source._record_risk_signals") as rec:
            source = ZhilianCdpSource(
                browser_account="a", cdp_port=9223, run_id="run-z",
                preflight_runner=lambda port: _fake_preflight(signal="rate_limited"),
            )
            outcome = source.preflight()
        self.assertEqual(outcome.failed_code, "source_rate_limited")
        rec.assert_called_once()
        self.assertEqual(rec.call_args.args[:3], ("a", "zhilian", "source_rate_limited"))
        self.assertEqual(rec.call_args.kwargs["run_id"], "run-z")

    def test_preflight_blocked_returns_source_blocked(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            preflight_runner=lambda port: _fake_preflight(signal="blocked"),
        )
        outcome = source.preflight()
        self.assertEqual(outcome.failed_code, "source_blocked")

    def test_preflight_unreachable_returns_source_unreachable(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            preflight_runner=lambda port: _fake_preflight(signal="unreachable"),
        )
        outcome = source.preflight()
        self.assertEqual(outcome.failed_code, "source_unreachable")

    def test_preflight_timeout_returns_source_timeout(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            preflight_runner=lambda port: _fake_preflight(signal="timeout"),
        )
        outcome = source.preflight()
        self.assertEqual(outcome.failed_code, "source_timeout")

    def test_preflight_success_returns_ok(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            preflight_runner=lambda port: _fake_preflight(signal="ok"),
        )
        outcome = source.preflight()
        self.assertTrue(outcome.ok)
        self.assertIsNone(outcome.failed_code)

    def test_preflight_does_not_fallback_to_boss_port(self):
        """preflight 只检查智联冻结端口 9223，不触碰 BOSS 9222。"""
        captured_ports = []

        def runner(port):
            captured_ports.append(port)
            return _fake_preflight(signal="ok")

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            preflight_runner=runner,
        )
        source.preflight()
        self.assertEqual(captured_ports, [9223])
        self.assertNotIn(9222, captured_ports)


class ZhilianCdpSourcePreflightLogSafetyTests(_LoginCacheIsolated):
    """T304：日志只含平台/阶段/计数/ID 是否存在/URL host，不含 Cookie/JD/页面正文/profile 路径。"""

    _FORBIDDEN_LOG_TOKENS = (
        "cookie", "jd", "description", "profile_dir", "profile_path",
        ".zhilian", "C:\\", "/home/", "/Users/", "password", "token",
        "secret", "api_key", "resume",
    )

    def test_preflight_success_log_is_safe(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            preflight_runner=lambda port: _fake_preflight(signal="ok"),
        )
        outcome = source.preflight()
        self._assert_safe_log(outcome.safe_log)

    def test_preflight_failure_log_is_safe(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            preflight_runner=lambda port: _fake_preflight(signal="login_required"),
        )
        outcome = source.preflight()
        self._assert_safe_log(outcome.safe_log)
        # 平台和阶段必须出现，便于审计。
        self.assertIn("zhilian", outcome.safe_log)
        self.assertIn("preflight", outcome.safe_log)

    def _assert_safe_log(self, log_text: str) -> None:
        low = (log_text or "").lower()
        for token in self._FORBIDDEN_LOG_TOKENS:
            self.assertNotIn(
                token, low,
                f"safe_log 含禁止 token: {token!r} (full: {log_text!r})",
            )


class ZhilianCdpSourceFetchListInputTests(unittest.TestCase):
    """T306：fetch_list 输入校验骨架。

    真实列表抓取与字段归一化需要 list_page_markers fixture（blocked_facts），
    本任务只实现输入校验：拒绝 AI filters、要求规范城市解析快照、页数。
    """

    def _valid_plan_item(self, **overrides) -> dict:
        item = {
            "platform": "zhilian",
            "keyword": "Python 后端",
            "city": {
                "name": "全国",
                "platform_code": "jl0",
                "mapping_version": 1,
                "mapping_label": "全国",
            },
            "target_pages": 1,
            "input_hash": _zhilian_input_hash({
                "platform": "zhilian",
                "keyword": "Python 后端",
                "city": {
                    "name": "全国",
                    "platform_code": "jl0",
                    "mapping_version": 1,
                },
                "target_pages": 1,
            }),
            "list_output_path": "",  # 由 _bind_temp_output 注入
        }
        item.update(overrides)
        return item

    def test_fetch_list_rejects_non_dict_plan_item(self):
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        outcome = source.fetch_list("not-a-dict")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_fetch_list_rejects_platform_mismatch(self):
        """plan_item.platform 必须与 adapter platform 一致，不回退 BOSS。"""
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        item = self._valid_plan_item(platform="boss")
        outcome = source.fetch_list(item)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_fetch_list_rejects_ai_filters_in_plan_item(self):
        """AI 筛选字段（salary/experience/degree/industry/scale/company_nature/stage）
        不得进入 adapter 列表参数。"""
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        for forbidden_key in ("source_filters", "filters", "screening_fields",
                              "company_nature", "stage", "salary"):
            item = self._valid_plan_item(**{forbidden_key: ["x"]})
            outcome = source.fetch_list(item)
            self.assertFalse(outcome.ok, f"应拒绝 {forbidden_key}")
            self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_fetch_list_requires_city_snapshot_with_platform_code(self):
        """city 必须是带 platform_code/mapping_version 的解析快照，不接受裸字符串。"""
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        item = self._valid_plan_item(city="上海")  # 裸字符串，缺平台码
        outcome = source.fetch_list(item)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_fetch_list_rejects_missing_city_mapping(self):
        """缺城映射（city.platform_code 为空或缺失）→ city_mapping_missing。"""
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        item = self._valid_plan_item(city={"name": "未知城市"})
        outcome = source.fetch_list(item)
        self.assertFalse(outcome.ok)
        # 缺城映射属于输入校验失败，归入 source_invalid_output
        # （编排层在任务创建前应已用 city_mapping_missing 阻断）。
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_fetch_list_rejects_zero_pages(self):
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        item = self._valid_plan_item(target_pages=0)
        outcome = source.fetch_list(item)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_fetch_list_rejects_missing_keyword(self):
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        item = self._valid_plan_item(keyword="")
        outcome = source.fetch_list(item)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_fetch_list_rejects_missing_input_hash(self):
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        item = self._valid_plan_item(input_hash="")
        outcome = source.fetch_list(item)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")


class ZhilianCdpSourceInputHashTests(unittest.TestCase):
    """T309：input_hash 覆盖 platform/关键词/完整城市解析快照/页数。"""

    def test_input_hash_includes_platform(self):
        from webui.source import _zhilian_input_hash
        base = {
            "keyword": "Python", "city": {"name": "全国", "platform_code": "jl0",
                                           "mapping_version": 1},
            "target_pages": 1,
        }
        h1 = _zhilian_input_hash({**base, "platform": "zhilian"})
        h2 = _zhilian_input_hash({**base, "platform": "boss"})
        self.assertNotEqual(h1, h2, "platform 必须影响 input_hash")

    def test_input_hash_includes_keyword(self):
        from webui.source import _zhilian_input_hash
        base = {
            "platform": "zhilian",
            "city": {"name": "全国", "platform_code": "jl0", "mapping_version": 1},
            "target_pages": 1,
        }
        h1 = _zhilian_input_hash({**base, "keyword": "Python"})
        h2 = _zhilian_input_hash({**base, "keyword": "Java"})
        self.assertNotEqual(h1, h2)

    def test_input_hash_includes_full_city_snapshot(self):
        from webui.source import _zhilian_input_hash
        base = {
            "platform": "zhilian", "keyword": "Python", "target_pages": 1,
        }
        h1 = _zhilian_input_hash({**base, "city": {
            "name": "全国", "platform_code": "jl0", "mapping_version": 1,
        }})
        # 缺 mapping_version 的不完整快照必须产生不同 hash。
        h2 = _zhilian_input_hash({**base, "city": {
            "name": "全国", "platform_code": "jl0",
        }})
        self.assertNotEqual(h1, h2)
        # 不同 platform_code 产生不同 hash。
        h3 = _zhilian_input_hash({**base, "city": {
            "name": "全国", "platform_code": "jl999", "mapping_version": 1,
        }})
        self.assertNotEqual(h1, h3)

    def test_input_hash_includes_target_pages(self):
        from webui.source import _zhilian_input_hash
        base = {
            "platform": "zhilian", "keyword": "Python",
            "city": {"name": "全国", "platform_code": "jl0", "mapping_version": 1},
        }
        h1 = _zhilian_input_hash({**base, "target_pages": 1})
        h2 = _zhilian_input_hash({**base, "target_pages": 2})
        self.assertNotEqual(h1, h2)

    def test_input_hash_is_deterministic(self):
        from webui.source import _zhilian_input_hash
        payload = {
            "platform": "zhilian", "keyword": "Python",
            "city": {"name": "全国", "platform_code": "jl0", "mapping_version": 1},
            "target_pages": 1,
        }
        self.assertEqual(
            _zhilian_input_hash(payload),
            _zhilian_input_hash(dict(payload)),
        )


class ZhilianCdpSourceFetchDetailTests(unittest.TestCase):
    """T310/T311：fetch_detail URL/平台校验骨架。

    真实 JD 取得需要 detail_page_markers fixture（blocked_facts），本任务只实现
    URL/平台/平台岗位身份校验骨架。无法取得 JD 时返回明确单项失败，不伪造正文。
    """

    def test_fetch_detail_rejects_non_dict_job(self):
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        outcome = source.fetch_detail("not-a-dict")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_fetch_detail_requires_platform_job_id(self):
        """岗位必须有可归属的 platform_job_id，不能只凭客户端任意 URL 抓取。"""
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        outcome = source.fetch_detail({
            "platform": "zhilian",
            "canonical_url": "https://www.zhaopin.com/jobdetail/abc.htm",
            # 缺 platform_job_id
        })
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_fetch_detail_requires_canonical_url(self):
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        outcome = source.fetch_detail({
            "platform": "zhilian",
            "platform_job_id": "abc",
            # 缺 canonical_url
        })
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_fetch_detail_rejects_non_zhilian_url(self):
        """非智联域名 URL 不得作为智联原链接打开（platform_url_mismatch）。"""
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        outcome = source.fetch_detail({
            "platform": "zhilian",
            "platform_job_id": "abc",
            "canonical_url": "https://www.zhipin.com/job/abc",  # BOSS 域名
        })
        self.assertFalse(outcome.ok)
        # URL host 不匹配智联 allowlist → platform_url_mismatch 映射到 invalid_output
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_fetch_detail_rejects_platform_mismatch(self):
        """plan_item.platform != zhilian 时拒绝，不串平台。"""
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        outcome = source.fetch_detail({
            "platform": "boss",  # 错配
            "platform_job_id": "abc",
            "canonical_url": "https://www.zhaopin.com/jobdetail/abc.htm",
        })
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_fetch_detail_returns_not_found_without_faking_jd(self):
        """无法取得真实 JD 时返回 source_not_found，不伪造正文（T311）。"""
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            detail_runner=lambda job, **kw: _fake_detail(signal="not_found"),
        )
        outcome = source.fetch_detail({
            "platform": "zhilian",
            "platform_job_id": "abc",
            "canonical_url": "https://www.zhaopin.com/jobdetail/abc.htm",
        })
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_not_found")
        # 不得伪造 JD 正文。
        self.assertFalse(outcome.detail)

    def test_fetch_detail_returns_invalid_output_on_parse_failure(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            detail_runner=lambda job, **kw: _fake_detail(signal="invalid_output"),
        )
        outcome = source.fetch_detail({
            "platform": "zhilian",
            "platform_job_id": "abc",
            "canonical_url": "https://www.zhaopin.com/jobdetail/abc.htm",
        })
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_fetch_detail_returns_timeout(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            detail_runner=lambda job, **kw: _fake_detail(signal="timeout"),
        )
        outcome = source.fetch_detail({
            "platform": "zhilian",
            "platform_job_id": "abc",
            "canonical_url": "https://www.zhaopin.com/jobdetail/abc.htm",
        })
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_timeout")

    def test_fetch_detail_returns_login_required_on_platform_block(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            detail_runner=lambda job, **kw: _fake_detail(signal="login_required"),
        )
        outcome = source.fetch_detail({
            "platform": "zhilian",
            "platform_job_id": "abc",
            "canonical_url": "https://www.zhaopin.com/jobdetail/abc.htm",
        })
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_login_required")


class ZhilianCdpSourceBatchTests(unittest.TestCase):
    """T312：fetch_details_batch 单项异常继续，连续平台级 signal 触发熔断。"""

    def test_batch_returns_outcome_per_job(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            detail_runner=lambda job, **kw: _fake_detail(signal="ok"),
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": "j1",
             "canonical_url": "https://www.zhaopin.com/jobdetail/j1.htm"},
            {"platform": "zhilian", "platform_job_id": "j2",
             "canonical_url": "https://www.zhaopin.com/jobdetail/j2.htm"},
        ]
        results = source.fetch_details_batch(jobs)
        self.assertEqual(set(results.keys()), {"j1", "j2"})
        self.assertTrue(results["j1"].ok)
        self.assertTrue(results["j2"].ok)

    def test_batch_single_failure_does_not_block_others(self):
        """单岗位失败不抛出到批次外（T312）。"""
        def runner(job, **kw):
            if job["platform_job_id"] == "j2":
                return _fake_detail(signal="not_found")
            return _fake_detail(signal="ok")

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223, detail_runner=runner,
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": "j1",
             "canonical_url": "https://www.zhaopin.com/jobdetail/j1.htm"},
            {"platform": "zhilian", "platform_job_id": "j2",
             "canonical_url": "https://www.zhaopin.com/jobdetail/j2.htm"},
            {"platform": "zhilian", "platform_job_id": "j3",
             "canonical_url": "https://www.zhaopin.com/jobdetail/j3.htm"},
        ]
        results = source.fetch_details_batch(jobs)
        self.assertTrue(results["j1"].ok)
        self.assertFalse(results["j2"].ok)
        self.assertEqual(results["j2"].failed_code, "source_not_found")
        self.assertTrue(results["j3"].ok)

    def test_batch_consecutive_platform_signals_open_breaker(self):
        """连续两次平台级 signal（login_required/verification/rate_limited/blocked）
        触发熔断，后续岗位返回 source_blocked（T312 熔断器合同）。"""
        call_count = {"n": 0}

        def runner(job, **kw):
            call_count["n"] += 1
            return _fake_detail(signal="login_required")

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223, detail_runner=runner,
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": f"j{i}",
             "canonical_url": f"https://www.zhaopin.com/jobdetail/j{i}.htm"}
            for i in range(4)
        ]
        results = source.fetch_details_batch(jobs)
        # 前两个调用产生 login_required signal，第 2 个之后熔断器打开。
        self.assertFalse(results["j0"].ok)
        self.assertEqual(results["j0"].failed_code, "source_login_required")
        self.assertFalse(results["j1"].ok)
        self.assertEqual(results["j1"].failed_code, "source_login_required")
        # 熔断器打开后，后续岗位不再调用 runner，直接返回 source_blocked。
        self.assertLess(call_count["n"], len(jobs))
        for jid in ("j2", "j3"):
            self.assertFalse(results[jid].ok)
            self.assertEqual(results[jid].failed_code, "source_blocked")

    def test_batch_invalid_job_gets_failure(self):
        source = ZhilianCdpSource(browser_account="a", cdp_port=9223)
        results = source.fetch_details_batch(["not-a-dict"])
        self.assertIn("idx0", results)
        self.assertFalse(results["idx0"].ok)

    def test_batch_parallel_forwards_options_to_batch_runner(self):
        """tab_pool_size>1 走并行分支：参数透传 _batch_detail_runner，不透传 detail_runner。

        T313 回归：旧实现忽略 BOSS 节流参数走串行；新实现按高级设置参数
        对齐 BOSS（gap_min/gap_max → inter_job_gap_range，reset_every、
        tab_pool_size 同名透传）。
        """
        batch_calls = []
        detail_calls = []

        def batch_runner(list_data, **kw):
            batch_calls.append((list_data, kw))
            jobs = list_data.get("jobs", [])
            return [("ok", {"jd": "fake-jd"}) for _ in jobs], None

        def detail_runner(job, **kw):
            detail_calls.append(job)
            return _fake_detail(signal="ok")

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            detail_runner=detail_runner,
            batch_detail_runner=batch_runner,
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": f"j{i}",
             "canonical_url": f"https://www.zhaopin.com/jobdetail/j{i}.htm"}
            for i in range(3)
        ]
        results = source.fetch_details_batch(
            jobs, detail_output_path="out.json", max_batch_size=5,
            gap_min=1, gap_max=2, reset_every=3, tab_pool_size=4,
        )
        self.assertEqual(set(results.keys()), {"j0", "j1", "j2"})
        self.assertTrue(all(r.ok for r in results.values()))
        self.assertEqual(len(batch_calls), 1, "并行分支必须调用 _batch_detail_runner 一次")
        _, kwargs = batch_calls[0]
        self.assertEqual(kwargs["tab_pool_size"], 4)
        self.assertEqual(kwargs["inter_job_gap_range"], (1.0, 2.0))
        self.assertEqual(kwargs["reset_every"], 3)
        self.assertEqual(kwargs["cdp_port"], 9223)
        self.assertEqual(len(detail_calls), 0, "并行分支不得调用单条 detail_runner")

    def test_batch_serial_default_when_tab_pool_size_absent(self):
        """不传 tab_pool_size 必须走串行（现有测试零回归前提）。"""
        batch_calls = []
        detail_calls = []

        def batch_runner(list_data, **kw):
            batch_calls.append(kw)
            return [], None

        def detail_runner(job, **kw):
            detail_calls.append(job)
            return _fake_detail(signal="ok")

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            detail_runner=detail_runner,
            batch_detail_runner=batch_runner,
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": f"j{i}",
             "canonical_url": f"https://www.zhaopin.com/jobdetail/j{i}.htm"}
            for i in range(2)
        ]
        with mock.patch("webui.source.time.sleep") as sleep:
            results = source.fetch_details_batch(jobs, gap_min=1, gap_max=2)
        self.assertTrue(all(r.ok for r in results.values()))
        self.assertEqual(len(detail_calls), 2)
        self.assertEqual(len(batch_calls), 0)
        self.assertEqual(sleep.call_count, 1, "串行分支保留条间 gap")

    def test_batch_tab_pool_size_one_keeps_serial(self):
        """tab_pool_size=1 退化为串行路径（detail_runner 替身）。"""
        batch_calls = []

        def batch_runner(list_data, **kw):
            batch_calls.append(kw)
            return [], None

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            detail_runner=lambda job, **kw: _fake_detail(signal="ok"),
            batch_detail_runner=batch_runner,
        )
        jobs = [{"platform": "zhilian", "platform_job_id": "j0",
                 "canonical_url": "https://www.zhaopin.com/jobdetail/j0.htm"}]
        with mock.patch("webui.source.time.sleep"):
            results = source.fetch_details_batch(jobs, tab_pool_size=1)
        self.assertTrue(results["j0"].ok)
        self.assertEqual(len(batch_calls), 0)

    def test_batch_tab_pool_size_clamped(self):
        """tab_pool_size 钳制 1-10：<=0 走串行，>10 钳 10，字符串可解析。"""
        def make(batch_calls):
            return ZhilianCdpSource(
                browser_account="a", cdp_port=9223,
                detail_runner=lambda job, **kw: _fake_detail(signal="ok"),
                batch_detail_runner=lambda list_data, **kw: (
                    batch_calls.append(kw) or
                    ([( "ok", {"jd": "x"}) for _ in list_data.get("jobs", [])], None)
                ),
            )

        jobs = [{"platform": "zhilian", "platform_job_id": "j0",
                 "canonical_url": "https://www.zhaopin.com/jobdetail/j0.htm"}]
        with mock.patch("webui.source.time.sleep"):
            for bad_value in (0, -3):
                batch_calls = []
                source = make(batch_calls)
                results = source.fetch_details_batch(jobs, tab_pool_size=bad_value)
                self.assertTrue(results["j0"].ok)
                self.assertEqual(batch_calls, [], f"tab_pool_size={bad_value} 应钳到 1 走串行")
            for big_value, expected in ((11, 10), ("5", 5)):
                batch_calls = []
                source = make(batch_calls)
                results = source.fetch_details_batch(jobs, tab_pool_size=big_value)
                self.assertTrue(results["j0"].ok)
                self.assertEqual(batch_calls[0]["tab_pool_size"], expected,
                                 f"tab_pool_size={big_value} 应钳为 {expected}")

    def test_batch_parallel_single_failure_continues(self):
        """并行分支单条失败不阻断其余条目。"""
        def batch_runner(list_data, **kw):
            jobs = list_data.get("jobs", [])
            return [
                ("ok", {"jd": "jd-a"}) if j["platform_job_id"] != "j1"
                else ("not_found", {})
                for j in jobs
            ], None

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            batch_detail_runner=batch_runner,
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": f"j{i}",
             "canonical_url": f"https://www.zhaopin.com/jobdetail/j{i}.htm"}
            for i in range(3)
        ]
        results = source.fetch_details_batch(jobs, tab_pool_size=2)
        self.assertTrue(results["j0"].ok)
        self.assertFalse(results["j1"].ok)
        self.assertEqual(results["j1"].failed_code, "source_not_found")
        self.assertTrue(results["j2"].ok)

    def test_batch_parallel_platform_signals_open_breaker(self):
        """并行分支多条平台级 signal 连续记录后熔断打开，下一批不再调 runner。"""
        batch_calls = []

        def batch_runner(list_data, **kw):
            batch_calls.append(kw)
            jobs = list_data.get("jobs", [])
            return [("login_required", {}) for _ in jobs], "login_required"

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            batch_detail_runner=batch_runner,
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": f"j{i}",
             "canonical_url": f"https://www.zhaopin.com/jobdetail/j{i}.htm"}
            for i in range(4)
        ]
        results = source.fetch_details_batch(jobs, tab_pool_size=2)
        self.assertTrue(source.breaker.is_open(), "连续平台级 signal 必须打开熔断器")
        for jid in ("j0", "j1", "j2", "j3"):
            self.assertFalse(results[jid].ok)
            self.assertEqual(results[jid].failed_code, "source_login_required")
        # 熔断打开后第二批不再调用 runner，直接 source_blocked
        results2 = source.fetch_details_batch(jobs, tab_pool_size=2)
        self.assertEqual(len(batch_calls), 1, "熔断打开后不得再调用 runner")
        for jid in ("j0", "j1", "j2", "j3"):
            self.assertEqual(results2[jid].failed_code, "source_blocked")

    def test_batch_parallel_breaker_open_skips_runner(self):
        """调用前熔断已打开：整批 source_blocked，runner 零调用。"""
        batch_calls = []

        def batch_runner(list_data, **kw):
            batch_calls.append(kw)
            return [], None

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            batch_detail_runner=batch_runner,
        )
        source.breaker.record_signal("source_blocked")
        source.breaker.record_signal("source_blocked")
        jobs = [{"platform": "zhilian", "platform_job_id": "j0",
                 "canonical_url": "https://www.zhaopin.com/jobdetail/j0.htm"}]
        results = source.fetch_details_batch(jobs, tab_pool_size=3)
        self.assertEqual(results["j0"].failed_code, "source_blocked")
        self.assertEqual(len(batch_calls), 0)

    def test_batch_parallel_degraded_skipped_maps_blocked(self):
        """degrade 停工后未处理项（skipped）映射 source_blocked。"""
        def batch_runner(list_data, **kw):
            jobs = list_data.get("jobs", [])
            per_item = [("ok", {"jd": "jd"})] + [("skipped", {})] * (len(jobs) - 1)
            return per_item, "rate_limited"

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            batch_detail_runner=batch_runner,
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": f"j{i}",
             "canonical_url": f"https://www.zhaopin.com/jobdetail/j{i}.htm"}
            for i in range(3)
        ]
        results = source.fetch_details_batch(jobs, tab_pool_size=2)
        self.assertTrue(results["j0"].ok)
        self.assertEqual(results["j1"].failed_code, "source_blocked")
        self.assertEqual(results["j2"].failed_code, "source_blocked")

    def test_batch_parallel_cdp_unavailable_maps_cdp_error(self):
        """建池失败（cdp_unavailable 降级）映射 source_cdp_unavailable。"""
        def batch_runner(list_data, **kw):
            jobs = list_data.get("jobs", [])
            return [("skipped", {}) for _ in jobs], "cdp_unavailable"

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            batch_detail_runner=batch_runner,
        )
        jobs = [{"platform": "zhilian", "platform_job_id": "j0",
                 "canonical_url": "https://www.zhaopin.com/jobdetail/j0.htm"}]
        results = source.fetch_details_batch(jobs, tab_pool_size=2)
        self.assertEqual(results["j0"].failed_code, "source_cdp_unavailable")

    def test_batch_parallel_runner_exception_maps_unknown_error(self):
        """runner 抛异常：整批 source_unknown_error（与串行异常语义一致）。"""
        def batch_runner(list_data, **kw):
            raise RuntimeError("boom")

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            batch_detail_runner=batch_runner,
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": f"j{i}",
             "canonical_url": f"https://www.zhaopin.com/jobdetail/j{i}.htm"}
            for i in range(2)
        ]
        results = source.fetch_details_batch(jobs, tab_pool_size=2)
        self.assertEqual(results["j0"].failed_code, "source_unknown_error")
        self.assertEqual(results["j1"].failed_code, "source_unknown_error")

    def test_batch_parallel_item_done_replayed_in_input_order(self):
        """并行分支按输入顺序回放 on_item_done（对齐 BOSS 批返回语义）。"""
        def batch_runner(list_data, **kw):
            jobs = list_data.get("jobs", [])
            return [("ok", {"jd": "jd"}) for _ in jobs], None

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            batch_detail_runner=batch_runner,
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": f"j{i}",
             "canonical_url": f"https://www.zhaopin.com/jobdetail/j{i}.htm"}
            for i in range(3)
        ]
        done = []
        results = source.fetch_details_batch(jobs, tab_pool_size=2, on_item_done=done.append)
        self.assertTrue(all(r.ok for r in results.values()))
        self.assertEqual(done, [1, 2, 3])

    def test_batch_reports_item_done_after_each_job(self):
        """智联串行逐条抓取必须逐条回调 on_item_done，供前端实时进度。

        回归：此前条级进度只在整批返回后一次性回报，一批 15 条串行
        抓十几分钟，前端进度条一直停在 0、浏览器关闭才显示完成。
        """
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            detail_runner=lambda job, **kw: _fake_detail(signal="ok"),
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": f"j{i}",
             "canonical_url": f"https://www.zhaopin.com/jobdetail/j{i}.htm"}
            for i in range(3)
        ]
        done = []
        results = source.fetch_details_batch(jobs, on_item_done=done.append)
        self.assertTrue(all(r.ok for r in results.values()))
        self.assertEqual(done, [1, 2, 3], "每条完成后必须回调一次，含最后一条")

    def test_batch_item_done_covers_failed_and_blocked_jobs(self):
        """失败与熔断跳过项也必须推进 on_item_done 计数（进度不卡死）。"""
        def runner(job, **kw):
            return _fake_detail(signal="login_required")

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223, detail_runner=runner,
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": f"j{i}",
             "canonical_url": f"https://www.zhaopin.com/jobdetail/j{i}.htm"}
            for i in range(4)
        ]
        done = []
        results = source.fetch_details_batch(jobs, on_item_done=done.append)
        self.assertEqual(len(results), 4)
        self.assertEqual(done, [1, 2, 3, 4],
                         "熔断跳过项同样推进计数，进度才能走到底")

    def test_batch_parallel_missing_url_isolated_no_misalignment(self):
        """并行分支缺 canonical_url 的 job 单独判失败，其余结果不错位。

        回归：旧实现不校验 URL，raw 层去重后 per_item 变短，按索引对齐
        valid 导致后续 job 张冠李戴拿到前一个的 JD。
        """
        runner_inputs = []

        def batch_runner(list_data, **kw):
            runner_inputs.append(list_data.get("jobs", []))
            jobs = list_data.get("jobs", [])
            # 模拟真实 runner：每个收到的 job 都返回自己的详情
            return [("ok", {"jd": f"jd-{j['platform_job_id']}"}) for j in jobs], None

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            batch_detail_runner=batch_runner,
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": "j0",
             "canonical_url": "https://www.zhaopin.com/jobdetail/j0.htm"},
            {"platform": "zhilian", "platform_job_id": "j1"},  # 缺 URL
            {"platform": "zhilian", "platform_job_id": "j2",
             "canonical_url": "https://www.zhaopin.com/jobdetail/j2.htm"},
        ]
        results = source.fetch_details_batch(jobs, tab_pool_size=2)
        self.assertEqual(results["j0"].detail["jd"], "jd-j0")
        self.assertEqual(results["j2"].detail["jd"], "jd-j2", "j2 不得拿到 j0 的结果")
        self.assertFalse(results["j1"].ok)
        self.assertEqual(results["j1"].failed_code, "source_invalid_output")
        self.assertEqual([j["platform_job_id"] for j in runner_inputs[0]], ["j0", "j2"])

    def test_batch_parallel_duplicate_url_isolated(self):
        """并行分支重复 canonical_url 的 job 单独判失败，不触发错位。"""
        def batch_runner(list_data, **kw):
            jobs = list_data.get("jobs", [])
            return [("ok", {"jd": f"jd-{j['platform_job_id']}"}) for j in jobs], None

        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            batch_detail_runner=batch_runner,
        )
        jobs = [
            {"platform": "zhilian", "platform_job_id": "j0",
             "canonical_url": "https://www.zhaopin.com/jobdetail/same.htm"},
            {"platform": "zhilian", "platform_job_id": "j1",
             "canonical_url": "https://www.zhaopin.com/jobdetail/same.htm"},
            {"platform": "zhilian", "platform_job_id": "j2",
             "canonical_url": "https://www.zhaopin.com/jobdetail/j2.htm"},
        ]
        results = source.fetch_details_batch(jobs, tab_pool_size=2)
        self.assertEqual(results["j0"].detail["jd"], "jd-j0")
        self.assertEqual(results["j2"].detail["jd"], "jd-j2")
        self.assertFalse(results["j1"].ok)
        self.assertEqual(results["j1"].failed_code, "source_invalid_output")


# ===========================================================================
# T313：zhilian_cdp_raw.scrape_details_batch tab 池并行逻辑
# ===========================================================================
class ZhilianScrapeDetailsBatchTests(unittest.TestCase):
    """scrape_details_batch：connector/sleeper 替身验证 worker 循环行为。

    通过 patch ``_scrape_detail_on_ws``（按 job 查表返回，线程安全）与
    ``_reset_detail_session``（记录导航次数）隔离 CDP 细节，只测并行编排：
    错峰、条间 gap、reset、单条失败不中断、平台级信号降级、顺序恢复、去重。
    """

    def _make_waits(self):
        waits = []

        def sleeper(seconds, label=None):
            waits.append((seconds, label))

        return waits, sleeper

    def _connector(self, ws_list):
        import json as _json

        class _FakeTabWS:
            def __init__(self):
                self.sent = []
                self._msg_id = 0

            def send(self, text):
                self.sent.append(text)
                self._msg_id = int(_json.loads(text)["id"])

            def recv(self):
                return _json.dumps({"id": self._msg_id, "result": {}})

            def settimeout(self, timeout):
                pass

            def close(self):
                pass

        def connector(port):
            ws = _FakeTabWS()
            ws_list.append(ws)
            return ws, f"target-{len(ws_list)}"

        return connector

    def _jobs(self, n=3, prefix="j"):
        return [
            {"platform": "zhilian", "platform_job_id": f"{prefix}{i}",
             "canonical_url": f"https://www.zhaopin.com/jobdetail/{prefix}{i}.htm"}
            for i in range(n)
        ]

    def test_parallel_runs_all_jobs_and_restores_input_order(self):
        """tab 池并行抓完全部任务，per_item 按输入顺序返回。"""
        import scripts.zhilian_cdp_raw as zha

        jobs = self._jobs(4)
        scraped = []

        def fake_scrape(ws, job, *, sleeper=None):
            scraped.append(job["platform_job_id"])
            return "ok", {"jd": f"jd-{job['platform_job_id']}",
                          "platform_job_id": job["platform_job_id"]}

        waits, sleeper = self._make_waits()
        ws_list = []
        with mock.patch("scripts.zhilian_cdp_raw._scrape_detail_on_ws", side_effect=fake_scrape), \
             mock.patch("scripts.zhilian_cdp_raw._reset_detail_session"):
            per_item, degrade = zha.scrape_details_batch(
                {"jobs": jobs}, tab_pool_size=2,
                sleeper=sleeper, connector=self._connector(ws_list),
            )
        self.assertIsNone(degrade)
        self.assertEqual([sig for sig, _ in per_item], ["ok"] * 4)
        self.assertEqual(
            [d["platform_job_id"] for _, d in per_item],
            ["j0", "j1", "j2", "j3"],
            "per_item 必须按输入顺序恢复",
        )
        self.assertEqual(sorted(scraped), ["j0", "j1", "j2", "j3"])
        self.assertEqual(len(ws_list), 2, "tab_pool_size=2 应建 2 个 tab")

    def test_single_failure_does_not_stop_others(self):
        """单条失败（not_found）不中断，其余条目照常抓取。"""
        import scripts.zhilian_cdp_raw as zha

        jobs = self._jobs(3)

        def fake_scrape(ws, job, *, sleeper=None):
            if job["platform_job_id"] == "j1":
                return "not_found", {}
            return "ok", {"jd": "jd"}

        waits, sleeper = self._make_waits()
        with mock.patch("scripts.zhilian_cdp_raw._scrape_detail_on_ws", side_effect=fake_scrape):
            per_item, degrade = zha.scrape_details_batch(
                {"jobs": jobs}, tab_pool_size=2,
                sleeper=sleeper, connector=self._connector([]),
            )
        self.assertIsNone(degrade)
        self.assertEqual(per_item[0], ("ok", {"jd": "jd"}))
        self.assertEqual(per_item[1][0], "not_found")
        self.assertEqual(per_item[2][0], "ok")

    def test_platform_signal_triggers_degrade_and_skips_rest(self):
        """平台级 signal 触发停工：剩余队列任务以 skipped 占位。"""
        import scripts.zhilian_cdp_raw as zha

        jobs = self._jobs(4)

        def fake_scrape(ws, job, *, sleeper=None):
            return "rate_limited", {}

        waits, sleeper = self._make_waits()
        # tab=1 保证 degrade 后剩余任务确定留在队列（单 worker 串行领任务）
        with mock.patch("scripts.zhilian_cdp_raw._scrape_detail_on_ws", side_effect=fake_scrape):
            per_item, degrade = zha.scrape_details_batch(
                {"jobs": jobs}, tab_pool_size=1,
                sleeper=sleeper, connector=self._connector([]),
            )
        self.assertEqual(degrade, "rate_limited")
        # 首个队列任务（seq=0，orig_idx 随机）命中信号，其余 3 个留在队列
        self.assertEqual(per_item.count(("rate_limited", {})), 1)
        self.assertEqual(per_item.count(("skipped", {})), 3)

    def test_reset_every_navigates_home_between_jobs(self):
        """每抓 reset_every 条导航回首页（非最后一条时）。"""
        import scripts.zhilian_cdp_raw as zha

        waits, sleeper = self._make_waits()
        with mock.patch("scripts.zhilian_cdp_raw._scrape_detail_on_ws",
                        side_effect=lambda ws, job, *, sleeper=None: ("ok", {"jd": "jd"})), \
             mock.patch("scripts.zhilian_cdp_raw._reset_detail_session") as reset_mock:
            zha.scrape_details_batch(
                {"jobs": self._jobs(3)}, tab_pool_size=1, reset_every=2,
                sleeper=sleeper, connector=self._connector([]),
            )
        self.assertEqual(reset_mock.call_count, 1,
                         "3 条任务 reset_every=2：第 2 条后重置一次，最后一条不重置")

    def test_reset_not_on_last_job(self):
        """最后一条之后不导航回首页（对齐 BOSS 不补尾节奏）。"""
        import scripts.zhilian_cdp_raw as zha

        waits, sleeper = self._make_waits()
        with mock.patch("scripts.zhilian_cdp_raw._scrape_detail_on_ws",
                        side_effect=lambda ws, job, *, sleeper=None: ("ok", {"jd": "jd"})), \
             mock.patch("scripts.zhilian_cdp_raw._reset_detail_session") as reset_mock:
            zha.scrape_details_batch(
                {"jobs": self._jobs(4)}, tab_pool_size=1, reset_every=2,
                sleeper=sleeper, connector=self._connector([]),
            )
        self.assertEqual(reset_mock.call_count, 1,
                         "4 条 reset_every=2：第 2 条后重置一次，第 4 条（最后）不重置")

    def test_stagger_and_gap_sleeps(self):
        """错峰启动（tab>0）与条间 gap 通过 sleeper 记录。"""
        import scripts.zhilian_cdp_raw as zha

        jobs = self._jobs(3)
        waits, sleeper = self._make_waits()
        with mock.patch("scripts.zhilian_cdp_raw._scrape_detail_on_ws",
                        side_effect=lambda ws, job, *, sleeper=None: ("ok", {"jd": "jd"})), \
             mock.patch("scripts.zhilian_cdp_raw._reset_detail_session"):
            zha.scrape_details_batch(
                {"jobs": jobs}, tab_pool_size=2, reset_every=99,
                sleeper=sleeper, connector=self._connector([]),
            )
        labels = [label for _, label in waits]
        self.assertIn("stagger", labels, "tab2 必须错峰启动")
        self.assertGreaterEqual(labels.count("inter_job_gap"), 1)

    def test_visibility_injected_on_each_new_tab(self):
        """每个新 tab 建池时必须注入 visibility 覆盖脚本。"""
        import scripts.zhilian_cdp_raw as zha

        ws_list = []
        waits, sleeper = self._make_waits()
        with mock.patch("scripts.zhilian_cdp_raw._scrape_detail_on_ws",
                        side_effect=lambda ws, job, *, sleeper=None: ("ok", {"jd": "jd"})), \
             mock.patch("scripts.zhilian_cdp_raw._reset_detail_session"):
            zha.scrape_details_batch(
                {"jobs": self._jobs(2)}, tab_pool_size=2,
                sleeper=sleeper, connector=self._connector(ws_list),
            )
        for ws in ws_list:
            self.assertTrue(
                any("Page.addScriptToEvaluateOnNewDocument" in s for s in ws.sent),
                "新 tab 必须注入 visibility 覆盖脚本",
            )

    def test_deduplicates_by_canonical_url(self):
        """同 canonical_url 只抓一条（保持输入顺序）。"""
        import scripts.zhilian_cdp_raw as zha

        jobs = self._jobs(2)
        jobs.append({"platform": "zhilian", "platform_job_id": "dup",
                     "canonical_url": jobs[0]["canonical_url"]})
        scraped = []

        def fake_scrape(ws, job, *, sleeper=None):
            scraped.append(job["platform_job_id"])
            return "ok", {"jd": "jd"}

        waits, sleeper = self._make_waits()
        with mock.patch("scripts.zhilian_cdp_raw._scrape_detail_on_ws", side_effect=fake_scrape):
            per_item, degrade = zha.scrape_details_batch(
                {"jobs": jobs}, tab_pool_size=2,
                sleeper=sleeper, connector=self._connector([]),
            )
        self.assertEqual(len(scraped), 2)
        self.assertEqual([sig for sig, _ in per_item], ["ok", "ok"])

    def test_empty_input_returns_empty(self):
        """空任务列表直接返回 ([], None)，不建池。"""
        import scripts.zhilian_cdp_raw as zha

        ws_list = []
        per_item, degrade = zha.scrape_details_batch(
            {"jobs": []}, tab_pool_size=2, connector=self._connector(ws_list),
        )
        self.assertEqual(per_item, [])
        self.assertIsNone(degrade)
        self.assertEqual(ws_list, [])

    def test_connector_failure_degrades_cdp_unavailable(self):
        """建池失败：全部 skipped + degrade=cdp_unavailable。"""
        import scripts.zhilian_cdp_raw as zha

        def connector(port):
            raise RuntimeError("no_cdp")

        waits, sleeper = self._make_waits()
        per_item, degrade = zha.scrape_details_batch(
            {"jobs": self._jobs(2)}, tab_pool_size=2,
            sleeper=sleeper, connector=connector,
        )
        self.assertEqual(degrade, "cdp_unavailable")
        self.assertEqual(per_item, [("skipped", {}), ("skipped", {})])

    def test_validates_parameters(self):
        """tab_pool_size/reset_every/gap/stagger 非法值必须拒绝。"""
        import scripts.zhilian_cdp_raw as zha

        waits, sleeper = self._make_waits()
        jobs = {"jobs": self._jobs(1)}
        for bad in (0, 11, 1.5, "5"):
            with self.assertRaises(ValueError):
                zha.scrape_details_batch(jobs, tab_pool_size=bad, sleeper=sleeper)
        for bad in (0, -2):
            with self.assertRaises(ValueError):
                zha.scrape_details_batch(jobs, reset_every=bad, sleeper=sleeper)
        with self.assertRaises(ValueError):
            zha.scrape_details_batch(jobs, inter_job_gap_range=(5, 1), sleeper=sleeper)
        with self.assertRaises(ValueError):
            zha.scrape_details_batch(jobs, stagger_range=(-1, 2), sleeper=sleeper)

    def test_worker_exception_maps_to_single_failure(self):
        """worker 内 _scrape_detail_on_ws 抛异常只废掉该条，不杀线程。

        回归：旧实现无异常保护，CDP 求值异常直接杀死 worker 线程，
        剩余任务全部变 skipped，违反"单条失败不中断"契约。
        """
        import scripts.zhilian_cdp_raw as zha

        jobs = self._jobs(3)

        def fake_scrape(ws, job, *, sleeper=None):
            if job["platform_job_id"] == "j1":
                raise RuntimeError("page evaluate failed")
            return "ok", {"jd": "jd"}

        waits, sleeper = self._make_waits()
        with mock.patch("scripts.zhilian_cdp_raw._scrape_detail_on_ws", side_effect=fake_scrape):
            per_item, degrade = zha.scrape_details_batch(
                {"jobs": jobs}, tab_pool_size=2,
                sleeper=sleeper, connector=self._connector([]),
            )
        self.assertIsNone(degrade)
        self.assertEqual(per_item[0], ("ok", {"jd": "jd"}))
        self.assertEqual(per_item[1][0], "unreachable", "异常条映射单条失败")
        self.assertEqual(per_item[2], ("ok", {"jd": "jd"}), "剩余任务继续抓取")

    def test_default_sleeper_accepts_label_kwarg(self):
        """默认 sleeper 必须兼容 label 关键字（对齐 BOSS _default_scrape_sleeper）。

        回归：默认 time.sleep 不接受关键字参数，worker 在第一次
        stagger/gap 等待时抛 TypeError 崩溃，全部任务变 skipped。
        """
        import scripts.zhilian_cdp_raw as zha

        jobs = self._jobs(3)
        # gap/stagger 置 0 避免真实等待；reset_every 放大避免触发首页重置
        with mock.patch("scripts.zhilian_cdp_raw._scrape_detail_on_ws",
                        return_value=("ok", {"jd": "jd"})), \
             mock.patch("scripts.zhilian_cdp_raw._reset_detail_session"):
            per_item, degrade = zha.scrape_details_batch(
                {"jobs": jobs}, tab_pool_size=2,
                inter_job_gap_range=(0, 0), stagger_range=(0, 0),
                reset_every=999, connector=self._connector([]),
            )
        self.assertIsNone(degrade)
        self.assertEqual([sig for sig, _ in per_item], ["ok", "ok", "ok"],
                         "默认 sleeper 路径下全部任务必须完成，无线程崩溃")


class ZhilianCdpSourceOutcomeContractTests(_LoginCacheIsolated):
    """T313：adapter outcome 可无损表达 non_empty/empty/failed/paused、计数、证据和安全错误。"""

    def test_non_empty_outcome_carries_job_count(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            list_runner=lambda item: _fake_list(signal="ok", jobs=[
                {"platform": "zhilian", "platform_job_id": "x",
                 "title": "T", "company": "C", "canonical_url": "https://www.zhaopin.com/jobdetail/x.htm"},
            ]),
        )
        item = {
            "platform": "zhilian", "keyword": "Python",
            "city": {"name": "全国", "platform_code": "jl0", "mapping_version": 1},
            "target_pages": 1,
            "input_hash": _zhilian_input_hash({
                "platform": "zhilian", "keyword": "Python",
                "city": {"name": "全国", "platform_code": "jl0", "mapping_version": 1},
                "target_pages": 1,
            }),
        }
        outcome = source.fetch_list(item)
        self.assertTrue(outcome.ok)
        self.assertFalse(outcome.empty_result)
        self.assertEqual(len(outcome.jobs), 1)
        self.assertIsNone(outcome.failed_code)
        self.assertIsNotNone(outcome.input_hash)

    def test_failed_outcome_carries_safe_code_and_reason(self):
        source = ZhilianCdpSource(
            browser_account="a", cdp_port=9223,
            preflight_runner=lambda port: _fake_preflight(signal="rate_limited"),
        )
        outcome = source.preflight()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_rate_limited")
        self.assertIsInstance(outcome.failed_reason, str)

    def test_paused_signal_codes_are_distinct_from_failed_codes(self):
        """paused 信号码（login/verification/rate_limited/blocked/cdp_unavailable）
        必须与 failed 信号码（unreachable/timeout/invalid_output/not_found/input_drift）
        可区分。"""
        paused_codes = {
            "source_login_required", "source_verification_required",
            "source_rate_limited", "source_blocked", "source_cdp_unavailable",
        }
        failed_codes = {
            "source_unreachable", "source_timeout", "source_invalid_output",
            "source_not_found", "source_input_drift", "source_unknown_error",
        }
        self.assertTrue(paused_codes.isdisjoint(failed_codes))

    def test_empty_result_requires_evidence_and_zero_jobs(self):
        """真实空结果 outcome：ok=True, jobs=[], empty_result=True, empty_evidence 必填。

        T308 真实空结果判定需要 empty_state_markers fixture（blocked_facts），
        本任务不实现真实判定，但 outcome 合同必须能无损表达。
        """
        evidence = {
            "kind": "explicit_empty_state",
            "fixture_version": "zhilian-list-v1",
            "marker": "normalized-empty-state",
        }
        outcome = SourceOutcome.empty_success(
            empty_evidence=evidence,
            safe_log="platform=zhilian stage=list empty_result=1",
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.jobs, [])
        self.assertTrue(outcome.empty_result)
        self.assertEqual(outcome.empty_evidence, evidence)


# ---------------------------------------------------------------------------
# 测试替身：模拟 scripts/zhilian_cdp_raw.py 的 preflight/detail/list 返回。
# 真实 marker 检测函数在 scripts/zhilian_cdp_raw.py 中保持占位（返回 None），
# adapter 通过 preflight_runner/detail_runner/list_runner 注入测试替身。
# ---------------------------------------------------------------------------

def _fake_preflight(*, signal: str):
    """模拟 zhilian_cdp_raw.preflight() 返回的 signal 字符串。

    返回值约定（与 scripts/zhilian_cdp_raw.py preflight 一致）：
      "ok" / "cdp_unavailable" / "login_required" / "verification" /
      "rate_limited" / "blocked" / "unreachable" / "timeout"
    """
    return signal


def _fake_detail(*, signal: str):
    """模拟 zhilian_cdp_raw.fetch_detail() 返回的 (signal, detail_dict)。"""
    if signal == "ok":
        return "ok", {"jd": "fake-jd", "platform_job_id": "fake"}
    if signal == "not_found":
        return "not_found", {}
    return signal, {}


def _fake_list(*, signal: str, jobs=None):
    """模拟 zhilian_cdp_raw.fetch_list() 返回的 (signal, jobs_list)。"""
    if signal == "ok":
        return "ok", jobs or []
    return signal, []


def _zhilian_input_hash(payload: dict) -> str:
    """测试用 input_hash 计算（与 webui.source._zhilian_input_hash 一致）。"""
    from webui.source import _zhilian_input_hash as _impl
    return _impl(payload)


# ===========================================================================
# spec003 tasks004 T026 — BossCdpSource in_process argv 翻译
#
# 冻结合同：specs/003-desktop-exe/contracts/inprocess-runner.md §4.3。
#
# in_process=True 时，_runner 把本类构建的三类命令（list-only / detail-only /
# detail-batch）翻译为 programmatic 库式调用（run_search_programmatic /
# scrape_details），不经过 argv 文本往返；其余行为（SourceOutcome、事件校验、
# 熔断器、输入 hash、产物读取）零改动。无法翻译的命令返回失败 outcome。
# ===========================================================================
import json as _json_for_inprocess
from scripts import boss_cdp_raw as _boss_for_inprocess
from webui.source import _input_hash as _boss_input_hash


class BossCdpSourceInProcessTests(unittest.TestCase):
    """T026：BossCdpSource in_process=True argv 翻译执行器。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.artifact_root = self.root / "artifacts"
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp.cleanup()

    def _make_source(self, **overrides):
        kwargs = dict(
            artifact_root=self.artifact_root,
            cdp_port=9222,
            in_process=True,
        )
        kwargs.update(overrides)
        return BossCdpSource(**kwargs)

    def _write_json(self, path, payload):
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json_for_inprocess.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # ---- 构造参数 ----------------------------------------------------

    def test_constructor_accepts_in_process_false(self):
        """in_process 参数默认 False。"""
        BossCdpSource.__new__(BossCdpSource)
        # 只验证签名接受参数，不触发依赖加载
        import inspect
        sig = inspect.signature(BossCdpSource.__init__)
        self.assertIn("in_process", sig.parameters)
        self.assertFalse(sig.parameters["in_process"].default)

    def test_default_runner_forwards_on_poll_to_executor(self):
        """子进程模式默认 runner 必须把 on_poll 传给执行器，页事件才能实时转发。"""
        from webui.process_executor import ExecutionResult
        source = self._make_source(in_process=False)
        captured = {}
        poll = lambda: None

        def fake_execute(command, **kwargs):
            captured.update(kwargs)
            return ExecutionResult(0, "")

        with mock.patch.object(source._executor, "execute", side_effect=fake_execute):
            source._run_command(["python", "x"], 30, on_poll=poll)
        self.assertIs(captured.get("on_poll"), poll)

    # ---- list-only 翻译 ---------------------------------------------

    def test_list_only_translates_to_programmatic(self):
        """list-only（--no-detail）命令翻译为 run_search_programmatic(detail=False)。"""
        source = self._make_source()
        list_path = str(self.artifact_root / "list.json")
        plan_item = {
            "keyword": "AI",
            "city": "上海",
            "source_filters": {},
            "target_pages": 1,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "上海",
                "source_filters": {}, "target_pages": 1,
            }),
            "list_output_path": list_path,
        }

        captured = {}

        def fake_run(**kwargs):
            captured.update(kwargs)
            # 写列表产物（jobs 含 BOSS 字段，触发 _normalize_job_fields）
            self._write_json(kwargs["output_path"], {
                "jobs": [{"encrypt_job_id": "j1", "job_link": "https://www.zhipin.com/job/1",
                          "boss_name": "Corp"}]
            })
            return {"list_data": {"jobs": []}, "details": None}

        with mock.patch.object(_boss_for_inprocess, "run_search_programmatic", side_effect=fake_run):
            outcome = source.fetch_list(plan_item)

        # programmatic 被调用，detail=False
        self.assertTrue(outcome.ok)
        self.assertEqual(captured["keyword"], "AI")
        self.assertEqual(captured["city"], "上海")
        self.assertEqual(captured["pages"], 1)
        self.assertFalse(captured["detail"])
        self.assertTrue(captured["skip_login_check"])
        self.assertEqual(captured["output_path"], list_path)
        # jobs 被归一化（encrypt_job_id → job_id）
        self.assertEqual(len(outcome.jobs), 1)
        self.assertEqual(outcome.jobs[0]["job_id"], "j1")

    def test_list_only_filters_translated(self):
        """list-only 命令的 source_filters 翻译为 filters dict。"""
        source = self._make_source()
        list_path = str(self.artifact_root / "list_filter.json")
        filters = {"salary": "403", "experience": "003"}
        plan_item = {
            "keyword": "AI", "city": "北京",
            "source_filters": filters,
            "target_pages": 2,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "北京",
                "source_filters": filters, "target_pages": 2,
            }),
            "list_output_path": list_path,
        }

        captured = {}

        def fake_run(**kwargs):
            captured.update(kwargs)
            self._write_json(kwargs["output_path"], {"jobs": []})
            return {"list_data": {"jobs": []}, "details": None}

        with mock.patch.object(_boss_for_inprocess, "run_search_programmatic", side_effect=fake_run):
            source.fetch_list(plan_item)

        self.assertEqual(captured["filters"], filters)

    def test_list_only_translates_start_page(self):
        """断点续抓的 start_page 必须翻译给 run_search_programmatic。"""
        source = self._make_source()
        list_path = str(self.artifact_root / "list_start.json")
        plan_item = {
            "keyword": "AI", "city": "上海",
            "source_filters": {}, "target_pages": 10, "start_page": 4,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "上海",
                "source_filters": {}, "target_pages": 10,
            }),
            "list_output_path": list_path,
        }
        captured = {}

        def fake_run(**kwargs):
            captured.update(kwargs)
            self._write_json(kwargs["output_path"], {"jobs": []})
            return {"list_data": {"jobs": []}, "details": None}

        with mock.patch.object(_boss_for_inprocess, "run_search_programmatic", side_effect=fake_run):
            source.fetch_list(plan_item)
        self.assertEqual(captured["start_page"], 4)

    def test_list_only_forwards_page_completed_callback(self):
        """in-process 列表抓取把页级事件直接转发给调用方。"""
        source = self._make_source()
        list_path = str(self.artifact_root / "list_pages.json")
        plan_item = {
            "keyword": "AI", "city": "上海",
            "source_filters": {}, "target_pages": 2,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "上海",
                "source_filters": {}, "target_pages": 2,
            }),
            "list_output_path": list_path,
        }
        events = []

        def fake_run(**kwargs):
            self._write_json(kwargs["output_path"], {
                "jobs": [{"encrypt_job_id": "j1", "job_link": "https://zhipin.example/1"}]})
            if kwargs.get("on_page_completed"):
                kwargs["on_page_completed"]({
                    "kind": "page_completed", "combo_key": "AI|上海",
                    "keyword": "AI", "city": "上海", "page": 1, "target_pages": 2,
                    "jobs_delta": 1, "jobs_count": 1, "has_more": True,
                    "resume_page": 2, "last_completed_page": 1,
                    "jobs_snapshot": [{"job_id": "j1"}],
                })
            return {"list_data": {"jobs": []}, "details": None}

        with mock.patch.object(_boss_for_inprocess, "run_search_programmatic", side_effect=fake_run):
            outcome = source.fetch_list(plan_item, on_page_completed=events.append)

        self.assertTrue(outcome.ok)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["page"], 1)
        self.assertEqual(events[0]["jobs_count"], 1)

    # ---- detail-only 翻译 -------------------------------------------

    def test_detail_only_translates_to_scrape_details(self):
        """detail-only（--input + --detail + --max-details 1）翻译为 scrape_details。"""
        source = self._make_source()
        detail_path = str(self.artifact_root / "detail.json")
        job = {"job_id": "j1", "source_url": "https://www.zhipin.com/job/1",
               "job_link": "https://www.zhipin.com/job/1", "company": "Corp"}

        captured = {}

        def fake_scrape_details(list_data, max_details, output_path, **kwargs):
            captured.update(kwargs)
            captured["list_data"] = list_data
            captured["max_details"] = max_details
            captured["output_path"] = output_path
            self._write_json(output_path, [{"job_id": "j1", "jd": "desc",
                                            "job_link": "https://www.zhipin.com/job/1"}])
            return [{"job_id": "j1", "jd": "desc"}]

        with mock.patch.object(_boss_for_inprocess, "scrape_details", side_effect=fake_scrape_details):
            outcome = source.fetch_detail(job, detail_output_path=detail_path)

        self.assertTrue(outcome.ok)
        self.assertEqual(captured["max_details"], 1)
        self.assertEqual(captured["output_path"], detail_path)
        # input.json 被读取并构造为 list_data
        self.assertEqual(len(captured["list_data"]["jobs"]), 1)

    # ---- detail-batch 翻译 ------------------------------------------

    def test_detail_batch_translates_to_scrape_details_with_events(self):
        """detail-batch（--events-output + --enable-parallel）翻译为 scrape_details with events。"""
        source = self._make_source()
        detail_path = str(self.artifact_root / "batch_detail.json")
        jobs = [
            {"job_id": "j1", "source_url": "https://www.zhipin.com/job/1",
             "job_link": "https://www.zhipin.com/job/1"},
            {"job_id": "j2", "source_url": "https://www.zhipin.com/job/2",
             "job_link": "https://www.zhipin.com/job/2"},
        ]

        captured = {}

        def fake_scrape_details(list_data, max_details, output_path, **kwargs):
            captured.update(kwargs)
            captured["list_data"] = list_data
            captured["max_details"] = max_details
            captured["output_path"] = output_path
            # 写详情产物
            self._write_json(output_path, [
                {"job_id": "j1", "jd": "d1", "job_link": "https://www.zhipin.com/job/1"},
                {"job_id": "j2", "jd": "d2", "job_link": "https://www.zhipin.com/job/2"},
            ])
            # 发出 terminal safe events
            if kwargs.get("event_callback"):
                kwargs["event_callback"]({
                    "kind": "detail", "status": "completed",
                    "job_id": "https://www.zhipin.com/job/1",
                    "duration_ms": 100, "safe_code": "ok",
                })
                kwargs["event_callback"]({
                    "kind": "detail", "status": "completed",
                    "job_id": "https://www.zhipin.com/job/2",
                    "duration_ms": 200, "safe_code": "ok",
                })
            return [{"job_id": "j1"}, {"job_id": "j2"}]

        with mock.patch.object(_boss_for_inprocess, "scrape_details", side_effect=fake_scrape_details):
            results = source.fetch_details_batch(jobs, detail_output_path=detail_path)

        # batch_size=2（len(valid_jobs)），enable_parallel=True
        self.assertTrue(captured.get("enable_parallel"))
        self.assertEqual(captured["max_details"], 2)
        # event_callback 被传入（写 events JSONL）
        self.assertTrue(callable(captured.get("event_callback")))
        # 两个 job 都成功
        self.assertEqual(len(results), 2)
        self.assertTrue(all(o.ok for o in results.values()))

    def test_list_empty_without_events_maps_to_cdp_lost(self):
        """退出码 0 + 0 结果 + 0 事件：列表阶段视为浏览器/CDP 失联。"""
        source = self._make_source()
        list_path = str(self.artifact_root / "list_empty_lost.json")
        plan_item = {
            "keyword": "AI", "city": "上海", "source_filters": {},
            "target_pages": 1,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "上海",
                "source_filters": {}, "target_pages": 1,
            }),
            "list_output_path": list_path,
        }

        def fake_run(**kwargs):
            self._write_json(kwargs["output_path"], {"jobs": []})
            return {"list_data": {"jobs": []}, "details": None}

        with mock.patch.object(_boss_for_inprocess, "run_search_programmatic",
                               side_effect=fake_run), \
                mock.patch.object(source.breaker, "record_signal") as m_signal:
            outcome = source.fetch_list(plan_item)

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_cdp_unavailable")
        self.assertIn("empty_batch_no_events_cdp_lost", outcome.safe_log)
        m_signal.assert_called_once_with("source_cdp_unavailable")

    def test_list_empty_with_page_event_remains_success(self):
        """有页级事件佐证的真实空结果不被误判为浏览器失联。"""
        source = self._make_source()
        list_path = str(self.artifact_root / "list_empty_ok.json")
        plan_item = {
            "keyword": "AI", "city": "上海", "source_filters": {},
            "target_pages": 1,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "上海",
                "source_filters": {}, "target_pages": 1,
            }),
            "list_output_path": list_path,
        }

        def fake_run(**kwargs):
            self._write_json(kwargs["output_path"], {"jobs": []})
            events_path = kwargs.get("list_events_output")
            if events_path:
                pathlib.Path(events_path).write_text(
                    _json_for_inprocess.dumps({
                        "kind": "page_completed", "combo_key": "AI|上海",
                        "keyword": "AI", "city": "上海", "page": 1,
                        "target_pages": 1, "jobs_delta": 0, "jobs_count": 0,
                        "has_more": False, "resume_page": 2, "last_completed_page": 1,
                    }) + "\n", encoding="utf-8")
            return {"list_data": {"jobs": []}, "details": None}

        with mock.patch.object(_boss_for_inprocess, "run_search_programmatic",
                               side_effect=fake_run):
            outcome = source.fetch_list(plan_item)

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.jobs, [])

    def test_detail_batch_empty_without_events_not_mapped_to_cdp_lost(self):
        """B050：JD 批次退出码 0 + 0 结果 + 0 事件不再判为浏览器失联。"""
        source = self._make_source()
        detail_path = str(self.artifact_root / "batch_empty_lost.json")
        jobs = [
            {"job_id": "j1", "source_url": "https://www.zhipin.com/job/1",
             "job_link": "https://www.zhipin.com/job/1"},
        ]

        def fake_scrape_details(*_args, **_kwargs):
            self._write_json(_kwargs["output_path"], [])
            return []

        with mock.patch.object(_boss_for_inprocess, "scrape_details",
                               side_effect=fake_scrape_details), \
                mock.patch.object(source.breaker, "record_signal") as m_signal:
            results = source.fetch_details_batch(jobs, detail_output_path=detail_path)

        self.assertEqual(len(results), 1)
        self.assertFalse(results["j1"].ok)
        self.assertEqual(results["j1"].failed_code, "source_invalid_output")
        self.assertNotIn("empty_batch_no_events_cdp_lost", results["j1"].safe_log)
        m_signal.assert_not_called()

    # ---- B050/B053：worker 异常透出与运行级计数隔离 ----------------------

    def test_worker_exception_surfaces_instead_of_empty_success(self):
        """B050：并行 worker 命中请求上限必须透出，不得返回空成功。"""
        class FailingSession:
            def __init__(self, *_args, **_kwargs):
                pass

            def send(self, *_args, **_kwargs):
                raise _boss_for_inprocess.RequestLimitExceededError("limit hit")

            def eval_js(self, *_args, **_kwargs):
                raise AssertionError("unused")

            def close(self):
                pass

        output = self.artifact_root / "batch_limit.json"
        with self.assertRaises(_boss_for_inprocess.RequestLimitExceededError):
            _boss_for_inprocess.scrape_details(
                {"jobs": [
                    {"job_link": "https://www.zhipin.com/job/1", "job_id": "j1"},
                    {"job_link": "https://www.zhipin.com/job/2", "job_id": "j2"},
                ]},
                output_path=str(output),
                cdp_port=9222,
                enable_parallel=True,
                tab_pool_size=2,
                session_factory=FailingSession,
                sleeper=lambda seconds, label=None: None,
                inter_job_gap_range=(0, 0),
                stagger_range=(0, 0),
            )
        self.assertEqual(output.read_text(encoding="utf-8"), "[]")

    def test_request_counter_is_isolated_per_run(self):
        """B053：命中上限后新一轮 begin_request_run 从 0 重新计数。"""
        with mock.patch.object(_boss_for_inprocess, "MAX_API_REQUESTS", 3):
            _boss_for_inprocess.begin_request_run()
            for _ in range(3):
                _boss_for_inprocess.incr_request()
            with self.assertRaises(_boss_for_inprocess.RequestLimitExceededError):
                _boss_for_inprocess.incr_request()
            _boss_for_inprocess.begin_request_run()
            _boss_for_inprocess.incr_request()
            _boss_for_inprocess.incr_request()
            _boss_for_inprocess.incr_request()
            with self.assertRaises(_boss_for_inprocess.RequestLimitExceededError):
                _boss_for_inprocess.incr_request()

    # ---- 异常映射 ----------------------------------------------------

    def test_cdp_unavailable_maps_to_returncode_2(self):
        """CDPUnavailableError → (2, ...) → source_cdp_unavailable，熔断器记录。"""
        source = self._make_source()
        list_path = str(self.artifact_root / "list_cdp.json")
        plan_item = {
            "keyword": "AI", "city": "上海", "source_filters": {},
            "target_pages": 1,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "上海",
                "source_filters": {}, "target_pages": 1,
            }),
            "list_output_path": list_path,
        }
        with mock.patch.object(_boss_for_inprocess, "run_search_programmatic",
                               side_effect=_boss_for_inprocess.CDPUnavailableError("cdp down")):
            outcome = source.fetch_list(plan_item)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_cdp_unavailable")

    def test_invalid_start_page_maps_to_returncode_3(self):
        """start_page 超出 pages → (3, ...) → source_invalid_output，不误判 CDP。"""
        source = self._make_source()
        list_path = str(self.artifact_root / "list_invalid_start.json")
        plan_item = {
            "keyword": "AI", "city": "上海", "source_filters": {},
            "target_pages": 1,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "上海",
                "source_filters": {}, "target_pages": 1,
            }),
            "list_output_path": list_path,
        }
        with mock.patch.object(_boss_for_inprocess, "run_search_programmatic",
                               side_effect=ValueError("start_page 必须在 1 到 3 之间")):
            outcome = source.fetch_list(plan_item)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_invalid_output")

    def test_programmatic_run_resets_counter_between_runs(self):
        """B053：run_search_programmatic 每轮独立计数，组合流程内不重复重置。"""
        with mock.patch.object(_boss_for_inprocess, "MAX_API_REQUESTS", 3):
            output = self.artifact_root / "prog_run.json"

            def fake_list(*_args, **_kwargs):
                for _ in range(3):
                    _boss_for_inprocess.incr_request()
                return {"jobs": [], "total": 0}

            with mock.patch.object(
                _boss_for_inprocess, "scrape_list", side_effect=fake_list,
            ), mock.patch.object(
                _boss_for_inprocess, "check_login_state", return_value=True,
            ):
                _boss_for_inprocess.run_search_programmatic(
                    keyword="AI", city="上海", pages=1,
                    output_path=str(output), detail=False,
                )
                _boss_for_inprocess.run_search_programmatic(
                    keyword="AI", city="上海", pages=1,
                    output_path=str(output), detail=False,
                )

    def test_request_limit_exceeded_maps_to_returncode_11(self):
        """B053：RequestLimitExceededError → (11, ...) → source_request_limit_exceeded。"""
        source = self._make_source()
        list_path = str(self.artifact_root / "list_limit.json")
        plan_item = {
            "keyword": "AI", "city": "上海", "source_filters": {},
            "target_pages": 1,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "上海",
                "source_filters": {}, "target_pages": 1,
            }),
            "list_output_path": list_path,
        }
        with mock.patch.object(
            _boss_for_inprocess, "run_search_programmatic",
            side_effect=_boss_for_inprocess.RequestLimitExceededError("limit hit"),
        ):
            outcome = source.fetch_list(plan_item)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_request_limit_exceeded")

    def test_login_required_maps_to_returncode_1(self):
        """LoginRequiredError → (1, '登录') → source_login_required。"""
        source = self._make_source()
        list_path = str(self.artifact_root / "list_login.json")
        plan_item = {
            "keyword": "AI", "city": "上海", "source_filters": {},
            "target_pages": 1,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "上海",
                "source_filters": {}, "target_pages": 1,
            }),
            "list_output_path": list_path,
        }
        with mock.patch.object(_boss_for_inprocess, "run_search_programmatic",
                               side_effect=_boss_for_inprocess.LoginRequiredError("not logged in")):
            outcome = source.fetch_list(plan_item)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_login_required")

    def test_risk_control_maps_to_returncode_10(self):
        """RiskControlError → (10, reason) → 按 reason 分类。"""
        source = self._make_source()
        list_path = str(self.artifact_root / "list_risk.json")
        plan_item = {
            "keyword": "AI", "city": "上海", "source_filters": {},
            "target_pages": 1,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "上海",
                "source_filters": {}, "target_pages": 1,
            }),
            "list_output_path": list_path,
        }
        err = _boss_for_inprocess.RiskControlError("频繁访问，请稍后再试", page=1)
        with mock.patch.object(_boss_for_inprocess, "run_search_programmatic", side_effect=err):
            outcome = source.fetch_list(plan_item)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_rate_limited")

    def test_risk_control_records_run_id_and_platform(self):
        """高置信 BOSS 风控写冷却时带真实 run_id 与平台。"""
        source = self._make_source(run_id="run-b", browser_account="a")
        list_path = str(self.artifact_root / "list_risk_run.json")
        plan_item = {
            "keyword": "AI", "city": "上海", "source_filters": {},
            "target_pages": 1,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "上海",
                "source_filters": {}, "target_pages": 1,
            }),
            "list_output_path": list_path,
        }
        err = _boss_for_inprocess.RiskControlError("操作频繁，请稍后再试", page=1)
        with mock.patch.object(_boss_for_inprocess, "run_search_programmatic", side_effect=err), \
                mock.patch("webui.source._record_risk_signals") as rec:
            outcome = source.fetch_list(plan_item)
        self.assertEqual(outcome.failed_code, "source_rate_limited")
        rec.assert_called_once()
        self.assertEqual(rec.call_args.args[:3], ("a", "boss", "source_rate_limited"))
        self.assertEqual(rec.call_args.kwargs["run_id"], "run-b")

    # ---- 无法翻译命令 -----------------------------------------------

    def test_untranslatable_command_returns_failure(self):
        """无法翻译的命令（如 --setup-chrome）返回失败 outcome 而非崩溃。"""
        source = self._make_source()
        # 构造一个 --setup-chrome 命令，通过自定义 runner 触发翻译器
        # setup-chrome 不属于三类命令，翻译器应返回失败
        list_path = str(self.artifact_root / "list_setup.json")
        plan_item = {
            "keyword": "AI", "city": "上海", "source_filters": {},
            "target_pages": 1,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "上海",
                "source_filters": {}, "target_pages": 1,
            }),
            "list_output_path": list_path,
        }
        # mock run_search_programmatic 不应被调用（setup-chrome 不可翻译）
        # 但 fetch_list 构建的是 list-only 命令（--no-detail），不是 setup-chrome
        # 所以这个测试验证的是：翻译器对未知 argv 模式返回失败
        # 我们通过 mock _build_list_command 返回一个不可翻译的命令来测试
        with mock.patch.object(BossCdpSource, "_build_list_command",
                               return_value=["python", "scraper.py", "--setup-chrome"]):
            with mock.patch.object(_boss_for_inprocess, "run_search_programmatic") as m_run:
                outcome = source.fetch_list(plan_item)
        # run_search_programmatic 不应被调用
        self.assertEqual(m_run.call_count, 0)
        # 返回失败 outcome
        self.assertFalse(outcome.ok)

    # ---- 熔断器/产物零改动 ------------------------------------------

    def test_breaker_records_success_after_list(self):
        """in_process 模式成功后熔断器 record_success 被调用（零改动路径）。"""
        source = self._make_source()
        list_path = str(self.artifact_root / "list_breaker.json")
        plan_item = {
            "keyword": "AI", "city": "上海", "source_filters": {},
            "target_pages": 1,
            "input_hash": _boss_input_hash({
                "keyword": "AI", "city": "上海",
                "source_filters": {}, "target_pages": 1,
            }),
            "list_output_path": list_path,
        }

        def fake_run(**kwargs):
            self._write_json(kwargs["output_path"], {
                "jobs": [{"encrypt_job_id": "j1", "job_link": "https://www.zhipin.com/job/1",
                          "boss_name": "C"}]
            })
            return {"list_data": {"jobs": []}, "details": None}

        with mock.patch.object(_boss_for_inprocess, "run_search_programmatic", side_effect=fake_run), \
                mock.patch.object(source.breaker, "record_success") as m_record:
            outcome = source.fetch_list(plan_item)

        self.assertTrue(outcome.ok)
        # 熔断器 record_success 被调用（零改动路径复用）
        self.assertEqual(m_record.call_count, 1)


# ===========================================================================
# T002/T003：BOSS welfare → extra.welfare_list（specs/004 tasks001）
# ===========================================================================
class NormalizeJobFieldsWelfareTests(unittest.TestCase):
    """_normalize_job_fields 将 BOSS welfare 拆分写入 extra.welfare_list。"""

    def test_welfare_string_split_into_extra_welfare_list(self):
        job = {"welfare": "五险一金 | 双休", "boss_name": "Corp"}
        normalized = _normalize_job_fields(job)
        self.assertEqual(normalized["extra"]["welfare_list"], ["五险一金", "双休"])

    def test_welfare_split_tolerates_spaces_and_empty_segments(self):
        job = {"welfare": " 五险一金|双休 | | 弹性工作 "}
        normalized = _normalize_job_fields(job)
        self.assertEqual(
            normalized["extra"]["welfare_list"],
            ["五险一金", "双休", "弹性工作"],
        )

    def test_existing_extra_dict_is_preserved_and_merged(self):
        job = {"welfare": "双休", "extra": {"industry_label": "互联网"}}
        normalized = _normalize_job_fields(job)
        self.assertEqual(normalized["extra"]["industry_label"], "互联网")
        self.assertEqual(normalized["extra"]["welfare_list"], ["双休"])

    def test_missing_or_empty_welfare_omits_welfare_list_key(self):
        for job in ({"boss_name": "Corp"}, {"welfare": "", "boss_name": "Corp"}, {"welfare": "|", "boss_name": "Corp"}):
            normalized = _normalize_job_fields(job)
            self.assertNotIn("welfare_list", normalized.get("extra") or {})

    def test_input_dict_is_not_mutated(self):
        job = {"welfare": "双休", "boss_name": "Corp"}
        _normalize_job_fields(job)
        self.assertNotIn("extra", job)


class RiskSignalClassificationTests(unittest.TestCase):
    """016：分类只认结构化失败行；退出码兜底；无冷却、无受限缓存副作用。"""

    def test_exit_10_without_failure_line_is_status_unclear(self):
        from webui.source import _classify_failed_code
        # 岗位标题/薪资里的"429/滑块/稍后再试"不再参与分类（全文扫描已删除）
        for sample in (
            "登录解锁更多职位", "频繁更新职位", "冻结岗位",
            "  ✓ 滑块交互工程师 | 429元/天 | 操作频繁系统维护",
        ):
            self.assertEqual(
                _classify_failed_code(10, sample), "source_status_unclear")

    def test_failure_line_is_the_authoritative_classifier(self):
        from webui.source import _classify_failed_code
        # 失败行存在时，输出其余内容（哪怕含敏感词）不影响定类
        self.assertEqual(
            _classify_failed_code(
                10, "✓ 滑块工程师 | 429元/天\n__CAREERSCOUT_FAILED__ code=source_rate_limited hint=操作频繁"),
            "source_rate_limited")
        self.assertEqual(
            _classify_failed_code(
                10, "__CAREERSCOUT_FAILED__ code=source_verification_required hint=验证码"),
            "source_verification_required")
        self.assertEqual(
            _classify_failed_code(
                10, "__CAREERSCOUT_FAILED__ code=source_login_required hint=401"),
            "source_login_required")
        # 多行取最后一行；别名归一
        self.assertEqual(
            _classify_failed_code(
                10, "__CAREERSCOUT_FAILED__ code=source_rate_limited hint=a\nsome tail\n__CAREERSCOUT_FAILED__ code=ip_risk_control hint=b"),
            "source_blocked")

    def test_exit_code_fallback_mapping(self):
        from webui.source import _classify_failed_code
        self.assertEqual(_classify_failed_code(2, ""), "source_cdp_unavailable")
        self.assertEqual(_classify_failed_code(3, ""), "source_invalid_output")
        self.assertEqual(
            _classify_failed_code(11, ""), "source_request_limit_exceeded")
        self.assertEqual(_classify_failed_code(10, ""), "source_status_unclear")
        self.assertEqual(
            _classify_failed_code(10, "旧脚本无失败行"), "source_status_unclear")

    def test_exit_1_loose_login_words_not_login_required(self):
        """退出码 1 只认高置信短语，单个“登录/login/cookie”字眼不再误判。"""
        from webui.source import _classify_failed_code
        for sample in (
            "登录解锁更多职位", "页面顶部有登录按钮", "需要登录后可见",
            "cookie 已设置", "login",
        ):
            self.assertEqual(_classify_failed_code(1, sample), "source_unknown_error")

    def test_exit_1_high_confidence_login_required(self):
        from webui.source import _classify_failed_code
        for sample in (
            "登录态失效, 请重新登录", "登录失效", "登录已失效", "请先登录",
            "未登录", "cookie 已失效",
        ):
            self.assertEqual(_classify_failed_code(1, sample), "source_login_required")

    def test_record_risk_signals_only_persists_login_fact(self):
        from webui.source import _record_risk_signals
        with mock.patch("scripts.login_state_cache.write_login_state") as write_state:
            # 受限/验证码/无法确认：一律不写任何持久状态（无冷却模块可写）
            for code in ("source_blocked", "source_rate_limited",
                         "source_verification_required", "source_status_unclear"):
                _record_risk_signals("acc", "boss", code, "操作频繁", "run-1")
            write_state.assert_not_called()
            # 登录失效是事实态：写 not_logged_in
            _record_risk_signals(
                "acc", "boss", "source_login_required", "401", "run-2")
            write_state.assert_called_once_with("acc", "boss", "not_logged_in")


class BossCdpSourcePreflightTests(_LoginCacheIsolated):
    """BOSS preflight：不信任 not_logged_in 缓存，unknown 重试一次后放行。"""

    def _source(self):
        return BossCdpSource(browser_account="a", cdp_port=9222)

    def _mock_cdp_ok(self):
        from scripts import boss_cdp_raw as boss
        resp = mock.Mock()
        resp.status_code = 200
        resp.json.return_value = {"Browser": "Chrome"}
        return mock.patch.object(boss.requests, "get", return_value=resp)

    def test_cached_not_logged_in_blocks_until_invalidated(self):
        # 016：未登录是事实态，命中直接提示登录；打开登录窗口会失效缓存重探
        from scripts import boss_cdp_raw as boss
        from scripts import login_state_cache as cache
        cache.write_login_state("a", "boss", "not_logged_in")
        source = self._source()
        with self._mock_cdp_ok(), \
                mock.patch.object(boss, "check_login_state_tri") as m:
            outcome = source.preflight()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_login_required")
        m.assert_not_called()

    def test_restricted_probe_never_persists_to_cache(self):
        # 016：受限只在当次生效，探测受限后缓存里不应出现任何受限态
        from scripts import boss_cdp_raw as boss
        from scripts import login_state_cache as cache
        source = self._source()
        with self._mock_cdp_ok(), \
                mock.patch.object(boss, "check_login_state_tri",
                    side_effect=["restricted", "restricted"]), \
                mock.patch("webui.source.time.sleep"):
            outcome = source.preflight()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_account_restricted")
        self.assertNotEqual(cache.read_cached_state("a", "boss"), "restricted")

    def test_unknown_probe_retries_once_then_proceeds(self):
        from scripts import boss_cdp_raw as boss
        source = self._source()
        with self._mock_cdp_ok(), \
                mock.patch.object(boss, "check_login_state_tri", return_value="unknown") as m:
            outcome = source.preflight()
        self.assertTrue(outcome.ok)
        self.assertEqual(m.call_count, 2)
        self.assertIn("probe_unknown", outcome.safe_log)

    def test_unknown_then_logged_in_succeeds(self):
        from scripts import boss_cdp_raw as boss
        source = self._source()
        with self._mock_cdp_ok(), \
                mock.patch.object(boss, "check_login_state_tri",
                    side_effect=["unknown", "logged_in"]) as m:
            outcome = source.preflight()
        self.assertTrue(outcome.ok)
        self.assertEqual(m.call_count, 2)

    def test_restricted_then_logged_in_retries_after_delay(self):
        from scripts import boss_cdp_raw as boss
        from scripts import login_state_cache as cache
        source = self._source()
        with self._mock_cdp_ok(), \
                mock.patch.object(boss, "check_login_state_tri",
                    side_effect=["restricted", "logged_in"]) as m, \
                mock.patch("webui.source.time.sleep") as sleep:
            outcome = source.preflight()
        self.assertTrue(outcome.ok)
        self.assertEqual(m.call_count, 2)
        sleep.assert_called_once_with(PREFLIGHT_RETRY_DELAY_SECONDS)
        self.assertEqual(cache.read_cached_state("a", "boss"), "logged_in")

    def test_restricted_twice_returns_account_restricted(self):
        from scripts import boss_cdp_raw as boss
        source = self._source()
        with self._mock_cdp_ok(), \
                mock.patch.object(boss, "check_login_state_tri",
                    side_effect=["restricted", "restricted"]) as m, \
                mock.patch("webui.source.time.sleep") as sleep:
            outcome = source.preflight()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_account_restricted")
        self.assertEqual(m.call_count, 2)
        sleep.assert_called_once_with(PREFLIGHT_RETRY_DELAY_SECONDS)
        self.assertIn("retry=1", outcome.safe_log)

    def test_real_not_logged_in_fails_login_required(self):
        from scripts import boss_cdp_raw as boss
        source = self._source()
        with self._mock_cdp_ok(), \
                mock.patch.object(boss, "check_login_state_tri", return_value="not_logged_in"):
            outcome = source.preflight()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_login_required")


if __name__ == "__main__":
    unittest.main()
