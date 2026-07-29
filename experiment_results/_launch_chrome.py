"""Launch debug Chrome and wait for user to log in to BOSS."""
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from webui.pipeline_exec import ensure_chrome_ready, close_debug_chrome
from webui.source import BossCdpSource
from scripts import boss_cdp_raw as boss

print("=== Launching debug Chrome ===")
ok, err = ensure_chrome_ready()
if not ok:
    print(f"FAIL: {err}")
    sys.exit(1)
print(f"Chrome ready: {ok}")

print("\n=== Checking BOSS login ===")
source = BossCdpSource(python_executable=sys.executable)
pre = source.preflight()
print(f"preflight ok={pre.ok} failed_code={pre.failed_code}")
print(f"safe_log={pre.safe_log}")

if not pre.ok and pre.failed_code == "source_login_required":
    print("\n>>> PLEASE LOG IN TO BOSS in the Chrome window that just opened.")
    print(">>> Navigate to https://www.zhipin.com and log in.")
    print(">>> After login, this script will re-check. Press Ctrl+C to abort.")
    for i in range(60):  # wait up to 10 minutes
        time.sleep(10)
        pre = source.preflight()
        print(f"  [{(i+1)*10}s] preflight ok={pre.ok} failed_code={pre.failed_code}")
        if pre.ok:
            print("\n=== BOSS login confirmed ===")
            print("You can now close this script and re-run the orchestrator.")
            break
    else:
        print("\nTimeout waiting for login.")
else:
    print("\nBOSS already logged in or other state. You can re-run orchestrator.")

# Don't close chrome — leave it for the orchestrator to use
print("\nLeaving Chrome running. Orchestrator will reuse it.")
