"""Discovery HTTP contract tests (feature 004)."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from webui.app import create_app
from webui.discovery import (
    AISecurityError,
    DEFAULT_USER_MESSAGES,
    ERROR_CODE_MAP,
    DiscoveryError,
    build_portfolio,
)
from webui.store import TaskStore


class CandidateAnalysisV3ContractTests(unittest.TestCase):
    """Specification-level guards for the backend-owned candidate v3 contract."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.ai_contract = (cls.root / "specs/004-resume-job-discovery/contracts/ai-contracts.md").read_text(encoding="utf-8")
        cls.data_model = (cls.root / "specs/004-resume-job-discovery/data-model.md").read_text(encoding="utf-8")
        cls.state_machine = (cls.root / "specs/004-resume-job-discovery/contracts/state-machine.md").read_text(encoding="utf-8")

    def test_candidate_v3_has_backend_owned_typed_empty_shape(self):
        self.assertIn("Candidate analysis provider output v3", self.ai_contract)
        self.assertIn('"contract_version": "v3"', self.ai_contract)
        self.assertIn('quality {status: "complete|partial|manual_required", warnings: []}', self.ai_contract)
        self.assertIn("backend-owned typed empty values", self.ai_contract)

    def test_one_invalid_evidence_item_does_not_discard_valid_summary(self):
        self.assertIn("invalid evidence item is quarantined", self.ai_contract)
        self.assertIn("valid summary", self.ai_contract)
        self.assertNotIn("partial unvalidated output is not persisted as ready", self.ai_contract)

    def test_unverified_search_fields_never_become_confirmed_constraints(self):
        for phrase in ("quarantined fields cannot influence confirmation", "SearchPlan", "scraper inputs"):
            self.assertIn(phrase, self.ai_contract)

    def test_identity_fields_are_not_candidate_or_search_fields(self):
        self.assertIn("Identity fields", self.ai_contract)
        self.assertIn("name", self.ai_contract)
        self.assertIn("exact address", self.ai_contract)
        self.assertIn("excluded from candidate and search fields", self.ai_contract)


# Resume text used by HTTP contract tests. Kept脱敏: no phone/ID/address.
_CONTRACT_RESUME_TEXT = (
    "李四 高级后端开发工程师\n"
    "5年 Python 后端经验，熟悉 Django/Flask，主导订单服务设计与维护。\n"
    "熟练使用 MySQL/Redis/Kafka，负责团队 6 人技术管理。\n"
    "本科计算机科学与技术专业毕业。\n"
)


def _contract_valid_ai_response():
    """A v2-shape AI response that passes validate_candidate_analysis."""
    return {
        "summary": {
            "headline": "高级后端开发工程师",
            "experience_level": "高级",
            "domains": ["后端", "订单"],
            "strengths": ["Python", "系统设计"],
        },
        "evidence": [
            {
                "client_ref": "e1",
                "type": "skill",
                "normalized_value": "Python",
                "safe_excerpt": "Python 后端",
                "source_quote": "Python 后端",
                "source_locator": {"start": 16, "end": 25},
                "assertion_type": "explicit",
                "confidence": 95,
            },
            {
                "client_ref": "e2",
                "type": "responsibility",
                "normalized_value": "订单服务设计",
                "safe_excerpt": "订单服务设计",
                "source_quote": "订单服务设计",
                "source_locator": {"start": 46, "end": 52},
                "assertion_type": "explicit",
                "confidence": 90,
            },
        ],
        "unknowns": [
            {"field": "current_city", "message": "未提及城市"},
        ],
        "directions": [
            {
                "client_ref": "d1",
                "name": "后端开发工程师",
                "type": "core",
                "rationale": "5年后端经验且主导订单服务设计",
                "evidence_refs": ["e1", "e2"],
                "gaps": [],
                "confidence": 92,
                "default_enabled": True,
                "search_terms": ["Python 后端"],
            },
        ],
    }


class DiscoveryErrorEnvelopeTests(unittest.TestCase):
    """T016: safe error codes and failure envelope contract."""

    def test_error_envelope_has_required_fields(self):
        err = DiscoveryError("ai_timeout")
        env = err.to_envelope()
        self.assertIn("error_code", env)
        self.assertIn("user_message", env)
        self.assertIn("stage", env)
        self.assertIn("retryable", env)

    def test_ai_timeout_is_retryable(self):
        err = DiscoveryError("ai_timeout")
        self.assertTrue(err.retryable)
        self.assertEqual(err.stage, "analyzing")

    def test_ai_auth_failed_not_retryable(self):
        err = DiscoveryError("ai_auth_failed")
        self.assertFalse(err.retryable)

    def test_ai_network_error_retryable(self):
        err = DiscoveryError("ai_network_error")
        self.assertTrue(err.retryable)

    def test_ai_invalid_output_not_retryable(self):
        err = DiscoveryError("ai_invalid_output")
        self.assertFalse(err.retryable)

    def test_ai_uncertain_retryable_stage_evaluating(self):
        err = DiscoveryError("ai_uncertain")
        self.assertTrue(err.retryable)
        self.assertEqual(err.stage, "evaluating")

    def test_evidence_reference_invalid_not_retryable(self):
        err = DiscoveryError("evidence_reference_invalid")
        self.assertFalse(err.retryable)

    def test_input_incomplete_not_retryable_no_stage(self):
        err = DiscoveryError("input_incomplete")
        self.assertFalse(err.retryable)
        self.assertIsNone(err.stage)

    def test_verification_error_retryable(self):
        err = DiscoveryError("verification_error")
        self.assertTrue(err.retryable)

    def test_unknown_code_coerced_to_verification_error(self):
        err = DiscoveryError("totally_unknown_code")
        self.assertEqual(err.error_code, "verification_error")

    def test_user_message_never_empty(self):
        for code in ERROR_CODE_MAP:
            err = DiscoveryError(code)
            self.assertTrue(err.user_message, f"empty message for {code}")

    def test_log_detail_not_in_envelope(self):
        err = DiscoveryError("ai_timeout", log_detail="internal stack trace with PII")
        env = err.to_envelope()
        self.assertNotIn("log_detail", env)
        self.assertNotIn("internal stack trace", env["user_message"])

    def test_ai_security_error_defaults_to_invalid_output(self):
        err = AISecurityError()
        self.assertEqual(err.error_code, "ai_invalid_output")
        self.assertFalse(err.retryable)

    def test_ai_security_error_can_override_code(self):
        err = AISecurityError("ai_timeout")
        self.assertEqual(err.error_code, "ai_timeout")
        self.assertTrue(err.retryable)

    def test_all_safe_codes_have_messages(self):
        for code in ERROR_CODE_MAP:
            self.assertIn(code, DEFAULT_USER_MESSAGES, f"missing message for {code}")


class AnalysisConfirmationHttpContractTests(unittest.TestCase):
    """T028/T108: analysis/confirmation HTTP contract.

    契约来源: contracts/openapi.yaml
      POST /api/discovery/analyses  application/json {resume_id, ai_consent} -> 202 AnalysisSummary
      GET  /api/discovery/analyses/{id} -> 200 完整 analysis state
      POST /api/discovery/analyses/{id}/retry -> 202 新版本 AnalysisSummary

    RED 状态（当前）:
      - 路由使用 request.form.get("profile_id")，不接受 JSON body {resume_id, ai_consent}
      - 同步调用 AI 并直接返回 ready/failed，未走 runtime 异步路径（status 应初始为 queued）
      - retry 路由强制 ai_consent=True，不读请求体
      - 没有显式 provider 注入入口，HTTP 级测试无法注入 fake provider
    T109 实现后将转为 GREEN。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.app = create_app({
            "TESTING": True,
            "DB_PATH": self._tmp.name,
            "START_TASKS": False,
        })
        self.client = self.app.test_client()
        # 本地 API 保护中间件要求 X-Boss-Token；通过 /api/session 获取
        sess = self.client.get("/api/session")
        self.assertEqual(sess.status_code, 200, "session endpoint must return 200")
        self.token = sess.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        # 直接通过 store 建立画像与简历，避免依赖上传路由
        self.store = TaskStore(self._tmp.name)
        self.profile = self.store.create_profile("测试画像")
        self.resume = self.store.save_resume(
            self.profile["id"], "storage/contract.pdf", "pdf",
            _CONTRACT_RESUME_TEXT, "hash-contract", "contract.pdf",
        )

    def tearDown(self) -> None:
        # 先关闭 DiscoveryTaskRuntime 的 executor，避免后台线程持有 SQLite 连接
        runtime = self.app.config.get("DISCOVERY_RUNTIME") if hasattr(self, "app") else None
        if runtime is not None:
            try:
                runtime.shutdown()
            except Exception:
                pass
        if hasattr(self, "_tmp") and os.path.exists(self._tmp.name):
            try:
                os.unlink(self._tmp.name)
            except PermissionError:
                pass  # 后台线程可能仍持有连接；测试进程退出时会清理

    # --- helpers -------------------------------------------------------

    def _configure_ai_settings(self) -> None:
        """让 store.get_ai_settings().is_configured == True，以便 _build_ai_provider 继续执行。"""
        self.store.save_ai_settings(
            endpoint_url="https://test.example/v1",
            credential_ref="test-cred-ref",
            status="configured",
            model="test-model",
        )

    def _patch_ai_provider(self, response=None, raises=None) -> mock.Mock:
        """Patch webui.app.ai_service 以注入 fake DiscoveryAIProvider。

        路由内 _build_ai_provider 通过 ai_service.retrieve_api_key 和
        ai_service.DiscoveryAIProvider 构造 provider；patch 模块级 ai_service
        引用可同时覆盖同步路径和 runtime factory 路径。
        """
        fake_provider = mock.Mock()
        if raises is not None:
            fake_provider.analyze.side_effect = raises
        else:
            fake_provider.analyze.return_value = response or _contract_valid_ai_response()
        patcher = mock.patch("webui.app.ai_service")
        mock_ai = patcher.start()
        mock_ai.retrieve_api_key.return_value = "fake-api-key"
        mock_ai.DiscoveryAIProvider.return_value = fake_provider
        mock_ai.AISecurityError = AISecurityError
        self.addCleanup(patcher.stop)
        return fake_provider

    def _poll_until_terminal(self, analysis_id: str, timeout: float = 5.0,
                             interval: float = 0.05) -> str:
        """轮询 GET /api/discovery/analyses/{id} 直到 ready/failed。"""
        deadline = time.monotonic() + timeout
        last_status = None
        while time.monotonic() < deadline:
            resp = self.client.get(f"/api/discovery/analyses/{analysis_id}")
            if resp.status_code == 200:
                last_status = resp.get_json().get("status")
                if last_status in ("ready", "failed"):
                    return last_status
            time.sleep(interval)
        raise AssertionError(
            f"analysis {analysis_id} did not reach terminal state within {timeout}s "
            f"(last_status={last_status})"
        )

    def _create_ready_analysis_over_http(self) -> dict:
        self._configure_ai_settings()
        self._patch_ai_provider(response=_contract_valid_ai_response())
        response = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        self.assertEqual(response.status_code, 202)
        analysis_id = response.get_json()["analysis_id"]
        self.assertEqual(self._poll_until_terminal(analysis_id), "ready")
        return self.client.get(f"/api/discovery/analyses/{analysis_id}").get_json()

    # --- POST /api/discovery/analyses ----------------------------------

    def test_post_analyses_accepts_json_body_with_resume_id_and_consent(self):
        """契约: POST 接受 application/json {resume_id, ai_consent}，返回 202 + AnalysisSummary。"""
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": False},
        )
        self.assertEqual(resp.status_code, 202)
        data = resp.get_json()
        self.assertEqual(data["resume_id"], self.resume["id"])
        self.assertEqual(data["profile_id"], self.profile["id"])
        self.assertEqual(data["status"], "queued")
        self.assertIn("analysis_id", data)
        self.assertIn("version", data)

    def test_post_analyses_missing_resume_id_returns_400(self):
        """契约: 缺少 resume_id 返回 400 安全错误信封。"""
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"ai_consent": False},
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn("error_code", data)

    def test_post_analyses_consent_false_stays_queued_and_no_provider_call(self):
        """契约: consent=false 时分析保持 queued，不调用 AI provider。"""
        self._configure_ai_settings()
        fake_provider = self._patch_ai_provider()
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": False},
        )
        self.assertEqual(resp.status_code, 202)
        fake_provider.analyze.assert_not_called()
        analysis_id = resp.get_json()["analysis_id"]
        get_resp = self.client.get(f"/api/discovery/analyses/{analysis_id}")
        self.assertEqual(get_resp.get_json()["status"], "queued")

    def test_post_analyses_consent_true_with_provider_no_500(self):
        """契约: 已配置 + consent=true 不得 NameError/500，最终 ready 或安全 failed。"""
        self._configure_ai_settings()
        self._patch_ai_provider(response=_contract_valid_ai_response())
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        self.assertEqual(resp.status_code, 202)
        analysis_id = resp.get_json()["analysis_id"]
        status = self._poll_until_terminal(analysis_id, timeout=5.0)
        self.assertIn(status, ("ready", "failed"))

    def test_post_analyses_provider_timeout_returns_safe_envelope_no_500(self):
        """契约: provider 超时 -> 安全错误信封 (ai_timeout)，不泄漏原始异常。"""
        self._configure_ai_settings()
        self._patch_ai_provider(raises=TimeoutError("internal connection timeout trace"))
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        self.assertEqual(resp.status_code, 202)
        analysis_id = resp.get_json()["analysis_id"]
        status = self._poll_until_terminal(analysis_id, timeout=5.0)
        self.assertEqual(status, "failed")
        get_resp = self.client.get(f"/api/discovery/analyses/{analysis_id}")
        data = get_resp.get_json()
        failure = data.get("failure") or {}
        self.assertIn("error_code", failure)
        self.assertIn(
            failure["error_code"],
            ("ai_timeout", "ai_invalid_output", "ai_network_error"),
        )
        body = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("internal connection timeout trace", body)

    # --- POST /api/discovery/confirmations ----------------------------

    def test_post_confirmation_freezes_http_payload_and_returns_contract_schema(self):
        """T028/T029: 真实 HTTP 路由创建不可变确认版本并返回契约字段。"""
        analysis = self._create_ready_analysis_over_http()
        selected = [direction["id"] for direction in analysis["directions"][:2]]

        response = self.client.post("/api/discovery/confirmations", json={
            "analysis_id": analysis["analysis_id"],
            "enabled_direction_ids": selected,
            "hard_constraints": {"city": "北京", "salary": ""},
            "soft_preferences": {"industry": "AI"},
            "safe_limits": {"max_details": 12},
        })

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(
            set(("confirmation_id", "analysis_id", "version", "enabled_direction_ids", "confirmed_at")) - set(payload),
            set(),
        )
        self.assertEqual(payload["analysis_id"], analysis["analysis_id"])
        self.assertEqual(payload["enabled_direction_ids"], selected)
        persisted = self.store.get_confirmation(payload["confirmation_id"])
        self.assertEqual(persisted["hard_constraints"], {"city": "北京"})
        self.assertEqual(persisted["soft_preferences"], {"industry": "AI"})

    def test_post_confirmation_rejects_direction_from_another_analysis(self):
        """T029: 路由必须拒绝不属于本次分析的方向，不能冻结越界 ID。"""
        analysis = self._create_ready_analysis_over_http()
        response = self.client.post("/api/discovery/confirmations", json={
            "analysis_id": analysis["analysis_id"],
            "enabled_direction_ids": ["foreign-direction-id"],
            "hard_constraints": {},
            "soft_preferences": {},
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error_code"], "state_conflict")

    def test_post_analyses_response_does_not_leak_resume_text(self):
        """契约: 202 响应体不得包含简历正文片段。"""
        self._configure_ai_settings()
        self._patch_ai_provider(response=_contract_valid_ai_response())
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        self.assertEqual(resp.status_code, 202)
        body = json.dumps(resp.get_json(), ensure_ascii=False)
        self.assertNotIn("5年 Python 后端经验", body)
        self.assertNotIn(_CONTRACT_RESUME_TEXT, body)

    # --- GET /api/discovery/analyses/{id} ------------------------------

    def test_get_analysis_returns_full_state(self):
        """契约: GET 返回 analysis_id/resume_id/profile_id/status/evidence/directions/unknowns/failure。"""
        self._configure_ai_settings()
        self._patch_ai_provider(response=_contract_valid_ai_response())
        create_resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        analysis_id = create_resp.get_json()["analysis_id"]
        self._poll_until_terminal(analysis_id, timeout=5.0)

        resp = self.client.get(f"/api/discovery/analyses/{analysis_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for field in ("analysis_id", "resume_id", "profile_id", "status",
                       "evidence", "directions", "unknowns", "failure"):
            self.assertIn(field, data, f"missing field: {field}")

    def test_get_analysis_404_for_unknown_id(self):
        """契约: GET 未知 id 返回 404 安全错误信封。"""
        resp = self.client.get("/api/discovery/analyses/unknown-id")
        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertIn("error_code", data)

    def test_get_analysis_does_not_leak_resume_text(self):
        """契约: GET 响应不得包含简历正文。"""
        self._configure_ai_settings()
        self._patch_ai_provider(response=_contract_valid_ai_response())
        create_resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        analysis_id = create_resp.get_json()["analysis_id"]
        self._poll_until_terminal(analysis_id, timeout=5.0)
        resp = self.client.get(f"/api/discovery/analyses/{analysis_id}")
        body = json.dumps(resp.get_json(), ensure_ascii=False)
        self.assertNotIn(_CONTRACT_RESUME_TEXT, body)
        self.assertNotIn("5年 Python 后端经验", body)

    # --- POST /api/discovery/analyses/{id}/retry -----------------------

    def test_retry_creates_new_version(self):
        """契约: POST /retry 创建新版本分析，version 递增，analysis_id 不同。"""
        self._configure_ai_settings()
        self._patch_ai_provider(response=_contract_valid_ai_response())
        first_resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        first_data = first_resp.get_json()
        first_id = first_data["analysis_id"]
        first_version = first_data.get("version")
        self._poll_until_terminal(first_id, timeout=5.0)

        retry_resp = self.client.post(f"/api/discovery/analyses/{first_id}/retry")
        self.assertEqual(retry_resp.status_code, 202)
        retry_data = retry_resp.get_json()
        self.assertEqual(retry_data["version"], first_version + 1)
        self.assertNotEqual(retry_data["analysis_id"], first_id)
        self.assertEqual(retry_data["resume_id"], self.resume["id"])
        self._poll_until_terminal(retry_data["analysis_id"], timeout=5.0)

    def test_retry_respects_consent_false(self):
        """契约: retry 请求体 ai_consent=false 时新建分析保持 queued，不调用 provider。"""
        self._configure_ai_settings()
        fake_provider = self._patch_ai_provider(response=_contract_valid_ai_response())
        first_resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        first_id = first_resp.get_json()["analysis_id"]
        self._poll_until_terminal(first_id, timeout=5.0)
        calls_before_retry = fake_provider.analyze.call_count

        retry_resp = self.client.post(
            f"/api/discovery/analyses/{first_id}/retry",
            json={"ai_consent": False},
        )
        self.assertEqual(retry_resp.status_code, 202)
        retry_id = retry_resp.get_json()["analysis_id"]
        # consent=false -> provider 不应被再次调用
        self.assertEqual(fake_provider.analyze.call_count, calls_before_retry)
        get_resp = self.client.get(f"/api/discovery/analyses/{retry_id}")
        self.assertEqual(get_resp.get_json()["status"], "queued")

    def test_retry_unknown_analysis_returns_404(self):
        """契约: retry 未知 id 返回 404。"""
        resp = self.client.post("/api/discovery/analyses/unknown-id/retry")
        self.assertEqual(resp.status_code, 404)


class RunResultsHttpContractTests(unittest.TestCase):
    """T047/T048: run/results/retry HTTP contract.

    契约来源: contracts/openapi.yaml
      POST /api/discovery/runs  {confirmation_id} -> 202 Run
      GET  /api/discovery/runs/{id} -> 200 Run
      GET  /api/discovery/runs/{id}/results -> 200 {items: [JobResult], counts, next}
      POST /api/discovery/runs/{id}/jobs/{job_id}/retry -> 202 {accepted, run_id, job_id}

    Run schema required: run_id, confirmation_id, status, stage, progress, counts, updated_at
    JobResult schema required: job_id, title, company, source_url, completeness,
                               primary_assessment, assessments
    """

    def setUp(self):
        import tempfile
        from webui.app import create_app
        from webui.discovery import analyze_resume, confirm_directions
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.app = create_app({"TESTING": True, "DB_PATH": self._tmp.name, "START_TASKS": False})
        self.client = self.app.test_client()
        sess = self.client.get("/api/session")
        self.token = sess.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        self.store = TaskStore(self._tmp.name)
        self.profile = self.store.create_profile("run/results 测试画像")
        self.resume = self.store.save_resume(
            self.profile["id"], "storage/run_contract.pdf", "pdf",
            _CONTRACT_RESUME_TEXT, "hash-run-contract", "run_contract.pdf",
        )
        # 通过直接 Python 调用建立 ready analysis + confirmation（聚焦 runs 契约，不重复测试 analysis 契约）
        provider = _FakeAIProviderForRuns(_contract_valid_ai_response())
        self.analysis = analyze_resume(
            self.store, self.resume["id"], ai_consent=True, ai_provider=provider,
        )
        directions = self.store.list_directions(self.analysis["id"])
        self.direction_ids = [d["id"] for d in directions]
        self.confirmation = confirm_directions(
            self.store, self.analysis["id"], self.direction_ids,
            hard_constraints={"city": "北京"},
        )

    def tearDown(self):
        import os
        runtime = self.app.config.get("DISCOVERY_RUNTIME")
        if runtime:
            try:
                runtime.shutdown()
            except Exception:
                pass
        if os.path.exists(self._tmp.name):
            try:
                os.unlink(self._tmp.name)
            except PermissionError:
                pass

    def _create_run_directly(self, status="succeeded"):
        """通过 store API 创建 run，用于 /results 和 /retry 测试。"""
        import hashlib
        input_hash = hashlib.sha256(self.confirmation["id"].encode("utf-8")).hexdigest()
        run = self.store.create_discovery_run(
            profile_id=self.profile["id"], resume_id=self.resume["id"],
            analysis_id=self.analysis["id"], confirmation_id=self.confirmation["id"],
            input_hash=input_hash,
        )
        if status != "created":
            self.store.update_discovery_run(run["id"], status=status, stage="assembling")
        return run

    def _create_snapshot_and_assessment(self, run_id, *, job_id, category="high_match",
                                         direction_id=None, match_score=85,
                                         completeness="complete", hard_outcome="pass",
                                         source_url=None):
        """创建一个 job snapshot + 对应 assessment，用于 /results 测试。

        discovery_job_snapshots 有 FK job_id -> jobs(id)，需先在 jobs 表中创建 job。
        """
        # 先在 jobs 表中创建 job（save_job 返回的 job_id 是 UUID）
        canonical_url = (
            source_url if source_url is not None
            else f"https://www.zhipin.com/job_detail/{job_id}.html"
        )
        storage_url = canonical_url or f"boss://diagnostic/{job_id}"
        job = self.store.save_job(
            canonical_url=storage_url,
            source_url=canonical_url,
            title=f"岗位 {job_id}", company=f"公司 {job_id}",
            salary="20-40K", location="北京", jd="",
        )
        real_job_id = job["id"]
        snapshot = self.store.save_job_snapshot(
            run_id, real_job_id,
            source_url=canonical_url,
            title=f"岗位 {job_id}", company=f"公司 {job_id}",
            salary="20-40K", location="北京", completeness=completeness,
            source_status="active", fetch_status="ok",
        )
        did = direction_id or (self.direction_ids[0] if self.direction_ids else "d1")
        self.store.create_assessment(
            run_id, snapshot["id"], did,
            hard_outcome=hard_outcome, category=category,
            match_score=match_score, confidence=90,
            dimensions={
                "direction_alignment": {
                    "score": 85,
                    "candidate_evidence_refs": ["resume-evidence"],
                    "job_evidence_refs": ["title"],
                },
            },
            hard_checks={"city": "pass"}, status="completed",
        )
        return snapshot

    # --- POST /api/discovery/runs ---

    def test_post_runs_returns_202_with_run_schema(self):
        """契约: POST /api/discovery/runs {confirmation_id} 返回 202 + Run schema。"""
        resp = self.client.post("/api/discovery/runs", json={
            "confirmation_id": self.confirmation["id"],
        })
        self.assertEqual(resp.status_code, 202)
        data = resp.get_json()
        # Run schema required fields
        for field in ("run_id", "confirmation_id", "status", "stage",
                       "progress", "counts", "updated_at"):
            self.assertIn(field, data, f"Run missing required field: {field}")
        self.assertEqual(data["confirmation_id"], self.confirmation["id"])

    def test_post_runs_missing_confirmation_id_returns_400(self):
        """契约: 缺少 confirmation_id 返回 400。"""
        resp = self.client.post("/api/discovery/runs", json={})
        self.assertEqual(resp.status_code, 400)

    def test_post_runs_nonexistent_confirmation_returns_404(self):
        """契约: 不存在的 confirmation_id 返回 404。"""
        resp = self.client.post("/api/discovery/runs", json={
            "confirmation_id": "nonexistent-confirmation-id",
        })
        self.assertEqual(resp.status_code, 404)

    # --- GET /api/discovery/runs/{id} ---

    def test_get_run_returns_200_with_run_schema(self):
        """契约: GET /api/discovery/runs/{id} 返回 200 + Run schema。"""
        run = self._create_run_directly()
        resp = self.client.get(f"/api/discovery/runs/{run['id']}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["run_id"], run["id"])
        for field in ("run_id", "confirmation_id", "status", "stage",
                       "progress", "counts", "updated_at"):
            self.assertIn(field, data, f"Run missing required field: {field}")

    def test_get_run_nonexistent_returns_404(self):
        """契约: GET 不存在的 run 返回 404。"""
        resp = self.client.get("/api/discovery/runs/nonexistent-run-id")
        self.assertEqual(resp.status_code, 404)

    # --- GET /api/discovery/runs/{id}/results ---

    def test_get_results_returns_200_with_items_counts_next(self):
        """契约: GET /results 返回 200 + {items, counts, next}。"""
        run = self._create_run_directly()
        self._create_snapshot_and_assessment(run["id"], job_id="job-1", category="high_match")
        self._create_snapshot_and_assessment(run["id"], job_id="job-2", category="needs_review")
        resp = self.client.get(f"/api/discovery/runs/{run['id']}/results")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("items", data)
        self.assertIn("counts", data)
        self.assertIn("next", data)
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["counts"].get("high_match", 0), 1)
        self.assertEqual(data["counts"].get("needs_review", 0), 1)

    def test_results_route_downgrades_invalid_persisted_high_match(self):
        """T043: the user-facing HTTP path must enforce the portfolio high gate."""
        run = self._create_run_directly()
        self._create_snapshot_and_assessment(
            run["id"], job_id="partial-high", category="high_match", completeness="partial"
        )

        data = self.client.get(f"/api/discovery/runs/{run['id']}/results").get_json()

        self.assertEqual(data["items"][0]["primary_assessment"]["category"], "needs_review")
        self.assertEqual(data["counts"], {"needs_review": 1})

    def test_results_exclude_snapshot_without_valid_boss_source_url(self):
        """FR-042: 没有可访问 BOSS HTTPS 详情链接的快照不进入正式结果。"""
        run = self._create_run_directly()
        self._create_snapshot_and_assessment(
            run["id"], job_id="missing-source", source_url="",
        )
        self._create_snapshot_and_assessment(
            run["id"], job_id="foreign-source", source_url="https://example.com/job/1",
        )

        data = self.client.get(f"/api/discovery/runs/{run['id']}/results").get_json()

        self.assertEqual(data["items"], [])
        self.assertEqual(data["counts"], {})

    def test_results_items_match_job_result_schema(self):
        """契约: items 中每个元素必须匹配 JobResult schema 必填字段。"""
        run = self._create_run_directly()
        self._create_snapshot_and_assessment(run["id"], job_id="job-schema-1", category="high_match")
        resp = self.client.get(f"/api/discovery/runs/{run['id']}/results")
        self.assertEqual(resp.status_code, 200)
        items = resp.get_json()["items"]
        self.assertGreaterEqual(len(items), 1)
        item = items[0]
        # JobResult required fields per openapi.yaml
        for field in ("job_id", "title", "company", "source_url", "completeness",
                       "primary_assessment", "assessments"):
            self.assertIn(field, item, f"JobResult missing required field: {field}")
        # primary_assessment must be AssessmentSummary-shaped
        pa = item["primary_assessment"]
        self.assertIsNotNone(pa)
        for field in ("direction_id", "category", "hard_outcome"):
            self.assertIn(field, pa, f"AssessmentSummary missing required field: {field}")
        # assessments must be a list of AssessmentSummary
        self.assertIsInstance(item["assessments"], list)
        self.assertGreaterEqual(len(item["assessments"]), 1)

    def test_results_pagination_respects_limit(self):
        """契约: limit 参数限制返回 item 数量。"""
        run = self._create_run_directly()
        self._create_snapshot_and_assessment(run["id"], job_id="job-p1", category="high_match")
        self._create_snapshot_and_assessment(run["id"], job_id="job-p2", category="high_match")
        self._create_snapshot_and_assessment(run["id"], job_id="job-p3", category="high_match")
        resp = self.client.get(f"/api/discovery/runs/{run['id']}/results?limit=2")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertLessEqual(len(data["items"]), 2)

    def test_results_category_filter(self):
        """契约: category 过滤参数。"""
        run = self._create_run_directly()
        self._create_snapshot_and_assessment(run["id"], job_id="job-f1", category="high_match")
        self._create_snapshot_and_assessment(run["id"], job_id="job-f2", category="needs_review")
        resp = self.client.get(f"/api/discovery/runs/{run['id']}/results?category=high_match")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for item in data["items"]:
            self.assertEqual(item.get("primary_assessment", {}).get("category"), "high_match")

    def test_get_results_nonexistent_run_returns_404(self):
        """契约: GET 不存在 run 的 results 返回 404。"""
        resp = self.client.get("/api/discovery/runs/nonexistent-run-id/results")
        self.assertEqual(resp.status_code, 404)

    # --- POST /api/discovery/runs/{id}/jobs/{job_id}/retry ---

    def test_post_retry_job_returns_202(self):
        """契约: POST /jobs/{job_id}/retry 返回 202 + {accepted, run_id, job_id}。"""
        run = self._create_run_directly(status="partial")
        snapshot = self._create_snapshot_and_assessment(run["id"], job_id="job-retry-1", category="needs_review")
        real_job_id = snapshot["job_id"]
        resp = self.client.post(f"/api/discovery/runs/{run['id']}/jobs/{real_job_id}/retry")
        self.assertEqual(resp.status_code, 202)
        data = resp.get_json()
        self.assertTrue(data.get("accepted") is True)
        self.assertEqual(data["run_id"], run["id"])
        self.assertEqual(data["job_id"], real_job_id)

    def test_post_retry_job_terminal_run_returns_409(self):
        """契约: 已终态 run 的 job retry 返回 409。"""
        run = self._create_run_directly(status="succeeded")
        snapshot = self._create_snapshot_and_assessment(run["id"], job_id="job-terminal", category="high_match")
        real_job_id = snapshot["job_id"]
        resp = self.client.post(f"/api/discovery/runs/{run['id']}/jobs/{real_job_id}/retry")
        self.assertEqual(resp.status_code, 409)

    def test_post_retry_job_nonexistent_run_returns_404(self):
        """契约: 不存在 run 的 job retry 返回 404。"""
        resp = self.client.post("/api/discovery/runs/nonexistent-run-id/jobs/job-x/retry")
        self.assertEqual(resp.status_code, 404)

    # --- T118: dispatch beyond created within 5 seconds ---

    def test_post_run_advances_beyond_created_within_5_seconds(self):
        """T118 契约: POST /api/discovery/runs 后 5 秒内进入 planning 或明确 dispatch_failed。

        契约来源: contracts/openapi.yaml
          POST /api/discovery/runs 202: "returned state must advance to planning
          or expose a safe dispatch failure"
        """
        import time
        resp = self.client.post("/api/discovery/runs", json={
            "confirmation_id": self.confirmation["id"],
        })
        self.assertEqual(resp.status_code, 202)
        run_id = resp.get_json()["run_id"]
        deadline = time.monotonic() + 5.0
        status = "created"
        while time.monotonic() < deadline:
            poll = self.client.get(f"/api/discovery/runs/{run_id}")
            self.assertEqual(poll.status_code, 200)
            status = poll.get_json().get("status", "created")
            if status != "created":
                break
            time.sleep(0.1)
        self.assertNotEqual(
            status, "created",
            "POST /api/discovery/runs must advance run beyond 'created' within 5 seconds "
            "(contract: advance to planning or expose a safe dispatch failure)",
        )


class _FakeAIProviderForRuns:
    """Minimal fake AI provider for RunResultsHttpContractTests setup.

    Never calls a real service; returns a pre-built v2-shape response.
    """

    def __init__(self, response):
        self._response = response

    def analyze(self, resume_text):
        return self._response


class FeedbackHttpContractTests(unittest.TestCase):
    """T059/T127: feedback HTTP contract."""

    def setUp(self):
        import tempfile
        from webui.app import create_app
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.app = create_app({"TESTING": True, "DB_PATH": self._tmp.name, "START_TASKS": False})
        self.client = self.app.test_client()
        sess = self.client.get("/api/session")
        self.token = sess.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        from webui.store import TaskStore
        self.store = TaskStore(self._tmp.name)
        self.profile = self.store.create_profile("feedback 测试画像")

    def _insert_source_job(self, job_id):
        now = "2026-07-15T00:00:00+00:00"
        source_url = f"https://www.zhipin.com/job_detail/{job_id}.html"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, '', '', '', '', '', ?, ?)",
                (job_id, source_url, source_url, now, now),
            )

    def tearDown(self):
        import os
        runtime = self.app.config.get("DISCOVERY_RUNTIME")
        if runtime:
            try:
                runtime.shutdown()
            except Exception:
                pass
        if os.path.exists(self._tmp.name):
            try:
                os.unlink(self._tmp.name)
            except PermissionError:
                pass

    def test_post_feedback_creates_and_returns_201(self):
        """POST /api/discovery/feedback 创建反馈返回 201 + feedback_id。"""
        resp = self.client.post("/api/discovery/feedback", json={
            "profile_id": self.profile["id"],
            "target_type": "job",
            "target_id": "job-123",
            "action": "like",
            "scope": "exact_job",
        })
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertIn("feedback_id", data)

    def test_job_feedback_defaults_to_exact_job_and_lists_visible_change(self):
        self._insert_source_job("job-exact")
        created = self.client.post("/api/discovery/feedback", json={
            "profile_id": self.profile["id"],
            "target_type": "job",
            "target_id": "job-exact",
            "action": "not_interested",
            "reason_code": "company_unsuitable",
        })
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["effective_scope"], "exact_job")

        listed = self.client.get(
            f"/api/discovery/feedback?profile_id={self.profile['id']}"
        )
        self.assertEqual(listed.status_code, 200)
        body = listed.get_json()
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["reason_code"], "company_unsuitable")
        self.assertEqual(body["preference_changes"][0]["kind"], "job_excluded")
        self.assertEqual(self.store.get_profile_job(self.profile["id"], "job-exact")["status"], "deleted")

    def test_revoke_not_interested_restores_job_and_removes_active_trash(self):
        self._insert_source_job("job-restore")
        created = self.client.post("/api/discovery/feedback", json={
            "profile_id": self.profile["id"],
            "target_type": "job",
            "target_id": "job-restore",
            "action": "not_interested",
        }).get_json()

        revoked = self.client.post(
            f"/api/discovery/feedback/{created['feedback_id']}/revoke"
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertTrue(revoked.get_json()["revoked"])
        self.assertEqual(self.store.get_profile_job(self.profile["id"], "job-restore")["status"], "new")
        self.assertEqual(self.store.list_trash_with_origin(self.profile["id"]), [])

        listed = self.client.get(
            f"/api/discovery/feedback?profile_id={self.profile['id']}"
        ).get_json()
        self.assertEqual(listed["items"], [])
        self.assertEqual(listed["preference_changes"], [])

    def test_revoke_not_interested_restores_preexisting_long_term_interest(self):
        job = self.store.save_job(
            canonical_url="https://www.zhipin.com/job_detail/legacy-interest.html",
            source_url="https://www.zhipin.com/job_detail/legacy-interest.html",
            title="历史感兴趣岗位", company="历史公司", salary="", location="", jd="",
        )
        self.store.link_profile_job(
            self.profile["id"], job["id"], "legacy-run", "legacy-run", status="interested"
        )
        created = self.client.post("/api/discovery/feedback", json={
            "profile_id": self.profile["id"],
            "target_type": "job",
            "target_id": job["id"],
            "action": "not_interested",
        }).get_json()

        self.client.post(f"/api/discovery/feedback/{created['feedback_id']}/revoke")

        restored = self.store.get_profile_job(self.profile["id"], job["id"])
        self.assertEqual(restored["status"], "interested")

    def test_direction_feedback_is_visible_and_does_not_use_exact_job_scope(self):
        resume = self.store.save_resume(
            self.profile["id"], "feedback/resume.txt", "txt", "脱敏简历", "feedback-hash", "resume.txt"
        )
        analysis = self.store.create_analysis(resume["id"], self.profile["id"])
        direction = self.store.add_direction(
            analysis["id"], "后端工程", "core", search_terms=["Python"]
        )
        created = self.client.post("/api/discovery/feedback", json={
            "profile_id": self.profile["id"],
            "target_type": "direction",
            "direction_id": direction["id"],
            "action": "direction_disable",
            "reason_code": "direction_not_wanted",
            "scope": "direction",
        })
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["effective_scope"], "direction")

        listed = self.client.get(
            f"/api/discovery/feedback?profile_id={self.profile['id']}"
        ).get_json()
        self.assertEqual(listed["preference_changes"][0]["kind"], "direction_disabled")
        self.assertEqual(listed["preference_changes"][0]["direction_id"], direction["id"])

    def test_post_feedback_missing_field_returns_400(self):
        """缺少必填字段返回 400 安全错误信封。"""
        resp = self.client.post("/api/discovery/feedback", json={
            "profile_id": self.profile["id"],
            "target_type": "job",
            # missing target_id and action
        })
        self.assertEqual(resp.status_code, 400)

    def test_revoke_feedback_returns_200(self):
        """POST /api/discovery/feedback/<id>/revoke 撤销反馈返回 200。"""
        create_resp = self.client.post("/api/discovery/feedback", json={
            "profile_id": self.profile["id"],
            "target_type": "job",
            "target_id": "job-456",
            "action": "dislike",
        })
        fid = create_resp.get_json()["feedback_id"]
        revoke_resp = self.client.post(f"/api/discovery/feedback/{fid}/revoke")
        self.assertEqual(revoke_resp.status_code, 200)

    def test_revoke_nonexistent_returns_404(self):
        """撤销不存在的反馈返回 404。"""
        resp = self.client.post("/api/discovery/feedback/nonexistent/revoke")
        self.assertEqual(resp.status_code, 404)


class CancelResumeHttpContractTests(unittest.TestCase):
    """T070/T125: cancel/resume HTTP contract."""

    def setUp(self):
        import tempfile
        from webui.app import create_app
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.app = create_app({"TESTING": True, "DB_PATH": self._tmp.name, "START_TASKS": False})
        self.client = self.app.test_client()
        sess = self.client.get("/api/session")
        self.token = sess.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        from webui.store import TaskStore
        self.store = TaskStore(self._tmp.name)
        self.profile = self.store.create_profile("cancel/resume 测试画像")

    def tearDown(self):
        import os
        runtime = self.app.config.get("DISCOVERY_RUNTIME")
        if runtime:
            try:
                runtime.shutdown()
            except Exception:
                pass
        if os.path.exists(self._tmp.name):
            try:
                os.unlink(self._tmp.name)
            except PermissionError:
                pass

    def test_cancel_nonexistent_run_returns_404(self):
        """cancel 不存在的 run 返回 404。"""
        resp = self.client.post("/api/discovery/runs/nonexistent/cancel")
        self.assertEqual(resp.status_code, 404)

    def test_resume_nonexistent_run_returns_404(self):
        """resume 不存在的 run 返回 404。"""
        resp = self.client.post("/api/discovery/runs/nonexistent/resume")
        self.assertEqual(resp.status_code, 404)

    def _create_run_directly(self, status="succeeded"):
        """直接用 SQL 创建 run，绕过 FK 约束。"""
        import sqlite3, uuid
        run_id = str(uuid.uuid4())
        with sqlite3.connect(self._tmp.name) as conn:
            conn.execute(
                "INSERT INTO discovery_runs (id, profile_id, resume_id, analysis_id, "
                "confirmation_id, input_hash, status, stage, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, self.profile["id"], "r1", "a1", "c1", "h1",
                 status, "assembling", "2025-01-01", "2025-01-01"),
            )
        return {"id": run_id}

    def test_cancel_terminal_run_returns_409(self):
        """cancel 已终态的 run 返回 409 state_conflict。"""
        run = self._create_run_directly(status="succeeded")
        resp = self.client.post(f"/api/discovery/runs/{run['id']}/cancel")
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json() or {}
        self.assertEqual(data.get("error_code"), "state_conflict")

    def test_resume_non_resumable_returns_409(self):
        """resume 不可恢复状态（如 succeeded）的 run 返回 409。"""
        run = self._create_run_directly(status="succeeded")
        resp = self.client.post(f"/api/discovery/runs/{run['id']}/resume")
        self.assertEqual(resp.status_code, 409)

    def test_cancel_active_run_returns_202_and_persists_cancel_request(self):
        """T070: active run 的 HTTP cancel 返回 202，并先持久化取消事件。"""
        run = self._create_run_directly(status="fetching_lists")
        response = self.client.post(f"/api/discovery/runs/{run['id']}/cancel")

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertEqual(payload["run_id"], run["id"])
        persisted = self.store.get_discovery_run(run["id"])
        self.assertIsNotNone(persisted["cancel_requested_at"])
        events = self.store.list_discovery_events(run["id"])
        self.assertEqual(events[-1]["event_type"], "cancel_requested")

    def test_resume_interrupted_run_returns_202_and_resubmits_runtime(self):
        """T071: interrupted run 的 HTTP resume 必须调用 runtime 重新提交。"""
        run = self._create_run_directly(status="interrupted")
        runtime = self.app.config["DISCOVERY_RUNTIME"]
        with mock.patch.object(runtime, "resume_run", wraps=runtime.resume_run) as resume_spy:
            response = self.client.post(f"/api/discovery/runs/{run['id']}/resume")

        self.assertEqual(response.status_code, 202)
        resume_spy.assert_called_once_with(run["id"])
        events = self.store.list_discovery_events(run["id"])
        self.assertIn("resume_accepted", [event["event_type"] for event in events])


class ResultTraceabilityTests(unittest.TestCase):
    """T053: result traceability (resume/confirmation/run identifiers). (Phase 5)"""

    def test_portfolio_carries_all_traceability_refs(self):
        """T054: portfolio must carry resume_id/analysis_id/confirmation_id/run_id."""
        assessments = [
            {
                "job_id": "j1", "direction_id": "d1", "category": "high_match",
                "title": "Python", "company": "A", "salary": "20k", "location": "北京",
                "ai_assessment": {"match_score": 90, "confidence": 88},
                "hard_rule_outcome": "pass",
            },
        ]
        directions = [{"id": "d1", "name": "后端"}]
        portfolio = build_portfolio(
            "run-1", assessments, directions,
            resume_id="res-1", analysis_id="an-1", confirmation_id="conf-1",
        )
        self.assertEqual(portfolio["run_id"], "run-1")
        self.assertEqual(portfolio["resume_id"], "res-1")
        self.assertEqual(portfolio["analysis_id"], "an-1")
        self.assertEqual(portfolio["confirmation_id"], "conf-1")
        self.assertIn("policy_version", portfolio)

    def test_portfolio_item_carries_run_id(self):
        """Each portfolio item must carry run_id for traceability."""
        assessments = [
            {
                "job_id": "j1", "direction_id": "d1", "category": "high_match",
                "title": "Python", "company": "A",
                "ai_assessment": {"match_score": 90},
                "hard_rule_outcome": "pass",
            },
        ]
        directions = [{"id": "d1", "name": "后端"}]
        portfolio = build_portfolio("run-tr-1", assessments, directions)
        self.assertGreater(len(portfolio["items"]), 0)
        self.assertEqual(portfolio["items"][0]["run_id"], "run-tr-1")

    def test_portfolio_defaults_empty_refs_when_not_provided(self):
        """When traceability refs are not provided, they default to empty strings."""
        portfolio = build_portfolio("run-x", [], [])
        self.assertEqual(portfolio["resume_id"], "")
        self.assertEqual(portfolio["analysis_id"], "")
        self.assertEqual(portfolio["confirmation_id"], "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
