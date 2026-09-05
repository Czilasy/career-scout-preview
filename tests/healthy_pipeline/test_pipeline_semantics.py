"""健康流程语义守恒合同测试（027 自 tests/test_healthy_pipeline.py 拆出）。"""
import os
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from tests.healthy_pipeline.harness import _make_app, _authed_test_client, _wait_for_pipeline_task, _pause_run


class Slice8RecrawlTests(unittest.TestCase):
    """切片 8：批量+单条补救改造（FR-022/FR-023）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = _authed_test_client(self.app)
        self.token = self.app.config["API_TOKEN"]
        self.store = self.app.config["TASK_STORE"]
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        self.run_id = "recrawl-source-run"
        self.store.create_screening_run(self.run_id, source_count=100)

    def tearDown(self):
        # 先等后台线程池任务结束（任务会因无 Chrome 快速失败），释放 db 连接
        try:
            executor = self.app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
        except Exception:
            pass
        import gc
        gc.collect()
        self.temp.cleanup()

    def _auth(self):
        return {"X-Boss-Token": self.token}

    def _save_pending_source(self, *, job_id="j1", jd=""):
        """Persist one isolated pending job for recrawl regression tests."""
        return self.store.save_pipeline_result({
            "jobs": [{
                "job_id": job_id,
                "title": "前端工程师",
                "verdict": "uncertain",
                "verdict_reason": "详情超时",
                "jd_failed_code": "detail_timeout",
                "jd": jd,
                "source_url": f"https://www.zhipin.com/job_detail/{job_id}.html",
            }],
            "dropped": [],
            "total_scraped": 1,
            "total_kept": 1,
            "total_matched": 0,
            "total_dropped": 0,
            "profile_summary": "前端工程师",
        }, {"keyword": "前端", "city": ["上海"]})

    def _post_recrawl(self, source_run_id, *, job_id="j1"):
        """Start one isolated recrawl task and return its task id."""
        response = self.client.post(
            "/api/pipeline/recrawl",
            json={
                "source_run_id": source_run_id,
                "job_ids": [job_id],
                "profile_summary": "前端工程师",
            },
            headers=self._auth(),
        )
        self.assertEqual(response.status_code, 202, response.get_json())
        return response.get_json()["task_id"]

    def test_recrawl_chrome_not_ready_pauses_with_persisted_reason(self):
        """Chrome preflight failure is systemic and must never finish recrawl."""
        source_run_id = self._save_pending_source()
        with mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready",
            return_value=(False, "debug port unavailable"),
        ):
            task_id = self._post_recrawl(source_run_id)
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["error_code"], "source_cdp_unavailable")
        self.assertEqual(
            self.store.get_pending_result(task_id, "j1")["failed_code"],
            "source_cdp_unavailable",
        )

    def test_recrawl_without_ai_configuration_pauses_instead_of_succeeding(self):
        """已有 JD 但 AI 未配置时不得伪装为补抓成功。"""
        source_run_id = self._save_pending_source(
            jd="岗位职责：负责后端服务开发"
        )
        task_id = self._post_recrawl(source_run_id)
        paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["error_code"], "ai_key_invalid")
        self.assertEqual(run["current_stage"], "recrawl_ai")
        self.assertIsNotNone(self.store.get_pending_result(source_run_id, "j1"))

    def test_recrawl_missing_cdp_source_pauses_with_persisted_reason(self):
        """A missing CDP source after preflight must use the same hard-stop contract."""
        source_run_id = self._save_pending_source()
        with mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ), mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=None):
            task_id = self._post_recrawl(source_run_id)
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["error_code"], "source_cdp_unavailable")

    def test_recrawl_zero_detail_results_pauses_instead_of_completing(self):
        """详情抓取器返回 0 条 JD 时，重抓不能伪装为已完成。"""
        source_run_id = self._save_pending_source()
        detail_result = {
            "jobs": [{
                "job_id": "j1", "jd": "",
                "jd_failed_code": "source_invalid_output",
                "jd_failed_reason": "浏览器未返回岗位详情",
            }],
            "hard_stop": False, "stopped": False, "fetched": 0,
        }
        with mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ), mock.patch.object(
            self.app.config["PIPELINE_CONTEXT"], "source_class",
            return_value=object(),
        ), mock.patch(
            "webui.pipeline_exec.fetch_job_details", return_value=detail_result
        ):
            task_id = self._post_recrawl(source_run_id)
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["error_code"], "recrawl_no_work")
        self.assertIn("0/1", str(run["error_reason"]))

    def test_zhilian_recrawl_detail_input_keeps_platform_identity(self):
        """重抓智联详情时，输入必须保留 adapter 所需的平台身份三元组。"""
        source_run_id = self.store.save_pipeline_result({
            "jobs": [{
                "job_id": "CC000544460J40760128216",
                "platform": "zhilian",
                "platform_job_id": "CC000544460J40760128216",
                "canonical_url": (
                    "https://www.zhaopin.com/jobdetail/"
                    "CC000544460J40760128216.htm"
                ),
                "source_url": (
                    "https://www.zhaopin.com/jobdetail/"
                    "CC000544460J40760128216.htm"
                ),
                "title": "AI 应用开发工程师",
                "verdict": "uncertain",
                "jd": "",
            }],
            "dropped": [],
            "total_scraped": 1,
            "total_kept": 1,
            "total_matched": 0,
            "total_dropped": 0,
            "profile_summary": "AI 应用开发工程师",
        }, {
            "platform": "zhilian",
        }, execution_params={
            "browser_account": "a",
            "cdp_port": 9223,
            "profile_key": "zhilian:a",
        })
        captured = {}
        detail_result = {
            "jobs": [{
                "job_id": "CC000544460J40760128216",
                "jd": "岗位职责：负责 AI 应用开发",
            }],
            "hard_stop": False, "stopped": False, "fetched": 1,
        }

        def capture_detail(jobs, *_args, **_kwargs):
            captured["jobs"] = jobs
            return detail_result

        with mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ), mock.patch("webui.source.ZhilianCdpSource", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details",
                           side_effect=capture_detail):
            task_id = self._post_recrawl(
                source_run_id, job_id="CC000544460J40760128216"
            )
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        self.assertEqual(
            self.store.get_screening_run(task_id)["error_code"], "ai_key_invalid"
        )
        self.assertEqual(captured["jobs"], [{
            "platform": "zhilian",
            "platform_job_id": "CC000544460J40760128216",
            "job_id": "CC000544460J40760128216",
            "source_url": (
                "https://www.zhaopin.com/jobdetail/"
                "CC000544460J40760128216.htm"
            ),
            "job_link": (
                "https://www.zhaopin.com/jobdetail/"
                "CC000544460J40760128216.htm"
            ),
            "canonical_url": (
                "https://www.zhaopin.com/jobdetail/"
                "CC000544460J40760128216.htm"
            ),
        }])

    def test_zhilian_recrawl_rebinds_frozen_profile_before_cdp_check(self):
        """重抓线程不能沿用其他任务留下的 CDP profile。"""
        from webui import pipeline_exec
        from webui.pipeline_exec import resolve_browser_account
        from webui.platforms import derive_zhilian_profile_dir

        source_run_id = self.store.save_pipeline_result({
            "jobs": [{
                "job_id": "CC000544460J40760128216",
                "platform": "zhilian",
                "platform_job_id": "CC000544460J40760128216",
                "canonical_url": (
                    "https://www.zhaopin.com/jobdetail/"
                    "CC000544460J40760128216.htm"
                ),
                "title": "AI 应用开发工程师",
                "verdict": "uncertain",
                "jd": "",
            }],
            "dropped": [],
            "total_scraped": 1,
            "total_kept": 1,
            "total_matched": 0,
            "total_dropped": 0,
            "profile_summary": "AI 应用开发工程师",
        }, {"platform": "zhilian"}, execution_params={
            "browser_account": "a",
            "cdp_port": 9223,
            "profile_key": "zhilian:a",
        })
        boss_profile = resolve_browser_account(
            "a", self.app.config["BROWSER_ACCOUNTS_PATH"]
        )
        expected_profile = derive_zhilian_profile_dir(boss_profile)
        seen_profiles = []

        def verify_profile(_port, **_kwargs):
            seen_profiles.append(pipeline_exec._ACTIVE_CDP_DATA_DIR)
            return True, ""

        detail_result = {
            "jobs": [{
                "job_id": "CC000544460J40760128216",
                "jd": "岗位职责：负责 AI 应用开发",
            }],
            "hard_stop": False, "stopped": False, "fetched": 1,
        }
        old_profile = pipeline_exec._ACTIVE_CDP_DATA_DIR
        pipeline_exec._ACTIVE_CDP_DATA_DIR = "C:/wrong-profile"
        try:
            with mock.patch(
                "webui.pipeline_exec.ensure_chrome_ready",
                side_effect=verify_profile,
            ), mock.patch(
                "webui.source.ZhilianCdpSource", return_value=object()
            ), mock.patch(
                "webui.pipeline_exec.fetch_job_details", return_value=detail_result
            ):
                task_id = self._post_recrawl(
                    source_run_id, job_id="CC000544460J40760128216"
                )
                paused = _wait_for_pipeline_task(self.client, task_id)
        finally:
            pipeline_exec._ACTIVE_CDP_DATA_DIR = old_profile

        self.assertEqual(paused["status"], "paused", paused)
        self.assertEqual(
            [os.path.normcase(os.path.normpath(path)) for path in seen_profiles],
            [os.path.normcase(os.path.normpath(expected_profile))],
        )

    def test_recrawl_pause_persistence_failure_does_not_claim_paused(self):
        """Recrawl pause writes are mandatory before the in-memory pause is visible."""
        source_run_id = self._save_pending_source()
        original_update = self.store.update_screening_run

        def fail_pause(run_id, **kwargs):
            if kwargs.get("status") == "paused":
                raise RuntimeError("pause write rejected")
            return original_update(run_id, **kwargs)

        detail_failure = {
            "jobs": [{
                "job_id": "j1", "jd": "",
                "jd_failed_code": "captcha_required",
                "jd_failed_reason": "验证码仍存在",
            }],
            "hard_stop": True, "hard_stop_code": "captcha_required",
            "stopped": False, "fetched": 0,
        }
        with mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ), mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch(
                    "webui.pipeline_exec.fetch_job_details", return_value=detail_failure
                ), mock.patch.object(
                    self.store, "update_screening_run", side_effect=fail_pause
                ):
            task_id = self._post_recrawl(source_run_id)
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        self.assertEqual(self.store.get_screening_run(task_id)["status"], "failed")

    def test_recrawl_source_verdict_failure_preserves_pending_and_fails(self):
        """A source verdict write failure must stop before pending deletion."""
        source_run_id = self._save_pending_source(jd="负责前端开发")
        self.store.save_ai_settings("http://example.invalid", "test-ref", status="ready")
        original_save = self.store.save_screening_verdicts

        def fail_source_verdict(run_id, verdicts):
            if run_id == source_run_id:
                raise RuntimeError("source verdict write rejected")
            return original_save(run_id, verdicts)

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.match_jds", return_value={
                    "verdicts": {
                        "j1": {"verdict": "match", "reason": "匹配", "caveats": []}
                    }
                }), mock.patch.object(
                    self.store, "save_screening_verdicts",
                    side_effect=fail_source_verdict,
                ):
            task_id = self._post_recrawl(source_run_id)
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        self.assertIsNotNone(self.store.get_pending_result(source_run_id, "j1"))

    def test_recrawl_pending_delete_failure_does_not_finish(self):
        """Pending deletion is part of the recrawl commit and cannot be best-effort."""
        source_run_id = self._save_pending_source(jd="负责前端开发")
        self.store.save_ai_settings("http://example.invalid", "test-ref", status="ready")
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.match_jds", return_value={
                    "verdicts": {
                        "j1": {"verdict": "match", "reason": "匹配", "caveats": []}
                    }
                }), mock.patch.object(
                    self.store, "delete_pending_result",
                    side_effect=RuntimeError("pending delete rejected"),
                ):
            task_id = self._post_recrawl(source_run_id)
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)

    def test_recrawl_terminal_persistence_failure_does_not_finish(self):
        """The in-memory task cannot report done until its terminal DB write commits."""
        source_run_id = self._save_pending_source(jd="负责前端开发")
        self.store.save_ai_settings(
            "http://example.invalid", "test-ref", status="ready"
        )
        original_update = self.store.update_screening_run

        def fail_terminal(run_id, **kwargs):
            if kwargs.get("status") == "succeeded":
                raise RuntimeError("terminal write rejected")
            return original_update(run_id, **kwargs)

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.match_jds", return_value={
                    "verdicts": {
                        "j1": {"verdict": "match", "reason": "匹配", "caveats": []}
                    }
                }), mock.patch.object(
                    self.store, "update_screening_run", side_effect=fail_terminal
                ):
            task_id = self._post_recrawl(source_run_id)
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "failed", finished)
        self.assertEqual(self.store.get_screening_run(task_id)["status"], "failed")

    def test_recrawl_auto_reads_from_pending_table(self):
        """job_ids 缺省时从 screening_pending_results 自动读取（FR-023）。"""
        # 给 run 加 3 条 pending
        for i in range(3):
            self.store.insert_pending_result(
                self.run_id, f"job-{i}",
                failure_stage="jd_detail", retryable=True,
                origin_zone="jd", failed_code="detail_timeout")
        # 不传 job_ids，应自动从 pending 表读
        resp = self.client.post("/api/pipeline/recrawl",
                                json={"source_run_id": self.run_id},
                                headers=self._auth())
        # 202 表示任务已接受
        self.assertEqual(resp.status_code, 202)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("source_run_id"), self.run_id)

    def test_recrawl_concurrent_rejected(self):
        """同 source_run_id 已有 running 重抓任务时拒绝（FR-022）。"""
        # 给 run 加 pending，使第一次 recrawl 能启动
        self.store.insert_pending_result(
            self.run_id, "job-1", failure_stage="jd_detail",
            origin_zone="jd", failed_code="detail_timeout")
        # 第一次：202（任务已接受）
        resp1 = self.client.post("/api/pipeline/recrawl",
                                 json={"source_run_id": self.run_id},
                                 headers=self._auth())
        self.assertEqual(resp1.status_code, 202)
        # 第二次立即调：第一个任务可能还在 running（409）或已 cleanup（202）
        # 关键验证：不会启动两个并发任务（要么 409 拒绝，要么 202 接受但第一个已结束）
        resp2 = self.client.post("/api/pipeline/recrawl",
                                 json={"source_run_id": self.run_id},
                                 headers=self._auth())
        self.assertIn(resp2.status_code, (202, 409))

    def test_recrawl_concurrent_requests_claim_source_run_once(self):
        """Two truly concurrent starts may enqueue only one recrawl (FR-022)."""
        self.store.insert_pending_result(
            self.run_id, "job-race", failure_stage="jd_detail",
            origin_zone="jd", failed_code="detail_timeout",
        )
        request_barrier = threading.Barrier(2)
        real_uuid4 = __import__("uuid").uuid4

        def synchronized_uuid4():
            request_barrier.wait(timeout=2)
            return real_uuid4()

        def post_recrawl():
            with _authed_test_client(self.app) as client:
                return client.post(
                    "/api/pipeline/recrawl",
                    json={"source_run_id": self.run_id},
                    headers=self._auth(),
                ).status_code

        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch("uuid.uuid4", side_effect=synchronized_uuid4), \
                mock.patch.object(executor, "submit") as submit, \
                ThreadPoolExecutor(max_workers=2) as requests:
            statuses = sorted(f.result(timeout=3) for f in (
                requests.submit(post_recrawl), requests.submit(post_recrawl),
            ))

        self.assertEqual(statuses, [202, 409])
        submit.assert_called_once()


class Slice11RecrawlResumeTests(unittest.TestCase):
    """A.4 重抓继续：必须用原 task_id，不得调 recrawlUncertain 新建（阻断项 4）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = _authed_test_client(self.app)
        self.token = self.app.config["API_TOKEN"]
        self.store = self.app.config["TASK_STORE"]
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")

    def tearDown(self):
        try:
            executor = self.app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
        except Exception:
            pass
        import gc
        gc.collect()
        self.temp.cleanup()

    def _auth(self):
        return {"X-Boss-Token": self.token}

    def test_recrawl_continue_uses_original_task_id(self):
        """重抓继续必须调 /api/recrawl/continue/<original_task_id>，不新建 task_id。

        修复目标（B.4）：新增 /api/recrawl/continue/<original_task_id> 路由；
        前端继续按钮调新路由而非 recrawlUncertain()。
        """
        original_task_id = "recrawl-original-task"
        self.store.create_screening_run(
            original_task_id, source_count=10,
            execution_params={
                "source_run_id": "source-run-1",
                "job_ids": ["j1", "j2"],
                "profile_summary": "前端工程师",
            },
        )
        _pause_run(self.store, original_task_id,
                                          error_code="captcha_required",
                                          current_stage="recrawl_fetch_jd")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post(
                f"/api/recrawl/continue/{original_task_id}",
                headers=self._auth())
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json() or {}
        self.assertEqual(data.get("task_id"), original_task_id)
        self.assertNotIn("new_task_id", data)
        submit.assert_called_once()
        self.assertEqual(submit.call_args.args[1], original_task_id)

    def test_recrawl_continue_loads_source_run_id_and_checkpoint(self):
        """重抓继续从 scrape_run_jobs + checkpoint 加载 source_run_id 和 skip_combos。"""
        original_task_id = "recrawl-original-task-2"
        self.store.create_screening_run(
            original_task_id, source_count=10,
            execution_params={
                "source_run_id": "source-run-2",
                "job_ids": ["j1"],
                "profile_summary": "后端工程师",
            },
        )
        _pause_run(self.store, original_task_id,
                                          error_code="captcha_required",
                                          current_stage="recrawl_fetch_jd")
        self.store.save_checkpoint(original_task_id, "recrawl_jd", ["j1"])

        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post(
                f"/api/recrawl/continue/{original_task_id}",
                headers=self._auth())
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json() or {}
        self.assertEqual(data.get("source_run_id"), "source-run-2")
        self.assertEqual(set(data.get("completed_job_ids") or []), {"j1"})
        submitted = submit.call_args.args
        self.assertEqual(submitted[1], original_task_id)
        self.assertEqual(submitted[2], ["j1"])
        self.assertEqual(submitted[4], "source-run-2")
        self.assertEqual(set(submitted[5]), {"j1"})

    def test_recrawl_hard_stop_persists_partial_jd_before_pause(self):
        """同批部分成功后验证码：先落 JD 和 checkpoint，再进入 paused。"""
        source_run_id = self.store.save_pipeline_result({
            "jobs": [{
                "job_id": "j1", "title": "前端", "verdict": "uncertain",
                "source_url": "https://www.zhipin.com/job_detail/j1.html",
            }],
            "dropped": [], "total_scraped": 1, "total_kept": 1,
            "total_matched": 0, "total_dropped": 0,
            "profile_summary": "前端工程师",
        }, {"keyword": "前端", "city": ["上海"]})

        with mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                        return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value={
                    "jobs": [{"job_id": "j1", "jd": "岗位职责：负责前端开发"}],
                    "hard_stop": True,
                    "hard_stop_code": "captcha_required",
                    "stopped": False,
                    "fetched": 1,
                }):
            response = self.client.post(
                "/api/pipeline/recrawl",
                json={
                    "job_ids": ["j1"], "profile_summary": "前端工程师",
                    "source_run_id": source_run_id,
                },
                headers=self._auth(),
            )
            self.assertEqual(response.status_code, 202, response.get_json())
            task_id = response.get_json()["task_id"]
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        payload = self.store.load_latest_pipeline_result()
        job = payload["result"]["jobs"][0]
        self.assertEqual(job.get("jd"), "岗位职责：负责前端开发")
        self.assertEqual(self.store.load_checkpoint(task_id, "recrawl_jd"), {"j1"})

    def test_recrawl_hard_stop_persists_job_failure_on_task_and_source(self):
        source_run_id = self.store.save_pipeline_result({
            "jobs": [{
                "job_id": "j1", "title": "前端", "verdict": "uncertain",
                "verdict_reason": "详情超时", "jd_failed_code": "detail_timeout",
                "source_url": "https://www.zhipin.com/job_detail/j1.html",
            }],
            "dropped": [], "total_scraped": 1, "total_kept": 1,
            "total_matched": 0, "total_dropped": 0,
            "profile_summary": "前端工程师",
        }, {"keyword": "前端", "city": ["上海"]})
        detail_failure = {
            "jobs": [{
                "job_id": "j1", "jd": "",
                "jd_failed_code": "cdp_unavailable",
                "jd_failed_reason": "CDP websocket disconnected",
            }],
            "hard_stop": True, "hard_stop_code": "cdp_unavailable",
            "stopped": False, "fetched": 0,
        }

        with mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class", return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details", return_value=detail_failure):
            response = self.client.post(
                "/api/pipeline/recrawl",
                json={
                    "job_ids": ["j1"], "profile_summary": "前端工程师",
                    "source_run_id": source_run_id,
                },
                headers=self._auth(),
            )
            task_id = response.get_json()["task_id"]
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        for run_id in (task_id, source_run_id):
            pending = self.store.get_pending_result(run_id, "j1")
            self.assertIsNotNone(pending, run_id)
            self.assertEqual(pending["failed_code"], "cdp_unavailable")
            self.assertEqual(
                pending["ai_payload"]["reason"], "CDP websocket disconnected"
            )
        failures = [
            event for event in self.store.list_task_events(task_id)
            if event["type"] == "job_fail"
        ]
        self.assertEqual(failures[-1]["payload"]["job_id"], "j1")
        self.assertEqual(failures[-1]["payload"]["failed_code"], "cdp_unavailable")

    def test_recrawl_ai_pause_persists_batch_and_resume_skips_it(self):
        """重抓 AI 第二批限流后，第一批落库；继续只调用未完成岗位。"""
        from webui.ai import AISecurityError, ERROR_RATE_LIMIT

        source_run_id = self.store.save_pipeline_result({
            "jobs": [
                {
                    "job_id": "j1", "title": "前端", "verdict": "uncertain",
                    "jd": "岗位职责：前端开发",
                },
                {
                    "job_id": "j2", "title": "后端", "verdict": "uncertain",
                    "jd": "岗位职责：后端开发",
                },
            ],
            "dropped": [], "total_scraped": 2, "total_kept": 2,
            "total_matched": 0, "total_dropped": 0,
            "profile_summary": "工程师",
        }, {"keyword": "工程师", "city": ["上海"]})
        self.store.save_ai_settings("http://example.invalid", "test-ref", status="ready")

        first_calls = []

        def first_match(jobs, *_args, **_kwargs):
            first_calls.append([job["job_id"] for job in jobs])
            if jobs[0]["job_id"] == "j2":
                raise AISecurityError(ERROR_RATE_LIMIT)
            return {"verdicts": {
                "j1": {"verdict": "match", "reason": "匹配", "caveats": []},
            }}

        common_patches = (
            mock.patch("webui.ai.retrieve_api_key", return_value="key"),
            mock.patch("webui.pipeline_exec.load_advanced_settings",
                       return_value={"match_batch_size": 1}),
        )
        with common_patches[0], common_patches[1], \
                mock.patch("webui.ai.match_jds", side_effect=first_match):
            response = self.client.post(
                "/api/pipeline/recrawl",
                json={
                    "job_ids": ["j1", "j2"], "profile_summary": "工程师",
                    "source_run_id": source_run_id,
                },
                headers=self._auth(),
            )
            task_id = response.get_json()["task_id"]
            paused = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(paused["status"], "paused", paused)
        self.assertEqual(first_calls, [["j1"], ["j2"]])
        self.assertIn("j1", self.store.load_screening_verdicts(task_id))
        self.assertEqual(self.store.load_checkpoint(task_id, "recrawl_ai"), {"j1"})

        resumed_calls = []

        def resumed_match(jobs, *_args, **_kwargs):
            resumed_calls.append([job["job_id"] for job in jobs])
            return {"verdicts": {
                "j2": {"verdict": "not_match", "reason": "不匹配", "caveats": []},
            }}

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.pipeline_exec.load_advanced_settings",
                           return_value={"match_batch_size": 1}), \
                mock.patch("webui.ai.match_jds", side_effect=resumed_match):
            response = self.client.post(
                f"/api/recrawl/continue/{task_id}", headers=self._auth()
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            finished = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(finished["status"], "completed", finished)
        self.assertEqual(resumed_calls, [["j2"]])


class LoginRecheckTests(unittest.TestCase):
    """登录失效二次复核：通过则重试本组合，确认失败则跳过并上报原因，不整场暂停。"""

    class _Source:
        platform = "boss"

        def __init__(self, outcomes, recheck):
            self.outcomes = list(outcomes)
            self.recheck = recheck
            self.fetch_calls = 0

        def preflight(self):
            from webui.source import SourceOutcome
            return SourceOutcome.success()

        def recheck_login(self):
            return self.recheck

        def fetch_list(self, plan_item, *, on_page_completed=None):
            self.fetch_calls += 1
            idx = min(self.fetch_calls - 1, len(self.outcomes) - 1)
            return self.outcomes[idx]

    @staticmethod
    def _run(outcomes, recheck, params=None):
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome
        source = LoginRecheckTests._Source(outcomes, recheck)
        issues = []
        with mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                        return_value=(True, "")), \
             mock.patch("webui.pipeline_exec.close_debug_chrome"):
            result = run_search(
                params or {"keyword": "A,B", "city": ["上海"]},
                source, pages=1, sleeper=lambda _s: None,
                on_issue=lambda combo, entry: issues.append((combo, dict(entry))),
                close_chrome_on_success=False,
            )
        return source, result, issues

    def test_recheck_passes_retries_and_recovers_combo(self):
        from webui.source import SourceOutcome
        source, result, issues = self._run(
            [
                SourceOutcome.failure(
                    failed_code="source_login_required", safe_log="reason=疑似"),
                SourceOutcome.success(
                    jobs=[{"job_id": "j1", "source_url": "u1"}],
                    scope_complete=True,
                ),
            ],
            SourceOutcome.success(),
            params={"keyword": "A", "city": ["上海"]},  # 单组合：1 次失败 + 1 次重试
        )
        self.assertTrue(result["ok"], result)
        self.assertNotIn("hard_stop", result)
        self.assertEqual(source.fetch_calls, 2)
        self.assertEqual(result["total_scraped"], 1)
        self.assertEqual(issues[0][1]["event"], "login_recheck_passed_retry")

    def test_recheck_confirmed_skips_combo_and_continues(self):
        from webui.source import SourceOutcome
        source, result, issues = self._run(
            [
                SourceOutcome.failure(
                    failed_code="source_login_required", safe_log="reason=确认"),
                SourceOutcome.success(
                    jobs=[{"job_id": "j2", "source_url": "u2"}],
                    scope_complete=True,
                ),
            ],
            SourceOutcome.failure(failed_code="source_login_required"),
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["integrity"]["conclusion"], "partial")
        self.assertNotIn("hard_stop", result)
        self.assertEqual(source.fetch_calls, 2)
        self.assertEqual(result["total_scraped"], 1)
        self.assertEqual(result["completed_combos"], ["B|上海"])
        self.assertEqual(issues[0][1]["event"], "login_required_confirmed_skip")

    def test_all_combos_confirmed_login_skip_no_hard_stop(self):
        from webui.source import SourceOutcome
        source, result, issues = self._run(
            [SourceOutcome.failure(
                failed_code="source_login_required", safe_log="reason=全部")],
            SourceOutcome.failure(failed_code="source_login_required"),
            params={"keyword": "A", "city": ["上海"]},
        )
        self.assertFalse(result["ok"], result)
        self.assertNotIn("hard_stop", result)
        self.assertIn("因登录失效跳过了全部 1 个组合", result["error"])
        self.assertEqual(source.fetch_calls, 1)
        self.assertEqual(issues[0][1]["event"], "login_required_confirmed_skip")

    def test_confirmed_login_skip_keeps_inter_combo_wait(self):
        """登录复核确认失败跳过组合时，仍需保留组合间冷却等待。"""
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome
        source = LoginRecheckTests._Source(
            [
                SourceOutcome.failure(failed_code="source_login_required"),
                SourceOutcome.failure(failed_code="source_login_required"),
            ],
            SourceOutcome.failure(failed_code="source_login_required"),
        )
        wakes = []
        with mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                        return_value=(True, "")), \
             mock.patch("webui.pipeline_exec.close_debug_chrome"):
            result = run_search(
                {"keyword": "A,B", "city": ["上海"]},
                source, pages=1, sleeper=lambda s: wakes.append(s),
                close_chrome_on_success=False,
            )
        self.assertFalse(result["ok"], result)
        self.assertNotIn("hard_stop", result)
        self.assertEqual(len(wakes), 1)


# ===========================================================================
# SPEC011 T005 — 冻结配置摘要一致性 RED 测试
# ===========================================================================


class Spec006ProgressSemanticsTests(unittest.TestCase):
    """B011：进度百分比只由真实完成事件推进。"""

    def test_scrape_overall_percent_uses_real_completed_combos(self):
        from webui import pipeline_exec as pe

        self.assertEqual(pe._scrape_overall_percent("ensure_chrome", 0, 10), 1)
        self.assertEqual(pe._scrape_overall_percent("preflight", 0, 10), 1)
        self.assertEqual(pe._scrape_overall_percent("searching", 0, 10), 0)
        self.assertEqual(pe._scrape_overall_percent("searching", 1, 10), 10)
        self.assertEqual(pe._scrape_overall_percent("combo_done", 1, 10), 10)
        self.assertEqual(pe._scrape_overall_percent("combo_done", 2, 10), 20)
        self.assertEqual(pe._scrape_overall_percent("waiting", 1, 10), 10)
        self.assertEqual(pe._scrape_overall_percent("combo_failed", 1, 10), 10)
        self.assertEqual(pe._scrape_overall_percent("risk_warning", 0, 10), 0)
        self.assertEqual(pe._scrape_overall_percent("closing_chrome", 10, 10), 100)
        self.assertEqual(pe._scrape_overall_percent("done", 0, 10), 100)
        self.assertEqual(pe._scrape_overall_percent("cancelled", 3, 10), 0)

    def test_screen_overall_percent_uses_25_50_25_weights(self):
        from webui.app import _screen_overall_percent

        self.assertEqual(_screen_overall_percent("screen_a", 0, 10), 0)
        self.assertEqual(_screen_overall_percent("screen_a", 10, 10), 25)
        self.assertEqual(_screen_overall_percent("screen_a_done", 0, 10), 25)
        self.assertEqual(_screen_overall_percent("ensure_chrome", 0, 10), 25)
        self.assertEqual(_screen_overall_percent("fetch_jd", 0, 10), 25)
        self.assertEqual(_screen_overall_percent("fetch_jd", 10, 10), 75)
        self.assertEqual(_screen_overall_percent("screen_b", 0, 10), 75)
        self.assertEqual(_screen_overall_percent("screen_b", 10, 10), 100)
        self.assertEqual(_screen_overall_percent("done", 0, 10), 100)

    def test_screen_overall_percent_never_rounds_to_100_before_done(self):
        from webui.app import _screen_overall_percent

        self.assertEqual(_screen_overall_percent("ai_rough", 10, 10), 25)
        self.assertEqual(_screen_overall_percent("jd_detail", 10, 10), 75)
        self.assertEqual(_screen_overall_percent("ai_fine", 10, 10), 100)
        self.assertEqual(_screen_overall_percent("screen_b", 98, 100), 99)
        self.assertEqual(_screen_overall_percent("screen_b", 100, 100), 100)
        self.assertEqual(_screen_overall_percent("fetch_jd", 98, 100), 74)

    def test_recrawl_overall_percent_uses_60_40_weights(self):
        from webui.app import _recrawl_overall_percent

        self.assertEqual(_recrawl_overall_percent("fetch_jd", 0, 10), 0)
        self.assertEqual(_recrawl_overall_percent("fetch_jd", 10, 10), 60)
        self.assertEqual(_recrawl_overall_percent("recrawl_fetch_jd", 5, 10), 30)
        self.assertEqual(_recrawl_overall_percent("recrawl_jd", 5, 10), 30)
        self.assertEqual(_recrawl_overall_percent("screen_b", 0, 10), 60)
        self.assertEqual(_recrawl_overall_percent("screen_b", 98, 100), 99)
        self.assertEqual(_recrawl_overall_percent("screen_b", 100, 100), 100)
        self.assertEqual(_recrawl_overall_percent("recrawl_ai", 5, 10), 80)
        self.assertEqual(_recrawl_overall_percent("done", 0, 10), 100)

    def test_run_search_emits_real_completed_combo_percent(self):
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        class TwoComboSource:
            platform = "boss"

            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, plan_item, *, on_page_completed=None):
                return SourceOutcome.success(jobs=[{
                    "job_id": f"job-{plan_item['keyword']}",
                    "title": plan_item["keyword"],
                }])

        events = []
        with mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
             mock.patch("webui.pipeline_exec.close_debug_chrome"):
            run_search(
                {"keyword": "前端,后端", "city": ["上海"]},
                TwoComboSource(), pages=1, sleeper=lambda _seconds: None,
                progress=events.append, close_chrome_on_success=False,
            )
        by_stage = {}
        for event in events:
            by_stage.setdefault(event["stage"], []).append(event["overall_percent"])
        self.assertEqual(by_stage["searching"][0], 0)
        self.assertEqual(by_stage["combo_done"][0], 50)
        self.assertEqual(by_stage["waiting"][0], 50)
        self.assertEqual(by_stage["searching"][1], 50)
        self.assertEqual(by_stage["combo_done"][1], 100)
        self.assertEqual(by_stage["done"][-1], 100)

    def test_run_search_combo_failure_does_not_advance_percent(self):
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        class MixedSource:
            platform = "boss"

            def __init__(self):
                self.calls = 0

            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, _plan_item, *, on_page_completed=None):
                self.calls += 1
                if self.calls == 1:
                    return SourceOutcome.failure(
                        failed_code="source_unknown_error", safe_log="普通失败",
                    )
                return SourceOutcome.success(jobs=[{"job_id": "j2", "title": "后端"}])

        events = []
        with mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
             mock.patch("webui.pipeline_exec.close_debug_chrome"):
            run_search(
                {"keyword": "前端,后端", "city": ["上海"]},
                MixedSource(), pages=1, sleeper=lambda _seconds: None,
                progress=events.append, close_chrome_on_success=False,
            )
        failed_events = [event for event in events if event["stage"] == "combo_failed"]
        done_events = [event for event in events if event["stage"] == "done"]
        self.assertEqual(failed_events[0]["overall_percent"], 0)
        self.assertEqual(done_events[-1]["overall_percent"], 100)

    def test_run_search_emits_page_level_percent_and_message(self):
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        class PageSource:
            platform = "boss"

            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, plan_item, *, on_page_completed=None):
                if on_page_completed is not None:
                    on_page_completed({
                        "kind": "page_completed", "combo_key": "Python|北京",
                        "keyword": "Python", "city": "北京", "page": 2,
                        "target_pages": 10, "jobs_delta": 15, "jobs_count": 30,
                        "has_more": True, "resume_page": 3, "last_completed_page": 2,
                    })
                return SourceOutcome.success(
                    jobs=[{"job_id": "j1", "title": "工程师"}])

        events = []
        with mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
             mock.patch("webui.pipeline_exec.close_debug_chrome"):
            run_search(
                {"keyword": "Python", "city": ["北京"]},
                PageSource(), pages=10, sleeper=lambda _seconds: None,
                progress=events.append, close_chrome_on_success=False,
            )
        page_events = [event for event in events if event["stage"] == "page_done"]
        self.assertEqual(len(page_events), 1)
        self.assertEqual(page_events[0]["overall_percent"], 18)
        self.assertEqual(page_events[0]["page"], 2)
        self.assertEqual(page_events[0]["target_pages"], 10)
        self.assertIn("第 2/10 页", page_events[0]["message"])

    def test_run_search_page_persistence_failure_hard_stops(self):
        from webui.pipeline_exec import run_search
        from webui.source import PageEventPersistenceError, SourceOutcome

        class PageFailSource:
            platform = "boss"

            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, plan_item, *, on_page_completed=None):
                if on_page_completed is not None:
                    on_page_completed({
                        "kind": "page_completed", "combo_key": "Python|北京",
                        "page": 1, "target_pages": 10, "jobs_count": 0,
                    })
                return SourceOutcome.success(jobs=[{"job_id": "j1"}])

        def fail_persist(_event):
            raise PageEventPersistenceError("disk full")

        with mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
             mock.patch("webui.pipeline_exec.close_debug_chrome"):
            result = run_search(
                {"keyword": "Python", "city": ["北京"]},
                PageFailSource(), pages=10, sleeper=lambda _seconds: None,
                on_page_completed=fail_persist, close_chrome_on_success=False,
            )
        self.assertTrue(result["hard_stop"])
        self.assertEqual(result["hard_stop_code"], "internal_error")
        self.assertIn("页级快照持久化失败", result["error"])


class FrozenConfigDigestTests(unittest.TestCase):
    """SPEC011 T005: 证明 list/detail/rough/fine/recrawl 阶段使用同一冻结配置摘要。

    这些测试在 T006 完成前应失败（RED），因为当前流水线在运行时从 JSON 文件
    晚绑定读取配置，而非使用任务创建时冻结的快照。
    """

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = _authed_test_client(self.app)
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token

    def tearDown(self):
        self.temp.cleanup()

    def test_run_search_accepts_execution_config_snapshot(self):
        """run_search 必须接受 execution_config 参数，而非运行时读 JSON。"""
        from webui.execution_config import ExecutionConfigSnapshot
        from webui import pipeline_exec

        ExecutionConfigSnapshot.create({
            "inter_combo_delay": 10.0,
            "detail_batch_size": 15,
            "detail_interval": 2.0,
            "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0,
            "screen_batch_size": 50,
            "screen_concurrency": 5,
            "match_batch_size": 4,
            "match_concurrency": 10,
        })
        # run_search 应接受 execution_config 参数
        import inspect
        sig = inspect.signature(pipeline_exec.run_search)
        self.assertIn("execution_config", sig.parameters,
                        "run_search 必须接受 execution_config 参数")

    def test_fetch_job_details_accepts_execution_config_snapshot(self):
        """fetch_job_details 必须接受 execution_config 参数。"""
        import inspect
        from webui import pipeline_exec

        sig = inspect.signature(pipeline_exec.fetch_job_details)
        self.assertIn("execution_config", sig.parameters,
                        "fetch_job_details 必须接受 execution_config 参数")

    def test_screen_jobs_accepts_execution_config_snapshot(self):
        """screen_jobs 必须接受 execution_config 参数。"""
        import inspect
        from webui import ai as ai_module

        sig = inspect.signature(ai_module.screen_jobs)
        self.assertIn("execution_config", sig.parameters,
                        "screen_jobs 必须接受 execution_config 参数")

    def test_match_jds_accepts_execution_config_snapshot(self):
        """match_jds 必须接受 execution_config 参数。"""
        import inspect
        from webui import ai as ai_module

        sig = inspect.signature(ai_module.match_jds)
        self.assertIn("execution_config", sig.parameters,
                        "match_jds 必须接受 execution_config 参数")

    def test_run_search_uses_frozen_config_not_json(self):
        """提供 execution_config 时，run_search 不应读取 advanced_settings.json。"""
        from webui.execution_config import ExecutionConfigSnapshot
        from webui import pipeline_exec

        config = ExecutionConfigSnapshot.create({
            "inter_combo_delay": 42.0,
            "detail_batch_size": 7,
            "detail_interval": 3.0,
            "detail_reset_every": 2,
            "detail_batch_cooldown": 8.0,
            "screen_batch_size": 25,
            "screen_concurrency": 3,
            "match_batch_size": 2,
            "match_concurrency": 4,
        })

        captured_config = {}

        def fake_scrape(params, source, **kwargs):
            # 捕获实际使用的配置摘要
            ec = kwargs.get("execution_config")
            if ec is not None:
                captured_config["digest"] = ec.config_digest
            return {"jobs": [], "details_path": None, "events_path": None}

        source = mock.MagicMock()

        with mock.patch.object(pipeline_exec, "load_advanced_settings") as mock_load, \
             mock.patch.object(pipeline_exec, "boss") as mock_boss:
            mock_load.return_value = {"inter_combo_delay": 999}  # 不同的值
            mock_boss.scrape_jobs = fake_scrape
            mock_boss.ensure_chrome_running = mock.Mock(return_value=True)

            try:
                pipeline_exec.run_search(
                    {"keyword": "test", "city": ["北京"], "pages": 3},
                    source,
                    pages=3,
                    execution_config=config,
                )
            except TypeError:
                # 如果 execution_config 参数不存在，会抛 TypeError — 这是 RED 预期
                self.fail("run_search 不接受 execution_config 参数 (T006 未完成)")

            # 如果执行成功，验证使用了冻结的配置
            if "digest" in captured_config:
                self.assertEqual(captured_config["digest"], config.config_digest)
            # load_advanced_settings 不应被调用
            mock_load.assert_not_called()

    def test_pipeline_task_stores_config_digest(self):
        """真实启动从后端 scope/selection 冻结并存储两个摘要。"""
        from webui.execution_config import ExecutionConfigSnapshot

        config = ExecutionConfigSnapshot.create({
            "inter_combo_delay": 10.0,
            "detail_batch_size": 15,
            "detail_interval": 2.0,
            "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0,
            "screen_batch_size": 50,
            "screen_concurrency": 5,
            "match_batch_size": 4,
            "match_concurrency": 10,
        })

        self.app.config["TASK_STORE"].save_custom_config(config.to_dict())
        preview = self.client.post("/api/search-scope/preview", json={
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post("/api/execute-search", json={
                "script_params": {
                    "keyword": "Python",
                    "city": ["上海"],
                    "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
        submit.assert_called_once()

        # 任务应被接受并存储配置摘要
        self.assertEqual(resp.status_code, 200)
        task_id = resp.get_json()["task_id"]

        # 查询任务进度时应返回配置摘要
        progress = self.client.get(f"/api/search-progress/{task_id}").get_json()
        self.assertIn("config_digest", progress,
                        "任务进度必须包含 config_digest")
        self.assertEqual(progress["config_digest"], config.config_digest)
        self.assertEqual(progress["scope_digest"], preview["scope_digest"])
        submitted = submit.call_args.args
        self.assertEqual(submitted[3].config_digest, config.config_digest)
        self.assertEqual(submitted[4].scope_digest, preview["scope_digest"])
        persisted = self.app.config["TASK_STORE"].get_screening_run(task_id)
        self.assertEqual(
            persisted["execution_params"]["execution_config"]["config_digest"],
            config.config_digest,
        )
        self.assertEqual(
            persisted["execution_params"]["frozen_scope"]["scope_digest"],
            preview["scope_digest"],
        )

    def test_changing_settings_after_task_start_does_not_affect_stages(self):
        """Scenario B: 任务启动后修改正式设置不影响任何阶段。"""
        from webui.execution_config import ExecutionConfigSnapshot

        config_a = ExecutionConfigSnapshot.create({
            "inter_combo_delay": 10.0,
            "detail_batch_size": 15,
            "detail_interval": 2.0,
            "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0,
            "screen_batch_size": 50,
            "screen_concurrency": 5,
            "match_batch_size": 4,
            "match_concurrency": 10,
        })

        self.app.config["TASK_STORE"].save_custom_config(config_a.to_dict())
        preview = self.client.post("/api/search-scope/preview", json={
            "keywords": ["Python"], "scope_kind": "cities",
            "cities": ["上海"], "pages_per_combination": 1,
        }).get_json()["scope"]
        # 创建任务使用后端当前选择解析出的 config_a
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post("/api/execute-search", json={
                "script_params": {
                    "keyword": "Python",
                    "city": ["上海"],
                    "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
        submit.assert_called_once()
        task_id = resp.get_json()["task_id"]

        # 保存不同的设置（config_b）
        self.client.post("/api/advanced-settings", json={
            "settings": {
                "pages": 1,
                "inter_combo_delay": 99.0,
                "detail_batch_size": 99,
                "detail_interval": 99,
                "detail_reset_every": 99,
                "detail_batch_cooldown": 99,
                "screen_batch_size": 99,
                "screen_concurrency": 99,
                "match_batch_size": 99,
                "match_concurrency": 99,
            }
        })

        # 任务仍应使用 config_a 的摘要
        progress = self.client.get(f"/api/search-progress/{task_id}").get_json()
        self.assertEqual(progress.get("config_digest"), config_a.config_digest,
                         "任务启动后修改设置不应改变已冻结的配置摘要")


class B073TaskAccountRoleTests(unittest.TestCase):
    """Spec 038 B091：BOSS 任务按池解析冻结 browser_account。

    FR-021：旧 R1/R2 角色互斥 schema 已弃用，新 schema 是 R1/R2 共用账号池。
    任务创建时按 ``pool.selected`` 取第一个选中账号冻结（沿用 B073 续跑冻结语义）；
    智联也走同一池解析（不再有 R1/R2 角色差异）；登录态不可用降级 fallback。
    """

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = _authed_test_client(self.app)
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()
        from webui.pipeline_exec import reset_browser_accounts_path
        reset_browser_accounts_path()

    def _set_only_selected(self, account_id: str) -> None:
        """把目标账号设为唯一选中（其他账号取消选中），用新 pool schema。"""
        from webui.pipeline_exec_accounts import (
            load_browser_accounts, save_browser_accounts, update_account_pool,
        )
        accounts = load_browser_accounts()
        for aid in list(accounts.keys()):
            accounts = update_account_pool(
                accounts, aid, selected=(str(aid) == str(account_id)))
        save_browser_accounts(accounts)

    def _create_search_task(self, platform: str):
        from webui.execution_config import ExecutionConfigSnapshot
        ExecutionConfigSnapshot.create({
            "inter_combo_delay": 10.0, "detail_batch_size": 15,
            "detail_interval": 2.0, "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0, "detail_tab_pool_size": 5,
            "screen_batch_size": 50, "screen_concurrency": 5,
            "match_batch_size": 4, "match_concurrency": 10,
        })
        preview = self.client.post("/api/search-scope/preview", json={
            "keywords": ["Python"], "scope_kind": "cities",
            "cities": ["上海"], "pages_per_combination": 1,
            "platform": platform,
        }).get_json()["scope"]
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit"):
            resp = self.client.post("/api/execute-search", json={
                "platform": platform,
                "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
                "scope_digest": preview["scope_digest"],
            })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        task_id = resp.get_json()["task_id"]
        return self.store.get_screening_run(task_id)

    def test_boss_list_task_freezes_pool_first_selected(self):
        # b 设为唯一选中 → 任务冻结 browser_account == "b"
        self._set_only_selected("b")
        persisted = self._create_search_task("boss")
        self.assertEqual(
            persisted["execution_params"]["browser_account"], "b")

    def test_boss_list_task_freezes_default_first_selected(self):
        # 默认账号簿 a 是第一个 selected → 任务冻结 a
        persisted = self._create_search_task("boss")
        self.assertEqual(
            persisted["execution_params"]["browser_account"], "a")

    def test_boss_list_task_downgrades_when_pool_account_login_missing(self):
        # b 唯一 selected 但登录态 not_logged_in → fallback=a（兜底到内置账号）
        self._set_only_selected("b")
        with mock.patch(
                "scripts.login_state_cache.read_cached_state",
                return_value="not_logged_in"):
            persisted = self._create_search_task("boss")
        self.assertEqual(
            persisted["execution_params"]["browser_account"], "a")

    def test_zhilian_task_uses_pool_first_selected(self):
        # 智联也走同一池解析（FR-020：两平台通吃；旧"智联不受 R1/R2 影响"已不适用）
        self._set_only_selected("b")
        persisted = self._create_search_task("zhilian")
        self.assertEqual(
            persisted["execution_params"]["browser_account"], "b")


class TuningLeaseOrdinaryTaskConflictTests(unittest.TestCase):
    """SPEC011 T015 RED: 实验租约与普通任务启动路径冲突。

    覆盖 FR-035、SC-004、state-machine.md 第 4 节。
    租约被持有时所有普通任务启动路径（execute-search、ai-screen、
    recrawl/continue、task/continue）必须返回 409。
    """

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = _authed_test_client(self.app)
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()

    def _hold_lease(self):
        """通过 store 直接 claim 租约，模拟实验持有。"""
        self.store.claim_tuning_lease(
            experiment_id="exp-conflict-1",
            round_id="round-conflict-1",
            owner_token="test-owner-token",
        )

    def _release_lease(self):
        self.store.release_tuning_lease(owner_token="test-owner-token")

    # -- execute-search ------------------------------------------------

    def test_execute_search_blocked_when_lease_held(self):
        """FR-035: 租约持有时 /api/execute-search 必须返回 409。"""
        self._hold_lease()
        resp = self.client.post("/api/execute-search", json={
            "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
        })
        self.assertEqual(resp.status_code, 409, "租约持有时必须返回 409")
        body = resp.get_json()
        self.assertFalse(body.get("ok"), "响应 ok 必须为 false")
        self.assertIn("lease", body.get("error", "").lower() + body.get("error_code", "").lower(),
                      "错误必须表明是租约冲突")

    def test_execute_search_allowed_when_lease_free(self):
        """FR-035: 无租约时 /api/execute-search 可启动。"""
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post("/api/execute-search", json={
                "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
            })
        self.assertEqual(resp.status_code, 200, "无租约时 execute-search 应被接受")
        submit.assert_called_once()

    def test_execute_search_allowed_after_lease_released(self):
        """FR-035: 租约释放后 execute-search 可启动。"""
        self._hold_lease()
        self._release_lease()
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post("/api/execute-search", json={
                "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
            })
        self.assertEqual(resp.status_code, 200, "租约释放后 execute-search 应被接受")
        submit.assert_called_once()

    # -- ai-screen -----------------------------------------------------

    def test_ai_screen_blocked_when_lease_held(self):
        """FR-035: 租约持有时 /api/ai-screen 必须返回 409。"""
        # 预置一个已完成的抓取任务
        scrape_task_id = self._seed_done_scrape_task()
        self._hold_lease()
        resp = self.client.post("/api/ai-screen", json={
            "screening_fields": {"city": ["上海"]},
            "profile_summary": "测试",
            "scrape_task_id": scrape_task_id,
        })
        self.assertEqual(resp.status_code, 409, "租约持有时 ai-screen 必须返回 409")

    # -- recrawl/continue ---------------------------------------------

    def test_recrawl_continue_blocked_when_lease_held(self):
        """FR-035: 租约持有时 /api/recrawl/continue 必须返回 409。"""
        run_id = self._seed_paused_recrawl_run()
        self._hold_lease()
        resp = self.client.post(f"/api/recrawl/continue/{run_id}", json={})
        self.assertEqual(resp.status_code, 409, "租约持有时 recrawl/continue 必须返回 409")

    # -- task/continue (统一入口) ------------------------------------

    def test_task_continue_blocked_when_lease_held(self):
        """FR-035: 租约持有时 /api/task/continue 必须返回 409。"""
        run_id = self._seed_paused_recrawl_run()
        self._hold_lease()
        resp = self.client.post(f"/api/task/continue/{run_id}", json={})
        self.assertEqual(resp.status_code, 409, "租约持有时 task/continue 必须返回 409")

    # -- 辅助方法 ------------------------------------------------------

    def _seed_done_scrape_task(self) -> str:
        """预置一个已完成的抓取任务，供 ai-screen 启动。"""
        task_id = "scrape-done-1"
        self.app.config["PIPELINE_TASKS"][task_id] = {
            "id": task_id, "kind": "scrape", "status": "done",
            "progress": 100, "logs": [], "error": "",
            "result": {"ok": True, "jobs": [], "total_scraped": 0,
                       "total_matched": 0, "completed_combos": [],
                       "error": ""},
            "started_at": None, "finished_at": None,
        }
        return task_id

    def _seed_paused_recrawl_run(self) -> str:
        """预置一个 paused 状态的 recrawl run，供 continue 端点使用。"""
        run_id = "recrawl-paused-1"
        self.store.create_screening_run(
            run_id, source_count=10,
            execution_params={
                "source_run_id": "src-1",
                "job_ids": ["j1", "j2"],
                "profile_summary": "测试",
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="recrawl_jd",
        )
        return run_id


if __name__ == "__main__":
    unittest.main()
