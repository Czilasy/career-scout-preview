# Task 005：生命周期、提醒、事件与建议 HTTP API

## 新会话启动指令

你是 Wave 2 后端 HTTP API 执行会话。Task 001、002、003 已完成并通过各自聚焦门禁后执行。只负责独立 route registrar/Blueprint 与测试，完成后停止；不要注册到 webui/app.py，不要执行 Task 008。

## 目标

把已完成的存储、领域、AI 和身份模块暴露为冻结合同中的 state/actions/events/reminders/advice API。路由层只负责认证边界、请求解析、领域调用和稳定错误映射，不复制领域规则。所有读取零副作用；advice 只读且失败走规则兜底；提醒 count/list 不接受或使用 platform 过滤。

## 必读文件

- AGENTS.md
- specs/002-job-feedback-reminders/spec.md
- specs/002-job-feedback-reminders/plan.md
- specs/002-job-feedback-reminders/contracts/http-api.md
- specs/002-job-feedback-reminders/contracts/ui-interaction.md
- specs/002-job-feedback-reminders/data-model.md
- webui/store.py
- webui/job_feedback.py
- webui/job_advice.py
- webui/pipeline_job_identity.py
- webui/app.py（只读，参考现有路由和认证约定）

## 允许写入

- webui/job_feedback_api.py
- tests/test_job_feedback_api.py

## 禁止写入与行为

- 禁止修改或注册 webui/app.py；Task 008 是唯一集成所有者。
- 禁止复制 action/time/reminder/advice 规则；必须调用前置领域模块。
- 禁止引入任意字段 PATCH 作为新入口，禁止把 platform 加入提醒查询。
- 禁止泄露 SQL、文件路径、Key、endpoint、prompt 或异常堆栈。

## 执行门禁

1. 先读 git status --short 和前置任务实际导出；若合同实现不完整，停止并报告主会话。
2. 建立隔离 Flask app/registrar 测试夹具，不依赖最终 app 注册。
3. 先写失败合同测试，覆盖成功体与冻结错误码，且对读取和 advice 做零副作用断言。
4. 任何需要改前置模块的发现都回报，不越界修改。

## 执行清单

- [x] T037 记录前置模块导出、app.py 路由约定，并建立隔离 registrar fixture。
- [x] T038 测试 state/events 只读、revision、after_sequence、limit 上限和偏好事件不混入。
- [x] T039 测试七种 action、首次权威三元组入库、replay/冲突、回滚和稳定错误体。
- [x] T040 实现 state/actions/events route registrar，按合同映射 status/error_code/user_message。
- [x] T041 实现 count/list 共用 projection、当前 profile 隔离、最多 100、无 platform 参数和零副作用。
- [x] T042 实现单岗位逾期 advice route：服务端重读岗位事实、调用只读 adapter、非逾期 409、规则兜底。
- [x] T043 测试 BOSS/智联同规则、提醒混排、身份错误零副作用和所有业务查询没有平台谓词。
- [x] T044 运行本包及前置模块回归，修复本包范围问题。
- [x] T045 仅提交两个允许路径，提交 feat: add job feedback api；不要注册 app。

## 验证命令

    uv run python -m unittest tests.test_job_feedback_api tests.test_job_feedback tests.test_job_advice tests.test_pipeline_job_identity
    uv run python -m unittest tests.test_repo_hygiene
    git diff --check
    git status --short

## 完成证据

    Task: 005
    Changed: webui/job_feedback_api.py; tests/test_job_feedback_api.py
    Tests: exact commands and result
    Evidence: routes / status codes / rollback / replay / no platform filter / advice read-only
    Git: commit hash and subject, or blocked reason
    Blocked: none or reproducible blocker

完成后只通知主会话解锁 Task 008，不执行 Task 008 或 Converge。
