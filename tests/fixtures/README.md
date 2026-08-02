# 测试样本索引

本目录存放 AI 求职工作台自动化测试使用的无敏感数据样本。所有样本均为虚构，不含真实个人身份信息、真实 API Key 或真实 BOSS 岗位标识。

## 样本清单

### 基础 CLI 工作台

- `sample_jobs.json` — 虚构 BOSS 岗位列表（5 条），用于搜索、去重和卡片投影测试。
- `sample_details.json` — 虚构岗位 JD 详情（5 条），与 `sample_jobs.json` 一一对应。
- `sample_ai_resume.json` — AI 简历解析的模拟 JSON 响应，用于画像建议与字段覆盖测试。
- `sample_ai_rank.json` — AI JD 排序的模拟 JSON 响应，用于分批排序与 job_id 校验测试。
- `sample_ai_preference.json` — AI 偏好更新的模拟 JSON 响应，用于五条反馈触发偏好更新测试。

### 简历驱动的两层筛选

- `sample_ai_suggest.json` — AI 读简历给筛选项建议的模拟 JSON 响应，字段结构与筛选条件一致（city/salary/experience/degree/scale/stage/industry），空字符串表示 AI 无法从简历提取。
- `sample_screening_jobs.json` — 虚构 BOSS 岗位列表（5 条），字段结构与 `scripts/boss_cdp_raw.py` 输出一致，含硬规则核验所需字段（location/tags/company_scale/company_stage/company_industry），全部符合 `sample_screening_filters.json` 的 full 条件。
- `sample_screening_filters.json` — 筛选条件样本，含 full（全字段）/ partial（部分字段）/ empty（全空）三种，用于验证字段无强制必填。

## 简历样本

简历文本与 PDF/DOCX 字节由 `tests/test_workbench_fixtures.py` 中的 `sample_resume_text()`、`sample_pdf_bytes()`、`sample_docx_bytes()` 在运行时动态生成，不落盘真实简历文件。动态生成的简历内容为虚构的"张三 / Python 后端 / 上海"文本，不含任何真实个人信息。

## 动态夹具说明

筛选 JSON 样本作为稳定快照供测试直接加载；旧测试切片中的参数化筛选函数已归档，不在公开仓库中。简历文本与 PDF/DOCX 字节仍由 `tests/test_workbench_fixtures.py` 动态生成。

## 安全约束

- 不得在此目录放置真实简历、真实 API Key 或真实 BOSS 抓取结果。
- 所有 `job_id`、`job_link` 使用 `job-xxx` 和 `zhipin.com/job_detail/` 演示路径。
- 所有 AI 响应样本只包含应用端可校验的 JSON 结构，不含真实模型输出。
- 筛选条件代码来自 `scripts/boss_cdp_raw.py` 的映射（SALARY_MAP/EXPERIENCE_MAP 等），均为 BOSS 公开筛选项枚举，不含敏感数据。
