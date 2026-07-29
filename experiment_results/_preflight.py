"""Pre-flight check: imports, credentials, settings write/read."""
import sys
import os
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

print("=== 1. Module imports ===")
try:
    from webui import ai as ai_service
    from webui import pipeline_exec as pe
    from webui.source import BossCdpSource
    from webui.store import TaskStore
    from scripts import boss_cdp_raw as boss
    print("OK: all webui modules imported")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    raise

print("\n=== 2. AI credentials ===")
try:
    import sqlite3, keyring
    DB = Path.home() / ".career-scout" / "webui" / "webui.db"
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT endpoint_url, model, credential_ref FROM ai_settings WHERE id=1").fetchone()
    conn.close()
    print(f"endpoint: {row['endpoint_url']}")
    print(f"model: {row['model']}")
    print(f"cred_ref: {row['credential_ref']}")
    api_key = keyring.get_password("boss-workbench", row["credential_ref"]) or ""
    print(f"api_key length: {len(api_key)} (first 4: {api_key[:4]}...)")
    if not api_key:
        raise RuntimeError("api_key empty")
    print("OK: AI credentials retrievable")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    raise

print("\n=== 3. Settings write/read ===")
try:
    STATE_DIR = Path.home() / ".career-scout" / "webui"
    SETTINGS = STATE_DIR / "advanced_settings.json"
    # Save current
    original = SETTINGS.read_text(encoding="utf-8")
    # Write test
    test_settings = {"pages": 3, "inter_combo_delay": 10.0, "detail_batch_size": 99,
                     "detail_interval": 4, "detail_reset_every": 4, "detail_batch_cooldown": 15,
                     "screen_batch_size": 50, "screen_concurrency": 3,
                     "match_batch_size": 4, "match_concurrency": 6}
    SETTINGS.write_text(json.dumps(test_settings, ensure_ascii=False, indent=2), encoding="utf-8")
    # Read back via pipeline_exec
    s = pe.load_advanced_settings()
    print(f"read back: detail_batch_size={s['detail_batch_size']} match_concurrency={s['match_concurrency']}")
    assert s["detail_batch_size"] == 99, "settings write/read mismatch"
    # Restore
    SETTINGS.write_text(original, encoding="utf-8")
    print("OK: settings write/read works (restored original)")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    raise

print("\n=== 4. CDP port check ===")
import requests
try:
    r = requests.get("http://127.0.0.1:9222/json/version", timeout=3)
    print(f"CDP already running, status={r.status_code}")
except Exception as e:
    print(f"CDP not running (will be auto-launched by ensure_chrome_ready): {type(e).__name__}")

print("\n=== 5. AI endpoint connectivity (lightweight test call) ===")
try:
    # Use ai_service.test_connection if available; else do a minimal call
    if hasattr(ai_service, "test_connection"):
        result = ai_service.test_connection(
            endpoint_url=row["endpoint_url"],
            api_key=api_key,
            model=row["model"],
        )
        print(f"test_connection result: {result}")
    else:
        print("test_connection not available, doing minimal call_ai")
        messages = [{"role": "system", "content": "Return {\"ok\":true}"},
                    {"role": "user", "content": "ping"}]
        data = ai_service.call_ai(row["endpoint_url"], api_key, messages,
                                   model=row["model"], timeout=30)
        print(f"call_ai ok: {data}")
    print("OK: AI endpoint reachable")
except Exception as e:
    print(f"WARN: {type(e).__name__}: {e}")

print("\n=== Preflight complete ===")
