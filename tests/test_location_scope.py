"""B054 地点范围测试：规范化、组合展开、参数翻译与摘要。"""
import unittest
from unittest import mock

from webui.location_scope import (
    build_boss_filters,
    build_zhilian_city_snapshot,
    expand_location_combinations,
    location_summary,
    normalize_locations,
)
from webui.pipeline_exec import _combo_hash, run_search
from webui.source import SourceOutcome


def _boss_location(district_name="浦东新区", district_code="310115", business=None):
    location = {
        "platform": "boss",
        "city_name": "上海",
        "city_code": "101020100",
        "district_name": district_name,
        "district_code": district_code,
    }
    if business:
        location["business_name"] = business[0]
        location["business_code"] = business[1]
    return location


class NormalizeLocationTests(unittest.TestCase):
    def test_boss_valid_location_normalized_with_label(self):
        result = normalize_locations("boss", [_boss_location()])
        self.assertEqual(result[0]["label"], "上海 · 浦东新区")

    def test_boss_business_child_validated(self):
        location = _boss_location(business=("北蔡", "154"))
        result = normalize_locations("boss", [location])
        self.assertEqual(result[0]["business_name"], "北蔡")
        self.assertEqual(result[0]["business_code"], "154")

    def test_boss_invalid_business_rejected(self):
        location = _boss_location(business=("不存在", "999999"))
        with self.assertRaises(ValueError) as ctx:
            normalize_locations("boss", [location])
        self.assertIn("商圈/镇不属于所选区", str(ctx.exception))

    def test_zhilian_rejects_business(self):
        location = {
            "platform": "zhilian",
            "city_name": "北京",
            "city_code": "530",
            "district_name": "朝阳区",
            "district_code": "2006",
            "business_name": "国贸",
            "business_code": "1",
        }
        with self.assertRaises(ValueError) as ctx:
            normalize_locations("zhilian", [location])
        self.assertIn("智联不支持商圈/镇", str(ctx.exception))

    def test_duplicate_district_deduplicated(self):
        result = normalize_locations("boss", [_boss_location(), _boss_location()])
        self.assertEqual(len(result), 1)


class ExpandCombinationTests(unittest.TestCase):
    def test_three_districts_expand_three_combos(self):
        params = {
            "platform": "boss",
            "keyword": "Python",
            "city": ["上海"],
            "locations": [
                _boss_location(),
                _boss_location("徐汇区", "310104"),
                _boss_location("黄浦区", "310101"),
            ],
            "pages": 3,
        }
        combos = expand_location_combinations(params)
        self.assertEqual(len(combos), 3)
        keys = [combo["combo_key"] for combo in combos]
        self.assertEqual(keys, [
            "Python|上海·浦东新区",
            "Python|上海·徐汇区",
            "Python|上海·黄浦区",
        ])
        self.assertEqual(len(set(keys)), 3)
        self.assertEqual(combos[0]["display_city"], "上海 · 浦东新区")
        self.assertEqual(
            combos[0]["source_filters"],
            {"multiBusinessDistrict": "310115"},
        )

    def test_no_locations_keeps_old_combo_key(self):
        combos = expand_location_combinations({
            "platform": "boss",
            "keyword": "Python",
            "city": ["上海"],
        })
        self.assertEqual(combos[0]["combo_key"], "Python|上海")
        self.assertEqual(combos[0]["source_filters"], {})

    def test_zhilian_combo_route_city_code(self):
        combos = expand_location_combinations({
            "platform": "zhilian",
            "keyword": "Python",
            "city": ["北京"],
            "locations": [{
                "platform": "zhilian",
                "city_name": "北京",
                "city_code": "530",
                "district_name": "朝阳区",
                "district_code": "2006",
            }],
        })
        self.assertEqual(combos[0]["route_city_code"], "530")
        self.assertEqual(combos[0]["combo_key"], "Python|北京·朝阳区")
        self.assertEqual(combos[0]["source_filters"], {})


class ParameterTranslationTests(unittest.TestCase):
    def test_boss_filter_without_business(self):
        self.assertEqual(
            build_boss_filters(_boss_location()),
            {"multiBusinessDistrict": "310115"},
        )

    def test_boss_filter_with_business(self):
        self.assertEqual(
            build_boss_filters(_boss_location(business=("北蔡", "154"))),
            {"multiBusinessDistrict": "310115:154"},
        )

    def test_zhilian_city_snapshot_uses_district_code(self):
        class Entry:
            name = "北京"
            label = "北京"
            platform_code = "530"
            mapping_version = 2

        snapshot = build_zhilian_city_snapshot({
            "city_name": "北京",
            "city_code": "530",
            "district_name": "朝阳区",
            "district_code": "2006",
        }, Entry())
        self.assertEqual(snapshot["platform_code"], "2006")
        self.assertEqual(snapshot["route_city_code"], "530")

    def test_location_summary_joins_labels(self):
        locations = [
            _boss_location(),
            _boss_location("徐汇区", "310104"),
        ]
        self.assertEqual(location_summary(locations), "上海 · 浦东新区、徐汇区")



class RunSearchComboKeyTests(unittest.TestCase):
    """run_search 使用含地点的 combo_key 与 BOSS source_filters。"""

    def test_combo_hash_includes_source_filters(self):
        plain = _combo_hash("Python", "上海", 1)
        with_district = _combo_hash(
            "Python", "上海", 1, source_filters={"multiBusinessDistrict": "310115"},
        )
        self.assertNotEqual(plain, with_district)

    def test_run_search_uses_location_combo_keys(self):
        params = {
            "platform": "boss",
            "keyword": "Python",
            "city": ["上海"],
            "locations": [
                _boss_location(),
                _boss_location("徐汇区", "310104"),
                _boss_location("黄浦区", "310101"),
            ],
        }
        source = mock.MagicMock()
        source.platform = "boss"
        source.cdp_port = 9222
        source.preflight.return_value = SourceOutcome.success(safe_log="ready")
        source.fetch_list.side_effect = [
            SourceOutcome.success(jobs=[{"job_id": "j1", "source_url": "u1"}], safe_log="ok", input_hash="h1"),
            SourceOutcome.success(jobs=[{"job_id": "j2", "source_url": "u2"}], safe_log="ok", input_hash="h2"),
            SourceOutcome.success(jobs=[{"job_id": "j3", "source_url": "u3"}], safe_log="ok", input_hash="h3"),
        ]
        with mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch("webui.pipeline_exec.time.sleep"):
            result = run_search(
                params, source, pages=1, artifact_dir="tmp",
                sleeper=lambda *args, **kwargs: None,
                close_chrome_on_success=True,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["completed_combos"], [
            "Python|上海·浦东新区",
            "Python|上海·徐汇区",
            "Python|上海·黄浦区",
        ])
        plan_items = [call.args[0] for call in source.fetch_list.call_args_list]
        self.assertEqual(len(plan_items), 3)
        self.assertEqual(plan_items[0]["source_filters"], {"multiBusinessDistrict": "310115"})
        self.assertEqual(plan_items[0]["combo_key"], "Python|上海·浦东新区")

if __name__ == "__main__":

    unittest.main()
