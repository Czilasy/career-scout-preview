"""Final B096 worker and Flow closure regression tests."""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from webui.execution_config import ExecutionConfigSnapshot, FrozenTaskScope
from webui.flow_service import FlowService
from webui.flow_task_state import FlowStateClosureError
from webui.runners.ai_screen_task import run_ai_screen_task
from webui.runners.pipeline_task import _sync_flow_track_after_scrape
from webui.store import TaskStore


class B096FinalWorkerReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b096-final-worker-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile_id = "final-worker-profile"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'final', '{}', '{}', '2026-01-01', '2026-01-01')",
                (self.profile_id,),
            )

    def tearDown(self):
        self.temp.cleanup()

    def _context(self, *, jobs=None, integrity="succeeded"):
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="boss",
            start_key=f"final-worker-{integrity}-{len(jobs or [])}",
            confirmed_filters={"boss": {}},
        )
        track = flow["tracks"][0]
        scrape_id = f"{flow['id']}-scrape"
        ai_id = f"{flow['id']}-ai"
        config = ExecutionConfigSnapshot({
            "inter_combo_delay": 0,
            "detail_batch_size": 1,
            "detail_interval": 0,
            "detail_reset_every": 1,
            "detail_batch_cooldown": 0,
            "detail_tab_pool_size": 1,
            "screen_batch_size": 1,
            "screen_concurrency": 1,
            "match_batch_size": 1,
            "match_concurrency": 1,
        })
        scope = FrozenTaskScope(
            keywords=["Python"], scope_kind="cities", cities=["上海"],
            pages_per_combination=1, combination_count=1, planned_pages=1,
            task_size="small", platform="boss",
        )
        params = {
            "platform": "boss", "flow_id": flow["id"],
            "track_id": track["id"], "execution_config": config.to_dict(),
            "frozen_scope": scope.to_dict(),
        }
        self.store.create_screening_run(
            scrape_id, profile_id=self.profile_id, execution_params=params,
        )
        self.store.create_scrape_search_run(
            scrape_id, self.profile_id, platform="boss", flow_id=flow["id"],
            track_id=track["id"],
        )
        self.store.claim_flow_track_submission(
            flow["id"], "boss", profile_id=self.profile_id,
        )
        self.store.bind_flow_track_runs(
            flow["id"], "boss", profile_id=self.profile_id,
            scrape_run_id=scrape_id,
        )
        self.store.update_flow_track(
            flow["id"], "boss", profile_id=self.profile_id,
            status="running", stage="scrape",
        )
        self.store.update_screening_run(scrape_id, status="succeeded")
        jobs = [dict(job) for job in (jobs or [])]
        tasks = {
            scrape_id: {
                "kind": "scrape", "status": "done", "platform": "boss",
                "profile_id": self.profile_id, "result": {
                    "ok": True, "jobs": jobs, "dropped": [],
                    "total_scraped": len(jobs), "combinations": 1,
                },
            },
            ai_id: {
                "kind": "ai_screen", "status": "queued", "platform": "boss",
                "profile_id": self.profile_id, "source_task_id": scrape_id,
                "logs": [], "progress": {}, "error": "",
                "stop_event": threading.Event(),
            },
        }
        app = SimpleNamespace(config={"RESULT_DIR": self.temp.name})
        ctx = SimpleNamespace(
            app=app, store=self.store, flow_service=FlowService(self.store),
            tasks=tasks, lock=threading.RLock(), operational_errors=(Exception,),
            backend_version="test", event_stage_names={}, screen_stage_messages={},
            activate_task_browser=lambda _task_id: None,
            is_user_finished=lambda _task_id: False,
            account_for_run=lambda *_args: "a",
            ensure_scrape_source=lambda _task_id: tasks[scrape_id],
            write_run=lambda run_id, **kwargs: self.store.update_screening_run(run_id, **kwargs),
            clear_auto_screen=lambda _task_id: None,
            schedule_pipeline_task_cleanup=lambda _task_id: None,
            release_worker_resume_claims=lambda _task: None,
            remove_jd_checkpoint=lambda _path: None,
            jd_checkpoint_path=lambda _result_dir, _task_id: str(pathlib.Path(self.temp.name) / "jd.json"),
            load_jd_checkpoint=lambda _path: {},
            save_jd_checkpoint=lambda _path, _value: None,
            prune_history_best_effort=lambda: None,
            screen_overall_percent=lambda _stage, _current, _total: 0,
            msg_user_finished="已结束",
            msg_user_stopped_screen="已停止",
        )
        self.store.get_ai_settings = lambda: {"endpoint_url": "https://ai.test", "model": "test"}
        self.store.get_credential_ref = lambda: None
        self.store.finalize_run_status = lambda _run_id: "succeeded"
        self.store.get_whitebox_run = lambda *_args: None
        return ctx, flow, scrape_id, ai_id

    def _patch_worker_stages(self, *, integrity="succeeded"):
        job = {"job_id": "job-1", "platform_job_id": "job-1", "title": "Python"}

        def rough(*_args, **_kwargs):
            return ([dict(job)], []) if integrity != "empty" else ([], [])

        def jd(_ctx, _task_id, enriched, *_args, **_kwargs):
            for item in enriched:
                item["jd"] = "负责 Python 开发"
            return ({item["job_id"]: item.get("jd", "") for item in enriched}, [])

        def fine(_ctx, _task_id, enriched, *_args, **_kwargs):
            for item in enriched:
                item["verdict"] = "match"
            return len(enriched)

        fake_whitebox = mock.MagicMock()
        fake_whitebox.begin.return_value = SimpleNamespace(id="wb-final")
        fake_whitebox.finalize.return_value = {
            "conclusion": integrity,
            "evidence_complete": integrity in {"succeeded", "empty"},
            "primary_code": "whitebox_incomplete" if integrity not in {"succeeded", "empty"} else "",
            "primary_reason": "AI 白箱证据不足" if integrity not in {"succeeded", "empty"} else "",
        }
        return mock.patch.multiple(
            "webui.runners.ai_screen_task",
            run_rough_stage=rough,
            run_jd_stage=jd,
            run_fine_stage=fine,
        ), mock.patch("webui.whitebox.WhiteboxService", return_value=fake_whitebox), mock.patch(
            "webui.ai.is_ai_available", return_value=True,
        )

    def test_result_binding_failure_closes_track_and_propagates_safe_error(self):
        ctx, flow, scrape_id, ai_id = self._context(jobs=[{"job_id": "job-1"}])
        stage_patch, whitebox_patch, ai_patch = self._patch_worker_stages()
        with stage_patch, whitebox_patch, ai_patch, mock.patch(
            "webui.result_rounds.save_finished_round", return_value="snapshot-1",
        ), mock.patch.object(
            ctx.flow_service, "mark_result_ready", side_effect=RuntimeError("db failure"),
        ), self.assertRaises(FlowStateClosureError):
            run_ai_screen_task(ctx, ai_id, {}, "profile", scrape_id)
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["status"], "failed")
        self.assertIsNone(track["result_run_id"])

    def test_zero_jobs_closes_track_instead_of_leaving_running(self):
        ctx, flow, scrape_id, ai_id = self._context(jobs=[{"job_id": "job-1"}], integrity="empty")
        stage_patch, whitebox_patch, ai_patch = self._patch_worker_stages(integrity="empty")
        with stage_patch, whitebox_patch, ai_patch, mock.patch(
            "webui.result_rounds.save_finished_round", return_value=None,
        ):
            run_ai_screen_task(ctx, ai_id, {}, "profile", scrape_id)
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["status"], "failed")

    def test_unverifiable_ai_does_not_publish_screened_result(self):
        ctx, flow, scrape_id, ai_id = self._context(jobs=[{"job_id": "job-1"}], integrity="unverifiable")
        stage_patch, whitebox_patch, ai_patch = self._patch_worker_stages(integrity="unverifiable")
        with stage_patch, whitebox_patch, ai_patch, mock.patch(
            "webui.result_rounds.save_finished_round", return_value="snapshot-uncertain",
        ) as save_round, mock.patch.object(
            ctx.flow_service, "mark_result_ready",
        ) as mark_ready:
            run_ai_screen_task(ctx, ai_id, {}, "profile", scrape_id)
        track = self.store.get_flow(flow["id"], profile_id=self.profile_id)["tracks"][0]
        self.assertEqual(track["status"], "failed")
        self.assertIsNone(track["result_run_id"])
        mark_ready.assert_not_called()
        save_round.assert_not_called()

    def test_scrape_double_closure_failure_is_observable(self):
        class Marker:
            def mark_scrape_complete(self, **_kwargs):
                raise RuntimeError("marker failure")

            def fail_track(self, **_kwargs):
                raise RuntimeError("compensation failure")

        class Store:
            def get_screening_run(self, _task_id):
                return {
                    "execution_params": {"flow_id": "flow-1", "platform": "boss"},
                    "platform": "boss",
                    "profile_id": "profile-1",
                }

        ctx = SimpleNamespace(store=Store(), flow_service=Marker())
        with self.assertRaises(FlowStateClosureError):
            _sync_flow_track_after_scrape(ctx, "scrape-1", "done")

    def test_pipeline_runner_remains_a_thin_facade(self):
        path = pathlib.Path(__file__).parents[1] / "webui" / "runners" / "pipeline_task.py"
        self.assertLess(len(path.read_text(encoding="utf-8").splitlines()), 600)


if __name__ == "__main__":
    unittest.main()


class B096V2StructuralGuardTests(unittest.TestCase):
    """T001: freeze V2 boundaries before implementation."""

    ROOT = pathlib.Path(__file__).resolve().parents[1]
    #: SPEC 046 V2 之前那一个提交（`chore(release): bump version to 1.9.5`）：
    #: 边界闸以此为基准核对「本链改过的全部文件」，而不只是未提交的那一层。
    V2_CHAIN_BASE_COMMIT = "54164e6"
    BASELINE_PATH = (
        ROOT / "specs" / "046-parallel-platform-flow" / "v2"
        / "pre-v2-protected.sha256"
    )
    PARALLEL_PROGRESS_PATH = (
        ROOT / "webui" / "src" / "components" / "ParallelPlatformProgress.vue"
    )
    DISCOVERY_VIEW_PATH = ROOT / "webui" / "src" / "views" / "DiscoveryView.vue"

    V2_ALLOWED_PATHS = {
        ".specify/memory/constitution.md",
        "CHANGELOG.md",
        "README.md",
        "specs/046-parallel-platform-flow/INDEX.md",
        "specs/046-parallel-platform-flow/v2/plan.md",
        "specs/046-parallel-platform-flow/v2/research.md",
        "specs/046-parallel-platform-flow/v2/data-model.md",
        "specs/046-parallel-platform-flow/v2/quickstart.md",
        "specs/046-parallel-platform-flow/v2/contracts/condition-snapshot.md",
        "specs/046-parallel-platform-flow/v2/contracts/flow-presentation.md",
        "specs/046-parallel-platform-flow/v2/checklists/requirements.md",
        "specs/046-parallel-platform-flow/v2/contracts/filter-mapping.md",
        "specs/046-parallel-platform-flow/v2/spec.md",
        "specs/046-parallel-platform-flow/v2/pre-v2-protected.sha256",
        "specs/046-parallel-platform-flow/v2/tasks.md",
        "tests/test_b096_flow_api.py",
        "tests/test_b096_final_review.py",
        "tests/test_b096_flow_history.py",
        "tests/test_search_packages.py",
        "tests/webui_store/test_store_migrations.py",
        "webui/flow_api.py",
        "webui/search_packages.py",
        "webui/store_search_packages.py",
        "webui/store_migrations.py",
        "webui/store_migrations_v8.py",
        "webui/src/parallelFilterMapping.ts",
        "webui/src/types.ts",
        "webui/src/discovery.ts",
        "webui/src/screenFlow.ts",
        "webui/src/components/OneClickScreenDialog.vue",
        "webui/src/components/ParallelPlatformProgress.vue",
        "webui/src/components/ScreenRoundActions.vue",
        # D-04：轨道动作条收进卡边框内部——落点是既有卡的一个可选 slot。
        "webui/src/components/TaskProgress.vue",
        # D-05：并行轨道模块与底部动作行同宽，横跨 02 页整行。
        "webui/src/styles.css",
        "webui/src/composables/useDiscoveryParallelFlow.ts",
        "webui/src/composables/useScreenRoundFlow.ts",
        "webui/src/composables/useDiscoveryFlowPresentation.ts",
        "webui/src/composables/useDiscoveryIslandBridge.ts",
        "webui/src/composables/useDiscoveryTasks.ts",
        "webui/src/composables/useDiscoveryResults.ts",
        "webui/src/composables/useSearchPackages.ts",
        "webui/src/composables/useTheme.ts",
        "webui/src/styles/theme.css",
        "webui/src/views/DiscoveryView.vue",
        "webui/src/__tests__/parallelFilterMapping.spec.ts",
        "webui/src/__tests__/screenFlow.spec.ts",
        "webui/src/components/__tests__/OneClickScreenDialog.spec.ts",
        "webui/src/components/__tests__/ParallelPlatformProgress.spec.ts",
        "webui/src/composables/__tests__/useDiscoveryParallelFlow.spec.ts",
        "webui/src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts",
        "webui/src/composables/__tests__/useDiscoveryIslandBridge.spec.ts",
        "webui/src/composables/__tests__/useDiscoveryTasks.spec.ts",
        "webui/src/composables/__tests__/useDiscoveryResults.spec.ts",
        "webui/src/composables/__tests__/useSearchPackages.spec.ts",
        "webui/src/composables/__tests__/useTheme.spec.ts",
        "webui/src/views/__tests__/DiscoveryRecovery.spec.ts",
        "webui/src/views/__tests__/DiscoverySearchPackages.spec.ts",
        "webui/src/views/__tests__/DiscoveryView.spec.ts",
        # D-06 / D-07 / D-08 与 Edge Cases「平台禁用点名」的落点登记（2026-10-01 返修批）。
        # 依据是 v2/spec.md 新增的 `## 状态所有权` 与 `## 状态词表` 两节，以及
        # tasks.md 里 Forbidden files 清单尚未按这两节尺子更新这一事实：以规格为准。
        # 逐条登记，只记实际改过且改动由上述条目要求的文件，不是整批放行：
        # - App.vue：D-06 把通知池的轮次身份接成既有轮次令牌（Ref 形参），不在 App 数轮次。
        # - useDiscoveryState.ts：状态词表要求中断与暂停两种性质各自的出路口径；
        #   结果可渲染判定（hasRenderableResult）按状态所有权收进状态层。
        # - useIslandNotices.ts：D-06 轮次身份登记与上一轮告警撤销。
        # - useDiscoveryFlowCoordinator.ts：D-07 冻结快照汇总、D-08 本轮仍在进行说明、
        #   Edge Cases「全部」的平台可用性点名（含恢复配置包路径不做逐平台预检）。
        # - 三个 spec 文件是上述三条行为与点名的验收用例，不是顺手重构：
        #   useDiscoveryExecution.spec.ts / useDiscoveryState.spec.ts 钉 D-06 状态词表
        #   （中断只说「开始新一轮」，不再承诺界面上不存在的动作），
        #   useIslandNotices.spec.ts 钉 D-06 轮次身份与上一轮告警撤销。
        "webui/src/App.vue",
        "webui/src/composables/useDiscoveryState.ts",
        "webui/src/composables/useIslandNotices.ts",
        "webui/src/composables/useDiscoveryFlowCoordinator.ts",
        "webui/src/composables/__tests__/useDiscoveryExecution.spec.ts",
        "webui/src/composables/__tests__/useDiscoveryState.spec.ts",
        "webui/src/composables/__tests__/useIslandNotices.spec.ts",
        # 2026-10-01 状态层收口批（FR-015 + 状态词表 A/B 谓词拆分），逐条登记：
        # - useDiscoveryWorkflow.ts：词表禁止「名字叫 LIVE 却装着 paused/interrupted
        #   的清单」，会话快照清单改名并归位问题 B，内容与行为不变。
        # - useScreenRoundFlow.spec.ts：验收用例「中断续跑目标是重新开始 AI 筛选
        #   而非继续」（ai_screen_api.py:350-351 的分层覆写）。
        "webui/src/composables/useDiscoveryWorkflow.ts",
        "webui/src/composables/__tests__/useScreenRoundFlow.spec.ts",
        # 边界闸改按「提交区间 54164e6..HEAD ∪ 工作树」核对后补登记的本链实际改动
        # （T046：允许路径与冻结清单之外零新增差异）。逐条附规格依据，不改判定形式、
        # 不整批放行；依据只取 v2/spec.md 的条文与本链 Known Defects 编号。
        #
        # 后端产品代码：
        # - ai_platform_adapter.py：D-01「哨兵值与具体档互斥」——智联经验 `-1` 是岗位
        #   属性档，不得当字段级不限制，否则冻结条件把岗位刷光。
        "webui/ai_platform_adapter.py",
        # - flow_task_coordinator.py：FR-015（一线动作不得改变另一线）与 `## 状态所有权`
        #   （轨道状态归 flows/flow_tracks 一处）——任务动作与轨道事实必须同一处发布。
        "webui/flow_task_coordinator.py",
        # - platforms_boss.py：Edge Cases「任一平台被系统禁用新建任务时…指出不可用平台」
        #   ——BOSS 侧被停用时要有可读原因，树干与前端显示名投影不动。
        "webui/platforms_boss.py",
        # - platforms_zhilian.py：同上 Edge Cases 的智联侧——禁用原因串要点名平台
        #   （用注册表既有显示名「智联招聘」）、说中文、不含内部字段名；接线形状与
        #   BOSS 一致不动，树干 flow_api.py 与前端显示名投影同样不动。
        "webui/platforms_zhilian.py",
        # - task_state_api.py / task_status.py：`## 状态词表`「活体任务」只算真有 worker
        #   在跑——排队中与已中断/终态一律让时长定格，不再随轮询增长。
        "webui/task_state_api.py",
        "webui/task_status.py",
        # - constants.py / core_api.py：`## 状态所有权`「平台名称、平台字段、平台特例只
        #   允许出现在树枝」——环境检查不再由树干拼装 BOSS 登录项。
        "webui/constants.py",
        "webui/core_api.py",
        #
        # 树枝侧对上面这条树干改动的适配（树枝适配树干，不是树干迁就树枝）：
        # - scripts/boss/constants.py / scripts/boss/smoke.py：BOSS 登录探测项退出共用
        #   环境检查，`## 状态所有权` 平台名边界。
        "scripts/boss/constants.py",
        "scripts/boss/smoke.py",
        #
        # 后端验收用例：
        # - tests/ai/test_ai_platform_filters.py：D-01 的字段级不限制码表与反向投影。
        "tests/ai/test_ai_platform_filters.py",
        # - tests/test_b096_flow_new_round_release.py：FR-015 与 `## 状态词表`——中断轮
        #   没有活体 worker，外壳不得谎报「排队中」把「开始新一轮」永久锁死。
        "tests/test_b096_flow_new_round_release.py",
        # - tests/test_b096_round4_review.py：FR-009/FR-010 与 `## 状态所有权`——真实
        #   双轨持久形状（一 Flow 两 Track、抓取后筛选）的读时派生回归。
        "tests/test_b096_round4_review.py",
        # - tests/test_env_check.py：`## 状态所有权` 平台名边界——环境检查项清单里不再
        #   出现由树干点名的平台登录项。
        "tests/test_env_check.py",
        # - tests/test_resume_continue.py：FR-015 与 Edge Cases「…历史使用已冻结值，
        #   不静默重算」——续跑沿用冻结的登录身份，一条线的恢复不得改写另一条线。
        "tests/test_resume_continue.py",
        # - tests/test_task_state_elapsed_freeze.py：`## 状态词表`——非在跑状态时长定格。
        "tests/test_task_state_elapsed_freeze.py",
        #
        # 前端产品代码（`## 状态所有权`：状态文案与显示名唯一来源是 discovery.ts，
        # 任何组件不得自带一套判定；Edge Cases 的点名也只在这一份权威上生效）：
        "webui/src/components/AccountPoolSheet.vue",
        "webui/src/components/BrowserAccountsDialog.vue",
        # - DynamicIsland.vue：灵动岛不再用三元式猜平台名，未登记身份给中性中文。
        "webui/src/components/DynamicIsland.vue",
        # - EnvCheckDialog.vue：环境检查项只渲染树干那一份清单（同上平台名边界）。
        "webui/src/components/EnvCheckDialog.vue",
        "webui/src/components/JobWorkspace.vue",
        "webui/src/components/ReminderDrawer.vue",
        # - useDiscoverySearch.ts：`## 状态词表` 两个谓词分派（判活 ≠ 本轮未结束）＋
        #   FR-012（人工停留与历史轮不回锁，提示必须跟真实落点）。
        "webui/src/composables/useDiscoverySearch.ts",
        # - useResumeAnalysisFlow.ts：FR-011/FR-012——页面可达性只由一处投影决定，
        #   组件不再直接写步骤状态。
        "webui/src/composables/useResumeAnalysisFlow.ts",
        #
        # 前端验收用例：
        # - discovery.spec.ts：`## 状态词表`/`## 状态所有权` 与 Edge Cases 点名的唯一
        #   权威落点（外壳状态中文口径、平台显示名不默认成任一平台）。
        "webui/src/__tests__/discovery.spec.ts",
        "webui/src/components/__tests__/BrowserAccountsDialog.spec.ts",
        "webui/src/components/__tests__/EnvCheckDialog.spec.ts",
        # - ScreenRoundActions.spec.ts：D-03——并行轨道复用既有动作条，禁用不得扩散到
        #   「结束并保存 / 放弃本轮」。
        "webui/src/components/__tests__/ScreenRoundActions.spec.ts",
        # - TaskProgress.spec.ts：FR-009（复用原有单平台进度、不给百分比）与
        #   `## 状态词表`（终态与中断定格、真在跑才继续走表）。
        "webui/src/components/__tests__/TaskProgress.spec.ts",
        "webui/src/composables/__tests__/useDiscoveryWorkflow.spec.ts",
        "webui/src/composables/__tests__/useResumeAnalysisFlow.spec.ts",
    }

    E2E_FOLLOWUP_ALLOWED_PATHS = frozenset({
        "scripts/zhilian/cdp.py",
        "scripts/zhilian/detail.py",
        "tests/healthy_pipeline/test_pipeline_state.py",
        "tests/source/test_source_zhilian.py",
        "webui/src/composables/discoveryDeps.ts",
        "webui/src/composables/useDiscoveryExecution.ts",
        "webui/pipeline_exec_details.py",
        "webui/source_zhilian_cdp.py",
    })

    def test_baseline_files_keep_pre_v2_hashes(self):
        expected = {}
        for raw in self.BASELINE_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            digest, relative = line.split("  ", 1)
            expected[relative.replace("\\", "/")] = digest
        self.assertEqual(len(expected), 76)
        missing = []
        changed = []
        for relative, digest in expected.items():
            path = self.ROOT / relative
            if not path.is_file():
                missing.append(relative)
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != digest:
                changed.append(relative)
        self.assertEqual(missing, [])
        self.assertEqual(changed, [])

    def _committed_chain_changes(self) -> set[str]:
        """本链已提交的变更文件（`54164e6..HEAD`）。

        只看工作树的那一版查不出已经进过仓库的越界改动：返修批次一路提交上去，
        边界闸到收口时永远「干净」。T046 要的是允许路径与冻结清单之外零新增差异，
        所以基准必须包含提交区间。
        """
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{self.V2_CHAIN_BASE_COMMIT}..HEAD"],
            cwd=self.ROOT, check=True, capture_output=True, text=True,
            encoding="utf-8",
        )
        return {
            line.strip().replace("\\", "/")
            for line in result.stdout.splitlines()
            if line.strip()
        }

    def _working_tree_changes(self) -> set[str]:
        """工作树里尚未提交的变更与未跟踪文件。"""
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=self.ROOT, check=True, capture_output=True, text=True,
            encoding="utf-8",
        )
        paths = set()
        for raw in result.stdout.splitlines():
            path = raw[3:].strip().strip('"').replace("\\", "/")
            if "->" in path:  # 重命名条目取新路径
                path = path.split("->")[-1].strip().strip('"')
            if path.endswith("/"):
                continue
            paths.add(path)
        return paths

    def test_no_chain_or_working_tree_changes_outside_v2_boundary(self):
        """T046「允许路径和冻结清单之外零新增差异」（原 `test_no_new_working_tree_changes_outside_v2_boundary`）。

        基准从「只看工作树」改成「提交区间 `54164e6..HEAD` 的变更文件 ∪ 工作树未提交变更」：
        本链的越界改动大多已经提交，旧写法在收口时看不见它们。断言形式不放宽——
        清单里只要有一条文件既不在冻结基线、也不在逐条登记过的允许路径里，就失败。
        """
        changed = self._committed_chain_changes() | self._working_tree_changes()
        protected = {
            raw.split("  ", 1)[1].strip().replace("\\", "/")
            for raw in self.BASELINE_PATH.read_text(encoding="utf-8").splitlines()
            if raw.strip() and not raw.strip().startswith("#")
        }
        unexpected = sorted(
            path for path in changed
            if path not in protected
            and path not in self.V2_ALLOWED_PATHS
            and path not in self.E2E_FOLLOWUP_ALLOWED_PATHS
        )
        self.assertEqual(unexpected, [])

    def test_e2e_followup_allowlist_is_exact(self):
        self.assertEqual(
            self.E2E_FOLLOWUP_ALLOWED_PATHS,
            frozenset({
                "scripts/zhilian/cdp.py",
                "scripts/zhilian/detail.py",
                "tests/healthy_pipeline/test_pipeline_state.py",
                "tests/source/test_source_zhilian.py",
                "webui/src/composables/discoveryDeps.ts",
                "webui/src/composables/useDiscoveryExecution.ts",
                "webui/pipeline_exec_details.py",
                "webui/source_zhilian_cdp.py",
            }),
        )

    def test_parallel_progress_uses_task_progress_without_fake_steps(self):
        source = self.PARALLEL_PROGRESS_PATH.read_text(encoding="utf-8")
        self.assertIn('from "./TaskProgress.vue"', source)
        self.assertNotIn("35", source)
        self.assertNotIn("65", source)
        self.assertNotIn("100", source)
        self.assertNotIn("grid-template-columns: repeat(2", source)

    # D-03 结构门禁：并行轨道行不许自带动作条与状态判定。
    # 每条负向断言都配一条「复用还在」的正向断言，防止删功能换绿灯。
    TRACK_STATUS_LITERALS = (
        "queued", "running", "pausing", "paused", "interrupted", "failed",
        "stopped", "cancelled", "succeeded", "done", "partial",
        "completed_with_pending", "unavailable",
    )

    def test_parallel_progress_renders_shared_controls_and_not_its_own(self):
        source = self.PARALLEL_PROGRESS_PATH.read_text(encoding="utf-8")
        # 正向：每一行都是既有 TaskProgress + 既有 ScreenRoundActions。
        self.assertIn('import TaskProgress from "./TaskProgress.vue"', source)
        self.assertIn('import ScreenRoundActions from "./ScreenRoundActions.vue"', source)
        self.assertIn("<TaskProgress", source)
        self.assertIn("<ScreenRoundActions", source)
        # 负向：没有裸按钮、没有自写显隐、没有第二套状态判定。
        self.assertEqual(source.count("<button"), 0)
        self.assertNotIn("visibleButtons", source)
        for status in self.TRACK_STATUS_LITERALS:
            self.assertNotIn(f'"{status}"', source, f"轨道行不得自带状态 {status}")
            self.assertNotIn(f"'{status}'", source, f"轨道行不得自带状态 {status}")

    def test_track_actions_still_reach_the_backend(self):
        # 上一条负向断言不许以砍掉动作为代价：轨道动作仍然要能发出去。
        source = self.PARALLEL_PROGRESS_PATH.read_text(encoding="utf-8")
        view = self.DISCOVERY_VIEW_PATH.read_text(encoding="utf-8")
        self.assertIn('emit("action"', source)
        self.assertRegex(source, r"emit\(['\"]finish['\"]")
        self.assertIn('defineEmits(["action", "finish"])', source)
        self.assertIn('@action="parallelFlow.operateTrack"', view)
        self.assertIn("@finish=\"finishPausedTask\"", view)
        # 轨道级收尾复用单平台同一条 run 级路径，不新增端点。
        self.assertNotIn("/api/flows/", source)

    def test_screen_round_actions_style_is_defined_only_in_the_shared_component(self):
        source_root = self.ROOT / "webui" / "src"
        markup_owners = []
        style_owners = []
        for path in source_root.rglob("*"):
            if not path.is_file() or path.suffix not in {".vue", ".css", ".ts", ".tsx"}:
                continue
            if "__tests__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            relative = str(path.relative_to(self.ROOT)).replace("\\", "/")
            if 'class="screen-round-actions"' in source:
                markup_owners.append(relative)
            # 只查这个类名自己的样式规则；别处的前缀后代选择器不算定义处。
            if "\n.screen-round-actions {" in source:
                style_owners.append(relative)
        # 正向：定义处仍然是成熟动作条本体，它照样渲染这片现场并自带这一段样式。
        self.assertEqual(markup_owners, ["webui/src/components/ScreenRoundActions.vue"])
        self.assertEqual(style_owners, ["webui/src/components/ScreenRoundActions.vue"])
        shared = (source_root / "components" / "ScreenRoundActions.vue").read_text(encoding="utf-8")
        self.assertIn("<button", shared)
        # 负向：并行轨道行不许再自带同类名（那意味着第二套动作条回来了）。
        self.assertNotIn(
            "screen-round-actions",
            self.PARALLEL_PROGRESS_PATH.read_text(encoding="utf-8"),
        )

    def test_confirmation_gate_is_removed_from_parallel_sources(self):
        roots = [
            self.ROOT / "webui" / "src" / "components",
            self.ROOT / "webui" / "src" / "composables",
        ]
        offenders = []
        for root in roots:
            for path in root.rglob("*"):
                if path.suffix not in {".ts", ".vue"} or not path.is_file():
                    continue
                source = path.read_text(encoding="utf-8")
                if "platformConfirmed" in source or "我已确认" in source:
                    offenders.append(str(path.relative_to(self.ROOT)))
        self.assertEqual(offenders, [])

    def test_discovery_view_respects_line_red_line(self):
        lines = self.DISCOVERY_VIEW_PATH.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 1200)

    def test_v2_new_modules_exist_and_are_not_empty(self):
        # T046「新模块存在」：V2 引入的三个新模块必须落仓且非空——
        # 并行筛选映射（树枝唯一一份）、Flow 呈现投影（页面可达性唯一来源）、
        # v8 迁移（flows/flow_tracks 的 schema 归属）。
        for relative in (
            "webui/src/parallelFilterMapping.ts",
            "webui/src/composables/useDiscoveryFlowPresentation.ts",
            "webui/store_migrations_v8.py",
        ):
            path = self.ROOT / relative
            self.assertTrue(path.is_file(), f"missing V2 module: {relative}")
            self.assertGreater(path.stat().st_size, 0, f"empty V2 module: {relative}")
