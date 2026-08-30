"""tasks001 T005 — JobSource Protocol / SourceOutcome / FakeJobSource 合同测试。

验证 ``webui/source.py`` 符合 ``contracts/job-source.md`` 合同：
- JobSource Protocol 结构化校验（platform + preflight + fetch_*）；
- SourceOutcome 普通/空/失败三态与 empty_evidence 校验；
- FakeJobSource 携带 platform、显式 cdp_port、preflight 和 batch；
- BossCdpSource 携带 platform 和显式 cdp_port；
- SAFE_FAILURE_CODES 覆盖错误矩阵全部稳定码。
"""
import json
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

import json as _json_for_inprocess
from scripts import boss_cdp_raw as _boss_for_inprocess
from scripts.boss import constants as boss_constants
from scripts.boss import login, search
from webui.source import _input_hash as _boss_input_hash

from tests.source.harness import _LoginCacheIsolated


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
# spec003 tasks004 T026 — BossCdpSource in_process argv 翻译
#
# 冻结合同：specs/003-desktop-exe/contracts/inprocess-runner.md §4.3。
#
# in_process=True 时，_runner 把本类构建的三类命令（list-only / detail-only /
# detail-batch）翻译为 programmatic 库式调用（run_search_programmatic /
# scrape_details），不经过 argv 文本往返；其余行为（SourceOutcome、事件校验、
# 熔断器、输入 hash、产物读取）零改动。无法翻译的命令返回失败 outcome。
# ===========================================================================


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

    def test_nonzero_exit_rescues_partial_details(self):
        """returncode!=0（子进程被强杀/异常退出）时抢救已落盘产物，缺失的才标失败。

        子进程边抓边原子写盘，被杀时 output 文件保存着已完成岗位的 JD；
        已抓到的标成功、缺失的标失败，不整批丢弃（不重抓已抓到的部分）。
        """
        source = self._make_source()
        detail_path = str(self.artifact_root / "batch_rescue.json")
        jobs = [
            {"job_id": "j1", "source_url": "https://www.zhipin.com/job/1",
             "job_link": "https://www.zhipin.com/job/1"},
            {"job_id": "j2", "source_url": "https://www.zhipin.com/job/2",
             "job_link": "https://www.zhipin.com/job/2"},
        ]
        # 子进程被强杀时 output 文件已保存 j1 的 JD，j2 尚未完成
        self._write_json(detail_path, [
            {"job_id": "j1", "jd": "已抓到的JD内容",
             "job_link": "https://www.zhipin.com/job/1"},
        ])
        with mock.patch.object(source, "_run_command",
                               return_value=(1, "模拟异常退出（被强杀）")):
            results = source.fetch_details_batch(
                jobs, detail_output_path=detail_path)

        self.assertEqual(len(results), 2)
        # j1 已落盘 → 抢救成功，jd 原样返回
        self.assertTrue(results["j1"].ok, results["j1"].safe_log)
        self.assertEqual(results["j1"].detail.get("jd"), "已抓到的JD内容")
        self.assertIn("rescued_partial", results["j1"].safe_log)
        # j2 缺失 → 按退出码分类失败，仅重抓缺失部分
        self.assertFalse(results["j2"].ok)
        self.assertEqual(results["j2"].failed_code, "source_unknown_error")
        self.assertIn("returncode=1", results["j2"].safe_log)

    def test_nonzero_exit_no_partial_keeps_whole_batch_failed(self):
        """returncode!=0 且产物文件无任何记录：整批按退出码分类失败（原语义）。"""
        source = self._make_source()
        detail_path = str(self.artifact_root / "batch_rescue_empty.json")
        jobs = [
            {"job_id": "j1", "source_url": "https://www.zhipin.com/job/1",
             "job_link": "https://www.zhipin.com/job/1"},
        ]
        with mock.patch.object(source, "_run_command",
                               return_value=(1, "模拟异常退出（被强杀）")):
            results = source.fetch_details_batch(
                jobs, detail_output_path=detail_path)

        self.assertFalse(results["j1"].ok)
        self.assertEqual(results["j1"].failed_code, "source_unknown_error")
        self.assertNotIn("rescued_partial", results["j1"].safe_log)

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
        with mock.patch.object(boss_constants, "MAX_API_REQUESTS", 3):
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

    def test_connection_error_maps_to_returncode_2(self):
        """运行中 WebSocket 断开（内置 ConnectionError）→ (2, ...) → source_cdp_unavailable。

        026：CDPSession.send 把 WebSocketException 转成内置 ConnectionError，
        必须与连接失败同语义（浏览器失联），而非落入通用 Exception 分支
        被分类成 source_unknown_error 静默标待确认。
        """
        source = self._make_source()
        list_path = str(self.artifact_root / "list_cdp_conn.json")
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
                               side_effect=ConnectionError("CDP 连接异常")):
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
        with mock.patch.object(boss_constants, "MAX_API_REQUESTS", 3):
            output = self.artifact_root / "prog_run.json"

            def fake_list(*_args, **_kwargs):
                for _ in range(3):
                    _boss_for_inprocess.incr_request()
                return {"jobs": [], "total": 0}

            with mock.patch.object(
                search, "scrape_list", side_effect=fake_list,
            ), mock.patch.object(
                login, "check_login_state", return_value=True,
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


# ===========================================================================
# 020 US1：熔断器开闸失败码透传 last_signal + 冷却期满可复位
# ===========================================================================


class BreakerOpenFailureCodeTests(unittest.TestCase):
    """open_failure_code() 透传开闸信号；缺失时回落 source_blocked。"""

    def test_open_failure_code_transmits_login_signal(self):
        breaker = SourceCircuitBreaker()
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_login_required")
        self.assertTrue(breaker.is_open())
        self.assertEqual(breaker.open_failure_code(), "source_login_required")

    def test_open_failure_code_transmits_risk_signals(self):
        for signal in (
            "source_verification_required",
            "source_rate_limited",
            "source_blocked",
        ):
            breaker = SourceCircuitBreaker()
            breaker.record_signal(signal)
            breaker.record_signal(signal)
            self.assertTrue(breaker.is_open())
            self.assertEqual(breaker.open_failure_code(), signal)

    def test_open_failure_code_falls_back_when_signal_missing(self):
        breaker = SourceCircuitBreaker()
        breaker.record_signal("source_rate_limited")
        breaker.record_signal("source_rate_limited")
        # 防御路径：开闸但 last_signal 缺失（理论不可达）
        breaker._last_signal = None
        self.assertEqual(breaker.open_failure_code(), "source_blocked")

    def test_cooldown_elapsed_reflects_time_progress(self):
        times = [0.0]
        breaker = SourceCircuitBreaker(
            cooldown_seconds=60, clock=lambda: times[0],
        )
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_login_required")
        self.assertFalse(breaker.cooldown_elapsed())
        times[0] = 60.0
        self.assertTrue(breaker.cooldown_elapsed())


class BossBreakerFaithfulnessTests(unittest.TestCase):
    """020 US1：Boss fetch_list 开闸失败码透传 + 冷却期满复位接线。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.artifact_root = self.root / "artifacts"
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp.cleanup()

    def _breaker(self):
        self._times = [0.0]
        return SourceCircuitBreaker(
            cooldown_seconds=60, clock=lambda: self._times[0],
        )

    def _source(self, breaker):
        return BossCdpSource(
            artifact_root=self.artifact_root,
            cdp_port=9222,
            in_process=True,
            breaker=breaker,
        )

    def _plan_item(self, name):
        list_path = str(self.artifact_root / f"list_{name}.json")
        payload = {
            "keyword": "AI", "city": "上海", "source_filters": {},
            "target_pages": 1,
        }
        return {
            **payload,
            "input_hash": _boss_input_hash(payload),
            "list_output_path": list_path,
        }

    def test_open_gate_transmits_login_signal(self):
        breaker = self._breaker()
        source = self._source(breaker)
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_login_required")
        with mock.patch.object(
            source, "preflight",
            return_value=SourceOutcome.success(safe_log="preflight_ok"),
        ) as m_preflight, mock.patch.object(source, "_run_command") as m_run:
            outcome = source.fetch_list(self._plan_item("login_open"))
        # 冷却未满：不做探测、不发起抓取，失败码如实透传登录信号
        m_preflight.assert_not_called()
        m_run.assert_not_called()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_login_required")

    def test_open_gate_transmits_risk_signals(self):
        for signal in ("source_verification_required", "source_rate_limited"):
            breaker = self._breaker()
            source = self._source(breaker)
            breaker.record_signal(signal)
            breaker.record_signal(signal)
            with mock.patch.object(source, "preflight") as m_preflight, \
                    mock.patch.object(source, "_run_command") as m_run:
                outcome = source.fetch_list(self._plan_item(f"risk_{signal}"))
            m_preflight.assert_not_called()
            m_run.assert_not_called()
            self.assertFalse(outcome.ok)
            self.assertEqual(outcome.failed_code, signal)

    def test_cooldown_elapsed_preflight_success_resets_and_proceeds(self):
        breaker = self._breaker()
        source = self._source(breaker)
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_login_required")
        self._times[0] = 120.0  # 冷却期满

        plan = self._plan_item("reset_ok")
        output_path = plan["list_output_path"]

        def fake_run(command, timeout, **kwargs):
            pathlib.Path(output_path).write_text(
                json.dumps({"jobs": [{"job_id": "j1",
                                      "source_url": "https://www.zhipin.com/job/1"}]},
                           ensure_ascii=False),
                encoding="utf-8",
            )
            return 0, ""

        with mock.patch.object(
            source, "preflight",
            return_value=SourceOutcome.success(safe_log="preflight_ok"),
        ) as m_preflight, mock.patch.object(
            source, "_run_command", side_effect=fake_run,
        ) as m_run:
            outcome = source.fetch_list(plan)

        m_preflight.assert_called_once()
        m_run.assert_called_once()
        self.assertFalse(breaker.is_open(), "preflight 通过且冷却期满必须复位熔断器")
        self.assertTrue(outcome.ok)

    def test_cooldown_elapsed_preflight_failure_keeps_breaker_open(self):
        breaker = self._breaker()
        source = self._source(breaker)
        breaker.record_signal("source_login_required")
        breaker.record_signal("source_login_required")
        self._times[0] = 120.0
        with mock.patch.object(
            source, "preflight",
            return_value=SourceOutcome.failure(
                failed_code="source_login_required", safe_log="preflight_fail"),
        ) as m_preflight, mock.patch.object(source, "_run_command") as m_run:
            outcome = source.fetch_list(self._plan_item("reset_fail"))
        m_preflight.assert_called_once()
        m_run.assert_not_called()
        self.assertTrue(breaker.is_open(), "preflight 失败不得复位")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_code, "source_login_required")

    def test_preflight_exception_treated_as_failure_no_reset(self):
        breaker = self._breaker()
        source = self._source(breaker)
        breaker.record_signal("source_rate_limited")
        breaker.record_signal("source_rate_limited")
        self._times[0] = 120.0
        with mock.patch.object(
            source, "preflight", side_effect=RuntimeError("boom"),
        ), mock.patch.object(source, "_run_command") as m_run:
            outcome = source.fetch_list(self._plan_item("reset_exc"))
        m_run.assert_not_called()
        self.assertTrue(breaker.is_open())
        self.assertEqual(outcome.failed_code, "source_rate_limited")


if __name__ == "__main__":
    unittest.main()
