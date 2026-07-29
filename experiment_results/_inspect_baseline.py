"""Inspect latest screening_run to extract profile_summary and screening_fields for experiment baseline."""
import sqlite3
import json

DB = r"~/.career-scout\webui\webui.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Most recent process_log with profile_summary
row = conn.execute(
    "SELECT id, frozen_filters_json, profile_summary, execution_params_json, "
    "search_params_json, source_count, total_kept, total_dropped, match_count "
    "FROM screening_runs WHERE record_kind='process_log' "
    "ORDER BY created_at DESC LIMIT 1"
).fetchone()
if row:
    d = dict(row)
    print("LATEST_RUN:")
    print(json.dumps(d, ensure_ascii=False, indent=2))
else:
    print("NO_PROCESS_LOG_RUN")

# Also check the result_snapshot
print("\n=== Latest result_snapshot ===")
row2 = conn.execute(
    "SELECT id, frozen_filters_json, profile_summary, source_count, total_kept, match_count "
    "FROM screening_runs WHERE record_kind='result_snapshot' "
    "ORDER BY created_at DESC LIMIT 1"
).fetchone()
if row2:
    print(json.dumps(dict(row2), ensure_ascii=False, indent=2))
