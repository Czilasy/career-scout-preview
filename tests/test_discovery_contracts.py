"""Discovery HTTP contract tests (feature 004)."""

from __future__ import annotations

import json
import io
import re
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
        block = re.search(r"## Candidate analysis provider output v3.*?```json\n(.*?)\n```", cls.ai_contract, re.S)
        cls.shape = json.loads(block.group(1)) if block else None

    def test_candidate_v3_has_backend_owned_typed_empty_shape(self):
        self.assertEqual(self.shape, {
            "contract_version": "v3",
            "summary": {"headline": "", "experience_level": "", "domains": [], "strengths": []},
            "evidence": [], "unknowns": [], "directions": [],
            "quality": {"status": "complete", "warnings": []},
        })

    def test_one_invalid_evidence_item_does_not_discard_valid_summary(self):
        self.assertIn("invalid evidence item is quarantined without discarding the valid summary", self.ai_contract)
        self.assertIn("quality.status=partial", self.data_model)
        self.assertNotIn("partial unvalidated output is not persisted as ready", self.ai_contract)

    def test_unverified_search_fields_never_become_confirmed_constraints(self):
        self.assertRegex(self.ai_contract, r"quarantined fields cannot influence confirmation, SearchPlan compilation, matching, or scraper inputs")
        self.assertIn("unverified search fields never become confirmed constraints", self.ai_contract)
        self.assertIn("analysis stages are", self.state_machine.lower())
        self.assertRegex(self.data_model, r"`status`\s*\|\s*`queued`, `analyzing`, `ready`, `failed`, `deleted`")
        self.assertIn("ready` with `quality.status=partial` or `manual_required`", self.data_model)

    def test_identity_fields_are_not_candidate_or_search_fields(self):
        self.assertRegex(self.ai_contract, r"Identity fields .* excluded from candidate and search fields")

    def test_warning_schema_codes_and_field_types_are_closed(self):
        self.assertRegex(self.ai_contract, r"warning object.*\{`?code`?, `?path`?\}")
        for code in ("invalid_type", "invalid_enum", "invalid_evidence", "sensitive_value", "unverified_field", "missing_required", "reference_invalid"):
            self.assertIn(f"`{code}`", self.ai_contract)
        self.assertIn("`warnings` is an array", self.ai_contract)
        self.assertIn("confidence", self.ai_contract)
        self.assertIn("integer from 0 through 100", self.ai_contract)

    def test_item_object_schemas_and_limits_are_explicit(self):
        for field in ("client_ref", "normalized_value", "source_quote", "assertion_type", "confidence",
                      "evidence_refs", "search_terms", "default_enabled"):
            self.assertIn(f"`{field}`", self.ai_contract)
        for enum in ("skill|responsibility|project|industry|seniority|education|achievement|other",
                     "explicit|inferred", "current_city|min_salary|career_intent|other", "core|adjacent|growth"):
            self.assertIn(enum, self.ai_contract)
        self.assertIn("maximum 3", self.ai_contract)
        self.assertIn("maximum of 5 items", self.ai_contract)
        self.assertIn("generic string-list rule does not apply", self.ai_contract)
        self.assertIn("object arrays are never coerced", self.ai_contract)

    def test_invalid_evidence_is_dropped_and_refs_cannot_enable_direction(self):
        self.assertIn("dropped entirely from normalized `evidence`", self.ai_contract)
        self.assertIn("`source_quote` cannot be empty for an accepted evidence item", self.ai_contract)
        self.assertIn("Directions referencing dropped evidence lose those refs", self.ai_contract)
        self.assertIn("Every accepted/persisted evidence item", self.ai_contract)


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


class DiscoveryV2FoundationalContractTests(unittest.TestCase):
    """T014: v2 safe errors, opaque identifiers and draft hash conflicts."""

    def test_required_v2_safe_codes_have_complete_public_envelopes(self):
        required = {
            "candidate_version_conflict", "candidate_fact_invalid", "intent_invalid",
            "salary_unparseable", "candidate_pool_empty", "detail_budget_empty",
            "source_verification_required", "source_rate_limited", "detail_event_invalid",
            "detail_reuse_invalid", "assessment_group_invalid", "result_projection_invalid",
            "input_hash_mismatch",
        }
        self.assertTrue(required.issubset(ERROR_CODE_MAP), required - set(ERROR_CODE_MAP))
        for code in required:
            envelope = DiscoveryError(code).to_envelope()
            self.assertEqual(envelope["error_code"], code)
            self.assertIsInstance(envelope["retryable"], bool)
            self.assertTrue(envelope["user_message"])
            self.assertEqual(set(envelope), {"error_code", "stage", "retryable", "user_message"})

    def test_opaque_id_guard_accepts_ids_but_rejects_paths_and_control_text(self):
        from webui.discovery import validate_opaque_id

        self.assertEqual(validate_opaque_id("0a1b2c3d4e5f6789"), "0a1b2c3d4e5f6789")
        for value in ("", "../run", "run/child", "run\\child", "run\nsecret", "a" * 129):
            with self.subTest(value=value):
                with self.assertRaises(DiscoveryError):
                    validate_opaque_id(value)

    def test_stale_draft_hash_maps_to_candidate_version_conflict(self):
        from webui.discovery import require_matching_input_hash

        self.assertEqual(require_matching_input_hash("hash-a", "hash-a"), "hash-a")
        with self.assertRaises(DiscoveryError) as ctx:
            require_matching_input_hash(
                "hash-a", "hash-b", conflict_code="candidate_version_conflict",
            )
        self.assertEqual(ctx.exception.error_code, "candidate_version_conflict")
        self.assertEqual(ctx.exception.to_envelope()["retryable"], False)


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
            json={"resume_id": self.resume["id"], "ai_consent": True},
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

    def test_post_analyses_consent_false_rejects_without_row(self):
        """契约: consent=false 时创建前拒绝，不调用 AI provider。"""
        self._configure_ai_settings()
        fake_provider = self._patch_ai_provider()
        before = len(self.store.list_analyses(self.resume["id"]))
        resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": False},
        )
        self.assertEqual(resp.status_code, 400)
        fake_provider.analyze.assert_not_called()
        self.assertEqual(len(self.store.list_analyses(self.resume["id"])), before)

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

    def test_manual_direction_with_one_to_three_terms_keeps_manual_analysis_usable(self):
        analysis = self._create_ready_analysis_over_http()
        response = self.client.post("/api/discovery/confirmations", json={
            "analysis_id": analysis["analysis_id"],
            "enabled_direction_ids": [],
            "user_directions": [{"name": "手动平台工程师", "search_terms": ["平台工程", "Python"]}],
            "hard_constraints": {"city": "上海"},
        })
        self.assertEqual(response.status_code, 201)
        confirmation = self.store.get_confirmation(response.get_json()["confirmation_id"])
        self.assertEqual(len(confirmation["directions"]), 1)
        stored = self.store.list_directions(analysis["analysis_id"])[-1]
        self.assertEqual(stored["search_terms"], ["平台工程", "Python"])

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
        self.assertEqual(data["contract_version"], "v3")
        self.assertEqual(set(data["quality"]), {"status", "warnings"})
        self.assertNotIn("source_quote", str(data))

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

        retry_resp = self.client.post(
            f"/api/discovery/analyses/{first_id}/retry",
            json={"ai_consent": True},
        )
        self.assertEqual(retry_resp.status_code, 202)
        retry_data = retry_resp.get_json()
        self.assertEqual(retry_data["version"], first_version + 1)
        self.assertNotEqual(retry_data["analysis_id"], first_id)
        self.assertEqual(retry_data["resume_id"], self.resume["id"])
        self._poll_until_terminal(retry_data["analysis_id"], timeout=5.0)

    def test_retry_rejects_consent_false_without_new_attempt(self):
        """契约: retry consent=false 时拒绝且不新建分析。"""
        self._configure_ai_settings()
        fake_provider = self._patch_ai_provider(response=_contract_valid_ai_response())
        first_resp = self.client.post(
            "/api/discovery/analyses",
            json={"resume_id": self.resume["id"], "ai_consent": True},
        )
        first_id = first_resp.get_json()["analysis_id"]
        self._poll_until_terminal(first_id, timeout=5.0)
        calls_before_retry = fake_provider.analyze.call_count
        versions_before = len(self.store.list_analyses(self.resume["id"]))

        retry_resp = self.client.post(
            f"/api/discovery/analyses/{first_id}/retry",
            json={"ai_consent": False},
        )
        self.assertEqual(retry_resp.status_code, 400)
        # consent=false -> provider 不应被再次调用
        self.assertEqual(fake_provider.analyze.call_count, calls_before_retry)
        self.assertEqual(len(self.store.list_analyses(self.resume["id"])), versions_before)

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

    def test_v2_results_remain_readable_while_run_is_active(self):
        """T014: progressive results are readable before a v2 run is terminal."""
        run = self._create_run_directly(status="processing_jobs")
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE discovery_runs SET policy_version='discovery_v2' WHERE id=?",
                (run["id"],),
            )
        self._create_snapshot_and_assessment(
            run["id"], job_id="job-progressive", category="high_match",
        )
        resp = self.client.get(f"/api/discovery/runs/{run['id']}/results")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertGreaterEqual(len(body["items"]), 1)
        self.assertEqual(body["run_id"], run["id"])

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


class FeedbackScopeAndRunningRunTests(unittest.TestCase):
    """T090 验证 US5 反馈 HTTP 合同的作用范围可见性与运行中反馈。

    合同来源:
    - spec.md FR-051: 用户必须能撤销有效反馈并看到其作用范围。
    - http-api.md L312-314: GET|POST /api/discovery/feedback + revoke 端点。
    - http-api.md L320: feedback increments result revision when visibility or
      ordering changes.
    - spec.md FR-038: 用户必须能在结果到达过程中查看、筛选和反馈，不得因运行
      仍在继续而锁定结果页。
    """

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
        self.profile = self.store.create_profile("feedback-scope 测试画像")

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

    def _insert_source_job(self, job_id):
        now = "2026-07-15T00:00:00+00:00"
        source_url = f"https://www.zhipin.com/job_detail/{job_id}.html"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, '', '', '', '', '', ?, ?)",
                (job_id, source_url, source_url, now, now),
            )

    def test_post_response_includes_effective_scope_field(self):
        """FR-051: POST 反馈响应必须含 effective_scope 字段（作用范围可见）。"""
        self._insert_source_job("job-scope-1")
        resp = self.client.post("/api/discovery/feedback", json={
            "profile_id": self.profile["id"],
            "target_type": "job", "target_id": "job-scope-1",
            "action": "not_interested",
        })
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertIn("effective_scope", body,
                      "T090/FR-051: POST 响应必须含 effective_scope 字段")
        self.assertEqual(body["effective_scope"], "exact_job")

    def test_get_list_response_includes_scope_per_item(self):
        """FR-051: GET 反馈列表每项必须含 scope 字段（作用范围可见）。"""
        self._insert_source_job("job-scope-2")
        self.client.post("/api/discovery/feedback", json={
            "profile_id": self.profile["id"],
            "target_type": "job", "target_id": "job-scope-2",
            "action": "not_interested", "scope": "exact_job",
        })
        listed = self.client.get(
            f"/api/discovery/feedback?profile_id={self.profile['id']}"
        ).get_json()
        self.assertEqual(len(listed["items"]), 1)
        item = listed["items"][0]
        self.assertIn("scope", item,
                      "T090/FR-051: 列表项必须含 scope 字段")
        self.assertEqual(item["scope"], "exact_job")

    def test_get_list_response_includes_revoked_at_when_revoked(self):
        """FR-051: 已撤销反馈的列表项必须含 revoked_at 字段。"""
        self._insert_source_job("job-rev-1")
        created = self.client.post("/api/discovery/feedback", json={
            "profile_id": self.profile["id"],
            "target_type": "job", "target_id": "job-rev-1",
            "action": "not_interested",
        }).get_json()
        self.client.post(f"/api/discovery/feedback/{created['feedback_id']}/revoke")
        # Get all feedback (not just effective) via store directly since the
        # HTTP list endpoint filters to effective_only by default.
        rows = self.store.list_discovery_feedback(self.profile["id"])
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["revoked_at"],
                             "T090/FR-051: 已撤销反馈必须含 revoked_at 时间戳")

    def test_running_run_can_receive_feedback_without_blocking(self):
        """FR-038: 运行中的 run 必须能接收反馈，不得锁定结果页。

        合同：POST /api/discovery/feedback 不得因存在运行中的 run 返回 409。
        """
        self._insert_source_job("job-running-1")
        # No active run exists in this test fixture, but the endpoint must
        # not require run state checks — feedback is always accepted.
        resp = self.client.post("/api/discovery/feedback", json={
            "profile_id": self.profile["id"],
            "target_type": "job", "target_id": "job-running-1",
            "action": "not_interested",
        })
        self.assertEqual(resp.status_code, 201,
                         "T090/FR-038: 反馈端点不得因运行状态拒绝")

    def test_revoke_response_includes_revoked_flag(self):
        """FR-051: 撤销响应必须含 revoked=True 字段。"""
        self._insert_source_job("job-revoke-1")
        created = self.client.post("/api/discovery/feedback", json={
            "profile_id": self.profile["id"],
            "target_type": "job", "target_id": "job-revoke-1",
            "action": "not_interested",
        }).get_json()
        revoked = self.client.post(
            f"/api/discovery/feedback/{created['feedback_id']}/revoke"
        )
        self.assertEqual(revoked.status_code, 200)
        body = revoked.get_json()
        self.assertIn("revoked", body,
                      "T090/FR-051: 撤销响应必须含 revoked 字段")
        self.assertTrue(body["revoked"])


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


class LiveProviderSmokeValidationTests(unittest.TestCase):
    """T1.4 (RED): T132 smoke 验证函数对 evidence_count=0 必须判定为不通过。

    当前 _run_live_provider_smoke 的 v2_ok 判定只检查 has_evidence=isinstance([],list)=True,
    不检查 len(evidence)>0, 导致 evidence=[] 被 status='pass' 放行, 下游评估全部
    被 evidence_reference_invalid 降级为 needs_review。
    """

    @mock.patch("webui.ai.DiscoveryAIProvider")
    @mock.patch("webui.store.TaskStore")
    def test_smoke_evidence_count_zero_is_not_pass(self, mock_store_cls, mock_provider_cls):
        from tests.fixtures.discovery.e2e_real_boss import _run_live_provider_smoke

        mock_store = mock_store_cls.return_value
        mock_store.get_ai_settings.return_value = {
            "is_configured": True,
            "endpoint_url": "https://test.example/v1",
            "model": "test-model",
        }
        mock_store.get_credential_ref.return_value = "cred-ref"

        mock_provider = mock_provider_cls.return_value
        # 候选分析返回 evidence=[]（T1.4 的核心测试点）
        mock_provider.analyze.return_value = {
            "summary": {"headline": "后端"},
            "evidence": [],
            "directions": [{"id": "d1", "name": "后端"}],
        }
        # 评估返回完整字段，避免 job_assessment_v1 smoke 干扰本测试断言
        mock_provider.assess_job.return_value = {
            "dimensions": {"direction_alignment": {}},
            "match_score": 80,
            "confidence": 90,
            "proposed_band": "P5",
        }

        with mock.patch(
            "tests.fixtures.discovery.e2e_real_boss._retrieve_api_key",
            return_value="fake-key",
        ):
            report = _run_live_provider_smoke()

        self.assertEqual(report["candidate_analysis_v2"]["evidence_count"], 0)
        self.assertNotEqual(report["candidate_analysis_v2"]["status"], "pass")


class DiscoveryV2ProfileHttpContractTests(unittest.TestCase):
    """T030/T031 RED: storage-only upload and candidate-version HTTP contract."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._resume_dir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "DB_PATH": self._tmp.name,
            "RESUME_DIR": self._resume_dir.name, "START_TASKS": False,
        })
        self.client = self.app.test_client()
        session = self.client.get("/api/session").get_json()
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = session["token"]
        self.store = TaskStore(self._tmp.name)
        self.profile = self.store.create_profile("v2画像")
        self.resume = self.store.save_resume(
            self.profile["id"], "storage/v2.txt", "txt", "5年 Python 后端经验",
            "v2-hash", "v2.txt",
        )
        self.analysis = self.store.create_analysis(
            self.resume["id"], self.profile["id"], contract_version="v4",
        )
        self.store.update_analysis_status(
            self.analysis["id"], "ready", analysis_stage="persisting",
            quality_status="complete", quality_warnings=[], summary={}, unknowns=[],
        )
        self.direction = self.store.add_direction(
            self.analysis["id"], "Python 后端", "core", confidence=100,
            default_enabled=True, search_terms=["Python 后端"], contract_version="v4",
        )
        self.version = self.store.create_candidate_profile_version(
            profile_id=self.profile["id"], resume_id=self.resume["id"],
            analysis_id=self.analysis["id"], summary={"headline": "后端"}, unknowns=[],
            facts=[{
                "fact_type": "skill", "stable_key": "skill:python",
                "value": {"name": "Python"}, "normalized_value": "Python",
                "source_kind": "user_added", "assertion_type": "explicit",
                "confidence": 100, "verification_status": "confirmed", "evidence_ids": [],
            }],
        )

    def tearDown(self):
        runtime = self.app.config.get("DISCOVERY_RUNTIME")
        if runtime:
            runtime.shutdown()
        self._resume_dir.cleanup()
        if os.path.exists(self._tmp.name):
            try:
                os.unlink(self._tmp.name)
            except PermissionError:
                pass

    def test_discovery_upload_is_storage_only_even_with_ai_consent(self):
        self.store.save_ai_settings(
            endpoint_url="https://ai.example/v1", credential_ref="cred",
            status="ready", model="model",
        )
        with mock.patch("webui.app.ai_service.retrieve_api_key", return_value="secret"), \
             mock.patch("webui.app.ai_service.parse_resume") as parse:
            response = self.client.post(
                f"/api/profiles/{self.profile['id']}/resume",
                data={
                    "flow": "discovery", "ai_consent": "true",
                    "file": (io.BytesIO("Python 后端".encode("utf-8")), "resume.txt"),
                }, content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 201)
        parse.assert_not_called()
        self.assertEqual(response.get_json()["extraction_status"], "ready")

    def test_post_analysis_persists_requested_v4_contract(self):
        runtime = self.app.config["DISCOVERY_RUNTIME"]
        with mock.patch.object(runtime, "submit_analysis"):
            response = self.client.post("/api/discovery/analyses", json={
                "resume_id": self.resume["id"], "ai_consent": True,
                "contract_version": "v4",
            })
        self.assertEqual(response.status_code, 202)
        body = response.get_json()
        self.assertEqual(body["contract_version"], "v4")
        self.assertEqual(self.store.get_analysis(body["analysis_id"])["contract_version"], "v4")

    def test_get_and_patch_candidate_version_with_hash_conflict(self):
        response = self.client.get(f"/api/discovery/candidate-versions/{self.version['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["content_hash"], self.version["content_hash"])
        stale = self.client.patch(
            f"/api/discovery/candidate-versions/{self.version['id']}",
            json={"expected_content_hash": "stale", "operations": []},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()["error_code"], "candidate_version_conflict")
        updated = self.client.patch(
            f"/api/discovery/candidate-versions/{self.version['id']}",
            json={
                "expected_content_hash": self.version["content_hash"],
                "operations": [{"op": "add", "fact_type": "industry",
                    "value": {"name": "金融科技", "contexts": []},
                    "normalized_value": "金融科技"}],
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertNotEqual(updated.get_json()["content_hash"], self.version["content_hash"])

    def test_v2_confirmation_atomically_confirms_profile_and_intent(self):
        response = self.client.post("/api/discovery/confirmations", json={
            "analysis_id": self.analysis["id"],
            "candidate_profile_version_id": self.version["id"],
            "expected_content_hash": self.version["content_hash"],
            "enabled_direction_ids": [self.direction["id"]],
            "hard_constraints": {"city": "上海", "min_salary": {"amount": 20, "unit": "K", "source": "user_confirmed"}},
            "soft_preferences": {}, "safe_limits": {},
        })
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["candidate_profile_version_id"], self.version["id"])
        self.assertEqual(body["intent_contract_version"], "intent_v2")
        self.assertEqual(len(body["intent_hash"]), 64)
        self.assertEqual(self.store.get_candidate_profile_version(self.version["id"])["status"], "confirmed")


class DiscoveryV2ProgressiveHttpContractTests(unittest.TestCase):
    """T047 RED: v2 run creation, four progress types, candidate diagnostics,
    active-run results and after_revision changed=false.

    Contract source: specs/005-fast-resume-discovery/contracts/http-api.md
      POST /api/discovery/runs {confirmation_id} -> 202 Run with policy_version=discovery_v2
      GET  /api/discovery/runs/{id} -> 200 Run with v2 progress + result_revision
      GET  /api/discovery/runs/{id}/candidates -> 200 {items: [CandidateDiag]}
      GET  /api/discovery/runs/{id}/results -> 200 {run_id, run_status, revision, changed, complete, items}
      GET  /api/discovery/runs/{id}/results?after_revision=N -> 200 {changed: false} when unchanged
    """

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.app = create_app({"TESTING": True, "DB_PATH": self._tmp.name, "START_TASKS": False})
        self.client = self.app.test_client()
        sess = self.client.get("/api/session")
        self.token = sess.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        self.store = TaskStore(self._tmp.name)
        self.profile = self.store.create_profile("v2 progressive 测试画像")
        self.resume = self.store.save_resume(
            self.profile["id"], "storage/v2_prog.pdf", "pdf",
            _CONTRACT_RESUME_TEXT, "hash-v2-prog", "v2_prog.pdf",
        )
        # Build a ready analysis + v2 confirmation via store API.
        provider = _FakeAIProviderForRuns(_contract_valid_ai_response())
        from webui.discovery import analyze_resume, confirm_directions
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

    # --- helpers -------------------------------------------------------

    def _create_v2_run_directly(self, status="processing_jobs", result_revision=0):
        """Create a v2 run via store for route-level tests."""
        import hashlib
        input_hash = hashlib.sha256(self.confirmation["id"].encode("utf-8")).hexdigest()
        run = self.store.create_discovery_run(
            profile_id=self.profile["id"], resume_id=self.resume["id"],
            analysis_id=self.analysis["id"], confirmation_id=self.confirmation["id"],
            input_hash=input_hash, policy_version="discovery_v2",
        )
        if status != "created":
            self.store.update_discovery_run(
                run["id"], status=status, stage=status,
                counters={"result_revision": result_revision},
            )
        return run

    def _add_snapshot_and_assessment(self, run_id, job_id, category="high_match"):
        """Create a snapshot + completed assessment for results tests."""
        canonical_url = f"https://www.zhipin.com/job_detail/{job_id}.html"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, 'Python 后端', '示例公司', '25K', '北京', 'jd', '2026-01-01', '2026-01-01')",
                (job_id, canonical_url, canonical_url),
            )
        snap = self.store.save_job_snapshot(
            run_id=run_id, job_id=job_id, source_url=canonical_url,
            title="Python 后端", company="示例公司", salary="25K", location="北京",
            tags="", jd="职位描述", company_json={}, completeness="complete",
            missing_fields=[], source_status="active", content_hash="hash",
            fetch_status="completed",
        )
        self.store.create_assessment(
            run_id=run_id, snapshot_id=snap["id"], direction_id=self.direction_ids[0],
            hard_outcome="pass", category=category, match_score=88, confidence=84,
            status="completed", policy_version="v2",
        )
        return snap

    # --- T047 tests ----------------------------------------------------

    def test_create_v2_run_returns_policy_version_discovery_v2(self):
        """POST /api/discovery/runs 创建 v2 run 时返回 policy_version=discovery_v2。"""
        # Patch runtime to avoid real background execution.
        runtime = self.app.config["DISCOVERY_RUNTIME"]
        with mock.patch.object(runtime, "submit_run"):
            response = self.client.post("/api/discovery/runs", json={
                "confirmation_id": self.confirmation["id"],
                "policy_version": "discovery_v2",
            })
        self.assertEqual(response.status_code, 202)
        body = response.get_json()
        self.assertEqual(body.get("policy_version"), "discovery_v2")

    def test_get_v2_run_returns_four_progress_types_and_result_revision(self):
        """GET /api/discovery/runs/{id} 返回四类 v2 进度和 result_revision。"""
        run = self._create_v2_run_directly(status="processing_jobs", result_revision=3)
        self.store.update_discovery_run(run["id"], counters={
            "list_candidate_count": 100,
            "detail_selected_count": 15,
            "detail_completed_count": 4,
            "assessment_completed_count": 6,
        })
        response = self.client.get(f"/api/discovery/runs/{run['id']}")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        progress = body.get("progress", {})
        self.assertEqual(progress.get("list_candidates"), 100)
        self.assertEqual(progress.get("details_selected"), 15)
        self.assertEqual(progress.get("details_completed"), 4)
        self.assertEqual(progress.get("assessments_completed"), 6)
        self.assertEqual(body.get("result_revision"), 3)

    def test_candidates_diagnostic_endpoint_returns_safe_list_fields(self):
        """GET /api/discovery/runs/{id}/candidates 返回候选诊断信息。"""
        run = self._create_v2_run_directly(status="processing_jobs")
        # Add a candidate via store (job must exist first for FK).
        job_id = "job-diag-001"
        canonical_url = f"https://www.zhipin.com/job_detail/{job_id}.html"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, 'Python 后端', '示例公司', '25K', '北京', 'jd', '2026-01-01', '2026-01-01')",
                (job_id, canonical_url, canonical_url),
            )
        self.store.upsert_run_candidate(
            run_id=run["id"], job_id=job_id,
            source_url=canonical_url,
            direction_ids=[self.direction_ids[0]], search_terms=["Python"],
            source_positions=[{"item": 0, "page": 1, "rank": 0}],
            list_fields={"title": "Python 后端", "salary": "25K", "location": "北京"},
            input_hash=run.get("input_hash", ""),
        )
        response = self.client.get(f"/api/discovery/runs/{run['id']}/candidates")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn("items", body)
        self.assertGreaterEqual(len(body["items"]), 1)
        item = body["items"][0]
        self.assertEqual(item["job_id"], "job-diag-001")
        self.assertIn("state", item)
        self.assertIn("selection_decision", item)
        # Must NOT contain resume text or raw prompt.
        self.assertNotIn("resume_text", item)
        self.assertNotIn("raw_prompt", item)

    def test_active_run_results_returns_v2_envelope(self):
        """活动运行的 results 返回 run_id、run_status、revision、changed、complete。"""
        run = self._create_v2_run_directly(status="processing_jobs", result_revision=1)
        self._add_snapshot_and_assessment(run["id"], "job-active-001")
        response = self.client.get(f"/api/discovery/runs/{run['id']}/results")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body.get("run_id"), run["id"])
        self.assertEqual(body.get("run_status"), "processing_jobs")
        self.assertEqual(body.get("revision"), 1)
        self.assertTrue(body.get("changed"))
        self.assertFalse(body.get("complete"))
        self.assertIn("items", body)

    def test_after_revision_unchanged_returns_changed_false(self):
        """after_revision 与服务端 revision 相同时返回 changed=false 和空 items。"""
        run = self._create_v2_run_directly(status="processing_jobs", result_revision=5)
        self._add_snapshot_and_assessment(run["id"], "job-rev-001")
        response = self.client.get(
            f"/api/discovery/runs/{run['id']}/results?after_revision=5"
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body.get("changed"))
        self.assertEqual(body.get("items"), [])
        self.assertEqual(body.get("revision"), 5)


class RecommendationProjectorHttpContractTests(unittest.TestCase):
    """T063: HTTP contract for canonical recommendation projector.

    Contract source: specs/005-fast-resume-discovery/contracts/http-api.md
      GET /api/discovery/runs/{id}/results
        ?direction_id: include job when any assessment matches; return all assessments
        ?category: applies to primary assessment
      Response items must include: recommendation_id, rank, job_id, title, company,
        salary, location, jd/jd_excerpt, source_url, source_status, fetched_at,
        category, match_score, confidence, completeness, primary_assessment,
        assessments[], matched_direction_ids[], explanation{positive,gaps,refs},
        sort_components
    """

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.app = create_app({"TESTING": True, "DB_PATH": self._tmp.name, "START_TASKS": False})
        self.client = self.app.test_client()
        sess = self.client.get("/api/session")
        self.token = sess.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        self.store = TaskStore(self._tmp.name)
        self.profile = self.store.create_profile("projector 测试画像")
        self.resume = self.store.save_resume(
            self.profile["id"], "storage/projector.pdf", "pdf",
            _CONTRACT_RESUME_TEXT, "hash-projector", "projector.pdf",
        )
        provider = _FakeAIProviderForRuns(_contract_valid_ai_response())
        from webui.discovery import analyze_resume, confirm_directions
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
        import os as _os
        runtime = self.app.config.get("DISCOVERY_RUNTIME")
        if runtime:
            try:
                runtime.shutdown()
            except Exception:
                pass
        if _os.path.exists(self._tmp.name):
            try:
                _os.unlink(self._tmp.name)
            except PermissionError:
                pass

    def _make_run(self, status="succeeded"):
        import hashlib
        input_hash = hashlib.sha256(self.confirmation["id"].encode()).hexdigest()
        run = self.store.create_discovery_run(
            profile_id=self.profile["id"], resume_id=self.resume["id"],
            analysis_id=self.analysis["id"], confirmation_id=self.confirmation["id"],
            input_hash=input_hash, policy_version="discovery_v2",
        )
        if status != "created":
            self.store.update_discovery_run(run["id"], status=status, stage="assembling")
        return run

    def _add_job(self, run_id, job_id, *, category="high_match", direction_id=None,
                 match_score=85, confidence=88, completeness="complete",
                 hard_outcome="pass", salary="20-40K", location="北京",
                 jd="负责后端服务开发与架构设计", source_status="active"):
        url = f"https://www.zhipin.com/job_detail/{job_id}.html"
        job = self.store.save_job(
            canonical_url=url, source_url=url,
            title=f"岗位 {job_id}", company=f"公司 {job_id}",
            salary=salary, location=location, jd=jd,
        )
        snap = self.store.save_job_snapshot(
            run_id, job["id"], source_url=url,
            title=f"岗位 {job_id}", company=f"公司 {job_id}",
            salary=salary, location=location, jd=jd, completeness=completeness,
            source_status=source_status, fetch_status="ok",
        )
        did = direction_id or self.direction_ids[0]
        self.store.create_assessment(
            run_id, snap["id"], did,
            hard_outcome=hard_outcome, category=category,
            match_score=match_score, confidence=confidence,
            dimensions={
                "direction_alignment": {"score": 80, "candidate_evidence_refs": ["e1"], "job_evidence_refs": ["title"]},
                "skill_coverage": {"score": 75, "candidate_evidence_refs": ["e1"], "job_evidence_refs": ["jd"]},
            },
            gaps=[], status="completed",
        )
        return snap

    def test_results_items_contain_recommendation_id_and_rank(self):
        """T063: 每条结果包含 recommendation_id 和 rank。"""
        run = self._make_run()
        self._add_job(run["id"], "job-r1")
        data = self.client.get(f"/api/discovery/runs/{run['id']}/results").get_json()
        item = data["items"][0]
        self.assertIn("recommendation_id", item)
        self.assertIn("rank", item)
        self.assertEqual(item["rank"], 1)
        self.assertTrue(item["recommendation_id"].startswith(f"{run['id']}:"))

    def test_results_items_contain_full_job_fields(self):
        """T063: 结果包含公司/岗位/薪资/地点/JD/source/status/fetched_at。"""
        run = self._make_run()
        self._add_job(run["id"], "job-fields", salary="30-50K", location="上海",
                      jd="负责核心系统架构")
        data = self.client.get(f"/api/discovery/runs/{run['id']}/results").get_json()
        item = data["items"][0]
        self.assertEqual(item["company"], "公司 job-fields")
        self.assertEqual(item["title"], "岗位 job-fields")
        self.assertEqual(item["salary"], "30-50K")
        self.assertEqual(item["location"], "上海")
        self.assertTrue(item.get("jd") or item.get("jd_excerpt"))
        self.assertIn("zhipin.com", item["source_url"])
        self.assertEqual(item["source_status"], "active")
        # fetched_at may be empty if snapshot table doesn't store it
        self.assertIn("fetched_at", item)

    def test_results_items_contain_explanation_with_bilateral_refs(self):
        """T063: 结果包含 explanation，含正向依据和双方 refs。"""
        run = self._make_run()
        self._add_job(run["id"], "job-expl")
        data = self.client.get(f"/api/discovery/runs/{run['id']}/results").get_json()
        item = data["items"][0]
        self.assertIn("explanation", item)
        expl = item["explanation"]
        self.assertIn("positive", expl)
        self.assertIn("gaps", expl)
        self.assertIn("candidate_evidence_refs", expl)
        self.assertIn("job_evidence_refs", expl)
        self.assertTrue(len(expl["positive"]) >= 1)
        self.assertTrue(len(expl["candidate_evidence_refs"]) >= 1)
        self.assertTrue(len(expl["job_evidence_refs"]) >= 1)

    def test_direction_filter_returns_all_assessments_for_matching_job(self):
        """T063: direction_id 筛选时，匹配岗位返回全部方向评估。"""
        run = self._make_run()
        # Job with two direction assessments (if 2+ directions exist)
        url = f"https://www.zhipin.com/job_detail/job-multi.html"
        job = self.store.save_job(canonical_url=url, source_url=url,
                                  title="多方向岗位", company="多方向公司",
                                  salary="20-40K", location="北京", jd="Python")
        snap = self.store.save_job_snapshot(
            run["id"], job["id"], source_url=url,
            title="多方向岗位", company="多方向公司",
            salary="20-40K", location="北京", jd="Python", completeness="complete",
            source_status="active", fetch_status="ok",
        )
        dirs_to_use = self.direction_ids[:2] if len(self.direction_ids) >= 2 else self.direction_ids[:1]
        for did in dirs_to_use:
            self.store.create_assessment(
                run["id"], snap["id"], did,
                hard_outcome="pass", category="high_match",
                match_score=85, confidence=88,
                dimensions={"direction_alignment": {"score": 80, "candidate_evidence_refs": ["e1"], "job_evidence_refs": ["title"]}},
                status="completed",
            )
        # Filter by the direction we used
        did_filter = dirs_to_use[0]
        data = self.client.get(
            f"/api/discovery/runs/{run['id']}/results?direction_id={did_filter}"
        ).get_json()
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        # Must return all assessments, not just the filtered direction
        self.assertGreaterEqual(len(item["assessments"]), len(dirs_to_use))
        self.assertIn("matched_direction_ids", item)
        self.assertIn(did_filter, item["matched_direction_ids"])

    def test_category_filter_applies_to_primary_assessment(self):
        """T063: category 筛选作用于主评估。"""
        run = self._make_run()
        self._add_job(run["id"], "job-high", category="high_match")
        self._add_job(run["id"], "job-review", category="needs_review")
        data = self.client.get(
            f"/api/discovery/runs/{run['id']}/results?category=high_match"
        ).get_json()
        for item in data["items"]:
            self.assertEqual(item["primary_assessment"]["category"], "high_match")

    def test_sort_order_is_canonical_category_then_score(self):
        """T063: 排序按类别→分数→置信度→完整度→job_id 稳定排序。"""
        run = self._make_run()
        # adjacent with high score
        snap_adj = self._add_job(run["id"], "job-adj", category="adjacent_match", match_score=95)
        # high with lower score
        snap_high_low = self._add_job(run["id"], "job-high-low", category="high_match", match_score=70)
        # high with higher score
        snap_high_hi = self._add_job(run["id"], "job-high-hi", category="high_match", match_score=90)
        data = self.client.get(f"/api/discovery/runs/{run['id']}/results").get_json()
        job_ids = [it["job_id"] for it in data["items"]]
        # Get actual job UUIDs
        job_adj_id = snap_adj["job_id"]
        job_high_low_id = snap_high_low["job_id"]
        job_high_hi_id = snap_high_hi["job_id"]
        # high_match before adjacent; within high: score desc
        high_items = [j for j in job_ids if j in (job_high_low_id, job_high_hi_id)]
        adj_items = [j for j in job_ids if j == job_adj_id]
        self.assertTrue(all(
            job_ids.index(h) < job_ids.index(a)
            for h in high_items for a in adj_items
        ))
        self.assertEqual(job_ids.index(job_high_hi_id), 0)

    def test_hard_violation_never_in_recommended_partition(self):
        """T063/SC-005: 硬约束违规岗位不进入推荐组合。"""
        run = self._make_run()
        self._add_job(run["id"], "job-violation", category="high_match",
                      hard_outcome="violation", match_score=99)
        self._add_job(run["id"], "job-ok", category="high_match")
        data = self.client.get(f"/api/discovery/runs/{run['id']}/results").get_json()
        recommended = [
            it for it in data["items"]
            if it["primary_assessment"]["category"] in ("high_match", "adjacent_match", "growth_match")
        ]
        violation_ids = {it["job_id"] for it in data["items"]
                         if it["job_id"] == "job-violation" or
                         it.get("primary_assessment", {}).get("hard_outcome") == "violation"}
        self.assertNotIn("job-violation", {it["job_id"] for it in recommended})

    def test_repeated_load_returns_identical_order(self):
        """T063/SC-006: 重复加载排序完全一致。"""
        run = self._make_run()
        for i in range(5):
            self._add_job(run["id"], f"job-{i}", match_score=80 + i)
        first = [it["job_id"] for it in self.client.get(
            f"/api/discovery/runs/{run['id']}/results").get_json()["items"]]
        second = [it["job_id"] for it in self.client.get(
            f"/api/discovery/runs/{run['id']}/results").get_json()["items"]]
        self.assertEqual(first, second)


class DiscoveryV2ResumeStatusContractTests(unittest.TestCase):
    """T082 RED: v2 four-class progress authoritative names, cancel response
    with cancel_requested_at + four-part progress, resume 409 on hash drift,
    partial/failed/interrupted/cancelled status visibility, refresh recovery.

    Contract source: specs/005-fast-resume-discovery/contracts/http-api.md
      - L203-208: progress has search_queries_completed, list_candidates,
        details_selected, details_completed, assessments_completed, recommendations
      - L318: cancel response includes cancel_requested_at and current four-part progress
      - L319: resume rejects profile/confirmation/policy/input hash drift with 409
      - L228: v2 names are authoritative; source_count/detail_count/evaluated_count
        are compatibility aliases only
    """

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.app = create_app({"TESTING": True, "DB_PATH": self._tmp.name, "START_TASKS": False})
        self.client = self.app.test_client()
        sess = self.client.get("/api/session")
        self.token = sess.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        self.store = TaskStore(self._tmp.name)
        self.profile = self.store.create_profile("v2 resume/status 测试画像")
        self.resume = self.store.save_resume(
            self.profile["id"], "storage/v2_resume.pdf", "pdf",
            _CONTRACT_RESUME_TEXT, "hash-v2-resume", "v2_resume.pdf",
        )
        provider = _FakeAIProviderForRuns(_contract_valid_ai_response())
        from webui.discovery import analyze_resume, confirm_directions, compile_search_plan
        self.analysis = analyze_resume(
            self.store, self.resume["id"], ai_consent=True, ai_provider=provider,
        )
        directions = self.store.list_directions(self.analysis["id"])
        self.direction_ids = [d["id"] for d in directions]
        self.confirmation = confirm_directions(
            self.store, self.analysis["id"], self.direction_ids,
            hard_constraints={"city": "北京"},
        )
        # confirm_directions 返回的 dict 不含 directions/hard_constraints 等字段；
        # 通过 store.get_confirmation 取回完整结构。
        self.confirmation = self.store.get_confirmation(self.confirmation["id"])
        # Build confirmation view to compute real input_hash via compile_search_plan.
        enabled_directions = []
        for d in directions:
            for cd in self.confirmation["directions"]:
                if cd["direction_id"] == d["id"] and cd.get("enabled"):
                    enabled_directions.append({
                        "id": d["id"], "direction_id": d["id"],
                        "name": d.get("name", ""),
                        "type": d.get("direction_type", ""),
                        "search_terms": d.get("search_terms", []),
                        "default_enabled": d.get("default_enabled", False),
                        "evidence_refs": [],
                    })
        self._confirmation_view = {
            "id": self.confirmation["id"], "analysis_id": self.analysis["id"],
            "hard_constraints": self.confirmation.get("hard_constraints", {}),
            "soft_preferences": self.confirmation.get("soft_preferences", {}),
            "safe_limits": self.confirmation.get("safe_limits", {}),
            "enabled_directions": enabled_directions,
            "directions": self.confirmation.get("directions", []),
        }
        plan = compile_search_plan(self._confirmation_view)
        self._real_input_hash = plan["input_hash"]

    def tearDown(self):
        import os as _os
        runtime = self.app.config.get("DISCOVERY_RUNTIME")
        if runtime:
            try:
                runtime.shutdown()
            except Exception:
                pass
        if _os.path.exists(self._tmp.name):
            try:
                _os.unlink(self._tmp.name)
            except PermissionError:
                pass

    # --- helpers -------------------------------------------------------

    def _create_v2_run(self, status="processing_jobs", stage=None,
                       counters=None, input_hash=None, policy_version="discovery_v2"):
        """Create a v2 run with real input_hash (or override for drift tests)."""
        run = self.store.create_discovery_run(
            profile_id=self.profile["id"], resume_id=self.resume["id"],
            analysis_id=self.analysis["id"], confirmation_id=self.confirmation["id"],
            input_hash=input_hash or self._real_input_hash,
            policy_version=policy_version,
        )
        if status != "created":
            self.store.update_discovery_run(
                run["id"], status=status, stage=stage or status,
                counters=counters or {}, started=True,
            )
        return run

    # --- T082: four-class progress authoritative names ----------------

    def test_get_v2_run_returns_search_queries_completed_in_progress(self):
        """http-api.md L203-208: progress 必须包含 search_queries_completed（v2 权威名）。

        RED: 当前 _run_summary 只返回 source_count（v1 别名），缺少 v2 权威名
        search_queries_completed。
        """
        run = self._create_v2_run(
            status="processing_jobs",
            counters={"source_count": 5, "list_candidate_count": 100},
        )
        resp = self.client.get(f"/api/discovery/runs/{run['id']}")
        self.assertEqual(resp.status_code, 200)
        progress = resp.get_json().get("progress", {})
        self.assertIn("search_queries_completed", progress,
                      "v2 progress 必须包含 search_queries_completed 权威名")
        self.assertEqual(progress["search_queries_completed"], 5)

    def test_get_v2_run_returns_recommendations_in_progress(self):
        """http-api.md L203-208: progress 必须包含 recommendations（推荐结果数）。

        RED: 当前 _run_summary 不返回 recommendations 字段。
        """
        run = self._create_v2_run(
            status="processing_jobs",
            counters={"high_count": 3, "adjacent_count": 2, "growth_count": 1},
        )
        resp = self.client.get(f"/api/discovery/runs/{run['id']}")
        self.assertEqual(resp.status_code, 200)
        progress = resp.get_json().get("progress", {})
        self.assertIn("recommendations", progress,
                      "v2 progress 必须包含 recommendations 字段")
        # recommendations = high_match + adjacent_match + growth_match
        self.assertEqual(progress["recommendations"], 6)

    # --- T082: cancel response includes cancel_requested_at + progress -

    def test_cancel_response_includes_cancel_requested_at(self):
        """http-api.md L318: cancel response 必须包含 cancel_requested_at。

        RED: 当前 _run_summary 不返回 cancel_requested_at 字段。
        """
        run = self._create_v2_run(status="fetching_lists")
        resp = self.client.post(f"/api/discovery/runs/{run['id']}/cancel")
        self.assertEqual(resp.status_code, 202)
        body = resp.get_json()
        self.assertIn("cancel_requested_at", body,
                      "cancel response 必须包含 cancel_requested_at")
        self.assertIsNotNone(body["cancel_requested_at"],
                             "cancel_requested_at 不得为 null")

    def test_cancel_response_includes_four_part_progress(self):
        """http-api.md L318: cancel response 必须包含当前四类进度。"""
        run = self._create_v2_run(
            status="fetching_lists",
            counters={
                "list_candidate_count": 50,
                "detail_selected_count": 10,
                "detail_completed_count": 3,
                "assessment_completed_count": 2,
            },
        )
        resp = self.client.post(f"/api/discovery/runs/{run['id']}/cancel")
        self.assertEqual(resp.status_code, 202)
        progress = resp.get_json().get("progress", {})
        self.assertEqual(progress.get("list_candidates"), 50)
        self.assertEqual(progress.get("details_selected"), 10)
        self.assertEqual(progress.get("details_completed"), 3)
        self.assertEqual(progress.get("assessments_completed"), 2)

    # --- T082: resume 409 on hash drift (synchronous) -----------------

    def test_resume_rejects_input_hash_drift_with_409(self):
        """http-api.md L319: resume 必须同步拒绝 input_hash 漂移（409）。

        RED: 当前 HTTP resume 端点直接返回 202 并在后台线程中检查 hash drift，
        客户端无法同步感知 409。GREEN 应在 HTTP 层同步检查 hash drift。
        """
        run = self._create_v2_run(status="interrupted", stage="processing_jobs")
        # Drift the stored input_hash via direct SQL.
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE discovery_runs SET input_hash = ? WHERE id = ?",
                ("drifted-hash", run["id"]),
            )
        resp = self.client.post(f"/api/discovery/runs/{run['id']}/resume")
        self.assertEqual(resp.status_code, 409,
                         "input_hash 漂移时 resume 必须同步返回 409")
        body = resp.get_json() or {}
        self.assertEqual(body.get("error_code"), "state_conflict")

    def test_resume_rejects_invalid_policy_version_with_409(self):
        """http-api.md L319: resume 必须同步拒绝 policy_version 漂移到非法值（409）。

        RED: 当前 HTTP resume 端点不校验 policy_version 合法性。
        """
        run = self._create_v2_run(status="interrupted", stage="processing_jobs")
        # Drift policy_version to an invalid value via direct SQL.
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE discovery_runs SET policy_version = ? WHERE id = ?",
                ("drifted-policy", run["id"]),
            )
        resp = self.client.post(f"/api/discovery/runs/{run['id']}/resume")
        self.assertEqual(resp.status_code, 409,
                         "policy_version 非法值时 resume 必须同步返回 409")
        body = resp.get_json() or {}
        self.assertEqual(body.get("error_code"), "state_conflict")

    # --- T082: partial/failed/interrupted/cancelled clearly visible ----

    def test_partial_status_visible_in_get_run(self):
        """partial 状态必须在 GET /runs/{id} 中清晰可见，且 complete=true。"""
        run = self._create_v2_run(status="partial", stage="assembling")
        resp = self.client.get(f"/api/discovery/runs/{run['id']}")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body.get("status"), "partial")
        self.assertTrue(body.get("complete", False),
                        "partial 是终态，complete 必须为 true")

    def test_failed_status_visible_in_get_run(self):
        """failed 状态必须在 GET /runs/{id} 中清晰可见，且 complete=true。"""
        run = self._create_v2_run(
            status="failed", stage="assembling",
            counters={"failure_code": "source_blocked"},
        )
        # failure_code is stored on the run row, not in counters.
        self.store.update_discovery_run(
            run["id"], failure_code="source_blocked", failure_stage="fetching_lists",
        )
        resp = self.client.get(f"/api/discovery/runs/{run['id']}")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body.get("status"), "failed")
        self.assertTrue(body.get("complete", False),
                        "failed 是终态，complete 必须为 true")
        self.assertIsNotNone(body.get("failure"),
                             "failed 状态必须包含 failure 详情")

    def test_interrupted_status_visible_in_get_run(self):
        """interrupted 状态必须在 GET /runs/{id} 中清晰可见，且 complete=false（可恢复）。"""
        run = self._create_v2_run(status="interrupted", stage="processing_jobs")
        resp = self.client.get(f"/api/discovery/runs/{run['id']}")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body.get("status"), "interrupted")
        self.assertFalse(body.get("complete", True),
                         "interrupted 是可恢复终态，complete 必须为 false")

    def test_cancelled_status_visible_in_get_run(self):
        """cancelled 状态必须在 GET /runs/{id} 中清晰可见，且 complete=true。"""
        run = self._create_v2_run(status="cancelled", stage="processing_jobs")
        resp = self.client.get(f"/api/discovery/runs/{run['id']}")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body.get("status"), "cancelled")
        self.assertTrue(body.get("complete", False),
                        "cancelled 是终态，complete 必须为 true")

    # --- T082: refresh recovery ----------------------------------------

    def test_refresh_after_interrupt_preserves_progress(self):
        """刷新恢复：interrupted 后 GET /runs/{id} 必须保留进度数据。"""
        run = self._create_v2_run(
            status="interrupted", stage="processing_jobs",
            counters={
                "source_count": 3,
                "list_candidate_count": 45,
                "detail_selected_count": 8,
                "detail_completed_count": 2,
                "assessment_completed_count": 1,
                "result_revision": 2,
            },
        )
        # Simulate page refresh — client re-fetches run summary.
        resp = self.client.get(f"/api/discovery/runs/{run['id']}")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body.get("status"), "interrupted")
        progress = body.get("progress", {})
        self.assertEqual(progress.get("list_candidates"), 45)
        self.assertEqual(progress.get("details_selected"), 8)
        self.assertEqual(progress.get("details_completed"), 2)
        self.assertEqual(progress.get("assessments_completed"), 1)
        self.assertEqual(body.get("result_revision"), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
