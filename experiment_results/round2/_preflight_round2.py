"""Round2 preflight: launch CDP Chrome and check BOSS login state.

Usage:
    python _preflight_round2.py

Exit code:
    0 = login OK, can proceed
    1 = login required (user must log in in the browser)
    2 = other failure (CDP didn't come up, etc.)
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from webui import pipeline_exec as pe
from webui.source import BossCdpSource


def main() -> int:
    print("[preflight] launching CDP Chrome...")
    ok, err = pe.ensure_chrome_ready()
    if not ok:
        print(f"[preflight] FAILED to launch Chrome: {err}")
        return 2
    print("[preflight] CDP Chrome is ready.")

    print("[preflight] checking BOSS login state...")
    source = BossCdpSource(python_executable=sys.executable,
                           artifact_root=str(Path.home() / ".career-scout" / "job-result"))
    outcome = source.preflight()
    print(f"[preflight] preflight result: ok={outcome.ok} code={outcome.failed_code} log={outcome.safe_log}")
    if outcome.ok:
        print("[preflight] LOGIN OK — can proceed with round2 experiment.")
        return 0
    if outcome.failed_code == "source_login_required":
        print("[preflight] LOGIN REQUIRED — user must log in at https://www.zhipin.com/ in the debug Chrome window.")
        return 1
    print(f"[preflight] OTHER FAILURE: {outcome.failed_code} / {outcome.safe_log}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
