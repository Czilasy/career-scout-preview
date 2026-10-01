"""BOSS/智联公共 AI evaluator 的平台字段适配回归。"""

from __future__ import annotations

import unittest
from unittest.mock import patch


class ZhilianFilterMapperTests(unittest.TestCase):
    @staticmethod
    def _job(*, nature: str = "民营", salary: str = "5K-8K") -> dict:
        return {
            "platform": "zhilian",
            "job_id": "zh-j1",
            "title": "后端工程师",
            "salary": salary,
            "location": "上海",
            "experience": "3-5年",
            "degree": "本科",
            "extra": {
                "company_size": "100-299人",
                "industry": "互联网/AI/软件/IT服务",
                "company_nature_label": nature,
            },
        }

    def test_company_nature_hard_filter_uses_zhilian_mapper(self):
        from webui.ai_filters import _job_criteria_hard_mismatch

        field, reason = _job_criteria_hard_mismatch(
            self._job(), {"company_nature": ["1"]}, platform="zhilian",
        )

        self.assertEqual(field, "company_nature")
        self.assertIn("民营", reason)

    def test_zhilian_criteria_description_contains_platform_specific_fields(self):
        from webui.ai_filters import _build_criteria_description

        description = _build_criteria_description({
            "experience": ["0305"],
            "degree": ["4"],
            "industry": ["1800000000"],
            "scale": ["3"],
            "company_nature": ["1"],
        }, "zhilian")

        self.assertIn("3-5年", description)
        self.assertIn("本科", description)
        self.assertIn("互联网/AI/软件/IT服务", description)
        self.assertIn("100-299人", description)  # schema label for selected scale
        self.assertIn("国企", description)

    def test_zhilian_salary_codes_are_hard_filtered(self):
        from webui.ai_filters import _job_criteria_hard_mismatch

        field, _ = _job_criteria_hard_mismatch(
            self._job(salary="8K-10K"),
            {"salary": ["4001,6000"]},
            platform="zhilian",
        )

        self.assertEqual(field, "salary")

    def test_screen_jobs_prompt_and_payload_include_zhilian_company_nature(self):
        from webui.ai import screen_jobs

        with patch("webui.ai.call_ai", return_value={"dropped": []}) as call:
            result = screen_jobs(
                [self._job(nature="国企")],
                {"company_nature": ["1"]},
                "https://example.invalid", "test-key",
                batch_size=1, platform="zhilian",
            )

        self.assertEqual(result["kept"], ["zh-j1"])
        messages = call.call_args.args[2]
        self.assertIn("公司性质：国企", messages[0]["content"])
        self.assertIn("国企", messages[1]["content"])

    def test_match_jds_hard_filters_zhilian_company_nature_before_ai(self):
        from webui.ai import match_jds

        with patch("webui.ai.call_ai") as call:
            result = match_jds(
                [{**self._job(), "jd": "负责后端开发"}],
                "Python 后端",
                "https://example.invalid", "test-key",
                batch_size=1,
                criteria={"company_nature": ["1"]},
                platform="zhilian",
            )

        self.assertEqual(result["verdicts"]["zh-j1"]["verdict"], "not_match")
        call.assert_not_called()

    def test_screen_prompt_uses_current_platform_fields_only(self):
        from webui.ai import screen_jobs

        boss_job = {**self._job(), "platform": "boss", "job_id": "boss-j1"}
        with patch("webui.ai.call_ai", return_value={"dropped": []}) as boss_call:
            screen_jobs(
                [boss_job], {}, "https://example.invalid", "test-key",
                batch_size=1, platform="boss",
            )
        boss_prompt = boss_call.call_args.args[2][0]["content"]
        self.assertNotIn("智联", boss_prompt)
        self.assertNotIn("公司性质", boss_prompt)

        with patch("webui.ai.call_ai", return_value={"dropped": []}) as zhilian_call:
            screen_jobs(
                [self._job()], {}, "https://example.invalid", "test-key",
                batch_size=1, platform="zhilian",
            )
        zhilian_prompt = zhilian_call.call_args.args[2][0]["content"]
        self.assertIn("行业和公司性质", zhilian_prompt)

    def test_schema_registry_failure_does_not_continue_hard_screening(self):
        """平台注册表故障必须传播到公共边界，不能生成错误筛选结果。"""
        from webui.ai_filters import _job_criteria_hard_mismatch

        with patch(
            "webui.ai_platform_adapter.get_platform",
            side_effect=RuntimeError("registry unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                _job_criteria_hard_mismatch(
                    self._job(), {"company_nature": ["1"]}, platform="zhilian",
                )

    def test_missing_schema_field_does_not_return_empty_mapping(self):
        """注册项缺少字段时不能静默返回空 map 并继续硬筛。"""
        from types import SimpleNamespace
        from webui.ai_filters import _job_criteria_hard_mismatch

        broken_registry = SimpleNamespace(
            filter_schema=SimpleNamespace(get_field=lambda _field: None),
        )
        with patch(
            "webui.ai_platform_adapter.get_platform",
            return_value=broken_registry,
        ):
            with self.assertRaises(ValueError):
                _job_criteria_hard_mismatch(
                    self._job(), {"company_nature": ["1"]}, platform="zhilian",
                )

    def test_combined_experience_codes_have_one_adapter_source(self):
        from webui.ai_filters import _COMBINED_EXPERIENCE_CODES as filters_codes
        from webui.ai_platform_adapter import _COMBINED_EXPERIENCE_CODES as adapter_codes

        self.assertIs(filters_codes, adapter_codes)

    def test_zhilian_experience_field_level_unrestricted_code_is_only_all(self):
        """智联经验只有 ``-99``（全部）是字段级不限制；``-1``（经验不限）是岗位属性档。"""
        from webui.ai_platform_adapter import resolve_platform_ai_adapter

        adapter = resolve_platform_ai_adapter("zhilian")
        self.assertTrue(adapter.is_unrestricted("experience", "-99"))
        self.assertFalse(adapter.is_unrestricted("experience", "-1"))
        self.assertTrue(adapter.is_unrestricted("degree", "-1"))
        self.assertTrue(adapter.is_unrestricted("salary", "0000,9999999"))
        self.assertFalse(adapter.is_unrestricted("salary", "-1"))

    def test_zhilian_flexible_experience_job_survives_the_under_one_year_mapping(self):
        """契约「1年以下」= 经验不限 + 1年以下，标注「经验不限」的岗位必须放行。"""
        from webui.ai_filters import _job_criteria_hard_mismatch

        job = {**self._job(), "experience": "经验不限"}
        field, reason = _job_criteria_hard_mismatch(
            job, {"experience": ["-1", "0001"]}, platform="zhilian",
        )

        self.assertIsNone(field, reason)

    def test_zhilian_flexible_experience_code_still_hard_filters_other_tiers(self):
        """``-1`` 回归普通档后，只选「经验不限」时 3-5年 岗位必须被刷掉。"""
        from webui.ai_filters import _job_criteria_hard_mismatch

        field, reason = _job_criteria_hard_mismatch(
            self._job(), {"experience": ["-1"]}, platform="zhilian",
        )

        self.assertEqual(field, "experience")
        self.assertIn("不在筛选范围", reason)

    def test_zhilian_experience_all_code_adds_no_hard_filter(self):
        """字段级不限制码单独出现即该字段不增加限制。"""
        from webui.ai_filters import _job_criteria_hard_mismatch

        field, reason = _job_criteria_hard_mismatch(
            self._job(), {"experience": ["-99"]}, platform="zhilian",
        )

        self.assertIsNone(field, reason)


class BossExperienceFieldAdapterTests(unittest.TestCase):
    """BOSS 经验字段适配：码表见 ``scripts/boss/constants.py`` 的 ``EXPERIENCE_MAP``。"""

    @staticmethod
    def _job(*, experience: str) -> dict:
        return {
            "platform": "boss",
            "job_id": "boss-j1",
            "title": "后端工程师",
            "salary": "15-20K",
            "location": "上海",
            "job_labels": experience,
        }

    def test_boss_unrestricted_codes_match_the_platform_code_table(self):
        """逐码核对 BOSS 适配：只有码表里名为「不限」的码是字段级不限制。

        码表出处 ``scripts/boss/constants.py``（SALARY_MAP/EXPERIENCE_MAP/
        DEGREE_MAP 各有一个「不限」=0；INDUSTRY_MAP/SCALE_MAP/STAGE_MAP 没有）。
        「经验不限」=101 是岗位自身属性档，必须按普通档参与硬筛。
        """
        from scripts import boss_cdp_raw as boss
        from webui.ai_platform_adapter import resolve_platform_ai_adapter

        adapter = resolve_platform_ai_adapter("boss")
        code_maps = {
            "salary": boss.SALARY_MAP, "experience": boss.EXPERIENCE_MAP,
            "degree": boss.DEGREE_MAP, "industry": boss.INDUSTRY_MAP,
            "scale": boss.SCALE_MAP, "stage": boss.STAGE_MAP,
        }
        for field, mapping in code_maps.items():
            for label, code in mapping.items():
                with self.subTest(field=field, label=label):
                    self.assertEqual(
                        adapter.is_unrestricted(field, code), label == "不限",
                    )
        self.assertEqual(boss.EXPERIENCE_MAP["经验不限"], "101")
        self.assertFalse(adapter.is_unrestricted("experience", "101"))

    def test_boss_experience_without_selection_adds_no_hard_filter(self):
        """用户未挑经验 ⇒ BOSS 经验字段不产生任何硬筛。"""
        from webui.ai_filters import _job_criteria_hard_mismatch

        for criteria in ({"experience": []}, {}, {"experience": ["0"]}):
            with self.subTest(criteria=criteria):
                field, reason = _job_criteria_hard_mismatch(
                    self._job(experience="3-5年"), criteria, platform="boss",
                )
                self.assertIsNone(field, reason)

    def test_boss_under_one_year_mapping_keeps_flexible_and_student_jobs(self):
        """契约「1年以下」= 在校生 + 应届生 + 经验不限 + 1年以内，四类岗位全部放行。"""
        from webui.ai_filters import _job_criteria_hard_mismatch

        selected = ["108", "102", "101", "103"]
        for experience in ("在校生", "应届生", "经验不限", "1年以内", "在校/应届"):
            with self.subTest(experience=experience):
                field, reason = _job_criteria_hard_mismatch(
                    self._job(experience=experience),
                    {"experience": selected},
                    platform="boss",
                )
                self.assertIsNone(field, reason)

    def test_boss_flexible_experience_code_still_hard_filters_other_tiers(self):
        from webui.ai_filters import _job_criteria_hard_mismatch

        field, reason = _job_criteria_hard_mismatch(
            self._job(experience="3-5年"), {"experience": ["101"]}, platform="boss",
        )

        self.assertEqual(field, "experience")
        self.assertIn("经验", reason)


if __name__ == "__main__":
    unittest.main()
