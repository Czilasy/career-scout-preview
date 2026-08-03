"""Tests for webui.workbench: link normalization, keywords, dedup, budget, cards."""

from __future__ import annotations

import unittest

from tests.test_workbench_fixtures import sample_job, sample_jobs, sample_detail


class LinkNormalizationTests(unittest.TestCase):
    def test_accepts_https_zhipin_domain(self):
        from webui.workbench import normalize_job_link

        url = "https://www.zhipin.com/job_detail/abc123.html"
        self.assertEqual(normalize_job_link(url), url)

    def test_accepts_bare_zhipin_domain(self):
        from webui.workbench import normalize_job_link

        url = "https://zhipin.com/job_detail/xyz.html"
        result = normalize_job_link(url)
        self.assertTrue(result.startswith("https://"))
        self.assertIn("zhipin.com", result)

    def test_rejects_http(self):
        from webui.workbench import normalize_job_link

        self.assertEqual(normalize_job_link("http://www.zhipin.com/job_detail/x.html"), "")

    def test_rejects_non_zhipin_domain(self):
        from webui.workbench import normalize_job_link

        self.assertEqual(normalize_job_link("https://evil.example.com/job_detail/x.html"), "")
        self.assertEqual(normalize_job_link("https://zhipin.evil.com/x.html"), "")

    def test_rejects_malformed_and_javascript(self):
        from webui.workbench import normalize_job_link

        self.assertEqual(normalize_job_link("javascript:alert(1)"), "")
        self.assertEqual(normalize_job_link("not a url"), "")
        self.assertEqual(normalize_job_link(""), "")

    def test_strips_unsafe_query_params_but_keeps_path(self):
        from webui.workbench import normalize_job_link

        url = "https://www.zhipin.com/job_detail/abc.html?lid=abc&securityId=def"
        result = normalize_job_link(url)
        self.assertIn("/job_detail/abc.html", result)


class JobIdentityTests(unittest.TestCase):
    def test_canonical_job_id_from_link(self):
        from webui.workbench import canonical_job_id

        job = sample_job(job_id="raw-1")
        job["job_link"] = "https://www.zhipin.com/job_detail/real-123.html"
        self.assertEqual(canonical_job_id(job), "real-123")

    def test_falls_back_to_job_id_when_link_missing(self):
        from webui.workbench import canonical_job_id

        job = {"job_id": "fallback-id", "job_link": ""}
        self.assertEqual(canonical_job_id(job), "fallback-id")


class KeywordSelectionTests(unittest.TestCase):
    def test_manual_keywords_take_priority_over_ai(self):
        from webui.workbench import select_keywords

        manual = ["Python 后端", "Go 开发"]
        ai = ["AI suggestion 1", "AI suggestion 2", "AI suggestion 3"]
        result = select_keywords(manual_keywords=manual, ai_keywords=ai, confirmed_fields={"city": "上海"})
        self.assertEqual(result, manual)

    def test_ai_keywords_capped_at_three(self):
        from webui.workbench import select_keywords

        ai = ["k1", "k2", "k3", "k4", "k5"]
        result = select_keywords(manual_keywords=[], ai_keywords=ai, confirmed_fields={"city": "上海"})
        self.assertEqual(len(result), 3)
        self.assertEqual(result, ["k1", "k2", "k3"])

    def test_no_keywords_returns_empty(self):
        from webui.workbench import select_keywords

        self.assertEqual(select_keywords(manual_keywords=[], ai_keywords=[], confirmed_fields={}), [])

    def test_requires_city_raises_when_missing(self):
        from webui.workbench import select_keywords

        with self.assertRaisesRegex(ValueError, "城市"):
            select_keywords(manual_keywords=["k"], ai_keywords=[], confirmed_fields={"city": ""})


class DedupTests(unittest.TestCase):
    def test_dedup_by_canonical_id_across_queries(self):
        from webui.workbench import dedupe_jobs

        job_a = sample_job(job_id="dup-1")
        job_a["job_link"] = "https://www.zhipin.com/job_detail/same.html"
        job_b = sample_job(job_id="dup-2")
        job_b["job_link"] = "https://www.zhipin.com/job_detail/same.html"
        job_c = sample_job(job_id="uniq-1")
        job_c["job_link"] = "https://www.zhipin.com/job_detail/unique.html"

        result = dedupe_jobs([job_a, job_b, job_c])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["job_id"], "dup-1")


class DetailBudgetTests(unittest.TestCase):
    def test_total_budget_capped_at_60(self):
        from webui.workbench import allocate_detail_budget

        # 3 queries, each wants 30 -> total must be <= 60
        budgets = allocate_detail_budget(query_count=3, total_budget=60)
        self.assertEqual(sum(budgets), 60)
        self.assertEqual(len(budgets), 3)

    def test_single_query_gets_full_budget(self):
        from webui.workbench import allocate_detail_budget

        budgets = allocate_detail_budget(query_count=1, total_budget=60)
        self.assertEqual(budgets, [60])


class CardProjectionTests(unittest.TestCase):
    def test_card_has_required_fields_no_ai_score(self):
        from webui.workbench import project_card

        job = sample_job(job_id="c-1")
        detail = sample_detail("c-1")
        card = project_card(job, detail, interest_state="new")

        for field in ("job_id", "title", "company", "salary", "location", "jd_excerpt", "canonical_url", "interest_state"):
            self.assertIn(field, card)
        # Must not expose AI score or ranking reason
        self.assertNotIn("ai_score", card)
        self.assertNotIn("ai_rank", card)
        self.assertNotIn("match_reason", card)

    def test_jd_excerpt_truncated(self):
        from webui.workbench import project_card

        job = sample_job(job_id="c-2")
        long_detail = sample_detail("c-2")
        long_detail["jd"] = "x" * 5000
        card = project_card(job, long_detail, interest_state="new")
        self.assertLess(len(card["jd_excerpt"]), 5000)

    def test_canonical_url_validated(self):
        from webui.workbench import project_card

        job = sample_job(job_id="c-3")
        job["job_link"] = "https://evil.example.com/x.html"
        detail = sample_detail("c-3")
        card = project_card(job, detail, interest_state="new")
        self.assertEqual(card["canonical_url"], "")


class FeedbackAggregationTests(unittest.TestCase):
    def test_interest_state_updated(self):
        from webui.workbench import aggregate_feedback_state

        events = [
            {"action": "interested", "revoked_at": None},
            {"action": "not_interested", "revoked_at": None},
        ]
        state = aggregate_feedback_state(events)
        # Last non-revoked wins
        self.assertEqual(state, "not_interested")

    def test_revoked_feedback_ignored(self):
        from webui.workbench import aggregate_feedback_state

        events = [
            {"action": "not_interested", "revoked_at": "2026-01-01T00:00:00Z"},
            {"action": "interested", "revoked_at": None},
        ]
        state = aggregate_feedback_state(events)
        self.assertEqual(state, "interested")

    def test_no_events_is_new(self):
        from webui.workbench import aggregate_feedback_state

        self.assertEqual(aggregate_feedback_state([]), "new")


class ProfileOverrideTests(unittest.TestCase):
    def test_manual_fields_override_ai_suggestions(self):
        from webui.workbench import merge_profile_fields

        confirmed = {"city": "北京", "roles": ["Java 工程师"]}
        ai_suggestion = {"city": "上海", "roles": ["Python 后端"], "skills": ["Python"]}
        merged = merge_profile_fields(confirmed, ai_suggestion)
        self.assertEqual(merged["city"], "北京")
        self.assertEqual(merged["roles"], ["Java 工程师"])
        self.assertEqual(merged["skills"], ["Python"])

    def test_empty_confirmed_uses_ai(self):
        from webui.workbench import merge_profile_fields

        ai_suggestion = {"city": "上海", "roles": ["Python 后端"]}
        merged = merge_profile_fields({}, ai_suggestion)
        self.assertEqual(merged["city"], "上海")

    def test_explicit_empty_string_overrides_ai(self):
        """An explicit empty string is a deliberate manual clear, not unset."""
        from webui.workbench import merge_profile_fields

        confirmed = {"city": ""}
        ai_suggestion = {"city": "上海", "skills": ["Python"]}
        merged = merge_profile_fields(confirmed, ai_suggestion)
        self.assertEqual(merged["city"], "")
        self.assertEqual(merged["skills"], ["Python"])

    def test_explicit_empty_list_overrides_ai(self):
        """An explicit empty list is a deliberate manual clear, not unset."""
        from webui.workbench import merge_profile_fields

        confirmed = {"roles": []}
        ai_suggestion = {"roles": ["Python 后端"], "city": "北京"}
        merged = merge_profile_fields(confirmed, ai_suggestion)
        self.assertEqual(merged["roles"], [])
        self.assertEqual(merged["city"], "北京")

    def test_none_value_skipped(self):
        """Only None (key not provided) is skipped; AI value preserved."""
        from webui.workbench import merge_profile_fields

        confirmed = {"city": None, "roles": ["Java"]}
        ai_suggestion = {"city": "上海", "roles": ["Python"]}
        merged = merge_profile_fields(confirmed, ai_suggestion)
        self.assertEqual(merged["city"], "上海")
        self.assertEqual(merged["roles"], ["Java"])


class PlatformAwareLinkNormalizationTests(unittest.TestCase):
    """T009: normalize_job_link_for_platform 平台感知 URL 规范化。"""

    def test_none_platform_falls_back_to_boss(self):
        from webui.workbench import (
            normalize_job_link,
            normalize_job_link_for_platform,
        )

        url = "https://www.zhipin.com/job_detail/abc.html"
        self.assertEqual(normalize_job_link_for_platform(url, platform=None), url)
        self.assertEqual(
            normalize_job_link_for_platform(url, platform=None),
            normalize_job_link(url),
        )

    def test_boss_platform_matches_legacy_function(self):
        from webui.workbench import (
            normalize_job_link,
            normalize_job_link_for_platform,
        )

        for raw in (
            "https://www.zhipin.com/job_detail/abc.html",
            "https://zhipin.com/job_detail/x.html",
            "http://www.zhipin.com/job_detail/x.html",
            "https://evil.example.com/x.html",
            "",
            "not a url",
        ):
            self.assertEqual(
                normalize_job_link_for_platform(raw, platform="boss"),
                normalize_job_link(raw),
                f"BOSS 平台分支应与 legacy 完全一致: {raw!r}",
            )

    def test_unknown_platform_rejected(self):
        from webui.workbench import normalize_job_link_for_platform
        from webui.platforms import UnknownPlatformError

        with self.assertRaises(UnknownPlatformError):
            normalize_job_link_for_platform(
                "https://www.zhaopin.com/jobdetail/abc.htm",
                platform="linkedin",
            )

    def test_zhilian_not_registered_raises(self):
        """智联已知但未注册（真实 fixture 未核验）时拒绝，不回退 BOSS。"""
        from webui.workbench import normalize_job_link_for_platform
        from webui.platforms import PlatformNotRegisteredError

        with self.assertRaises(PlatformNotRegisteredError):
            normalize_job_link_for_platform(
                "https://www.zhaopin.com/jobdetail/abc.htm",
                platform="zhilian",
            )

    def test_zhilian_normalizes_when_registered(self):
        """注册测试用智联平台后，链接按 zhaopin 规则归一化。"""
        from webui.workbench import normalize_job_link_for_platform
        from webui.platforms import (
            PlatformRegistry,
            PlatformFilterSchema,
            PlatformCityCatalog,
            register_platform,
            normalize_zhilian_job_url,
            resolve_zhilian_login_space,
        )

        schema = PlatformFilterSchema(
            platform="zhilian",
            schema_version=1,
            enabled_for_new_tasks=False,
            fields=(),
        )
        catalog = PlatformCityCatalog(
            platform="zhilian", mapping_version=1, entries=(),
        )
        reg = PlatformRegistry(
            key="zhilian",
            display_name="智联招聘",
            filter_schema=schema,
            city_catalog=catalog,
            enabled_for_new_tasks=False,
            availability_reason="测试",
            default_cdp_port=9223,
            normalize_job_url_fn=normalize_zhilian_job_url,
            resolve_login_space_fn=resolve_zhilian_login_space,
        )
        try:
            register_platform(reg)
            url = "https://www.zhaopin.com/jobdetail/abc123.htm?lid=x#frag"
            self.assertEqual(
                normalize_job_link_for_platform(url, platform="zhilian"),
                "https://www.zhaopin.com/jobdetail/abc123.htm",
            )
            # http 升级
            self.assertEqual(
                normalize_job_link_for_platform(
                    "http://www.zhaopin.com/jobdetail/abc.htm",
                    platform="zhilian",
                ),
                "https://www.zhaopin.com/jobdetail/abc.htm",
            )
            # 非 zhaopin 域名拒绝
            self.assertEqual(
                normalize_job_link_for_platform(
                    "https://evil.example.com/jobdetail/abc.htm",
                    platform="zhilian",
                ),
                "",
            )
            # 非 jobdetail path 拒绝
            self.assertEqual(
                normalize_job_link_for_platform(
                    "https://www.zhaopin.com/sou/abc.htm",
                    platform="zhilian",
                ),
                "",
            )
        finally:
            # 清理注册表，避免污染其它测试。
            from webui.platforms import _REGISTRY
            _REGISTRY.pop("zhilian", None)


class PlatformAwareCardProjectionTests(unittest.TestCase):
    """T009: project_card 平台感知 canonical_url。"""

    def test_card_with_boss_platform_uses_boss_rules(self):
        from webui.workbench import project_card

        job = sample_job(job_id="c-boss")
        job["job_link"] = "https://www.zhipin.com/job_detail/abc.html"
        detail = sample_detail("c-boss")
        card = project_card(job, detail, platform="boss")
        self.assertEqual(
            card["canonical_url"], "https://www.zhipin.com/job_detail/abc.html"
        )

    def test_card_job_platform_field_overrides_param(self):
        """岗位自身 platform 字段优先于调用方 platform 参数（FR-012）。"""
        from webui.workbench import project_card

        job = sample_job(job_id="c-boss-2")
        job["platform"] = "boss"
        job["job_link"] = "https://www.zhipin.com/job_detail/abc.html"
        detail = sample_detail("c-boss-2")
        # 调用方传 zhilian 但岗位自身是 boss，应走 boss 规则
        # （zhilian 未注册，若走 zhilian 会抛错；走 boss 则成功）
        card = project_card(job, detail, platform="zhilian")
        self.assertEqual(
            card["canonical_url"], "https://www.zhipin.com/job_detail/abc.html"
        )

    def test_card_without_platform_falls_back_to_boss(self):
        """省略 platform 时退化为 BOSS 兼容行为（保持现有调用方不变）。"""
        from webui.workbench import project_card

        job = sample_job(job_id="c-legacy")
        job["job_link"] = "https://evil.example.com/x.html"
        detail = sample_detail("c-legacy")
        card = project_card(job, detail)
        self.assertEqual(card["canonical_url"], "")


if __name__ == "__main__":
    unittest.main()
