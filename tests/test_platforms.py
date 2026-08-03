"""tasks001 T003 + tasks003 T204-T210 — 平台注册表测试。

验证 ``webui/platforms.py`` 作为唯一平台注册边界，符合
``specs/001-add-zhilian-platform/contracts/platform-schema.md`` 合同。

覆盖：
- 已知平台键集合与默认平台；
- BOSS 注册项字段集合、城市目录、URL allowlist、默认端口和兼容行为；
- 智联已注册但 fixture 未核验前 ``enabled_for_new_tasks=False``（tasks003 T204）；
- 智联城市目录仅含全国 ``jl0``（T205）；
- schema/城市 fixture 完整性检查（T206）；
- 服务投影不泄露 profile 路径（T207）；
- 登录空间 profile_dir 派生、受控切换、双平台 delete/activate 检查（T208-T210）；
- FilterOption / FilterField / PlatformFilterSchema 不可变值对象校验；
- URL 规范化、schema 投影与筛选快照。
"""
from __future__ import annotations

import unittest

from webui import platforms
from webui.platforms import (
    BOSS_CITY_MAPPING_VERSION,
    BOSS_DEFAULT_CDP_PORT,
    BOSS_FILTER_SCHEMA_VERSION,
    DEFAULT_PLATFORM,
    KNOWN_PLATFORM_KEYS,
    PlatformDisabledError,
    PlatformNotRegisteredError,
    PlatformRegistry,
    UnknownPlatformError,
    ZHILIAN_AVAILABILITY_REASON,
    ZHILIAN_CITY_MAPPING_VERSION,
    ZHILIAN_DEFAULT_CDP_PORT,
    ZHILIAN_FILTER_SCHEMA_VERSION,
    ZHILIAN_NATIONWIDE_CODE,
    ZHILIAN_NATIONWIDE_NAME,
    build_filter_snapshot,
    check_browser_account_activate,
    check_browser_account_delete,
    check_login_space_conflict,
    check_platform_fixture_integrity,
    derive_zhilian_profile_dir,
    get_platform,
    get_platform_or_none,
    is_known_platform_key,
    list_platform_keys,
    list_platforms,
    normalize_job_url,
    project_filter_schema,
    resolve_login_space,
    resolve_platform_city,
    resolve_platform_or_default,
    validate_filter_values,
    validate_platform_key,
)


# ===========================================================================
# 已知平台键与默认平台
# ===========================================================================
class KnownPlatformKeysTests(unittest.TestCase):
    """KNOWN_PLATFORM_KEYS 固定为 (boss, zhilian)；默认平台为 boss。"""

    def test_known_keys_exact(self):
        self.assertEqual(KNOWN_PLATFORM_KEYS, ("boss", "zhilian"))

    def test_default_platform_is_boss(self):
        self.assertEqual(DEFAULT_PLATFORM, "boss")

    def test_is_known_platform_key(self):
        self.assertTrue(is_known_platform_key("boss"))
        self.assertTrue(is_known_platform_key("zhilian"))
        self.assertFalse(is_known_platform_key("linkedin"))
        self.assertFalse(is_known_platform_key(None))
        self.assertFalse(is_known_platform_key(""))

    def test_validate_platform_key_known(self):
        self.assertEqual(validate_platform_key("boss"), "boss")
        self.assertEqual(validate_platform_key("zhilian"), "zhilian")

    def test_validate_platform_key_unknown_raises(self):
        with self.assertRaises(UnknownPlatformError):
            validate_platform_key("linkedin")
        with self.assertRaises(UnknownPlatformError):
            validate_platform_key("")
        with self.assertRaises(UnknownPlatformError):
            validate_platform_key(None)

    def test_resolve_platform_or_default(self):
        self.assertEqual(resolve_platform_or_default(None), "boss")
        self.assertEqual(resolve_platform_or_default(""), "boss")
        self.assertEqual(resolve_platform_or_default("boss"), "boss")
        self.assertEqual(resolve_platform_or_default("zhilian"), "zhilian")
        with self.assertRaises(UnknownPlatformError):
            resolve_platform_or_default("unknown")


# ===========================================================================
# BOSS 注册基线
# ===========================================================================
class BossRegistrationTests(unittest.TestCase):
    """BOSS 必须在导入 platforms 后自动注册（boss_cdp_raw 可导入时）。"""

    def test_boss_is_registered(self):
        keys = list_platform_keys()
        self.assertIn("boss", keys)

    def test_zhilian_registered_but_disabled(self):
        """智联已注册但 fixture 未核验前 enabled_for_new_tasks=False（T204）。"""
        self.assertIn("zhilian", KNOWN_PLATFORM_KEYS)
        zhilian = get_platform("zhilian")
        self.assertIsNotNone(zhilian)
        self.assertFalse(zhilian.enabled_for_new_tasks)
        self.assertTrue(zhilian.availability_reason)
        self.assertEqual(zhilian.availability_reason, ZHILIAN_AVAILABILITY_REASON)
        self.assertEqual(zhilian.display_name, "智联招聘")
        self.assertEqual(zhilian.default_cdp_port, ZHILIAN_DEFAULT_CDP_PORT)

    def test_unknown_platform_raises_unknown(self):
        with self.assertRaises(UnknownPlatformError):
            get_platform("linkedin")

    def test_boss_display_name_and_port(self):
        boss = get_platform("boss")
        self.assertEqual(boss.display_name, "BOSS直聘")
        self.assertEqual(boss.default_cdp_port, BOSS_DEFAULT_CDP_PORT)
        self.assertEqual(BOSS_DEFAULT_CDP_PORT, 9222)

    def test_zhilian_default_port_constant(self):
        self.assertEqual(ZHILIAN_DEFAULT_CDP_PORT, 9223)

    def test_boss_filter_schema_fields_order(self):
        """BOSS schema 字段顺序：salary, experience, degree, industry, scale, stage。"""
        boss = get_platform("boss")
        keys = [f.key for f in boss.filter_schema.fields]
        self.assertEqual(
            keys,
            ["salary", "experience", "degree", "industry", "scale", "stage"],
        )

    def test_boss_filter_schema_version(self):
        boss = get_platform("boss")
        self.assertEqual(boss.filter_schema.schema_version, BOSS_FILTER_SCHEMA_VERSION)
        self.assertGreaterEqual(boss.filter_schema.schema_version, 1)

    def test_boss_filter_schema_excludes_company_nature(self):
        """BOSS schema 禁止 company_nature 字段。"""
        boss = get_platform("boss")
        keys = {f.key for f in boss.filter_schema.fields}
        self.assertNotIn("company_nature", keys)

    def test_boss_filter_schema_all_fields_multiple(self):
        """BOSS 所有筛选字段为多选。"""
        boss = get_platform("boss")
        for f in boss.filter_schema.fields:
            self.assertTrue(f.multiple, f"字段 {f.key} 应为 multiple=True")

    def test_boss_filter_options_non_empty_values(self):
        """每个 option 的 value 和 label 都非空（platform-schema.md:85）。"""
        boss = get_platform("boss")
        for f in boss.filter_schema.fields:
            self.assertGreater(len(f.options), 0, f"字段 {f.key} 应有选项")
            for opt in f.options:
                self.assertTrue(opt.value, f"字段 {f.key} option.value 不能为空")
                self.assertTrue(opt.label, f"字段 {f.key} option.label 不能为空")

    def test_boss_unlimited_option_preserves_zero_value(self):
        """BOSS '不限' 的码 '0' 是非空稳定值，保留为 option.value。

        research.md 决策 4：是否把 '不限' 视作'不下推筛选'由调用方在下推层
        处理，不在 schema 层改写为空串。
        """
        boss = get_platform("boss")
        salary_field = boss.filter_schema.get_field("salary")
        self.assertIsNotNone(salary_field)
        unlimited = [opt for opt in salary_field.options if opt.label == "不限"]
        self.assertGreater(len(unlimited), 0, "薪资字段应包含 '不限' 选项")
        self.assertEqual(unlimited[0].value, "0")

    def test_boss_city_catalog_has_entries(self):
        boss = get_platform("boss")
        self.assertGreater(len(boss.city_catalog.entries), 0)
        names = boss.city_catalog.names()
        self.assertIn("全国", names)

    def test_boss_city_catalog_version(self):
        boss = get_platform("boss")
        self.assertEqual(boss.city_catalog.mapping_version, BOSS_CITY_MAPPING_VERSION)

    def test_boss_city_catalog_entries_have_platform_code(self):
        boss = get_platform("boss")
        for entry in boss.city_catalog.entries:
            self.assertTrue(entry.platform_code, f"城市 {entry.name} 缺少 platform_code")
            self.assertEqual(entry.mapping_version, BOSS_CITY_MAPPING_VERSION)

    def test_boss_enabled_for_new_tasks(self):
        boss = get_platform("boss")
        self.assertTrue(boss.enabled_for_new_tasks)
        self.assertEqual(boss.availability_reason, "")

    def test_boss_normalize_job_url_fn(self):
        """BOSS URL 规范化：HTTPS + *.zhipin.com，剥离 query/fragment。"""
        boss = get_platform("boss")
        ok = "https://www.zhipin.com/job_detail/abc123.htm"
        self.assertEqual(boss.normalize_job_url(ok), ok)
        # http 拒绝（BOSS 保持 HTTPS-only）
        self.assertEqual(boss.normalize_job_url("http://www.zhipin.com/job_detail/abc.htm"), "")
        # query/fragment 剥离
        self.assertEqual(
            boss.normalize_job_url("https://www.zhipin.com/job_detail/abc.htm?foo=bar#frag"),
            "https://www.zhipin.com/job_detail/abc.htm",
        )
        # 非 zhipin 域名拒绝
        self.assertEqual(boss.normalize_job_url("https://evil.com/job_detail/abc.htm"), "")

    def test_boss_resolve_login_space(self):
        boss = get_platform("boss")
        space = boss.resolve_login_space("a", boss_profile_dir="/tmp/profile-a")
        self.assertEqual(space.platform, "boss")
        self.assertEqual(space.browser_account, "a")
        self.assertEqual(space.profile_key, "boss:a")
        self.assertEqual(space.cdp_port, BOSS_DEFAULT_CDP_PORT)


# ===========================================================================
# URL 规范化（平台权威函数）
# ===========================================================================
class NormalizeJobUrlTests(unittest.TestCase):
    def test_boss_url_via_registry(self):
        ok = "https://www.zhipin.com/job_detail/abc.htm"
        self.assertEqual(normalize_job_url("boss", ok), ok)

    def test_zhilian_url_via_registry(self):
        """智联 URL 规范化：HTTPS + zhaopin.com + jobdetail/<id>.htm。"""
        ok = "https://www.zhaopin.com/jobdetail/abc123.htm"
        self.assertEqual(normalize_job_url("zhilian", ok), ok)
        # http 升级为 https
        self.assertEqual(
            normalize_job_url("zhilian", "http://www.zhaopin.com/jobdetail/abc.htm"),
            "https://www.zhaopin.com/jobdetail/abc.htm",
        )
        # query/fragment 剥离
        self.assertEqual(
            normalize_job_url("zhilian", "https://zhaopin.com/jobdetail/abc.htm?x=1#f"),
            "https://zhaopin.com/jobdetail/abc.htm",
        )
        # 非 zhaopin 域名拒绝
        self.assertEqual(
            normalize_job_url("zhilian", "https://evil.com/jobdetail/abc.htm"),
            "",
        )
        # 非 jobdetail path 拒绝
        self.assertEqual(
            normalize_job_url("zhilian", "https://www.zhaopin.com/sou/jl0/kwPython"),
            "",
        )

    def test_unknown_platform_url_raises(self):
        with self.assertRaises(UnknownPlatformError):
            normalize_job_url("linkedin", "https://example.com/x")


# ===========================================================================
# 登录空间解析
# ===========================================================================
class LoginSpaceTests(unittest.TestCase):
    def test_boss_login_space(self):
        space = resolve_login_space("boss", "a", boss_profile_dir="/tmp/profile-a")
        self.assertEqual(space.platform, "boss")
        self.assertEqual(space.profile_key, "boss:a")
        self.assertEqual(space.cdp_port, 9222)

    def test_login_space_invalid_port(self):
        from webui.platforms import LoginSpace
        with self.assertRaises(ValueError):
            LoginSpace(
                platform="boss", browser_account="a",
                profile_key="boss:a", cdp_port=0,
            )

    def test_login_space_invalid_profile_key(self):
        from webui.platforms import LoginSpace
        with self.assertRaises(ValueError):
            LoginSpace(
                platform="boss", browser_account="a",
                profile_key="zhilian:a", cdp_port=9222,
            )

    def test_login_space_missing_account(self):
        from webui.platforms import resolve_boss_login_space
        with self.assertRaises(ValueError):
            resolve_boss_login_space("", boss_profile_dir="/tmp/p")
        with self.assertRaises(ValueError):
            resolve_boss_login_space("a", boss_profile_dir="")


# ===========================================================================
# 不可变值对象校验
# ===========================================================================
class FilterOptionTests(unittest.TestCase):
    def test_valid_option(self):
        from webui.platforms import FilterOption
        opt = FilterOption(value="301", label="0-20人")
        self.assertEqual(opt.value, "301")
        self.assertEqual(opt.label, "0-20人")

    def test_zero_value_is_valid(self):
        """'0' 是非空稳定值，FilterOption 应接受。"""
        from webui.platforms import FilterOption
        opt = FilterOption(value="0", label="不限")
        self.assertEqual(opt.value, "0")

    def test_empty_value_rejected(self):
        from webui.platforms import FilterOption
        with self.assertRaises(ValueError):
            FilterOption(value="", label="不限")

    def test_empty_label_rejected(self):
        from webui.platforms import FilterOption
        with self.assertRaises(ValueError):
            FilterOption(value="301", label="")

    def test_frozen(self):
        from webui.platforms import FilterOption
        opt = FilterOption(value="1", label="x")
        with self.assertRaises(Exception):
            opt.value = "2"  # type: ignore[misc]


class FilterFieldTests(unittest.TestCase):
    def test_valid_field(self):
        from webui.platforms import FilterField, FilterOption
        f = FilterField(
            key="salary", label="薪资", multiple=True,
            options=(FilterOption(value="0", label="不限"),),
        )
        self.assertEqual(f.key, "salary")
        self.assertEqual(f.option_values(), ("0",))
        self.assertEqual(f.label_for("0"), "不限")
        self.assertIsNone(f.label_for("999"))

    def test_duplicate_value_rejected(self):
        from webui.platforms import FilterField, FilterOption
        with self.assertRaises(ValueError):
            FilterField(
                key="salary", label="薪资", multiple=True,
                options=(
                    FilterOption(value="0", label="不限"),
                    FilterOption(value="0", label="重复"),
                ),
            )

    def test_empty_key_rejected(self):
        from webui.platforms import FilterField
        with self.assertRaises(ValueError):
            FilterField(key="", label="x", multiple=True)


class PlatformFilterSchemaTests(unittest.TestCase):
    def test_duplicate_field_key_rejected(self):
        from webui.platforms import FilterField, PlatformFilterSchema
        with self.assertRaises(ValueError):
            PlatformFilterSchema(
                platform="boss", schema_version=1,
                enabled_for_new_tasks=True,
                fields=(
                    FilterField(key="salary", label="薪资", multiple=True),
                    FilterField(key="salary", label="重复", multiple=True),
                ),
            )

    def test_invalid_schema_version_rejected(self):
        from webui.platforms import PlatformFilterSchema
        with self.assertRaises(ValueError):
            PlatformFilterSchema(
                platform="boss", schema_version=0,
                enabled_for_new_tasks=True, fields=(),
            )


class PlatformCityCatalogTests(unittest.TestCase):
    def test_duplicate_name_rejected(self):
        from webui.platforms import CityEntry, PlatformCityCatalog
        with self.assertRaises(ValueError):
            PlatformCityCatalog(
                platform="boss", mapping_version=1,
                entries=(
                    CityEntry(name="上海", label="上海", platform_code="1", mapping_version=1),
                    CityEntry(name="上海", label="上海", platform_code="2", mapping_version=1),
                ),
            )

    def test_duplicate_code_rejected(self):
        from webui.platforms import CityEntry, PlatformCityCatalog
        with self.assertRaises(ValueError):
            PlatformCityCatalog(
                platform="boss", mapping_version=1,
                entries=(
                    CityEntry(name="上海", label="上海", platform_code="1", mapping_version=1),
                    CityEntry(name="北京", label="北京", platform_code="1", mapping_version=1),
                ),
            )


# ===========================================================================
# schema 投影与筛选快照
# ===========================================================================
class ProjectFilterSchemaTests(unittest.TestCase):
    def test_boss_schema_projection(self):
        schema = project_filter_schema("boss")
        self.assertTrue(schema["ok"])
        self.assertEqual(schema["platform"], "boss")
        self.assertEqual(schema["schema_version"], BOSS_FILTER_SCHEMA_VERSION)
        self.assertTrue(schema["enabled_for_new_tasks"])
        keys = [f["key"] for f in schema["fields"]]
        self.assertEqual(
            keys,
            ["salary", "experience", "degree", "industry", "scale", "stage"],
        )

    def test_boss_schema_projection_options_non_empty(self):
        schema = project_filter_schema("boss")
        for f in schema["fields"]:
            self.assertGreater(len(f["options"]), 0, f"字段 {f['key']} 选项不能为空")
            for opt in f["options"]:
                self.assertTrue(opt["value"], f"字段 {f['key']} option.value 不能为空")
                self.assertTrue(opt["label"], f"字段 {f['key']} option.label 不能为空")

    def test_zhilian_schema_available_but_disabled(self):
        """智联 schema 已注册但 enabled=False，字段顺序正确，options 为空（T204/T207）。"""
        schema = project_filter_schema("zhilian")
        self.assertTrue(schema["ok"])
        self.assertEqual(schema["platform"], "zhilian")
        self.assertEqual(schema["schema_version"], ZHILIAN_FILTER_SCHEMA_VERSION)
        self.assertFalse(schema["enabled_for_new_tasks"])
        keys = [f["key"] for f in schema["fields"]]
        self.assertEqual(
            keys,
            ["salary", "experience", "degree", "industry", "scale", "company_nature"],
        )
        # fixture 未核验前所有字段 options 为空
        for f in schema["fields"]:
            self.assertEqual(len(f["options"]), 0, f"字段 {f['key']} options 应为空")
        # 响应不含 profile 路径
        self.assertNotIn("profile_dir", repr(schema))
        self.assertNotIn("profile_key", repr(schema))
        self.assertNotIn("cdp_port", repr(schema))


class ValidateFilterValuesTests(unittest.TestCase):
    def test_validate_boss_known_value(self):
        boss = get_platform("boss")
        salary_field = boss.filter_schema.get_field("salary")
        any_value = salary_field.options[0].value
        result = validate_filter_values(
            "boss",
            schema_version=BOSS_FILTER_SCHEMA_VERSION,
            screening_fields={"salary": [any_value]},
        )
        self.assertEqual(result, {"salary": [any_value]})

    def test_validate_boss_version_mismatch(self):
        with self.assertRaises(ValueError) as ctx:
            validate_filter_values(
                "boss", schema_version=999,
                screening_fields={},
            )
        self.assertIn("mismatch", str(ctx.exception).lower())

    def test_validate_boss_rejects_company_nature(self):
        """BOSS schema 禁止 company_nature 字段。"""
        with self.assertRaises(ValueError) as ctx:
            validate_filter_values(
                "boss",
                schema_version=BOSS_FILTER_SCHEMA_VERSION,
                screening_fields={"company_nature": ["any"]},
            )
        self.assertIn("filter_validation_failed", str(ctx.exception))

    def test_validate_boss_rejects_unknown_value(self):
        with self.assertRaises(ValueError):
            validate_filter_values(
                "boss",
                schema_version=BOSS_FILTER_SCHEMA_VERSION,
                screening_fields={"salary": ["NONEXISTENT_VALUE"]},
            )


class BuildFilterSnapshotTests(unittest.TestCase):
    def test_boss_snapshot_includes_values_and_labels(self):
        boss = get_platform("boss")
        salary_field = boss.filter_schema.get_field("salary")
        any_value = salary_field.options[0].value
        any_label = salary_field.options[0].label
        snapshot = build_filter_snapshot(
            "boss",
            schema_version=BOSS_FILTER_SCHEMA_VERSION,
            screening_fields={"salary": [any_value]},
        )
        self.assertEqual(snapshot["platform"], "boss")
        self.assertEqual(snapshot["schema_version"], BOSS_FILTER_SCHEMA_VERSION)
        self.assertIn("salary", snapshot["fields"])
        self.assertEqual(snapshot["fields"]["salary"]["values"], [any_value])
        self.assertEqual(snapshot["fields"]["salary"]["labels"], [any_label])

    def test_boss_snapshot_empty_fields(self):
        snapshot = build_filter_snapshot(
            "boss",
            schema_version=BOSS_FILTER_SCHEMA_VERSION,
            screening_fields={},
        )
        self.assertEqual(snapshot["fields"], {})


# ===========================================================================
# 注册表存储
# ===========================================================================
class RegistryStorageTests(unittest.TestCase):
    def test_list_platforms_returns_tuple(self):
        result = list_platforms()
        self.assertIsInstance(result, tuple)
        for item in result:
            self.assertIsInstance(item, PlatformRegistry)

    def test_register_platform_rejects_unknown_key(self):
        from webui.platforms import PlatformRegistry, register_platform
        # 构造一个未知平台的注册项（绕过 dataclass 校验直接构造字段）。
        # PlatformRegistry 是 frozen dataclass，key 字段无校验，但
        # register_platform 会拒绝未知键。
        reg = PlatformRegistry(
            key="linkedin",
            display_name="LinkedIn",
            filter_schema=get_platform("boss").filter_schema,
            city_catalog=get_platform("boss").city_catalog,
            enabled_for_new_tasks=False,
            availability_reason="not supported",
            default_cdp_port=9224,
            normalize_job_url_fn=lambda raw: "",
            resolve_login_space_fn=lambda account, **kw: None,
        )
        with self.assertRaises(UnknownPlatformError):
            register_platform(reg)


# ===========================================================================
# T204: 智联注册细节
# ===========================================================================
class ZhilianRegistrationTests(unittest.TestCase):
    """智联平台注册项字段集合（T204）。"""

    def test_zhilian_filter_schema_fields_order(self):
        """智联 schema 字段顺序：salary, experience, degree, industry, scale, company_nature。"""
        zhilian = get_platform("zhilian")
        keys = [f.key for f in zhilian.filter_schema.fields]
        self.assertEqual(
            keys,
            ["salary", "experience", "degree", "industry", "scale", "company_nature"],
        )

    def test_zhilian_filter_schema_version(self):
        zhilian = get_platform("zhilian")
        self.assertEqual(zhilian.filter_schema.schema_version, ZHILIAN_FILTER_SCHEMA_VERSION)
        self.assertGreaterEqual(zhilian.filter_schema.schema_version, 1)

    def test_zhilian_filter_schema_excludes_stage(self):
        """智联 schema 禁止 stage 字段。"""
        zhilian = get_platform("zhilian")
        keys = {f.key for f in zhilian.filter_schema.fields}
        self.assertNotIn("stage", keys)

    def test_zhilian_filter_schema_all_fields_multiple(self):
        """智联所有筛选字段为多选。"""
        zhilian = get_platform("zhilian")
        for f in zhilian.filter_schema.fields:
            self.assertTrue(f.multiple, f"字段 {f.key} 应为 multiple=True")

    def test_zhilian_all_options_empty_before_verification(self):
        """fixture 未核验前所有字段 options 为空。"""
        zhilian = get_platform("zhilian")
        for f in zhilian.filter_schema.fields:
            self.assertEqual(len(f.options), 0, f"字段 {f.key} options 应为空")

    def test_zhilian_enabled_false_with_reason(self):
        zhilian = get_platform("zhilian")
        self.assertFalse(zhilian.enabled_for_new_tasks)
        self.assertTrue(zhilian.availability_reason)

    def test_zhilian_in_list_platforms(self):
        """list_platforms 包含智联（禁用项仍需返回）。"""
        keys = list_platform_keys()
        self.assertIn("zhilian", keys)
        platforms_list = list_platforms()
        zhilian_keys = [p.key for p in platforms_list]
        self.assertIn("zhilian", zhilian_keys)

    def test_zhilian_normalize_job_url_fn(self):
        """智联 URL 规范化函数已绑定。"""
        zhilian = get_platform("zhilian")
        ok = "https://www.zhaopin.com/jobdetail/abc.htm"
        self.assertEqual(zhilian.normalize_job_url(ok), ok)
        self.assertEqual(zhilian.normalize_job_url("https://evil.com/x"), "")

    def test_zhilian_resolve_login_space_fn(self):
        """智联登录空间解析函数已绑定。"""
        zhilian = get_platform("zhilian")
        space = zhilian.resolve_login_space("a", boss_profile_dir="/tmp/profile-a")
        self.assertEqual(space.platform, "zhilian")
        self.assertEqual(space.browser_account, "a")
        self.assertEqual(space.profile_key, "zhilian:a")
        self.assertEqual(space.cdp_port, ZHILIAN_DEFAULT_CDP_PORT)


# ===========================================================================
# T205: 智联城市目录
# ===========================================================================
class ZhilianCityCatalogTests(unittest.TestCase):
    """智联城市目录仅含全国 jl0（T205）。"""

    def test_zhilian_city_catalog_has_nationwide_only(self):
        zhilian = get_platform("zhilian")
        names = zhilian.city_catalog.names()
        self.assertEqual(names, (ZHILIAN_NATIONWIDE_NAME,))

    def test_zhilian_nationwide_code_is_jl0(self):
        zhilian = get_platform("zhilian")
        nationwide = zhilian.city_catalog.find(ZHILIAN_NATIONWIDE_NAME)
        self.assertIsNotNone(nationwide)
        self.assertEqual(nationwide.platform_code, ZHILIAN_NATIONWIDE_CODE)
        self.assertEqual(ZHILIAN_NATIONWIDE_CODE, "jl0")

    def test_zhilian_city_catalog_version(self):
        zhilian = get_platform("zhilian")
        self.assertEqual(zhilian.city_catalog.mapping_version, ZHILIAN_CITY_MAPPING_VERSION)

    def test_zhilian_non_nationwide_city_missing(self):
        """非全国城市未核验，解析时阻断。"""
        with self.assertRaises(ValueError) as ctx:
            resolve_platform_city("zhilian", "上海")
        self.assertIn("city_mapping_missing", str(ctx.exception))

    def test_zhilian_nationwide_resolvable(self):
        """全国 jl0 可解析。"""
        entry = resolve_platform_city("zhilian", ZHILIAN_NATIONWIDE_NAME)
        self.assertEqual(entry.platform_code, "jl0")

    def test_zhilian_city_codes_json_file_exists(self):
        """data/zhilian_city_codes.json 存在且仅含全国。"""
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "data" / "zhilian_city_codes.json"
        self.assertTrue(path.exists(), "data/zhilian_city_codes.json 应存在")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["platform"], "zhilian")
        self.assertEqual(data["nationwide"]["code"], "jl0")
        self.assertEqual(data["cities"], [])


# ===========================================================================
# T206: fixture 完整性检查
# ===========================================================================
class ZhilianFixtureIntegrityTests(unittest.TestCase):
    """智联 fixture 完整性检查返回不通过（T206）。"""

    def test_zhilian_fixture_integrity_fails(self):
        """智联所有字段 options 为空 → 完整性检查失败。"""
        ok, reason = check_platform_fixture_integrity("zhilian")
        self.assertFalse(ok)
        self.assertTrue(reason)
        self.assertIn("empty options", reason)

    def test_boss_fixture_integrity_passes(self):
        """BOSS 所有字段 options 非空 → 完整性检查通过。"""
        ok, reason = check_platform_fixture_integrity("boss")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_zhilian_disabled_consistent_with_integrity(self):
        """智联 enabled=False 与 fixture 完整性检查失败一致。"""
        zhilian = get_platform("zhilian")
        ok, _ = check_platform_fixture_integrity("zhilian")
        self.assertEqual(zhilian.enabled_for_new_tasks, ok)


# ===========================================================================
# T207: 服务投影不泄露 profile 路径
# ===========================================================================
class ZhilianServiceProjectionTests(unittest.TestCase):
    """智联服务投影不返回 profile 路径或路径摘要（T207）。"""

    def test_zhilian_project_filter_schema_no_profile(self):
        schema = project_filter_schema("zhilian")
        repr_text = repr(schema)
        self.assertNotIn("profile_dir", repr_text)
        self.assertNotIn("profile_key", repr_text)
        self.assertNotIn("cdp_port", repr_text)
        self.assertNotIn("boss_profile_dir", repr_text)

    def test_zhilian_platform_registry_no_profile_in_repr(self):
        """PlatformRegistry 的 repr 不含 profile 路径。"""
        zhilian = get_platform("zhilian")
        repr_text = repr(zhilian)
        self.assertNotIn("profile_dir", repr_text.lower())
        # profile_key 是逻辑标识，不含路径
        self.assertNotIn("/tmp", repr_text)
        self.assertNotIn("\\users", repr_text.lower())

    def test_list_platforms_no_profile_paths(self):
        """list_platforms 返回的注册项不含 profile 路径。"""
        for reg in list_platforms():
            repr_text = repr(reg)
            self.assertNotIn("/tmp/", repr_text)
            self.assertNotIn("\\users\\", repr_text.lower())
            self.assertNotIn("chrome-profile", repr_text.lower())

    def test_zhilian_login_space_no_profile_dir(self):
        """LoginSpace 不含 profile_dir 字段。"""
        from webui.platforms import LoginSpace
        space = LoginSpace(
            platform="zhilian", browser_account="a",
            profile_key="zhilian:a", cdp_port=9223,
        )
        self.assertFalse(hasattr(space, "profile_dir"))
        repr_text = repr(space)
        self.assertNotIn("profile_dir", repr_text)
        self.assertNotIn("/tmp", repr_text)


# ===========================================================================
# T208-T210: 浏览器登录空间派生与双平台检查
# ===========================================================================
class LoginSpaceIsolationTests(unittest.TestCase):
    """T208: profile_dir 派生；T209: 受控切换；T210: delete/activate 检查。"""

    # ----- T208: derive_zhilian_profile_dir -----

    def test_derive_zhilian_profile_dir_appends_suffix(self):
        self.assertEqual(
            derive_zhilian_profile_dir("/home/user/.career-scout/chrome-profile"),
            "/home/user/.career-scout/chrome-profile.zhilian",
        )

    def test_derive_zhilian_profile_dir_strips_trailing_slash(self):
        self.assertEqual(
            derive_zhilian_profile_dir("/home/user/chrome-profile/"),
            "/home/user/chrome-profile.zhilian",
        )
        self.assertEqual(
            derive_zhilian_profile_dir("C:\\Users\\u\\profile\\"),
            "C:\\Users\\u\\profile.zhilian",
        )

    def test_derive_zhilian_profile_dir_differs_from_boss(self):
        """智联 profile_dir 与 BOSS profile_dir 不同（隔离）。"""
        boss_dir = "/home/user/.career-scout/chrome-profile"
        zhilian_dir = derive_zhilian_profile_dir(boss_dir)
        self.assertNotEqual(boss_dir, zhilian_dir)
        self.assertTrue(zhilian_dir.endswith(".zhilian"))

    def test_derive_zhilian_profile_dir_deterministic(self):
        """同一 boss_profile_dir 总是产生同一智联 profile_dir。"""
        boss_dir = "/home/user/chrome-profile"
        self.assertEqual(
            derive_zhilian_profile_dir(boss_dir),
            derive_zhilian_profile_dir(boss_dir),
        )

    def test_derive_zhilian_profile_dir_empty_raises(self):
        with self.assertRaises(ValueError):
            derive_zhilian_profile_dir("")

    # ----- T209: check_login_space_conflict -----

    def test_login_space_conflict_port_idle(self):
        """端口空闲 → 允许。"""
        ok, reason = check_login_space_conflict(
            "zhilian", "a",
            boss_profile_dir="/tmp/profile-a",
            port_profile_paths=[],
            known_profile_paths=["/tmp/profile-a.zhilian", "/tmp/profile-b.zhilian"],
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_login_space_conflict_expected_profile(self):
        """端口被期望 profile 占用 → 复用。"""
        zhilian_dir = derive_zhilian_profile_dir("/tmp/profile-a")
        ok, reason = check_login_space_conflict(
            "zhilian", "a",
            boss_profile_dir="/tmp/profile-a",
            port_profile_paths=[zhilian_dir],
            known_profile_paths=[zhilian_dir],
        )
        self.assertTrue(ok)

    def test_login_space_conflict_known_profile_allows_switch(self):
        """端口被同平台已知 profile 占用 → 允许受控切换。"""
        ok, reason = check_login_space_conflict(
            "zhilian", "a",
            boss_profile_dir="/tmp/profile-a",
            port_profile_paths=["/tmp/profile-b.zhilian"],
            known_profile_paths=["/tmp/profile-a.zhilian", "/tmp/profile-b.zhilian"],
        )
        self.assertTrue(ok)

    def test_login_space_conflict_unknown_profile_rejected(self):
        """端口被未知 profile 占用 → 拒绝。"""
        ok, reason = check_login_space_conflict(
            "zhilian", "a",
            boss_profile_dir="/tmp/profile-a",
            port_profile_paths=["/tmp/unknown-profile"],
            known_profile_paths=["/tmp/profile-a.zhilian", "/tmp/profile-b.zhilian"],
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "login_space_conflict")

    def test_login_space_conflict_boss_platform(self):
        """BOSS 平台同样适用受控切换。"""
        ok, reason = check_login_space_conflict(
            "boss", "a",
            boss_profile_dir="/tmp/profile-a",
            port_profile_paths=[],
            known_profile_paths=["/tmp/profile-a", "/tmp/profile-b"],
        )
        self.assertTrue(ok)

    def test_login_space_conflict_unknown_platform_raises(self):
        with self.assertRaises(UnknownPlatformError):
            check_login_space_conflict(
                "linkedin", "a",
                boss_profile_dir="/tmp/profile-a",
                port_profile_paths=[],
                known_profile_paths=[],
            )

    # ----- T210: check_browser_account_delete -----

    def test_delete_allowed_when_no_locks_no_port_use(self):
        """无运行锁、无端口占用 → 允许删除。"""
        ok, reason = check_browser_account_delete(
            "a",
            boss_profile_dir="/tmp/profile-a",
            running_locks=[],
            port_profiles_boss=[],
            port_profiles_zhilian=[],
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_delete_blocked_by_running_lock(self):
        """有运行锁 → 阻断删除。"""
        ok, reason = check_browser_account_delete(
            "a",
            boss_profile_dir="/tmp/profile-a",
            running_locks=[{"platform": "boss", "account": "a", "kind": "running"}],
            port_profiles_boss=[],
            port_profiles_zhilian=[],
        )
        self.assertFalse(ok)
        self.assertIn("browser_busy", reason)

    def test_delete_blocked_by_boss_port_use(self):
        """BOSS profile 被 9222 占用 → 阻断删除。"""
        ok, reason = check_browser_account_delete(
            "a",
            boss_profile_dir="/tmp/profile-a",
            running_locks=[],
            port_profiles_boss=["/tmp/profile-a"],
            port_profiles_zhilian=[],
        )
        self.assertFalse(ok)
        self.assertIn("browser_in_use", reason)

    def test_delete_blocked_by_zhilian_port_use(self):
        """智联 profile 被 9223 占用 → 阻断删除。"""
        zhilian_dir = derive_zhilian_profile_dir("/tmp/profile-a")
        ok, reason = check_browser_account_delete(
            "a",
            boss_profile_dir="/tmp/profile-a",
            running_locks=[],
            port_profiles_boss=[],
            port_profiles_zhilian=[zhilian_dir],
        )
        self.assertFalse(ok)
        self.assertIn("browser_in_use", reason)
        self.assertIn("zhilian", reason)

    def test_delete_atomic_check_both_profiles(self):
        """delete 原子检查两个 profile，任一占用即阻断。"""
        zhilian_dir = derive_zhilian_profile_dir("/tmp/profile-a")
        # BOSS 占用但智联不占用 → 仍阻断
        ok1, _ = check_browser_account_delete(
            "a", boss_profile_dir="/tmp/profile-a",
            running_locks=[], port_profiles_boss=["/tmp/profile-a"],
            port_profiles_zhilian=[],
        )
        self.assertFalse(ok1)
        # 智联占用但 BOSS 不占用 → 仍阻断
        ok2, _ = check_browser_account_delete(
            "a", boss_profile_dir="/tmp/profile-a",
            running_locks=[], port_profiles_boss=[],
            port_profiles_zhilian=[zhilian_dir],
        )
        self.assertFalse(ok2)

    def test_delete_does_not_leak_profile_paths_in_reason(self):
        """delete reason 不含 profile 路径。"""
        ok, reason = check_browser_account_delete(
            "a",
            boss_profile_dir="/tmp/secret-profile-path",
            running_locks=[],
            port_profiles_boss=["/tmp/secret-profile-path"],
            port_profiles_zhilian=[],
        )
        self.assertFalse(ok)
        self.assertNotIn("/tmp/secret-profile-path", reason)
        self.assertNotIn("secret", reason)

    # ----- T210: check_browser_account_activate -----

    def test_activate_allowed_when_account_exists(self):
        """账号存在 → 允许激活（只改草稿）。"""
        ok, reason = check_browser_account_activate("a", account_exists=True)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_activate_blocked_when_account_missing(self):
        """账号不存在 → 阻断激活。"""
        ok, reason = check_browser_account_activate("a", account_exists=False)
        self.assertFalse(ok)
        self.assertEqual(reason, "account_not_found")

    def test_activate_does_not_check_ports(self):
        """activate 不检查端口/profile（只改草稿），无端口参数。"""
        import inspect
        sig = inspect.signature(check_browser_account_activate)
        param_names = list(sig.parameters.keys())
        self.assertNotIn("port_profiles_boss", param_names)
        self.assertNotIn("port_profiles_zhilian", param_names)
        self.assertNotIn("running_locks", param_names)


# ===========================================================================
# T208: 端口隔离常量
# ===========================================================================
class PortIsolationTests(unittest.TestCase):
    """BOSS 9222 与智联 9223 端口隔离（T208/T211）。"""

    def test_boss_and_zhilian_ports_differ(self):
        self.assertNotEqual(BOSS_DEFAULT_CDP_PORT, ZHILIAN_DEFAULT_CDP_PORT)
        self.assertEqual(BOSS_DEFAULT_CDP_PORT, 9222)
        self.assertEqual(ZHILIAN_DEFAULT_CDP_PORT, 9223)

    def test_boss_and_zhilian_login_spaces_use_different_ports(self):
        boss_space = resolve_login_space("boss", "a", boss_profile_dir="/tmp/p")
        zhilian_space = resolve_login_space("zhilian", "a", boss_profile_dir="/tmp/p")
        self.assertNotEqual(boss_space.cdp_port, zhilian_space.cdp_port)
        self.assertNotEqual(boss_space.profile_key, zhilian_space.profile_key)
        self.assertEqual(boss_space.profile_key, "boss:a")
        self.assertEqual(zhilian_space.profile_key, "zhilian:a")


if __name__ == "__main__":
    unittest.main()
