import csv
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from tests.chrome_setup.harness import SCRIPT_PATH, load_module, tempfile_profile, fake_run


class ChromeSetupTests(unittest.TestCase):
    def test_launch_chrome_closes_parent_stderr_handle_after_spawn(self):
        module = load_module()
        spawned = object()

        with tempfile_profile() as paths:
            paths["cdp_profile"].mkdir(parents=True, exist_ok=True)
            with mock.patch.object(module, "DEFAULT_CDP_DATA_DIR", str(paths["cdp_profile"])), \
                    mock.patch.object(module.subprocess, "Popen", return_value=spawned) as popen:
                result = module.launch_chrome(["chrome", "--headless"])
                stderr_handle = popen.call_args.kwargs["stderr"]
                was_closed = stderr_handle.closed
                if not was_closed:
                    stderr_handle.close()

        self.assertIs(result, spawned)
        self.assertTrue(was_closed)

    def test_session_import_requires_explicit_authorization(self):
        module = load_module()
        factory = mock.Mock()

        result = module.import_boss_session(
            source_cdp_port=9223,
            target_cdp_port=9222,
            authorized=False,
            session_factory=factory,
        )

        self.assertEqual(result, {
            "status": "blocked",
            "code": "authorization_required",
            "imported_count": 0,
        })
        factory.assert_not_called()

    def test_session_import_only_transfers_boss_cookies_without_exposing_values(self):
        module = load_module()
        secret_one = "boss-secret-one"
        secret_two = "boss-secret-two"

        class FakeSession:
            def __init__(self, port):
                self.port = port
                self.calls = []
                self.closed = False

            def send(self, method, params=None):
                self.calls.append((method, params or {}))
                if method == "Storage.getCookies":
                    cookies = {
                        9223: [
                            {"name": "wt2", "value": secret_one, "domain": ".zhipin.com", "path": "/", "secure": True, "httpOnly": True},
                            {"name": "lastCity", "value": secret_two, "domain": "www.zhipin.com", "path": "/", "sameSite": "Lax"},
                            {"name": "trap", "value": "must-not-move", "domain": "evilzhipin.com", "path": "/"},
                            {"name": "google", "value": "must-not-move", "domain": ".google.com", "path": "/"},
                        ],
                        9222: [],
                    }[self.port]
                    return {"result": {"cookies": cookies}}
                if method == "Storage.setCookies":
                    return {"result": {}}
                raise AssertionError(method)

            def close(self):
                self.closed = True

        sessions = {}

        def factory(port):
            sessions[port] = FakeSession(port)
            return sessions[port]

        result = module.import_boss_session(
            source_cdp_port=9223,
            target_cdp_port=9222,
            authorized=True,
            session_factory=factory,
            login_checker=lambda port: port == 9222,
            target_profile_checker=lambda _port: True,
        )

        self.assertEqual(result, {"status": "completed", "code": "ok", "imported_count": 2})
        set_call = next(call for call in sessions[9222].calls if call[0] == "Storage.setCookies")
        imported = set_call[1]["cookies"]
        self.assertEqual([cookie["domain"] for cookie in imported], [".zhipin.com", "www.zhipin.com"])
        self.assertNotIn("trap", repr(imported))
        self.assertNotIn("google", repr(imported))
        self.assertNotIn(secret_one, repr(result))
        self.assertNotIn(secret_two, repr(result))
        self.assertTrue(sessions[9223].closed)
        self.assertTrue(sessions[9222].closed)

    def test_session_import_rolls_back_target_boss_cookies_when_probe_fails(self):
        module = load_module()

        class FakeSession:
            def __init__(self, port):
                self.port = port
                self.calls = []

            def send(self, method, params=None):
                params = params or {}
                self.calls.append((method, params))
                if method == "Storage.getCookies":
                    return {"result": {"cookies": {
                        9223: [{"name": "wt2", "value": "new-secret", "domain": ".zhipin.com", "path": "/"}],
                        9222: [
                            {"name": "old", "value": "old-secret", "domain": ".zhipin.com", "path": "/"},
                            {"name": "unrelated", "value": "keep", "domain": ".example.com", "path": "/"},
                        ],
                    }[self.port]}}
                if method in ("Storage.setCookies", "Network.deleteCookies"):
                    return {"result": {}}
                raise AssertionError(method)

            def close(self):
                pass

        sessions = {}

        def factory(port):
            sessions[port] = FakeSession(port)
            return sessions[port]

        result = module.import_boss_session(
            source_cdp_port=9223,
            target_cdp_port=9222,
            authorized=True,
            session_factory=factory,
            login_checker=lambda _port: False,
            target_profile_checker=lambda _port: True,
        )

        self.assertEqual(result, {
            "status": "failed",
            "code": "session_import_unverified",
            "imported_count": 0,
        })
        target_calls = sessions[9222].calls
        deleted = [params for method, params in target_calls if method == "Network.deleteCookies"]
        self.assertEqual(
            {(item["name"], item["domain"], item["path"]) for item in deleted},
            {("wt2", ".zhipin.com", "/"), ("old", ".zhipin.com", "/")},
        )
        set_calls = [params["cookies"] for method, params in target_calls if method == "Storage.setCookies"]
        self.assertEqual(len(set_calls), 2)
        self.assertEqual([cookie["name"] for cookie in set_calls[1]], ["old"])
        self.assertNotIn("unrelated", repr(target_calls))

    def test_session_import_rejects_same_source_and_target_port_before_connecting(self):
        module = load_module()
        factory = mock.Mock()

        result = module.import_boss_session(
            source_cdp_port=9222,
            target_cdp_port=9222,
            authorized=True,
            session_factory=factory,
        )

        self.assertEqual(result, {
            "status": "blocked",
            "code": "source_target_port_conflict",
            "imported_count": 0,
        })
        factory.assert_not_called()

    def test_session_import_rejects_non_dedicated_target_before_connecting(self):
        module = load_module()
        factory = mock.Mock()

        result = module.import_boss_session(
            source_cdp_port=9223,
            target_cdp_port=9222,
            authorized=True,
            session_factory=factory,
            target_profile_checker=lambda _port: False,
        )

        self.assertEqual(result, {
            "status": "blocked",
            "code": "target_not_dedicated_profile",
            "imported_count": 0,
        })
        factory.assert_not_called()

    def test_session_import_rolls_back_and_redacts_probe_exceptions(self):
        module = load_module()
        secret = "probe-secret-must-not-leak"

        class FakeSession:
            def __init__(self, port):
                self.port = port
                self.calls = []

            def send(self, method, params=None):
                params = params or {}
                self.calls.append((method, params))
                if method == "Storage.getCookies":
                    cookies = [{
                        "name": "wt2",
                        "value": secret,
                        "domain": ".zhipin.com",
                        "path": "/",
                    }] if self.port == 9223 else []
                    return {"result": {"cookies": cookies}}
                if method in ("Storage.setCookies", "Network.deleteCookies"):
                    return {"result": {}}
                raise AssertionError(method)

            def close(self):
                pass

        sessions = {}

        def factory(port):
            sessions[port] = FakeSession(port)
            return sessions[port]

        def failing_probe(_port):
            raise RuntimeError(secret)

        result = module.import_boss_session(
            source_cdp_port=9223,
            target_cdp_port=9222,
            authorized=True,
            session_factory=factory,
            login_checker=failing_probe,
            target_profile_checker=lambda _port: True,
        )

        self.assertEqual(result, {
            "status": "failed",
            "code": "session_import_unverified",
            "imported_count": 0,
        })
        self.assertNotIn(secret, repr(result))
        self.assertTrue(any(method == "Network.deleteCookies" for method, _ in sessions[9222].calls))

    def test_session_import_rolls_back_when_cookie_write_raises(self):
        module = load_module()
        secret = "write-secret-must-not-leak"

        class FakeSession:
            def __init__(self, port):
                self.port = port
                self.calls = []

            def send(self, method, params=None):
                params = params or {}
                self.calls.append((method, params))
                if method == "Storage.getCookies":
                    cookies = [{
                        "name": "wt2", "value": secret,
                        "domain": ".zhipin.com", "path": "/",
                    }] if self.port == 9223 else []
                    return {"result": {"cookies": cookies}}
                if method == "Storage.setCookies":
                    raise RuntimeError(secret)
                if method == "Network.deleteCookies":
                    return {"result": {}}
                raise AssertionError(method)

            def close(self):
                pass

        sessions = {}

        def factory(port):
            sessions[port] = FakeSession(port)
            return sessions[port]

        result = module.import_boss_session(
            source_cdp_port=9223,
            target_cdp_port=9222,
            authorized=True,
            session_factory=factory,
            login_checker=lambda _port: True,
            target_profile_checker=lambda _port: True,
        )

        self.assertEqual(result, {
            "status": "failed",
            "code": "session_write_failed",
            "imported_count": 0,
        })
        self.assertNotIn(secret, repr(result))
        self.assertTrue(any(method == "Network.deleteCookies" for method, _ in sessions[9222].calls))

    def test_default_cdp_profile_is_persistent_and_not_default_or_tmp(self):
        module = load_module()

        self.assertNotEqual(module.DEFAULT_CDP_DATA_DIR, module.DEFAULT_PROFILE_DIR)
        self.assertNotIn("/tmp/", module.DEFAULT_CDP_DATA_DIR)
        self.assertTrue(module.DEFAULT_CDP_DATA_DIR.endswith(".career-scout/chrome-profile"))

    def test_default_result_dir_is_persistent_user_state(self):
        module = load_module()

        self.assertNotIn("/tmp/", module.DEFAULT_RESULT_DIR)
        self.assertTrue(module.DEFAULT_RESULT_DIR.endswith(".career-scout/job-result"))
        self.assertTrue(module.default_output_path("jobs").startswith(module.DEFAULT_RESULT_DIR))
        self.assertTrue(module.default_output_path("details").startswith(module.DEFAULT_RESULT_DIR))
        self.assertIn("boss_jobs_", module.default_output_path("jobs"))
        self.assertIn("boss_details_", module.default_output_path("details"))

    def test_default_city_is_shanghai_when_not_provided(self):
        module = load_module()

        self.assertEqual(module.DEFAULT_CITY_INPUT, "上海")
        self.assertEqual(module.resolve_city(module.DEFAULT_CITY_INPUT), ("上海", "101020100"))

    # ----- 本地静态城市码表（data/city_codes.json）-----

    def test_local_city_map_loads_and_valid(self):
        """本地码表能加载、是字典、非空、value 全是数字字符串。"""
        module = load_module()
        name_to_code, code_to_name = module.load_local_city_map()

        self.assertIsInstance(name_to_code, dict)
        self.assertGreater(len(name_to_code), 100, "码表应包含上百个城市")
        for name, code in name_to_code.items():
            self.assertIsInstance(name, str)
            self.assertIsInstance(code, str)
            self.assertTrue(code.isdigit(), f"城市码应为数字字符串: {name}={code!r}")
        # 反向映射一致
        self.assertEqual(code_to_name.get("101020100"), "上海")

    def test_local_city_map_contains_known_cities(self):
        """码表覆盖一线城市 + 三/四线城市（验证是全量，非旧 24 城）。"""
        module = load_module()
        name_to_code, _ = module.load_local_city_map()

        for city in ("全国", "北京", "上海", "深圳"):
            self.assertIn(city, name_to_code, f"缺少常见城市: {city}")
        # 三/四线城市（旧内置码表没有的），证明已扩展到全量
        for tier34 in ("赣州", "洛阳", "临沂", "襄阳"):
            self.assertIn(tier34, name_to_code, f"缺少三四线城市: {tier34}")

    def test_local_city_map_is_superset_of_old_builtin(self):
        """防回归：新静态码表必须 ⊇ 原内置 24 城且码值一致。"""
        module = load_module()
        name_to_code, _ = module.load_local_city_map()

        old_builtin = {
            "全国": "100010000",
            "北京": "101010100", "上海": "101020100", "广州": "101280100",
            "深圳": "101280600", "杭州": "101210100", "成都": "101270100",
            "西安": "101110100", "重庆": "101040100", "南京": "101190100",
            "长沙": "101250100", "福州": "101230100", "武汉": "101200100",
            "合肥": "101220100", "济南": "101120100", "大连": "101070200",
            "青岛": "101120200", "宁波": "101210400", "厦门": "101230200",
            "天津": "101030100", "苏州": "101190400", "郑州": "101180100",
            "东莞": "101281600", "佛山": "101280800", "沈阳": "101070100",
        }
        for name, code in old_builtin.items():
            self.assertEqual(name_to_code.get(name), code,
                             f"原内置城市 {name}={code} 在新码表中缺失或码值不一致")

    def test_city_codes_json_has_structured_format(self):
        """SPEC011 T003: city_codes.json 升级为结构化格式，包含 aliases/enabled/nationwide。"""
        module = load_module()
        import json as _json
        path = module._city_data_path()
        with open(path, "r", encoding="utf-8") as f:
            raw = _json.load(f)

        self.assertIsInstance(raw, dict)
        self.assertIn("cities", raw)
        self.assertIn("nationwide", raw)
        self.assertIn("schema_version", raw)
        self.assertGreaterEqual(raw["schema_version"], 2)

        # nationwide 元数据
        nw = raw["nationwide"]
        self.assertEqual(nw["name"], "全国")
        self.assertEqual(nw["code"], "100010000")
        self.assertTrue(nw.get("enabled", True))

        # 城市条目结构
        cities = raw["cities"]
        self.assertGreater(len(cities), 100)
        for entry in cities:
            self.assertIn("name", entry)
            self.assertIn("code", entry)
            self.assertIn("aliases", entry)
            self.assertIsInstance(entry["aliases"], list)
            if "enabled" in entry:
                self.assertIsInstance(entry["enabled"], bool)

    def test_city_codes_json_contains_explicit_aliases(self):
        """SPEC011 T003: 主要城市有显式注册的别名。"""
        import json as _json
        path = load_module()._city_data_path()
        with open(path, "r", encoding="utf-8") as f:
            raw = _json.load(f)

        city_by_name = {c["name"]: c for c in raw["cities"]}
        # 东莞市 应注册为 东莞 的别名
        self.assertIn("东莞", city_by_name)
        self.assertIn("东莞市", city_by_name["东莞"]["aliases"])
        # 北京 应有 北京市 别名
        self.assertIn("北京市", city_by_name["北京"]["aliases"])

    def test_load_local_city_map_handles_structured_format(self):
        """SPEC011 T003: load_local_city_map 正确解析新结构化格式。"""
        module = load_module()
        # 重置缓存
        module._local_city_map_cache = None
        name_to_code, code_to_name = module.load_local_city_map()

        # 全国仍包含在 name_to_code 中（向后兼容）
        self.assertIn("全国", name_to_code)
        self.assertEqual(name_to_code["全国"], "100010000")
        # 已知城市
        for city in ("北京", "上海", "深圳", "东莞"):
            self.assertIn(city, name_to_code)
        # 码值类型
        for name, code in name_to_code.items():
            self.assertIsInstance(name, str)
            self.assertIsInstance(code, str)

    # ----- resolve_city 三级查询链 -----

    def test_resolve_city_hit_local_map(self):
        """本地静态码表命中（含三四线城市）。"""
        module = load_module()

        for name, code in [("上海", "101020100"), ("赣州", "101240700")]:
            self.assertEqual(module.resolve_city(name), (name, code))

    def test_resolve_city_reverse_lookup(self):
        """用城市码反查中文名。"""
        module = load_module()

        self.assertEqual(module.resolve_city("101020100"), ("上海", "101020100"))
        self.assertEqual(module.resolve_city("101240700"), ("赣州", "101240700"))

    def test_resolve_city_fallback_to_live(self):
        """本地码表没有时降级到运行时拉取（mock）。"""
        module = load_module()

        with mock.patch.object(module, "load_local_city_map",
                               return_value=({}, {})), \
             mock.patch.object(module, "load_live_city_maps",
                               return_value=({"长春": "101060100"},
                                             {"101060100": "长春"})):
            self.assertEqual(module.resolve_city("长春"), ("长春", "101060100"))
            self.assertEqual(module.resolve_city("101060100"), ("长春", "101060100"))

    def test_resolve_city_fallback_to_raw(self):
        """本地和实时都查不到时，原样返回（兼容用户传裸 code）。"""
        module = load_module()

        with mock.patch.object(module, "load_local_city_map",
                               return_value=({}, {})), \
             mock.patch.object(module, "load_live_city_maps",
                               return_value=({}, {})):
            self.assertEqual(module.resolve_city("999999999"), ("999999999", "999999999"))

    def test_resolve_city_empty_input(self):
        module = load_module()

        self.assertEqual(module.resolve_city(""), ("", ""))

    # ----- --list-cities -----

    def test_list_cities_prints_all(self):
        """--list-cities 打印全部城市（用本地码表，mock 掉联网）。"""
        module = load_module()

        with mock.patch.object(module, "load_live_city_maps",
                               return_value=({}, {})):
            with mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
                module.list_cities(keyword=None)
            text = out.getvalue()
        self.assertIn("个城市", text)
        self.assertIn("上海", text)
        self.assertIn("赣州", text)

    def test_list_cities_with_filter(self):
        """关键词过滤只打印匹配的城市。"""
        module = load_module()

        with mock.patch.object(module, "load_live_city_maps",
                               return_value=({}, {})):
            with mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
                module.list_cities(keyword="江")
            text = out.getvalue()
        self.assertIn("江", text)
        self.assertNotIn("上海", text)
        self.assertNotIn("赣州", text)

    def test_list_cities_offline_uses_local(self):
        """联网失败时回退本地静态码表，不报错。"""
        module = load_module()

        with mock.patch.object(module, "load_live_city_maps",
                               return_value=({}, {})):
            with mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
                module.list_cities(keyword=None)
            text = out.getvalue()
        # 本地码表非空时应有输出
        self.assertIn("个城市", text)

    def test_filter_maps_match_current_boss_condition_snapshot(self):
        module = load_module()

        self.assertEqual(
            module.SALARY_MAP,
            {
                "不限": "0",
                "3K以下": "402",
                "3-5K": "403",
                "5-10K": "404",
                "10-20K": "405",
                "20-50K": "406",
                "50K以上": "407",
            },
        )
        self.assertEqual(
            module.EXPERIENCE_MAP,
            {
                "不限": "0",
                "在校生": "108",
                "应届生": "102",
                "经验不限": "101",
                "1年以内": "103",
                "1-3年": "104",
                "3-5年": "105",
                "5-10年": "106",
                "10年以上": "107",
            },
        )
        self.assertEqual(
            module.DEGREE_MAP,
            {
                "不限": "0",
                "初中及以下": "209",
                "中专/中技": "208",
                "高中": "206",
                "大专": "202",
                "本科": "203",
                "硕士": "204",
                "博士": "205",
            },
        )

    def test_login_probe_requires_plaintext_salary(self):
        module = load_module()

        hidden_salary = {"code": 0, "zpData": {"jobList": [{"jobName": "Java", "salaryDesc": ""}]}}
        visible_salary = {"code": 0, "zpData": {"jobList": [{"jobName": "Java", "salaryDesc": "20-40K"}]}}

        self.assertFalse(module.is_logged_in_search_response(hidden_salary))
        self.assertTrue(module.is_logged_in_search_response(visible_salary))
        self.assertFalse(module.is_logged_in_search_response({"code": 7, "zpData": {"jobList": []}}))

    def test_detail_record_preserves_job_id_and_job_link(self):
        module = load_module()
        job = {
            "job_id": "abc123",
            "title": "AI Engineer",
            "boss_name": "Acme",
            "salary": "30-60K",
            "salary_source": "api",
            "location": "上海",
            "tags": "3-5年 | 本科",
            "job_link": "https://www.zhipin.com/job_detail/abc.html",
        }
        extracted = {"tags": ["Python"], "jd": "Build AI agents"}

        detail = module.build_detail_record(job, extracted)

        self.assertEqual(detail["job_id"], "abc123")
        self.assertEqual(detail["job_link"], job["job_link"])
        self.assertEqual(detail["link"], job["job_link"])
        self.assertEqual(detail["salary"], "30-60K")
        self.assertEqual(detail["salary_source"], "api")

    def test_detail_extractor_never_uses_body_text_as_jd_fallback(self):
        module = load_module()

        self.assertNotIn("jd = body.substring", module.EXTRACT_DETAIL_JS)
        self.assertIn("page_text", module.EXTRACT_DETAIL_JS)
        self.assertIn("text.indexOf('职位描述')", module.EXTRACT_DETAIL_JS)

    def test_extract_job_description_removes_header_and_recruiter_footer(self):
        module = load_module()
        description = (
            "公司介绍\n这段属于招聘方发布的岗位正文，应当保留。\n"
            + "负责 AI 产品规划、需求分析、研发协作和上线复盘。\n" * 8
        ).strip()
        page_text = (
            "微信扫码分享 举报\n职位描述\n"
            f"{description}\n"
            "张女士\n今日活跃\n示例公司\n·\n招聘者\n竞争力分析\n"
            "查看完整个人竞争力\nBOSS 安全提示\n公司工商信息\n更多职位"
        )

        jd = module.extract_job_description({"jd": page_text, "page_text": page_text})

        self.assertEqual(jd, description)
        self.assertIn("公司介绍", jd)
        self.assertNotIn("张女士", jd)
        self.assertNotIn("竞争力分析", jd)

    def test_extract_job_description_rejects_login_truncation(self):
        module = load_module()
        page_text = (
            "职位描述\n负责产品规划和需求分析。\n"
            "登录查看完整内容\n招聘者\nBOSS 安全提示"
        )

        with self.assertRaises(module.DetailLoginRequiredError):
            module.extract_job_description({"jd": "", "page_text": page_text})

    def test_extract_job_description_preserves_competitiveness_heading_in_jd(self):
        module = load_module()
        description = (
            "岗位职责\n负责产品规划、需求分析和跨团队项目推进。\n"
            "竞争力分析\n负责持续研究竞品并制定差异化产品策略。\n" * 5
        )

        jd = module.extract_job_description({"jd": f"职位描述\n{description}"})

        self.assertIn("竞争力分析", jd)
        self.assertIn("制定差异化产品策略", jd)

    def test_extract_job_description_removes_trailing_recruiter_card(self):
        module = load_module()
        description = "负责 AI 产品规划、需求分析和跨团队项目推进。\n" * 8
        page_text = (
            f"职位描述\n{description}"
            "李女士\n在线\n示例公司\n·\n招聘专员"
        )

        jd = module.extract_job_description({"jd": page_text, "page_text": page_text})

        self.assertEqual(jd, description.strip())
        self.assertNotIn("李女士", jd)
        self.assertNotIn("招聘专员", jd)

    def test_extract_job_description_removes_recruiter_card_before_safety_footer(self):
        module = load_module()
        description = "负责视觉算法研发、模型部署和业务场景落地。\n" * 8
        page_text = (
            f"职位描述\n{description}"
            "认证资质\n人力资源服务许可证\n"
            "曾先生\n示例猎头\n·\n猎头顾问\n\n"
            "BOSS 安全提示\n公司介绍\n更多职位"
        )

        jd = module.extract_job_description({"jd": page_text, "page_text": page_text})

        self.assertEqual(
            jd,
            f"{description}认证资质\n人力资源服务许可证".strip(),
        )
        self.assertNotIn("曾先生", jd)
        self.assertNotIn("猎头顾问", jd)

    def test_extract_job_description_rejects_navigation_page(self):
        module = load_module()
        page_text = "首页\n职位\n公司\n校园\n无障碍专区\n热门职位\n产品经理"

        with self.assertRaisesRegex(module.DetailExtractionError, "navigation chrome"):
            module.extract_job_description({"jd": "", "page_text": page_text})

    def test_extract_job_description_rejects_short_text(self):
        module = load_module()

        with self.assertRaisesRegex(module.DetailExtractionError, "too short"):
            module.extract_job_description({"jd": "职位描述\n只有一句话"})

    def test_extract_job_description_keeps_jd_with_rate_limit_words(self):
        """真实 JD 正文含“限流/解锁”等词时不得误判成账号限流页。"""
        module = load_module()
        description = (
            "岗位职责\n负责模型调用成本和系统稳定性控制，包括 Token、并发、超时、重试、限流和用量统计。\n"
            "需要处理操作频繁导致的告警，并设计解锁与冻结策略。\n" * 6
        ).strip()
        page_text = f"职位描述\n{description}\n招聘者\nBOSS 安全提示"

        jd = module.extract_job_description({"jd": page_text, "page_text": page_text})

        self.assertIn("限流", jd)
        self.assertIn("解锁", jd)

    def test_extract_job_description_still_rejects_rate_limit_page(self):
        """没有真实 JD 的限流提示页仍必须按限流处理。"""
        module = load_module()
        page_text = "操作频繁，请稍后再试\n您的账号访问受限，请解锁后继续"

        with self.assertRaises(module.DetailRateLimitedError):
            module.extract_job_description({"jd": "", "page_text": page_text})

    def test_detail_url_adds_security_context_without_changing_job_link(self):
        module = load_module()
        job = {
            "job_link": "https://www.zhipin.com/job_detail/abc.html",
            "security_id": "sec value",
            "lid": "lid-123",
        }

        detail_url = module.build_detail_url(job)

        self.assertEqual(job["job_link"], "https://www.zhipin.com/job_detail/abc.html")
        self.assertEqual(
            detail_url,
            "https://www.zhipin.com/job_detail/abc.html?lid=lid-123&securityId=sec+value",
        )

    def test_api_extraction_keeps_detail_context_fields(self):
        module = load_module()

        self.assertIn("security_id: j.securityId", module.FETCH_API_JS_TEMPLATE)
        self.assertIn("lid: j.lid", module.FETCH_API_JS_TEMPLATE)
        self.assertIn("encrypt_job_id: j.encryptJobId", module.FETCH_API_JS_TEMPLATE)

    def test_dom_fallback_is_opt_in(self):
        module = load_module()

        self.assertFalse(module.should_use_dom_fallback([], allow_dom_fallback=False))
        self.assertTrue(module.should_use_dom_fallback([], allow_dom_fallback=True))
        self.assertFalse(module.should_use_dom_fallback([{"title": "Java"}], allow_dom_fallback=True))

    def test_api_job_parser_rejects_error_rows(self):
        module = load_module()

        self.assertEqual(module.parse_api_jobs_eval_value(json.dumps([{"error": 403}])), [])
        self.assertEqual(
            module.parse_api_jobs_eval_value(json.dumps([{"title": "Java", "job_link": "https://example.com"}])),
            [{"title": "Java", "job_link": "https://example.com"}],
        )

    def _probe_payload(self, module, text, status=0):
        """构造 probe eval_js 返回值：{status, text} 包装的 JSON 字符串。"""
        return json.dumps({"status": status, "text": text})

    def test_login_probe_uses_single_request_for_tri_state(self):
        module = load_module()
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._probe_payload(
            module,
            json.dumps({"code": 0, "zpData": {"jobList": [
                {"jobName": "Java", "salaryDesc": "20-40K"},
            ]}}),
        )

        self.assertTrue(module.probe_login_state(cdp, "sid"))
        self.assertEqual(cdp.eval_js.call_count, 1)

    def test_probe_tri_state_logged_in(self):
        module = load_module()
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._probe_payload(
            module,
            json.dumps({"code": 0, "zpData": {"jobList": [
                {"jobName": "Java", "salaryDesc": "20-40K"},
            ]}}),
        )
        self.assertEqual(module.probe_login_state_tri(cdp, "sid"), "logged_in")

    def test_probe_tri_state_not_logged_in_without_plaintext_salary(self):
        module = load_module()
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._probe_payload(
            module,
            json.dumps({"code": 0, "zpData": {"jobList": [
                {"jobName": "Java", "salaryDesc": ""},
            ]}}),
        )
        self.assertEqual(module.probe_login_state_tri(cdp, "sid"), "not_logged_in")

    def test_probe_tri_state_restricted_on_http_error(self):
        module = load_module()
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._probe_payload(module, "", status=403)
        self.assertEqual(module.probe_login_state_tri(cdp, "sid"), "restricted")

    def test_probe_tri_state_restricted_on_risk_text(self):
        module = load_module()
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._probe_payload(
            module, "访问受限，请完成安全验证")
        self.assertEqual(module.probe_login_state_tri(cdp, "sid"), "restricted")

    def test_probe_tri_state_restricted_on_429(self):
        module = load_module()
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._probe_payload(
            module, "操作频繁，请稍后再试", status=429)
        self.assertEqual(module.probe_login_state_tri(cdp, "sid"), "restricted")

    def test_probe_tri_state_not_logged_in_on_401(self):
        module = load_module()
        cdp = mock.Mock()
        cdp.eval_js.return_value = self._probe_payload(module, "", status=401)
        self.assertEqual(module.probe_login_state_tri(cdp, "sid"), "not_logged_in")

    def test_probe_tri_state_empty_probe_is_unknown(self):
        module = load_module()
        cdp = mock.Mock()
        cdp.eval_js.return_value = ""
        self.assertEqual(module.probe_login_state_tri(cdp, "sid"), "unknown")

    def test_check_login_state_unknown_on_cdp_failure(self):
        module = load_module()
        import requests as _requests
        with mock.patch.object(module, "CDPSession",
                               side_effect=_requests.ConnectionError("down")):
            self.assertEqual(module.check_login_state_tri(9333), "unknown")
            self.assertFalse(module.check_login_state(9333))

    def test_find_latest_detail_file_uses_default_result_dir(self):
        module = load_module()
        with tempfile_profile() as paths:
            result_dir = paths["cdp_profile"] / "job-result"
            result_dir.mkdir(parents=True)
            older = result_dir / "boss_details_20260612_1000.json"
            newer = result_dir / "boss_details_20260612_1100.json"
            older.write_text("[]", encoding="utf-8")
            newer.write_text("[]", encoding="utf-8")

            self.assertEqual(module.find_latest_detail_file(str(result_dir)), str(newer))

    def test_existing_detail_loader_prefers_sibling_detail_file(self):
        module = load_module()
        with tempfile_profile() as paths:
            result_dir = paths["cdp_profile"] / "job-result"
            result_dir.mkdir(parents=True)
            list_path = result_dir / "boss_jobs_20260612_1100.json"
            detail_path = result_dir / "boss_details_20260612_1100.json"
            list_path.write_text('{"jobs":[]}', encoding="utf-8")
            detail_path.write_text('[{"job_id":"abc123"}]', encoding="utf-8")

            details = module.load_existing_details(
                input_path=str(list_path),
                detail_output=None,
                result_dir=str(result_dir),
            )

        self.assertEqual(details, [{"job_id": "abc123"}])

    def test_windows_default_paths_use_localappdata(self):
        module = load_module()
        env = {
            "LOCALAPPDATA": r"C:\Users\demo-user\AppData\Local",
            "PROGRAMFILES": r"C:\Program Files",
            "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
        }
        expected_chrome = r"C:\Users\demo-user\AppData\Local\Google\Chrome\Application\chrome.exe"
        with mock.patch.object(module.platform, "system", return_value="Windows"), \
                mock.patch.dict(module.os.environ, env, clear=False), \
                mock.patch.object(module.os.path, "exists", side_effect=lambda p: p == expected_chrome):
            self.assertEqual(module.get_default_chrome_path(), expected_chrome)
            self.assertEqual(
                module.get_default_profile_dir(),
                r"C:\Users\demo-user\AppData\Local\Google\Chrome\User Data",
            )

    def test_windows_process_parsing_matches_user_data_dir_and_cdp_port(self):
        module = load_module()
        if not hasattr(module.subprocess, "CREATE_NO_WINDOW"):
            self.skipTest("仅 Windows 有 CREATE_NO_WINDOW 语义")
        ps_json = json.dumps([{
            "ProcessId": 456,
            "CommandLine": (
                r'"C:\Program Files\Google\Chrome\Application\chrome.exe" '
                r'--remote-debugging-port=9333 '
                r'--user-data-dir="C:\Users\demo-user\.career-scout\chrome-profile"'
            ),
        }])
        with mock.patch.object(module.platform, "system", return_value="Windows"), \
                mock.patch.object(module.subprocess, "run", return_value=type("Completed", (), {"stdout": ps_json, "returncode": 0})()):
            self.assertEqual(
                module.chrome_pids_for_user_data_dir(r"C:\Users\demo-user\.career-scout\chrome-profile"),
                [456],
            )
            self.assertEqual(
                module.chrome_user_data_dirs_for_cdp_port(9333),
                [r"C:\Users\demo-user\.career-scout\chrome-profile"],
            )

    def test_windows_process_parsing_decodes_utf8_non_ascii_paths(self):
        """非 ASCII 项目路径必须按 UTF-8 解码，避免 profile 识别成未知。"""
        module = load_module()
        profile_path = r"D:\测试\demo-project\.chrome-profiles\account_b.zhilian"
        ps_json = json.dumps([{
            "ProcessId": 789,
            "CommandLine": (
                r'"C:\Program Files\Google\Chrome\Application\chrome.exe" '
                f'--remote-debugging-port=9333 --user-data-dir="{profile_path}"'
            ),
        }], ensure_ascii=False)
        with mock.patch.object(module.platform, "system", return_value="Windows"), \
                mock.patch.object(module.subprocess, "run", return_value=type(
                    "Completed", (), {"stdout": ps_json, "returncode": 0})()) as run:
            self.assertEqual(module.chrome_user_data_dirs_for_cdp_port(9333), [profile_path])
        self.assertEqual(run.call_args.kwargs.get("encoding"), "utf-8")
    def test_smoke_jobs_require_api_salary_and_link(self):
        module = load_module()

        self.assertTrue(module.has_usable_smoke_jobs([{
            "title": "AI Engineer",
            "salary": "30-60K",
            "salary_source": "api",
            "job_link": "https://www.zhipin.com/job_detail/abc.html",
        }]))
        self.assertFalse(module.has_usable_smoke_jobs([{
            "title": "AI Engineer",
            "salary": "",
            "salary_source": "api_empty",
            "job_link": "https://www.zhipin.com/job_detail/abc.html",
        }]))

    def test_write_detail_csv_exports_detail_fields(self):
        module = load_module()
        with tempfile_profile() as paths:
            csv_path = paths["cdp_profile"] / "details.csv"
            module.write_detail_csv(str(csv_path), [{
                "job_id": "abc123",
                "title": "AI Engineer",
                "company": "Acme",
                "salary": "30-60K",
                "salary_source": "api",
                "location": "上海",
                "tags_list": "3-5年 | 本科",
                "job_link": "https://www.zhipin.com/job_detail/abc.html",
                "skill_tags": ["Python", "LLM"],
                "jd": "Build AI agents",
            }])

            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(rows[0]["job_id"], "abc123")
        self.assertEqual(rows[0]["salary_source"], "api")
        self.assertEqual(rows[0]["skill_tags"], "Python | LLM")
        self.assertEqual(rows[0]["jd"], "Build AI agents")

    def test_atomic_json_writer_replaces_with_complete_payload(self):
        module = load_module()
        with tempfile_profile() as paths:
            output = paths["cdp_profile"] / "atomic.json"

            module.write_json_atomic(str(output), {"jobs": [{"job_id": "one"}]})

            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["jobs"][0]["job_id"], "one")
            self.assertEqual(list(output.parent.glob("*.tmp-*")), [])

    def test_scrape_details_final_save_handles_bare_filename(self):
        """--detail-output 传不带目录的裸文件名时，最终保存不应崩溃。

        空 jobs 列表不触发 CDP，可直接走到最终保存逻辑；此前最终保存用
        os.makedirs(os.path.dirname(path))，dirname 为空字符串会抛
        FileNotFoundError，丢掉收尾保存和 CSV 导出。
        """
        module = load_module()
        with tempfile_profile() as paths:
            workdir = paths["cdp_profile"]
            workdir.mkdir(parents=True, exist_ok=True)
            cwd = os.getcwd()
            os.chdir(workdir)
            try:
                module.scrape_details({"jobs": []}, output_path="boss_details.json")
                self.assertTrue((workdir / "boss_details.json").exists())
            finally:
                os.chdir(cwd)

    def test_scrape_details_stops_before_writing_login_truncation(self):
        module = load_module()
        session = mock.Mock()

        def send(method, params=None, sid=None):
            if method == "Target.createTarget":
                return {"result": {"targetId": "target-1"}}
            if method == "Target.attachToTarget":
                return {"result": {"sessionId": "session-1"}}
            return {}

        session.send.side_effect = send
        session.eval_js.side_effect = lambda script, sid: (
            json.dumps(
                {
                    "jd": "",
                    "page_text": "职位描述\n负责产品规划\n登录查看完整内容",
                    "tags": [],
                }
            )
            if script == module.EXTRACT_DETAIL_JS
            else None
        )
        job = {
            "job_id": "blocked",
            "title": "AI Product Manager",
            "job_link": "https://www.zhipin.com/job_detail/blocked.html",
        }

        with tempfile_profile() as paths:
            output = paths["cdp_profile"] / "details.json"
            with mock.patch.object(module, "CDPSession", return_value=session), \
                    mock.patch.object(module.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "login expired"):
                    module.scrape_details({"jobs": [job]}, output_path=str(output))

        self.assertFalse(output.exists())
        session.send.assert_any_call(
            "Target.closeTarget", {"targetId": "target-1"}
        )
        session.close.assert_called_once()

    def test_setup_defaults_do_not_copy_cookies_or_kill_all_chrome(self):
        module = load_module()
        calls = {"copy2": [], "run": [], "popen": []}
        fake_requests = mock.Mock()
        responses = iter([
            Exception("not ready"),
            type("Resp", (), {"status_code": 200})(),
        ])

        def fake_get(*args, **kwargs):
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

        with tempfile_profile() as paths:
            expected_profile_arg = f"--user-data-dir={paths['cdp_profile']}"
            with mock.patch.object(module, "DEFAULT_PROFILE_DIR", str(paths["source_profile"])), \
                    mock.patch.object(module, "DEFAULT_CDP_DATA_DIR", str(paths["cdp_profile"])), \
                    mock.patch.object(module, "requests", fake_requests), \
                    mock.patch.object(module.shutil, "copy2", side_effect=lambda src, dst: calls["copy2"].append((src, dst))), \
                    mock.patch.object(module.subprocess, "run", side_effect=lambda *args, **kwargs: fake_run(calls, *args, **kwargs)), \
                    mock.patch.object(module.subprocess, "Popen", side_effect=lambda cmd, **kwargs: calls["popen"].append(cmd)), \
                    mock.patch.object(module.time, "sleep", return_value=None), \
                    mock.patch.object(module, "wait_for_login", return_value=True) as wait_login, \
                    mock.patch.object(module, "wait_for_cdp", return_value=True):
                fake_requests.get.side_effect = fake_get
                self.assertEqual(module.run_setup_chrome(cdp_port=9333), 0)

        self.assertEqual(calls["copy2"], [])
        self.assertTrue(all("killall" not in cmd for cmd in calls["run"]))
        self.assertTrue(calls["popen"])
        launched = calls["popen"][0]
        self.assertIn(expected_profile_arg, launched)
        wait_login.assert_called_once_with(9333, timeout=module.DEFAULT_LOGIN_TIMEOUT)

    def test_copy_login_state_is_rejected_without_copying_browser_databases(self):
        module = load_module()
        with mock.patch.object(module, "require_runtime_dependencies", return_value=True), \
                mock.patch.object(module.shutil, "copy2") as copy2, \
                mock.patch.object(module, "is_cdp_ready", return_value=False), \
                mock.patch.object(module, "stop_cdp_chrome", return_value=0), \
                mock.patch.object(module, "wait_for_cdp", return_value=False), \
                mock.patch.object(module.subprocess, "Popen") as popen:
            result = module.run_setup_chrome(copy_login_state=True)

        self.assertEqual(result, 1)
        copy2.assert_not_called()
        popen.assert_not_called()

    def test_copy_login_state_is_rejected_before_other_cli_modes(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--copy-login-state",
                "--check",
                "--cdp-port", "1",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("--copy-login-state", result.stdout)
        self.assertNotIn("[1/3]", result.stdout)

    def test_setup_rejects_ready_cdp_port_owned_by_other_profile(self):
        module = load_module()

        with tempfile_profile() as paths:
            ps_output = (
                "123 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                "--remote-debugging-port=9333 --user-data-dir=/tmp/chrome-cdp-data\n"
            )
            with mock.patch.object(module.platform, "system", return_value="Darwin"), \
                    mock.patch.object(module, "DEFAULT_CDP_DATA_DIR", str(paths["cdp_profile"])), \
                    mock.patch.object(module, "is_cdp_ready", return_value=True), \
                    mock.patch.object(module.subprocess, "run", return_value=type("Completed", (), {"stdout": ps_output, "returncode": 0})()), \
                    mock.patch.object(module.subprocess, "Popen") as popen, \
                    mock.patch.object(module, "terminate_process"):
                self.assertEqual(module.run_setup_chrome(cdp_port=9333), 1)

        popen.assert_not_called()

    def test_setup_reuses_ready_cdp_port_owned_by_dedicated_profile(self):
        module = load_module()

        with tempfile_profile() as paths:
            ps_output = (
                "123 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                f"--remote-debugging-port=9333 --user-data-dir={paths['cdp_profile']}\n"
            )
            with mock.patch.object(module.platform, "system", return_value="Darwin"), \
                    mock.patch.object(module, "DEFAULT_CDP_DATA_DIR", str(paths["cdp_profile"])), \
                    mock.patch.object(module, "is_cdp_ready", return_value=True), \
                    mock.patch.object(module.subprocess, "run", return_value=type("Completed", (), {"stdout": ps_output, "returncode": 0})()), \
                    mock.patch.object(module.subprocess, "Popen") as popen, \
                    mock.patch.object(module, "wait_for_login", return_value=True) as wait_login, \
                    mock.patch.object(module, "terminate_process"):
                self.assertEqual(module.run_setup_chrome(cdp_port=9333), 0)

        popen.assert_not_called()
        wait_login.assert_called_once_with(9333, timeout=module.DEFAULT_LOGIN_TIMEOUT)

    def test_setup_can_skip_waiting_for_login(self):
        module = load_module()

        with tempfile_profile() as paths:
            ps_output = (
                "123 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                f"--remote-debugging-port=9333 --user-data-dir={paths['cdp_profile']}\n"
            )
            with mock.patch.object(module.platform, "system", return_value="Darwin"), \
                    mock.patch.object(module, "DEFAULT_CDP_DATA_DIR", str(paths["cdp_profile"])), \
                    mock.patch.object(module, "is_cdp_ready", return_value=True), \
                    mock.patch.object(module.subprocess, "run", return_value=type("Completed", (), {"stdout": ps_output, "returncode": 0})()), \
                    mock.patch.object(module, "wait_for_login") as wait_login, \
                    mock.patch.object(module, "terminate_process"):
                self.assertEqual(module.run_setup_chrome(cdp_port=9333, wait_login=False), 0)

        wait_login.assert_not_called()

    def test_chrome_process_parsing_matches_unquoted_user_data_dir(self):
        module = load_module()

        with tempfile_profile() as paths:
            ps_output = (
                "123 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                f"--remote-debugging-port=9333 --user-data-dir={paths['cdp_profile']}\n"
                "456 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                "--remote-debugging-port=9334 --user-data-dir=/tmp/other-profile\n"
            )
            with mock.patch.object(module.platform, "system", return_value="Darwin"), \
                    mock.patch.object(module.subprocess, "run", return_value=type("Completed", (), {"stdout": ps_output, "returncode": 0})()):
                self.assertEqual(module.chrome_pids_for_user_data_dir(str(paths["cdp_profile"])), [123])
                self.assertEqual(module.chrome_user_data_dirs_for_cdp_port(9333), [str(paths["cdp_profile"])])
                self.assertTrue(module.cdp_port_uses_profile(9333, str(paths["cdp_profile"])))

    def test_stop_cdp_chrome_terminates_only_matching_profile(self):
        module = load_module()

        terminated = []
        # chrome_pids_for_user_data_dir 第一次返回 scraper profile 的 pid（111），
        # SIGTERM 后轮询返回空 -> 成功关闭，不升级 SIGKILL。
        # （按 user-data-dir 过滤出 111、不关其它 profile 的进程，该过滤逻辑由
        #   test_chrome_process_parsing_matches_unquoted_user_data_dir 独立覆盖）
        pid_lookups = iter([[111], []])
        with mock.patch.object(module, "chrome_pids_for_user_data_dir",
                               side_effect=lambda _dir: next(pid_lookups)), \
             mock.patch.object(module, "terminate_process",
                               side_effect=lambda pid, force=False: terminated.append((pid, force))), \
             mock.patch.object(module.time, "sleep"):
            stopped = module.stop_cdp_chrome("/fake/scraper-profile")

        self.assertEqual(stopped, 1)
        # 只对 scraper 的 pid 用 SIGTERM（force=False），且只一次
        self.assertEqual(terminated, [(111, False)])

    def test_stop_cdp_chrome_no_processes_returns_zero(self):
        module = load_module()

        with mock.patch.object(module, "chrome_pids_for_user_data_dir", return_value=[]):
            stopped = module.stop_cdp_chrome("/fake/scraper-profile")
        self.assertEqual(stopped, 0)

    def test_stop_cdp_chrome_escalates_to_force_kill(self):
        module = load_module()

        terminated = []
        # SIGTERM 后进程始终在 -> 轮询 10 次都不为空 -> 升级 SIGKILL
        with mock.patch.object(module, "chrome_pids_for_user_data_dir", return_value=[333]), \
             mock.patch.object(module, "terminate_process",
                               side_effect=lambda pid, force=False: terminated.append((pid, force))), \
             mock.patch.object(module.time, "sleep"):
            stopped = module.stop_cdp_chrome("/fake/scraper-profile")

        self.assertEqual(stopped, 1)
        # 先 SIGTERM（force=False），10 次轮询后升级 SIGKILL（force=True）
        self.assertIn((333, False), terminated)
        self.assertIn((333, True), terminated)
        self.assertLess(terminated.index((333, False)), terminated.index((333, True)))

    def test_run_stop_chrome_closes_dedicated_profile(self):
        module = load_module()

        with tempfile_profile() as paths:
            scraper_dir = str(paths["cdp_profile"])
            captured = {}

            def fake_prepare(**kwargs):
                # run_stop_chrome 必须以 copy_login_state=False, reset=False 调用（只定位，不动 profile）
                captured["prepare_kwargs"] = kwargs
                return {"path": scraper_dir, "copied": 0, "reset": False, "copy_login_state": False}

            def fake_stop(directory):
                captured["stopped_dir"] = directory
                return 1

            with mock.patch.object(module, "require_runtime_dependencies", return_value=True), \
                 mock.patch.object(module, "prepare_cdp_profile", side_effect=fake_prepare), \
                 mock.patch.object(module, "stop_cdp_chrome", side_effect=fake_stop):
                rc = module.run_stop_chrome()

            self.assertEqual(rc, 0)
            # 只定位 profile，绝不复制登录态 / 重置
            self.assertEqual(captured["prepare_kwargs"], {"copy_login_state": False, "reset": False})
            # 关的就是 scraper 隔离 profile 目录
            self.assertEqual(captured["stopped_dir"], scraper_dir)

    def test_run_stop_chrome_returns_zero_when_no_chrome_running(self):
        module = load_module()

        with tempfile_profile() as paths:
            scraper_dir = str(paths["cdp_profile"])
            with mock.patch.object(module, "require_runtime_dependencies", return_value=True), \
                 mock.patch.object(module, "prepare_cdp_profile",
                                   return_value={"path": scraper_dir, "copied": 0,
                                                 "reset": False, "copy_login_state": False}), \
                 mock.patch.object(module, "stop_cdp_chrome", return_value=0):
                rc = module.run_stop_chrome()
            self.assertEqual(rc, 0)

    def test_help_does_not_require_cdp_runtime_dependencies(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("--setup-chrome", result.stdout)
        self.assertIn("--reset-chrome-profile", result.stdout)
        self.assertIn("--no-wait-login", result.stdout)
        self.assertIn("--login-timeout", result.stdout)
        self.assertIn("--import-boss-session", result.stdout)
        self.assertIn("--source-cdp-port", result.stdout)
        self.assertIn("--confirm-session-import", result.stdout)
        self.assertIn("--stop-chrome", result.stdout)
        self.assertIn("--close-chrome", result.stdout)

    def test_session_import_cli_requires_confirmation_before_browser_probe(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--import-boss-session",
                "--source-cdp-port", "2",
                "--cdp-port", "1",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("code=authorization_required", result.stdout)
        self.assertNotIn("target_not_dedicated_profile", result.stdout)

    def test_check_does_not_crash_when_console_encoding_cannot_encode_emoji(self):
        env = os.environ.copy()
        env.pop("PYTHONUTF8", None)
        env["PYTHONIOENCODING"] = "gbk"

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--check", "--cdp-port", "1"],
            capture_output=True,
            timeout=15,
            env=env,
        )

        self.assertNotIn(b"UnicodeEncodeError", result.stderr)
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
