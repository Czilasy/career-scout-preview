"""webui.app 深度调优入口合同测试（027 自 tests/test_webui_app.py 拆出）。"""
import hashlib
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from webui.app import create_app


def _tuning_quality_context():
    return {
        "profile_summary": (
            "AI应用开发候选人，掌握 Python、FastAPI、LangGraph 和 RAG。"
        ),
        "screening_fields": {
            "salary": ["403", "404", "405"],
            "experience": ["101", "103", "104"],
            "degree": ["202", "203"],
        },
        "profile_ref": "user-confirmed:test",
    }


def _make_valid_manifest_payload_web(
    *, experiment_id: str, candidate_id: str, round_id: str,
) -> dict:
    """构造一份完整的合法 manifest payload（web 测试用）。"""
    import hashlib
    config = {
        "schema_version": 1,
        "inter_combo_delay": 5.0,
        "detail_batch_size": 10,
        "detail_interval": 2.0,
        "detail_reset_every": 3,
        "detail_batch_cooldown": 4.0,
        "detail_tab_pool_size": 5,
        "screen_batch_size": 30,
        "screen_concurrency": 3,
        "match_batch_size": 2,
        "match_concurrency": 4,
    }
    config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
    config["config_digest"] = "sha256:" + hashlib.sha256(
        config_json.encode("utf-8")
    ).hexdigest()
    scope = {
        "keywords": ["AI应用开发"],
        "scope_kind": "cities",
        "cities": ["东莞"],
        "pages_per_combination": 3,
    }
    scope_json = json.dumps(scope, ensure_ascii=False, sort_keys=True)
    scope_digest = "sha256:" + hashlib.sha256(
        scope_json.encode("utf-8")
    ).hexdigest()
    artifact_digest = "sha256:" + hashlib.sha256(b"artifact").hexdigest()
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "candidate_id": candidate_id,
        "round_id": round_id,
        "task_id": f"task-{round_id}",
        "spec_version": "011-deep-configuration-probing",
        "objective": "测试候选在 list 阶段的耗时与完整性",
        "round_kind": "list",
        "strategy_step": "single_field",
        "repetition_index": 1,
        "preconditions": [
            {
                "id": "check_lease",
                "instruction": "确认实验租约由当前进程持有",
                "expected": "lease owner is current process",
                "on_failure": "block_and_report",
                "evidence_field": "preflight[0].evidence",
            },
            {
                "id": "check_input_artifact",
                "instruction": "确认冻结输入产物存在且摘要匹配",
                "expected": "artifact_digest matches frozen_input.artifact_digest",
                "on_failure": "block_and_report",
                "evidence_field": "preflight[1].evidence",
            },
        ],
        "frozen_input": {
            "input_version_id": "iv-1",
            "workload_id": "wl-1",
            "task_size": "small",
            "structure_index": 1,
            "scope": scope,
            "scope_digest": scope_digest,
            "artifact_path": f"tuning/{experiment_id}/inputs/iv-1.json",
            "artifact_digest": artifact_digest,
            "expected_input_count": 30,
            "planned_pages": 3,
        },
        "execution_config": config,
        "fixed_fields": {
            "keywords": ["AI应用开发"],
            "scope_kind": "cities",
            "cities": ["东莞"],
            "pages_per_combination": 3,
            "planned_pages": 3,
            "task_size": "small",
            "model_reference": "gpt-default",
            "build_identity": "v1",
        },
        "execution_steps": [
            {
                "seq": 1,
                "action": "start_round",
                "instruction": "按 manifest 启动轮次并写入 evidence",
                "expected_status": "running",
                "timeout_seconds": 600,
                "on_timeout": "stop_new_work_and_block_report",
                "named_retry": None,
                "evidence_field": "steps[0].evidence",
            },
            {
                "seq": 2,
                "action": "confirm_round",
                "instruction": "在 evidence 写入后确认轮次完成",
                "expected_status": "confirmed",
                "timeout_seconds": 60,
                "on_timeout": "stop",
                "named_retry": None,
                "evidence_field": "steps[1].evidence",
            },
        ],
        "monitoring": {
            "status_endpoint": f"/api/tuning/rounds/{round_id}",
            "polling_interval_seconds": 5,
            "max_observation_interval_seconds": 3600,
            "expected_stage_sequence": ["running", "confirmed"],
            "monotonic_counters": ["input_count", "terminal_count"],
            "hard_error_codes": ["hard_error"],
            "recoverable_error_rule": {
                "max_retries": 2,
                "backoff_ms": 1000,
            },
            "evidence_snapshot_interval_seconds": 30,
            "final_artifact_path": f"tuning/{experiment_id}/evidence/{round_id}.json",
        },
        "retry_policy": {
            "max_retries": 2,
            "backoff_ms": 1000,
            "recoverable_codes": ["captcha_required"],
        },
        "stop_conditions": [
            {
                "code": "captcha_required",
                "match": "program error_code equals captcha_required",
                "severity": "recoverable",
                "action": "execute_named_retry",
                "required_evidence": ["status_snapshot"],
            },
            {
                "code": "hard_error",
                "match": "program error_code equals hard_error",
                "severity": "fatal",
                "action": "block_and_report",
                "required_evidence": ["status_snapshot"],
            },
        ],
        "allowed_writes": [
            f"tuning/{experiment_id}/evidence/",
            f"tuning/{experiment_id}/tasks/",
        ],
        "required_artifacts": [
            {
                "artifact_type": "program_report",
                "path": f"tuning/{experiment_id}/evidence/{round_id}.json",
                "producer": "application",
                "existence_requirement": "required",
                "digest_requirement": "sha256",
                "minimum_fields": ["total_duration_ms", "terminal_count"],
                "absence_makes": "invalid",
            },
        ],
        "forbidden_actions": [
            "edit_source_code",
            "select_another_candidate",
            "overwrite_prior_results",
            "write_outside_allowed_paths",
            "adjust_acceptance_criteria",
        ],
        "report_contract": {
            "required_fields": [
                "schema_version", "report_id", "task_id", "experiment_id",
                "candidate_id", "round_id", "manifest_digest", "status",
                "preflight", "steps", "program_evidence", "artifacts",
                "stop_reason", "unexecuted_steps", "started_at",
                "finished_at",
            ],
            "forbidden_fields": [
                "parameter_suggestions", "candidate_ranking",
                "next_candidate", "mode_recommendation",
            ],
            "notes_policy": "observable_facts_only",
        },
    }


class TuningManifestRouteTests(unittest.TestCase):
    """SPEC011 T023 RED: 控制者 manifest/decision 路由与执行者路由测试。

    覆盖 contracts/http-api.md 第 4-6 节。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        # 通过 store 直接创建实验+候选+轮次（T030 才会添加实验 HTTP 路由）
        from webui.store import TaskStore
        from webui.tuning import TuningController
        db_path = root / "state" / "webui.db"
        self.store = TaskStore(db_path)
        self.controller = TuningController(self.store)
        def scope(keyword_count, pages):
            return {
                "keywords": [f"接口结构{i}" for i in range(keyword_count)],
                "scope_kind": "cities", "cities": ["东莞"],
                "pages_per_combination": pages,
            }
        scopes = [
            ("small", scope(1, 3)), ("small", scope(2, 3)),
            # 024 规模新口径（<15 小 / 15~30 中 / >30 大）：16 页属中规模
            ("medium", scope(2, 8)), ("medium", scope(3, 5)),
            ("large", scope(10, 5)), ("large", scope(11, 5)),
        ]
        self.experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope={**scopes[0][1], "browser_account": "a", "filter_schema_version": 1},
            quality_context=_tuning_quality_context(),
            workloads=[
                {"task_size": size, "structure_index": index % 2 + 1,
                 "scope": value}
                for index, (size, value) in enumerate(scopes)
            ],
        )
        self.controller.confirm_input(self.experiment["id"])
        self.bundle = self.store.get_tuning_input_bundle(self.experiment["id"])
        self.workload = self.bundle["workloads"][0]
        self.candidate = self.controller.add_candidate(
            experiment_id=self.experiment["id"],
            stage="list",
            strategy_step="single_field",
            config={
                "inter_combo_delay": 5.0,
                "detail_batch_size": 10,
                "detail_interval": 2.0,
                "detail_reset_every": 3,
                "detail_batch_cooldown": 4.0,
                "screen_batch_size": 30,
                "screen_concurrency": 3,
                "match_batch_size": 2,
                "match_concurrency": 4,
            },
        )
        self.round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id=self.workload["id"],
            round_kind="list",
            repetition_index=1,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _make_manifest(self) -> dict:
        manifest = _make_valid_manifest_payload_web(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            round_id=self.round["id"],
        )
        root = f"tuning/{self.experiment['id']}"
        scope = self.workload["scope"]
        manifest["execution_config"] = self.store.get_tuning_candidate(
            self.candidate["id"])["config"]
        manifest["frozen_input"].update({
            "input_version_id": self.bundle["input_version"]["id"],
            "workload_id": self.workload["id"],
            "task_size": self.workload["task_size"],
            "structure_index": self.workload["structure_index"],
            "scope_digest": scope["scope_digest"],
            "artifact_digest": self.workload["artifact_digest"],
            "quality_context_digest": self.bundle["input_version"][
                "quality_context_digest"
            ],
            "planned_pages": self.workload["planned_pages"],
            "artifact_manifest_path": f"{root}/input/{self.workload['id']}.json",
        })
        manifest["fixed_fields"] = {
            key: scope[key] for key in (
                "keywords", "scope_kind", "cities", "pages_per_combination",
                "planned_pages", "task_size",
            )
        }
        manifest["fixed_fields"]["platform"] = "boss"
        evidence_path = f"{root}/evidence/{self.round['id']}.json"
        manifest["monitoring"]["final_artifact_path"] = evidence_path
        manifest["allowed_writes"] = [evidence_path, f"{root}/artifacts/{self.round['id']}/"]
        manifest["required_artifacts"][0]["path"] = evidence_path
        return manifest

    # -- POST /api/tuning/experiments/{id}/manifests --------------------

    def test_issue_manifest_route_success(self):
        """POST /manifests 成功签发返回 201。"""
        manifest = self._make_manifest()
        resp = self.client.post(
            f"/api/tuning/experiments/{self.experiment['id']}/manifests",
            json=manifest,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "issued")
        self.assertIsNotNone(data["manifest_id"])
        self.assertIsNotNone(data["manifest_digest"])
        self.assertIn("rendered_task_path", data)

    def test_issue_manifest_route_missing_field_returns_422(self):
        """POST /manifests 缺字段返回 422。"""
        manifest = self._make_manifest()
        del manifest["objective"]
        resp = self.client.post(
            f"/api/tuning/experiments/{self.experiment['id']}/manifests",
            json=manifest,
        )
        self.assertEqual(resp.status_code, 422)
        data = resp.get_json()
        self.assertFalse(data["ok"])

    def test_issue_manifest_route_placeholder_returns_422(self):
        """POST /manifests 包含占位符返回 422。"""
        manifest = self._make_manifest()
        manifest["objective"] = "<placeholder> 目标"
        resp = self.client.post(
            f"/api/tuning/experiments/{self.experiment['id']}/manifests",
            json=manifest,
        )
        self.assertEqual(resp.status_code, 422)

    # -- GET /api/tuning/manifests/{id} ---------------------------------

    def test_get_manifest_route_success(self):
        """GET /manifests/{id} 返回安全结构化 manifest。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        resp = self.client.get(
            f"/api/tuning/manifests/{issued['manifest_id']}"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["manifest_id"], issued["manifest_id"])
        self.assertEqual(
            data["manifest_digest"], issued["manifest_digest"]
        )
        self.assertIn("rendered_task_path", data)
        # 不返回凭据/敏感内容
        manifest_text = json.dumps(data.get("manifest", {}))
        self.assertNotIn("api_key", manifest_text.lower())

    def test_get_manifest_route_not_found(self):
        """GET /manifests/{id} 不存在返回 404。"""
        resp = self.client.get("/api/tuning/manifests/missing-id")
        self.assertEqual(resp.status_code, 404)

    # -- POST /api/tuning/manifests/{id}/execute ------------------------

    def test_execute_manifest_route_success(self):
        """POST /manifests/{id}/execute 启动轮次返回 202。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        resp = self.client.post(
            f"/api/tuning/manifests/{issued['manifest_id']}/execute"
        )
        self.assertEqual(resp.status_code, 202)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["round_id"], self.round["id"])
        self.assertEqual(data["child_task_id"], manifest["task_id"])
        self.assertIn("status_url", data)

    def test_execute_manifest_route_wrong_digest_returns_409(self):
        """POST /execute 在 manifest 被篡改后返回 409。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        # 篡改已签发的 manifest
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE tuning_task_manifests SET manifest_json = ? WHERE id = ?",
                ('{"tampered": true}', issued["manifest_id"]),
            )
        resp = self.client.post(
            f"/api/tuning/manifests/{issued['manifest_id']}/execute"
        )
        self.assertEqual(resp.status_code, 409)

    def test_manifest_child_persists_stage_artifact_before_reported(self):
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        result = {"jobs": [{"job_id": "job-1"}]}
        self.app.config["TUNING_ROUND_RUNNER"].execute = mock.Mock(
            return_value=result,
        )

        self.app.config["RUN_TUNING_MANIFEST_CHILD"](issued["manifest_id"])

        round_record = self.store.get_tuning_round(self.round["id"])
        self.assertEqual(round_record["status"], "reported")
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT id FROM tuning_stage_artifacts "
                "WHERE producer_round_id = ?",
                (self.round["id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        artifact = self.store.get_tuning_stage_artifact(row["id"])
        self.assertEqual(artifact["stage"], "list")
        artifact_path = pathlib.Path(self.temp.name) / artifact["artifact_path"]
        self.assertEqual(
            json.loads(artifact_path.read_text(encoding="utf-8")), result,
        )

    def test_manifest_child_does_not_report_when_stage_artifact_write_fails(self):
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        self.app.config["TUNING_ROUND_RUNNER"].execute = mock.Mock(
            return_value={"jobs": [{"job_id": "job-1"}]},
        )

        with mock.patch(
            "webui.tuning.TuningController.persist_stage_artifact",
            side_effect=OSError("artifact write failed"),
        ):
            with self.assertRaisesRegex(OSError, "artifact write failed"):
                self.app.config["RUN_TUNING_MANIFEST_CHILD"](
                    issued["manifest_id"]
                )

        round_record = self.store.get_tuning_round(self.round["id"])
        self.assertEqual(round_record["status"], "running")

    def test_manifest_child_persists_safe_ai_failure_instead_of_stalling(self):
        from webui.ai import AISecurityError

        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        self.app.config["TUNING_ROUND_RUNNER"].execute = mock.Mock(
            side_effect=AISecurityError("network_error"),
        )

        self.app.config["RUN_TUNING_MANIFEST_CHILD"](issued["manifest_id"])

        round_record = self.store.get_tuning_round(self.round["id"])
        self.assertEqual(round_record["status"], "reported")
        self.assertEqual(round_record["failure_code"], "network_error")
        evidence_path = (
            pathlib.Path(self.temp.name)
            / manifest["monitoring"]["final_artifact_path"]
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["error_counts"], {"network_error": 1})

    def test_manifest_child_preserves_safe_stage_failure_code(self):
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        error = RuntimeError("list stage stopped")
        error.error_code = "source_cdp_unavailable"
        self.app.config["TUNING_ROUND_RUNNER"].execute = mock.Mock(
            side_effect=error,
        )

        self.app.config["RUN_TUNING_MANIFEST_CHILD"](issued["manifest_id"])

        round_record = self.store.get_tuning_round(self.round["id"])
        self.assertEqual(round_record["failure_code"], "source_cdp_unavailable")

    def test_create_app_reconciles_issued_manifest_after_restart(self):
        manifest = self._make_manifest()
        self.controller.issue_manifest(manifest)
        self.assertEqual(
            self.store.get_tuning_round(self.round["id"])["status"], "issued"
        )
        self.assertEqual(
            self.store.get_tuning_lease()["owner_round_id"], self.round["id"]
        )

        restarted = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(pathlib.Path(self.temp.name) / "results"),
            "DB_PATH": str(pathlib.Path(self.temp.name) / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        restarted_store = restarted.config["TASK_STORE"]

        self.assertEqual(
            restarted_store.get_tuning_round(self.round["id"])["status"],
            "uncertain",
        )
        experiment = restarted_store.get_tuning_experiment(
            self.experiment["id"]
        )
        self.assertEqual(experiment["status"], "blocked")
        self.assertEqual(
            experiment["blocked_code"], "restart_interrupted_round"
        )
        self.assertIsNone(
            restarted_store.get_tuning_lease()["owner_round_id"]
        )

    def test_create_app_reconciles_unconfirmed_report_after_restart(self):
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        self.store.update_tuning_round_status(
            self.round["id"], status="reported",
        )

        restarted = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(pathlib.Path(self.temp.name) / "results"),
            "DB_PATH": str(pathlib.Path(self.temp.name) / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        restarted_store = restarted.config["TASK_STORE"]

        self.assertEqual(
            restarted_store.get_tuning_round(self.round["id"])["status"],
            "uncertain",
        )
        experiment = restarted_store.get_tuning_experiment(
            self.experiment["id"]
        )
        self.assertEqual(experiment["status"], "blocked")
        self.assertEqual(
            experiment["blocked_code"], "restart_interrupted_round"
        )
        self.assertIsNone(
            restarted_store.get_tuning_lease()["owner_round_id"]
        )

    # -- GET /api/tuning/rounds/{round_id} ------------------------------

    def test_get_round_route_success(self):
        """GET /rounds/{id} 返回程序状态。"""
        manifest = self._make_manifest()
        self.controller.issue_manifest(manifest)
        resp = self.client.get(
            f"/api/tuning/rounds/{self.round['id']}"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["round"]["id"], self.round["id"])
        self.assertIn("status", data["round"])

    def test_get_round_route_not_found(self):
        """GET /rounds/{id} 不存在返回 404。"""
        resp = self.client.get("/api/tuning/rounds/missing-id")
        self.assertEqual(resp.status_code, 404)

    # -- POST /api/tuning/manifests/{id}/report -------------------------

    def test_submit_report_route_success(self):
        """POST /report 成功接受返回 201。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        report = self._make_valid_report(issued["manifest_digest"])
        resp = self.client.post(
            f"/api/tuning/manifests/{issued['manifest_id']}/report",
            json=report,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data["validation_status"], "accepted")

    def test_submit_report_route_invalid_returns_422(self):
        """POST /report 校验失败返回 422。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        report = self._make_valid_report(issued["manifest_digest"])
        report["manifest_digest"] = "sha256-wrong"
        resp = self.client.post(
            f"/api/tuning/manifests/{issued['manifest_id']}/report",
            json=report,
        )
        self.assertEqual(resp.status_code, 422)

    # -- GET /api/tuning/rounds/{round_id}/evidence ---------------------

    def test_get_evidence_route_success(self):
        """GET /rounds/{id}/evidence 返回安全聚合证据。"""
        manifest = self._make_manifest()
        self.controller.issue_manifest(manifest)
        # 记录一些测量事件
        self.controller.record_measurement(
            round_id=self.round["id"],
            event_type="stage",
            stage="list",
            duration_ms=1000,
            counts={"input_count": 30, "output_count": 30},
        )
        resp = self.client.get(
            f"/api/tuning/rounds/{self.round['id']}/evidence"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("evidence", data)

    # -- POST /api/tuning/experiments/{id}/decisions --------------------

    def test_post_decision_route_promote_success(self):
        """POST /decisions promote 成功返回 200。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        report = self._make_valid_report(issued["manifest_digest"])
        self.controller.accept_report(
            manifest_id=issued["manifest_id"], report=report,
        )
        resp = self.client.post(
            f"/api/tuning/experiments/{self.experiment['id']}/decisions",
            json={
                "candidate_id": self.candidate["id"],
                "decision": "promote",
                "reason_evidence": [self.round["id"]],
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])

    def test_post_decision_route_reject_success(self):
        """POST /decisions reject 成功返回 200。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        report = self._make_valid_report(issued["manifest_digest"])
        self.controller.accept_report(
            manifest_id=issued["manifest_id"], report=report,
        )
        resp = self.client.post(
            f"/api/tuning/experiments/{self.experiment['id']}/decisions",
            json={
                "candidate_id": self.candidate["id"],
                "decision": "reject",
                "code": "hard_error",
                "reason_evidence": [self.round["id"]],
            },
        )
        self.assertEqual(resp.status_code, 200)

    def _make_valid_report(self, manifest_digest: str) -> dict:
        """构造一份完整的合法 executor report。"""
        manifest = self._make_manifest()
        evidence = {
            "program_report_path": manifest["required_artifacts"][0]["path"],
            "config_digest": manifest["execution_config"]["config_digest"],
            "scope_digest": manifest["frozen_input"]["scope_digest"],
            "input_artifact_digest": manifest["frozen_input"]["artifact_digest"],
            "total_duration_ms": 45000,
            "stage_durations_ms": {"list": 40000},
            "work_duration_ms": 40000,
            "wait_duration_ms": 5000, "retry_duration_ms": 0,
            "attempt_count": 1, "retry_count": 0,
            "input_count": 30, "terminal_count": 30,
            "success_count": 30, "failed_count": 0,
            "missing_count": 0, "duplicate_count": 0,
            "quality_diff_count": 0, "error_counts": {},
        }
        evidence_path = pathlib.Path(self.temp.name) / evidence["program_report_path"]
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        raw_evidence = json.dumps(
            evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        evidence_path.write_bytes(raw_evidence)
        evidence_digest = "sha256:" + hashlib.sha256(raw_evidence).hexdigest()
        return {
            "schema_version": 1,
            "report_id": "report-001",
            "task_id": manifest["task_id"],
            "experiment_id": manifest["experiment_id"],
            "candidate_id": manifest["candidate_id"],
            "round_id": manifest["round_id"],
            "manifest_digest": manifest_digest,
            "status": "completed",
            "preflight": [
                {"id": "check_lease", "result": "passed",
                 "evidence": "lease ok"},
                {"id": "check_input_artifact", "result": "passed",
                 "evidence": "frozen input checked"},
            ],
            "steps": [
                {"seq": 1, "status": "completed",
                 "evidence": "round started"},
                {"seq": 2, "status": "completed",
                 "evidence": "round confirmed"},
            ],
            "observations": {
                "total_duration_observed": 45000,
                "stages_observed": ["list", "done"],
            },
            "program_evidence": {**evidence, "program_report_digest": evidence_digest},
            "artifacts": [
                {
                    "artifact_type": "program_report",
                    "path": evidence["program_report_path"],
                    "digest": evidence_digest,
                    "exists": True,
                },
            ],
            "stop_reason": None,
            "unexecuted_steps": [],
            "executor_notes": ["所有步骤按任务单完成"],
            "started_at": "2026-07-29T10:00:00+08:00",
            "finished_at": "2026-07-29T10:01:30+08:00",
        }


class TuningExperimentRouteTests(unittest.TestCase):
    """SPEC011 T030 RED: 实验生命周期 HTTP 路由测试。

    覆盖 contracts/http-api.md 第 3 节：create / confirm-input / status /
    cancel / resume。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _complete_workloads():
        return [
            {"task_size": "small", "structure_index": 1, "scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3}},
            {"task_size": "small", "structure_index": 2, "scope": {
                "keywords": ["AI应用开发", "智能体开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 4}},
            {"task_size": "medium", "structure_index": 1, "scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞", "深圳"], "pages_per_combination": 8}},  # 024 新口径：16 页属中规模
            {"task_size": "medium", "structure_index": 2, "scope": {
                "keywords": ["AI应用开发", "智能体开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 8}},  # 024 新口径：16 页属中规模
            {"task_size": "large", "structure_index": 1, "scope": {
                "keywords": ["AI应用开发", "智能体开发", "Python后端", "Java后端", "前端开发"], "scope_kind": "cities",
                "cities": ["东莞", "深圳"], "pages_per_combination": 5}},
            {"task_size": "large", "structure_index": 2, "scope": {
                "keywords": ["AI应用开发", "智能体开发", "Python后端", "Java后端", "前端开发", "Go后端", "测试开发", "运维开发", "数据分析", "产品经理"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 5}},
        ]

    @staticmethod
    def _quality_context():
        return {
            "profile_summary": "AI应用开发候选人，掌握 Python、FastAPI、LangGraph 和 RAG。",
            "screening_fields": {
                "salary": ["403", "404", "405"],
                "experience": ["101", "103", "104"],
                "degree": ["202", "203"],
            },
            "profile_ref": "user-confirmed:test",
        }

    # -- POST /api/tuning/experiments ------------------------------------

    def test_create_experiment_route_returns_201(self):
        """POST /api/tuning/experiments 创建实验返回 201。"""
        resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
                "browser_account": "a",
                "filter_schema_version": 1,
            },
            "quality_context": self._quality_context(),
            "workloads": [
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        })
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIsNotNone(data["experiment_id"])
        self.assertEqual(data["status"], "draft")

    def test_create_experiment_route_rejects_missing_quality_context(self):
        resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
            },
            "workloads": self._complete_workloads(),
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("quality_context", resp.get_json()["error"])

    def test_create_experiment_route_missing_fields_returns_400(self):
        """缺少必填字段返回 400。"""
        resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
        })
        self.assertEqual(resp.status_code, 400)

    # -- GET /api/tuning/experiments/{id} --------------------------------

    def test_get_experiment_route_returns_picture(self):
        """GET /api/tuning/experiments/{id} 返回实验快照。"""
        # 先创建实验
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
                "browser_account": "a",
                "filter_schema_version": 1,
            },
            "quality_context": self._quality_context(),
            "workloads": [
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        resp = self.client.get(f"/api/tuning/experiments/{exp_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["experiment"]["id"], exp_id)
        self.assertIn("status", data["experiment"])
        self.assertIn("can_cancel", data["experiment"])
        self.assertIn("can_resume", data["experiment"])

    def test_get_experiment_route_not_found_returns_404(self):
        """不存在的实验返回 404。"""
        resp = self.client.get("/api/tuning/experiments/nonexistent")
        self.assertEqual(resp.status_code, 404)

    # -- POST /api/tuning/experiments/{id}/cancel ------------------------

    def test_cancel_experiment_route_returns_200(self):
        """POST /cancel 取消实验返回 200。"""
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
                "browser_account": "a",
                "filter_schema_version": 1,
            },
            "quality_context": self._quality_context(),
            "workloads": [
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        resp = self.client.post(f"/api/tuning/experiments/{exp_id}/cancel")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        # 验证实验状态为 cancelled
        get_resp = self.client.get(f"/api/tuning/experiments/{exp_id}")
        self.assertEqual(
            get_resp.get_json()["experiment"]["status"], "cancelled")

    def test_cancel_experiment_route_not_found_returns_404(self):
        """取消不存在的实验返回 404。"""
        resp = self.client.post(
            "/api/tuning/experiments/nonexistent/cancel")
        self.assertEqual(resp.status_code, 404)

    # -- POST /api/tuning/experiments/{id}/confirm-input -----------------

    def test_confirm_input_route_advances_through_preflight(self):
        """确认后完成本地 preflight 并进入可签发状态。"""
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
                "browser_account": "a",
                "filter_schema_version": 1,
            },
            "quality_context": self._quality_context(),
            "workloads": self._complete_workloads(),
        })
        exp_id = create_resp.get_json()["experiment_id"]
        resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/confirm-input")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "awaiting_instruction")
        self.assertTrue(data["input_version_id"])
        self.assertEqual(len(data["scope_digest"]), 64)
        int(data["scope_digest"], 16)
        self.assertEqual(len(data["workload_digests"]), 6)

    def test_create_persists_draft_input_and_all_workloads(self):
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
                "browser_account": "a",
                "filter_schema_version": 1,
            },
            "quality_context": self._quality_context(),
            "workloads": self._complete_workloads(),
        })
        self.assertEqual(create_resp.status_code, 201, create_resp.get_json())
        exp_id = create_resp.get_json()["experiment_id"]
        from webui.store import TaskStore
        store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        bundle = store.get_tuning_input_bundle(exp_id)
        self.assertEqual(bundle["input_version"]["status"], "draft")
        self.assertEqual(len(bundle["workloads"]), 6)
        self.assertEqual(
            {item["task_size"] for item in bundle["workloads"]},
            {"small", "medium", "large"})

    def test_confirm_input_rejects_incomplete_workload_matrix(self):
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
                "browser_account": "a",
                "filter_schema_version": 1,
            },
            "quality_context": self._quality_context(),
            "workloads": [
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/confirm-input")
        self.assertEqual(resp.status_code, 409)

    def test_confirm_input_rejects_duplicate_workload_structures(self):
        workloads = self._complete_workloads()
        for start in (0, 2, 4):
            workloads[start + 1]["scope"] = dict(workloads[start]["scope"])
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
                "browser_account": "a",
                "filter_schema_version": 1,
            },
            "quality_context": self._quality_context(),
            "workloads": workloads,
        })
        exp_id = create_resp.get_json()["experiment_id"]
        resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/confirm-input"
        )
        self.assertEqual(resp.status_code, 409)
        self.assertIn("不同", resp.get_json()["error"])
        self.assertEqual(resp.get_json()["error_code"], "input_incomplete")

    # -- POST /api/tuning/experiments/{id}/resume ------------------------

    def test_resume_experiment_route_blocked_to_awaiting(self):
        """POST /resume 从 blocked 恢复到 awaiting_instruction。"""
        # 创建并推进到 blocked
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
                "browser_account": "a",
                "filter_schema_version": 1,
            },
            "quality_context": self._quality_context(),
            "workloads": [
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        # 推进到 preflight 再到 blocked
        from webui.store import TaskStore
        root = pathlib.Path(self.temp.name)
        store = TaskStore(root / "state" / "webui.db")
        store.update_tuning_experiment_status(exp_id, status="preflight")
        store.update_tuning_experiment_status(
            exp_id, status="blocked",
            blocked_code="test_block",
            blocked_reason="测试阻断")
        resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/resume")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])

    def test_resume_experiment_route_not_blocked_returns_409(self):
        """非 blocked 状态恢复返回 409。"""
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
                "browser_account": "a",
                "filter_schema_version": 1,
            },
            "quality_context": self._quality_context(),
            "workloads": [
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        # draft 状态不能 resume
        resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/resume")
        self.assertEqual(resp.status_code, 409)

    def test_incomplete_result_is_visible_but_not_applicable(self):
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
                "browser_account": "a",
                "filter_schema_version": 1,
            },
            "quality_context": self._quality_context(),
            "workloads": [],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        result = self.client.get(f"/api/tuning/experiments/{exp_id}/result")
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.get_json()["can_apply"])
        apply_resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/apply",
            json={"candidate_mode_version_digest": "sha256:not-ready"},
        )
        self.assertEqual(apply_resp.status_code, 409)

    def test_zero_round_candidate_cannot_complete_or_apply(self):
        from webui.store import TaskStore
        from webui.tuning import TuningController

        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
                "browser_account": "a",
                "filter_schema_version": 1,
            },
            "quality_context": self._quality_context(),
            "workloads": [],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        root = pathlib.Path(self.temp.name)
        store = TaskStore(root / "state" / "webui.db")
        controller = TuningController(store)
        config = {
            "inter_combo_delay": 10.0, "detail_batch_size": 15,
            "detail_interval": 2.0, "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0, "screen_batch_size": 50,
            "screen_concurrency": 5, "match_batch_size": 4,
            "match_concurrency": 10,
        }
        matrix = {
            mode: {size: dict(config) for size in ("small", "medium", "large")}
            for mode in ("stable", "balanced", "extreme")
        }
        custom_digest = store.save_custom_config({**config, "detail_batch_size": 8})
        previous_id = store.create_mode_version(matrix=matrix, manual_ranges={})
        store.apply_mode_version(previous_id)
        for status in ("preflight", "awaiting_instruction", "queued", "running"):
            store.update_tuning_experiment_status(exp_id, status=status)
        candidate = controller.create_candidate_mode_version(
            experiment_id=exp_id, matrix=matrix)
        store.update_tuning_experiment_status(exp_id, status="evaluating")
        with self.assertRaises(ValueError):
            store.update_tuning_experiment_status(exp_id, status="completed")

        result = self.client.get(f"/api/tuning/experiments/{exp_id}/result")
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertFalse(result.get_json()["can_apply"])
        self.assertEqual(
            result.get_json()["candidate_mode_version_digest"],
            candidate["version_digest"])

        apply_resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/apply",
            json={"candidate_mode_version_digest": candidate["version_digest"]},
        )
        self.assertEqual(apply_resp.status_code, 409, apply_resp.get_json())
        self.assertEqual(
            store.get_advanced_config_state()["active_mode_version_id"],
            previous_id)
        self.assertEqual(
            store.get_advanced_config_state()["last_custom_digest"], custom_digest)

        self.assertEqual(
            store.get_advanced_config_state()["last_custom_digest"], custom_digest)


class SourceDetailBatchCommandTests(unittest.TestCase):
    """JD 批量详情命令必须透传并发 tab 数，默认值为 5。"""

    def test_batch_command_forwards_tab_pool_size(self):
        from webui.source import BossCdpSource

        source = BossCdpSource(
            python_executable="python",
            scraper_path="scripts/boss_cdp_raw.py",
        )
        command = source._build_detail_batch_command(
            "batch.input.json", "batch.out.json", "batch.events.jsonl",
            batch_size=2, gap_min=1, gap_max=2, reset_every=3,
            tab_pool_size=5,
        )
        self.assertIn("--tab-pool-size", command)
        self.assertEqual(command[command.index("--tab-pool-size") + 1], "5")

    def test_batch_detail_default_tab_pool_is_five(self):
        import inspect
        from webui.source import BossCdpSource

        self.assertEqual(
            inspect.signature(BossCdpSource.fetch_details_batch)
            .parameters["tab_pool_size"].default,
            5,
        )
        self.assertEqual(
            inspect.signature(BossCdpSource._build_detail_batch_command)
            .parameters["tab_pool_size"].default,
            5,
        )

    def test_batch_command_omits_simulation_mode_by_default(self):
        from webui.source import BossCdpSource

        source = BossCdpSource(
            python_executable="python",
            scraper_path="scripts/boss_cdp_raw.py",
        )
        command = source._build_detail_batch_command(
            "batch.input.json", "batch.out.json", "batch.events.jsonl",
            batch_size=2, gap_min=1, gap_max=2, reset_every=3,
            tab_pool_size=5,
        )
        self.assertNotIn("--simulation-mode", command)

    def test_batch_command_forwards_simulation_mode(self):
        from webui.source import BossCdpSource

        source = BossCdpSource(
            python_executable="python",
            scraper_path="scripts/boss_cdp_raw.py",
        )
        command = source._build_detail_batch_command(
            "batch.input.json", "batch.out.json", "batch.events.jsonl",
            batch_size=2, gap_min=1, gap_max=2, reset_every=3,
            tab_pool_size=5, simulation_mode="stable",
        )
        self.assertIn("--simulation-mode", command)
        self.assertEqual(
            command[command.index("--simulation-mode") + 1], "stable",
        )


class LegacyPlatformGuardTests(unittest.TestCase):
    """tasks007 T601-T604: legacy BOSS-only 路由对显式智联/未知平台的零副作用拒绝。

    合同（contracts/http-api.md 第 351-370 行 Legacy BOSS-only 矩阵）：
    - 显式 ``zhilian`` → ``422 legacy_platform_not_supported``，且发生在任务/对象
      查找和任何副作用之前。
    - 其它未知平台 → ``400 platform_validation_failed``。
    - 显式 ``boss`` 或省略平台 → 走既有 BOSS 行为，成功对象标识 ``platform=boss``。
    """

    _TASK_BODY = {
        "keyword": "Python 后端", "city": "上海", "pages": 1,
        "detail": False, "analysis": False, "format": "json",
    }
    _CONFIRM_BODY = {
        "keyword": [{"word": "Python", "recommended": True}],
        "city": "上海",
    }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.result_dir = root / "results"
        self.db_path = root / "state" / "webui.db"
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.result_dir),
            "DB_PATH": str(self.db_path),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        session = self.client.get("/api/session")
        self.assertEqual(session.status_code, 200)
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        # 预置一个 BOSS 任务用于 task 子路由（cancel/retry/result/summary/export）。
        resp = self.client.post("/api/tasks", json=self._TASK_BODY)
        self.assertEqual(resp.status_code, 202)
        self.task_id = resp.get_json()["task"]["id"]

    def tearDown(self):
        self.temp.cleanup()

    # ----- 快照助手 -------------------------------------------------------

    def _db_table_counts(self):
        conn = sqlite3.connect(str(self.db_path))
        try:
            names = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            return {
                name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in names
            }
        finally:
            conn.close()

    def _result_files(self):
        if not self.result_dir.exists():
            return []
        return sorted(p.name for p in self.result_dir.glob("*"))

    def _snapshot(self):
        return {
            "db": self._db_table_counts(),
            "result_files": self._result_files(),
            "task_count": len(self.client.get("/api/tasks").get_json()["tasks"]),
        }

    def _assert_no_side_effects(self, before, *, context=""):
        after = self._snapshot()
        self.assertEqual(
            after["db"], before["db"],
            f"{context}DB 表行数发生变化: {before['db']} -> {after['db']}",
        )
        self.assertEqual(
            after["result_files"], before["result_files"],
            f"{context}结果文件列表发生变化: {before['result_files']} -> {after['result_files']}",
        )
        self.assertEqual(
            after["task_count"], before["task_count"],
            f"{context}任务数量发生变化: {before['task_count']} -> {after['task_count']}",
        )

    # ----- 路由矩阵 -------------------------------------------------------

    def _legacy_routes(self):
        """返回 (name, method, path, body_or_None) 列表，path 已填入 task_id。"""
        tid = self.task_id
        return [
            ("tasks_get", "GET", "/api/tasks", None),
            ("tasks_post", "POST", "/api/tasks", dict(self._TASK_BODY)),
            ("scrape_post", "POST", "/api/scrape", dict(self._TASK_BODY)),
            ("setup_chrome_post", "POST", "/api/setup-chrome", {}),
            ("task_detail_get", "GET", f"/api/tasks/{tid}", None),
            ("task_cancel_post", "POST", f"/api/tasks/{tid}/cancel", {}),
            ("task_retry_post", "POST", f"/api/tasks/{tid}/retry", {}),
            ("task_result_get", "GET", f"/api/tasks/{tid}/result", None),
            ("task_summary_get", "GET", f"/api/tasks/{tid}/summary", None),
            ("task_export_get", "GET", f"/api/tasks/{tid}/export.csv", None),
            ("results_get", "GET", "/api/results", None),
            ("confirm_fields_post", "POST", "/api/confirm-fields", dict(self._CONFIRM_BODY)),
            ("search_runs_post", "POST", "/api/search-runs",
             {"profile_id": "missing", "manual_keywords": ["Python"]}),
            ("search_run_detail_get", "GET", "/api/search-runs/missing-run", None),
            ("search_run_jobs_get", "GET", "/api/search-runs/missing-run/jobs", None),
            ("search_run_cancel_post", "POST", "/api/search-runs/missing-run/cancel", {}),
        ]

    def _send(self, method, path, *, platform, body=None):
        if method == "GET":
            qs = {} if platform is None else {"platform": platform}
            return self.client.get(path, query_string=qs)
        payload = dict(body or {})
        if platform is not None:
            payload["platform"] = platform
        return self.client.post(path, json=payload)

    # ----- T601/T602: 显式 zhilian → 422 + 零副作用 -----------------------

    def test_zhilian_rejected_with_422_and_zero_side_effects(self):
        for name, method, path, body in self._legacy_routes():
            with self.subTest(route=name):
                before = self._snapshot()
                resp = self._send(method, path, platform="zhilian", body=body)
                self.assertEqual(
                    resp.status_code, 422,
                    f"{name}: 期望 422，实际 {resp.status_code} "
                    f"{resp.get_data(as_text=True)[:200]}",
                )
                data = resp.get_json()
                self.assertIsNotNone(data, f"{name}: 响应非 JSON")
                self.assertEqual(
                    data["error_code"], "legacy_platform_not_supported",
                    f"{name}: error_code={data.get('error_code')}",
                )
                self._assert_no_side_effects(before, context=f"[{name}] ")

    # ----- T601/T602: 未知平台 → 400 + 零副作用 ---------------------------

    def test_unknown_platform_rejected_with_400_and_zero_side_effects(self):
        for name, method, path, body in self._legacy_routes():
            with self.subTest(route=name):
                before = self._snapshot()
                resp = self._send(method, path, platform="weird-platform", body=body)
                self.assertEqual(
                    resp.status_code, 400,
                    f"{name}: 期望 400，实际 {resp.status_code} "
                    f"{resp.get_data(as_text=True)[:200]}",
                )
                data = resp.get_json()
                self.assertIsNotNone(data, f"{name}: 响应非 JSON")
                self.assertEqual(
                    data["error_code"], "platform_validation_failed",
                    f"{name}: error_code={data.get('error_code')}",
                )
                self._assert_no_side_effects(before, context=f"[{name}] ")

    # ----- T601/T602: zhilian 拒绝发生在对象查找前（不返回 404） -----------

    def test_zhilian_rejects_before_object_lookup(self):
        """对不存在的 task/run id，显式 zhilian 必须返回 422 而非 404。"""
        cases = [
            ("missing_task_detail", "GET", "/api/tasks/missing-task", None),
            ("missing_task_cancel", "POST", "/api/tasks/missing-task/cancel", {}),
            ("missing_task_retry", "POST", "/api/tasks/missing-task/retry", {}),
            ("missing_task_result", "GET", "/api/tasks/missing-task/result", None),
            ("missing_task_summary", "GET", "/api/tasks/missing-task/summary", None),
            ("missing_task_export", "GET", "/api/tasks/missing-task/export.csv", None),
            ("missing_run_detail", "GET", "/api/search-runs/missing-run", None),
            ("missing_run_jobs", "GET", "/api/search-runs/missing-run/jobs", None),
            ("missing_run_cancel", "POST", "/api/search-runs/missing-run/cancel", {}),
        ]
        for name, method, path, body in cases:
            with self.subTest(route=name):
                resp = self._send(method, path, platform="zhilian", body=body)
                self.assertEqual(
                    resp.status_code, 422,
                    f"{name}: 期望 422（拒绝先于对象查找），实际 {resp.status_code}",
                )
                self.assertEqual(
                    resp.get_json()["error_code"], "legacy_platform_not_supported",
                )

    # ----- T604: 显式 boss / 省略平台 → 既有 BOSS 行为 --------------------

    def test_explicit_boss_preserves_legacy_behavior(self):
        for name, path in [
            ("tasks_list", "/api/tasks"),
            ("results", "/api/results"),
            ("task_detail", f"/api/tasks/{self.task_id}"),
            ("task_result", f"/api/tasks/{self.task_id}/result"),
            ("task_summary", f"/api/tasks/{self.task_id}/summary"),
        ]:
            with self.subTest(route=name):
                resp = self.client.get(path, query_string={"platform": "boss"})
                self.assertEqual(resp.status_code, 200, f"{name}: {resp.status_code}")

    def test_omitted_platform_preserves_legacy_behavior(self):
        for name, path in [
            ("tasks_list", "/api/tasks"),
            ("results", "/api/results"),
            ("task_detail", f"/api/tasks/{self.task_id}"),
            ("task_result", f"/api/tasks/{self.task_id}/result"),
            ("task_summary", f"/api/tasks/{self.task_id}/summary"),
        ]:
            with self.subTest(route=name):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200, f"{name}: {resp.status_code}")

    def test_boss_success_objects_marked_platform_boss(self):
        """T604: legacy 成功对象补充 platform=boss 标识。"""
        resp = self.client.post("/api/tasks", json=self._TASK_BODY)
        self.assertEqual(resp.status_code, 202)
        task = resp.get_json()["task"]
        self.assertEqual(
            task.get("platform"), "boss",
            f"任务对象缺少 platform=boss 标识: {task.get('platform')}",
        )

        detail = self.client.get(f"/api/tasks/{task['id']}").get_json()["task"]
        self.assertEqual(detail.get("platform"), "boss")

        results = self.client.get("/api/results").get_json()
        self.assertEqual(
            results.get("platform"), "boss",
            f"/api/results 响应缺少 platform=boss: {results.get('platform')}",
        )

    def test_scrape_omitted_platform_creates_boss_task(self):
        """/api/scrape 省略平台保持旧创建别名，任务标识 platform=boss。"""
        resp = self.client.post("/api/scrape", json=self._TASK_BODY)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.get_json()["task"].get("platform"), "boss")


if __name__ == "__main__":
    unittest.main()
