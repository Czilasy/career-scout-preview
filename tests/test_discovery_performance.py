"""RED contract tests for feature 005 deterministic performance reporting.

These tests intentionally target the production discovery runner contract.  The
fixture integrity tests are expected to pass in Phase 1; the performance tests
remain RED until the policy-v2 monotonic metrics implementation exists.
"""

from __future__ import annotations

import inspect
import json
import unittest
from pathlib import Path
from typing import Any


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "discovery"
    / "fast_resume_discovery_v2.json"
)


class FakeMonotonicClock:
    """Controllable monotonic clock used only through production injection."""

    def __init__(self, initial: float = 0.0):
        self._value = float(initial)

    def __call__(self) -> float:
        return self._value

    def set(self, value: float) -> None:
        value = float(value)
        if value < self._value:
            raise ValueError("monotonic clock cannot move backwards")
        self._value = value


def _load_fixture() -> dict[str, Any]:
    with FIXTURE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _value_at_path(value: dict[str, Any], dotted_path: str) -> Any:
    current: Any = value
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise AssertionError(f"performance report missing required field: {dotted_path}")
        current = current[part]
    return current


class DiscoveryPerformanceFixtureTests(unittest.TestCase):
    """T006 fixture is deterministic and internally self-consistent."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = _load_fixture()

    def test_fixture_has_exact_pool_duplicate_detail_and_direction_counts(self):
        fixture = self.fixture
        results = fixture["list_results"]
        expected = fixture["expected"]

        self.assertEqual(len(results), 106)
        self.assertEqual(expected["raw_list_result_count"], 106)
        self.assertEqual(len({item["job_id"] for item in results}), 100)
        self.assertEqual(len({item["source_url"] for item in results}), 100)
        self.assertEqual(expected["unique_candidate_count"], 100)

        duplicate_rows = [item for item in results if "duplicate_of" in item]
        self.assertEqual(len(duplicate_rows), 6)
        self.assertEqual(expected["duplicate_result_count"], 6)
        self.assertTrue(all(item["job_id"] == item["duplicate_of"] for item in duplicate_rows))

        details = fixture["accessible_details"]
        self.assertEqual(len(details), 20)
        self.assertEqual(len({item["job_id"] for item in details}), 20)
        self.assertEqual(expected["accessible_detail_count"], 20)

        self.assertEqual(len(fixture["directions"]), 3)
        self.assertEqual(expected["direction_count"], 3)
        self.assertEqual(
            {item["type"] for item in fixture["directions"]},
            {"core", "adjacent", "growth"},
        )

    def test_fixture_covers_salary_and_city_three_state_contracts(self):
        unique_rows: dict[str, dict[str, Any]] = {}
        for item in self.fixture["list_results"]:
            unique_rows.setdefault(item["job_id"], item)

        for field in ("salary", "city"):
            observed = {
                item["expected_hard_rules"][field]
                for item in unique_rows.values()
            }
            self.assertEqual(observed, {"pass", "unknown", "violation"})
            counts = {
                outcome: sum(
                    item["expected_hard_rules"][field] == outcome
                    for item in unique_rows.values()
                )
                for outcome in observed
            }
            self.assertEqual(counts, self.fixture["expected"][f"{field}_outcomes"])

        self.assertEqual(
            {item["expected_precheck"] for item in unique_rows.values()},
            {"pass", "unknown", "violation"},
        )

    def test_accessible_details_reference_unique_nonviolating_candidates(self):
        unique_rows: dict[str, dict[str, Any]] = {}
        for item in self.fixture["list_results"]:
            unique_rows.setdefault(item["job_id"], item)

        for detail in self.fixture["accessible_details"]:
            self.assertTrue(detail["accessible"])
            self.assertEqual(detail["source_status"], "active")
            self.assertIn(detail["job_id"], unique_rows)
            self.assertEqual(detail["source_url"], unique_rows[detail["job_id"]]["source_url"])
            self.assertNotEqual(
                unique_rows[detail["job_id"]]["expected_precheck"],
                "violation",
            )

        covered_directions = {
            direction_id
            for detail in self.fixture["accessible_details"]
            for direction_id in detail["assessment"]["direction_ids"]
        }
        self.assertEqual(
            covered_directions,
            {item["id"] for item in self.fixture["directions"]},
        )


class DiscoveryPerformanceContractTests(unittest.TestCase):
    """T007 RED tests for the production policy-v2 metrics contract."""

    REQUIRED_REPORT_PATHS = (
        "contract_version",
        "status",
        "list.query_count",
        "list.job_count",
        "list.duration_seconds",
        "selection.selected_count",
        "selection.deferred_count",
        "selection.reasons",
        "details.processed_count",
        "details.reused_count",
        "details.failed_count",
        "details.cancelled_count",
        "details.items",
        "details.duration_seconds.p50",
        "details.duration_seconds.p95",
        "details.wait_duration_seconds.p50",
        "details.wait_duration_seconds.p95",
        "details.batch_count",
        "details.peak_concurrency",
        "ai.group_count",
        "ai.call_count",
        "ai.duration_seconds",
        "timing.first_result_seconds",
        "timing.first_five_seconds",
        "timing.all_complete_seconds",
        "resume_count",
        "source_breaker_events",
        "blockers",
        "gates.list_pool_within_90_seconds",
        "gates.first_five_within_300_seconds",
        "gates.all_complete_within_600_seconds",
        "gates.no_external_blocker",
        "gates.overall",
    )

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = _load_fixture()

    def _metrics_type(self):
        from webui import discovery_runner

        metrics_type = getattr(discovery_runner, "DiscoveryPerformanceMetrics", None)
        if metrics_type is None:
            self.fail(
                "feature 005 performance implementation is missing: "
                "webui.discovery_runner.DiscoveryPerformanceMetrics"
            )
        return metrics_type

    def _new_metrics(self, clock: FakeMonotonicClock):
        metrics_type = self._metrics_type()
        metrics = metrics_type(monotonic_clock=clock)
        metrics.start()
        return metrics

    def _record_healthy_fixture(self, metrics, clock: FakeMonotonicClock) -> dict[str, Any]:
        scenario = self.fixture["performance_scenarios"]["healthy_boundary"]
        clock.set(scenario["list_complete_seconds"])
        metrics.mark_list_completed(query_count=3, job_count=100)
        metrics.record_selection(
            selected_count=15,
            deferred_count=85,
            reasons={"budget_deferred": 85},
        )

        for detail in self.fixture["accessible_details"][:15]:
            timing = detail["timing"]
            metrics.record_detail_completed(
                job_id=detail["job_id"],
                total_seconds=timing["total_seconds"],
                wait_seconds=timing["wait_seconds"],
                wait_reason="readiness",
                batch=timing["batch"],
                concurrency=timing["concurrency"],
                reused=False,
            )
            assessment = detail["assessment"]
            metrics.record_ai_group_completed(
                job_id=detail["job_id"],
                direction_count=len(assessment["direction_ids"]),
                call_count=assessment["ai_calls"],
                duration_seconds=assessment["duration_seconds"],
            )

        for job_id, visible_at in zip(
            [item["job_id"] for item in self.fixture["accessible_details"][:5]],
            scenario["result_visible_seconds"],
            strict=True,
        ):
            clock.set(visible_at)
            metrics.record_result_visible(job_id=job_id)

        clock.set(scenario["all_complete_seconds"])
        metrics.mark_all_complete()
        return metrics.build_report()

    def test_discovery_runner_accepts_injected_monotonic_clock(self):
        from webui.discovery_runner import DiscoveryRunner

        parameters = inspect.signature(DiscoveryRunner.__init__).parameters
        self.assertIn(
            "monotonic_clock",
            parameters,
            "feature 005 runner must inject a monotonic clock; wall-clock timing "
            "cannot drive deterministic performance gates",
        )

    def test_performance_report_contains_every_required_field(self):
        scenario = self.fixture["performance_scenarios"]["healthy_boundary"]
        clock = FakeMonotonicClock(scenario["start_seconds"])
        report = self._record_healthy_fixture(self._new_metrics(clock), clock)

        for dotted_path in self.REQUIRED_REPORT_PATHS:
            with self.subTest(field=dotted_path):
                _value_at_path(report, dotted_path)

        details = report["details"]["items"]
        self.assertEqual(len(details), 15)
        for item in details:
            self.assertTrue(
                {
                    "job_id",
                    "total_seconds",
                    "wait_seconds",
                    "wait_reason",
                    "batch",
                    "concurrency",
                }.issubset(item),
                item,
            )

    def test_first_result_first_five_and_all_complete_are_inclusive_boundaries(self):
        scenario = self.fixture["performance_scenarios"]["healthy_boundary"]
        clock = FakeMonotonicClock(scenario["start_seconds"])
        report = self._record_healthy_fixture(self._new_metrics(clock), clock)

        self.assertEqual(report["timing"]["first_result_seconds"], 120.0)
        self.assertEqual(report["timing"]["first_five_seconds"], 300.0)
        self.assertEqual(report["timing"]["all_complete_seconds"], 600.0)
        self.assertTrue(report["gates"]["list_pool_within_90_seconds"])
        self.assertTrue(report["gates"]["first_five_within_300_seconds"])
        self.assertTrue(report["gates"]["all_complete_within_600_seconds"])
        self.assertTrue(report["gates"]["overall"])

    def test_performance_time_gates_fail_immediately_above_boundaries(self):
        clock = FakeMonotonicClock(0.0)
        metrics = self._new_metrics(clock)

        clock.set(90.001)
        metrics.mark_list_completed(query_count=3, job_count=100)
        for index in range(5):
            clock.set(100.0 if index == 0 else 300.001)
            metrics.record_result_visible(job_id=f"job-{index + 1:03d}")
        clock.set(600.001)
        metrics.mark_all_complete()
        report = metrics.build_report()

        self.assertEqual(report["timing"]["first_result_seconds"], 100.0)
        self.assertFalse(report["gates"]["list_pool_within_90_seconds"])
        self.assertFalse(report["gates"]["first_five_within_300_seconds"])
        self.assertFalse(report["gates"]["all_complete_within_600_seconds"])
        self.assertFalse(report["gates"]["overall"])

    def test_external_blocker_is_reported_and_cannot_be_counted_as_pass(self):
        scenario = self.fixture["performance_scenarios"]["externally_blocked"]
        clock = FakeMonotonicClock(scenario["start_seconds"])
        metrics = self._new_metrics(clock)

        clock.set(scenario["list_complete_seconds"])
        metrics.mark_list_completed(query_count=3, job_count=100)
        clock.set(scenario["result_visible_seconds"][0])
        metrics.record_result_visible(job_id="job-001")
        blocker = scenario["blockers"][0]
        metrics.record_blocker(
            code=blocker["code"],
            stage=blocker["stage"],
            external=blocker["external"],
        )
        report = metrics.build_report()

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["blockers"], scenario["blockers"])
        self.assertIsNone(report["timing"]["first_five_seconds"])
        self.assertIsNone(report["timing"]["all_complete_seconds"])
        self.assertFalse(report["gates"]["no_external_blocker"])
        self.assertFalse(report["gates"]["overall"])


class PrioritySelectionDeterminismTests(unittest.TestCase):
    """T041: 100→15 deterministic priority detail selection."""

    def _select(self, candidates, *, detail_budget=15, directions=None):
        from webui.discovery import select_priority_details
        return select_priority_details(
            candidates, detail_budget=detail_budget, directions=directions or [],
        )

    def _make_candidates(self, count, directions_per_job=1):
        """Generate deterministic candidates with direction attribution."""
        candidates = []
        for i in range(count):
            dirs = [f"d{j % 3}" for j in range(directions_per_job)]
            candidates.append({
                "id": f"cand-{i:03d}",
                "job_id": f"job-{i:03d}",
                "direction_ids": dirs,
                "precheck_outcome": "pass",
                "list_fields": {"title": f"岗位{i}", "salary": f"{20+i%10}K"},
                "priority_components": {},
            })
        return candidates

    def test_default_budget_selects_fifteen_from_hundred(self):
        """100 候选 → 默认预算选择 15 个。"""
        candidates = self._make_candidates(100)
        result = self._select(candidates, detail_budget=15)
        self.assertEqual(len(result["selected"]), 15)
        self.assertEqual(len(result["deferred"]), 85)

    def test_budget_boundary_min_twelve(self):
        """预算下限 12：候选不足 12 时全选。"""
        candidates = self._make_candidates(8)
        result = self._select(candidates, detail_budget=12)
        self.assertEqual(len(result["selected"]), 8)

    def test_budget_boundary_max_twenty(self):
        """预算上限 20：即使候选充足也不超过 20。"""
        candidates = self._make_candidates(100)
        result = self._select(candidates, detail_budget=20)
        self.assertEqual(len(result["selected"]), 20)

    def test_per_direction_floor_at_least_one(self):
        """每个有候选的方向至少分配 1 个详情名额。"""
        candidates = []
        for i in range(50):
            candidates.append({
                "id": f"cand-d0-{i}", "job_id": f"job-d0-{i}",
                "direction_ids": ["d0"], "precheck_outcome": "pass",
                "list_fields": {}, "priority_components": {},
            })
        for i in range(2):
            candidates.append({
                "id": f"cand-d1-{i}", "job_id": f"job-d1-{i}",
                "direction_ids": ["d1"], "precheck_outcome": "pass",
                "list_fields": {}, "priority_components": {},
            })
        result = self._select(candidates, detail_budget=15, directions=["d0", "d1"])
        selected_dirs = {d for s in result["selected"] for d in s["direction_ids"]}
        self.assertIn("d0", selected_dirs)
        self.assertIn("d1", selected_dirs)

    def test_per_direction_floor_max_two(self):
        """方向 floor 不超过 2：即使预算紧张也不给空方向超过 2 个保底。"""
        candidates = []
        for i in range(40):
            candidates.append({
                "id": f"cand-d0-{i}", "job_id": f"job-d0-{i}",
                "direction_ids": ["d0"], "precheck_outcome": "pass",
                "list_fields": {}, "priority_components": {},
            })
        for i in range(40):
            candidates.append({
                "id": f"cand-d1-{i}", "job_id": f"job-d1-{i}",
                "direction_ids": ["d1"], "precheck_outcome": "pass",
                "list_fields": {}, "priority_components": {},
            })
        for i in range(20):
            candidates.append({
                "id": f"cand-d2-{i}", "job_id": f"job-d2-{i}",
                "direction_ids": ["d2"], "precheck_outcome": "pass",
                "list_fields": {}, "priority_components": {},
            })
        result = self._select(candidates, detail_budget=12, directions=["d0", "d1", "d2"])
        self.assertEqual(len(result["selected"]), 12)
        for d in ["d0", "d1", "d2"]:
            count = sum(1 for s in result["selected"] if d in s["direction_ids"])
            self.assertGreaterEqual(count, 1)

    def test_shared_candidate_not_double_counted(self):
        """共享岗位（多方向）只占一个预算名额。"""
        candidates = []
        for i in range(10):
            candidates.append({
                "id": f"cand-shared-{i}", "job_id": f"job-shared-{i}",
                "direction_ids": ["d0", "d1"], "precheck_outcome": "pass",
                "list_fields": {}, "priority_components": {},
            })
        for i in range(10):
            candidates.append({
                "id": f"cand-d0-{i}", "job_id": f"job-d0-{i}",
                "direction_ids": ["d0"], "precheck_outcome": "pass",
                "list_fields": {}, "priority_components": {},
            })
        result = self._select(candidates, detail_budget=15, directions=["d0", "d1"])
        self.assertEqual(len(result["selected"]), 15)
        selected_ids = [s["id"] for s in result["selected"]]
        self.assertEqual(len(selected_ids), len(set(selected_ids)))

    def test_stable_tie_break_under_input_reorder(self):
        """输入重排不改变选择结果（稳定 tie-break by job_id）。"""
        import random
        candidates = self._make_candidates(50)
        result_a = self._select(list(candidates), detail_budget=15)
        shuffled = list(candidates)
        random.Random(42).shuffle(shuffled)
        result_b = self._select(shuffled, detail_budget=15)
        ids_a = sorted(s["id"] for s in result_a["selected"])
        ids_b = sorted(s["id"] for s in result_b["selected"])
        self.assertEqual(ids_a, ids_b)

    def test_violation_candidates_never_selected(self):
        """precheck violation 的候选不得被 selected。"""
        candidates = self._make_candidates(20)
        for c in candidates[:5]:
            c["precheck_outcome"] = "violation"
        result = self._select(candidates, detail_budget=15)
        selected_outcomes = {s["precheck_outcome"] for s in result["selected"]}
        self.assertNotIn("violation", selected_outcomes)

    def test_selection_rank_is_sequential_from_one(self):
        """selected 候选的 selection_rank 从 1 开始连续递增。"""
        candidates = self._make_candidates(30)
        result = self._select(candidates, detail_budget=15)
        ranks = [s["selection_rank"] for s in result["selected"]]
        self.assertEqual(ranks, list(range(1, 16)))


class Sc004Sc010Sc011PerformanceGateTests(unittest.TestCase):
    """T084 验证 SC-004 / SC-010 / SC-011 的性能门。

    合同来源:
    - spec.md SC-004 (L273): 工作单元完成后 10 秒内进度可见；刷新后计数一致。
    - spec.md SC-010 (L279): cancel 后 30 秒内不再启动新 source/AI 工作；已完成保留 100%。
    - spec.md SC-011 (L280): 输入身份一致的 resume 不重复执行已完成 detail/assessment。

    这些是验证测试（非 RED→GREEN）：针对 T077/T079/T081 已实现的行为，
    在 ``tests/test_discovery_performance.py`` 中独立复核三个 SC 的性能门。
    """

    def setUp(self) -> None:
        import os
        import tempfile
        from webui.store import TaskStore

        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.store = TaskStore(self._tmp.name)
        self.profile = self.store.create_profile("测试画像")
        self.resume = self.store.save_resume(
            self.profile["id"], "storage/path.pdf", "pdf",
            "张三 高级后端开发工程师\n5年 Python 后端经验。",
            "hash123", "path.pdf",
        )
        self._teardown_paths = [self._tmp.name]

    def tearDown(self) -> None:
        import os
        for path in self._teardown_paths:
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Helpers (mirror ResumeHashDriftAndSc011Tests patterns)
    # ------------------------------------------------------------------

    def _make_v2_run_selected_n(self, candidate_count=5, detail_budget=5,
                                job_prefix="job-perf"):
        """Create a v2 run with N selected candidates ready for detail fetch.

        Computes real input_hash via compile_search_plan so the resume hash
        drift check passes; persists the search plan with completed items so
        ``calculate_run_completion`` can reach a terminal state.
        """
        import tempfile
        from pathlib import Path
        from webui.discovery import (
            SCRAPER_FILTER_FIELDS, compile_search_plan, select_priority_details,
        )
        from webui.source import _input_hash as _source_input_hash

        pid = self.profile["id"]
        rid = self.resume["id"]
        a = self.store.create_analysis(rid, pid)
        d1 = self.store.add_direction(
            a["id"], name="后端", direction_type="core", rationale="r",
            gaps=[], confidence=80, default_enabled=True, search_terms=["Python"],
        )
        c = self.store.create_confirmation(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            hard_constraints={}, soft_preferences={},
            safe_limits={"max_details": detail_budget},
            directions=[{"direction_id": d1["id"], "enabled": True,
                         "user_added": False, "user_label": None}],
        )
        confirmation_view = {
            "id": c["id"], "analysis_id": a["id"],
            "hard_constraints": {}, "soft_preferences": {},
            "safe_limits": {"max_details": detail_budget},
            "enabled_directions": [{
                "id": d1["id"], "direction_id": d1["id"],
                "name": d1.get("name", ""),
                "type": d1.get("direction_type", ""),
                "search_terms": d1.get("search_terms", []),
                "default_enabled": d1.get("default_enabled", False),
                "evidence_refs": [],
            }],
            "directions": c.get("directions", []),
        }
        plan = compile_search_plan(confirmation_view)
        real_input_hash = plan["input_hash"]
        run = self.store.create_discovery_run(
            profile_id=pid, resume_id=rid, analysis_id=a["id"],
            confirmation_id=c["id"], input_hash=real_input_hash,
            policy_version="discovery_v2",
        )
        for i in range(candidate_count):
            job_id = f"{job_prefix}-{i:03d}"
            with self.store._connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, '后端', '公司', '25K', '上海', 'jd', '2026-01-01', '2026-01-01')",
                    (job_id, f"https://www.zhipin.com/job_detail/{job_id}.html",
                     f"https://www.zhipin.com/job_detail/{job_id}.html"),
                )
            self.store.upsert_run_candidate(
                run_id=run["id"], job_id=job_id,
                source_url=f"https://www.zhipin.com/job_detail/{job_id}.html",
                direction_ids=[d1["id"]], search_terms=["Python"],
                source_positions=[{"item": i, "page": 1, "rank": i}],
                list_fields={"title": f"岗位{i}", "salary": "25K", "location": "上海"},
                input_hash=real_input_hash,
            )
        candidates = self.store.list_run_candidates(run["id"])
        result = select_priority_details(
            candidates, detail_budget=detail_budget, directions=[d1["id"]],
        )
        for item in result["selected"]:
            self.store.update_run_candidate_state(
                item["id"], state="selected", selection_decision="selected",
                selection_rank=item["selection_rank"], expected_state="discovered",
            )
        for item in result["deferred"]:
            self.store.update_run_candidate_state(
                item["id"], selection_decision="deferred",
                selection_reason="budget_deferred",
            )
        self.store.update_discovery_run(run["id"], counters={
            "list_candidate_count": candidate_count,
        })
        # Persist the search plan with completed items so resume via runner.run()
        # can reach a terminal state (calculate_run_completion requires no
        # pending plan items).
        materialized_items = []
        hard_constraints = plan.get("hard_constraints") or {}
        safe_limits = plan.get("safe_limits") or {}
        city = hard_constraints.get("city", "")
        source_filters = {
            k: v for k, v in hard_constraints.items()
            if k in SCRAPER_FILTER_FIELDS
        }
        target_pages = int(safe_limits.get("max_pages", 1))
        for raw_item in plan["items"]:
            item_input_hash = _source_input_hash({
                "keyword": raw_item["term"],
                "city": city,
                "source_filters": source_filters,
                "target_pages": target_pages,
            })
            materialized_items.append({
                "keyword": raw_item["term"],
                "city": city,
                "source_filters": source_filters,
                "direction_ids": raw_item["direction_ids"],
                "input_hash": item_input_hash,
                "target_pages": target_pages,
                "detail_budget": int(plan["detail_budget"] // max(1, len(plan["items"]))),
            })
        self.store.create_search_plan(
            run["id"],
            detail_budget=plan["detail_budget"],
            items=materialized_items,
        )
        persisted_plan = self.store.get_search_plan(run["id"])
        for item in persisted_plan["items"]:
            self.store.update_plan_item(
                item["id"], status="completed", completed=True,
            )
        return run, d1

    @staticmethod
    def _counting_source():
        """Fake source that records every fetch_detail invocation."""

        class _Source:
            def __init__(self):
                self.calls = []
                self.cancel_event = None

            def fetch_detail(self, job, detail_output_path=None):
                job_id = job.get("job_id")
                self.calls.append(job_id)

                class Outcome:
                    pass
                out = Outcome()
                out.ok = True
                out.detail = {"jd": "详细职位描述", "tags": "Python,Django"}
                out.failed_code = None
                out.safe_log = None
                return out
        return _Source()

    @staticmethod
    def _counting_ai():
        """Fake v2 AI provider that records every assess_job invocation."""

        class _AI:
            def __init__(self):
                self.calls = 0
                self.v2_calls = []

            def assess_job(self, *, candidate_profile=None, directions=None,
                           job_snapshot=None, contract_version="v1", **_kwargs):
                self.calls += 1
                self.v2_calls.append(job_snapshot.get("snapshot_id", "") if job_snapshot else "")
                score = 80
                assessments = []
                for d in directions or []:
                    assessments.append({
                        "direction_id": d["id"],
                        "dimensions": {
                            "capability": {"score": score, "candidate_fact_refs": [],
                                           "candidate_evidence_refs": [],
                                           "job_evidence_refs": []},
                            "experience": {"score": score, "candidate_fact_refs": [],
                                           "candidate_evidence_refs": [],
                                           "job_evidence_refs": []},
                            "environment": {"score": score, "candidate_fact_refs": [],
                                            "candidate_evidence_refs": [],
                                            "job_evidence_refs": []},
                            "stability": {"score": score, "candidate_fact_refs": [],
                                          "candidate_evidence_refs": [],
                                          "job_evidence_refs": []},
                        },
                        "match_score": score, "confidence": score,
                        "gaps": [], "proposed_band": "high",
                    })
                return {
                    "contract_version": "job_assessment_v2",
                    "assessments": assessments,
                    "quarantined": [],
                    "quality": {"status": "complete", "warnings": []},
                    "metrics": {"provider_call_count": 1},
                }
        return _AI()

    def _make_runner(self, source, ai):
        import tempfile
        from pathlib import Path
        from webui.discovery_runner import DiscoveryRunner
        runner = DiscoveryRunner(self.store, source=source, ai_provider=ai,
                                 result_dir=Path(tempfile.mkdtemp()))
        self._teardown_paths.append(str(runner.result_dir))
        return runner

    # ------------------------------------------------------------------
    # SC-004: progress visible within 10 simulated seconds of work-unit
    # completion; refresh preserves counts.
    # ------------------------------------------------------------------

    def test_sc004_detail_completed_count_visible_same_simulated_instant(self):
        """SC-004: detail 完成后 ``detail_completed_count`` 立即可见（同模拟时刻）。

        合同：工作单元完成 → store 同事务更新计数 → 下一次 ``get_discovery_run``
        读到的 ``detail_completed_count`` 反映该完成。模拟秒数=0（即时）≤10。
        """
        import tempfile
        from pathlib import Path
        from webui.discovery_runner import DiscoveryRunner

        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=3)
        source = self._counting_source()
        runner = self._make_runner(source, self._counting_ai())

        before = self.store.get_discovery_run(run["id"])
        self.assertEqual(int(before.get("detail_completed_count") or 0), 0)

        # Drive progressive eval; each completed detail writes the counter
        # synchronously in the same transaction as the snapshot persist.
        runner.run_progressive_detail_eval(run["id"])

        after = self.store.get_discovery_run(run["id"])
        selected = self.store.list_run_candidates(run["id"], selection_decision="selected")
        self.assertEqual(len(source.calls), len(selected))
        # detail_completed_count matches the number of snapshots persisted
        # (≤ selected count; failed details produce partial snapshots that
        # also count as completed fetches per data-model.md L318-328).
        snapshots = self.store.list_snapshots(run["id"])
        self.assertEqual(int(after.get("detail_completed_count") or 0),
                         len(snapshots),
                         "detail_completed_count 必须在 detail 完成同模拟时刻反映")
        self.assertGreaterEqual(int(after.get("detail_completed_count") or 0),
                                len(selected) - 0,
                                "全部 selected detail 完成后计数必须覆盖")

    def test_sc004_assessment_completed_count_visible_same_simulated_instant(self):
        """SC-004: assessment 完成后 ``assessment_completed_count`` 立即可见。"""
        from webui.discovery_runner import DiscoveryRunner

        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=3)
        runner = self._make_runner(self._counting_source(), self._counting_ai())

        before = self.store.get_discovery_run(run["id"])
        self.assertEqual(int(before.get("assessment_completed_count") or 0), 0)

        runner.run_progressive_detail_eval(run["id"])

        after = self.store.get_discovery_run(run["id"])
        assessments = self.store.list_assessments(run["id"])
        self.assertGreaterEqual(len(assessments), 1)
        # assessment_completed_count counts completed assessments (per direction).
        completed_assessments = [a for a in assessments
                                 if a.get("status") == "completed"]
        self.assertEqual(int(after.get("assessment_completed_count") or 0),
                         len(completed_assessments),
                         "assessment_completed_count 必须在评估完成同模拟时刻反映")

    def test_sc004_refresh_preserves_counts(self):
        """SC-004: 刷新（重新读取 run）后计数保持一致。"""
        from webui.discovery_runner import DiscoveryRunner

        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=3)
        runner = self._make_runner(self._counting_source(), self._counting_ai())
        runner.run_progressive_detail_eval(run["id"])

        first = self.store.get_discovery_run(run["id"])
        # Re-read (simulating page refresh) — counts must be identical.
        second = self.store.get_discovery_run(run["id"])
        self.assertEqual(first.get("detail_completed_count"),
                         second.get("detail_completed_count"))
        self.assertEqual(first.get("assessment_completed_count"),
                         second.get("assessment_completed_count"))
        self.assertEqual(first.get("list_candidate_count"),
                         second.get("list_candidate_count"))

    def test_sc004_first_result_at_written_on_first_unit_completion(self):
        """SC-004: 首个评估完成时 ``first_result_at`` 必须写入（≤10 模拟秒的边界）。"""
        from webui.discovery_runner import DiscoveryRunner

        run, _ = self._make_v2_run_selected_n(candidate_count=3, detail_budget=3)
        runner = self._make_runner(self._counting_source(), self._counting_ai())

        before = self.store.get_discovery_run(run["id"])
        self.assertIsNone(before.get("first_result_at"))

        runner.run_progressive_detail_eval(run["id"])

        after = self.store.get_discovery_run(run["id"])
        self.assertIsNotNone(after.get("first_result_at"),
                             "首个结果可见后 first_result_at 必须立即写入（≤10 模拟秒）")

    # ------------------------------------------------------------------
    # SC-010: cancel reaches terminal within 30 wall-clock seconds with
    # 100% preservation of already-persisted results.
    # ------------------------------------------------------------------

    def test_sc010_cancel_reaches_terminal_within_30_wall_clock_seconds(self):
        """SC-010: cancel 后 30 秒（wall-clock）内 run 必须进入 cancelled 终态。"""
        import time
        from webui.discovery_runner import DiscoveryRunner

        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=5)
        runner = self._make_runner(self._counting_source(), self._counting_ai())
        runner.request_cancel(run["id"])

        started = time.monotonic()
        final = runner.run(run["id"])
        elapsed = time.monotonic() - started

        self.assertEqual(final["status"], "cancelled",
                         "SC-010: cancel 后必须进入 cancelled 终态")
        self.assertLessEqual(elapsed, 30.0,
                             "SC-010: cancel 到终态必须 ≤30 秒（wall-clock）")

    def test_sc010_cancel_preserves_100_percent_completed_results(self):
        """SC-010: cancel 后已完成的 snapshots / assessments / candidates 100% 保留。

        合同：cancel 不删除任何已持久化的工作产物；保留率 = 100%。
        """
        from webui.discovery_runner import DiscoveryRunner, STATUS_INTERRUPTED

        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=5)
        runner = self._make_runner(self._counting_source(), self._counting_ai())
        # Process all 5 candidates first (5 snapshots + 5 assessments).
        runner.run_progressive_detail_eval(run["id"])
        before_snapshots = self.store.list_snapshots(run["id"])
        before_assessments = self.store.list_assessments(run["id"])
        before_candidates = self.store.list_run_candidates(run["id"])
        self.assertEqual(len(before_snapshots), 5)
        self.assertEqual(len(before_assessments), 5)
        self.assertGreaterEqual(len(before_candidates), 5)

        # Cancel and re-run; results must survive.
        runner.request_cancel(run["id"])
        final = runner.run(run["id"])
        # final status is terminal (cancelled or succeeded if progressive eval
        # already finished and assembling completed). Either way, data survives.
        self.assertIn(final["status"], ("cancelled", "succeeded", "partial"))

        after_snapshots = self.store.list_snapshots(run["id"])
        after_assessments = self.store.list_assessments(run["id"])
        after_candidates = self.store.list_run_candidates(run["id"])

        # 100% preservation: identical ID sets.
        self.assertEqual({s["id"] for s in before_snapshots},
                         {s["id"] for s in after_snapshots},
                         "SC-010: snapshots 保留率必须为 100%")
        self.assertEqual({a["id"] for a in before_assessments},
                         {a["id"] for a in after_assessments},
                         "SC-010: assessments 保留率必须为 100%")
        self.assertEqual({c["id"] for c in before_candidates},
                         {c["id"] for c in after_candidates},
                         "SC-010: candidates 保留率必须为 100%")

    def test_sc010_cancel_blocks_new_source_and_ai_work(self):
        """SC-010: cancel 信号设置后不得再调用 source.fetch_detail 或 AI provider。"""
        from webui.discovery_runner import DiscoveryRunner

        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=5)
        source = self._counting_source()
        ai = self._counting_ai()
        runner = self._make_runner(source, ai)
        runner.request_cancel(run["id"])
        final = runner.run(run["id"])
        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(len(source.calls), 0,
                         "SC-010: cancel 后不得调用 source.fetch_detail")
        self.assertEqual(ai.calls, 0,
                         "SC-010: cancel 后不得调用 AI provider")

    # ------------------------------------------------------------------
    # SC-011: resume with matching input identity → 0 duplicate
    # detail/assessment executions.
    # ------------------------------------------------------------------

    def test_sc011_resume_zero_duplicate_detail_fetches(self):
        """SC-011: 输入身份一致的 resume 不重复调用 source.fetch_detail。"""
        from webui.discovery_runner import STATUS_INTERRUPTED

        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=5)
        source = self._counting_source()
        runner = self._make_runner(source, self._counting_ai())
        # First pass: all 5 candidates processed.
        runner.run_progressive_detail_eval(run["id"])
        self.assertEqual(len(source.calls), 5)
        # Simulate interrupt.
        self.store.update_discovery_run(
            run["id"], status=STATUS_INTERRUPTED, stage="processing_jobs", started=True,
        )
        # Reset counter; resume must NOT re-fetch any detail.
        source.calls.clear()
        runner.run_progressive_detail_eval(run["id"])
        self.assertEqual(len(source.calls), 0,
                         "SC-011: resume 时已完成 detail 重复执行数必须为 0")

    def test_sc011_resume_zero_duplicate_ai_calls(self):
        """SC-011: 输入身份一致的 resume 不重复调用 AI provider。"""
        from webui.discovery_runner import STATUS_INTERRUPTED

        run, _ = self._make_v2_run_selected_n(candidate_count=5, detail_budget=5)
        ai = self._counting_ai()
        runner = self._make_runner(self._counting_source(), ai)
        # First pass: 5 AI calls.
        runner.run_progressive_detail_eval(run["id"])
        self.assertEqual(ai.calls, 5)
        # Simulate interrupt, reset counter, resume.
        self.store.update_discovery_run(
            run["id"], status=STATUS_INTERRUPTED, stage="processing_jobs", started=True,
        )
        ai.calls = 0
        ai.v2_calls.clear()
        runner.run_progressive_detail_eval(run["id"])
        self.assertEqual(ai.calls, 0,
                         "SC-011: resume 时已完成 assessment 重复执行数必须为 0")


class Sc003DeterministicOrchestrationGateTests(unittest.TestCase):
    """T085 验证 SC-003 的确定性编排门。

    合同来源:
    - spec.md SC-003 (L272): 标准运行处理 15 个真实岗位详情并完成所需评估的
      总时间不超过 10 分钟（600 模拟秒）；结果必须同时报告实际处理数量和外部阻塞。
    - data-model.md: DiscoveryPerformanceMetrics 是确定性、可注入时钟的性能合同。

    这些是验证测试（非 RED→GREEN）：针对 T007 已实现的 ``DiscoveryPerformanceMetrics``，
    在 ``tests/test_discovery_performance.py`` 中独立复核 SC-003 的编排门，
    覆盖 15 详情 + 必需评估的总时长门、实际处理数、等待原因、AI 调用数和阻塞报告。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = _load_fixture()

    def _new_metrics(self, clock: FakeMonotonicClock):
        from webui.discovery_runner import DiscoveryPerformanceMetrics
        metrics = DiscoveryPerformanceMetrics(monotonic_clock=clock)
        metrics.start()
        return metrics

    def _record_fifteen_details(
        self, metrics, clock: FakeMonotonicClock, *,
        all_complete_seconds: float = 1600.0,
        record_blocker: bool = False,
    ) -> dict[str, Any]:
        """Record 15 detail + 15 AI group completions per fixture timing.

        ``all_complete_seconds`` is the absolute monotonic-clock value at
        which ``mark_all_complete`` is called (matching the fixture's
        ``healthy_boundary.all_complete_seconds`` = 1600; 1600 - start 1000
        = 600s, the SC-003 inclusive boundary).

        Returns the built performance report.
        """
        scenario = self.fixture["performance_scenarios"]["healthy_boundary"]
        clock.set(scenario["list_complete_seconds"])
        metrics.mark_list_completed(query_count=3, job_count=100)
        metrics.record_selection(
            selected_count=15,
            deferred_count=85,
            reasons={"budget_deferred": 85},
        )
        # Record first-five result_visible milestones (required for
        # gates.first_five_within_300_seconds to be satisfiable).
        for job_id, visible_at in zip(
            [item["job_id"] for item in self.fixture["accessible_details"][:5]],
            scenario["result_visible_seconds"],
            strict=True,
        ):
            clock.set(visible_at)
            metrics.record_result_visible(job_id=job_id)
        # Record 15 detail + 15 AI completions using fixture timing.
        for detail in self.fixture["accessible_details"][:15]:
            timing = detail["timing"]
            metrics.record_detail_completed(
                job_id=detail["job_id"],
                total_seconds=timing["total_seconds"],
                wait_seconds=timing["wait_seconds"],
                wait_reason="readiness",
                batch=timing["batch"],
                concurrency=timing["concurrency"],
                reused=False,
            )
            assessment = detail["assessment"]
            metrics.record_ai_group_completed(
                job_id=detail["job_id"],
                direction_count=len(assessment["direction_ids"]),
                call_count=assessment["ai_calls"],
                duration_seconds=assessment["duration_seconds"],
            )
        if record_blocker:
            blocker_scenario = self.fixture["performance_scenarios"]["externally_blocked"]
            blocker = blocker_scenario["blockers"][0]
            metrics.record_blocker(
                code=blocker["code"],
                stage=blocker["stage"],
                external=blocker["external"],
            )
        clock.set(all_complete_seconds)
        metrics.mark_all_complete()
        return metrics.build_report()

    # ------------------------------------------------------------------
    # SC-003: 15 details + required assessments ≤ 600 simulated seconds
    # ------------------------------------------------------------------

    def test_sc003_fifteen_details_complete_within_600_simulated_seconds(self):
        """SC-003: 15 详情 + 必需评估在 600 模拟秒内完成（边界值）。"""
        scenario = self.fixture["performance_scenarios"]["healthy_boundary"]
        clock = FakeMonotonicClock(scenario["start_seconds"])
        report = self._record_fifteen_details(
            self._new_metrics(clock), clock, all_complete_seconds=1600,
        )
        # 1600 - 1000 = 600s exactly (inclusive boundary).
        self.assertEqual(report["timing"]["all_complete_seconds"], 600.0)
        self.assertTrue(report["gates"]["all_complete_within_600_seconds"],
                        "SC-003: 15 详情 + 评估在 600 模拟秒内必须通过")
        self.assertTrue(report["gates"]["overall"],
                        "SC-003: 健康场景下 overall 门必须通过")

    def test_sc003_fails_above_600_simulated_seconds(self):
        """SC-003: 超过 600 模拟秒（600.001）必须立即失败。"""
        scenario = self.fixture["performance_scenarios"]["healthy_boundary"]
        clock = FakeMonotonicClock(scenario["start_seconds"])
        report = self._record_fifteen_details(
            self._new_metrics(clock), clock, all_complete_seconds=1600.001,
        )
        self.assertEqual(report["timing"]["all_complete_seconds"], 600.001)
        self.assertFalse(report["gates"]["all_complete_within_600_seconds"],
                         "SC-003: 超过 600 模拟秒必须失败")
        self.assertFalse(report["gates"]["overall"],
                         "SC-003: 超时后 overall 门必须失败")

    # ------------------------------------------------------------------
    # SC-003: report real processing count, wait reasons, AI calls, blockers
    # ------------------------------------------------------------------

    def test_sc003_reports_real_processing_count_fifteen(self):
        """SC-003: 报告真实处理数（15），而非计划数或估计数。"""
        scenario = self.fixture["performance_scenarios"]["healthy_boundary"]
        clock = FakeMonotonicClock(scenario["start_seconds"])
        report = self._record_fifteen_details(self._new_metrics(clock), clock)
        self.assertEqual(report["details"]["processed_count"], 15,
                         "SC-003: details.processed_count 必须反映真实处理数 15")
        self.assertEqual(len(report["details"]["items"]), 15,
                         "SC-003: details.items 必须有 15 条记录")
        self.assertEqual(report["details"]["reused_count"], 0,
                         "SC-003: 全新抓取场景 reused_count 必须为 0")

    def test_sc003_reports_wait_reasons_per_detail_item(self):
        """SC-003: 每条 detail item 必须报告 wait_reason（等待原因）。"""
        scenario = self.fixture["performance_scenarios"]["healthy_boundary"]
        clock = FakeMonotonicClock(scenario["start_seconds"])
        report = self._record_fifteen_details(self._new_metrics(clock), clock)
        for item in report["details"]["items"]:
            self.assertIn("wait_reason", item,
                          "SC-003: 每个 detail item 必须含 wait_reason")
            self.assertTrue(item["wait_reason"],
                            "SC-003: wait_reason 不得为空")
        # Aggregate wait reasons must be reportable.
        wait_reasons = {item["wait_reason"] for item in report["details"]["items"]}
        self.assertIn("readiness", wait_reasons,
                      "SC-003: 等待原因集合必须包含已记录的 readiness")

    def test_sc003_reports_ai_calls(self):
        """SC-003: 报告 AI 调用总数（ai.call_count）。"""
        scenario = self.fixture["performance_scenarios"]["healthy_boundary"]
        clock = FakeMonotonicClock(scenario["start_seconds"])
        report = self._record_fifteen_details(self._new_metrics(clock), clock)
        # Each fixture detail has ai_calls=1, so total = 15.
        self.assertEqual(report["ai"]["call_count"], 15,
                         "SC-003: ai.call_count 必须反映真实 AI 调用数")
        self.assertEqual(report["ai"]["group_count"], 15,
                         "SC-003: ai.group_count 必须反映评估分组数")
        self.assertGreater(report["ai"]["duration_seconds"], 0,
                           "SC-003: ai.duration_seconds 必须报告")

    def test_sc003_reports_blockers_empty_for_healthy(self):
        """SC-003: 健康场景下 blockers 必须为空列表（仍需报告）。"""
        scenario = self.fixture["performance_scenarios"]["healthy_boundary"]
        clock = FakeMonotonicClock(scenario["start_seconds"])
        report = self._record_fifteen_details(self._new_metrics(clock), clock)
        self.assertEqual(report["blockers"], [],
                         "SC-003: 健康场景 blockers 必须为空列表")
        self.assertTrue(report["gates"]["no_external_blocker"],
                        "SC-003: 健康场景 no_external_blocker 门必须通过")

    def test_sc003_reports_external_blocker_and_fails_overall(self):
        """SC-003: 外部阻塞必须被报告且 overall 门失败。"""
        scenario = self.fixture["performance_scenarios"]["healthy_boundary"]
        clock = FakeMonotonicClock(scenario["start_seconds"])
        report = self._record_fifteen_details(
            self._new_metrics(clock), clock, record_blocker=True,
        )
        self.assertEqual(len(report["blockers"]), 1,
                         "SC-003: 外部阻塞必须被报告")
        blocker = report["blockers"][0]
        self.assertTrue(blocker["external"],
                        "SC-003: 外部阻塞 external 标志必须为 True")
        self.assertEqual(report["status"], "blocked",
                         "SC-003: 存在外部阻塞时 status 必须为 blocked")
        self.assertFalse(report["gates"]["no_external_blocker"],
                         "SC-003: 外部阻塞时 no_external_blocker 门必须失败")
        self.assertFalse(report["gates"]["overall"],
                         "SC-003: 外部阻塞时 overall 门必须失败")

    def test_sc003_report_includes_all_required_orchestration_fields(self):
        """SC-003: 报告必须含编排门所需的全部字段（处理数、等待、AI、阻塞）。"""
        scenario = self.fixture["performance_scenarios"]["healthy_boundary"]
        clock = FakeMonotonicClock(scenario["start_seconds"])
        report = self._record_fifteen_details(self._new_metrics(clock), clock)
        # Required top-level fields for SC-003 orchestration gate.
        for field in ("contract_version", "status", "list", "selection", "details",
                      "ai", "timing", "blockers", "gates"):
            self.assertIn(field, report,
                          f"SC-003: 报告必须含 {field}")
        # details sub-fields (real processing count + wait reasons).
        for field in ("processed_count", "reused_count", "failed_count",
                      "cancelled_count", "items", "batch_count", "peak_concurrency"):
            self.assertIn(field, report["details"],
                          f"SC-003: details 必须含 {field}")
        # ai sub-fields (AI calls).
        for field in ("group_count", "call_count", "duration_seconds"):
            self.assertIn(field, report["ai"],
                          f"SC-003: ai 必须含 {field}")
        # gates sub-fields (orchestration gate decisions).
        for field in ("list_pool_within_90_seconds", "first_five_within_300_seconds",
                      "all_complete_within_600_seconds", "no_external_blocker",
                      "overall"):
            self.assertIn(field, report["gates"],
                          f"SC-003: gates 必须含 {field}")


if __name__ == "__main__":
    unittest.main()
