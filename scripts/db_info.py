#!/usr/bin/env python3
"""Show or set the lightweight live/test marker for a Career Scout database.

Use this before querying "the latest run" so the live DB and test DBs are not
confused.  The marker lives in the single-row table ``db_meta``:

    id=1, env=live|test, created_at, updated_at
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sqlite3
from pathlib import Path


def _resolve_db_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    configured = os.environ.get("CAREER_SCOUT_DB")
    if configured:
        return Path(configured).expanduser()
    for var in ("CAREER_SCOUT_STATE_DIR", "BOSS_WEBUI_STATE_DIR"):
        value = os.environ.get(var)
        if value:
            path = Path(value).expanduser()
            return path if path.name == "webui.db" else path / "webui.db"
    return Path.home() / ".career-scout" / "webui" / "webui.db"


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _set_marker(path: Path, env: str) -> None:
    connection = sqlite3.connect(str(path), timeout=10)
    try:
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS db_meta ("
            " id INTEGER PRIMARY KEY CHECK (id = 1),"
            " env TEXT NOT NULL,"
            " created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)"
        )
        now = _now()
        connection.execute(
            "INSERT INTO db_meta (id, env, created_at, updated_at) "
            "VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET env = excluded.env, "
            " updated_at = excluded.updated_at",
            (env, now, now),
        )
        connection.commit()
    finally:
        connection.close()


def _read_info(path: Path) -> dict:
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        meta = None
        try:
            row = connection.execute(
                "SELECT env, created_at, updated_at FROM db_meta WHERE id = 1"
            ).fetchone()
            meta = dict(row) if row is not None else None
        except sqlite3.OperationalError:
            pass
        latest = None
        try:
            row = connection.execute(
                "SELECT id, status, started_at, finished_at, source_count,"
                " processed_count, match_count, mismatch_count, pending_count"
                " FROM screening_runs"
                " ORDER BY COALESCE(finished_at, created_at) DESC LIMIT 1"
            ).fetchone()
            latest = dict(row) if row is not None else None
        except sqlite3.OperationalError:
            pass
        return {
            "db_path": str(path.resolve()),
            "db_meta": meta,
            "latest_screening_run": latest,
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="SQLite database path")
    parser.add_argument("--set-env", choices=("live", "test"), dest="env")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    db_path = _resolve_db_path(args.db)
    if args.env:
        _set_marker(db_path, args.env)

    info = _read_info(db_path)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    print(f"db_path: {info['db_path']}")
    meta = info["db_meta"]
    if meta:
        print(
            f"db_meta: env={meta['env']} created_at={meta['created_at']} "
            f"updated_at={meta['updated_at']}"
        )
    else:
        print("db_meta: missing")
    run = info["latest_screening_run"]
    if run:
        print(
            f"latest_screening_run: id={run['id']} status={run['status']} "
            f"started_at={run['started_at']} match={run['match_count']} "
            f"mismatch={run['mismatch_count']} pending={run['pending_count']}"
        )
    else:
        print("latest_screening_run: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
