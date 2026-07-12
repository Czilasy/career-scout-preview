"""Shared test fixtures for 002 resume-driven screening tests.

Extends the 001 fixture pattern with screening-specific samples:
filter conditions, AI suggest responses, BOSS-shaped jobs carrying the
fields needed for hard-rule verification (city/salary/experience/degree/
scale/stage/industry), and JD text. All samples are non-sensitive and
non-identifiable.

Job field shape mirrors ``scripts/boss_cdp_raw.py`` list output:
title, salary, salary_source, location, tags, boss_name, company_scale,
company_stage, company_industry, job_labels, skills, job_link, welfare.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import uuid
from contextlib import contextmanager


FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# Temporary state layout
# ---------------------------------------------------------------------------

def make_temp_root():
    """Create a temporary project root and return its path."""
    return pathlib.Path(tempfile.mkdtemp(prefix="boss-screening-"))


@contextmanager
def temp_screening_layout():
    """Yield (root, state_dir, result_dir, resume_dir) for an isolated screening run."""
    root = make_temp_root()
    state_dir = root / "state"
    result_dir = root / "results"
    resume_dir = root / "resumes"
    for path in (state_dir, result_dir, resume_dir):
        path.mkdir(parents=True, exist_ok=True)
    try:
        yield root, state_dir, result_dir, resume_dir
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Resume text (non-sensitive, 构造数据)
# ---------------------------------------------------------------------------

def sample_resume_text():
    """Non-sensitive resume text used across screening and AI tests."""
    return (
        "张三\n"
        "求职意向：Python 后端工程师\n"
        "期望城市：上海\n"
        "期望薪资：20-30K\n"
        "学历：本科\n"
        "经验：3-5年\n"
        "技能：Python, FastAPI, Redis, PostgreSQL\n"
    )


# ---------------------------------------------------------------------------
# Filter conditions (用户确认的筛选条件，字段均可空，无强制必填)
# 字段值来自 scripts/boss_cdp_raw.py 的映射代码：
#   salary: SALARY_MAP     (405 = 10-20K)
#   experience: EXPERIENCE_MAP (105 = 3-5年)
#   degree: DEGREE_MAP     (203 = 本科)
#   scale: SCALE_MAP       (303 = 100-499人)
#   stage: STAGE_MAP       (804 = B轮)
#   industry: INDUSTRY_MAP (1001 = 互联网)
#   city: CITY_MAP 的 key (中文城市名，如 "上海")
# ---------------------------------------------------------------------------

def sample_filters_full():
    """所有字段都填的筛选条件。"""
    return {
        "city": "上海",
        "salary": "405",
        "experience": "105",
        "degree": "203",
        "scale": "303",
        "stage": "804",
        "industry": "1001",
    }


def sample_filters_partial():
    """只填部分字段，验证无强制必填。"""
    return {
        "city": "上海",
        "salary": "405",
    }


def sample_filters_empty():
    """全空筛选条件：第一层全国搜索，第二层不核任何字段。"""
    return {}


def sample_filters_city_only():
    """仅填城市。"""
    return {"city": "上海"}


# ---------------------------------------------------------------------------
# AI 填筛建议响应（模拟 AI 读简历后返回的筛选项建议 JSON）
# 字段结构与 sample_filters_* 一致；空字符串表示 AI 无法从简历提取。
# ---------------------------------------------------------------------------

def sample_ai_suggest_response():
    """AI 能从简历提取多个字段的建议响应。"""
    return {
        "city": "上海",
        "salary": "405",
        "experience": "105",
        "degree": "203",
        "scale": "",
        "stage": "",
        "industry": "",
    }


def sample_ai_suggest_response_partial():
    """AI 只能从简历提取部分字段，其余留空。"""
    return {
        "city": "上海",
        "salary": "",
        "experience": "",
        "degree": "203",
        "scale": "",
        "stage": "",
        "industry": "",
    }


# ---------------------------------------------------------------------------
# BOSS-shaped jobs（字段结构与 scripts/boss_cdp_raw.py 输出一致）
# location 格式: "城市·区·商圈"（"·"分隔，城市为第一段）
# tags 格式: "经验 | 学历"（过滤"不限"后用 " | " 连接）
# ---------------------------------------------------------------------------

def sample_screening_job(
    job_id=None,
    *,
    title="Python 后端工程师",
    salary="25-35K",
    location="上海·浦东新区·张江",
    tags="3-5年 | 本科",
    boss_name="示例科技",
    company_scale="100-499人",
    company_stage="B轮",
    company_industry="互联网",
    job_link=None,
):
    """单条 BOSS 抓取格式的岗位，含硬规则核验所需字段。"""
    jid = job_id or f"job-{uuid.uuid4().hex[:8]}"
    return {
        "job_id": jid,
        "title": title,
        "salary": salary,
        "salary_source": "api",
        "location": location,
        "tags": tags,
        "boss_name": boss_name,
        "company_scale": company_scale,
        "company_stage": company_stage,
        "company_industry": company_industry,
        "job_labels": "Python | 后端",
        "skills": "Python | Redis",
        "job_link": job_link or f"https://www.zhipin.com/job_detail/{jid}.html",
        "welfare": "",
    }


def sample_screening_jobs(count=5):
    """返回 count 条岗位，全部符合 sample_filters_full()。"""
    return [sample_screening_job(job_id=f"job-{i:03d}") for i in range(count)]


def sample_mismatch_job(field="salary"):
    """返回一条与 sample_filters_full() 在指定字段上不匹配的岗位。

    field 取值: salary/city/experience/degree/scale/stage/industry。
    """
    overrides = {
        "salary": {"salary": "3-5K"},
        "city": {"location": "北京·朝阳区·望京"},
        "experience": {"tags": "1-3年 | 本科"},
        "degree": {"tags": "3-5年 | 大专"},
        "scale": {"company_scale": "20-99人"},
        "stage": {"company_stage": "未融资"},
        "industry": {"company_industry": "金融"},
    }
    base = sample_screening_job(job_id="job-mismatch")
    base.update(overrides.get(field, {}))
    return base


# ---------------------------------------------------------------------------
# JD text（用于 AI 语义相似度占位接口的输入）
# ---------------------------------------------------------------------------

def sample_jd_text():
    return (
        "负责使用 Python 和 FastAPI 开发后端服务，熟悉 Redis 与 PostgreSQL，"
        "参与微服务架构设计，编写单元测试，与前端协作交付 API。"
    )


def sample_detail(job_id="job-000"):
    return {
        "job_id": job_id,
        "jd": sample_jd_text(),
        "skill_tags": ["Python", "FastAPI", "Redis"],
    }


def sample_details(job_ids):
    return [sample_detail(jid) for jid in job_ids]


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def write_json_file(path: pathlib.Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_fixture(name: str):
    """Load a JSON fixture from tests/fixtures/."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Smoke test: fixtures importable and structurally sound
# ---------------------------------------------------------------------------

import unittest


class ScreeningFixturesSmokeTests(unittest.TestCase):
    """确保夹具可 import 且样本结构正确，供后续测试任务引用。"""

    def test_temp_layout_creates_and_cleans(self):
        with temp_screening_layout() as (root, state_dir, result_dir, resume_dir):
            self.assertTrue(state_dir.is_dir())
            self.assertTrue(result_dir.is_dir())
            self.assertTrue(resume_dir.is_dir())
            self.assertTrue(root.is_dir())
        self.assertFalse(root.exists())

    def test_filters_full_has_all_seven_fields(self):
        filters = sample_filters_full()
        expected = {"city", "salary", "experience", "degree", "scale", "stage", "industry"}
        self.assertEqual(set(filters.keys()), expected)

    def test_filters_empty_has_no_required_fields(self):
        self.assertEqual(sample_filters_empty(), {})

    def test_ai_suggest_response_fields_match_filters(self):
        suggest = sample_ai_suggest_response()
        self.assertEqual(set(suggest.keys()), set(sample_filters_full().keys()))

    def test_screening_job_has_verification_fields(self):
        job = sample_screening_job()
        for field in ("title", "salary", "location", "tags",
                      "company_scale", "company_stage", "company_industry", "job_link"):
            self.assertIn(field, job, f"job 缺少硬规则核验字段: {field}")

    def test_mismatch_job_differs_on_requested_field(self):
        full = sample_filters_full()
        for field in ("salary", "city", "experience", "degree", "scale", "stage", "industry"):
            job = sample_mismatch_job(field)
            # 每个不匹配岗位至少有一个字段与 full 对应的匹配岗位不同
            baseline = sample_screening_job()
            self.assertNotEqual(
                _job_field_value(job, field),
                _job_field_value(baseline, field),
                f"mismatch job 在 {field} 上未与基线区分",
            )

    def test_job_link_is_https_zhipin(self):
        job = sample_screening_job()
        self.assertTrue(job["job_link"].startswith("https://"))
        self.assertIn("zhipin.com", job["job_link"])


def _job_field_value(job, field):
    """从 job 提取指定核验字段的值（与硬规则核验的取值方式一致）。"""
    if field == "city":
        return (job.get("location") or "").split("·")[0]
    if field == "salary":
        return job.get("salary")
    if field in ("experience", "degree"):
        return job.get("tags")
    if field == "scale":
        return job.get("company_scale")
    if field == "stage":
        return job.get("company_stage")
    if field == "industry":
        return job.get("company_industry")
    return None


if __name__ == "__main__":
    unittest.main()
