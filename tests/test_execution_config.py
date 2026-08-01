"""SPEC011 T001 — 执行配置 schema、规范化、校验与不可变快照的 RED 测试。

覆盖 FR-001 ~ FR-009 与 data-model.md 中的 ExecutionConfigSnapshot / FrozenTaskScope。
这些测试在 T002 实现 webui/execution_config.py 之前应当全部失败（RED）。
"""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webui import execution_config


# ---------------------------------------------------------------------------
# 辅助：合法速度字段配置
# ---------------------------------------------------------------------------
def _valid_config_values():
    return {
        "inter_combo_delay": 10.0,
        "detail_batch_size": 15,
        "detail_interval": 2.0,
        "detail_reset_every": 4,
        "detail_batch_cooldown": 5.0,
        "detail_tab_pool_size": 5,
        "screen_batch_size": 50,
        "screen_concurrency": 5,
        "match_batch_size": 4,
        "match_concurrency": 10,
    }


# ===========================================================================
# ExecutionConfigSnapshot — 速度字段、物理校验、规范序列化、摘要
# ===========================================================================
class ExecutionConfigFieldsTests(unittest.TestCase):
    """FR-010: 仅调优速度字段；data-model 1.1 物理边界。"""

    SPEED_FIELDS = (
        "inter_combo_delay",
        "detail_batch_size",
        "detail_interval",
        "detail_reset_every",
        "detail_batch_cooldown",
        "detail_tab_pool_size",
        "screen_batch_size",
        "screen_concurrency",
        "match_batch_size",
        "match_concurrency",
    )

    def test_snapshot_contains_all_speed_fields_plus_meta(self):
        snap = execution_config.ExecutionConfigSnapshot.create(_valid_config_values())
        data = snap.to_dict()
        for field in self.SPEED_FIELDS:
            self.assertIn(field, data, f"缺字段 {field}")
        # pages 不属于配置快照
        self.assertNotIn("pages", data)
        # 元数据字段
        self.assertIn("schema_version", data)
        self.assertIn("config_digest", data)

    def test_inter_combo_delay_must_be_non_negative(self):
        values = _valid_config_values()
        values["inter_combo_delay"] = -0.1
        with self.assertRaises(ValueError):
            execution_config.ExecutionConfigSnapshot.create(values)

    def test_detail_batch_size_must_be_at_least_one(self):
        values = _valid_config_values()
        values["detail_batch_size"] = 0
        with self.assertRaises(ValueError):
            execution_config.ExecutionConfigSnapshot.create(values)

    def test_detail_interval_must_be_non_negative(self):
        values = _valid_config_values()
        values["detail_interval"] = -1
        with self.assertRaises(ValueError):
            execution_config.ExecutionConfigSnapshot.create(values)

    def test_detail_reset_every_must_be_at_least_one(self):
        values = _valid_config_values()
        values["detail_reset_every"] = 0
        with self.assertRaises(ValueError):
            execution_config.ExecutionConfigSnapshot.create(values)

    def test_detail_batch_cooldown_must_be_non_negative(self):
        values = _valid_config_values()
        values["detail_batch_cooldown"] = -0.01
        with self.assertRaises(ValueError):
            execution_config.ExecutionConfigSnapshot.create(values)

    def test_screen_batch_size_must_be_at_least_one(self):
        values = _valid_config_values()
        values["screen_batch_size"] = 0
        with self.assertRaises(ValueError):
            execution_config.ExecutionConfigSnapshot.create(values)

    def test_screen_concurrency_must_be_at_least_one(self):
        values = _valid_config_values()
        values["screen_concurrency"] = 0
        with self.assertRaises(ValueError):
            execution_config.ExecutionConfigSnapshot.create(values)

    def test_match_batch_size_must_be_at_least_one(self):
        values = _valid_config_values()
        values["match_batch_size"] = 0
        with self.assertRaises(ValueError):
            execution_config.ExecutionConfigSnapshot.create(values)

    def test_match_concurrency_must_be_at_least_one(self):
        values = _valid_config_values()
        values["match_concurrency"] = 0
        with self.assertRaises(ValueError):
            execution_config.ExecutionConfigSnapshot.create(values)

    def test_detail_tab_pool_size_must_be_between_one_and_ten(self):
        for value in (0, 11):
            with self.subTest(value=value):
                values = _valid_config_values()
                values["detail_tab_pool_size"] = value
                with self.assertRaises(ValueError):
                    execution_config.ExecutionConfigSnapshot.create(values)


    def test_detail_tab_pool_size_accepts_ten(self):
        values = _valid_config_values()
        values["detail_tab_pool_size"] = 10
        snapshot = execution_config.ExecutionConfigSnapshot.create(values)
        self.assertEqual(snapshot.detail_tab_pool_size, 10)

    def test_integer_fields_reject_fractional_and_boolean_values(self):
        integer_fields = (
            "detail_batch_size", "detail_reset_every", "detail_tab_pool_size",
            "screen_concurrency", "match_batch_size", "match_concurrency",
        )
        for field in integer_fields:
            for value in (1.9, True):
                with self.subTest(field=field, value=value):
                    values = _valid_config_values()
                    values[field] = value
                    with self.assertRaises((TypeError, ValueError)):
                        execution_config.ExecutionConfigSnapshot.create(values)

    def test_decimal_fields_reject_non_finite_and_boolean_values(self):
        decimal_fields = (
            "inter_combo_delay", "detail_interval", "detail_batch_cooldown",
        )
        for field in decimal_fields:
            for value in (float("nan"), float("inf"), float("-inf"), True):
                with self.subTest(field=field, value=value):
                    values = _valid_config_values()
                    values[field] = value
                    with self.assertRaises((TypeError, ValueError)):
                        execution_config.ExecutionConfigSnapshot.create(values)


class ExecutionConfigCanonicalSerializationTests(unittest.TestCase):
    """规范 JSON 序列化和 config_digest 不可变性。"""

    def test_canonical_json_is_deterministic(self):
        snap1 = execution_config.ExecutionConfigSnapshot.create(_valid_config_values())
        snap2 = execution_config.ExecutionConfigSnapshot.create(_valid_config_values())
        self.assertEqual(snap1.canonical_json(), snap2.canonical_json())
        self.assertEqual(snap1.config_digest, snap2.config_digest)

    def test_digest_changes_when_field_value_changes(self):
        snap1 = execution_config.ExecutionConfigSnapshot.create(_valid_config_values())
        values2 = _valid_config_values()
        values2["inter_combo_delay"] = 20.0
        snap2 = execution_config.ExecutionConfigSnapshot.create(values2)
        self.assertNotEqual(snap1.config_digest, snap2.config_digest)

    def test_digest_is_sha256_hex(self):
        snap = execution_config.ExecutionConfigSnapshot.create(_valid_config_values())
        self.assertRegex(snap.config_digest, r"^[0-9a-f]{64}$")

    def test_snapshot_is_immutable(self):
        snap = execution_config.ExecutionConfigSnapshot.create(_valid_config_values())
        with self.assertRaises((AttributeError, TypeError)):
            snap.inter_combo_delay = 999  # type: ignore[misc]

    def test_canonical_json_excludes_digest_field(self):
        """摘要计算输入不包含 digest 自身，避免循环。"""
        snap = execution_config.ExecutionConfigSnapshot.create(_valid_config_values())
        canonical = json.loads(snap.canonical_json())
        self.assertNotIn("config_digest", canonical)
        self.assertIn("schema_version", canonical)

    def test_to_dict_includes_digest(self):
        snap = execution_config.ExecutionConfigSnapshot.create(_valid_config_values())
        data = snap.to_dict()
        self.assertEqual(data["config_digest"], snap.config_digest)

    def test_from_dict_round_trip_preserves_digest(self):
        snap = execution_config.ExecutionConfigSnapshot.create(_valid_config_values())
        data = snap.to_dict()
        restored = execution_config.ExecutionConfigSnapshot.from_dict(data)
        self.assertEqual(restored.config_digest, snap.config_digest)
        self.assertEqual(restored.canonical_json(), snap.canonical_json())

    def test_from_dict_rejects_tampered_digest(self):
        snap = execution_config.ExecutionConfigSnapshot.create(_valid_config_values())
        data = snap.to_dict()
        data["config_digest"] = "tampered"
        with self.assertRaises(ValueError):
            execution_config.ExecutionConfigSnapshot.from_dict(data)


# ===========================================================================
# 关键词标准化与去重 (FR-001, FR-002)
# ===========================================================================
class KeywordNormalizationTests(unittest.TestCase):

    def test_strips_whitespace(self):
        result = execution_config.normalize_keywords(["  AI应用开发  "])
        self.assertEqual(result, ["AI应用开发"])

    def test_collapses_internal_spaces(self):
        result = execution_config.normalize_keywords(["AI  应用   开发"])
        self.assertEqual(result, ["AI 应用 开发"])

    def test_case_insensitive_dedup_english(self):
        result = execution_config.normalize_keywords(["Python", "python", "PYTHON"])
        self.assertEqual(len(result), 1)

    def test_semantically_different_keywords_retained(self):
        """FR-002: 含义相近但文字不同的关键词不自动合并。"""
        result = execution_config.normalize_keywords(["AI应用开发", "智能体开发"])
        self.assertEqual(len(result), 2)

    def test_exact_duplicate_after_normalization_removed(self):
        result = execution_config.normalize_keywords(["AI应用开发", " AI应用开发 "])
        self.assertEqual(result, ["AI应用开发"])

    def test_empty_keywords_removed(self):
        result = execution_config.normalize_keywords(["", "  ", "有效", None])
        self.assertEqual(result, ["有效"])

    def test_preserves_order(self):
        result = execution_config.normalize_keywords(["B", "A", "C"])
        self.assertEqual(result, ["B", "A", "C"])

    def test_unicode_compatibility_equivalents_deduplicate(self):
        result = execution_config.normalize_keywords(["ＡＩ Agent", "AI Agent"])
        self.assertEqual(result, ["AI Agent"])


# ===========================================================================
# 城市校验、别名与全国互斥 (FR-003, FR-004, FR-005)
# ===========================================================================
class CityValidationTests(unittest.TestCase):

    def test_known_city_resolves(self):
        result = execution_config.validate_cities(["北京"])
        self.assertEqual(result, ["北京"])

    def test_alias_unified_to_canonical(self):
        """FR-004: 城市别名统一为正式名称后去重。"""
        # 东莞市 是 东莞 的别名
        result = execution_config.validate_cities(["东莞", "东莞市"])
        self.assertEqual(result, ["东莞"])

    def test_unknown_city_rejected_with_suggestions(self):
        """FR-003: 未知城市不自动替换，提供可执行建议。"""
        with self.assertRaises(execution_config.CityValidationError) as ctx:
            execution_config.validate_cities(["不存在的城市XYZ"])
        self.assertIn("suggestions", ctx.exception.details or {})

    def test_disabled_city_rejected(self):
        """FR-003: 已禁用城市不通过。"""
        with self.assertRaises(execution_config.CityValidationError):
            execution_config.validate_cities(["已禁用城市"])

    def test_city_dedup_after_alias_normalization(self):
        result = execution_config.validate_cities(["北京", "北京"])
        self.assertEqual(result, ["北京"])

    def test_nationwide_excludes_cities(self):
        """FR-005: 全国与具体城市互斥。"""
        with self.assertRaises(ValueError):
            execution_config.normalize_scope(
                keywords=["AI"],
                scope_kind="nationwide",
                cities=["北京"],
                pages_per_combination=1,
            )

    def test_nationwide_scope_count_is_one(self):
        scope = execution_config.normalize_scope(
            keywords=["AI"],
            scope_kind="nationwide",
            cities=[],
            pages_per_combination=3,
        )
        self.assertEqual(scope.combination_count, 1)
        self.assertEqual(scope.planned_pages, 3)


# ===========================================================================
# 计划页数与任务规模 (FR-006, FR-007, FR-008)
# ===========================================================================
class TaskSizeClassificationTests(unittest.TestCase):

    # 使用真实城市名（足够覆盖 200 页边界测试）
    _REAL_CITIES = [
        "北京", "上海", "广州", "深圳", "杭州", "天津", "西安", "苏州", "武汉", "厦门",
        "长沙", "成都", "郑州", "重庆", "南京", "青岛", "大连", "沈阳", "哈尔滨", "济南",
        "昆明", "南宁", "福州", "太原", "贵阳", "合肥", "南昌", "长春", "石家庄", "兰州",
    ]

    def _scope(self, keyword_count, scope_count, pages_per_combo):
        keywords = [f"kw{i}" for i in range(keyword_count)]
        cities = self._REAL_CITIES[:scope_count]
        return execution_config.normalize_scope(
            keywords=keywords,
            scope_kind="cities",
            cities=cities,
            pages_per_combination=pages_per_combo,
        )

    def test_one_page_is_small(self):
        scope = self._scope(1, 1, 1)
        self.assertEqual(scope.planned_pages, 1)
        self.assertEqual(scope.task_size, "small")

    def test_nine_pages_is_small(self):
        scope = self._scope(3, 3, 1)  # 3×3×1=9
        self.assertEqual(scope.planned_pages, 9)
        self.assertEqual(scope.task_size, "small")

    def test_ten_pages_is_medium(self):
        scope = self._scope(2, 5, 1)  # 2×5×1=10
        self.assertEqual(scope.planned_pages, 10)
        self.assertEqual(scope.task_size, "medium")

    def test_forty_nine_pages_is_medium(self):
        scope = self._scope(49, 1, 1)  # 49×1×1=49
        self.assertEqual(scope.planned_pages, 49)
        self.assertEqual(scope.task_size, "medium")

    def test_fifty_pages_is_large(self):
        scope = self._scope(50, 1, 1)  # 50×1×1=50
        self.assertEqual(scope.planned_pages, 50)
        self.assertEqual(scope.task_size, "large")

    def test_two_hundred_pages_is_large(self):
        scope = self._scope(200, 1, 1)  # 200×1×1=200
        self.assertEqual(scope.planned_pages, 200)
        self.assertEqual(scope.task_size, "large")

    def test_over_two_hundred_pages_rejected(self):
        with self.assertRaises(ValueError):
            self._scope(201, 1, 1)

    def test_zero_pages_rejected(self):
        with self.assertRaises(ValueError):
            self._scope(1, 1, 0)

    def test_duplicate_keywords_do_not_increase_pages(self):
        """SC-002: 重复关键词不增加计划页数。"""
        scope = execution_config.normalize_scope(
            keywords=["AI", "AI", " AI "],
            scope_kind="cities",
            cities=["北京"],
            pages_per_combination=3,
        )
        self.assertEqual(scope.combination_count, 1)
        self.assertEqual(scope.planned_pages, 3)

    def test_duplicate_cities_do_not_increase_pages(self):
        scope = execution_config.normalize_scope(
            keywords=["AI"],
            scope_kind="cities",
            cities=["北京", "北京"],
            pages_per_combination=3,
        )
        self.assertEqual(scope.combination_count, 1)
        self.assertEqual(scope.planned_pages, 3)


# ===========================================================================
# FrozenTaskScope 不可变快照与摘要
# ===========================================================================
class FrozenTaskScopeTests(unittest.TestCase):

    def test_scope_is_immutable(self):
        scope = execution_config.normalize_scope(
            keywords=["AI"],
            scope_kind="cities",
            cities=["北京"],
            pages_per_combination=3,
        )
        with self.assertRaises((AttributeError, TypeError)):
            scope.keywords = ["other"]  # type: ignore[misc]

    def test_scope_digest_is_sha256_hex(self):
        scope = execution_config.normalize_scope(
            keywords=["AI"],
            scope_kind="cities",
            cities=["北京"],
            pages_per_combination=3,
        )
        self.assertRegex(scope.scope_digest, r"^[0-9a-f]{64}$")

    def test_scope_digest_changes_with_different_input(self):
        scope1 = execution_config.normalize_scope(
            keywords=["AI"],
            scope_kind="cities",
            cities=["北京"],
            pages_per_combination=3,
        )
        scope2 = execution_config.normalize_scope(
            keywords=["AI"],
            scope_kind="cities",
            cities=["上海"],
            pages_per_combination=3,
        )
        self.assertNotEqual(scope1.scope_digest, scope2.scope_digest)

    def test_scope_to_dict_round_trip(self):
        scope = execution_config.normalize_scope(
            keywords=["AI", "开发"],
            scope_kind="cities",
            cities=["北京", "上海"],
            pages_per_combination=2,
        )
        data = scope.to_dict()
        restored = execution_config.FrozenTaskScope.from_dict(data)
        self.assertEqual(restored.scope_digest, scope.scope_digest)
        self.assertEqual(restored.keywords, scope.keywords)
        self.assertEqual(restored.cities, scope.cities)
        self.assertEqual(restored.planned_pages, scope.planned_pages)
        self.assertEqual(restored.task_size, scope.task_size)

    def test_scope_digest_excludes_digest_field(self):
        scope = execution_config.normalize_scope(
            keywords=["AI"],
            scope_kind="cities",
            cities=["北京"],
            pages_per_combination=3,
        )
        canonical = json.loads(scope.canonical_json())
        self.assertNotIn("scope_digest", canonical)

    def test_from_dict_rejects_tampered_digest(self):
        scope = execution_config.normalize_scope(
            keywords=["AI"],
            scope_kind="cities",
            cities=["北京"],
            pages_per_combination=3,
        )
        data = scope.to_dict()
        data["scope_digest"] = "tampered"
        with self.assertRaises(ValueError):
            execution_config.FrozenTaskScope.from_dict(data)


# ===========================================================================
# 预览接口 (后端权威规范化)
# ===========================================================================
class ScopePreviewTests(unittest.TestCase):
    """对应 HTTP API POST /api/search-scope/preview 的纯函数预览。"""

    def test_preview_returns_normalized_scope_and_dedup_info(self):
        result = execution_config.preview_scope(
            keywords=["AI应用开发", " ai应用开发 "],
            scope_kind="cities",
            cities=["东莞", "东莞市"],
            pages_per_combination=3,
        )
        self.assertIn("scope", result)
        self.assertIn("deduplicated", result)
        self.assertEqual(result["scope"]["keywords"], ["AI应用开发"])
        self.assertEqual(result["scope"]["cities"], ["东莞"])
        self.assertEqual(result["scope"]["planned_pages"], 3)
        self.assertEqual(result["scope"]["task_size"], "small")

    def test_preview_rejects_nationwide_with_cities(self):
        with self.assertRaises(ValueError):
            execution_config.preview_scope(
                keywords=["AI"],
                scope_kind="nationwide",
                cities=["北京"],
                pages_per_combination=1,
            )

    def test_preview_rejects_empty_keywords(self):
        with self.assertRaises(ValueError):
            execution_config.preview_scope(
                keywords=[],
                scope_kind="cities",
                cities=["北京"],
                pages_per_combination=1,
            )

    def test_preview_rejects_non_positive_pages(self):
        with self.assertRaises(ValueError):
            execution_config.preview_scope(
                keywords=["AI"],
                scope_kind="cities",
                cities=["北京"],
                pages_per_combination=0,
            )


# ===========================================================================
# 模式选择不应改变 pages (FR-009)
# ===========================================================================
class ModeSelectionDoesNotTouchPagesTests(unittest.TestCase):
    """FR-009: 稳定、平衡、极限、自定义设置不改变 pages。"""

    def test_mode_config_snapshot_excludes_pages(self):
        for mode in ("stable", "balanced", "extreme"):
            config = execution_config.get_mode_config(mode, task_size="small")
            self.assertNotIn("pages", config.to_dict() if hasattr(config, "to_dict") else config)


if __name__ == "__main__":
    unittest.main()
