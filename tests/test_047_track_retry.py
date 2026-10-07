"""047 US2：failed 单轨 retry 的 store/服务一体回归（红测先行）。

契约（contracts/flow-recovery.md C2）：
- 精确画像/轨道 + 当前 failed 版本（expected_run_id/expected_updated_at）CAS；
- 同一失败版本并发两次请求最多一个成功 claim；
- claim 失败不得创建活体/不改旧诊断；新 run 与绑定同事务；
- 跨画像、旧 run、旧 updated_at 一律 409 语义（FlowConflictError）；
- 兄弟轨道与其结果完全不动。
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest
from types import SimpleNamespace

from webui.store import TaskStore
from webui.store_flow import FlowConflictError


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s047-retry-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        for pid in ("p1", "p2"):
            with self.store._connection() as conn:
                conn.execute(
                    "INSERT INTO candidate_profiles "
                    "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                    "VALUES (?, ?, '{}', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                    (pid, pid),
                )

    def tearDown(self):
        self.temp.cleanup()

    def make_flow(self, profile="p1"):
        flow = self.store.create_flow(
            profile_id=profile, selection="all", start_key=f"retry-{profile}",
            confirmed_filters={"boss": {}, "zhilian": {}},
        )
        return flow

    def seed_failed_track(self, flow, platform="boss", *, error_code="scrape_failed",
                          with_run=True):
        track = next(t for t in flow["tracks"] if t["platform"] == platform)
        run_id = ""
        if with_run:
            run_id = f"{platform}-failed-run"
            self.store.create_screening_run(
                run_id, profile_id=flow["profile_id"],
                execution_params={
                    "flow_id": flow["id"], "track_id": track["id"], "platform": platform,
                },
            )
            self.store.bind_flow_track_runs(
                flow["id"], platform, profile_id=flow["profile_id"],
                scrape_run_id=run_id,
            )
            self.store.update_screening_run(
                run_id, status="running", current_stage="scrape",
            )
            self.store.update_screening_run(
                run_id, status="failed", current_stage="scrape",
                error_code=error_code, error_reason="平台抓取失败",
            )
        self.store.update_flow_track(
            flow["id"], platform, profile_id=flow["profile_id"],
            status="failed", stage="scrape", error_code=error_code,
            reason="平台抓取失败",
        )
        return track, run_id

    def retry(self, flow, platform, *, run_id="", updated_at=None, profile="p1"):
        return self.store.claim_flow_track_retry(
            flow["id"], platform,
            profile_id=profile,
            expected_run_id=run_id,
            expected_updated_at=updated_at,
        )


class RetryClaimTests(_Fixture):
    def test_claims_exact_failed_version_and_binds_new_run(self):
        flow = self.make_flow()
        track, old_run = self.seed_failed_track(flow, "boss")
        current = next(t for t in self.store.get_flow(flow["id"], profile_id="p1")["tracks"]
                       if t["id"] == track["id"])
        claim = self.retry(
            flow, "boss", run_id=old_run, updated_at=current["updated_at"],
        )
        self.assertTrue(claim["claimed"])
        self.assertEqual(claim["track_id"], track["id"])
        self.assertEqual(claim["retry_of_run_id"], old_run)
        self.assertNotEqual(claim["new_run_id"], old_run)
        # 新执行记录已存在且绑定到 Track
        refreshed = self.store.get_flow(flow["id"], profile_id="p1")
        boss = next(t for t in refreshed["tracks"] if t["platform"] == "boss")
        self.assertEqual(boss["scrape_run_id"], claim["new_run_id"])
        self.assertEqual(boss["status"], "running")
        # 旧失败诊断保留
        old = self.store.get_screening_run(old_run)
        self.assertEqual(old["status"], "failed")

    def test_stale_run_id_conflicts(self):
        flow = self.make_flow()
        track, old_run = self.seed_failed_track(flow, "boss")
        current = next(t for t in self.store.get_flow(flow["id"], profile_id="p1")["tracks"]
                       if t["id"] == track["id"])
        with self.assertRaises(FlowConflictError):
            self.retry(flow, "boss", run_id="some-other-run",
                       updated_at=current["updated_at"])

    def test_stale_updated_at_conflicts(self):
        flow = self.make_flow()
        track, old_run = self.seed_failed_track(flow, "boss")
        with self.assertRaises(FlowConflictError):
            self.retry(flow, "boss", run_id=old_run,
                       updated_at="2020-01-01T00:00:00Z")

    def test_cross_profile_conflicts(self):
        flow = self.make_flow()
        track, old_run = self.seed_failed_track(flow, "boss")
        with self.assertRaises((FlowConflictError, KeyError)):
            self.retry(flow, "boss", run_id=old_run, profile="p2")

    def test_concurrent_retry_yields_single_winner(self):
        flow = self.make_flow()
        track, old_run = self.seed_failed_track(flow, "boss")
        current = next(t for t in self.store.get_flow(flow["id"], profile_id="p1")["tracks"]
                       if t["id"] == track["id"])
        first = self.retry(flow, "boss", run_id=old_run, updated_at=current["updated_at"])
        self.assertTrue(first["claimed"])
        with self.assertRaises(FlowConflictError):
            self.retry(flow, "boss", run_id=old_run, updated_at=current["updated_at"])

    def test_sibling_track_and_result_untouched(self):
        flow = self.make_flow()
        track, old_run = self.seed_failed_track(flow, "boss")
        sibling = next(t for t in flow["tracks"] if t["platform"] == "zhilian")
        before = self.store.get_flow(flow["id"], profile_id="p1")
        before_sibling = next(t for t in before["tracks"] if t["id"] == sibling["id"])
        current = next(t for t in before["tracks"] if t["id"] == track["id"])
        self.retry(flow, "boss", run_id=old_run, updated_at=current["updated_at"])
        after = self.store.get_flow(flow["id"], profile_id="p1")
        after_sibling = next(t for t in after["tracks"] if t["id"] == sibling["id"])
        self.assertEqual(after_sibling["status"], before_sibling["status"])
        self.assertEqual(after_sibling["scrape_run_id"], before_sibling["scrape_run_id"])
        self.assertEqual(after_sibling["screen_run_id"], before_sibling["screen_run_id"])

    def test_no_run_failed_track_claims_with_empty_run(self):
        flow = self.make_flow()
        track, _ = self.seed_failed_track(flow, "boss", with_run=False, error_code="track_submit_failed")
        current = next(t for t in self.store.get_flow(flow["id"], profile_id="p1")["tracks"]
                       if t["id"] == track["id"])
        claim = self.retry(flow, "boss", run_id="", updated_at=current["updated_at"])
        self.assertTrue(claim["claimed"])
        self.assertEqual(claim["retry_of_run_id"], "")

    def test_ai_failed_retry_binds_new_screen_run_and_keeps_scrape_and_result(self):
        """C2：AI failed 换 screen_run_id；source scrape 与旧 result 指针保留。"""
        flow = self.make_flow()
        track = next(t for t in flow["tracks"] if t["platform"] == "boss")
        scrape_id = "boss-scrape-source"
        self.store.create_screening_run(
            scrape_id, profile_id="p1",
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"], "platform": "boss",
            },
        )
        self.store.create_scrape_search_run(
            scrape_id, "p1", platform="boss", flow_id=flow["id"], track_id=track["id"],
        )
        old_screen = "boss-old-ai"
        self.store.create_screening_run(
            old_screen, profile_id="p1",
            frozen_filters={"salary": ["20K-50K"]},
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"], "platform": "boss",
                "scrape_task_id": scrape_id,
            },
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id="p1", scrape_run_id=scrape_id,
        )
        self.store.update_screening_run(old_screen, status="running", current_stage="ai_rough")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id="p1", status="running", stage="ai",
            screen_run_id=old_screen,
        )
        self.store.update_screening_run(
            old_screen, status="failed", current_stage="ai_rough",
            error_code="flow_ai_start_failed", error_reason="AI 筛选启动失败",
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id="p1", status="failed", stage="ai",
            error_code="flow_ai_start_failed", reason="AI 筛选启动失败",
        )
        before = next(t for t in self.store.get_flow(flow["id"], profile_id="p1")["tracks"]
                      if t["id"] == track["id"])
        claim = self.store.claim_flow_track_retry(
            flow["id"], "boss", profile_id="p1",
            expected_track_id=track["id"],
            expected_run_id=old_screen,
            expected_updated_at=before["updated_at"],
            stage="ai",
            attempt_params={"scrape_task_id": scrape_id},
            frozen_filters={"salary": ["20K-50K"]},
        )
        self.assertTrue(claim["claimed"], claim)
        after = next(t for t in self.store.get_flow(flow["id"], profile_id="p1")["tracks"]
                     if t["id"] == track["id"])
        self.assertEqual(after["stage"], "ai")
        self.assertEqual(after["status"], "running")
        self.assertEqual(after["screen_run_id"], claim["new_run_id"])
        # 来源抓取绑定与旧结果指针保持
        self.assertEqual(after["scrape_run_id"], scrape_id)
        self.assertIsNone(after["result_run_id"])
        # 旧 AI 失败记录保留
        self.assertEqual(self.store.get_screening_run(old_screen)["status"], "failed")
        new_run = self.store.get_screening_run(claim["new_run_id"])
        self.assertEqual(new_run["status"], "running")
        self.assertEqual(new_run["execution_params"]["retry_of_run_id"], old_screen)
        self.assertEqual(new_run["execution_params"]["scrape_task_id"], scrape_id)
        self.assertEqual(new_run["record_kind"], "process_log")
        # 兄弟轨道没有变化
        sibling = next(t for t in self.store.get_flow(flow["id"], profile_id="p1")["tracks"]
                       if t["platform"] == "zhilian")
        self.assertEqual(sibling["status"], "queued")

    def test_ai_retry_rejects_stale_expected_track_id(self):
        flow = self.make_flow()
        track = next(t for t in flow["tracks"] if t["platform"] == "boss")
        old_screen = "boss-ai-track-guard"
        self.store.create_screening_run(
            old_screen, profile_id="p1",
            execution_params={"flow_id": flow["id"], "track_id": track["id"], "platform": "boss"},
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id="p1", screen_run_id=old_screen,
        )
        self.store.update_screening_run(old_screen, status="running", current_stage="ai_rough")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id="p1", status="failed", stage="ai",
            screen_run_id=old_screen, error_code="flow_ai_start_failed",
        )
        current = next(t for t in self.store.get_flow(flow["id"], profile_id="p1")["tracks"]
                       if t["id"] == track["id"])
        with self.assertRaises(FlowConflictError):
            self.store.claim_flow_track_retry(
                flow["id"], "boss", profile_id="p1",
                expected_track_id="not-this-track",
                expected_run_id=old_screen,
                expected_updated_at=current["updated_at"],
                stage="ai",
            )

    def test_done_track_is_not_retryable(self):
        flow = self.make_flow()
        track = next(t for t in flow["tracks"] if t["platform"] == "boss")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id="p1", status="done", stage="complete",
        )
        current = next(t for t in self.store.get_flow(flow["id"], profile_id="p1")["tracks"]
                       if t["id"] == track["id"])
        with self.assertRaises(FlowConflictError):
            self.retry(flow, "boss", run_id="", updated_at=current["updated_at"])



class AiTrackRetryServiceTests(_Fixture):
    """047 C2：AI failed retry 复用已有抓取输入，不重抓、不改旧失败/旧结果。"""

    def _seed_ai_failed(self, flow, *, with_jobs=True):
        track = next(t for t in flow["tracks"] if t["platform"] == "boss")
        scrape_id = "boss-scrape-input"
        self.store.create_screening_run(
            scrape_id, profile_id=flow["profile_id"],
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"],
                "platform": "boss", "script_params": {"keyword": "python"},
            },
        )
        self.store.create_scrape_search_run(
            scrape_id, flow["profile_id"], platform="boss",
            flow_id=flow["id"], track_id=track["id"],
        )
        if with_jobs:
            self.store.save_scrape_combo_result(
                scrape_id, "kw|city", [{"job_id": "j1", "title": "岗位"}], ["kw|city"],
            )
        old_screen = "boss-old-ai-run"
        self.store.create_screening_run(
            old_screen, profile_id=flow["profile_id"],
            frozen_filters={"salary": ["20K-50K"]},
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"], "platform": "boss",
                "scrape_task_id": scrape_id,
                "browser_account": "acct-1", "cdp_port": 9222, "profile_key": "pk-1",
                "profile_summary": "摘要", "profile_facts": {"years": 5},
                "cross_platform_dedupe": True,
                "screening_policy_version": "b094-domain-v8",
                "domain_semantic_summary": "summary",
            },
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=flow["profile_id"], scrape_run_id=scrape_id,
        )
        self.store.update_screening_run(old_screen, status="running", current_stage="ai_rough")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=flow["profile_id"], status="running",
            stage="ai", screen_run_id=old_screen,
        )
        self.store.update_screening_run(
            old_screen, status="failed", current_stage="ai_rough",
            error_code="flow_ai_start_failed", error_reason="AI 筛选启动失败",
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=flow["profile_id"], status="failed",
            stage="ai", error_code="flow_ai_start_failed", reason="AI 筛选启动失败",
        )
        return track, scrape_id, old_screen

    def _service(self):
        from types import SimpleNamespace
        import threading
        from webui.flow_service import FlowService
        from webui.flow_submission_service import FlowSubmissionService

        service = FlowService(self.store, platform_enabled=lambda _p: True)
        submitted = []

        class _Lane:
            def submit(self, platform, fn, *args, **kwargs):
                submitted.append((platform, fn, args, kwargs))
                return object()

        tasks = {}
        ctx = SimpleNamespace(
            store=self.store, flow_service=service, lock=threading.RLock(),
            tasks=tasks, platform_executor=_Lane(), backend_version="t",
            run_ai_screen_task=lambda *a, **k: None,
            register_pipeline_task=lambda task_id, kind, **kw: tasks.setdefault(
                task_id, {"kind": kind, "status": "queued", "progress": {}, "logs": [],
                          "stop_event": threading.Event(), "result": None, "error": ""},
            ),
            write_run=lambda *a, **k: None,
            account_for_run=lambda *a, **k: "",
            app=SimpleNamespace(config={"BROWSER_ACCOUNTS_PATH": ""}),
            schedule_pipeline_task_cleanup=lambda *a, **k: None,
            release_worker_resume_claims=lambda *a, **k: None,
        )
        service.operation_context = ctx
        return FlowSubmissionService(ctx, service), ctx, submitted

    def test_ai_failed_retry_resubmits_from_persisted_scrape_input(self):
        flow = self.make_flow()
        track, scrape_id, old_screen = self._seed_ai_failed(flow)
        submission, ctx, submitted = self._service()
        current = next(t for t in self.store.get_flow(flow["id"], profile_id="p1")["tracks"]
                       if t["id"] == track["id"])
        result = submission.retry_failed_track(
            flow_id=flow["id"], platform="boss", profile_id="p1",
            expected_run_id=old_screen, expected_updated_at=current["updated_at"],
            flow=self.store.get_flow(flow["id"], profile_id="p1"), track=current,
        )
        boss = next(t for t in result["tracks"] if t["platform"] == "boss")
        self.assertEqual(boss["status"], "running")
        self.assertEqual(boss["stage"], "ai")
        new_run_id = boss["screen_run_id"]
        self.assertNotEqual(new_run_id, old_screen)
        # 只提交一个 AI worker，且触点是既有 runner
        self.assertEqual(len(submitted), 1)
        platform, fn, args, _kwargs = submitted[0]
        self.assertEqual(platform, "boss")
        self.assertIs(fn, ctx.run_ai_screen_task)
        self.assertEqual(args[0], new_run_id)
        self.assertEqual(args[3], scrape_id)
        # runner 用新 run 自己的主记录（resume_from_run_id == task_id），不做 REPLACE，
        # 绑定在整个提交前后都保持。
        self.assertEqual(args[4], new_run_id)
        # 来源抓取输入与旧失败记录保留
        self.assertEqual(boss["scrape_run_id"], scrape_id)
        self.assertEqual(self.store.get_screening_run(old_screen)["status"], "failed")
        new_run = self.store.get_screening_run(new_run_id)
        self.assertEqual(new_run["status"], "running")
        self.assertEqual(new_run["execution_params"]["retry_of_run_id"], old_screen)
        # 兄弟不动
        sibling = next(t for t in result["tracks"] if t["platform"] == "zhilian")
        self.assertEqual(sibling["status"], "queued")

    def test_ai_retry_without_persisted_scrape_input_is_rejected_without_claim(self):
        flow = self.make_flow()
        track, scrape_id, old_screen = self._seed_ai_failed(flow, with_jobs=False)
        submission, _ctx, submitted = self._service()
        before = self.store.get_flow(flow["id"], profile_id="p1")
        with self.assertRaises(Exception) as caught:
            submission.retry_failed_track(
                flow_id=flow["id"], platform="boss", profile_id="p1",
                expected_run_id=old_screen, expected_updated_at=None,
                flow=before, track=next(t for t in before["tracks"] if t["id"] == track["id"]),
            )
        self.assertEqual(getattr(caught.exception, "error_code", ""), "preflight_resume_unavailable")
        self.assertEqual(submitted, [])
        after = self.store.get_flow(flow["id"], profile_id="p1")
        boss = next(t for t in after["tracks"] if t["platform"] == "boss")
        self.assertEqual(boss["status"], "failed")
        self.assertEqual(boss["screen_run_id"], old_screen)
        self.assertEqual(self.store.get_screening_run(old_screen)["status"], "failed")





class ScrapeRetryInitializationTests(_Fixture):
    """复核 P1-4：抓取 failed 重试不得重复创建主记录、不得丢失重试关联。

    范围：真实 FlowSubmissionService → claim 事务 → 初始化 → 受控 worker 提交。
    前置齐备（提交快照/账号/配置/预检全部就绪）时必须严格成功，且只提交
    一个 worker；初始化不得用 INSERT OR REPLACE 清掉 claim 写入的
    retry_of_run_id 与冻结执行参数。
    """

    def _seed_failed_scrape_with_snapshot(self, flow):
        import json
        track = next(t for t in flow["tracks"] if t["platform"] == "boss")
        old_run = "scrape-failed-retry-init"
        self.store.create_screening_run(
            old_run, profile_id=flow["profile_id"],
            execution_params={
                "flow_id": flow["id"], "track_id": track["id"], "platform": "boss",
                "script_params": {"keyword": "python", "city": ["上海"], "pages": 1},
            },
        )
        self.store.create_scrape_search_run(
            old_run, flow["profile_id"], platform="boss",
            flow_id=flow["id"], track_id=track["id"],
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=flow["profile_id"], scrape_run_id=old_run,
        )
        self.store.update_screening_run(old_run, status="running", current_stage="scrape")
        self.store.update_screening_run(
            old_run, status="failed", current_stage="scrape",
            error_code="scrape_failed", error_reason="平台抓取失败",
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=flow["profile_id"],
            status="failed", stage="scrape", error_code="scrape_failed",
            reason="平台抓取失败",
        )
        # 冻结提交快照：真实运行里由 execute-search 校验通过后写入；此处直接
        # 落库以复现“前置齐备”的 retry 初始化现场（Track 已被上次失败占用
        # run 绑定，无法再走 save_flow_track_submission）。
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE flow_tracks SET submission_snapshot_json = ? WHERE id = ?",
                (
                    json.dumps({
                        "script_params": {
                            "keyword": "python", "city": ["上海"], "pages": 1,
                        },
                    }, ensure_ascii=False),
                    track["id"],
                ),
            )
        return track, old_run

    def _service(self):
        import threading
        from types import SimpleNamespace
        from webui.flow_service import FlowService
        from webui.flow_submission_service import FlowSubmissionService

        # 前置齐备：活动执行配置可解析（stable/small），账号池可用默认账号。
        self.store.select_mode("stable", task_size="small")
        service = FlowService(self.store, platform_enabled=lambda _p: True)
        submitted = []

        class _Lane:
            def submit(self, platform, fn, *args, **kwargs):
                submitted.append((platform, fn, args, kwargs))
                return object()

        tasks = {}
        ctx = SimpleNamespace(
            store=self.store, flow_service=service, lock=threading.RLock(),
            tasks=tasks, platform_executor=_Lane(), backend_version="t",
            run_pipeline_task=lambda *a, **k: None,
            register_pipeline_task=lambda task_id, kind, **kw: tasks.setdefault(
                task_id, {"kind": kind, "status": "queued", "progress": {}, "logs": [],
                          "stop_event": threading.Event(), "result": None, "error": ""},
            ),
            write_run=lambda *a, **k: None,
            account_for_run=lambda *a, **k: "",
            check_resume_block=lambda _candidate: (True, "", ""),
            app=SimpleNamespace(config={
                "BROWSER_ACCOUNTS_PATH": "",
                "RESUME_BLOCK_CHECKER": lambda _c: (True, "", ""),
            }),
            schedule_pipeline_task_cleanup=lambda *a, **k: None,
            release_worker_resume_claims=lambda *a, **k: None,
        )
        service.operation_context = ctx
        return FlowSubmissionService(ctx, service), ctx, submitted

    def test_scrape_retry_initialization_preserves_claim_and_submits_once(self):
        flow = self.make_flow()
        track, old_run = self._seed_failed_scrape_with_snapshot(flow)
        submission, _ctx, submitted = self._service()
        before = self.store.get_flow(flow["id"], profile_id="p1")
        current = next(t for t in before["tracks"] if t["id"] == track["id"])
        result = submission.retry_failed_track(
            flow_id=flow["id"], platform="boss", profile_id="p1",
            expected_run_id=old_run, expected_updated_at=current["updated_at"],
            flow=before, track=current,
        )
        boss = next(t for t in result["tracks"] if t["platform"] == "boss")
        self.assertEqual(boss["status"], "running")
        self.assertEqual(boss["stage"], "scrape")
        new_run_id = boss["scrape_run_id"]
        self.assertNotEqual(new_run_id, old_run)

        # 只提交一个抓取 worker，且触点是真实 runner。
        self.assertEqual(len(submitted), 1)
        platform, fn, args, _kwargs = submitted[0]
        self.assertEqual(platform, "boss")
        self.assertIs(fn, _ctx.run_pipeline_task)
        self.assertEqual(args[0], new_run_id)

        # 初始化后主记录与重试关联仍在（曾经被 INSERT OR REPLACE 清空）。
        new_run = self.store.get_screening_run(new_run_id)
        self.assertEqual(new_run["execution_params"]["retry_of_run_id"], old_run)
        self.assertEqual(
            new_run["execution_params"]["script_params"]["keyword"], "python",
        )
        self.assertEqual(new_run["record_kind"], "process_log")
        # search 账本绑定与 Track 一致；旧失败记录与兄弟不动。
        self.assertEqual(boss["scrape_run_id"], new_run_id)
        self.assertEqual(self.store.get_screening_run(old_run)["status"], "failed")
        sibling = next(t for t in result["tracks"] if t["platform"] == "zhilian")
        self.assertEqual(sibling["status"], "queued")
        self.assertEqual(
            self.store.get_flow(flow["id"], profile_id="p1")["tracks"][1]["status"],
            "queued",
        )



    def test_scrape_retry_initialization_failure_compensates_only_new_attempt(self):
        """初始化失败只补偿本次新尝试：旧失败、关联与绑定保持一致。"""
        flow = self.make_flow()
        track, old_run = self._seed_failed_scrape_with_snapshot(flow)
        submission, _ctx, submitted = self._service()
        before = self.store.get_flow(flow["id"], profile_id="p1")
        current = next(t for t in before["tracks"] if t["id"] == track["id"])
        original_begin = submission.begin_whitebox

        def failing_begin(**kwargs):
            raise RuntimeError("whitebox bootstrap failed")

        submission.begin_whitebox = failing_begin
        with self.assertRaises(Exception):
            submission.retry_failed_track(
                flow_id=flow["id"], platform="boss", profile_id="p1",
                expected_run_id=old_run, expected_updated_at=current["updated_at"],
                flow=before, track=current,
            )
        submission.begin_whitebox = original_begin

        after = self.store.get_flow(flow["id"], profile_id="p1")
        boss = next(t for t in after["tracks"] if t["id"] == track["id"])
        new_run_id = boss["scrape_run_id"]
        self.assertNotEqual(new_run_id, old_run)
        self.assertEqual(boss["status"], "paused", "本次尝试失败只收口为新尝试暂停")
        new_run = self.store.get_screening_run(new_run_id)
        self.assertEqual(new_run["status"], "paused")
        self.assertEqual(
            new_run["execution_params"]["retry_of_run_id"], old_run,
            "补偿不得抹掉 claim 写入的重试关联",
        )
        # 旧失败与兄弟不动；没有 worker 被交出去。
        self.assertEqual(self.store.get_screening_run(old_run)["status"], "failed")
        sibling = next(t for t in after["tracks"] if t["platform"] == "zhilian")
        self.assertEqual(sibling["status"], "queued")
        self.assertEqual(submitted, [])

    def test_concurrent_scrape_retry_submits_exactly_one_worker(self):
        """同一失败版本的两次并发抓取重试：最多一个成功、只提交一个 worker。"""
        import threading
        flow = self.make_flow()
        track, old_run = self._seed_failed_scrape_with_snapshot(flow)
        submission, _ctx, submitted = self._service()
        before = self.store.get_flow(flow["id"], profile_id="p1")
        current = next(t for t in before["tracks"] if t["id"] == track["id"])

        barrier = threading.Barrier(2)
        original_claim = self.store.claim_flow_track_retry

        def gated_claim(*args, **kwargs):
            barrier.wait(timeout=5)
            return original_claim(*args, **kwargs)

        self.store.claim_flow_track_retry = gated_claim
        outcomes = []

        def attempt():
            try:
                result = submission.retry_failed_track(
                    flow_id=flow["id"], platform="boss", profile_id="p1",
                    expected_run_id=old_run, expected_updated_at=current["updated_at"],
                    flow=before, track=current,
                )
                outcomes.append(("ok", result))
            except Exception as exc:  # noqa: BLE001 - loser must be a safe conflict
                outcomes.append(("conflict", type(exc).__name__))

        workers = [threading.Thread(target=attempt) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.store.claim_flow_track_retry = original_claim

        successes = [item for item in outcomes if item[0] == "ok"]
        conflicts = [item for item in outcomes if item[0] == "conflict"]
        self.assertEqual(len(successes), 1, outcomes)
        self.assertEqual(len(conflicts), 1, outcomes)
        self.assertEqual(len(submitted), 1, "并发重试只能提交一个 worker")
        after = self.store.get_flow(flow["id"], profile_id="p1")
        boss = next(t for t in after["tracks"] if t["id"] == track["id"])
        self.assertEqual(boss["status"], "running")
        self.assertEqual(
            self.store.get_screening_run(boss["scrape_run_id"])
            ["execution_params"]["retry_of_run_id"],
            old_run,
        )



class RetryHttpRouteTests(_Fixture):
    """047 C2：真实 Flask 路由 → 真实 service/store → 受控提交边界。"""

    def _client(self):
        from flask import Flask
        from types import SimpleNamespace
        from webui.flow_api import register_flow_routes
        from webui.flow_service import FlowService

        app = Flask(__name__)
        service = FlowService(self.store, platform_enabled=lambda _p: True)
        ctx = SimpleNamespace(store=self.store, flow_service=service)
        register_flow_routes(app, ctx)
        ctx.flow_service = service
        return app.test_client(), ctx

    def test_retry_route_claims_and_submits_through_real_chain(self):
        """复核 P1-4：前置齐备时严格成功，且只提交一个 worker。"""
        import json
        import threading
        flow = self.make_flow()
        track, old_run = self.seed_failed_track(flow, "boss")
        # 前置齐备：冻结提交快照 + 可解析执行配置 + 默认账号池 + 平台车道。
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE flow_tracks SET submission_snapshot_json = ? WHERE id = ?",
                (
                    json.dumps({
                        "script_params": {
                            "keyword": "python", "city": ["上海"], "pages": 1,
                        },
                    }, ensure_ascii=False),
                    track["id"],
                ),
            )
        self.store.select_mode("stable", task_size="small")
        client, ctx = self._client()
        submitted = []
        ctx.run_pipeline_task = lambda *a, **k: None
        ctx.platform_executor = SimpleNamespace(
            submit=lambda platform, fn, *args, **kwargs: submitted.append(
                (platform, fn, args, kwargs)
            ) or object(),
        )
        ctx.register_pipeline_task = lambda task_id, kind: {
            "kind": kind, "status": "queued", "progress": {}, "logs": [], "result": None,
            "error": "", "started_at": 0, "finished_at": None,
            "stop_event": threading.Event(),
        }
        ctx.lock = threading.RLock()
        ctx.write_run = lambda *a, **k: None
        ctx.app = SimpleNamespace(config={"BROWSER_ACCOUNTS_PATH": ""})
        ctx.account_for_run = lambda: ""
        service = ctx.flow_service
        service.operation_context = ctx

        current = next(t for t in self.store.get_flow(flow["id"], profile_id="p1")["tracks"]
                       if t["id"] == track["id"])
        response = client.post(
            f"/api/flows/{flow['id']}/tracks/boss/retry",
            json={
                "profile_id": "p1",
                "expected_run_id": old_run,
                "expected_updated_at": current["updated_at"],
            },
        )
        payload = response.get_json()
        self.assertEqual(response.status_code, 200, payload)
        boss = next(t for t in payload["flow"]["tracks"] if t["platform"] == "boss")
        self.assertEqual(boss["status"], "running")
        self.assertEqual(boss["stage"], "scrape")
        self.assertNotEqual(boss["scrape_run_id"], old_run)
        self.assertEqual(len(submitted), 1, "成功链路只能提交一个 worker")
        self.assertEqual(submitted[0][0], "boss")
        new_run = self.store.get_screening_run(boss["scrape_run_id"])
        self.assertEqual(new_run["execution_params"]["retry_of_run_id"], old_run)

    def test_retry_route_stale_version_conflicts(self):
        flow = self.make_flow()
        track, old_run = self.seed_failed_track(flow, "boss")
        client, ctx = self._client()
        response = client.post(
            f"/api/flows/{flow['id']}/tracks/boss/retry",
            json={
                "profile_id": "p1",
                "expected_run_id": old_run,
                "expected_updated_at": "1999-01-01T00:00:00Z",
            },
        )
        self.assertEqual(response.status_code, 409, response.get_json())
        # 旧失败未被改写
        self.assertEqual(self.store.get_screening_run(old_run)["status"], "failed")

    def test_retry_route_rejects_non_failed_track(self):
        flow = self.make_flow()
        track = next(t for t in flow["tracks"] if t["platform"] == "boss")
        self.store.update_flow_track(
            flow["id"], "boss", profile_id="p1", status="running", stage="scrape",
        )
        client, ctx = self._client()
        response = client.post(
            f"/api/flows/{flow['id']}/tracks/boss/retry",
            json={"profile_id": "p1", "expected_run_id": "", "expected_updated_at": ""},
        )
        self.assertEqual(response.status_code, 409, response.get_json())


if __name__ == "__main__":
    unittest.main()
