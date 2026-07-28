"""Run a disposable WebUI fixture for browser acceptance checks."""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from webui.app import create_app


def seed_ai_pause(store) -> None:
    scrape_id = "sc015-scrape"
    store.create_screening_run(scrape_id, source_count=50)
    store.save_scrape_combo_result(
        scrape_id,
        "前端工程师|上海",
        [
            {
                "job_id": f"job-{index}",
                "title": "前端工程师",
                "company": f"测试公司 {index}",
                "salary": "20-30K",
                "location": "上海",
                "source_url": f"https://www.zhipin.com/job_detail/job-{index}.html",
            }
            for index in range(1, 51)
        ],
        ["前端工程师|上海"],
    )
    run_id = "sc015-ai-paused"
    store.create_screening_run(
        run_id,
        source_count=50,
        frozen_filters={"city": ["上海"]},
        execution_params={
            "scrape_task_id": scrape_id,
            "scrape_completed": True,
            "profile_summary": "三年前端工程师",
        },
    )
    store.update_screening_run(run_id, status="running")
    store.update_screening_run(
        run_id,
        status="paused",
        processed_count=20,
        error_code="ai_rate_limited",
        error_reason="AI 接口限流：第 21 条开始收到 429，请稍后继续",
        current_stage="ai_rough",
        backend_version="010-healthy-pipeline-recovery",
    )


def seed_recrawl_pause(store) -> None:
    result = {
        "jobs": [
            {
                "job_id": "pending-1",
                "title": "前端开发工程师",
                "company": "示例科技",
                "salary": "18-28K",
                "location": "上海",
                "verdict": "uncertain",
                "verdict_reason": "触发验证码：JD 详情尚未获取，可继续补抓",
                "source_url": "https://www.zhipin.com/job_detail/pending-1.html",
            },
            {
                "job_id": "pending-2",
                "title": "Web 前端工程师",
                "company": "示例网络",
                "salary": "20-35K",
                "location": "上海",
                "verdict": "uncertain",
                "verdict_reason": "岗位详情请求超时，尚未完成 AI 精筛",
                "source_url": "https://www.zhipin.com/job_detail/pending-2.html",
            },
        ],
        "dropped": [],
        "total_scraped": 2,
        "total_kept": 2,
        "total_matched": 0,
        "total_dropped": 0,
        "profile_summary": "三年前端工程师",
    }
    source_run_id = store.save_pipeline_result(result, {"keyword": "前端", "city": ["上海"]})
    run_id = "sc015-recrawl-paused"
    store.create_screening_run(
        run_id,
        source_count=2,
        execution_params={
            "source_run_id": source_run_id,
            "job_ids": ["pending-1", "pending-2"],
            "profile_summary": "三年前端工程师",
        },
    )
    store.update_screening_run(run_id, status="running")
    store.update_screening_run(
        run_id,
        status="paused",
        processed_count=1,
        error_code="captcha_required",
        error_reason="触发验证码：已完成 1/2 条，浏览器保持打开",
        current_stage="recrawl_fetch_jd",
        backend_version="010-healthy-pipeline-recovery",
    )
    store.save_checkpoint(run_id, "recrawl_jd", ["pending-1"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--fixture", choices=("ai", "recrawl"), required=True)
    args = parser.parse_args()

    state_dir = pathlib.Path(args.state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    app = create_app({
        "START_TASKS": False,
        "DB_PATH": str(state_dir / "webui.db"),
        "RESULT_DIR": str(state_dir / "results"),
        "RESUME_DIR": str(state_dir / "resumes"),
    })
    store = app.config["TASK_STORE"]
    if args.fixture == "ai":
        seed_ai_pause(store)
    else:
        seed_recrawl_pause(store)
    app.run(host="127.0.0.1", port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
