import unittest

from webui.core import (
    build_filter_options,
    match_jobs,
    normalize_profile,
    salary_monthly_bounds,
    validate_search_params,
)


class WebUICoreTests(unittest.TestCase):
    def test_filter_options_use_scraper_labels_and_codes(self):
        options = build_filter_options()

        salary = {item["label"]: item["value"] for item in options["salary"]}
        stage = {item["label"]: item["value"] for item in options["stage"]}

        self.assertEqual(salary["10-20K"], "405")
        self.assertEqual(salary["50K以上"], "407")
        self.assertEqual(stage["B轮"], "804")
        self.assertEqual(stage["不需要融资"], "808")

    def test_search_params_reject_mislabeled_or_unknown_filter_codes(self):
        with self.assertRaisesRegex(ValueError, "salary"):
            validate_search_params({"keyword": "Python", "salary": "999"})

    def test_search_params_preserve_analysis_switch(self):
        params = validate_search_params({
            "keyword": " Python 后端 ",
            "city": " 上海 ",
            "pages": 2,
            "detail": True,
            "analysis": False,
            "format": "csv",
            "stage": "804",
        })

        self.assertEqual(params["keyword"], "Python 后端")
        self.assertFalse(params["analysis"])
        self.assertEqual(params["filters"], {"stage": "804"})

    def test_salary_normalizes_monthly_and_daily_ranges(self):
        self.assertEqual(salary_monthly_bounds("20-35K·14薪"), (20.0, 35.0))
        self.assertEqual(salary_monthly_bounds("900-1000元/天"), (19.58, 21.75))

    def test_match_jobs_applies_hard_filters_and_explains_score(self):
        profile = normalize_profile({
            "target_titles": "Python 后端,后端工程师",
            "must_skills": "Python,FastAPI",
            "nice_skills": "Redis",
            "exclude_keywords": "外包,驻场",
            "districts": "浦东新区",
            "min_salary": 20,
        })
        jobs = [
            {
                "job_id": "good",
                "title": "Python 后端工程师",
                "boss_name": "产品公司",
                "salary": "25-35K",
                "location": "上海·浦东新区·张江",
                "skills": "Python | Redis",
                "job_link": "https://www.zhipin.com/job_detail/good.html",
            },
            {
                "job_id": "bad",
                "title": "Python 外包开发",
                "boss_name": "外包公司",
                "salary": "10-15K",
                "location": "上海·闵行区",
                "skills": "Python",
            },
        ]
        details = [{"job_id": "good", "jd": "负责 FastAPI 服务开发", "skill_tags": ["FastAPI"]}]

        ranked = match_jobs(jobs, details, profile)

        self.assertEqual([item["job_id"] for item in ranked], ["good", "bad"])
        self.assertTrue(ranked[0]["eligible"])
        self.assertGreaterEqual(ranked[0]["match_score"], 80)
        self.assertEqual(ranked[0]["missing_skills"], [])
        self.assertIn("FastAPI", ranked[0]["matched_skills"])
        self.assertFalse(ranked[1]["eligible"])
        self.assertTrue(any("排除词" in flag for flag in ranked[1]["risk_flags"]))
        self.assertTrue(any("薪资" in flag for flag in ranked[1]["risk_flags"]))

    def test_ascii_skill_terms_require_token_boundaries(self):
        profile = normalize_profile({"must_skills": "Go,R,C++,.NET"})
        jobs = [{
            "job_id": "one",
            "title": "Django Redis 工程师",
            "skills": "Python",
        }]

        ranked = match_jobs(jobs, [], profile)

        self.assertEqual(ranked[0]["matched_skills"], [])
        self.assertEqual(ranked[0]["missing_skills"], ["Go", "R", "C++", ".NET"])

        matched = match_jobs([{
            "job_id": "two",
            "title": "Go 工程师",
            "skills": "R | C++ | .NET",
        }], [], profile)
        self.assertEqual(matched[0]["missing_skills"], [])


class SearchRequestFiltersTests(unittest.TestCase):
    """T010: /api/execute-search 搜索请求拒绝非空 AI filters。"""

    def _minimal_raw(self):
        return {"keyword": "Python 后端", "city": "上海", "pages": 1}

    def test_empty_filters_accepted(self):
        from webui.core import validate_search_request

        raw = self._minimal_raw()
        raw["filters"] = {}
        result = validate_search_request(raw)
        self.assertEqual(result["filters"], {})
        self.assertEqual(result["keyword"], "Python 后端")

    def test_missing_filters_accepted(self):
        from webui.core import validate_search_request

        result = validate_search_request(self._minimal_raw())
        self.assertEqual(result["filters"], {})

    def test_boss_salary_code_rejected(self):
        from webui.core import (
            validate_search_request,
            SearchFiltersNotSupportedError,
        )

        raw = self._minimal_raw()
        raw["salary"] = "405"  # BOSS 10-20K 码
        with self.assertRaises(SearchFiltersNotSupportedError):
            validate_search_request(raw)

    def test_zhilian_company_nature_rejected(self):
        from webui.core import (
            validate_search_request,
            SearchFiltersNotSupportedError,
        )

        raw = self._minimal_raw()
        raw["company_nature"] = "国企"
        with self.assertRaises(SearchFiltersNotSupportedError):
            validate_search_request(raw)

    def test_screening_fields_rejected(self):
        from webui.core import (
            validate_search_request,
            SearchFiltersNotSupportedError,
        )

        raw = self._minimal_raw()
        raw["screening_fields"] = [{"name": "must_skills", "value": ["Python"]}]
        with self.assertRaises(SearchFiltersNotSupportedError):
            validate_search_request(raw)

    def test_non_empty_filters_dict_rejected(self):
        from webui.core import (
            validate_search_request,
            SearchFiltersNotSupportedError,
        )

        raw = self._minimal_raw()
        raw["filters"] = {"stage": "804"}
        with self.assertRaises(SearchFiltersNotSupportedError):
            validate_search_request(raw)

    def test_error_code_constant(self):
        """错误码固定为 search_filters_not_supported，供路由层映射 HTTP 422。"""
        from webui.core import SearchFiltersNotSupportedError

        self.assertEqual(
            SearchFiltersNotSupportedError.ERROR_CODE,
            "search_filters_not_supported",
        )

    def test_zero_value_salary_rejected(self):
        """BOSS '不限' 前端 value 是 ''（见 build_filter_options），不是 '0'。

        搜索请求不应携带任何 filter 字段值；'0' 是 SALARY_MAP 内部码，
        不应出现在 /api/execute-search 请求中，应被拒绝。
        """
        from webui.core import (
            validate_search_request,
            SearchFiltersNotSupportedError,
        )

        raw = self._minimal_raw()
        raw["salary"] = "0"
        with self.assertRaises(SearchFiltersNotSupportedError):
            validate_search_request(raw)

    def test_empty_string_filter_values_accepted(self):
        """空字符串 filter 字段视为空，搜索请求应接受。"""
        from webui.core import validate_search_request

        raw = self._minimal_raw()
        raw["stage"] = ""
        raw["degree"] = ""
        result = validate_search_request(raw)
        self.assertEqual(result["filters"], {})

    def test_platform_param_accepted_without_changing_behavior(self):
        """platform 参数当前透传，不改变拒绝行为（T011 会扩展）。"""
        from webui.core import validate_search_request

        result = validate_search_request(self._minimal_raw(), platform="boss")
        self.assertEqual(result["filters"], {})


class LegacyPlatformGuardTests(unittest.TestCase):
    """T011: legacy BOSS-only 入口平台参数解析与零副作用拒绝助手。"""

    def test_none_returns_boss(self):
        from webui.core import parse_legacy_platform

        self.assertEqual(parse_legacy_platform(None), "boss")

    def test_empty_string_returns_boss(self):
        from webui.core import parse_legacy_platform

        self.assertEqual(parse_legacy_platform(""), "boss")
        self.assertEqual(parse_legacy_platform("   "), "boss")

    def test_explicit_boss_returns_boss(self):
        from webui.core import parse_legacy_platform

        self.assertEqual(parse_legacy_platform("boss"), "boss")
        self.assertEqual(parse_legacy_platform("BOSS"), "boss")
        self.assertEqual(parse_legacy_platform("Boss"), "boss")

    def test_zhilian_raises_legacy_not_supported(self):
        from webui.core import (
            parse_legacy_platform,
            LegacyPlatformNotSupportedError,
        )

        with self.assertRaises(LegacyPlatformNotSupportedError):
            parse_legacy_platform("zhilian")
        # 大小写归一化
        with self.assertRaises(LegacyPlatformNotSupportedError):
            parse_legacy_platform("Zhilian")
        with self.assertRaises(LegacyPlatformNotSupportedError):
            parse_legacy_platform("ZHILIAN")

    def test_unknown_platform_raises_unknown(self):
        from webui.core import parse_legacy_platform
        from webui.platforms import UnknownPlatformError

        with self.assertRaises(UnknownPlatformError):
            parse_legacy_platform("linkedin")
        with self.assertRaises(UnknownPlatformError):
            parse_legacy_platform("maimai")

    def test_non_string_raises_unknown(self):
        from webui.core import parse_legacy_platform
        from webui.platforms import UnknownPlatformError

        with self.assertRaises(UnknownPlatformError):
            parse_legacy_platform(123)
        with self.assertRaises(UnknownPlatformError):
            parse_legacy_platform(["boss"])

    def test_error_code_constant(self):
        """错误码固定为 legacy_platform_not_supported。"""
        from webui.core import LegacyPlatformNotSupportedError

        self.assertEqual(
            LegacyPlatformNotSupportedError.ERROR_CODE,
            "legacy_platform_not_supported",
        )

    def test_guard_is_alias_of_parse(self):
        from webui.core import (
            legacy_platform_guard,
            LegacyPlatformNotSupportedError,
        )

        self.assertEqual(legacy_platform_guard(None), "boss")
        self.assertEqual(legacy_platform_guard("boss"), "boss")
        with self.assertRaises(LegacyPlatformNotSupportedError):
            legacy_platform_guard("zhilian")

    def test_zero_side_effect_pure_function(self):
        """parse_legacy_platform 是纯函数：不读取 DB/浏览器/profile/注册表。

        本测试通过 mock 验证不触发常见副作用入口；路由层零副作用保证
        属于 tasks007 范围，此处只验证助手函数本身。
        """
        from unittest import mock
        from webui.core import parse_legacy_platform

        # 智联拒绝路径：确保抛异常前不触碰任何外部资源
        with mock.patch("webui.platforms._REGISTRY", {}) as mock_reg:
            with self.assertRaises(ValueError):
                parse_legacy_platform("zhilian")
            # 注册表未被修改
            self.assertEqual(mock_reg, {})

        # 兼容路径：boss 不触碰注册表
        with mock.patch("webui.platforms._REGISTRY", {"boss": object()}) as mock_reg:
            result = parse_legacy_platform("boss")
            self.assertEqual(result, "boss")
            # 注册表内容未被修改（仍只有 boss）
            self.assertEqual(set(mock_reg.keys()), {"boss"})


if __name__ == "__main__":
    unittest.main()
