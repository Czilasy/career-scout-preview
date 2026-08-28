import importlib.util
import pathlib
import sys
import tempfile
import time
from unittest import mock
from webui.app import create_app


_SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "boss_cdp_raw.py"


_SC015_PATH = pathlib.Path(__file__).resolve().parents[1] / "sc015_viewport_check.py"


def _load_boss_cdp_raw():
    sys.modules.setdefault("websocket", mock.Mock())
    sys.modules.setdefault("requests", mock.Mock())
    spec = importlib.util.spec_from_file_location("boss_cdp_raw_test", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_sc015_viewport_check():
    spec = importlib.util.spec_from_file_location("sc015_viewport_check_test", _SC015_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_app():
    temp = tempfile.TemporaryDirectory()
    root = pathlib.Path(temp.name)
    app = create_app({
        "TESTING": True,
        "START_TASKS": False,
        "RESULT_DIR": str(root / "results"),
        "DB_PATH": str(root / "state" / "webui.db"),
        "PYTHON_EXECUTABLE": sys.executable,
    })
    return app, temp


def _authed_test_client(app):
    """Return a Flask test client pre-authenticated for local API calls."""
    client = app.test_client()
    client.environ_base["HTTP_X_BOSS_TOKEN"] = app.config["API_TOKEN"]
    return client


def _wait_for_pipeline_task(client, task_id, timeout=8.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/search-progress/{task_id}")
        if response.status_code == 200:
            last = response.get_json()
            if last.get("status") not in ("queued", "running"):
                return last
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not stop within {timeout}s; last={last}")


def _pause_run(store, run_id, **fields):
    """Build a valid persisted paused fixture through running first."""
    if store.get_screening_run(run_id)["status"] == "queued":
        store.update_screening_run(run_id, status="running")
    store.update_screening_run(run_id, status="paused", **fields)
