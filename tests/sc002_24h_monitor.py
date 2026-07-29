"""Create and audit the real-wall-clock SC-002 paused-task fixture."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from webui.store import TaskStore


RUN_ID = "sc002-paused-24h"
STAGE = "jd_detail"
REQUIRED_SECONDS = 24 * 60 * 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _snapshot(db_path: pathlib.Path) -> dict:
    uri = "file:" + db_path.resolve().as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        run = connection.execute(
            "SELECT id, status, source_count, processed_count, pending_count, "
            "current_stage, error_code, error_reason, updated_at "
            "FROM screening_runs WHERE id = ?",
            (RUN_ID,),
        ).fetchone()
        checkpoint = connection.execute(
            "SELECT stage, completed_keys_json, saved_at "
            "FROM pipeline_checkpoints WHERE run_id = ? AND stage = ?",
            (RUN_ID, STAGE),
        ).fetchone()
        events = connection.execute(
            "SELECT seq, created_at, line FROM task_logs "
            "WHERE task_id = ? ORDER BY seq",
            (RUN_ID,),
        ).fetchall()
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        connection.close()
    return {
        "run": dict(run) if run is not None else None,
        "checkpoint": dict(checkpoint) if checkpoint is not None else None,
        "events": [dict(event) for event in events],
        "quick_check": quick_check,
    }


def _fingerprint(snapshot: dict) -> str:
    payload = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def initialize(state_dir: pathlib.Path) -> dict:
    """Create one isolated paused task and freeze its initial evidence."""
    state_dir.mkdir(parents=True, exist_ok=False)
    db_path = state_dir / "webui.db"
    store = TaskStore(str(db_path))
    store.create_screening_run(
        RUN_ID,
        source_count=1408,
        execution_params={"fixture": "sc002_real_wall_clock"},
        backend_version="010-healthy-pipeline-recovery",
    )
    store.update_screening_run(RUN_ID, status="running")
    store.update_screening_run(
        RUN_ID,
        status="paused",
        processed_count=762,
        pending_count=38,
        current_stage=STAGE,
        error_code="captcha_required",
        error_reason="SC-002 隔离墙钟测试：等待用户手动继续",
    )
    store.save_checkpoint(RUN_ID, STAGE, [f"job-{index}" for index in range(762)])
    store.append_task_event(
        RUN_ID,
        "pause",
        {"stage": STAGE, "code": "captcha_required", "fixture": "sc002"},
    )
    started_at = _utc_now()
    snapshot = _snapshot(db_path)
    manifest = {
        "run_id": RUN_ID,
        "db_path": str(db_path.resolve()),
        "started_at": _iso(started_at),
        "deadline_at": _iso(started_at + timedelta(seconds=REQUIRED_SECONDS)),
        "required_seconds": REQUIRED_SECONDS,
        "baseline_fingerprint": _fingerprint(snapshot),
        "baseline_snapshot": snapshot,
    }
    (state_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (state_dir / "observations.jsonl").touch()
    return manifest


def check(state_dir: pathlib.Path) -> dict:
    """Append one read-only observation and report final eligibility truthfully."""
    manifest = json.loads(
        (state_dir / "manifest.json").read_text(encoding="utf-8")
    )
    db_path = pathlib.Path(manifest["db_path"])
    snapshot = _snapshot(db_path)
    observed_at = _utc_now()
    started_at = datetime.fromisoformat(
        str(manifest["started_at"]).replace("Z", "+00:00")
    )
    elapsed_seconds = max(0.0, (observed_at - started_at).total_seconds())
    fingerprint = _fingerprint(snapshot)
    invariants_ok = (
        fingerprint == manifest["baseline_fingerprint"]
        and snapshot.get("quick_check") == "ok"
        and (snapshot.get("run") or {}).get("status") == "paused"
    )
    observation = {
        "observed_at": _iso(observed_at),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "fingerprint": fingerprint,
        "invariants_ok": invariants_ok,
        "eligible_for_final": (
            elapsed_seconds >= int(manifest["required_seconds"])
            and invariants_ok
        ),
        "status": (snapshot.get("run") or {}).get("status"),
    }
    with (state_dir / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation, ensure_ascii=False) + "\n")
    return observation


def main(argv=None) -> int:
    """Run the init/check command and return nonzero when invariants fail."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "check"))
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args(argv)
    state_dir = pathlib.Path(args.state_dir).resolve()
    result = initialize(state_dir) if args.command == "init" else check(state_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("invariants_ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
