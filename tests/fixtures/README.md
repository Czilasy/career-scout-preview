# 测试样本索引

本目录存放 AI 求职工作台自动化测试使用的无敏感数据样本。所有样本均为虚构，不含真实个人身份信息、真实 API Key 或真实 BOSS 岗位标识。

## 样本清单

- `sample_jobs.json` — 虚构 BOSS 岗位列表（5 条），用于搜索、去重和卡片投影测试。
- `sample_details.json` — 虚构岗位 JD 详情（5 条），与 `sample_jobs.json` 一一对应。
- `sample_ai_resume.json` — AI 简历解析的模拟 JSON 响应，用于画像建议与字段覆盖测试。
- `sample_ai_rank.json` — AI JD 排序的模拟 JSON 响应，用于分批排序与 job_id 校验测试。
- `sample_ai_preference.json` — AI 偏好更新的模拟 JSON 响应，用于五条反馈触发偏好更新测试。

## 简历样本

简历样本由 `tests/test_workbench_fixtures.py` 中的 `sample_resume_text()`、`sample_pdf_bytes()`、`sample_docx_bytes()` 在运行时动态生成，不落盘真实简历文件。动态生成的简历内容为虚构的"张三 / Python 后端 / 上海"文本，不含任何真实个人信息。

## 安全约束

- 不得在此目录放置真实简历、真实 API Key 或真实 BOSS 抓取结果。
- 所有 `job_id`、`job_link` 使用 `job-xxx` 和 `zhipin.com/job_detail/` 演示路径。
- 所有 AI 响应样本只包含应用端可校验的 JSON 结构，不含真实模型输出。
