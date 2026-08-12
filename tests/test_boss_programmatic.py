"""spec003 tasks001 — BOSS programmatic 执行入口测试。

覆盖 contract inprocess-runner.md §5 要求：
- T002 参数等价：programmatic 与 CLI 同输入产生同产物（不依赖真实 Chrome/CDP）
- T003 日志转发：on_log 收到与 print 一致的行序列
- T003a 轮询回调：on_poll 在列表逐页 + 详情逐岗位检查点被调用；None 零影响
- T004 取消：cancel_event 置位后快速停止、已写产物保留
- T005 异常映射：CDPUnavailable / RiskControl / LoginRequired / SearchCancelled

入口 run_search_programmatic 与异常类 SearchCancelled / LoginRequiredError
尚未实现，本文件全部测试预期 RED，直到 scripts/boss_cdp_raw.py 实现阶段完成。
"""

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "boss_cdp_raw.py"


def load_module():
    """加载 boss_cdp_raw 模块，mock 掉 websocket/requests 两个可选依赖。"""
    sys.modules.setdefault("websocket", mock.Mock())
    sys.modules.setdefault("requests", mock.Mock())
    spec = importlib.util.spec_from_file_location("boss_cdp_raw_programmatic", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ============================================================
# Fake CDP session — 服务 scrape_list / scrape_details 检查点测试
# ============================================================

class _FakeListCDPSession:
    """scrape_list 专用替身：eval_js 返回构造的 jobs JSON，send 返回最小响应。

    - eval_js(api_js) → 返回 self.jobs_payload（JSON 字符串）
    - eval_js(scrollBy) → None
    - send(已知 method) → {"result": {...}} / {"result": {}}
    """

    def __init__(self, jobs_payload):
        self._jobs_payload = jobs_payload
        self._mid = 0
        self.closed = False
        self.call_log = []

    def send(self, method, params=None, sid=None, timeout=30):
        params = params or {}
        self._mid += 1
        self.call_log.append({"method": method, "params": params})
        if method == "Target.createTarget":
            return {"result": {"targetId": f"target-{self._mid}"}}
        if method == "Target.attachToTarget":
            return {"result": {"sessionId": f"session-{self._mid}"}}
        return {"result": {}}

    def eval_js(self, js, sid):
        self.call_log.append({"method": "Runtime.evaluate", "params": {"expression": js}})
        if "scrollBy" in js:
            return None
        return self._jobs_payload

    def close(self):
        self.closed = True


class _FakeDetailCDPSession:
    """scrape_details 专用替身，参考 test_chrome_setup._FakeScrapeDetailsCDPSession。"""

    def __init__(self, *, detail_payload=None):
        self._mid = 0
        self._detail_payload = detail_payload if detail_payload is not None else {
            "jd": "岗位描述 " + ("后端服务开发参与系统架构设计。 " * 12),
            "tags": ["Python"],
        }
        self.closed = False
        self.call_log = []

    def send(self, method, params=None, sid=None, timeout=30):
        params = params or {}
        self._mid += 1
        self.call_log.append({"method": method, "params": params})
        if method == "Target.createTarget":
            return {"result": {"targetId": f"target-{self._mid}"}}
        if method == "Target.attachToTarget":
            return {"result": {"sessionId": f"session-{self._mid}"}}
        return {"result": {}}

    def eval_js(self, js, sid):
        self.call_log.append({"method": "Runtime.evaluate", "params": {"expression": js}})
        if "__boss_readiness_probe__" in js or "document.readyState" in js:
            return "ready"
        return json.dumps(self._detail_payload)

    def close(self):
        self.closed = True


def _list_payload(jobs):
    """构造 scrape_list eval_js 返回的新格式 payload。"""
    return json.dumps({"jobs": jobs, "hasMore": False, "totalCount": len(jobs)})


def _make_jobs(n=2):
    return [
        {
            "title": f"Job-{i}",
            "job_link": f"https://www.zhipin.com/job_detail/enc{i}.html",
            "salary": "20-30K",
            "location": "上海",
            "boss_name": f"Company-{i}",
        }
        for i in range(n)
    ]


def _no_sleep(seconds, label=None):
    """测试用 sleeper：不等待。"""
    return None


# ============================================================
# T002 参数等价
# ============================================================

class ParamEquivalenceTests(unittest.TestCase):
    """programmatic 入口与 CLI main() 在相同输入下产生相同产物编排。"""

    def setUp(self):
        self.module = load_module()

    def _patch_pipeline(self, *, list_data=None, details=None, login=True):
        """mock 掉 check_login_state / scrape_list / scrape_details，返回记录的 Mock。"""
        list_data = list_data if list_data is not None else {"jobs": _make_jobs(2)}
        details = details if details is not None else []
        patches = [
            mock.patch.object(self.module, "check_login_state", return_value=login),
            mock.patch.object(
                self.module, "scrape_list",
                mock.Mock(return_value=list_data, __name__="scrape_list"),
            ),
            mock.patch.object(
                self.module, "scrape_details",
                mock.Mock(return_value=details, __name__="scrape_details"),
            ),
            mock.patch.object(self.module, "require_runtime_dependencies", return_value=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return list_data, details

    def test_returns_list_data_and_details_dict(self):
        """返回结构必须是 {"list_data": dict, "details": list|None}。"""
        list_data, details = self._patch_pipeline()
        result = self.module.run_search_programmatic(
            keyword="Java", city="上海", pages=2,
        )
        self.assertIsInstance(result, dict)
        self.assertEqual(result["list_data"], list_data)
        self.assertEqual(result["details"], details)

    def test_passes_keyword_city_pages_filters_to_scrape_list(self):
        """keyword/city/pages/filters/start_page/output_path 直传 scrape_list。"""
        self._patch_pipeline()
        self.module.run_search_programmatic(
            keyword="AI Agent", city="101020100", pages=4,
            filters={"scale": "305"}, output_path="/tmp/list.json", start_page=2,
        )
        args, kwargs = self.module.scrape_list.call_args
        # scrape_list(keyword, city_input, max_pages, filters, output_path, ...)
        self.assertEqual(args[0], "AI Agent")
        self.assertEqual(args[1], "101020100")
        self.assertEqual(args[2], 4)
        self.assertEqual(args[3], {"scale": "305"})
        self.assertEqual(args[4], "/tmp/list.json")
        self.assertEqual(kwargs.get("start_page"), 2)

    def test_passes_cdp_port_to_scrape_list_and_login_check(self):
        """cdp_port 同时传给 check_login_state 与 scrape_list。"""
        self._patch_pipeline()
        self.module.run_search_programmatic(
            keyword="Java", city="上海", pages=1, cdp_port=9333,
        )
        # check_login_state 用位置参数（与 main 一致）；scrape_list 用 kwargs
        self.assertEqual(self.module.check_login_state.call_args.args[0], 9333)
        self.assertEqual(self.module.scrape_list.call_args.kwargs.get("cdp_port"), 9333)

    def test_passes_detail_and_max_details_to_scrape_details(self):
        """detail=True 时 scrape_details 收到 max_details / detail_output_path。"""
        self._patch_pipeline()
        self.module.run_search_programmatic(
            keyword="Java", city="上海", pages=1,
            detail=True, max_details=5, detail_output_path="/tmp/det.json",
        )
        args, kwargs = self.module.scrape_details.call_args
        self.assertEqual(args[0], self.module.scrape_list.return_value)
        self.assertEqual(args[1], 5)
        self.assertEqual(args[2], "/tmp/det.json")

    def test_detail_false_skips_scrape_details(self):
        """detail=False 时不调用 scrape_details，details 返回 None。"""
        self._patch_pipeline()
        result = self.module.run_search_programmatic(
            keyword="Java", city="上海", pages=1, detail=False,
        )
        self.module.scrape_details.assert_not_called()
        self.assertIsNone(result["details"])

    def test_scrape_details_skipped_when_no_jobs(self):
        """list_data.jobs 为空时即使 detail=True 也不调 scrape_details。"""
        self._patch_pipeline(list_data={"jobs": []})
        result = self.module.run_search_programmatic(
            keyword="Java", city="上海", pages=1, detail=True,
        )
        self.module.scrape_details.assert_not_called()
        self.assertIsNone(result["details"])

    def test_parallel_params_forwarded_to_scrape_details(self):
        """enable_parallel / tab_pool_size / stagger_range / gap / reset_every 透传。"""
        self._patch_pipeline()
        self.module.run_search_programmatic(
            keyword="Java", city="上海", pages=1,
            enable_parallel=True, tab_pool_size=3,
            stagger_range=(2.0, 4.0), inter_job_gap_range=(5.0, 8.0), reset_every=2,
        )
        kwargs = self.module.scrape_details.call_args.kwargs
        self.assertTrue(kwargs.get("enable_parallel"))
        self.assertEqual(kwargs.get("tab_pool_size"), 3)
        self.assertEqual(kwargs.get("stagger_range"), (2.0, 4.0))
        self.assertEqual(kwargs.get("inter_job_gap_range"), (5.0, 8.0))
        self.assertEqual(kwargs.get("reset_every"), 2)

    def test_events_output_wires_event_callback(self):
        """events_output 提供时，scrape_details 收到非 None 的 event_callback。"""
        self._patch_pipeline()
        with tempfile.TemporaryDirectory() as tmp:
            events_path = str(pathlib.Path(tmp) / "events.jsonl")
            self.module.run_search_programmatic(
                keyword="Java", city="上海", pages=1, events_output=events_path,
            )
            kwargs = self.module.scrape_details.call_args.kwargs
            self.assertIsNotNone(kwargs.get("event_callback"))

    def test_analysis_flag_invokes_analyze(self):
        """analysis=True 时调用 analyze，并传入 list_data/details/keyword。"""
        list_data, details = self._patch_pipeline(details=[{"job_id": "x"}])
        with mock.patch.object(self.module, "analyze", mock.Mock()) as m_analyze:
            self.module.run_search_programmatic(
                keyword="Java", city="上海", pages=1, analysis=True,
            )
            m_analyze.assert_called_once()
            args, kwargs = m_analyze.call_args
            self.assertEqual(args[0], list_data)
            self.assertEqual(args[1], details)
            self.assertEqual(kwargs.get("search_keyword"), "Java")


# ============================================================
# T003 日志转发
# ============================================================

class LogForwardingTests(unittest.TestCase):
    """on_log 收到与 print 一致的行序列；None 时回退 stdout。"""

    def setUp(self):
        self.module = load_module()

    def test_on_log_receives_print_lines(self):
        """scrape_list 内部的 print 经 redirect_stdout 转发到 on_log。"""
        received = []

        def fake_scrape_list(*args, **kwargs):
            print("line-1")
            print("line-2")
            return {"jobs": _make_jobs(1)}

        with mock.patch.object(self.module, "check_login_state", return_value=True), \
                mock.patch.object(self.module, "scrape_list", side_effect=fake_scrape_list), \
                mock.patch.object(self.module, "scrape_details", return_value=[]), \
                mock.patch.object(self.module, "require_runtime_dependencies", return_value=True):
            self.module.run_search_programmatic(
                keyword="Java", city="上海", pages=1,
                on_log=received.append,
            )
        self.assertIn("line-1", received)
        self.assertIn("line-2", received)

    def test_on_log_none_does_not_raise(self):
        """on_log=None 时 print 走原 stdout，不报错。"""
        with mock.patch.object(self.module, "check_login_state", return_value=True), \
                mock.patch.object(
                    self.module, "scrape_list",
                    mock.Mock(return_value={"jobs": []}),
                ), \
                mock.patch.object(self.module, "require_runtime_dependencies", return_value=True):
            # 用真实 stdout 跑一次，不应抛异常
            result = self.module.run_search_programmatic(
                keyword="Java", city="上海", pages=1, detail=False,
            )
            self.assertIsNone(result["details"])

    def test_risk_control_report_forwarded_via_on_log(self):
        """RiskControlError 抛出前，print_risk_control_report 文本经 on_log 转发。"""
        received = []
        err = self.module.RiskControlError(
            "captcha", page=2, scraped_count=10, output_path="/tmp/x.json",
        )

        def fake_scrape_list(*args, **kwargs):
            raise err

        with mock.patch.object(self.module, "check_login_state", return_value=True), \
                mock.patch.object(self.module, "scrape_list", side_effect=fake_scrape_list), \
                mock.patch.object(self.module, "require_runtime_dependencies", return_value=True):
            with self.assertRaises(self.module.RiskControlError):
                self.module.run_search_programmatic(
                    keyword="Java", city="上海", pages=3,
                    on_log=received.append,
                )
        # report 里的醒目文案应进 on_log
        joined = "\n".join(received)
        self.assertIn("抓取已被风控拦截", joined)


# ============================================================
# T003a 轮询回调
# ============================================================

class PollCallbackTests(unittest.TestCase):
    """on_poll 透传给 scrape_list / scrape_details；None 时零影响。"""

    def setUp(self):
        self.module = load_module()

    def test_on_poll_forwarded_to_scrape_list_and_scrape_details(self):
        """on_poll 作为 kwarg 传给 scrape_list 与 scrape_details。"""
        on_poll = mock.Mock()
        with mock.patch.object(self.module, "check_login_state", return_value=True), \
                mock.patch.object(
                    self.module, "scrape_list",
                    mock.Mock(return_value={"jobs": _make_jobs(2)}),
                ), \
                mock.patch.object(self.module, "scrape_details", return_value=[]), \
                mock.patch.object(self.module, "require_runtime_dependencies", return_value=True):
            self.module.run_search_programmatic(
                keyword="Java", city="上海", pages=1, on_poll=on_poll,
            )
            self.assertEqual(self.module.scrape_list.call_args.kwargs.get("on_poll"), on_poll)
            self.assertEqual(self.module.scrape_details.call_args.kwargs.get("on_poll"), on_poll)

    def test_on_poll_none_does_not_raise(self):
        """on_poll=None 时编排正常完成。"""
        with mock.patch.object(self.module, "check_login_state", return_value=True), \
                mock.patch.object(
                    self.module, "scrape_list",
                    mock.Mock(return_value={"jobs": []}),
                ), \
                mock.patch.object(self.module, "require_runtime_dependencies", return_value=True):
            self.module.run_search_programmatic(
                keyword="Java", city="上海", pages=1, detail=False, on_poll=None,
            )


# ============================================================
# T004 取消
# ============================================================

class CancelEventForwardingTests(unittest.TestCase):
    """cancel_event 透传给 scrape_list / scrape_details。"""

    def setUp(self):
        self.module = load_module()

    def test_cancel_event_forwarded_to_scrape_list(self):
        cancel = threading.Event()
        with mock.patch.object(self.module, "check_login_state", return_value=True), \
                mock.patch.object(
                    self.module, "scrape_list",
                    mock.Mock(return_value={"jobs": []}),
                ), \
                mock.patch.object(self.module, "require_runtime_dependencies", return_value=True):
            self.module.run_search_programmatic(
                keyword="Java", city="上海", pages=1, detail=False, cancel_event=cancel,
            )
            self.assertEqual(self.module.scrape_list.call_args.kwargs.get("cancel_event"), cancel)

    def test_cancel_event_forwarded_to_scrape_details(self):
        cancel = threading.Event()
        with mock.patch.object(self.module, "check_login_state", return_value=True), \
                mock.patch.object(
                    self.module, "scrape_list",
                    mock.Mock(return_value={"jobs": _make_jobs(2)}),
                ), \
                mock.patch.object(self.module, "scrape_details", return_value=[]), \
                mock.patch.object(self.module, "require_runtime_dependencies", return_value=True):
            self.module.run_search_programmatic(
                keyword="Java", city="上海", pages=1, cancel_event=cancel,
            )
            self.assertEqual(self.module.scrape_details.call_args.kwargs.get("cancel_event"), cancel)


class ScrapeListCancelCheckpointTests(unittest.TestCase):
    """scrape_list 真实循环 + fake CDP：cancel 置位后抛 SearchCancelled、产物保留。"""

    def setUp(self):
        self.module = load_module()

    def _patch_deps(self):
        """patch scrape_list 的外部依赖，让循环能在 fake CDP 上跑起来。"""
        patches = [
            mock.patch.object(self.module, "resolve_city", return_value=("上海", "101020100")),
            mock.patch.object(self.module, "incr_request"),
            mock.patch.object(self.module.time, "sleep", _no_sleep),
            mock.patch.object(self.module.random, "uniform", lambda a, b: 0.0),
            mock.patch.object(self.module.random, "randint", lambda a, b: 1),
            mock.patch.object(self.module.random, "random", lambda: 0.0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_scrape_list_cancel_raises_search_cancelled(self):
        """cancel_event 置位后 scrape_list 抛 SearchCancelled。"""
        self._patch_deps()
        jobs_payload = _list_payload(_make_jobs(2))
        fake_session = _FakeListCDPSession(jobs_payload)
        cancel = threading.Event()
        poll_calls = []

        def on_poll():
            poll_calls.append(1)
            # 第 2 次轮询时置位 cancel（第 1 页已跑完并 flush）
            if len(poll_calls) >= 2:
                cancel.set()

        with mock.patch.object(self.module, "CDPSession", lambda cdp_port=None: fake_session):
            with tempfile.TemporaryDirectory() as tmp:
                output = str(pathlib.Path(tmp) / "list.json")
                with self.assertRaises(self.module.SearchCancelled):
                    self.module.scrape_list(
                        "Java", "上海", 3, {}, output,
                        cancel_event=cancel, on_poll=on_poll,
                    )
                # 已写产物保留（第 1 页 jobs）
                self.assertTrue(os.path.exists(output))
                with open(output, encoding="utf-8") as f:
                    data = json.load(f)
                self.assertGreaterEqual(len(data.get("jobs", [])), 1)

    def test_scrape_list_cancel_none_is_no_op(self):
        """cancel_event=None / on_poll=None 时 scrape_list 正常跑完，不抛 SearchCancelled。"""
        self._patch_deps()
        jobs_payload = _list_payload(_make_jobs(2))
        fake_session = _FakeListCDPSession(jobs_payload)

        with mock.patch.object(self.module, "CDPSession", lambda cdp_port=None: fake_session):
            with tempfile.TemporaryDirectory() as tmp:
                output = str(pathlib.Path(tmp) / "list.json")
                # 不应抛 SearchCancelled
                result = self.module.scrape_list(
                    "Java", "上海", 1, {}, output,
                    cancel_event=None, on_poll=None,
                )
                self.assertEqual(len(result["jobs"]), 2)


class ScrapeDetailsCancelCheckpointTests(unittest.TestCase):
    """scrape_details 真实循环 + fake CDP：cancel 置位后抛 SearchCancelled。"""

    def setUp(self):
        self.module = load_module()

    def test_scrape_details_cancel_raises_search_cancelled(self):
        """cancel_event 置位后 scrape_details 抛 SearchCancelled。"""
        jobs = _make_jobs(3)
        list_data = {"jobs": jobs}
        cancel = threading.Event()
        poll_calls = []

        def on_poll():
            poll_calls.append(1)
            if len(poll_calls) >= 1:
                cancel.set()

        sleeper, _ = _make_recording_sleeper()
        with tempfile.TemporaryDirectory() as tmp:
            output = str(pathlib.Path(tmp) / "details.json")
            with self.assertRaises(self.module.SearchCancelled):
                self.module.scrape_details(
                    list_data, output_path=output,
                    session_factory=lambda cdp_port=None: _FakeDetailCDPSession(),
                    sleeper=sleeper,
                    cancel_event=cancel, on_poll=on_poll,
                )


def _make_recording_sleeper():
    calls = []

    def sleeper(seconds, label=None):
        calls.append((float(seconds), label))

    return sleeper, calls


# ============================================================
# T005 异常映射
# ============================================================

class ExceptionMappingTests(unittest.TestCase):
    """CDPUnavailable / RiskControl / LoginRequired / SearchCancelled 映射。"""

    def setUp(self):
        self.module = load_module()

    def test_login_required_error_class_exists(self):
        """LoginRequiredError 必须作为模块级异常存在。"""
        self.assertTrue(hasattr(self.module, "LoginRequiredError"))
        self.assertTrue(issubclass(self.module.LoginRequiredError, Exception))

    def test_search_cancelled_class_exists(self):
        """SearchCancelled 必须作为模块级异常存在。"""
        self.assertTrue(hasattr(self.module, "SearchCancelled"))
        self.assertTrue(issubclass(self.module.SearchCancelled, Exception))

    def test_login_required_raised_when_check_login_state_false(self):
        """check_login_state 返回 False 时抛 LoginRequiredError。"""
        with mock.patch.object(self.module, "check_login_state", return_value=False), \
                mock.patch.object(self.module, "require_runtime_dependencies", return_value=True):
            with self.assertRaises(self.module.LoginRequiredError):
                self.module.run_search_programmatic(
                    keyword="Java", city="上海", pages=1,
                )

    def test_skip_login_check_skips_login_probe(self):
        """skip_login_check=True 时不调用 check_login_state，直接进 scrape_list。"""
        with mock.patch.object(self.module, "check_login_state") as m_check, \
                mock.patch.object(self.module, "scrape_list", return_value={"jobs": []}) as m_list, \
                mock.patch.object(self.module, "require_runtime_dependencies", return_value=True):
            self.module.run_search_programmatic(
                keyword="Java", city="上海", pages=1, skip_login_check=True,
            )
        m_check.assert_not_called()
        m_list.assert_called_once()

    def test_cdp_unavailable_propagated(self):
        """scrape_list 抛 CDPUnavailableError 时原样传播。"""
        err = self.module.CDPUnavailableError("cdp down")

        def boom(*args, **kwargs):
            raise err

        with mock.patch.object(self.module, "check_login_state", return_value=True), \
                mock.patch.object(self.module, "scrape_list", side_effect=boom), \
                mock.patch.object(self.module, "require_runtime_dependencies", return_value=True):
            with self.assertRaises(self.module.CDPUnavailableError) as ctx:
                self.module.run_search_programmatic(
                    keyword="Java", city="上海", pages=1,
                )
            self.assertIs(ctx.exception, err)

    def test_risk_control_propagated_with_attrs(self):
        """RiskControlError 原样抛出，携带 reason/page/scraped_count/output_path。"""
        err = self.module.RiskControlError(
            "captcha", page=3, scraped_count=42, output_path="/tmp/x.json",
        )

        def boom(*args, **kwargs):
            raise err

        with mock.patch.object(self.module, "check_login_state", return_value=True), \
                mock.patch.object(self.module, "scrape_list", side_effect=boom), \
                mock.patch.object(self.module, "require_runtime_dependencies", return_value=True):
            with self.assertRaises(self.module.RiskControlError) as ctx:
                self.module.run_search_programmatic(
                    keyword="Java", city="上海", pages=3,
                )
            self.assertEqual(ctx.exception.reason, "captcha")
            self.assertEqual(ctx.exception.page, 3)
            self.assertEqual(ctx.exception.scraped_count, 42)
            self.assertEqual(ctx.exception.output_path, "/tmp/x.json")

    def test_search_cancelled_propagated(self):
        """scrape_list 抛 SearchCancelled 时原样传播（调用方映射为中断）。"""
        err = self.module.SearchCancelled("user cancelled")

        def boom(*args, **kwargs):
            raise err

        with mock.patch.object(self.module, "check_login_state", return_value=True), \
                mock.patch.object(self.module, "scrape_list", side_effect=boom), \
                mock.patch.object(self.module, "require_runtime_dependencies", return_value=True):
            with self.assertRaises(self.module.SearchCancelled) as ctx:
                self.module.run_search_programmatic(
                    keyword="Java", city="上海", pages=1,
                )
            self.assertIs(ctx.exception, err)


class EmptyPageNotRiskControlTests(unittest.TestCase):
    """API 正常应答但无职位（真实空结果）不得误判风控。

    回归背景：连续空页曾被一律当成“IP 级风控”，把没被封的账号
    误报成限流/风控阻断，还连带写入 4 小时风控冷却。
    """

    def setUp(self):
        self.module = load_module()

    def _patch_deps(self):
        patches = [
            mock.patch.object(self.module, "resolve_city", return_value=("上海", "101020100")),
            mock.patch.object(self.module, "incr_request"),
            mock.patch.object(self.module.time, "sleep", _no_sleep),
            mock.patch.object(self.module.random, "uniform", lambda a, b: 0.0),
            mock.patch.object(self.module.random, "randint", lambda a, b: 1),
            mock.patch.object(self.module.random, "random", lambda: 0.0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _run_scrape_list(self, payload):
        self._patch_deps()
        session = _FakeListCDPSession(payload)
        with tempfile.TemporaryDirectory() as tmp:
            output = str(pathlib.Path(tmp) / "list.json")
            with mock.patch.object(self.module, "CDPSession",
                                   lambda cdp_port=None: session):
                result = self.module.scrape_list("Java", "上海", 3, {}, output)
        return result, session

    def test_explicit_empty_meta_returns_empty_result_not_risk(self):
        """API 正常返回 totalCount=0/hasMore=false → 空结果，不报风控。"""
        payload = json.dumps({"jobs": [], "hasMore": False, "totalCount": 0})
        result, session = self._run_scrape_list(payload)
        self.assertEqual(result["jobs"], [])
        # 第一页就确认无职位，不应继续翻页撞阈值
        api_calls = [
            c for c in session.call_log
            if c["method"] == "Runtime.evaluate"
            and "joblist" in c["params"].get("expression", "")
        ]
        self.assertEqual(len(api_calls), 1)

    def test_legacy_empty_list_pages_are_not_risk(self):
        """旧格式空列表连续 3 页（API 均正常应答）→ 空结果，不抛风控。"""
        result, session = self._run_scrape_list(json.dumps([]))
        self.assertEqual(result["jobs"], [])

    def test_suspicious_empty_pages_still_raise_risk(self):
        """空页伴随结构异常诊断（parse_failed）仍按风控处置，保护不削弱。"""
        payload = json.dumps([{"error": "parse_failed", "sample": ""}])
        self._patch_deps()
        session = _FakeListCDPSession(payload)
        with tempfile.TemporaryDirectory() as tmp:
            output = str(pathlib.Path(tmp) / "list.json")
            with mock.patch.object(self.module, "CDPSession",
                                   lambda cdp_port=None: session):
                with self.assertRaises(self.module.RiskControlError):
                    self.module.scrape_list("Java", "上海", 3, {}, output)


class DetailRateLimitFalsePositiveTests(unittest.TestCase):
    """详情页限流判定不得被页面 chrome 词汇（解锁/冻结/裸词频繁）误触发。"""

    def setUp(self):
        self.module = load_module()

    def test_chrome_words_without_jd_are_not_rate_limit(self):
        """JD 提取失败但页面只含“登录解锁更多职位”类文案 → 不是限流页。"""
        page_text = "首页\n职位\n登录解锁更多职位内容\n安全提示"
        with self.assertRaises(self.module.DetailExtractionError):
            self.module.extract_job_description({"jd": "", "page_text": page_text})
        # 必须不是限流异常（DetailRateLimitedError 是 DetailExtractionError 子类）
        try:
            self.module.extract_job_description({"jd": "", "page_text": page_text})
        except self.module.DetailRateLimitedError:
            self.fail("页面 chrome 词汇不得误判为限流页")
        except self.module.DetailExtractionError:
            pass

    def test_real_rate_limit_page_still_detected(self):
        """真实限流页（操作频繁/稍后再试）仍按限流处理。"""
        page_text = "操作频繁，请稍后再试"
        with self.assertRaises(self.module.DetailRateLimitedError):
            self.module.extract_job_description({"jd": "", "page_text": page_text})


if __name__ == "__main__":
    unittest.main()
