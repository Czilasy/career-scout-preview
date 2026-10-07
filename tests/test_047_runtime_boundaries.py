"""047 independent prerequisite: runtime boundary gates.

These tests describe the pre-split compatibility contract plus the structural
gates for the planned extraction (webui/pipeline_search_preflight.py,
store_flow_cancel.py, store_flow_failure.py, flow_track_operations.py,
flow_preflight_recovery.py, flow_errors.py).  Compatibility assertions must
stay green while structure gates go red until the extraction lands.
"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _lines(relative: str) -> int:
    return len((ROOT / relative).read_text(encoding="utf-8").splitlines())


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _module_imports(relative: str) -> set[str]:
    tree = ast.parse(_source(relative))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class PreSplitCompatibilityTests(unittest.TestCase):
    """Public imports, patch surfaces and mixin assembly that must survive."""

    def test_pipeline_search_helpers_remain_importable(self):
        from webui.pipeline_exec_search import (
            _canonical_source_code,
            _is_source_hard_stop,
            _record_source_hard_stop_evidence,
            run_search,
        )

        self.assertTrue(callable(run_search))
        self.assertEqual(_canonical_source_code("cdp_unavailable"), "source_cdp_unavailable")
        self.assertTrue(_is_source_hard_stop("source_cdp_unavailable"))
        self.assertTrue(callable(_record_source_hard_stop_evidence))

    def test_pipeline_exec_facade_keeps_patch_surface(self):
        from webui import pipeline_exec

        for name in (
            "ensure_chrome_ready",
            "close_debug_chrome",
            "load_advanced_settings",
            "set_active_cdp_data_dir",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(pipeline_exec, name), name)

    def test_store_mixin_keeps_atomic_entrypoints(self):
        from webui.store import TaskStore
        from webui.store_flow import StoreFlowMixin
        from webui.store_flow_state import StoreFlowStateMixin

        for name in (
            "claim_flow_finish",
            "restore_flow_finish_claim",
            "discard_unbound_result_snapshot",
            "cancel_task_atomic",
            "finish_flow_task_atomic",
            "close_flow_task_state_atomic",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(StoreFlowStateMixin, name), name)
                self.assertTrue(hasattr(TaskStore, name), name)
        self.assertTrue(issubclass(StoreFlowMixin, StoreFlowStateMixin))

    def test_flow_service_public_exports_survive(self):
        from webui.flow_service import (
            FLOW_ERROR_MESSAGES,
            FlowResumeError,
            FlowService,
            PlatformUnavailableError,
            public_flow_message,
            submit_platform_task,
        )

        self.assertTrue(FLOW_ERROR_MESSAGES)
        self.assertTrue(callable(public_flow_message))
        self.assertTrue(callable(submit_platform_task))
        self.assertTrue(issubclass(FlowResumeError, Exception))
        self.assertTrue(issubclass(PlatformUnavailableError, ValueError))
        self.assertTrue(callable(getattr(FlowService, "operate_track")))

    def test_submission_service_public_methods_survive(self):
        from webui.flow_submission_service import FlowSubmissionService

        for name in (
            "resume_preflight_track",
            "claim_track",
            "create_scrape_records",
            "submit_scrape",
            "fail",
            "mark_executor_failure",
            "mark_whitebox_failure",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(FlowSubmissionService, name), name)


class RuntimeBoundaryStructureTests(unittest.TestCase):
    """Independent structural gates for the 047 prerequisite extraction."""

    NEW_MODULES = (
        "webui/pipeline_search_preflight.py",
        "webui/store_flow_cancel.py",
        "webui/store_flow_failure.py",
        "webui/flow_track_operations.py",
        "webui/flow_preflight_recovery.py",
        "webui/flow_errors.py",
    )

    def test_new_domain_modules_exist(self):
        for relative in self.NEW_MODULES:
            with self.subTest(relative=relative):
                self.assertTrue(
                    (ROOT / relative).is_file(),
                    f"missing extraction target {relative}",
                )

    def test_facades_shrink_below_boundaries(self):
        for relative, limit in (
            ("webui/pipeline_exec_search.py", 800),
            ("webui/store_flow_state.py", 600),
            ("webui/flow_service.py", 600),
            ("webui/flow_submission_service.py", 600),
        ):
            with self.subTest(relative=relative):
                self.assertLess(
                    _lines(relative), limit,
                    f"{relative} still above the split boundary",
                )

    def test_new_domain_modules_stay_below_six_hundred(self):
        for relative in self.NEW_MODULES:
            with self.subTest(relative=relative):
                if not (ROOT / relative).is_file():
                    self.skipTest(f"{relative} not created yet")
                self.assertLess(
                    _lines(relative), 600,
                    f"{relative} exceeds the new-domain boundary",
                )

    def test_search_failure_helpers_are_not_duplicated(self):
        search = _source("webui/pipeline_exec_search.py")
        for helper in (
            "def _canonical_source_code",
            "def _is_source_hard_stop",
            "def _record_source_hard_stop_evidence",
        ):
            with self.subTest(helper=helper):
                self.assertNotIn(
                    helper, search,
                    "helpers must be re-exported from webui.pipeline_search_preflight",
                )
        preflight = "webui/pipeline_search_preflight.py"
        if (ROOT / preflight).is_file():
            self.assertIn("def run_source_preflight", _source(preflight))

    def test_store_state_mixins_compose_extracted_domains(self):
        state = _source("webui/store_flow_state.py")
        for marker in (
            "from webui.store_flow_cancel import",
            "from webui.store_flow_failure import",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, state)
        for duplicate in (
            "def cancel_task_atomic",
            "def close_flow_task_state_atomic",
            "def _closure_run_status",
        ):
            with self.subTest(duplicate=duplicate):
                self.assertNotIn(
                    duplicate, state,
                    "atomic implementations must live in the extracted mixins",
                )

    def test_service_extractions_keep_thin_composition(self):
        service = _source("webui/flow_service.py")
        submission = _source("webui/flow_submission_service.py")
        # Plan T006: FlowService.operate_track stays as a thin delegate.
        self.assertIn("def operate_track", service)
        self.assertIn("from webui.flow_track_operations import operate_track", service)
        for heavy in (
            "stop_without_run",
            "assert_action_target(track",
            "resolve_flow_binding",
        ):
            with self.subTest(heavy=heavy):
                self.assertNotIn(
                    heavy, service,
                    "track action orchestration must live in flow_track_operations",
                )
        for moved in (
            "def _resume_scope",
            "def _resume_execution_config",
            "def _resume_login_space",
            "def _check_resume_block",
            "def _pause_preflight_resume",
            "def resume_preflight_track",
        ):
            with self.subTest(moved=moved):
                self.assertNotIn(moved, submission)
        self.assertIn("from webui.flow_preflight_recovery import", submission)

    def test_extracted_modules_keep_one_way_imports(self):
        store_modules = ("webui/store_flow_cancel.py", "webui/store_flow_failure.py")
        for relative in store_modules:
            if not (ROOT / relative).is_file():
                continue
            imports = _module_imports(relative)
            for forbidden in ("webui.flow_service", "webui.app", "webui.flow_submission_service"):
                with self.subTest(relative=relative, forbidden=forbidden):
                    self.assertNotIn(forbidden, imports)
        for relative in ("webui/flow_preflight_recovery.py", "webui/flow_errors.py"):
            if not (ROOT / relative).is_file():
                continue
            imports = _module_imports(relative)
            for forbidden in ("webui.app", "webui.flow_service"):
                with self.subTest(relative=relative, forbidden=forbidden):
                    self.assertNotIn(forbidden, imports)
        preflight = "webui/pipeline_search_preflight.py"
        if (ROOT / preflight).is_file():
            imports = _module_imports(preflight)
            for forbidden in ("webui.app", "webui.pipeline_exec"):
                with self.subTest(forbidden=forbidden):
                    self.assertNotIn(forbidden, imports)

    # ------------------------------------------------------------------
    # T053 最终审计：047 产品改动落点、行数上限与单向引用。
    # ------------------------------------------------------------------
    PRODUCT_MODULES = (
        "webui/pipeline_search_preflight.py",
        "webui/store_flow_cancel.py",
        "webui/store_flow_failure.py",
        "webui/flow_track_operations.py",
        "webui/flow_preflight_recovery.py",
        "webui/flow_errors.py",
        "webui/flow_run_lifecycle.py",
        "webui/store_run_lifecycle.py",
        "webui/store_flow_retry.py",
        "webui/flow_track_recovery.py",
        "webui/store_whitebox_lifecycle.py",
        "webui/task_state_lifecycle.py",
        "webui/store_history_lifecycle.py",
        "webui/src/composables/useFlowOperationEpoch.ts",
        "webui/src/composables/useExecutionPanelCollapse.ts",
    )

    def test_product_modules_exist_and_stay_within_python_limit(self):
        for relative in self.PRODUCT_MODULES:
            with self.subTest(relative=relative):
                target = ROOT / relative
                self.assertTrue(target.is_file(), f"missing {relative}")
                lines = len(target.read_text(encoding="utf-8").splitlines())
                if relative.endswith(".ts"):
                    self.assertLess(lines, 1200, f"{relative} above Vue/TS limit")
                else:
                    self.assertLessEqual(lines, 800, f"{relative} above Python limit")

    def test_untouched_boundary_files_have_no_047_business(self):
        protected = (
            "webui/app.py",
            "webui/task_continue_api.py",
            "scripts/boss_cdp_raw.py",
            "webui/source_zhilian_cdp.py",
        )
        for relative in protected:
            with self.subTest(relative=relative):
                imports = _module_imports(relative)
                for forbidden in (
                    "webui.flow_track_recovery",
                    "webui.store_flow_retry",
                    "webui.store_history_lifecycle",
                    "webui.store_run_lifecycle",
                    "webui.flow_run_lifecycle",
                    "webui.store_whitebox_lifecycle",
                    "webui.task_state_lifecycle",
                    "webui.useExecutionPanelCollapse",
                ):
                    self.assertNotIn(forbidden, imports)

    def test_new_store_domains_do_not_import_services(self):
        for relative in (
            "webui/store_run_lifecycle.py",
            "webui/store_flow_retry.py",
            "webui/store_whitebox_lifecycle.py",
            "webui/store_history_lifecycle.py",
        ):
            with self.subTest(relative=relative):
                if not (ROOT / relative).is_file():
                    self.skipTest(f"{relative} not created yet")
                imports = _module_imports(relative)
                for forbidden in (
                    "webui.app",
                    "webui.flow_service",
                    "webui.flow_submission_service",
                    "webui.flow_api",
                ):
                    self.assertNotIn(forbidden, imports)

    def test_recovery_domains_do_not_import_http_layer(self):
        for relative in (
            "webui/flow_track_recovery.py",
            "webui/flow_run_lifecycle.py",
            "webui/task_state_lifecycle.py",
        ):
            with self.subTest(relative=relative):
                if not (ROOT / relative).is_file():
                    self.skipTest(f"{relative} not created yet")
                imports = _module_imports(relative)
                for forbidden in ("webui.app", "webui.flow_api"):
                    self.assertNotIn(forbidden, imports)

    def test_panel_collapse_helper_does_not_import_view_or_http(self):
        source = _source("webui/src/composables/useExecutionPanelCollapse.ts")
        self.assertNotIn("DiscoveryView", source)
        self.assertNotIn("../views/", source)
        self.assertNotIn("apiRequest", source)

    def test_flow_errors_shares_exception_identity(self):
        try:
            from webui import flow_errors
        except ModuleNotFoundError:
            self.fail(
                "webui/flow_errors.py missing: shared Flow error definitions "
                "not extracted yet",
            )
        from webui.flow_service import FlowResumeError, PlatformUnavailableError

        self.assertIs(FlowResumeError, flow_errors.FlowResumeError)
        self.assertIs(PlatformUnavailableError, flow_errors.PlatformUnavailableError)


if __name__ == "__main__":
    unittest.main()
