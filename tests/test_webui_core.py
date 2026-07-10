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


if __name__ == "__main__":
    unittest.main()
