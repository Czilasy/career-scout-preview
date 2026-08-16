"""B054 地点目录单元测试：静态加载、运行时兜底、校验与刷新入口。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webui import location_catalog
from webui.location_catalog import (
    LocationCatalogUnavailable,
    get_districts,
    refresh_static_catalogs,
    reset_catalog_cache,
    validate_business,
)


class LocationCatalogTests(unittest.TestCase):
    def setUp(self):
        reset_catalog_cache()

    def tearDown(self):
        reset_catalog_cache()

    def test_boss_static_catalog_contains_shanghai_districts(self):
        districts = get_districts("boss", "上海")
        self.assertTrue(districts)
        names = [row["name"] for row in districts]
        self.assertIn("浦东新区", names)
        pudong = next(row for row in districts if row["name"] == "浦东新区")
        self.assertTrue(pudong["children"])

    def test_zhilian_static_catalog_contains_beijing_districts(self):
        districts = get_districts("zhilian", "北京")
        names = [row["name"] for row in districts]
        self.assertIn("朝阳区", names)
        for row in districts:
            self.assertEqual(row["children"], [])

    def test_runtime_fallback_when_static_missing(self):
        fixture = [{"code": "310115", "name": "浦东新区", "children": []}]
        with mock.patch.object(location_catalog, "_static_map", return_value={}), \
                mock.patch.object(
                    location_catalog, "fetch_boss_districts", return_value=fixture,
                ) as fetcher:
            districts = get_districts("boss", "上海")
        self.assertEqual(districts, fixture)
        fetcher.assert_called_once_with("101020100")

    def test_no_district_data_returns_empty(self):
        with mock.patch.object(location_catalog, "_static_map", return_value={}), \
                mock.patch.object(
                    location_catalog, "fetch_zhilian_districts", return_value=[],
                ):
            districts = get_districts("zhilian", "北京")
        self.assertEqual(districts, [])

    def test_unavailable_propagates(self):
        def boom(*args, **kwargs):
            raise LocationCatalogUnavailable("network down")

        with mock.patch.object(location_catalog, "_static_map", return_value={}), \
                mock.patch.object(location_catalog, "_runtime_fetch", side_effect=boom):
            with self.assertRaises(LocationCatalogUnavailable):
                get_districts("boss", "上海")

    def test_zhilian_business_validation_rejected(self):
        self.assertFalse(validate_business("zhilian", "北京", "2006", "999"))

    def test_refresh_static_catalogs_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            boss_path = Path(tmp) / "boss.json"
            zhilian_path = Path(tmp) / "zhilian.json"
            with mock.patch.object(location_catalog, "BOSS_FILE", boss_path), \
                    mock.patch.object(location_catalog, "ZHILIAN_FILE", zhilian_path), \
                    mock.patch.object(
                        location_catalog, "fetch_boss_hot_cities",
                        return_value=[{"code": "101020100", "name": "上海"}],
                    ), \
                    mock.patch.object(
                        location_catalog, "fetch_boss_districts",
                        return_value=[{"code": "310115", "name": "浦东新区", "children": []}],
                    ), \
                    mock.patch.object(
                        location_catalog, "fetch_zhilian_catalog",
                        return_value={"530": [{"code": "2006", "name": "朝阳区", "children": []}]},
                    ):
                paths = refresh_static_catalogs()
            self.assertTrue(paths["boss"].is_file())
            self.assertTrue(paths["zhilian"].is_file())
            boss_payload = json.loads(boss_path.read_text(encoding="utf-8"))
            self.assertEqual(boss_payload["cities"][0]["code"], "101020100")
            zhilian_payload = json.loads(zhilian_path.read_text(encoding="utf-8"))
            self.assertEqual(zhilian_payload["cities"][0]["code"], "530")


if __name__ == "__main__":
    unittest.main()
