#!/usr/bin/env python3
"""Controlled real-BOSS end-to-end script for feature 004 (T091/T092).

This script exercises the full discovery pipeline against the real BOSS
zhipin.com site via Chrome CDP. It requires:
  1. The dedicated persistent Chrome profile to contain a valid BOSS login
  2. Real AI credentials configured in the webui settings

If 127.0.0.1:9222 is down, the script starts the dedicated Chrome itself and
closes it on every exit path. A pre-existing instance is reused and preserved
unless --close-browser-after is explicitly supplied.

When any prerequisite is missing, the script reports the blocker and exits
with a non-zero code instead of simulating the missing capability.

Pipeline (when prerequisites are met):
  de-identified resume → AI analysis → >=2 direction confirmation
  → multi-keyword search → multi-page list dedup → detail fetch
  → per-direction assessment → feedback → interrupt/resume

Usage:
    python tests/fixtures/discovery/e2e_real_boss.py
    python tests/fixtures/discovery/e2e_real_boss.py --resume resume_cross_family.txt
    python tests/fixtures/discovery/e2e_real_boss.py --close-browser-after
"""

from __future__ import annotations

import json
import io
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = FIXTURE_DIR.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class ManagedCdpBrowser:
    """Own a CDP browser only when this E2E had to start it."""

    def __init__(self, backend, close_reused: bool = False):
        self._backend = backend
        self._close_reused = close_reused
        self._started_by_e2e = False
        self._close_status = "not_requested"

    def __enter__(self):
        if self._backend.is_ready():
            return self
        if not self._backend.start():
            raise RuntimeError("managed_cdp_start_failed")
        self._started_by_e2e = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self._started_by_e2e or self._close_reused:
            self._close_status = "closed" if self._backend.close() else "close_failed"
        return False

    def report(self) -> dict:
        return {
            "mode": "started_by_e2e" if self._started_by_e2e else "reused_existing",
            "close_status": self._close_status,
        }


class BossCdpBackend:
    """System-boundary adapter for the dedicated BOSS CDP Chrome."""

    def __init__(self, cdp_port: int = 9222):
        scripts_dir = str(PROJECT_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import boss_cdp_raw

        self._module = boss_cdp_raw
        self._cdp_port = cdp_port

    def is_ready(self) -> bool:
        return bool(self._module.is_cdp_ready(self._cdp_port))

    def start(self) -> bool:
        return self._module.run_setup_chrome(
            cdp_port=self._cdp_port,
            wait_login=False,
        ) == 0

    def close(self) -> bool:
        return bool(self._module.close_cdp_chrome(
            cdp_port=self._cdp_port,
            cdp_data_dir=self._module.DEFAULT_CDP_DATA_DIR,
        ))


def _default_cdp_data_dir() -> str:
    """Return the default CDP Chrome user-data-dir.

    Wrapped as a function so tests can patch it to point at a temp dir.
    Mirrors scripts/boss_cdp_raw.py:DEFAULT_CDP_DATA_DIR.
    """
    return os.path.expanduser("~/.career-scout/chrome-profile")


def _diagnose_login_offline(profile_dir: str) -> str:
    """Offline diagnosis of BOSS login state when CDP is down.

    Inspects the Cookies file inside the CDP Chrome user-data-dir.
    Does NOT verify server-side session validity — only whether any
    login state was ever persisted. Returns a human-readable note.
    """
    profile_path = Path(profile_dir)
    if not profile_path.exists():
        return "user-data-dir not found; login state was never persisted in this profile"

    # Chrome stores Cookies under Default\Network\Cookies (newer versions)
    # or Default\Cookies (older versions). Check both.
    candidates = [
        profile_path / "Default" / "Network" / "Cookies",
        profile_path / "Default" / "Cookies",
    ]
    cookie_file = next((p for p in candidates if p.exists()), None)

    if cookie_file is None:
        return ("No Cookies file in user-data-dir; "
                "login state likely not persisted in this profile")

    stat = cookie_file.stat()
    size = stat.st_size
    mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return (f"Cookies file exists (size={size} bytes, modified {mtime}); "
            "login state may still be valid — start CDP Chrome to verify")


def _probe_login_via_cdp(cdp_port: int = 9222) -> bool:
    """Actively probe BOSS login state via CDP.

    Imports scripts.boss_cdp_raw.check_login_state, which navigates a new
    tab to https://www.zhipin.com/ and checks whether plaintext salary is
    served (only visible to logged-in users). This is more accurate than
    inspecting the tab list — the user may have logged in earlier and
    since closed or navigated away from the zhipin tab.
    """
    try:
        # scripts/ is at PROJECT_ROOT/scripts; ensure importable.
        scripts_dir = str(PROJECT_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import boss_cdp_raw
        return bool(boss_cdp_raw.check_login_state(cdp_port))
    except Exception as exc:
        # Probe failed (e.g. websocket error, navigation timeout).
        # Treat as not-logged-in so we block safely.
        sys.stderr.write(f"WARNING: _probe_login_via_cdp failed: {exc}\n")
        return False


def _run_prerequisite_stage(name: str, operation, timeout_seconds: float):
    """Run one potentially blocking prerequisite in a daemon thread."""
    print(f"  [prereq] {name}: start", flush=True)
    outcome = queue.Queue(maxsize=1)

    def invoke():
        try:
            outcome.put(("ok", operation()))
        except Exception as exc:
            outcome.put(("error", type(exc).__name__))

    worker = threading.Thread(
        target=invoke, name=f"e2e-prereq-{name}", daemon=True,
    )
    worker.start()
    try:
        status, value = outcome.get(timeout=max(0.01, float(timeout_seconds)))
    except queue.Empty:
        print(f"  [prereq] {name}: timeout", flush=True)
        return "timeout", None
    print(f"  [prereq] {name}: {status}", flush=True)
    return status, value


def _check_prerequisites(stage_timeout_seconds: float = 15) -> dict:
    """Check all prerequisites for a real BOSS E2E run.

    boss_login is tri-state:
      - True:  CDP up and active probe confirms logged-in
      - False: CDP up and probe confirms NOT logged-in
      - "unknown": CDP down — cannot verify (with offline diagnosis note)
    """
    results = {
        "cdp": False, "boss_login": "unknown", "ai_credentials": False,
        "errors": [], "stages": {},
    }

    # 1. Chrome CDP connectivity
    try:
        import requests
        resp = requests.get("http://127.0.0.1:9222/json/version", timeout=3)
        if resp.status_code == 200:
            results["cdp"] = True
            results["stages"]["cdp"] = {"status": "ok"}
        else:
            results["stages"]["cdp"] = {"status": "failed"}
            results["errors"].append(f"CDP returned status {resp.status_code}")
    except Exception as exc:
        results["stages"]["cdp"] = {"status": "failed"}
        results["errors"].append(f"CDP not reachable: {exc}")

    # 2. BOSS login state
    if results["cdp"]:
        # CDP up: actively probe login state by navigating to zhipin.com
        # and checking for plaintext salary. The tab list alone is not a
        # reliable signal — the user may have logged in earlier and then
        # closed or navigated away from the zhipin tab.
        status, logged_in = _run_prerequisite_stage(
            "boss_login", lambda: _probe_login_via_cdp(9222), stage_timeout_seconds,
        )
        results["stages"]["boss_login"] = {"status": status}
        results["boss_login"] = bool(logged_in) if status == "ok" else False
        if status == "timeout":
            results["errors"].append("boss_login_probe_timeout")
        elif status == "error":
            results["errors"].append(f"boss_login_probe_error:{logged_in}")
        elif not logged_in:
            results["errors"].append(
                "BOSS login probe returned not-logged-in "
                "(run scripts/boss_cdp_raw.py --setup-chrome and login in the dedicated Chrome)")
    else:
        # CDP down: cannot verify login state online.
        # Run offline diagnosis so the user knows whether their persisted
        # login state is likely still good.
        note = _diagnose_login_offline(_default_cdp_data_dir())
        results["boss_login"] = "unknown"
        results["stages"]["boss_login"] = {"status": "skipped"}
        results["boss_login_note"] = note
        results["errors"].append("Cannot check BOSS login without CDP")

    # 3. AI credentials — use the real store+keyring API.
    # Previously this called ai_service.load_settings(), which does not exist;
    # hasattr() returned False and the check always reported "missing" even
    # when a key was configured. The correct flow is:
    #   store.get_ai_settings() -> {is_configured, ...}  (credential_ref is popped)
    #   store.get_credential_ref() -> hostname
    #   ai.retrieve_api_key(cred_ref) -> real key from keyring
    def check_ai_credentials():
        settings, cred_ref = _load_ai_settings()
        api_key = _retrieve_api_key(cred_ref) if cred_ref else ""
        return bool(settings.get("is_configured") and api_key), settings, bool(api_key)

    status, ai_result = _run_prerequisite_stage(
        "ai_credentials", check_ai_credentials, stage_timeout_seconds,
    )
    results["stages"]["ai_credentials"] = {"status": status}
    if status == "ok":
        configured, settings, has_key = ai_result
        results["ai_credentials"] = configured
        if not configured:
            results["errors"].append(
                "No AI API key configured "
                f"(is_configured={settings.get('is_configured')}, "
                f"keyring_has_key={has_key})")
    elif status == "timeout":
        results["errors"].append("ai_credentials_timeout")
    else:
        results["errors"].append(f"Cannot check AI credentials: {ai_result}")

    return results


def _load_ai_settings() -> tuple[dict, str]:
    """Load AI settings + credential_ref from the default store.

    Returns (settings_dict, credential_ref). settings_dict matches what
    store.get_ai_settings() returns (is_configured, endpoint_url, model, ...).
    credential_ref is the keyring username (hostname), or "" if unconfigured.
    """
    from webui.store import TaskStore
    db_path = os.path.expanduser("~/.career-scout/webui/webui.db")
    store = TaskStore(db_path)
    settings = store.get_ai_settings()
    cred_ref = store.get_credential_ref() if hasattr(store, "get_credential_ref") else ""
    return settings, cred_ref


def _retrieve_api_key(credential_ref: str) -> str:
    """Retrieve the API key from the system keyring."""
    if not credential_ref:
        return ""
    from webui import ai as ai_service
    return ai_service.retrieve_api_key(credential_ref) or ""


def _validate_t133_report(report: dict) -> list[str]:
    """Return stable blocker codes for every missing T133 acceptance gate.

    This gate is deliberately independent of the run's persisted terminal
    status.  A source run may legitimately succeed with no matching jobs,
    but that outcome cannot prove the real-list/detail/evaluation/feedback
    and interruption requirements of T133.
    """
    blockers: list[str] = []
    counts = report.get("counts") or {}
    feedback = report.get("feedback") or {}
    cancel = report.get("cancel_test") or {}
    resume = report.get("resume_test") or {}

    if report.get("execution_mode") != "http_routes":
        blockers.append("http_route_execution_missing")
    if report.get("provider_factory_mode") != "application_composition_root":
        blockers.append("provider_composition_bypassed")
    if int(counts.get("source_count") or 0) < 1:
        blockers.append("real_list_missing")
    if int(counts.get("detail_count") or 0) < 1:
        blockers.append("real_detail_missing")
    if int(counts.get("evaluated_count") or 0) < 1:
        blockers.append("real_evaluation_missing")
    if feedback.get("status") != "ok":
        blockers.append("feedback_not_executed")
    if not str(feedback.get("job_id") or "").strip():
        blockers.append("feedback_job_missing")

    if cancel.get("status") != "ok":
        blockers.append("cancel_not_verified")
    if cancel.get("cancel_stage") not in {"fetching_lists", "fetching_details"}:
        blockers.append("cancel_stage_unverified")
    if (int(cancel.get("cancelled_unfinished_count") or 0) < 1
            or int(cancel.get("new_work_started_after_cancel") or 0) != 0):
        blockers.append("cancel_did_not_stop_unfinished_work")

    if resume.get("status") != "ok":
        blockers.append("resume_not_verified")
    if resume.get("created_via_http") is not True:
        blockers.append("resume_not_from_http_run")
    if resume.get("interruption_stage") not in {"fetching_lists", "fetching_details"}:
        blockers.append("resume_interruption_stage_unverified")
    if (int(resume.get("unfinished_before_resume") or 0) < 1
            or int(resume.get("resubmitted_unfinished") or 0) < 1):
        blockers.append("resume_unfinished_work_not_resubmitted")
    if int(resume.get("duplicate_completed_count") or 0) != 0:
        blockers.append("resume_repeated_completed_work")
    if not report.get("interrupt_points"):
        blockers.append("controlled_interruption_missing")
    return blockers


def _finalize_t133_report(report: dict) -> dict:
    """Apply the non-bypassable T133 gate to a completed attempt report."""
    blockers = _validate_t133_report(report)
    report["blockers"] = blockers
    if blockers:
        report["status"] = "blocked"
        report["reason"] = (
            "T133 acceptance gates not satisfied: " + ", ".join(blockers)
        )
    else:
        report["status"] = "completed"
    return report


def _run_e2e(resume_name: str = "resume_cross_family.txt") -> dict:
    """Run the full controlled E2E pipeline via HTTP routes (T133).

    T133 审计修复：不再直接调用 Python API（analyze_resume / confirm_directions /
    runner.run），改为通过 Flask test_client 调用真实 HTTP 路由：
      POST /api/discovery/analyses       (ai_consent=true)
      GET  /api/discovery/analyses/{id}  (轮询至 ready/failed)
      POST /api/discovery/confirmations
      POST /api/discovery/runs           (自动提交 runtime)
      GET  /api/discovery/runs/{id}      (轮询至终态)
      GET  /api/discovery/runs/{id}/results
      POST /api/discovery/feedback       (即使 counts=0 也记录)
      POST /api/discovery/runs/{id}/cancel  (cancel 流程验证)
      POST /api/discovery/runs/{id}/resume  (resume 流程验证)

    使用临时 DB 避免污染生产数据；AI 设置从生产 DB 复制到临时 DB，
    runtime 的 ai_provider_factory 会从临时 DB 读取设置构建真实 provider。
    BossCdpSource 直接连接 127.0.0.1:9222 上的 Chrome，执行真实 BOSS 搜索。
    """
    import tempfile
    import traceback

    from webui.app import create_app
    from webui.store import TaskStore

    resume_path = FIXTURE_DIR / resume_name
    if not resume_path.is_file():
        return {"status": "blocked", "reason": f"resume file not found: {resume_name}"}
    resume_text = resume_path.read_text(encoding="utf-8")

    # 从生产 DB 复制 AI 设置到临时 DB
    prod_settings, prod_cred_ref = _load_ai_settings()
    if not prod_settings.get("is_configured"):
        return {"status": "blocked",
                "reason": "AI credentials not configured in production DB"}

    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    store = TaskStore(tmp_db.name)
    store.save_ai_settings(
        endpoint_url=prod_settings.get("endpoint_url", ""),
        credential_ref=prod_cred_ref,
        status="configured",
        model=prod_settings.get("model", ""),
    )

    # 创建 app：TESTING=True 关闭 TaskRunner（scraping runner），
    # 但 DISCOVERY_RUNTIME 仍然创建并可异步执行。
    app = create_app({
        "TESTING": True,
        "DB_PATH": tmp_db.name,
        "START_TASKS": False,
    })

    # 不覆盖 runtime._ai_provider_factory。分析与岗位评估必须通过
    # create_app 的生产 composition root，从临时 DB 设置 + 系统 keyring
    # 构建真实 DiscoveryAIProvider，才能满足 T133 的实际用户路径门。
    runtime = app.config["DISCOVERY_RUNTIME"]

    client = app.test_client()

    # 获取 session token（protect_local_api 中间件要求）
    sess = client.get("/api/session")
    if sess.status_code != 200:
        return {"status": "error",
                "reason": f"GET /api/session returned {sess.status_code}"}
    token = sess.get_json()["token"]
    client.environ_base["HTTP_X_BOSS_TOKEN"] = token

    report = {
        "resume": resume_name,
        "steps": [],
        "input_boundaries": {},
        "write_scope": {},
        "counts": {},
        "interrupt_points": [],
        "feedback": {},
        "cancel_test": {},
        "resume_test": {},
        "status": "running",
        "execution_mode": "http_routes",
        "provider_factory_mode": "application_composition_root",
    }

    try:
        # Step 1: 从实际 HTTP 用户路径创建 profile 并上传简历。
        profile_resp = client.post(
            "/api/profiles", json={"name": "e2e-test-profile"},
        )
        if profile_resp.status_code != 200:
            report["status"] = "error"
            report["reason"] = f"POST /api/profiles returned {profile_resp.status_code}"
            return report
        profile = profile_resp.get_json()
        upload_resp = client.post(
            f"/api/profiles/{profile['id']}/resume",
            data={
                "file": (io.BytesIO(resume_text.encode("utf-8")), resume_name),
                "ai_consent": "false",
            },
            content_type="multipart/form-data",
        )
        if upload_resp.status_code != 200:
            report["status"] = "error"
            report["reason"] = (
                f"POST /api/profiles/{{id}}/resume returned {upload_resp.status_code}: "
                f"{upload_resp.get_data(as_text=True)}"
            )
            return report
        upload_data = upload_resp.get_json()
        resume = {"id": upload_data["resume_id"]}
        report["steps"].append({"step": "upload_resume", "status": "ok",
                                "resume_id": resume["id"]})
        report["input_boundaries"]["resume_length"] = len(resume_text)
        report["input_boundaries"]["profile_id"] = profile["id"]

        # Step 2: POST /api/discovery/analyses (ai_consent=true)
        resp = client.post(
            "/api/discovery/analyses",
            json={"resume_id": resume["id"], "ai_consent": True},
        )
        if resp.status_code != 202:
            report["status"] = "error"
            report["reason"] = (f"POST /analyses returned {resp.status_code}: "
                                 f"{resp.get_data(as_text=True)}")
            return report
        analysis_id = resp.get_json()["analysis_id"]

        # 轮询 GET /api/discovery/analyses/{id} 直到 ready/failed
        # AI 分析可能需要较长时间（免费模型有排队），超时设为 180 秒
        analysis_status = _poll_analysis_status(client, analysis_id, timeout=180.0)

        # AI 响应可能不稳定（免费模型），失败时自动 retry 最多 3 次
        # （总共最多 4 次 analysis 尝试）。每次 retry 创建新版本 analysis。
        retry_count = 0
        max_retries = 3
        while analysis_status == "failed" and retry_count < max_retries:
            retry_count += 1
            retry_resp = client.post(
                f"/api/discovery/analyses/{analysis_id}/retry",
                json={"ai_consent": True},
            )
            if retry_resp.status_code != 202:
                break
            old_analysis_id = analysis_id
            analysis_id = retry_resp.get_json()["analysis_id"]
            report["steps"].append({
                "step": f"analyze_retry_{retry_count}", "status": "submitted",
                "old_analysis_id": old_analysis_id,
                "new_analysis_id": analysis_id,
            })
            analysis_status = _poll_analysis_status(
                client, analysis_id, timeout=180.0,
            )

        analysis_resp = client.get(f"/api/discovery/analyses/{analysis_id}")
        analysis_data = analysis_resp.get_json() if analysis_resp.status_code == 200 else {}
        directions = analysis_data.get("directions", [])
        report["steps"].append({"step": "analyze", "status": analysis_status,
                                "analysis_id": analysis_id,
                                "direction_count": len(directions)})
        if analysis_status != "ready":
            # 记录失败原因（从 GET /analyses/{id} 响应中提取 failure 信封）
            failure_info = {}
            try:
                analysis_resp = client.get(f"/api/discovery/analyses/{analysis_id}")
                if analysis_resp.status_code == 200:
                    analysis_data = analysis_resp.get_json()
                    failure_info = analysis_data.get("failure") or {}
            except Exception:
                pass
            report["status"] = "blocked"
            report["reason"] = f"analysis did not reach ready (status={analysis_status})"
            report["analysis_failure"] = failure_info
            return report

        # Step 3: POST /api/discovery/confirmations
        # 优先 default_enabled 方向；AI 免费模型不稳定，可能返回的 default_enabled
        # 不足。不足时补充非 default 方向（模拟用户手动启用），确保多方向搜索
        # 计划可被测试。总方向数 <2 才真正阻塞。
        enabled_dirs = [d for d in directions if d.get("default_enabled", True)]
        if len(enabled_dirs) < 2:
            other_dirs = [d for d in directions if not d.get("default_enabled", True)]
            enabled_dirs = (enabled_dirs + other_dirs)[:3]
        if len(enabled_dirs) < 2:
            report["status"] = "blocked"
            report["reason"] = (f"expected >=2 directions total, "
                                 f"got {len(enabled_dirs)}")
            return report
        direction_ids = [d["id"] for d in enabled_dirs[:2]]
        resp = client.post(
            "/api/discovery/confirmations",
            json={
                "analysis_id": analysis_id,
                "enabled_direction_ids": direction_ids,
                "hard_constraints": {"city": "北京"},
                # T133: 减少 detail_budget 让 E2E 在合理时间内完成。
                # 默认 60 个 detail × 3 directions = 180 次 AI 评估调用，
                # 免费模型每次 5-10 秒，总共 15-30 分钟会超时。
                # T133 只要求至少 1 个真实详情/评估；固定 2 个已确认方向
                # 和 1 个详情，将真实外部评估限制为 2 次，避免未确认方向
                # 或过量详情把受控验收拖入超时。
                "safe_limits": {"max_details": 1},
            },
        )
        if resp.status_code != 201:
            report["status"] = "error"
            report["reason"] = (f"POST /confirmations returned {resp.status_code}: "
                                 f"{resp.get_data(as_text=True)}")
            return report
        confirmation_id = resp.get_json()["confirmation_id"]
        report["steps"].append({"step": "confirm", "status": "ok",
                                "confirmation_id": confirmation_id,
                                "confirmed_directions": len(direction_ids)})
        # 诊断：dump confirmed directions 的 search_terms，便于定位
        # source_count=0 是否因 search_terms 太冷门
        report["confirmed_directions_detail"] = [
            {"name": d.get("name", ""),
             "search_terms": d.get("search_terms", []),
             "default_enabled": d.get("default_enabled", True)}
            for d in enabled_dirs[:2]
        ]

        # Step 4: POST /api/discovery/runs (自动提交 runtime)
        resp = client.post(
            "/api/discovery/runs",
            json={"confirmation_id": confirmation_id},
        )
        if resp.status_code != 202:
            report["status"] = "error"
            report["reason"] = (f"POST /runs returned {resp.status_code}: "
                                 f"{resp.get_data(as_text=True)}")
            return report
        run_id = resp.get_json()["run_id"]
        report["steps"].append({"step": "create_run", "status": "ok",
                                "run_id": run_id})
        report["write_scope"]["db"] = tmp_db.name

        # Step 5: 轮询 GET /api/discovery/runs/{id} 直到终态
        # source.fetch_list 单 item timeout=600s，3 items 可能需要最多 18 分钟。
        # 设 480s 足以完成 1-2 items 并观察部分行为；未到终态也记录诊断信息。
        run_status = _poll_run_status(client, run_id, timeout=480.0)
        run = store.get_discovery_run(run_id)
        # T133: get_discovery_run 返回的字典中没有 "counters" key。
        # 直接列名 source_count/detail_count/evaluated_count/high_count/...
        # 是真实值；progress 是 {source_count, detail_count, evaluated_count}；
        # counts 是 {high, adjacent, growth, review, unsuitable}。
        # 旧代码读 run.get("counters", {}) 返回 {}，导致 source_count=0 误报。
        report["counts"]["source_count"] = int(run.get("source_count", 0))
        report["counts"]["detail_count"] = int(run.get("detail_count", 0))
        report["counts"]["evaluated_count"] = int(run.get("evaluated_count", 0))
        report["counts"]["high_count"] = int(run.get("high_count", 0))
        report["counts"]["adjacent_count"] = int(run.get("adjacent_count", 0))
        report["counts"]["growth_count"] = int(run.get("growth_count", 0))
        report["steps"].append({"step": "run", "status": run_status})
        # 诊断：dump events + plan items 状态，便于定位 source_count=0 原因
        report["run_diagnostics"] = _dump_run_diagnostics(store, run_id)

        # Step 6: GET /api/discovery/runs/{id}/results
        resp = client.get(f"/api/discovery/runs/{run_id}/results")
        results = {}
        if resp.status_code == 200:
            results = resp.get_json()
            report["steps"].append({
                "step": "results", "status": "ok",
                "item_count": len(results.get("items", [])),
                "result_counts": results.get("counts", {}),
            })
        else:
            report["steps"].append({
                "step": "results", "status": "error",
                "status_code": resp.status_code,
            })

        # Step 7: POST /api/discovery/feedback
        # T133 审计要求：即使 counts=0 也要记录 feedback 步骤，不得静默跳过。
        # 反馈目标只来自 GET /results 的真实 JobResult，不直调 build_portfolio。
        result_items = results.get("items", []) if isinstance(results, dict) else []
        if result_items:
            first = result_items[0]
            primary = first.get("primary_assessment") or {}
            resp = client.post(
                "/api/discovery/feedback",
                json={
                    "profile_id": profile["id"],
                    "target_type": "job",
                    "action": "interested",
                    "scope": "exact_job",
                    "run_id": run_id,
                    "target_id": first.get("job_id", ""),
                    "direction_id": primary.get("direction_id", ""),
                },
            )
            if resp.status_code == 201:
                report["feedback"] = {
                    "status": "ok",
                    "job_id": first.get("job_id", ""),
                    "feedback_id": resp.get_json().get("feedback_id", ""),
                }
            else:
                report["feedback"] = {
                    "status": "error",
                    "status_code": resp.status_code,
                    "body": resp.get_data(as_text=True),
                }
        else:
            # 无岗位可反馈 — 记录事实，不静默跳过
            report["feedback"] = {
                "status": "no_portfolio_items",
                "reason": ("BOSS 搜索返回 0 结果或评估无 high_match 岗位，"
                           "无 portfolio item 可反馈"),
                "source_count": report["counts"].get("source_count", 0),
                "evaluated_count": report["counts"].get("evaluated_count", 0),
            }
        report["steps"].append({"step": "feedback",
                                "status": report["feedback"]["status"]})

        # Step 8: Cancel 流程验证
        # 提交第二个 run，立即 cancel，断言 run 变为 cancelled 且后续工作不执行。
        cancel_report = _test_cancel_flow(
            client, store, profile, resume, analysis_id, confirmation_id,
        )
        report["cancel_test"] = cancel_report
        report["steps"].append({"step": "cancel_test",
                                "status": cancel_report.get("status", "unknown")})

        # Step 9: Resume 流程验证
        # 创建第三个 run（不通过 HTTP 提交，直接用 store 创建以模拟中断），
        # 手动标记 interrupted，然后 POST /resume 验证重新执行。
        resume_report = _test_resume_flow(
            client, store, profile, resume, analysis_id, confirmation_id,
        )
        report["resume_test"] = resume_report
        if resume_report.get("interrupt_point"):
            report["interrupt_points"].append(resume_report["interrupt_point"])
        report["steps"].append({"step": "resume_test",
                                "status": resume_report.get("status", "unknown")})

        report["run_status"] = run_status
        _finalize_t133_report(report)

    except Exception as exc:
        report["status"] = "error"
        report["reason"] = str(exc)
        report["traceback"] = traceback.format_exc()
        report["interrupt_points"].append({
            "step": report["steps"][-1]["step"] if report["steps"] else "init",
            "error": str(exc),
        })
    finally:
        # 关闭 runtime executor，避免后台线程持有 SQLite 连接
        # T133: shutdown(wait=True) 会阻塞等待所有后台任务完成。
        # 如果主 run 仍在评估阶段（180 次 AI 调用），会阻塞很久。
        # 用 cancel_futures=True 取消排队任务，wait=True 等待正在运行
        # 的任务到下一个 cancel 检查点后退出。
        runtime = app.config.get("DISCOVERY_RUNTIME")
        if runtime is not None:
            try:
                runtime._executor.shutdown(wait=True, cancel_futures=True)
            except Exception:
                pass
        if os.path.exists(tmp_db.name):
            try:
                os.unlink(tmp_db.name)
            except PermissionError:
                pass

    return report


def _run_e2e_with_market_retries(resume_name: str, *, runner=None) -> dict:
    """Retry reasonable de-identified resumes only when real search is empty.

    A zero-result market response cannot validate detail/evaluation/feedback or
    cancellation/resume.  The retry record contains only counts and stable
    blocker codes; it never copies resume text, prompts or model responses.
    """
    runner = runner or _run_e2e
    candidates = []
    for candidate in (
        resume_name,
        "resume_single_path.txt",
        "resume_junior.txt",
        "resume_multi_industry_gap.txt",
    ):
        if candidate not in candidates:
            candidates.append(candidate)

    attempts: list[dict] = []
    selected: dict = {}
    for candidate in candidates:
        selected = runner(candidate)
        counts = selected.get("counts") or {}
        blockers = list(selected.get("blockers") or [])
        attempts.append({
            "resume": candidate,
            "status": selected.get("status"),
            "source_count": int(counts.get("source_count") or 0),
            "detail_count": int(counts.get("detail_count") or 0),
            "evaluated_count": int(counts.get("evaluated_count") or 0),
            "blockers": blockers,
        })
        selected["market_attempts"] = list(attempts)
        selected["selected_resume"] = candidate
        if selected.get("status") == "completed":
            return selected
        if "real_list_missing" not in blockers:
            return selected

    selected["status"] = "blocked"
    selected["market_attempts"] = attempts
    selected["market_search_blocker"] = (
        "真实搜索在测试时段返回 0 结果，已尝试多个脱敏简历/搜索词组合，"
        "无法验证列表/详情/评估/反馈/取消恢复。"
    )
    selected["reason"] = selected["market_search_blocker"]
    return selected


def _poll_analysis_status(client, analysis_id: str, timeout: float = 60.0) -> str:
    """轮询 GET /api/discovery/analyses/{id} 直到 ready/failed。"""
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/discovery/analyses/{analysis_id}")
        if resp.status_code == 200:
            last_status = resp.get_json().get("status")
            if last_status in ("ready", "failed"):
                return last_status
        time.sleep(0.5)
    return last_status or "timeout"


def _poll_run_status(client, run_id: str, timeout: float = 240.0) -> str:
    """轮询 GET /api/discovery/runs/{id} 直到终态 (succeeded/failed/cancelled)。

    T133: 超时后主动 POST /cancel，避免后台任务继续运行导致
    runtime.shutdown(wait=True) 阻塞。cancel 后再等最多 30 秒让 run
    到终态。
    """
    terminal = {"succeeded", "failed", "cancelled"}
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/discovery/runs/{run_id}")
        if resp.status_code == 200:
            last_status = resp.get_json().get("status")
            if last_status in terminal:
                return last_status
        time.sleep(1.0)
    # 超时后主动 cancel，避免 shutdown 阻塞
    if last_status not in terminal:
        try:
            client.post(f"/api/discovery/runs/{run_id}/cancel")
        except Exception:
            pass
        cancel_deadline = time.monotonic() + 30.0
        while time.monotonic() < cancel_deadline:
            resp = client.get(f"/api/discovery/runs/{run_id}")
            if resp.status_code == 200:
                last_status = resp.get_json().get("status")
                if last_status in terminal:
                    break
            time.sleep(1.0)
    return last_status or "timeout"


def _dump_run_diagnostics(store, run_id) -> dict:
    """从 store 读取 run 的 events 和 search plan items 状态，用于诊断。

    T133: 当 source_count=0 或 run 未到终态时，需要这些信息来定位原因
    （如 plan item 失败码、source_blocked、input_hash_mismatch 等）。
    """
    diag: dict = {"events": [], "plan_items": []}
    try:
        events = store.list_discovery_events(run_id)
        diag["events"] = [
            {"seq": e.get("sequence"), "type": e.get("event_type"),
             "payload": e.get("payload")}
            for e in events
        ]
    except Exception as exc:
        diag["events_error"] = str(exc)
    try:
        plan = store.get_search_plan(run_id)
        diag["plan_items"] = [
            {"keyword": it.get("keyword"), "city": it.get("city"),
             "status": it.get("status"),
             "failure_code": it.get("failure_code"),
             "input_hash_prefix": (it.get("input_hash") or "")[:12]}
            for it in plan.get("items", [])
        ]
    except Exception as exc:
        diag["plan_items_error"] = str(exc)
    return diag


def _wait_for_run_stage(client, run_id: str, stages: set[str], timeout: float) -> str:
    """Wait until a run enters one of the requested active stages or terminates."""
    terminal = {"succeeded", "partial", "failed", "interrupted", "cancelled"}
    deadline = time.monotonic() + timeout
    last_status = ""
    while time.monotonic() < deadline:
        resp = client.get(f"/api/discovery/runs/{run_id}")
        if resp.status_code == 200:
            body = resp.get_json() or {}
            last_status = body.get("status", "")
            if last_status in stages or last_status in terminal:
                return last_status
        time.sleep(0.1)
    return last_status or "timeout"


def _test_cancel_flow(client, store, profile, resume, analysis_id,
                      confirmation_id) -> dict:
    """Cancel via HTTP during list/detail work and prove no new unit starts."""
    report: dict = {"status": "running"}
    try:
        # 用同一 confirmation 创建第二个 run（HTTP POST /runs 自动提交 runtime）
        resp = client.post(
            "/api/discovery/runs",
            json={"confirmation_id": confirmation_id},
        )
        if resp.status_code != 202:
            return {"status": "error",
                    "reason": f"POST /runs returned {resp.status_code}: "
                              f"{resp.get_data(as_text=True)}"}
        run_id = resp.get_json()["run_id"]
        report["run_id"] = run_id

        cancel_stage = _wait_for_run_stage(
            client, run_id, {"fetching_lists", "fetching_details"}, timeout=120.0,
        )
        report["cancel_stage"] = cancel_stage
        if cancel_stage not in {"fetching_lists", "fetching_details"}:
            return {
                **report,
                "status": "blocked",
                "reason": f"run did not enter list/detail stage before cancel ({cancel_stage})",
            }

        # POST /cancel through the user HTTP route after real work has entered
        # list/detail. plan_item_started/detail_fetch_started events make the
        # post-cancel boundary independently auditable.
        resp = client.post(f"/api/discovery/runs/{run_id}/cancel")
        if resp.status_code == 409:
            # run 已经终态（太快完成了）— 记录事实
            run = store.get_discovery_run(run_id)
            return {"status": "already_terminal", "run_id": run_id,
                    "run_status": run["status"],
                    "reason": "run completed before cancel could take effect"}
        if resp.status_code != 202:
            return {"status": "error", "run_id": run_id,
                    "reason": f"POST /cancel returned {resp.status_code}: "
                              f"{resp.get_data(as_text=True)}"}

        # 轮询直到终态
        run_status = _poll_run_status(client, run_id, timeout=60.0)
        plan = store.get_search_plan(run_id)
        events = store.list_discovery_events(run_id)
        cancel_events = [e for e in events if e.get("event_type") == "cancel_requested"]
        cancel_sequence = cancel_events[-1].get("sequence", 0) if cancel_events else 0
        started_after_cancel = [
            e for e in events
            if int(e.get("sequence") or 0) > int(cancel_sequence or 0)
            and e.get("event_type") in {"plan_item_started", "detail_fetch_started"}
        ]
        cancelled_items = [it for it in plan["items"] if it["status"] == "cancelled"]
        pending = [
            it for it in plan["items"]
            if it["status"] not in ("completed", "failed", "cancelled", "skipped")
        ]
        report.update({
            "run_status": run_status,
            "cancel_sequence": cancel_sequence,
            "cancelled_unfinished_count": len(cancelled_items),
            "new_work_started_after_cancel": len(started_after_cancel),
            "pending_items_after_cancel": len(pending),
        })
        if (run_status == "cancelled" and cancel_sequence
                and cancelled_items and not started_after_cancel and not pending):
            report["status"] = "ok"
        else:
            report["status"] = "blocked"
            report["reason"] = (
                "cancel did not prove that unfinished work was stopped "
                f"(status={run_status}, cancelled={len(cancelled_items)}, "
                f"started_after={len(started_after_cancel)}, pending={len(pending)})"
            )
    except Exception as exc:
        report["status"] = "error"
        report["reason"] = str(exc)
    return report


def _test_resume_flow(client, store, profile, resume, analysis_id,
                      confirmation_id) -> dict:
    """Interrupt an HTTP-created real-source run, restart-converge, then resume."""
    import threading

    from webui.discovery_runner import DiscoveryTaskRuntime

    report: dict = {"status": "running", "created_via_http": False}
    runtime = None
    original_source_factory = None
    try:
        runtime = client.application.config["DISCOVERY_RUNTIME"]
        original_source_factory = runtime._source_factory
        real_source = original_source_factory()

        class _ControlledProcessInterruption(BaseException):
            pass

        class _InterruptAfterOneCompletedList:
            def __init__(self, delegate):
                self.delegate = delegate
                self.list_calls = 0
                self.triggered = threading.Event()

            def fetch_list(self, plan_item):
                self.list_calls += 1
                if self.list_calls > 1:
                    self.triggered.set()
                    raise _ControlledProcessInterruption("controlled restart")
                return self.delegate.fetch_list(plan_item)

            def fetch_detail(self, job, *, detail_output_path=None):
                return self.delegate.fetch_detail(
                    job, detail_output_path=detail_output_path,
                )

        interrupting_source = _InterruptAfterOneCompletedList(real_source)
        runtime._source_factory = lambda: interrupting_source

        # Create and submit through the actual HTTP route.
        resp = client.post(
            "/api/discovery/runs", json={"confirmation_id": confirmation_id},
        )
        if resp.status_code != 202:
            return {"status": "error", "created_via_http": False,
                    "reason": f"POST /runs returned {resp.status_code}"}
        run_id = resp.get_json()["run_id"]
        report["run_id"] = run_id
        report["created_via_http"] = True

        if not interrupting_source.triggered.wait(timeout=700.0):
            runtime._source_factory = original_source_factory
            return {**report, "status": "blocked",
                    "reason": "controlled interruption did not trigger during list stage"}

        # The BaseException ends only this worker future, emulating abrupt
        # process loss without letting application code manufacture a terminal
        # status.  Then construct a fresh runtime to execute the real startup
        # convergence rule active -> interrupted.
        future = runtime._futures.get(run_id)
        deadline = time.monotonic() + 10.0
        while future is not None and not future.done() and time.monotonic() < deadline:
            time.sleep(0.05)
        pre_restart_run = store.get_discovery_run(run_id)
        interruption_stage = pre_restart_run.get("stage") or pre_restart_run.get("status")
        report["interruption_stage"] = interruption_stage
        events_before = store.list_discovery_events(run_id)
        plan_before = store.get_search_plan(run_id)
        completed_before = {
            item["id"] for item in plan_before["items"]
            if item.get("status") == "completed"
        }
        unfinished_before = {
            item["id"] for item in plan_before["items"]
            if item.get("status") != "completed"
        }
        report["unfinished_before_resume"] = len(unfinished_before)

        runtime._source_factory = original_source_factory
        restart_probe = DiscoveryTaskRuntime(store)
        restart_probe.shutdown()
        interrupted_run = store.get_discovery_run(run_id)
        report["pre_resume_status"] = interrupted_run.get("status")
        report["interrupt_point"] = {
            "stage": interruption_stage,
            "kind": "controlled_restart",
            "completed_before": len(completed_before),
            "unfinished_before": len(unfinished_before),
        }
        if interrupted_run.get("status") != "interrupted":
            return {**report, "status": "blocked",
                    "reason": "restart convergence did not mark run interrupted"}

        # POST /resume（HTTP 路由验证）
        resp = client.post(f"/api/discovery/runs/{run_id}/resume")
        if resp.status_code != 202:
            return {"status": "error", "run_id": run_id,
                    "reason": f"POST /resume returned {resp.status_code}: "
                               f"{resp.get_data(as_text=True)}"}

        # 轮询直到终态
        run_status = _poll_run_status(client, run_id, timeout=480.0)
        final_run = store.get_discovery_run(run_id)
        report["post_resume_status"] = run_status
        report["post_resume_counts"] = {
            "source_count": int(final_run.get("source_count", 0)),
            "detail_count": int(final_run.get("detail_count", 0)),
            "evaluated_count": int(final_run.get("evaluated_count", 0)),
        }

        events = store.list_discovery_events(run_id)
        resume_events = [e for e in events if e.get("event_type") == "resume_accepted"]
        resume_sequence = resume_events[-1].get("sequence", 0) if resume_events else 0
        starts_after_resume = [
            e for e in events
            if int(e.get("sequence") or 0) > int(resume_sequence or 0)
            and e.get("event_type") == "plan_item_started"
        ]
        started_item_ids = {
            (e.get("payload") or {}).get("item_id") for e in starts_after_resume
        }
        report["resubmitted_unfinished"] = len(
            unfinished_before.intersection(started_item_ids)
        )
        report["duplicate_completed_count"] = len(
            completed_before.intersection(started_item_ids)
        )
        report["diagnostics"] = _dump_run_diagnostics(store, run_id)
        if (run_status in ("succeeded", "partial", "failed")
                and report["unfinished_before_resume"] > 0
                and report["resubmitted_unfinished"] > 0
                and report["duplicate_completed_count"] == 0):
            report["status"] = "ok"
        else:
            report["status"] = "blocked"
            report["reason"] = (
                "resume did not prove unfinished resubmission without repeats "
                f"(status={run_status}, unfinished={report['unfinished_before_resume']}, "
                f"resubmitted={report['resubmitted_unfinished']}, "
                f"duplicates={report['duplicate_completed_count']})"
            )
        # 如果 resume 后 failed，记录 failure_code 便于诊断
        if run_status == "failed":
            report["failure_code"] = final_run.get("failure_code")
            report["failure_stage"] = final_run.get("failure_stage")
    except Exception as exc:
        report["status"] = "error"
        report["reason"] = str(exc)
    finally:
        if runtime is not None and original_source_factory is not None:
            runtime._source_factory = original_source_factory
    return report


def _build_real_ai_provider():
    """Build a real AI provider adapter for feature 004.

    T132/T133: 现在返回真正的 DiscoveryAIProvider 实例（webui.ai 模块），
    持 endpoint/model/api_key，不读写 TaskStore。错误码映射到 ai_* 前缀。

    AI 凭据从生产 DB (~/.career-scout/webui/webui.db) 加载，
    而不是从 _run_e2e 的临时 DB store 加载——临时 DB 没有配置 AI 设置。

    Feature 004 需要两个 AI 调用：
      1. analyze(resume_text) -> {summary, evidence, unknowns, directions}
      2. assess_job(candidate_summary, direction, evidence, job_snapshot)
         -> {dimensions, match_score, ...}
    """
    settings, cred_ref = _load_ai_settings()
    if not settings.get("is_configured"):
        return None
    if not cred_ref:
        return None
    api_key = _retrieve_api_key(cred_ref)
    if not api_key:
        return None
    from webui.ai import DiscoveryAIProvider
    return DiscoveryAIProvider(
        endpoint=settings.get("endpoint_url", ""),
        api_key=api_key,
        model=settings.get("model", ""),
    )


def _run_live_provider_smoke() -> dict:
    """T132: 脱敏 live-provider contract smoke。

    分别验证 candidate-analysis v2 与 job-assessment v1。
    记录 endpoint/model/时间/合同结果，但不记录 key/prompt/raw response。
    """
    from webui.store import TaskStore
    from webui.ai import DiscoveryAIProvider

    db_path = os.path.expanduser("~/.career-scout/webui/webui.db")
    store = TaskStore(db_path)
    settings = store.get_ai_settings()
    cred_ref = store.get_credential_ref() if hasattr(store, "get_credential_ref") else ""
    api_key = _retrieve_api_key(cred_ref) if cred_ref else ""

    report = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "endpoint": settings.get("endpoint_url", ""),
        "model": settings.get("model", ""),
        "candidate_analysis_v2": {"status": "pending"},
        "job_assessment_v1": {"status": "pending"},
    }

    if not settings.get("is_configured") or not api_key:
        report["candidate_analysis_v2"]["status"] = "blocked"
        report["candidate_analysis_v2"]["reason"] = "AI credentials not configured"
        report["job_assessment_v1"]["status"] = "blocked"
        report["job_assessment_v1"]["reason"] = "AI credentials not configured"
        return report

    provider = DiscoveryAIProvider(
        endpoint=settings.get("endpoint_url", ""),
        api_key=api_key,
        model=settings.get("model", ""),
    )

    # --- candidate-analysis v2 smoke ---
    # 使用脱敏的测试简历（不含真实 PII，且用唯一性强的描述避免 quote 歧义）
    smoke_resume = (
        "求职意向：高级后端开发工程师（分布式系统方向）\n"
        "工作经历：\n"
        "2020-2024 某科技有限公司 后端架构师\n"
        "- 主导订单中台微服务拆分，将单体服务拆分为 12 个独立微服务\n"
        "- 设计 Redis Cluster 多级缓存方案，订单查询 P99 延迟从 800ms 降至 95ms\n"
        "- 构建 Kafka 异步消息总线，日均处理 5000 万条订单事件\n"
        "技能清单：\n"
        "- 编程语言：Golang、Java、Python（按熟练度排序）\n"
        "- 数据存储：TiDB、Elasticsearch、Cassandra\n"
        "教育背景：\n"
        "2016-2020 某大学 计算机科学与技术 本科\n"
    )
    try:
        result = provider.analyze(resume_text=smoke_resume)
        # 验证 v2 contract：summary + evidence + directions
        has_summary = isinstance(result.get("summary"), dict)
        has_evidence = isinstance(result.get("evidence"), list)
        has_directions = isinstance(result.get("directions"), list)
        # 验证 evidence 含 source_quote（v2 契约要求）
        v2_ok = has_summary and has_evidence and has_directions
        if v2_ok:
            for ev in result.get("evidence", []):
                if not ev.get("source_quote"):
                    v2_ok = False
                    break
        report["candidate_analysis_v2"] = {
            "status": "pass" if v2_ok else "contract_violation",
            "has_summary": has_summary,
            "has_evidence": has_evidence,
            "has_directions": has_directions,
            "evidence_count": len(result.get("evidence", [])),
            "direction_count": len(result.get("directions", [])),
            "all_evidence_has_source_quote": v2_ok,
        }
    except Exception as exc:
        # 安全错误码，不含 raw response
        code = getattr(exc, "error_code", "unknown")
        report["candidate_analysis_v2"] = {
            "status": "failed",
            "error_code": code,
        }

    # --- job-assessment v1 smoke ---
    try:
        candidate_summary = {
            "headline": "后端开发工程师",
            "experience_level": "中级",
            "domains": ["后端"],
            "strengths": ["Python", "系统设计"],
        }
        direction = {
            "id": "d1",
            "name": "后端开发工程师",
            "type": "core",
            "rationale": "后端经验",
            "gaps": [],
            "evidence": [],
            "evidence_refs": [],
            "analysis_evidence_ids": [],
        }
        evidence = []
        job_snapshot = {
            "job_id": "smoke-job-1",
            "completeness": "complete",
            "fields": {
                "title": "Python 后端工程师",
                "company": "某科技公司",
                "jd": "负责后端服务开发，使用 Python/Flask，"
                       "设计高并发接口，熟悉 MySQL/Redis。",
                "salary": "20-35K",
                "location": "北京",
                "tags": "Python,Flask,MySQL",
            },
        }
        result = provider.assess_job(
            candidate_summary=candidate_summary,
            direction=direction,
            evidence=evidence,
            job_snapshot=job_snapshot,
        )
        has_dims = isinstance(result.get("dimensions"), dict)
        has_match_score = "match_score" in result
        has_confidence = "confidence" in result
        has_proposed_band = "proposed_band" in result
        v1_ok = has_dims and has_match_score and has_confidence and has_proposed_band
        report["job_assessment_v1"] = {
            "status": "pass" if v1_ok else "contract_violation",
            "has_dimensions": has_dims,
            "has_match_score": has_match_score,
            "has_confidence": has_confidence,
            "has_proposed_band": has_proposed_band,
        }
    except Exception as exc:
        code = getattr(exc, "error_code", "unknown")
        report["job_assessment_v1"] = {
            "status": "failed",
            "error_code": code,
        }

    return report


def _enable_line_buffering(stream) -> None:
    """Make stage output visible immediately when stdout is piped."""
    try:
        stream.reconfigure(line_buffering=True)
    except (AttributeError, OSError, ValueError):
        pass


def main(browser_backend=None) -> int:
    _enable_line_buffering(sys.stdout)
    print("=" * 72)
    print("Feature 004 Controlled Real-BOSS E2E")
    print("=" * 72)

    browser = ManagedCdpBrowser(
        browser_backend or BossCdpBackend(),
        close_reused="--close-browser-after" in sys.argv,
    )
    with browser:
        print("\n[1/2] Checking prerequisites...")
        prereqs = _check_prerequisites()

        # Tri-state display: cdp/ai_credentials are bool; boss_login is True/False/"unknown".
        for key in ("cdp", "boss_login", "ai_credentials"):
            val = prereqs[key]
            if val is True:
                status = "OK"
            elif val is False:
                status = "MISSING"
            else:  # "unknown"
                status = "UNKNOWN (cannot verify)"
            print(f"  {key}: {status}")
            if key == "boss_login" and val == "unknown" and prereqs.get("boss_login_note"):
                print(f"    note: {prereqs['boss_login_note']}")
        if prereqs["errors"]:
            print("  Errors:")
            for err in prereqs["errors"]:
                print(f"    - {err}")

        # Block unless all three are explicitly True. "unknown" blocks too —
        # we never run E2E against real BOSS without verified login state.
        all_ok = prereqs["cdp"] is True and prereqs["boss_login"] is True and prereqs["ai_credentials"] is True
        if not all_ok:
            print("\n[2/2] BLOCKED — prerequisites not met.")
            print("  To unblock:")
            print("    1. Start Chrome CDP: python scripts/boss_cdp_raw.py --setup-chrome")
            print("    2. Login to zhipin.com in that Chrome")
            print("    3. Configure AI API key in webui settings")
            print("  This script will NOT simulate the missing capabilities.")
            report = {"status": "blocked", "prerequisites": prereqs}
            exit_code = 1
        else:
            print("\n[2/2] Prerequisites met. Running E2E...")
            resume_name = sys.argv[sys.argv.index("--resume") + 1] if "--resume" in sys.argv else "resume_cross_family.txt"

            # T132: 先运行 live-provider contract smoke
            print("\n--- T132: Live-provider contract smoke ---")
            smoke_report = _run_live_provider_smoke()
            print(json.dumps(smoke_report, ensure_ascii=False, indent=2))
            smoke_path = FIXTURE_DIR / "live_provider_smoke_result.json"
            with smoke_path.open("w", encoding="utf-8") as fh:
                json.dump(smoke_report, fh, ensure_ascii=False, indent=2)
            print(f"Smoke report written to: {smoke_path}")

            # T133: 运行真实 BOSS E2E
            print("\n--- T133: Real BOSS E2E ---")
            report = _run_e2e_with_market_retries(resume_name)
            exit_code = 0 if report.get("status") == "completed" else 1

            print("\n" + "=" * 72)
            print("E2E Report")
            print("=" * 72)
            print(json.dumps(report, ensure_ascii=False, indent=2))

    report["browser_lifecycle"] = browser.report()
    if report["browser_lifecycle"]["close_status"] == "close_failed":
        report.setdefault("operational_blockers", []).append("browser_close_failed")
        exit_code = 1
    report_path = FIXTURE_DIR / "e2e_real_boss_result.json"
    with report_path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\nReport written to: {report_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
