"""tasks001 T003 — 平台注册表 BOSS 基线测试。

验证 ``webui/platforms.py`` 作为唯一平台注册边界，符合
``specs/001-add-zhilian-platform/contracts/platform-schema.md`` 合同。

覆盖：
- 已知平台键集合与默认平台；
- BOSS 注册项字段集合、城市目录、URL allowlist、默认端口和兼容行为；
- 智联已知但未注册（真实 fixture 未核验前保持禁用）；
- FilterOption / FilterField / PlatformFilterSchema 不可变值对象校验；
- 登录空间解析、URL 规范化、schema 投影与筛选快照。
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
    ZHILIAN_DEFAULT_CDP_PORT,
    build_filter_snapshot,
    get_platform,
    get_platform_or_none,
    is_known_platform_key,
    list_platform_keys,
    list_platforms,
    normalize_job_url,
    project_filter_schema,
    resolve_login_space,
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

    def test_zhilian_known_but_not_registered(self):
        """智联已知但真实 fixture 未核验前不注册。"""
        self.assertIn("zhilian", KNOWN_PLATFORM_KEYS)
        with self.assertRaises(PlatformNotRegisteredError):
            get_platform("zhilian")
        self.assertIsNone(get_platform_or_none("zhilian"))

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

    def test_zhilian_not_registered_url_raises(self):
        with self.assertRaises(PlatformNotRegisteredError):
            normalize_job_url("zhilian", "https://www.zhaopin.com/jobdetail/abc.htm")

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

    def test_zhilian_schema_unavailable(self):
        with self.assertRaises(PlatformNotRegisteredError):
            project_filter_schema("zhilian")


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


if __name__ == "__main__":
    unittest.main()
