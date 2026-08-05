# Task 003：pipeline 权威岗位身份解析

## 新会话启动指令

你是 Wave 1 岗位身份执行会话。只处理权威岗位身份 DTO、校验和 transaction 编排协议；完成后停止。你可以读取共享文件，但不得编辑 `webui/store.py` 或 `webui/app.py`，真实装配留给 Task 008。

## 目标

实现 `webui/pipeline_job_identity.py`，把 pipeline 岗位统一表达为内部 `job_id`，或完整且可校验的 `platform + platform_job_id + canonical_url`。BOSS 和智联都必须通过同一协议；平台只用于 URL/身份校验，不参与提醒、生命周期或 AI 业务规则。不得用当前页面平台、标题、公司、JD 相似度或裸平台 ID 猜测内部岗位。

## 必读文件

- `AGENTS.md`
- `specs/002-job-feedback-reminders/spec.md`
- `specs/002-job-feedback-reminders/plan.md`
- `specs/002-job-feedback-reminders/research.md`
- `specs/002-job-feedback-reminders/data-model.md`
- `specs/002-job-feedback-reminders/contracts/http-api.md`
- `webui/platforms.py`
- `webui/store.py`
- `webui/app.py`
- `tests/test_platforms.py`
- `tests/test_workbench_api.py`

## 允许写入

- `webui/pipeline_job_identity.py`
- `tests/test_pipeline_job_identity.py`

## 禁止写入与行为

- 禁止编辑 `webui/store.py`、`webui/app.py`、`webui/platforms.py`、`webui/job_feedback.py` 或前端文件。
- 禁止在测试中依赖未完成的真实 app 装配；使用 fake store/connection protocol 验证编排。
- 禁止从 URL host 反推缺失 platform，禁止从 UI 当前 platform 补值，禁止把 platform_job_id 当内部 UUID。
- 禁止按标题、公司、JD 或裸平台 ID 合并相似岗位。

## 前置与工作区门禁

1. 检查当前工作区并读取现有身份实现，记录 `platforms.py` 实际 API；不得根据计划猜函数名。
2. 读取 Task 001 的 connection-aware helper 约定；若实现尚未可 import，使用 Protocol/fake 保持本包独立。
3. 先写失败测试，再实现 DTO、错误和编排；身份失败必须验证关联写入零副作用。
4. 如发现 store helper 需要调整，停止并向主会话提出接口变更，不得自行越界编辑 store。
5. 本任务与 Wave 1 其它会话并行时，不运行 broad `git add` 或提交；只回报本任务改动，主会话在 Wave 1 汇合后统一执行 hygiene 和 commit。

## 执行清单

- [x] T021 读取平台、存储、app 并记录 BOSS/智联 URL 校验、双索引字段和 pipeline payload。
- [x] T022 为内部 job ID、完整三元组、展示字段规范化写失败测试。
- [x] T023 为三元组缺失、URL 与 platform 错配、内部 ID 与三元组冲突、双索引冲突和零副作用写失败测试。
- [x] T024 为跨平台相同裸 ID、相似标题/公司不合并、不从 UI platform 猜身份写失败测试。
- [x] T025 定义权威身份 DTO、规范化展示字段、领域错误和接收 connection 的 store helper Protocol。
- [x] T026 实现内部 ID 校验、三元组完整性、URL 校验、平台枚举和安全投影。
- [x] T027 实现调用方 transaction 内的解析/upsert 编排；用 fake connection/store 证明成功、冲突和不完整身份不产生关联写入。
- [x] T028 运行 focused tests，检查只改允许路径并提交 `feat: add authoritative job identity resolver`。

## 精确验证命令

```powershell
uv run python -m unittest tests.test_pipeline_job_identity
uv run python -m unittest tests.test_platforms
uv run python -m unittest tests.test_repo_hygiene
git diff --check
git status --short
```

若平台测试依赖现有环境，报告原始失败与本包测试结果，不修改既有平台实现。

## 完成证据与解锁

```text
Task: 003
Changed: webui/pipeline_job_identity.py; tests/test_pipeline_job_identity.py
Tests: exact commands and result
Evidence: boss/zhilian / identity completeness / URL mismatch / dual-index conflict / no guessing / zero side effects
Git: commit hash and subject, or blocked reason
Blocked: none or reproducible blocker
```

完成后解锁 Task 005 的身份测试和 Task 008 的真实装配；不得执行 Task 005/008。
