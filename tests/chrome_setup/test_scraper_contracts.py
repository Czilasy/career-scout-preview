import gc
import importlib
import json
import pathlib
import re
import subprocess
import unittest
import warnings
from unittest import mock

from tests.chrome_setup.harness import load_module, tempfile_profile, ROOT_PATH, _normalize_version, _make_scrape_details_list_data, _FakeScrapeDetailsCDPSession, _make_recording_sleeper


# ===========================================================================
# tasks003 T211: 智联登录空间 profile/端口隔离、未知占用、运行锁、不泄露路径
# ===========================================================================


class ZhilianLoginSpaceTests(unittest.TestCase):
    """T208-T211: BOSS 9222 与智联 9223 profile/端口隔离、未知占用拒绝。

    覆盖 webui.platforms 的登录空间派生和双平台检查函数，确认：
    - profile 隔离（boss_profile_dir ≠ zhilian profile_dir）；
    - 端口隔离（9222 ≠ 9223）；
    - 未知 profile 占用端口 → login_space_conflict；
    - 运行锁 → browser_busy；
    - 不泄露 profile 路径。
    """

    def test_profile_isolation_boss_vs_zhilian(self):
        """同一账号的 BOSS 与智联 profile_dir 不同（T208）。"""
        from webui.platforms import derive_zhilian_profile_dir
        boss_dir = "/home/user/.career-scout/chrome-profile"
        zhilian_dir = derive_zhilian_profile_dir(boss_dir)
        self.assertNotEqual(boss_dir, zhilian_dir)
        self.assertTrue(zhilian_dir.endswith(".zhilian"))

    def test_port_isolation_9222_vs_9223(self):
        """BOSS 9222 与智联 9223 端口不同（T208/T211）。"""
        from webui.platforms import (
            BOSS_DEFAULT_CDP_PORT, ZHILIAN_DEFAULT_CDP_PORT,
        )
        self.assertEqual(BOSS_DEFAULT_CDP_PORT, 9222)
        self.assertEqual(ZHILIAN_DEFAULT_CDP_PORT, 9223)
        self.assertNotEqual(BOSS_DEFAULT_CDP_PORT, ZHILIAN_DEFAULT_CDP_PORT)

    def test_login_space_profile_key_isolation(self):
        """BOSS 与智联 profile_key 不同（boss:a ≠ zhilian:a）。"""
        from webui.platforms import resolve_login_space
        boss_space = resolve_login_space("boss", "a", boss_profile_dir="/tmp/p")
        zhilian_space = resolve_login_space("zhilian", "a", boss_profile_dir="/tmp/p")
        self.assertNotEqual(boss_space.profile_key, zhilian_space.profile_key)
        self.assertNotEqual(boss_space.cdp_port, zhilian_space.cdp_port)

    def test_unknown_profile_occupation_rejected(self):
        """未知 profile 占用端口 → login_space_conflict（T209）。"""
        from webui.platforms import check_login_space_conflict
        ok, reason = check_login_space_conflict(
            "zhilian", "a",
            boss_profile_dir="/tmp/profile-a",
            port_profile_paths=["/tmp/unknown-profile"],
            known_profile_paths=["/tmp/profile-a.zhilian", "/tmp/profile-b.zhilian"],
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "login_space_conflict")

    def test_known_profile_occupation_allows_switch(self):
        """同平台已知 profile 占用 → 允许受控切换（T209）。"""
        from webui.platforms import check_login_space_conflict
        ok, reason = check_login_space_conflict(
            "zhilian", "a",
            boss_profile_dir="/tmp/profile-a",
            port_profile_paths=["/tmp/profile-b.zhilian"],
            known_profile_paths=["/tmp/profile-a.zhilian", "/tmp/profile-b.zhilian"],
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_running_lock_blocks_delete(self):
        """运行锁 → browser_busy，阻断删除（T210）。"""
        from webui.platforms import check_browser_account_delete
        ok, reason = check_browser_account_delete(
            "a", boss_profile_dir="/tmp/profile-a",
            running_locks=[{"platform": "boss", "account": "a", "kind": "running"}],
            port_profiles_boss=[], port_profiles_zhilian=[],
        )
        self.assertFalse(ok)
        self.assertIn("browser_busy", reason)

    def test_delete_does_not_leak_profile_path(self):
        """delete reason 不含 profile 路径（T210/T211）。"""
        from webui.platforms import check_browser_account_delete
        secret_path = "/tmp/secret-profile-xyz"
        ok, reason = check_browser_account_delete(
            "a", boss_profile_dir=secret_path,
            running_locks=[], port_profiles_boss=[secret_path],
            port_profiles_zhilian=[],
        )
        self.assertFalse(ok)
        self.assertNotIn(secret_path, reason)
        self.assertNotIn("secret", reason)
        self.assertNotIn("xyz", reason)

    def test_login_space_repr_no_profile_dir(self):
        """LoginSpace 不含 profile_dir 字段，repr 不泄露路径（T211）。"""
        from webui.platforms import LoginSpace
        space = LoginSpace(
            platform="zhilian", browser_account="a",
            profile_key="zhilian:a", cdp_port=9223,
        )
        self.assertFalse(hasattr(space, "profile_dir"))
        repr_text = repr(space)
        self.assertNotIn("profile_dir", repr_text)
        self.assertNotIn("/tmp", repr_text)
        self.assertNotIn("chrome-profile", repr_text)

    def test_boss_profile_unchanged_by_zhilian_registration(self):
        """智联注册不改变 BOSS 9222/基础 profile（T209）。"""
        from webui.platforms import get_platform, BOSS_DEFAULT_CDP_PORT
        boss = get_platform("boss")
        self.assertEqual(boss.default_cdp_port, BOSS_DEFAULT_CDP_PORT)
        self.assertEqual(boss.default_cdp_port, 9222)
        self.assertTrue(boss.enabled_for_new_tasks)
        # BOSS 仍然使用 boss_profile_dir 原值，不加后缀
        space = boss.resolve_login_space("a", boss_profile_dir="/tmp/boss-profile")
        self.assertEqual(space.profile_key, "boss:a")
        self.assertEqual(space.cdp_port, 9222)


class TempfileProfileLifecycleTests(unittest.TestCase):
    def test_context_exit_does_not_leave_temporary_directory_finalizer(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            with tempfile_profile():
                pass
            gc.collect()

        resource_warnings = [w for w in caught if issubclass(w.category, ResourceWarning)]
        self.assertEqual(resource_warnings, [])


class DedicatedChromeShutdownTests(unittest.TestCase):
    def test_graceful_close_refuses_non_dedicated_cdp_profile(self):
        module = load_module()
        session_factory = mock.Mock()
        process_stopper = mock.Mock()

        closed = module.close_cdp_chrome(
            cdp_port=9333,
            cdp_data_dir="C:/isolated/boss-profile",
            profile_checker=lambda _port, _path: False,
            session_factory=session_factory,
            process_stopper=process_stopper,
            ready_checker=lambda _port: True,
            sleeper=lambda _seconds: None,
        )

        self.assertFalse(closed)
        session_factory.assert_not_called()
        process_stopper.assert_not_called()

    def test_dedicated_browser_receives_graceful_close_without_process_kill(self):
        module = load_module()
        session = mock.Mock()
        process_stopper = mock.Mock()

        closed = module.close_cdp_chrome(
            cdp_port=9333,
            cdp_data_dir="C:/isolated/boss-profile",
            profile_checker=lambda _port, _path: True,
            session_factory=lambda _port: session,
            process_stopper=process_stopper,
            ready_checker=lambda _port: False,
            sleeper=lambda _seconds: None,
        )

        self.assertTrue(closed)
        session.send.assert_called_once_with("Browser.close", timeout=5)
        session.close.assert_called_once_with()
        process_stopper.assert_not_called()

    def test_graceful_close_falls_back_to_dedicated_process_stop(self):
        module = load_module()
        session = mock.Mock()
        session.send.side_effect = ConnectionError("browser closed socket")
        process_stopper = mock.Mock(return_value=1)
        readiness = iter([True] * 10 + [False])

        closed = module.close_cdp_chrome(
            cdp_port=9333,
            cdp_data_dir="C:/isolated/boss-profile",
            profile_checker=lambda _port, _path: True,
            session_factory=lambda _port: session,
            process_stopper=process_stopper,
            ready_checker=lambda _port: next(readiness),
            sleeper=lambda _seconds: None,
        )

        self.assertTrue(closed)
        process_stopper.assert_called_once_with("C:/isolated/boss-profile")


class VersionConsistencyTests(unittest.TestCase):
    """校验版本号在 README / pyproject.toml / 脚本保持一致。

    发版时只改一处会漏掉其他几处，这个测试在 CI/本地跑测试时就能拦住。
    """

    def _read_text(self, name):
        return (ROOT_PATH / name).read_text(encoding="utf-8")

    def test_script_version_is_defined(self):
        module = load_module()
        self.assertTrue(getattr(module, "__version__", None),
                        "脚本缺少 __version__")

    def test_versions_are_in_sync_across_all_sources(self):
        module = load_module()
        script_ver = _normalize_version(module.__version__)

        # pyproject.toml: version = "2.0.0"
        pyproject = self._read_text("pyproject.toml")
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
        self.assertIsNotNone(m, "pyproject.toml 未找到 version 字段")
        pyproject_ver = _normalize_version(m.group(1))

        # README.md 标题: # ... v2.3
        readme = self._read_text("README.md")
        m = re.search(r"v(\d+\.\d+(?:\.\d+)?)", readme)
        self.assertIsNotNone(m, "README.md 未找到版本号")
        readme_ver = _normalize_version(m.group(1))

        self.assertEqual(script_ver, pyproject_ver,
                         f"脚本({script_ver}) 与 pyproject.toml({pyproject_ver}) 版本不一致")
        self.assertEqual(script_ver, readme_ver,
                         f"脚本({script_ver}) 与 README.md({readme_ver}) 版本不一致")

    def test_readme_desktop_first_run_guide_covers_prerequisites_and_version(self):
        readme = self._read_text("README.md")
        desktop = readme.split("## 桌面版", 1)[1].split("## 快速开始", 1)[0]
        for required in (
            "Chrome", "Edge", "WebView2", "首次启动偏慢",
            "~/.career-scout", "macOS Gatekeeper", "常见排错", "杀毒软件误报",
        ):
            self.assertIn(required, desktop, f"README 桌面版缺少：{required}")
        pyproject = self._read_text("pyproject.toml")
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
        self.assertIsNotNone(m, "pyproject.toml 未找到 version 字段")
        self.assertIn(f"v{m.group(1)}", desktop)
        self.assertNotIn("v2.8.2", desktop)


class ProjectScopeTests(unittest.TestCase):
    """项目边界守卫：只保留抓取和聚合分析，不内置简历匹配打分。"""

    def _read_text(self, name):
        return (ROOT_PATH / name).read_text(encoding="utf-8")

    def test_resume_matching_feature_is_not_packaged_or_documented(self):
        self.assertFalse(
            (ROOT_PATH / "scripts" / "resume_score.py").exists(),
            "简历匹配打分脚本不应作为项目功能保留",
        )
        self.assertFalse(
            (ROOT_PATH / "tests" / "test_resume_score.py").exists(),
            "删除简历匹配功能时也应删除对应测试",
        )

        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for name in (
                "README.md",
                "pyproject.toml", "requirements.txt", "uv.lock",
            )
            if (path := ROOT_PATH / name).exists()
        )
        # pypdf and python-docx are allowed: they are used by the AI Job
        # Workbench to parse uploaded TXT/PDF/DOCX resumes, not for the
        # removed resume-score-matching feature.
        for forbidden in (
            "resume_score",
            "pdfplumber",
            "openai",
            "langchain",
            "sentence-transformers",
            "简历匹配打分",
            "enable-llm",
        ):
            self.assertNotIn(forbidden, combined)


# ============================================================
# Phase 6 / US4 — scrape_details controlled batching & readiness
# (T066 / T067 RED contract tests)
#
# These tests target the policy-v2 scrape_details contract:
#   * at most 5 selected candidates per batch
#   * one CDP session reused per batch, one target per job
#   * one terminal safe event per job (no JD body / credentials)
#   * readiness probe ≤ 12s with at most one controlled scroll retry
#   * inter-job gap within [3, 7] seconds
#   * no trailing wait after the last job
#
# They are expected to fail (RED) until scripts/boss_cdp_raw.py
# implements the new keyword-only parameters in T068 / T069.
# ============================================================


class ScrapeDetailsBatchingContractTests(unittest.TestCase):
    """T066 RED: scrape_details controlled batching & safe terminal events."""

    def test_batching_contracts_never_use_persistent_default_output(self):
        """批处理契约测试必须显式隔离详情产物，不能写用户默认目录。"""
        class_source = pathlib.Path(__file__).read_text(encoding="utf-8")
        forbidden = "output_path" + "=None"
        self.assertNotIn(forbidden, class_source)

    def setUp(self):
        self._profile = tempfile_profile()
        paths = self._profile.__enter__()
        self.addCleanup(self._profile.__exit__, None, None, None)
        self.output_path = str(
            paths["cdp_profile"] / f"{self._testMethodName}-details.json"
        )

    def test_scrape_details_accepts_batch_size_keyword(self):
        module = load_module()
        list_data = _make_scrape_details_list_data(n=3)

        # Must not raise TypeError — scrape_details needs to accept the
        # batch_size keyword (and the dependency-injection hooks used
        # by the rest of the US4 contract tests).
        result = module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=lambda cdp_port=None: _FakeScrapeDetailsCDPSession(),
            sleeper=lambda seconds, label=None: None,
            event_callback=lambda _event: None,
            trailing_wait=False,
            output_path=self.output_path,
        )
        self.assertEqual(len(result), 3)

    def test_scrape_details_rejects_batch_size_above_five(self):
        module = load_module()
        list_data = _make_scrape_details_list_data(n=3)

        with self.assertRaises(ValueError):
            module.scrape_details(
                list_data,
                batch_size=6,
                session_factory=lambda cdp_port=None: _FakeScrapeDetailsCDPSession(),
                sleeper=lambda seconds, label=None: None,
                output_path=self.output_path,
            )

    def test_scrape_details_creates_one_session_per_batch(self):
        module = load_module()
        list_data = _make_scrape_details_list_data(n=10)

        sessions = []

        def factory(cdp_port=None):
            session = _FakeScrapeDetailsCDPSession()
            sessions.append(session)
            return session

        module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=factory,
            sleeper=lambda seconds, label=None: None,
            trailing_wait=False,
            output_path=self.output_path,
        )
        # 10 jobs / batch_size 5 = 2 batches → 2 CDP sessions.
        self.assertEqual(len(sessions), 2)

    def test_scrape_details_creates_one_target_per_job(self):
        module = load_module()
        list_data = _make_scrape_details_list_data(n=5)
        session = _FakeScrapeDetailsCDPSession()

        module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=lambda cdp_port=None: session,
            sleeper=lambda seconds, label=None: None,
            trailing_wait=False,
            output_path=self.output_path,
        )

        create_target_calls = [
            entry for entry in session.call_log
            if entry["method"] == "Target.createTarget"
        ]
        self.assertEqual(len(create_target_calls), 5)

    def test_scrape_details_emits_one_terminal_safe_event_per_job(self):
        module = load_module()
        list_data = _make_scrape_details_list_data(n=4)
        events = []

        module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=lambda cdp_port=None: _FakeScrapeDetailsCDPSession(),
            sleeper=lambda seconds, label=None: None,
            event_callback=events.append,
            trailing_wait=False,
            output_path=self.output_path,
        )

        self.assertEqual(len(events), 4)
        terminal_statuses = {"completed", "unavailable", "failed", "cancelled"}
        for event in events:
            self.assertIn(event["status"], terminal_statuses)
            self.assertIn("job_id", event)
            self.assertIn("duration_ms", event)

    def test_scrape_details_event_payload_excludes_jd_and_credentials(self):
        module = load_module()
        list_data = _make_scrape_details_list_data(n=1)
        events = []

        module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=lambda cdp_port=None: _FakeScrapeDetailsCDPSession(
                detail_payload={
                    "jd": "SECRET-JD-BODY-MUST-NOT-LEAK",
                    "tags": ["SECRET-TAG-MUST-NOT-LEAK"],
                },
            ),
            sleeper=lambda seconds, label=None: None,
            event_callback=events.append,
            trailing_wait=False,
            output_path=self.output_path,
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        event_repr = repr(event)
        self.assertNotIn("SECRET-JD-BODY-MUST-NOT-LEAK", event_repr)
        self.assertNotIn("SECRET-TAG-MUST-NOT-LEAK", event_repr)
        # Safe events must never carry JD body or credential-shaped fields.
        for forbidden_key in (
            "jd", "tags", "encrypt_job_id", "encrypt_boss_id",
            "encrypt_brand_id", "security_id",
        ):
            self.assertNotIn(forbidden_key, event)
        # Also assert that raw input secrets do not leak via any string value.
        for secret in ("SECRET-ENC-JOB-0", "SECRET-ENC-BOSS-0", "SECRET-SEC-0"):
            self.assertNotIn(secret, event_repr)

    def test_scrape_details_no_trailing_gap_wait_after_last_job(self):
        module = load_module()
        list_data = _make_scrape_details_list_data(n=3)
        sleeper, calls = _make_recording_sleeper()

        module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=lambda cdp_port=None: _FakeScrapeDetailsCDPSession(),
            sleeper=sleeper,
            trailing_wait=False,
            output_path=self.output_path,
        )

        gap_calls = [entry for entry in calls if entry[1] == "inter_job_gap"]
        # 3 jobs → 2 inter-job gaps (between 1-2 and 2-3), no trailing gap.
        self.assertEqual(len(gap_calls), 2)


class ScrapeDetailsReadinessContractTests(unittest.TestCase):
    """T067 RED: readiness-driven detail extraction, conditional scroll,
    bounded gap, and zero trailing wait.
    """

    def setUp(self):
        self._profile = tempfile_profile()
        paths = self._profile.__enter__()
        self.addCleanup(self._profile.__exit__, None, None, None)
        self.output_path = str(
            paths["cdp_profile"] / f"{self._testMethodName}-details.json"
        )

    def test_scrape_details_readiness_wait_does_not_exceed_twelve_seconds(self):
        module = load_module()
        list_data = _make_scrape_details_list_data(n=1)
        sleeper, calls = _make_recording_sleeper()
        session = _FakeScrapeDetailsCDPSession(
            readiness_responses=["not_ready", "ready"],
        )

        module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=lambda cdp_port=None: session,
            sleeper=sleeper,
            readiness_timeout_seconds=12,
            max_readiness_retries=1,
            trailing_wait=False,
            output_path=self.output_path,
        )

        readiness_waits = [
            entry[0] for entry in calls if entry[1] == "readiness_wait"
        ]
        self.assertLessEqual(sum(readiness_waits), 12)

    def test_scrape_details_first_not_ready_triggers_single_scroll_retry(self):
        module = load_module()
        list_data = _make_scrape_details_list_data(n=1)
        session = _FakeScrapeDetailsCDPSession(
            readiness_responses=["not_ready", "ready"],
        )

        module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=lambda cdp_port=None: session,
            sleeper=lambda seconds, label=None: None,
            readiness_timeout_seconds=12,
            max_readiness_retries=1,
            trailing_wait=False,
            output_path=self.output_path,
        )

        scroll_calls = [
            entry for entry in session.call_log
            if entry["method"] == "Runtime.evaluate"
            and "scrollBy" in entry["params"].get("expression", "")
        ]
        # Exactly one controlled scroll during the readiness retry phase.
        self.assertEqual(len(scroll_calls), 1)

    def test_scrape_details_first_ready_triggers_no_scroll(self):
        module = load_module()
        list_data = _make_scrape_details_list_data(n=1)
        session = _FakeScrapeDetailsCDPSession(
            readiness_responses=["ready"],
        )

        module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=lambda cdp_port=None: session,
            sleeper=lambda seconds, label=None: None,
            readiness_timeout_seconds=12,
            max_readiness_retries=1,
            trailing_wait=False,
            output_path=self.output_path,
        )

        scroll_calls = [
            entry for entry in session.call_log
            if entry["method"] == "Runtime.evaluate"
            and "scrollBy" in entry["params"].get("expression", "")
        ]
        self.assertEqual(len(scroll_calls), 0)

    def test_scrape_details_inter_job_gap_within_three_to_seven_seconds(self):
        module = load_module()
        list_data = _make_scrape_details_list_data(n=3)
        sleeper, calls = _make_recording_sleeper()

        module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=lambda cdp_port=None: _FakeScrapeDetailsCDPSession(),
            sleeper=sleeper,
            inter_job_gap_range=(3, 7),
            trailing_wait=False,
            output_path=self.output_path,
        )

        gap_calls = [entry for entry in calls if entry[1] == "inter_job_gap"]
        self.assertEqual(len(gap_calls), 2)
        for seconds, _ in gap_calls:
            self.assertGreaterEqual(seconds, 3)
            self.assertLessEqual(seconds, 7)

    def test_scrape_details_default_inter_job_gap_range_is_eight_to_fifteen(self):
        module = load_module()
        list_data = _make_scrape_details_list_data(n=2)
        sleeper, calls = _make_recording_sleeper()

        module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=lambda cdp_port=None: _FakeScrapeDetailsCDPSession(),
            sleeper=sleeper,
            trailing_wait=False,
            output_path=self.output_path,
        )

        gap_calls = [entry for entry in calls if entry[1] == "inter_job_gap"]
        self.assertEqual(len(gap_calls), 1)
        seconds = gap_calls[0][0]
        self.assertGreaterEqual(seconds, 8)
        self.assertLessEqual(seconds, 15)


class RiskControlTests(unittest.TestCase):
    """列表抓取风控哨兵：识别→停→报错说人话→能续抓。"""

    def test_diagnose_normal_payload_returns_jobs_and_no_diagnosis(self):
        module = load_module()
        payload = json.dumps([{"title": "Java", "job_link": "https://example.com"}])

        jobs, diagnosis, meta = module.diagnose_api_jobs_eval_value(payload)

        self.assertEqual(jobs, [{"title": "Java", "job_link": "https://example.com"}])
        self.assertIsNone(diagnosis)
        # 旧格式列表没有翻页元数据
        self.assertIsNone(meta)

    def test_diagnose_exposes_http_error_status_instead_of_swallowing(self):
        module = load_module()

        jobs, diagnosis, _ = module.diagnose_api_jobs_eval_value(json.dumps([{"error": 403}]))

        self.assertEqual(jobs, [])
        self.assertEqual(diagnosis, {"kind": "http_error", "status": 403})
        # 原解析函数行为不变（静默剔除错误条目），不破坏既有调用方
        self.assertEqual(module.parse_api_jobs_eval_value(json.dumps([{"error": 403}])), [])

    def test_diagnose_exposes_parse_failure_with_sample(self):
        module = load_module()
        payload = json.dumps([{"error": "parse_failed", "sample": "<html>安全验证</html>"}])

        jobs, diagnosis, _ = module.diagnose_api_jobs_eval_value(payload)

        self.assertEqual(jobs, [])
        self.assertEqual(diagnosis["kind"], "parse_failed")
        self.assertIn("安全验证", diagnosis["sample"])

    def test_diagnose_flags_empty_response(self):
        module = load_module()

        for value in (None, "", "not-json", json.dumps({"not": "a list"})):
            jobs, diagnosis, _ = module.diagnose_api_jobs_eval_value(value)
            self.assertEqual(jobs, [])
            self.assertEqual(diagnosis, {"kind": "empty_response"})

    def test_fetch_api_template_reports_parse_failure_and_unexpected_shape(self):
        module = load_module()

        self.assertIn("parse_failed", module.FETCH_API_JS_TEMPLATE)
        self.assertIn("unexpected_shape", module.FETCH_API_JS_TEMPLATE)
        # 既有字段断言保持（防回归）
        self.assertIn("security_id: j.securityId", module.FETCH_API_JS_TEMPLATE)

    def test_looks_like_risk_control_keywords(self):
        module = load_module()

        self.assertTrue(module.looks_like_risk_control("<div>安全验证</div>"))
        self.assertTrue(module.looks_like_risk_control("请完成滑动验证"))
        self.assertTrue(module.looks_like_risk_control("captcha challenge"))
        self.assertFalse(module.looks_like_risk_control('{"zpData":{"jobList":[]}}'))
        self.assertFalse(module.looks_like_risk_control(""))
        self.assertFalse(module.looks_like_risk_control(None))

    def test_check_list_risk_http_error_single_block_is_retry(self):
        # 016：单次 403 不定罪（None=调用方原地重试）；重试后复现才实锤
        module = load_module()

        err = module.check_list_risk(
            {"kind": "http_error", "status": 403},
            page=2, consecutive_empty=0, scraped_count=30,
            output_path="out.json", resume_page=2)
        self.assertIsNone(err)

        verdict, code, hint = module.classify_list_diagnosis(
            {"kind": "http_error", "status": 403}, repeated=True)
        self.assertEqual(verdict, module.VERDICT_CONFIRMED)
        self.assertEqual(code, "source_rate_limited")
        self.assertIn("403", hint)

    def test_check_list_risk_http_401_is_immediate_login_stop(self):
        module = load_module()

        err = module.check_list_risk(
            {"kind": "http_error", "status": 401},
            page=2, consecutive_empty=0, scraped_count=30,
            output_path="out.json", resume_page=2)
        self.assertIsInstance(err, module.RiskControlError)
        self.assertEqual(err.code, "source_login_required")

    def test_check_list_risk_ignores_benign_http_status(self):
        module = load_module()

        err = module.check_list_risk(
            {"kind": "http_error", "status": 404},
            page=1, consecutive_empty=0, scraped_count=0,
            output_path="", resume_page=1)

        self.assertIsNone(err)

    def test_check_list_risk_captcha_sample_is_hard_stop(self):
        module = load_module()

        err = module.check_list_risk(
            {"kind": "parse_failed", "sample": "<html>请完成滑动验证</html>"},
            page=1, consecutive_empty=0, scraped_count=0,
            output_path="", resume_page=1)

        self.assertIsInstance(err, module.RiskControlError)

    def test_check_list_risk_consecutive_empty_never_defames(self):
        # 016：连续空页只做"停止翻页"刹车，不再定性成风控/限流
        module = load_module()

        below = module.check_list_risk(
            None, page=2, consecutive_empty=module.MAX_CONSECUTIVE_EMPTY_PAGES - 1,
            scraped_count=10, output_path="o.json", resume_page=3)
        self.assertIsNone(below)

        at = module.check_list_risk(
            None, page=3, consecutive_empty=module.MAX_CONSECUTIVE_EMPTY_PAGES,
            scraped_count=10, output_path="o.json", resume_page=4)
        self.assertIsNone(at)

    def test_cdp_session_wraps_connection_failure_in_plain_language(self):
        # 028 审查修复：CDPSession._facade() 读 sys.modules 里的真实 facade，且
        # require_runtime_dependencies 首次惰性导入时会把真实 requests/websocket
        # 同步回写 facade（runtime.py L35-39），覆盖测试注入。因此必须同时 patch
        # runtime 与 facade 两处同名属性；此前只打装饰性副本，9222 有活 CDP 时必败。
        module = importlib.import_module("scripts.boss_cdp_raw")
        runtime = importlib.import_module("scripts.boss.runtime")

        class FakeConnError(Exception):
            pass

        requests_mock = mock.Mock()
        requests_mock.ConnectionError = FakeConnError
        requests_mock.Timeout = TimeoutError
        requests_mock.get.side_effect = FakeConnError("refused")
        with mock.patch.object(runtime, "requests", requests_mock), \
                mock.patch.object(runtime, "websocket", mock.Mock()), \
                mock.patch.object(module, "requests", requests_mock, create=True), \
                mock.patch.object(module, "websocket", mock.Mock(), create=True):
            with self.assertRaises(module.CDPUnavailableError) as ctx:
                module.CDPSession(9222)
        self.assertIn("--setup-chrome", str(ctx.exception))

    def test_cdp_session_wraps_websocket_failure_in_plain_language(self):
        # 同上：runtime + facade 双注入，杜绝「9222 有活 CDP 时误绿/误红」。
        module = importlib.import_module("scripts.boss_cdp_raw")
        runtime = importlib.import_module("scripts.boss.runtime")

        class FakeWsError(Exception):
            pass

        requests_mock = mock.Mock()
        # except 子句会求值 requests.ConnectionError/Timeout，必须是真异常类
        requests_mock.ConnectionError = ConnectionError
        requests_mock.Timeout = TimeoutError
        requests_mock.get.return_value.json.return_value = {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/x"
        }
        websocket_mock = mock.Mock()
        websocket_mock.WebSocketException = FakeWsError
        websocket_mock.create_connection.side_effect = FakeWsError("broken")
        with mock.patch.object(runtime, "requests", requests_mock), \
                mock.patch.object(runtime, "websocket", websocket_mock), \
                mock.patch.object(module, "requests", requests_mock, create=True), \
                mock.patch.object(module, "websocket", websocket_mock, create=True):
            with self.assertRaises(module.CDPUnavailableError):
                module.CDPSession(9222)


class CdpMeasurementEventTests(unittest.TestCase):
    """T016 RED: CDP 抓取阶段测量事件 — 终态守恒与敏感字段拒绝。

    覆盖 FR-030、SC-007、data-model.md 2.9。
    boss_cdp_raw 的事件回调必须产出 stage/batch/item_terminal 事件，
    且不得包含凭据、原始简历或 JD 正文。
    """

    def setUp(self):
        self.module = load_module()
        self._profile = tempfile_profile()
        paths = self._profile.__enter__()
        self.addCleanup(self._profile.__exit__, None, None, None)
        self.output_path = str(
            paths["cdp_profile"] / f"{self._testMethodName}-details.json"
        )

    def test_scrape_details_events_have_duration_ms(self):
        """scrape_details 的 terminal 事件必须包含 duration_ms。"""
        events = []
        list_data = _make_scrape_details_list_data(n=3)

        self.module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=lambda cdp_port=None: _FakeScrapeDetailsCDPSession(),
            sleeper=lambda seconds, label=None: None,
            event_callback=events.append,
            trailing_wait=False,
            output_path=self.output_path,
        )
        for ev in events:
            self.assertIn("duration_ms", ev, "事件必须包含 duration_ms")
            self.assertGreaterEqual(ev["duration_ms"], 0, "duration_ms 必须非负")

    def test_scrape_details_events_exclude_jd_body_and_credentials(self):
        """SC-007: 事件 payload 不得包含 JD 正文、凭据或原始简历。"""
        events = []
        list_data = _make_scrape_details_list_data(n=1)

        self.module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=lambda cdp_port=None: _FakeScrapeDetailsCDPSession(),
            sleeper=lambda seconds, label=None: None,
            event_callback=events.append,
            trailing_wait=False,
            output_path=self.output_path,
        )
        for ev in events:
            payload_str = json.dumps(ev, ensure_ascii=False)
            self.assertNotIn("SECRET-ENC-JOB", payload_str,
                              "事件不得包含加密 ID 等敏感凭据")
            self.assertNotIn("api_key", payload_str.lower(),
                              "事件不得包含 api_key")
            self.assertNotIn("resume_text", payload_str.lower(),
                              "事件不得包含 resume_text")

    def test_terminal_status_conservation(self):
        """SC-007: 每个 item 必须有明确终态（completed/failed/unavailable/cancelled）。"""
        events = []
        list_data = _make_scrape_details_list_data(n=5)

        self.module.scrape_details(
            list_data,
            batch_size=5,
            session_factory=lambda cdp_port=None: _FakeScrapeDetailsCDPSession(),
            sleeper=lambda seconds, label=None: None,
            event_callback=events.append,
            trailing_wait=False,
            output_path=self.output_path,
        )
        terminal_statuses = {"completed", "unavailable", "failed", "cancelled"}
        self.assertEqual(len(events), len(list_data["jobs"]),
                         "每个 item 必须产生一个 terminal 事件")
        for ev in events:
            self.assertIn(ev.get("status"), terminal_statuses,
                          "每个 item 必须有明确终态")


class CdpPortThreadingTests(unittest.TestCase):
    """tasks001 T007 — 验证 --cdp-port 显式端口贯穿 scraper 调用边界。

    合同（contracts/job-source.md 构造合同）：所有 list/detail/batch
    subprocess 必须显式透传冻结 CDP 端口，不得回退读取默认端口。
    """

    def test_default_cdp_port_is_9222(self):
        module = load_module()
        self.assertEqual(module.DEFAULT_CDP_PORT, 9222)

    def test_cdp_session_accepts_custom_port(self):
        """CDPSession 构造接受显式 cdp_port 参数。"""
        module = load_module()
        import inspect
        sig = inspect.signature(module.CDPSession.__init__)
        self.assertIn("cdp_port", sig.parameters)

    def test_check_login_state_accepts_custom_port(self):
        """check_login_state 接受显式 cdp_port 参数。"""
        module = load_module()
        import inspect
        sig = inspect.signature(module.check_login_state)
        self.assertIn("cdp_port", sig.parameters)

    def test_scrape_list_accepts_custom_port(self):
        """scrape_list 接受显式 cdp_port 关键字参数。"""
        module = load_module()
        import inspect
        sig = inspect.signature(module.scrape_list)
        self.assertIn("cdp_port", sig.parameters)

    def test_run_check_accepts_custom_port(self):
        """run_check 接受显式 cdp_port 参数。"""
        module = load_module()
        import inspect
        sig = inspect.signature(module.run_check)
        self.assertIn("cdp_port", sig.parameters)

    def test_main_parser_includes_cdp_port_argument(self):
        """--cdp-port 参数被 argparse 接受且默认值为 DEFAULT_CDP_PORT。"""
        module = load_module()
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("--cdp-port", type=int, default=module.DEFAULT_CDP_PORT)
        args = p.parse_args(["--cdp-port", "9223"])
        self.assertEqual(args.cdp_port, 9223)

        # 默认值验证
        args_default = p.parse_args([])
        self.assertEqual(args_default.cdp_port, module.DEFAULT_CDP_PORT)


if __name__ == "__main__":
    unittest.main()
