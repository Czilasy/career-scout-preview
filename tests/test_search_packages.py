"""通用搜索配置包测试（Spec 044 B100）。

分三层：
- 存储域：CRUD、事务边界、损坏 JSON 拒绝（`webui.store_search_packages`）；
- 服务域：归一化、完整性/版本校验、默认名称与领域错误（`webui.search_packages`）；
- HTTP：POST/GET/PATCH/DELETE 路由与错误映射（US1-US4 追加）。
"""

import json
import pathlib
import sys
import tempfile
import unittest

from webui.app import create_app
from webui.search_packages import SearchPackageError, SearchPackageService
from webui.store import TaskStore
from webui.store_search_packages import SearchPackageCorruptError


def valid_payload(**overrides) -> dict:
    payload = {
        "name": "",
        "payloadVersion": 1,
        "keywords": {
            "candidates": [
                {"word": "产品经理", "recommended": True},
                {"word": "项目经理", "recommended": False},
            ],
            "selected": ["产品经理"],
            "custom": "",
        },
        "city": {"text": "上海", "custom": ""},
        "profile": {"summary": "3 年 B 端产品经验", "facts": {"experience_years": 3, "core_skills": ["PRD"]}},
    }
    payload.update(overrides)
    return payload


class SearchPackageTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        self.service = SearchPackageService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def _row(self, package_id):
        with self.store._connection() as conn:
            return conn.execute(
                "SELECT * FROM search_packages WHERE id = ?", (package_id,),
            ).fetchone()


class SearchPackageStoreTests(SearchPackageTestCase):
    """T005：存储域 CRUD、事务边界与损坏快照拒绝。"""

    def test_create_reads_back_with_generated_identity_and_timestamps(self):
        created = self.store.create_search_package(
            name="产品经理 · 上海",
            payload_version=1,
            keywords={"candidates": [], "selected": ["产品经理"], "custom": ""},
            city={"text": "上海", "custom": ""},
            profile_summary="画像",
            profile_facts={"years": 3},
        )
        self.assertTrue(created["id"])
        self.assertEqual(created["name"], "产品经理 · 上海")
        self.assertEqual(created["payload_version"], 1)
        self.assertEqual(created["keywords"]["selected"], ["产品经理"])
        self.assertEqual(created["profile_facts"], {"years": 3})
        self.assertTrue(created["created_at"])
        self.assertEqual(created["created_at"], created["updated_at"])

        fetched = self.store.get_search_package(created["id"])
        self.assertEqual(fetched["name"], created["name"])
        self.assertEqual(fetched["profile_summary"], "画像")
        self.assertEqual(fetched["city"], {"text": "上海", "custom": ""})

    def test_get_missing_package_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.store.get_search_package("does-not-exist")

    def test_list_returns_summaries_most_recently_updated_first(self):
        first = self.store.create_search_package(
            name="第一套", payload_version=1,
            keywords={"candidates": [], "selected": [], "custom": ""},
            city={"text": "", "custom": ""}, profile_summary="", profile_facts={},
        )
        second = self.store.create_search_package(
            name="第二套", payload_version=1,
            keywords={"candidates": [], "selected": [], "custom": ""},
            city={"text": "", "custom": ""}, profile_summary="", profile_facts={},
        )
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE search_packages SET updated_at = '2026-01-01T00:00:00+08:00' WHERE id = ?",
                (first["id"],),
            )
            conn.execute(
                "UPDATE search_packages SET updated_at = '2026-02-01T00:00:00+08:00' WHERE id = ?",
                (second["id"],),
            )
        items = self.store.list_search_packages()
        self.assertEqual([item["id"] for item in items], [second["id"], first["id"]])
        self.assertEqual(
            set(items[0]), {"id", "name", "created_at", "updated_at"},
        )

    def test_rename_changes_only_name_and_updated_at(self):
        created = self.store.create_search_package(
            name="原名", payload_version=1,
            keywords={"candidates": [], "selected": ["词"], "custom": "草稿"},
            city={"text": "广州", "custom": ""}, profile_summary="画像", profile_facts={"a": 1},
        )
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE search_packages SET updated_at = '2026-01-01T00:00:00+08:00' WHERE id = ?",
                (created["id"],),
            )
        renamed = self.store.rename_search_package(created["id"], "改名后")
        before = self._row(created["id"])
        self.assertEqual(renamed["name"], "改名后")
        self.assertEqual(renamed["keywords"], created["keywords"])
        self.assertEqual(renamed["city"], created["city"])
        self.assertEqual(renamed["profile_summary"], "画像")
        self.assertEqual(renamed["profile_facts"], {"a": 1})
        self.assertEqual(before["payload_version"], 1)

    def test_rename_missing_package_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.store.rename_search_package("missing", "x")

    def test_delete_removes_only_target(self):
        keep = self.store.create_search_package(
            name="保留", payload_version=1,
            keywords={"candidates": [], "selected": [], "custom": ""},
            city={"text": "", "custom": ""}, profile_summary="", profile_facts={},
        )
        drop = self.store.create_search_package(
            name="删除", payload_version=1,
            keywords={"candidates": [], "selected": [], "custom": ""},
            city={"text": "", "custom": ""}, profile_summary="", profile_facts={},
        )
        self.store.delete_search_package(drop["id"])
        self.assertIsNone(self._row(drop["id"]))
        self.assertIsNotNone(self._row(keep["id"]))

    def test_delete_missing_package_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.store.delete_search_package("missing")

    def test_corrupt_json_is_rejected_not_repaired(self):
        created = self.store.create_search_package(
            name="坏包", payload_version=1,
            keywords={"candidates": [], "selected": [], "custom": ""},
            city={"text": "", "custom": ""}, profile_summary="", profile_facts={},
        )
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE search_packages SET keywords_json = '{not json' WHERE id = ?",
                (created["id"],),
            )
        with self.assertRaises(SearchPackageCorruptError):
            self.store.get_search_package(created["id"])

    def test_failed_create_leaves_no_partial_row(self):
        # 传入不可 JSON 序列化的对象：写事务必须整体失败且不留下半包。
        with self.assertRaises(TypeError):
            self.store.create_search_package(
                name="新名", payload_version=1,
                keywords={"candidates": [], "selected": [], "custom": ""},
                city={"text": "", "custom": ""}, profile_summary="",
                profile_facts={"bad": object()},
            )
        with self.store._connection() as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM search_packages").fetchone()[0]
        self.assertEqual(remaining, 0)


class SearchPackageServiceTests(SearchPackageTestCase):
    """T005/T007：归一化、版本与完整性校验、默认名称与领域错误。"""

    def test_default_name_uses_first_selected_keyword_and_city(self):
        package = self.service.create_package(valid_payload())
        self.assertEqual(package["name"], "产品经理 · 上海")

    def test_default_name_falls_back_when_keywords_and_city_empty(self):
        package = self.service.create_package(valid_payload(
            keywords={"candidates": [], "selected": [], "custom": ""},
            city={"text": "", "custom": ""},
        ))
        self.assertEqual(package["name"], "常用搜索配置")

    def test_default_name_uses_candidate_when_nothing_selected(self):
        package = self.service.create_package(valid_payload(
            keywords={
                "candidates": [{"word": "数据分析", "recommended": True}],
                "selected": [], "custom": "",
            },
            city={"text": "杭州", "custom": ""},
        ))
        self.assertEqual(package["name"], "数据分析 · 杭州")

    def test_explicit_name_is_trimmed_and_kept(self):
        package = self.service.create_package(valid_payload(name="  我的配置  "))
        self.assertEqual(package["name"], "我的配置")

    def test_name_over_eighty_chars_is_rejected(self):
        with self.assertRaises(SearchPackageError) as ctx:
            self.service.create_package(valid_payload(name="长" * 81))
        self.assertEqual(ctx.exception.code, "invalid_name")

    def test_unknown_payload_version_is_rejected(self):
        with self.assertRaises(SearchPackageError) as ctx:
            self.service.create_package(valid_payload(payloadVersion=2))
        self.assertEqual(ctx.exception.code, "invalid_package")

    def test_missing_sections_are_rejected(self):
        for broken in (
            {"keywords": None},
            {"city": None},
            {"profile": None},
            {"profile": {"summary": "画像", "facts": ["not", "an", "object"]}},
            {"profile": {"summary": 42, "facts": {}}},
            {"keywords": {"candidates": "x", "selected": [], "custom": ""}},
        ):
            with self.subTest(broken=broken):
                with self.assertRaises(SearchPackageError) as ctx:
                    self.service.create_package(valid_payload(**broken))
                self.assertEqual(ctx.exception.code, "invalid_package")

    def test_keyword_fields_are_trimmed_deduped_and_ordered(self):
        package = self.service.create_package(valid_payload(
            keywords={
                "candidates": [
                    {"word": "  产品经理 ", "recommended": "yes"},
                    {"word": "产品经理", "recommended": False},
                    {"word": "", "recommended": True},
                    {"word": "运营", "recommended": False},
                ],
                "selected": [" 产品经理 ", "产品经理", "", "运营"],
                "custom": " 未提交 ",
            },
        ))
        self.assertEqual(
            package["keywords"]["candidates"],
            [
                {"word": "产品经理", "recommended": True},
                {"word": "运营", "recommended": False},
            ],
        )
        self.assertEqual(package["keywords"]["selected"], ["产品经理", "运营"])
        self.assertEqual(package["keywords"]["custom"], " 未提交 ")

    def test_platform_and_filter_fields_never_reach_storage_or_projection(self):
        package = self.service.create_package(valid_payload(
            platform="boss",
            filterValues={"salary": ["20-30K"]},
            profileId="p-1",
        ))
        self.assertNotIn("platform", package)
        self.assertNotIn("filterValues", package)
        row = self._row(package["id"])
        stored = json.loads(row["keywords_json"])
        self.assertNotIn("platform", stored)
        self.assertNotIn("filterValues", stored)
        with self.store._connection() as conn:
            columns = {
                item["name"] for item in conn.execute("PRAGMA table_info(search_packages)")
            }
        self.assertNotIn("platform", columns)
        self.assertNotIn("filter_values_json", columns)

    def test_rename_keeps_content_and_rejects_bad_names(self):
        created = self.service.create_package(valid_payload())
        renamed = self.service.rename_package(created["id"], "新名字")
        self.assertEqual(renamed["name"], "新名字")
        self.assertEqual(renamed["keywords"], created["keywords"])
        self.assertEqual(renamed["profile"], created["profile"])
        for bad in ("", "   ", "长" * 81):
            with self.subTest(name=bad):
                with self.assertRaises(SearchPackageError) as ctx:
                    self.service.rename_package(created["id"], bad)
                self.assertEqual(ctx.exception.code, "invalid_name")

    def test_delete_reports_not_found_for_missing_package(self):
        created = self.service.create_package(valid_payload())
        self.service.delete_package(created["id"])
        self.assertEqual(self.service.list_packages(), [])
        with self.assertRaises(SearchPackageError) as ctx:
            self.service.delete_package(created["id"])
        self.assertEqual(ctx.exception.code, "package_not_found")

    def test_get_rejects_corrupt_json_with_unusable_code(self):
        created = self.service.create_package(valid_payload())
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE search_packages SET keywords_json = '[]' WHERE id = ?",
                (created["id"],),
            )
        with self.assertRaises(SearchPackageError) as ctx:
            self.service.get_package(created["id"])
        self.assertEqual(ctx.exception.code, "package_unusable")
        self.assertNotIn("keywords_json", str(ctx.exception))

    def test_get_rejects_unknown_stored_payload_version(self):
        created = self.service.create_package(valid_payload())
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE search_packages SET payload_version = 99 WHERE id = ?",
                (created["id"],),
            )
        with self.assertRaises(SearchPackageError) as ctx:
            self.service.get_package(created["id"])
        self.assertEqual(ctx.exception.code, "package_unusable")

    def test_get_rejects_non_object_profile_facts(self):
        created = self.service.create_package(valid_payload())
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE search_packages SET profile_facts_json = '\"text\"' WHERE id = ?",
                (created["id"],),
            )
        with self.assertRaises(SearchPackageError) as ctx:
            self.service.get_package(created["id"])
        self.assertEqual(ctx.exception.code, "package_unusable")

    def test_profile_facts_use_explicit_resume_fact_whitelist(self):
        package = self.service.create_package(valid_payload(
            profile={
                "summary": "画像",
                "facts": {
                    "core_skills": ["Python"],
                    "platform": "boss",
                    "filterValues": {"salary": ["20-30K"]},
                    "resumeAnalysis": {"fields": {"keyword": ["Python"]}},
                    "employment_history": [{
                        "company": "甲公司",
                        "role": "后端",
                        "district_code": "310104",
                    }],
                    "education_history": [{"school": "甲大学", "major": "计算机", "area_code": "3101"}],
                },
            },
        ))
        self.assertEqual(
            package["profile"]["facts"],
            {
                "core_skills": ["Python"],
                "employment_history": [{"company": "甲公司", "role": "后端"}],
                "education_history": [{"school": "甲大学", "major": "计算机"}],
                "companies": ["甲公司"],
            },
        )

    def test_stored_profile_facts_with_forbidden_nested_data_are_unusable(self):
        created = self.store.create_search_package(
            name="越界包",
            payload_version=1,
            keywords={"candidates": [], "selected": [], "custom": ""},
            city={"text": "", "custom": ""},
            profile_summary="画像",
            profile_facts={"core_skills": ["Python"], "filterValues": {"salary": ["20-30K"]}},
        )
        with self.assertRaises(SearchPackageError) as ctx:
            self.service.get_package(created["id"])
        self.assertEqual(ctx.exception.code, "package_unusable")

    def test_list_projection_has_no_platform_or_filter_fields(self):
        self.service.create_package(valid_payload())
        items = self.service.list_packages()
        self.assertEqual(len(items), 1)
        self.assertEqual(set(items[0]), {"id", "name", "createdAt", "updatedAt"})

    def test_full_projection_matches_contract(self):
        package = self.service.create_package(valid_payload())
        self.assertEqual(
            set(package),
            {
                "id", "name", "payloadVersion", "keywords", "city",
                "profile", "createdAt", "updatedAt",
            },
        )
        self.assertEqual(package["payloadVersion"], 1)
        self.assertEqual(package["profile"]["summary"], "3 年 B 端产品经验")
        self.assertEqual(package["profile"]["facts"], {
            "experience_years": 3.0,
            "experience_years_source": "resume_explicit",
            "core_skills": ["PRD"],
        })


class SearchPackageApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.root / "results"),
            "DB_PATH": str(self.root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()


class SearchPackageApiSaveTests(SearchPackageApiTestCase):
    """T008 [US1]：每次显式保存都创建独立配置包。"""

    def test_post_creates_package_and_returns_full_projection(self):
        resp = self.client.post("/api/search-packages", json=valid_payload())
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertTrue(body["id"])
        self.assertEqual(body["payloadVersion"], 1)
        self.assertEqual(body["name"], "产品经理 · 上海")
        self.assertEqual(body["city"], {"text": "上海", "custom": ""})
        self.assertEqual(body["profile"]["facts"], {
            "experience_years": 3.0,
            "experience_years_source": "resume_explicit",
            "core_skills": ["PRD"],
        })
        self.assertNotIn("platform", json.dumps(body, ensure_ascii=False))

    def test_post_with_empty_name_generates_editable_default_name(self):
        resp = self.client.post("/api/search-packages", json=valid_payload(name=""))
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json()["name"], "产品经理 · 上海")

    def test_post_again_creates_independent_package(self):
        first = self.client.post("/api/search-packages", json=valid_payload()).get_json()
        second = self.client.post(
            "/api/search-packages",
            json=valid_payload(name="另一套配置", city={"text": "北京", "custom": ""}),
        ).get_json()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.list_search_packages()), 2)
        original = self.store.get_search_package(first["id"])
        self.assertEqual(original["city"]["text"], "上海", "原包不得被新保存改写")
        self.assertEqual(
            self.store.get_search_package(second["id"])["name"], "另一套配置",
        )

    def test_put_is_not_supported(self):
        created = self.client.post("/api/search-packages", json=valid_payload()).get_json()
        resp = self.client.put(
            f"/api/search-packages/{created['id']}", json=valid_payload(name="不应更新"),
        )
        self.assertEqual(resp.status_code, 405)

    def test_post_invalid_snapshot_returns_400_invalid_package(self):
        resp = self.client.post(
            "/api/search-packages", json=valid_payload(keywords=None),
        )
        self.assertEqual(resp.status_code, 400)
        error = resp.get_json()["error"]
        self.assertEqual(error["code"], "invalid_package")
        self.assertIn("关键词", error["message"])

    def test_post_over_long_name_returns_400_invalid_name(self):
        resp = self.client.post(
            "/api/search-packages", json=valid_payload(name="长" * 81),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"]["code"], "invalid_name")

    def test_write_requires_session_token(self):
        anonymous = self.app.test_client()
        resp = anonymous.post("/api/search-packages", json=valid_payload())
        self.assertEqual(resp.status_code, 403)

    def test_error_body_never_leaks_sql_or_paths(self):
        resp = self.client.post(
            "/api/search-packages", json=valid_payload(payloadVersion=7),
        )
        raw = resp.get_data(as_text=True)
        for leaked in ("SELECT", "INSERT", "sqlite", "webui.db", "Traceback"):
            self.assertNotIn(leaked, raw)


class SearchPackageApiLoadTests(SearchPackageApiTestCase):
    """T016 [US2]：列表与单包读取。"""

    def _create(self, **overrides) -> dict:
        resp = self.client.post("/api/search-packages", json=valid_payload(**overrides))
        self.assertEqual(resp.status_code, 201)
        return resp.get_json()

    def test_get_list_returns_all_packages_most_recently_updated_first(self):
        first = self._create(name="第一套")
        second = self._create(name="第二套")
        items = self.client.get("/api/search-packages").get_json()["items"]
        self.assertEqual({item["id"] for item in items}, {first["id"], second["id"]})
        self.assertEqual(
            set(items[0]), {"id", "name", "createdAt", "updatedAt"},
        )

        # 名称管理会推进更新时间；配置内容保存始终通过 POST 新增。
        self.client.patch(
            f"/api/search-packages/{first['id']}/name",
            json={"name": "第一套（改）"},
        )
        items = self.client.get("/api/search-packages").get_json()["items"]
        self.assertEqual(items[0]["id"], first["id"])
        self.assertEqual(items[0]["name"], "第一套（改）")

    def test_get_list_empty_returns_empty_items(self):
        self.assertEqual(
            self.client.get("/api/search-packages").get_json(), {"items": []},
        )

    def test_get_single_returns_complete_projection_without_platform(self):
        created = self._create()
        resp = self.client.get(f"/api/search-packages/{created['id']}")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(
            set(body),
            {
                "id", "name", "payloadVersion", "keywords", "city",
                "profile", "createdAt", "updatedAt",
            },
        )
        self.assertEqual(body["keywords"]["selected"], ["产品经理"])
        self.assertEqual(body["profile"]["facts"], {
            "experience_years": 3.0,
            "experience_years_source": "resume_explicit",
            "core_skills": ["PRD"],
        })
        self.assertNotIn("platform", resp.get_data(as_text=True))

    def test_same_package_is_returned_without_platform_variants(self):
        created = self._create()
        with_platform = self.client.get(
            f"/api/search-packages/{created['id']}?platform=zhilian",
        ).get_json()
        without = self.client.get(
            f"/api/search-packages/{created['id']}",
        ).get_json()
        self.assertEqual(with_platform, without, "同一套配置不得按平台返回不同内容")

    def test_get_single_missing_returns_404(self):
        resp = self.client.get("/api/search-packages/missing-id")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"]["code"], "package_not_found")

    def test_corrupt_package_returns_409_package_unusable(self):
        created = self._create()
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE search_packages SET city_json = 'not-json' WHERE id = ?",
                (created["id"],),
            )
        resp = self.client.get(f"/api/search-packages/{created['id']}")
        self.assertEqual(resp.status_code, 409)
        error = resp.get_json()["error"]
        self.assertEqual(error["code"], "package_unusable")
        self.assertEqual(error["message"], "这套配置无法完整读取，请重新保存")
        raw = resp.get_data(as_text=True)
        self.assertNotIn("not-json", raw)
        self.assertNotIn("city_json", raw)

    def test_read_requires_session_token(self):
        created = self._create()
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/api/search-packages").status_code, 403)
        self.assertEqual(
            anonymous.get(f"/api/search-packages/{created['id']}").status_code, 403,
        )


class SearchPackageUnusableTests(SearchPackageApiTestCase):
    """T029 [US4]：损坏、缺字段、错类型与未知版本的包一律整包判不可用。"""

    def _create(self) -> dict:
        resp = self.client.post("/api/search-packages", json=valid_payload())
        self.assertEqual(resp.status_code, 201)
        return resp.get_json()

    def _break_row(self, package_id: str, column: str, value: str) -> None:
        with self.store._connection() as conn:
            conn.execute(
                f"UPDATE search_packages SET {column} = ? WHERE id = ?",
                (value, package_id),
            )

    def _assert_unusable(self, package_id: str) -> None:
        resp = self.client.get(f"/api/search-packages/{package_id}")
        self.assertEqual(resp.status_code, 409)
        error = resp.get_json()["error"]
        self.assertEqual(error["code"], "package_unusable")
        self.assertEqual(error["message"], "这套配置无法完整读取，请重新保存")
        raw = resp.get_data(as_text=True)
        for leaked in (
            "SELECT", "UPDATE", "sqlite", "webui.db", "Traceback",
            "keywords_json", "profile_facts_json", "city_json",
            "不是 JSON", "not-json", "文本",
        ):
            self.assertNotIn(leaked, raw, f"错误响应泄露了 {leaked}")

    def test_corrupt_json_is_unusable(self):
        created = self._create()
        self._break_row(created["id"], "keywords_json", "{不是 JSON")
        self._assert_unusable(created["id"])

    def test_missing_required_field_is_unusable(self):
        created = self._create()
        self._break_row(created["id"], "keywords_json", '{"custom": ""}')
        self._assert_unusable(created["id"])

    def test_wrong_type_is_unusable(self):
        created = self._create()
        self._break_row(created["id"], "keywords_json", "[]")
        self._assert_unusable(created["id"])
        second = self._create()
        self._break_row(second["id"], "profile_facts_json", '"文本"')
        self._assert_unusable(second["id"])

    def test_unknown_payload_version_is_unusable(self):
        created = self._create()
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE search_packages SET payload_version = 2 WHERE id = ?",
                (created["id"],),
            )
        self._assert_unusable(created["id"])

    def test_unusable_package_stays_in_list_but_never_loads_partially(self):
        created = self._create()
        self._break_row(created["id"], "city_json", "not-json")
        items = self.client.get("/api/search-packages").get_json()["items"]
        self.assertEqual([item["id"] for item in items], [created["id"]])
        self._assert_unusable(created["id"])


class SearchPackageApiManageTests(SearchPackageApiTestCase):
    """T024 [US3]：选择框内的重命名与删除。"""

    def _create(self, **overrides) -> dict:
        resp = self.client.post("/api/search-packages", json=valid_payload(**overrides))
        self.assertEqual(resp.status_code, 201)
        return resp.get_json()

    def test_patch_name_changes_only_name(self):
        created = self._create()
        resp = self.client.patch(
            f"/api/search-packages/{created['id']}/name", json={"name": "改名后"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["name"], "改名后")
        self.assertEqual(body["keywords"], created["keywords"])
        self.assertEqual(body["city"], created["city"])
        self.assertEqual(body["profile"], created["profile"])
        self.assertEqual(body["createdAt"], created["createdAt"])
        self.assertGreater(body["updatedAt"], created["updatedAt"])

    def test_patch_name_rejects_empty_and_over_long_names(self):
        created = self._create()
        for bad in ("", "   ", "长" * 81):
            with self.subTest(name=bad):
                resp = self.client.patch(
                    f"/api/search-packages/{created['id']}/name", json={"name": bad},
                )
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.get_json()["error"]["code"], "invalid_name")
        self.assertEqual(
            self.client.get(f"/api/search-packages/{created['id']}").get_json()["name"],
            created["name"],
            "非法改名不得改动配置包",
        )

    def test_patch_name_missing_package_returns_404(self):
        resp = self.client.patch(
            "/api/search-packages/missing-id/name", json={"name": "任意"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"]["code"], "package_not_found")

    def test_delete_removes_only_target_and_returns_204(self):
        keep = self._create(name="保留")
        drop = self._create(name="删除")
        resp = self.client.delete(f"/api/search-packages/{drop['id']}")
        self.assertEqual(resp.status_code, 204)
        items = self.client.get("/api/search-packages").get_json()["items"]
        self.assertEqual([item["id"] for item in items], [keep["id"]])
        self.assertEqual(
            self.client.get(f"/api/search-packages/{drop['id']}").status_code, 404,
        )

    def test_delete_does_not_cascade_to_other_data(self):
        profile = self.store.create_profile("删除配置包不该动画像")
        created = self._create()
        self.assertEqual(self.client.delete(f"/api/search-packages/{created['id']}").status_code, 204)
        self.assertEqual(self.store.get_profile(profile["id"])["name"], "删除配置包不该动画像")

    def test_delete_missing_package_returns_404(self):
        resp = self.client.delete("/api/search-packages/missing-id")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"]["code"], "package_not_found")

    def test_write_methods_require_session_token(self):
        created = self._create()
        anonymous = self.app.test_client()
        self.assertEqual(
            anonymous.patch(
                f"/api/search-packages/{created['id']}/name", json={"name": "x"},
            ).status_code,
            403,
        )
        self.assertEqual(
            anonymous.delete(f"/api/search-packages/{created['id']}").status_code, 403,
        )


if __name__ == "__main__":
    unittest.main()
