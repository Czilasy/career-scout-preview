"""Shared test fixtures and helpers for the AI job workbench tests.

Provides temporary state directories, sample resume builders, fake AI
responses, and BOSS-shaped job/detail payloads that contain no sensitive
data.  Keep all samples generic and non-identifiable.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import uuid
from contextlib import contextmanager


FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"


def make_temp_root():
    """Create a temporary project root and return its path."""
    return pathlib.Path(tempfile.mkdtemp(prefix="boss-wb-"))


@contextmanager
def temp_state_layout():
    """Yield (root, state_dir, result_dir, resume_dir) for an isolated run."""
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


def sample_resume_text():
    """Non-sensitive resume text used across resume and AI tests."""
    return (
        "张三\n"
        "求职意向：Python 后端工程师\n"
        "期望城市：上海\n"
        "技能：Python, FastAPI, Redis, PostgreSQL\n"
        "经历：3 年后端开发，熟悉微服务与 API 设计\n"
    )


def sample_pdf_bytes(text: str | None = None):
    """Build a minimal single-page PDF containing *text* using pypdf."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    # pypdf cannot embed arbitrary text without reportlab; tests that need
    # real PDF text extraction build a richer file via reportlab when available.
    # For structural tests this minimal PDF is sufficient.
    import io

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def sample_docx_bytes(text: str | None = None):
    """Build a minimal DOCX containing *text* using python-docx."""
    from docx import Document

    doc = Document()
    doc.add_paragraph(text or sample_resume_text())
    import io

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def sample_ai_resume_response():
    """Fake AI JSON response for resume parsing."""
    return {
        "profile_name": "Python 后端",
        "city": "上海",
        "roles": ["Python 后端工程师", "后端开发工程师"],
        "skills": ["Python", "FastAPI", "Redis", "PostgreSQL"],
        "keywords": ["Python 后端", "FastAPI 后端", "微服务开发"],
        "suggestions": [
            {"field": "city", "value": "上海", "source": "resume", "uncertain": False},
            {"field": "roles", "value": ["Python 后端工程师"], "source": "resume", "uncertain": False},
        ],
    }


def sample_ai_rank_response(job_ids):
    """Fake AI JSON response for JD ranking. Returns ordered job_ids."""
    return {
        "ranked_job_ids": list(job_ids),
        "labels": {jid: None for jid in job_ids},
    }


def sample_ai_preference_response():
    """Fake AI JSON response for preference update."""
    return {
        "positive_terms": ["Python", "FastAPI"],
        "negative_terms": ["外包"],
        "keyword_weights": {"Python": 1.0, "FastAPI": 0.8},
        "uncertain": [],
    }


def sample_job(job_id=None, title="Python 后端工程师", company="示例科技"):
    """A single BOSS-shaped list job with no real identifiers."""
    return {
        "job_id": job_id or f"job-{uuid.uuid4().hex[:8]}",
        "title": title,
        "boss_name": company,
        "salary": "25-35K",
        "location": "上海·浦东新区·张江",
        "skills": "Python | Redis",
        "job_labels": ["Python", "后端"],
        "tags": [],
        "job_link": f"https://www.zhipin.com/job_detail/{job_id or 'demo'}.html",
    }


def sample_jobs(count=5):
    """Return *count* BOSS-shaped jobs with distinct ids."""
    return [sample_job(job_id=f"job-{i:03d}") for i in range(count)]


def sample_detail(job_id="job-000"):
    """A single BOSS-shaped detail record with JD text."""
    return {
        "job_id": job_id,
        "jd": "负责使用 Python 和 FastAPI 开发后端服务，熟悉 Redis 与 PostgreSQL，"
        "参与微服务架构设计，编写单元测试，与前端协作交付 API。",
        "skill_tags": ["Python", "FastAPI", "Redis"],
    }


def sample_details(job_ids):
    """Return detail records for each job_id."""
    return [sample_detail(jid) for jid in job_ids]


def write_json_file(path: pathlib.Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_fixture(name: str):
    """Load a JSON fixture from tests/fixtures/."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
