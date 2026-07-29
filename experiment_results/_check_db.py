"""Check DB state for experiment preflight."""
import sqlite3
import json
import os

DB = r"~/.career-scout\webui\webui.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("=== AI settings ===")
row = conn.execute(
    "SELECT endpoint_url, model, status, credential_ref FROM ai_settings WHERE id=1"
).fetchone()
if row:
    print(json.dumps(dict(row), ensure_ascii=False, indent=2))
else:
    print("NONE")

print("\n=== Recent screening_runs (latest 10) ===")
rows = conn.execute(
    "SELECT id, status, record_kind, created_at, updated_at FROM screening_runs "
    "ORDER BY created_at DESC LIMIT 10"
).fetchall()
for r in rows:
    print(dict(r))

print("\n=== Running count ===")
n = conn.execute(
    "SELECT COUNT(*) AS n FROM screening_runs WHERE status='running'"
).fetchone()
print(dict(n))

print("\n=== Screening_runs columns ===")
cols = conn.execute("PRAGMA table_info(screening_runs)").fetchall()
for c in cols:
    print(dict(c))
