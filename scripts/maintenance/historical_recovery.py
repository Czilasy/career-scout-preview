"""Auditable preview, backup, and recovery for the 2026-07-28 runs.

2026-07-28 事故的一次性修复工具（031 B7 自 ``webui/historical_recovery.py``
整体迁入）。**只作手动运维工具使用**：原 ``/api/recovery/*`` 三条生产路由
已撤除，能力改由命令行显式执行，破坏性动作需 ``--confirm`` 安全栏。

用法::

    uv run python -m scripts.maintenance.historical_recovery preview  [--db PATH]
    uv run python -m scripts.maintenance.historical_recovery prepare  [--db PATH]
    uv run python -m scripts.maintenance.historical_recovery execute --backup-id ID --confirm [--db PATH]

修复逻辑、审计输出与 manifest 结构与迁出前逐行等价；唯一新增是 argparse
外壳与 ``--confirm`` 栏（契约 contracts/recovery-cli.md）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sqlite3
import sys
import uuid
from datetime import datetime
from typing import Any


ROUGH_RUN_ID = "15847d27-7419-4f01-ae09-9e4c9e2641bb"
FINE_RUN_ID = "e6250f0ed794492180269de050bfd41a"

ROUGH_50_JSON_MATCH = 17
ROUGH_50_JSON_NOT_MATCH = 33
FINE_50_UNCERTAIN = 50
PENDING_646 = 646
TOTAL_ANOMALY = 696


_SQL_VERDICTS_BY_RUN = "SELECT platform_job_id, verdict FROM screening_results WHERE run_id = ?"
_DB_FILENAME = "webui.db"
_SQL_VERDICT_JOB_IDS = "SELECT platform_job_id FROM screening_results "


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _query_run_summary(conn, run_id: str) -> dict:
    rows = conn.execute(
        "SELECT verdict, COUNT(*) AS n FROM screening_results "
        "WHERE run_id = ? GROUP BY verdict", (run_id,),
    ).fetchall()
    total_row = conn.execute(
        "SELECT COUNT(*) AS n FROM screening_results WHERE run_id = ?", (run_id,),
    ).fetchone()
    run_row = conn.execute(
        "SELECT source_count, total_dropped, total_kept, total_scraped, status "
        "FROM screening_runs WHERE id = ?", (run_id,),
    ).fetchone()
    jd_row = conn.execute(
        "SELECT COUNT(*) AS n FROM screening_results "
        "WHERE run_id = ? AND JD IS NOT NULL AND length(jd) > 0", (run_id,),
    ).fetchone()

    verdict_counts = {row["verdict"]: int(row["n"]) for row in rows}
    plain_verdicts: dict[str, int] = {}
    inner_verdicts: dict[str, int] = {}
    for verdict, count in verdict_counts.items():
        if verdict.startswith("{"):
            try:
                value = json.loads(verdict)
            except (json.JSONDecodeError, TypeError):
                value = None
            inner = value.get("verdict") if isinstance(value, dict) else verdict
            inner_verdicts[str(inner)] = inner_verdicts.get(str(inner), 0) + count
        else:
            plain_verdicts[verdict] = plain_verdicts.get(verdict, 0) + count
    return {
        "id": run_id,
        "total": int(total_row["n"] or 0) if total_row else 0,
        "verdict_counts": verdict_counts,
        "plain_verdicts": plain_verdicts,
        "inner_verdicts": inner_verdicts,
        "source_count": int(run_row["source_count"] or 0) if run_row else 0,
        "total_dropped": int(run_row["total_dropped"] or 0) if run_row else 0,
        "total_kept": int(run_row["total_kept"] or 0) if run_row else 0,
        "total_scraped": int(run_row["total_scraped"] or 0) if run_row else 0,
        "status": run_row["status"] if run_row else None,
        "jd_count": int(jd_row["n"] or 0) if jd_row else 0,
    }


def _identify_rough_50_json_split(conn, rough_run_id: str) -> dict:
    rows = conn.execute(
        "SELECT platform_job_id, verdict FROM screening_results "
        "WHERE run_id = ? AND verdict LIKE '{%'", (rough_run_id,),
    ).fetchall()
    counts = {"match": 0, "not_match": 0, "other": 0}
    samples = []
    for row in rows:
        try:
            value = json.loads(row["verdict"])
        except (json.JSONDecodeError, TypeError):
            value = None
        inner = value.get("verdict", "") if isinstance(value, dict) else ""
        key = inner if inner in ("match", "not_match") else "other"
        counts[key] += 1
        if isinstance(value, dict) and len(samples) < 3:
            samples.append({
                "job_id": row["platform_job_id"], "platform_job_id": row["platform_job_id"], "inner": inner,
                "reason": str(value.get("reason", ""))[:100],
            })
    return {
        **counts,
        "total": sum(counts.values()),
        "verdict_format": "json_inner",
        "has_valid_verdict": counts["other"] == 0,
        "recovery_action": "format_unify_no_ai_call",
        "sample_verdicts": samples,
    }


def _identify_fine_50_uncertain(conn, fine_run_id: str) -> dict:
    rows = conn.execute(
        _SQL_VERDICTS_BY_RUN,
        (fine_run_id,),
    ).fetchall()
    count = 0
    reason = ""
    sample_ids = []
    for row in rows:
        try:
            value = json.loads(row["verdict"])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict) and value.get("verdict") == "uncertain":
            count += 1
            reason = reason or str(value.get("reason", ""))
            if len(sample_ids) < 5:
                sample_ids.append(row["platform_job_id"])
    return {
        "count": count,
        "reason": reason,
        "sample_job_ids": sample_ids,
        "verdict_format": "json_inner_uncertain",
        "has_valid_verdict": False,
        "recovery_action": "rejudge_via_new_pipeline",
    }


def _identify_pending_646(conn, rough_run_id: str, fine_run_id: str) -> dict:
    rough_ids = {
        row["platform_job_id"] for row in conn.execute(
            _SQL_VERDICT_JOB_IDS +
            "WHERE run_id = ? AND verdict = 'uncertain'", (rough_run_id,),
        ).fetchall()
    }
    fine_ids = {
        row["platform_job_id"] for row in conn.execute(
            "SELECT platform_job_id FROM screening_results WHERE run_id = ?", (fine_run_id,),
        ).fetchall()
    }
    pending_ids = sorted(rough_ids - fine_ids)
    return {
        "count": len(pending_ids),
        "sample_job_ids": pending_ids[:5],
        "cannot_split_30_8_608": True,
        "recovery_action": "write_to_pending_table_realtime_classify",
        "failure_stage_default": "jd_detail",
    }


def _check_762_jd_protection(conn, rough_run_id: str) -> dict:
    jd_row = conn.execute(
        "SELECT COUNT(*) AS n FROM screening_results "
        "WHERE run_id = ? AND jd IS NOT NULL AND length(jd) > 0", (rough_run_id,),
    ).fetchone()
    total_row = conn.execute(
        "SELECT COUNT(*) AS n FROM screening_results WHERE run_id = ?", (rough_run_id,),
    ).fetchone()
    jd_count = int(jd_row["n"] or 0) if jd_row else 0
    return {
        "checked": int(total_row["n"] or 0) if total_row else 0,
        "jd_exists": jd_count,
        "expected_jd": 762,
        "jd_protected": jd_count == 762,
        "protection_action": "skip_recrawl_existing_jd",
    }


def _check_conservation(rough: dict, fine: dict, pending: dict) -> dict:
    source = rough.get("source_count", 0)
    dropped = rough.get("total_dropped", 0)
    kept = rough.get("total_kept", 0)
    fine_processed = fine.get("total", 0)
    pending_count = pending.get("count", 0)
    fine_uncertain = fine.get("inner_verdicts", {}).get("uncertain", 0)
    first_ok = dropped + kept == source
    second_ok = fine_processed + pending_count == kept
    anomaly_ok = pending_count + fine_uncertain == TOTAL_ANOMALY
    return {
        "source": source, "dropped": dropped, "kept": kept,
        "fine_processed": fine_processed, "pending": pending_count,
        "fine_uncertain": fine_uncertain,
        "total_anomaly": pending_count + fine_uncertain,
        "sum_dropped_kept_ok": first_ok,
        "sum_fine_pending_ok": second_ok,
        "anomaly_ok": anomaly_ok,
        "all_ok": first_ok and second_ok and anomaly_ok,
    }


def _check_gate(rough_50: dict, fine_50: dict, pending: dict,
                conservation: dict) -> dict:
    result = {
        "rough_50_json_match_17": rough_50.get("match") == ROUGH_50_JSON_MATCH,
        "rough_50_json_not_match_33": (
            rough_50.get("not_match") == ROUGH_50_JSON_NOT_MATCH
        ),
        "fine_50_uncertain_50": fine_50.get("count") == FINE_50_UNCERTAIN,
        "pending_646": pending.get("count") == PENDING_646,
        "conservation_ok": bool(conservation.get("all_ok")),
    }
    result["all_passed"] = all(result.values())
    return result


def _preview_from_conn(conn, rough_run_id: str, fine_run_id: str) -> dict[str, Any]:
    rough = _query_run_summary(conn, rough_run_id)
    fine = _query_run_summary(conn, fine_run_id)
    rough_50 = _identify_rough_50_json_split(conn, rough_run_id)
    fine_50 = _identify_fine_50_uncertain(conn, fine_run_id)
    pending = _identify_pending_646(conn, rough_run_id, fine_run_id)
    protection = _check_762_jd_protection(conn, rough_run_id)
    conservation = _check_conservation(rough, fine, pending)
    return {
        "rough_run": rough, "fine_run": fine,
        "rough_run_id": rough_run_id, "fine_run_id": fine_run_id,
        "rough_50_json": rough_50, "fine_50_uncertain": fine_50,
        "pending_646": pending, "jd_762_protection": protection,
        "conservation": conservation, "total_anomaly": TOTAL_ANOMALY,
        "written": False,
        "gate_passed": _check_gate(rough_50, fine_50, pending, conservation),
    }


def preview_recovery(store, *, rough_run_id: str = ROUGH_RUN_ID,
                     fine_run_id: str = FINE_RUN_ID,
                     result_dir: str | pathlib.Path | None = None) -> dict[str, Any]:
    """Return the read-only recovery gate evidence for the two frozen runs."""
    del result_dir
    with store._connection() as conn:
        return _preview_from_conn(conn, rough_run_id, fine_run_id)


def _compute_source_fingerprint_conn(conn) -> str:
    schema_row = conn.execute(
        "SELECT MAX(version) AS version FROM schema_migrations"
    ).fetchone()
    runs = [dict(row) for row in conn.execute(
        "SELECT id, status, source_count, total_dropped, total_kept, total_scraped, "
        "processed_count, pending_count FROM screening_runs "
        "WHERE id IN (?, ?) ORDER BY id", (ROUGH_RUN_ID, FINE_RUN_ID),
    ).fetchall()]
    results = [list(row) for row in conn.execute(
        "SELECT run_id, platform_job_id, verdict, COALESCE(jd, '') FROM screening_results "
        "WHERE run_id IN (?, ?) ORDER BY run_id, platform_job_id, verdict, COALESCE(jd, '')",
        (ROUGH_RUN_ID, FINE_RUN_ID),
    ).fetchall()]
    pending = [list(row) for row in conn.execute(
        "SELECT run_id, platform_job_id, failure_stage, retryable, attempts, origin_zone, "
        "COALESCE(failed_code, ''), ai_payload_json FROM screening_pending_results "
        "WHERE run_id IN (?, ?) ORDER BY run_id, platform_job_id",
        (ROUGH_RUN_ID, FINE_RUN_ID),
    ).fetchall()]
    payload = {
        "schema_version": int(schema_row["version"] or 0),
        "runs": runs,
        "results": results,
        "pending": pending,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _compute_source_fingerprint(store) -> str:
    with store._connection() as conn:
        return _compute_source_fingerprint_conn(conn)


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_path(store, backup_id: str) -> pathlib.Path:
    if not backup_id or any(ch not in "0123456789abcdef-" for ch in backup_id.lower()):
        raise ValueError("invalid_backup_id")
    root = pathlib.Path(store.db_path).resolve().parent / "backups"
    path = (root / backup_id / "manifest.json").resolve()
    if root.resolve() not in path.parents:
        raise ValueError("invalid_backup_id")
    return path


def _is_verified_committed_recovery_audit(store, audit_row) -> bool:
    """Bind a committed audit to its immutable server-owned backup manifest."""
    recovery_key = str(audit_row["recovery_key"] or "")
    backup_id = str(audit_row["backup_id"] or "")
    try:
        manifest_path = _manifest_path(store, backup_id)
    except ValueError:
        return False
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if (
        manifest.get("backup_id") != backup_id
        or manifest.get("recovery_key") != recovery_key
        or manifest.get("status") != "committed"
        or manifest.get("operation") == "pending_metadata_repair"
    ):
        return False
    source_fingerprint = str(manifest.get("source_fingerprint") or "")
    expected_key = hashlib.sha256(
        f"{source_fingerprint}|{ROUGH_RUN_ID}|{FINE_RUN_ID}".encode()
    ).hexdigest()
    if not source_fingerprint or expected_key != recovery_key:
        return False
    backup_path = manifest_path.parent / _DB_FILENAME
    expected_sha256 = str(manifest.get("backup_sha256") or "")
    return (
        backup_path.is_file()
        and bool(expected_sha256)
        and _sha256_file(backup_path) == expected_sha256
    )


def _committed_metadata_repair_error(
        manifest_path: pathlib.Path, manifest: dict, audit_row) -> str | None:
    """Return an integrity error for a committed metadata-repair idempotency check."""
    recovery_key = str(manifest.get("recovery_key") or "")
    source_fingerprint = str(manifest.get("source_fingerprint") or "")
    expected_key = hashlib.sha256(
        f"{source_fingerprint}|{ROUGH_RUN_ID}|{FINE_RUN_ID}".encode()
    ).hexdigest()
    if (
        manifest.get("status") != "committed"
        or manifest.get("operation") != "pending_metadata_repair"
        or not source_fingerprint
        or recovery_key != expected_key
        or str(audit_row["recovery_key"] or "") != recovery_key
        or str(audit_row["backup_id"] or "") != str(manifest.get("backup_id") or "")
    ):
        return "invalid_repair_manifest"
    try:
        stats = json.loads(audit_row["stats_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return "invalid_repair_audit"
    if (
        stats.get("operation") != "pending_metadata_repair"
        or not stats.get("parent_recovery_key")
        or not stats.get("parent_backup_id")
    ):
        return "invalid_repair_audit"
    backup_path = manifest_path.parent / _DB_FILENAME
    if (
        not backup_path.is_file()
        or not manifest.get("backup_sha256")
        or _sha256_file(backup_path) != manifest.get("backup_sha256")
    ):
        return "backup_hash_mismatch"
    return None


def _write_manifest(path: pathlib.Path, manifest: dict) -> None:
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temp_path, path)


def prepare_recovery(store) -> dict:
    """Create a server-owned SQLite backup and immutable recovery manifest."""
    backup_id = uuid.uuid4().hex
    manifest_path = _manifest_path(store, backup_id)
    manifest_path.parent.mkdir(parents=True, exist_ok=False)
    backup_path = manifest_path.parent / _DB_FILENAME

    source = store._connect()
    target = sqlite3.connect(str(backup_path))
    try:
        source.execute("BEGIN")
        source_fingerprint = _compute_source_fingerprint_conn(source)
        source.backup(target)
        target.commit()
        source.commit()
    finally:
        target.close()
        source.close()

    backup_sha256 = _sha256_file(backup_path)
    recovery_key = hashlib.sha256(
        f"{source_fingerprint}|{ROUGH_RUN_ID}|{FINE_RUN_ID}".encode()
    ).hexdigest()
    manifest = {
        "backup_id": backup_id,
        "backup_sha256": backup_sha256,
        "source_fingerprint": source_fingerprint,
        "recovery_key": recovery_key,
        "source_db": str(pathlib.Path(store.db_path).resolve()),
        "status": "prepared",
        "created_at": _now(),
    }
    _write_manifest(manifest_path, manifest)
    return {**manifest, "backup_path": str(backup_path)}


def _insert_pending(conn, *, run_id: str, job_id: str, failure_stage: str,
                    failed_code: str | None, origin_zone: str,
                    ai_payload: dict | None = None) -> None:
    timestamp = _now()
    conn.execute(
        "INSERT INTO screening_pending_results "
        "(id, run_id, platform_job_id, failure_stage, retryable, attempts, last_failed_at, "
        "origin_zone, ai_payload_json, created_at, failed_code) "
        "VALUES (?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?) "
        "ON CONFLICT(run_id, platform_job_id) DO UPDATE SET "
        " failure_stage = excluded.failure_stage, retryable = 1, "
        " last_failed_at = excluded.last_failed_at, origin_zone = excluded.origin_zone, "
        " ai_payload_json = excluded.ai_payload_json, failed_code = excluded.failed_code",
        (
            uuid.uuid4().hex[:16], run_id, job_id, failure_stage, timestamp,
            origin_zone, json.dumps(ai_payload or {}, ensure_ascii=False), timestamp,
            failed_code,
        ),
    )


def _apply_action_1(conn) -> int:
    rows = conn.execute(
        "SELECT platform_job_id, verdict FROM screening_results "
        "WHERE run_id = ? AND verdict LIKE '{%'", (ROUGH_RUN_ID,),
    ).fetchall()
    changed = 0
    for row in rows:
        value = json.loads(row["verdict"])
        inner = value.get("verdict") if isinstance(value, dict) else None
        if inner not in ("match", "not_match"):
            continue
        cursor = conn.execute(
            "UPDATE screening_results SET verdict = ?, verdict_reason = ?, caveats_json = ? "
            "WHERE run_id = ? AND platform_job_id = ? AND verdict LIKE '{%'",
            (
                inner, str(value.get("reason", "")),
                json.dumps(value.get("caveats", []), ensure_ascii=False),
                ROUGH_RUN_ID, row["platform_job_id"],
            ),
        )
        changed += cursor.rowcount
    return changed


def _apply_action_2(conn) -> int:
    rows = conn.execute(
        _SQL_VERDICTS_BY_RUN,
        (FINE_RUN_ID,),
    ).fetchall()
    changed = 0
    for row in rows:
        try:
            value = json.loads(row["verdict"])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict) and value.get("verdict") == "uncertain":
            _insert_pending(
                conn, run_id=FINE_RUN_ID, job_id=row["platform_job_id"],
                failure_stage="ai_fine", failed_code="ai_missing_job",
                origin_zone="match", ai_payload=value,
            )
            changed += 1
    return changed


def _apply_action_4(conn) -> int:
    rows = conn.execute(
        _SQL_VERDICT_JOB_IDS +
        "WHERE run_id = ? AND verdict = 'uncertain' "
        "AND platform_job_id NOT IN (SELECT platform_job_id FROM screening_results WHERE run_id = ?)",
        (ROUGH_RUN_ID, FINE_RUN_ID),
    ).fetchall()
    for row in rows:
        _insert_pending(
            conn, run_id=ROUGH_RUN_ID, job_id=row["platform_job_id"],
            failure_stage="jd_detail",
            failed_code="historical_reason_unavailable",
            origin_zone="kept",
            ai_payload=_historical_pending_payload(),
        )
    return len(rows)


def _historical_pending_payload() -> dict:
    return {
        "reason": "旧流程未保存该岗位停在详情无效、验证码失败或未开始中的哪一种",
        "next_action": "recrawl_jd",
        "evidence": "岗位通过粗筛，但没有进入精筛，且历史记录没有失败码",
    }


def _recovery_integrity_snapshot(conn) -> dict:
    rows = [list(row) for row in conn.execute(
        "SELECT run_id, platform_job_id, COALESCE(jd, '') FROM screening_results "
        "WHERE run_id IN (?, ?) ORDER BY run_id, platform_job_id",
        (ROUGH_RUN_ID, FINE_RUN_ID),
    ).fetchall()]
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    rough_summary = _query_run_summary(conn, ROUGH_RUN_ID)
    normalized_counts = dict(rough_summary["plain_verdicts"])
    for verdict, count in rough_summary["inner_verdicts"].items():
        normalized_counts[verdict] = normalized_counts.get(verdict, 0) + count
    return {
        "result_count": len(rows),
        "job_jd_sha256": hashlib.sha256(encoded).hexdigest(),
        "rough_normalized_verdict_counts": normalized_counts,
    }


def _expected_fine_pending_ids(conn) -> set[str]:
    pending_ids = set()
    rows = conn.execute(
        _SQL_VERDICTS_BY_RUN,
        (FINE_RUN_ID,),
    ).fetchall()
    for row in rows:
        try:
            value = json.loads(row["verdict"])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict) and value.get("verdict") == "uncertain":
            pending_ids.add(row["platform_job_id"])
    return pending_ids


def _check_post_recovery_gate(conn, before: dict, stats: dict) -> dict:
    after = _recovery_integrity_snapshot(conn)
    expected_rough = {
        row["platform_job_id"] for row in conn.execute(
            _SQL_VERDICT_JOB_IDS +
            "WHERE run_id = ? AND verdict = 'uncertain' "
            "AND platform_job_id NOT IN (SELECT platform_job_id FROM screening_results WHERE run_id = ?)",
            (ROUGH_RUN_ID, FINE_RUN_ID),
        ).fetchall()
    }
    expected_fine = _expected_fine_pending_ids(conn)
    actual = {}
    rough_metadata_exact = True
    for run_id in (ROUGH_RUN_ID, FINE_RUN_ID):
        rows = conn.execute(
            "SELECT platform_job_id, failed_code, ai_payload_json "
            "FROM screening_pending_results WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        actual[run_id] = {row["platform_job_id"] for row in rows}
        if run_id == ROUGH_RUN_ID:
            expected_payload = _historical_pending_payload()
            for row in rows:
                try:
                    payload = json.loads(row["ai_payload_json"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    payload = None
                if (
                    row["failed_code"] != "historical_reason_unavailable"
                    or payload != expected_payload
                ):
                    rough_metadata_exact = False

    duplicate_count = conn.execute(
        "SELECT COUNT(*) AS n FROM ("
        " SELECT run_id, platform_job_id FROM screening_results "
        " WHERE run_id IN (?, ?) GROUP BY run_id, platform_job_id HAVING COUNT(*) > 1"
        ")", (ROUGH_RUN_ID, FINE_RUN_ID),
    ).fetchone()["n"]
    invalid_rough = conn.execute(
        "SELECT COUNT(*) AS n FROM screening_results WHERE run_id = ? "
        "AND verdict NOT IN ('match', 'not_match', 'uncertain', 'dropped')",
        (ROUGH_RUN_ID,),
    ).fetchone()["n"]
    rough_json = conn.execute(
        "SELECT COUNT(*) AS n FROM screening_results WHERE run_id = ? AND verdict LIKE '{%'",
        (ROUGH_RUN_ID,),
    ).fetchone()["n"]
    rough_after = _query_run_summary(conn, ROUGH_RUN_ID)
    stored_counts = {
        row["id"]: int(row["pending_count"] or 0)
        for row in conn.execute(
            "SELECT id, pending_count FROM screening_runs WHERE id IN (?, ?)",
            (ROUGH_RUN_ID, FINE_RUN_ID),
        ).fetchall()
    }
    checks = {
        "result_count_conserved": before["result_count"] == after["result_count"],
        "job_ids_and_jd_conserved": before["job_jd_sha256"] == after["job_jd_sha256"],
        "no_duplicate_results": int(duplicate_count or 0) == 0,
        "rough_verdicts_valid": int(invalid_rough or 0) == 0 and int(rough_json or 0) == 0,
        "rough_verdict_distribution_exact": (
            not rough_after["inner_verdicts"]
            and rough_after["plain_verdicts"]
            == before["rough_normalized_verdict_counts"]
        ),
        "rough_pending_exact": actual[ROUGH_RUN_ID] == expected_rough,
        "fine_pending_exact": actual[FINE_RUN_ID] == expected_fine,
        "rough_pending_metadata_exact": rough_metadata_exact,
        "stored_pending_counts_match": (
            stored_counts.get(ROUGH_RUN_ID) == len(actual[ROUGH_RUN_ID])
            and stored_counts.get(FINE_RUN_ID) == len(actual[FINE_RUN_ID])
        ),
        "action_counts_match": stats == {
            "action_1_rough_50_json_unified": ROUGH_50_JSON_MATCH + ROUGH_50_JSON_NOT_MATCH,
            "action_2_fine_50_marked": FINE_50_UNCERTAIN,
            "action_3_jd_762_protected": 762,
            "action_4_pending_646_written": PENDING_646,
        },
    }
    return {
        "all_passed": all(checks.values()),
        "checks": checks,
        "rough_pending": len(actual[ROUGH_RUN_ID]),
        "fine_pending": len(actual[FINE_RUN_ID]),
    }


def _write_failure_audit(store, *, recovery_key: str, backup_id: str,
                         error: str) -> None:
    timestamp = _now()
    with store._connection() as conn:
        conn.execute(
            "INSERT INTO recovery_audit "
            "(id, recovery_key, backup_id, status, tx_committed, error, "
            "stats_json, started_at, finished_at) VALUES (?, ?, ?, 'failed', 0, ?, '{}', ?, ?) "
            "ON CONFLICT(recovery_key) DO UPDATE SET "
            " backup_id = excluded.backup_id, status = 'failed', tx_committed = 0, "
            " error = excluded.error, finished_at = excluded.finished_at",
            (uuid.uuid4().hex, recovery_key, backup_id, error, timestamp, timestamp),
        )


def execute_recovery(backup_id: str, *, store) -> dict:
    """Execute the gated recovery once for a verified server-owned backup."""
    try:
        manifest_path = _manifest_path(store, str(backup_id))
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "written": False}
    if not manifest_path.exists():
        return {"ok": False, "error": "unknown_backup_id", "written": False}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"ok": False, "error": "invalid_manifest", "written": False}
    if manifest.get("backup_id") != backup_id:
        return {"ok": False, "error": "backup_id_mismatch", "written": False}

    recovery_key = str(manifest.get("recovery_key") or "")
    source_fingerprint = str(manifest.get("source_fingerprint") or "")
    expected_recovery_key = hashlib.sha256(
        f"{source_fingerprint}|{ROUGH_RUN_ID}|{FINE_RUN_ID}".encode()
    ).hexdigest()
    if not source_fingerprint or recovery_key != expected_recovery_key:
        return {"ok": False, "error": "invalid_manifest", "written": False}

    # 即使 recovery_key 已提交，幂等返回也必须先复核本次 backup_id
    # 指向的服务端 manifest 和数据库备份；不能只信一条 audit 行。
    backup_path = manifest_path.parent / _DB_FILENAME
    if not backup_path.exists() or _sha256_file(backup_path) != manifest.get("backup_sha256"):
        return {"ok": False, "error": "backup_hash_mismatch", "written": False}

    with store._connection() as conn:
        committed = conn.execute(
            "SELECT * FROM recovery_audit "
            "WHERE recovery_key = ? AND status = 'committed' AND tx_committed = 1",
            (recovery_key,),
        ).fetchone()
    if committed is not None:
        if not _is_verified_committed_recovery_audit(store, committed):
            return {
                "ok": False,
                "error": "committed_recovery_invalid",
                "written": False,
            }
        return {
            "ok": True, "tx_committed": True, "already_recovered": True,
            "backup_id": backup_id,
        }

    owner_token = uuid.uuid4().hex
    try:
        store.acquire_recovery_lock(owner_token=owner_token, maintenance=True)
    except (RuntimeError, TimeoutError) as exc:
        return {"ok": False, "error": str(exc), "written": False}

    conn = None
    try:
        try:
            if _compute_source_fingerprint(store) != manifest.get("source_fingerprint"):
                return {"ok": False, "error": "source_fingerprint_mismatch", "written": False}

            conn = store._connect()
            conn.execute("BEGIN IMMEDIATE")
            timestamp = _now()
            conn.execute(
                "INSERT INTO recovery_audit "
                "(id, recovery_key, backup_id, status, tx_committed, error, "
                "stats_json, started_at) VALUES (?, ?, ?, 'running', 0, NULL, '{}', ?) "
                "ON CONFLICT(recovery_key) DO UPDATE SET "
                " backup_id = excluded.backup_id, status = 'running', tx_committed = 0, "
                " error = NULL, stats_json = '{}', started_at = excluded.started_at, "
                " finished_at = NULL",
                (uuid.uuid4().hex, recovery_key, backup_id, timestamp),
            )

            preview = _preview_from_conn(conn, ROUGH_RUN_ID, FINE_RUN_ID)
            if not preview["gate_passed"]["all_passed"]:
                raise RuntimeError("gate_not_passed")
            before = _recovery_integrity_snapshot(conn)

            stats = {
                "action_1_rough_50_json_unified": _apply_action_1(conn),
                "action_2_fine_50_marked": _apply_action_2(conn),
            }
            protection = _check_762_jd_protection(conn, ROUGH_RUN_ID)
            if not protection["jd_protected"]:
                raise RuntimeError("jd_762_protection_failed")
            stats["action_3_jd_762_protected"] = protection["jd_exists"]
            stats["action_4_pending_646_written"] = _apply_action_4(conn)
            for run_id in (ROUGH_RUN_ID, FINE_RUN_ID):
                conn.execute(
                    "UPDATE screening_runs SET pending_count = "
                    "(SELECT COUNT(*) FROM screening_pending_results WHERE run_id = ?) "
                    "WHERE id = ?", (run_id, run_id),
                )

            post_gate = _check_post_recovery_gate(conn, before, stats)
            if not post_gate["all_passed"]:
                failed = [
                    name for name, passed in post_gate["checks"].items() if not passed
                ]
                raise RuntimeError(
                    "post_recovery_gate_failed:" + ",".join(failed)
                )

            finished_at = _now()
            conn.execute(
                "UPDATE recovery_audit SET status = 'committed', tx_committed = 1, "
                "stats_json = ?, finished_at = ? WHERE recovery_key = ?",
                (json.dumps(stats, ensure_ascii=False), finished_at, recovery_key),
            )
            conn.commit()
        except (
            OSError, sqlite3.Error, RuntimeError, ValueError, KeyError, TypeError,
        ) as exc:
            if conn is not None:
                conn.rollback()
            error = f"{type(exc).__name__}: {exc}"
            _write_failure_audit(
                store, recovery_key=recovery_key, backup_id=backup_id, error=error
            )
            manifest["status"] = "failed"
            manifest["error"] = error
            _write_manifest(manifest_path, manifest)
            return {
                "ok": False, "tx_committed": False, "already_recovered": False,
                "written": False, "backup_id": backup_id, "error": error,
            }
        finally:
            if conn is not None:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

        result = {
            "ok": True, "tx_committed": True, "already_recovered": False,
            "written": True, "backup_id": backup_id, "post_recovery_gate": post_gate,
            **stats,
        }
        warnings = []
        manifest["status"] = "committed"
        manifest["finished_at"] = finished_at
        manifest.pop("error", None)
        try:
            _write_manifest(manifest_path, manifest)
        except OSError as exc:
            warnings.append(f"manifest_update_failed:{type(exc).__name__}")
        try:
            result["post_recovery"] = preview_recovery(store)
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            warnings.append(f"post_recovery_diagnostic_failed:{type(exc).__name__}")
        if warnings:
            result["post_commit_warnings"] = warnings
        return result
    finally:
        store.release_recovery_lock(owner_token=owner_token)


def repair_committed_pending_metadata(backup_id: str, *, store) -> dict:
    """Repair legacy NULL pending reasons after the main recovery committed.

    This is a separate audited transaction because a committed recovery key remains
    immutable and idempotent. It never changes screening results or JD text.
    """
    try:
        manifest_path = _manifest_path(store, str(backup_id))
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "written": False}
    if not manifest_path.exists():
        return {"ok": False, "error": "unknown_backup_id", "written": False}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"ok": False, "error": "invalid_manifest", "written": False}
    if manifest.get("backup_id") != backup_id:
        return {"ok": False, "error": "backup_id_mismatch", "written": False}

    recovery_key = str(manifest.get("recovery_key") or "")
    with store._connection() as check_conn:
        committed = check_conn.execute(
            "SELECT recovery_key, backup_id, stats_json FROM recovery_audit "
            "WHERE recovery_key = ? AND status = 'committed' AND tx_committed = 1",
            (recovery_key,),
        ).fetchone()
    if committed is not None:
        integrity_error = _committed_metadata_repair_error(
            manifest_path, manifest, committed
        )
        if integrity_error:
            return {"ok": False, "error": integrity_error, "written": False}
        return {
            "ok": True, "tx_committed": True, "already_repaired": True,
            "written": False, "backup_id": backup_id,
        }

    backup_path = manifest_path.parent / _DB_FILENAME
    if not backup_path.exists() or _sha256_file(backup_path) != manifest.get("backup_sha256"):
        return {"ok": False, "error": "backup_hash_mismatch", "written": False}

    owner_token = uuid.uuid4().hex
    try:
        store.acquire_recovery_lock(owner_token=owner_token, maintenance=True)
    except (RuntimeError, TimeoutError) as exc:
        return {"ok": False, "error": str(exc), "written": False}

    conn = None
    try:
        try:
            if _compute_source_fingerprint(store) != manifest.get("source_fingerprint"):
                return {"ok": False, "error": "source_fingerprint_mismatch", "written": False}
            conn = store._connect()
            conn.execute("BEGIN IMMEDIATE")
            expected_recovery_stats = {
                "action_1_rough_50_json_unified": (
                    ROUGH_50_JSON_MATCH + ROUGH_50_JSON_NOT_MATCH
                ),
                "action_2_fine_50_marked": FINE_50_UNCERTAIN,
                "action_3_jd_762_protected": 762,
                "action_4_pending_646_written": PENDING_646,
            }
            recovery_candidates = []
            for row in conn.execute(
                "SELECT recovery_key, backup_id, stats_json FROM recovery_audit "
                "WHERE status = 'committed' AND tx_committed = 1"
            ).fetchall():
                try:
                    audit_stats = json.loads(row["stats_json"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue
                try:
                    stats_match = all(
                        int(audit_stats.get(key, -1)) == expected
                        for key, expected in expected_recovery_stats.items()
                    )
                except (TypeError, ValueError):
                    stats_match = False
                if stats_match and _is_verified_committed_recovery_audit(store, row):
                    recovery_candidates.append(row)
            if len(recovery_candidates) != 1:
                raise RuntimeError("committed_recovery_required")
            prior_recovery = recovery_candidates[0]

            expected_ids = {
                row["platform_job_id"] for row in conn.execute(
                    _SQL_VERDICT_JOB_IDS +
                    "WHERE run_id = ? AND verdict = 'uncertain' "
                    "AND platform_job_id NOT IN (SELECT platform_job_id FROM screening_results WHERE run_id = ?)",
                    (ROUGH_RUN_ID, FINE_RUN_ID),
                ).fetchall()
            }
            pending_rows = conn.execute(
                "SELECT platform_job_id, failed_code FROM screening_pending_results WHERE run_id = ?",
                (ROUGH_RUN_ID,),
            ).fetchall()
            pending_ids = {row["platform_job_id"] for row in pending_rows}
            allowed_codes = {None, "", "historical_reason_unavailable"}
            if (
                len(expected_ids) != PENDING_646
                or pending_ids != expected_ids
                or any(row["failed_code"] not in allowed_codes for row in pending_rows)
            ):
                raise RuntimeError("pending_metadata_repair_gate_failed")

            before = _recovery_integrity_snapshot(conn)
            payload_json = json.dumps(
                _historical_pending_payload(), ensure_ascii=False
            )
            cursor = conn.execute(
                "UPDATE screening_pending_results SET failed_code = ?, ai_payload_json = ? "
                "WHERE run_id = ? AND (failed_code IS NULL OR failed_code = '')",
                (
                    "historical_reason_unavailable", payload_json, ROUGH_RUN_ID,
                ),
            )
            updated = int(cursor.rowcount or 0)
            after = _recovery_integrity_snapshot(conn)
            if before != after:
                raise RuntimeError("pending_metadata_repair_changed_results")
            remaining = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results "
                "WHERE run_id = ? AND (failed_code IS NULL OR failed_code != ?)",
                (ROUGH_RUN_ID, "historical_reason_unavailable"),
            ).fetchone()["n"]
            if int(remaining or 0) != 0:
                raise RuntimeError("pending_metadata_repair_incomplete")

            finished_at = _now()
            stats = {
                "operation": "pending_metadata_repair",
                "updated": updated,
                "verified": len(pending_ids),
                "parent_recovery_key": prior_recovery["recovery_key"],
                "parent_backup_id": prior_recovery["backup_id"],
            }
            conn.execute(
                "INSERT INTO recovery_audit "
                "(id, recovery_key, backup_id, status, tx_committed, error, "
                "stats_json, started_at, finished_at) "
                "VALUES (?, ?, ?, 'committed', 1, NULL, ?, ?, ?)",
                (
                    uuid.uuid4().hex, recovery_key, backup_id,
                    json.dumps(stats, ensure_ascii=False), finished_at, finished_at,
                ),
            )
            conn.commit()
        except (
            OSError, sqlite3.Error, RuntimeError, ValueError, KeyError, TypeError,
        ) as exc:
            if conn is not None:
                conn.rollback()
            error = (
                "committed_recovery_required"
                if isinstance(exc, RuntimeError)
                and str(exc) == "committed_recovery_required"
                else f"{type(exc).__name__}: {exc}"
            )
            _write_failure_audit(
                store, recovery_key=recovery_key, backup_id=backup_id, error=error
            )
            manifest["operation"] = "pending_metadata_repair"
            manifest["status"] = "failed"
            manifest["error"] = error
            _write_manifest(manifest_path, manifest)
            return {
                "ok": False, "tx_committed": False, "written": False,
                "backup_id": backup_id, "error": error,
            }
        finally:
            if conn is not None:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

        result = {
            "ok": True, "tx_committed": True, "written": updated > 0,
            "already_repaired": False, "backup_id": backup_id,
            "updated": updated, "verified": PENDING_646,
        }
        manifest["operation"] = "pending_metadata_repair"
        manifest["status"] = "committed"
        manifest["finished_at"] = finished_at
        manifest.pop("error", None)
        try:
            _write_manifest(manifest_path, manifest)
        except (OSError, TypeError, ValueError) as exc:
            result["post_commit_warnings"] = [
                f"manifest_update_failed:{type(exc).__name__}"
            ]
        return result
    finally:
        store.release_recovery_lock(owner_token=owner_token)


# ---------------------------------------------------------------------------
# 031 B7：手动运维 CLI（替代原 /api/recovery/* 三条生产路由）
# ---------------------------------------------------------------------------

# execute/prepare 失败时归入「数据校验失败（退出码 3）」的错误码；
# 其余失败（缺少 --confirm、锁竞争、环境错误等）按参数/校验失败退出码 2。
_DATA_VALIDATION_ERRORS = frozenset({
    "unknown_backup_id", "invalid_backup_id", "backup_id_mismatch",
    "invalid_manifest", "backup_hash_mismatch", "source_fingerprint_mismatch",
    "committed_recovery_invalid",
})
_DATA_VALIDATION_MARKERS = (
    "gate_not_passed", "jd_762_protection_failed", "post_recovery_gate_failed",
)


def _resolve_db_path(explicit: str | None) -> pathlib.Path:
    """解析目标库：--db > CAREER_SCOUT_DB > 状态目录环境变量 > 默认正式库。

    与 ``scripts/db_info.py`` 同一口径，保证"查库"与"修库"指向同一个文件。
    """
    if explicit:
        return pathlib.Path(explicit).expanduser()
    configured = os.environ.get("CAREER_SCOUT_DB")
    if configured:
        return pathlib.Path(configured).expanduser()
    for var in ("CAREER_SCOUT_STATE_DIR", "BOSS_WEBUI_STATE_DIR"):
        value = os.environ.get(var)
        if value:
            path = pathlib.Path(value).expanduser()
            return path if path.name == "webui.db" else path / "webui.db"
    return pathlib.Path.home() / ".career-scout" / "webui" / "webui.db"


def _open_store(db_path: pathlib.Path):
    """按给定库路径打开 TaskStore（延迟 import，仅 CLI 路径需要）。"""
    project_root = pathlib.Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from webui.store import TaskStore
    return TaskStore(str(db_path))


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _cmd_preview(args: argparse.Namespace) -> int:
    """只读预演：输出与迁出前 /api/recovery/preview 的 preview 载荷一致。"""
    store = _open_store(_resolve_db_path(getattr(args, "db", None)))
    try:
        result = preview_recovery(
            store, rough_run_id=ROUGH_RUN_ID, fine_run_id=FINE_RUN_ID,
            result_dir=getattr(args, "result_dir", None),
        )
    except (OSError, sqlite3.Error, RuntimeError, ValueError, KeyError) as exc:
        _emit({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
        return 2
    _emit(result)
    return 0


def _cmd_prepare(args: argparse.Namespace) -> int:
    """生成服务端 SQLite 备份与不可变 manifest，输出 backup_id。"""
    # prepare 不写结果目录；--result-dir 由公共选项提供，此处忽略。
    store = _open_store(_resolve_db_path(getattr(args, "db", None)))
    try:
        prepared = prepare_recovery(store)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        _emit({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
        return 3
    _emit({"ok": True, **prepared})
    return 0


def _cmd_execute(args: argparse.Namespace) -> int:
    """按 backup_id 执行恢复；破坏性，必须显式 --confirm。"""
    if not args.confirm:
        print(
            "错误：execute 会写正式数据库，必须显式追加 --confirm 才会执行。\n"
            "建议先跑 preview 核对门禁，再跑 prepare 生成备份。",
            file=sys.stderr,
        )
        return 2
    store = _open_store(_resolve_db_path(getattr(args, "db", None)))
    result = execute_recovery(str(args.backup_id).strip(), store=store)
    _emit(result)
    if result.get("ok"):
        return 0
    error = str(result.get("error") or "")
    if error in _DATA_VALIDATION_ERRORS or any(
        marker in error for marker in _DATA_VALIDATION_MARKERS
    ):
        return 3
    return 2


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    """挂公共选项；``SUPPRESS`` 保证子命令缺省时不覆盖顶层已解析的值。

    argparse 子 parser 默认值会回写父 namespace（会把 ``--db X preview``
    的 X 冲成 None），用 SUPPRESS 让未出现的选项不落属性，两种书写顺序
    （``--db X preview`` 与 ``preview --db X``）都可用。
    """
    parser.add_argument(
        "--db", default=argparse.SUPPRESS,
        help="SQLite 数据库路径（默认 ~/.career-scout/webui/webui.db）",
    )
    parser.add_argument(
        "--result-dir", default=argparse.SUPPRESS,
        help="结果目录（仅 preview 读取，默认不使用）",
    )


def build_parser() -> argparse.ArgumentParser:
    """构建三子命令 CLI：preview / prepare / execute。"""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.maintenance.historical_recovery",
        description="2026-07-28 事故恢复手动工具（preview/prepare/execute）",
    )
    _add_common_options(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    preview = sub.add_parser("preview", help="只读预演：输出门禁证据，不写库")
    _add_common_options(preview)
    prepare = sub.add_parser(
        "prepare", help="生成服务端备份与 manifest，输出 backup_id",
    )
    _add_common_options(prepare)
    execute = sub.add_parser("execute", help="按 backup_id 执行恢复（破坏性）")
    _add_common_options(execute)
    execute.add_argument("--backup-id", required=True, help="prepare 输出的服务端备份 id")
    execute.add_argument(
        "--confirm", action="store_true",
        help="显式确认执行写库（缺省则拒绝执行）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：0 成功；2 参数/校验失败；3 数据校验失败。

    退出码语义与 contracts/recovery-cli.md 一致。
    """
    args = build_parser().parse_args(argv)
    if args.command == "preview":
        return _cmd_preview(args)
    if args.command == "prepare":
        return _cmd_prepare(args)
    return _cmd_execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
