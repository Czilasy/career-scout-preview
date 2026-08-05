# Task 008：后端共享入口与兼容集成

## 新会话启动指令

你是 Wave 3 后端集成所有者。Task 001、002、003、005 完成后执行。你是唯一允许修改 webui/app.py 的会话，必须合并当前已有改动，不得回退其它会话。完成后停止，主会话负责 Converge。

## 目标

注册新 API 并保持现有认证；把 legacy profile-job PATCH 转入统一命令服务；让 pipeline interest/reject/cancel 通过权威身份和 connection-aware 双索引事务；移除读取时历史 feedback_events 覆盖当前 profile_jobs.status 的行为；保留显式收藏/不感兴趣操作和当前用户改动。

## 必读文件

- AGENTS.md
- specs/002-job-feedback-reminders/spec.md
- specs/002-job-feedback-reminders/plan.md
- specs/002-job-feedback-reminders/contracts/http-api.md
- specs/002-job-feedback-reminders/quickstart.md
- webui/app.py
- webui/store.py
- webui/job_feedback.py
- webui/job_feedback_api.py
- webui/job_advice.py
- webui/pipeline_job_identity.py
- tests/test_webui_app.py
- tests/test_workbench_api.py

## 允许写入

- webui/app.py
- tests/test_webui_app.py
- tests/test_workbench_api.py（仅确需 pipeline 回归时）

## 禁止写入与行为

- 禁止为集成问题改独立 feature modules；先报告合同缺陷。
- 禁止删除用户改动、重排无关 app.py 或重新格式化整个文件。
- 禁止保留 BOSS-only save_job 作为 lifecycle/pipeline 权威保存路径。
- 禁止让历史反馈把 applied/read/stale 显示成 interested；显式偏好仍可更新当前状态。
- 禁止绕过 request ID、时间校验、事件、receipt、rollback 和认证。

## 集成门禁

1. 先检查 dirty worktree，记录当前 app/test 改动和必须保留的路由。
2. 运行前置任务 focused tests；失败不得进入集成修改。
3. 先补集成失败测试，再做最小 app 装配。
4. Python 后端改动完成后重启受影响 Flask 服务并验证健康接口；若环境没有服务，记录阻断和替代证据。

## 执行清单

- [x] T064 读取已有 app/test 改动并记录认证包装与兼容行为。
- [x] T065 注册 state/actions/events/reminders/advice route registrar。
- [x] T066 将 legacy PATCH 映射统一命令，落实 Idempotency-Key/body request_id、note 混合原子性和 428/400。
- [x] T067 接入身份 resolver 和 connection-aware helper，移除 pipeline BOSS-only 保存分支。
- [x] T068 让 interest/reject/cancel 以内部 ID 原子写入，身份冲突零副作用。
- [x] T069 移除 feedback aggregate 对当前 snapshot 的隐式覆盖，保留历史偏好学习和显式偏好。
- [x] T070 添加 lifecycle 主链、失败原状态、重启持久化和 legacy 兼容集成测试。
- [x] T071 添加 BOSS/智联混合、无 platform 谓词、相似岗位隔离、安全 URL 回归。
- [x] T072 运行后端聚焦和既有相关测试，修复本包范围回归。
- [x] T073 仅提交允许路径，提交 feat: integrate job feedback backend；不得做最终 converge。

## 验证命令

    uv run python -m unittest tests.test_job_feedback_api tests.test_webui_app tests.test_workbench_api tests.test_webui_store
    uv run python -m unittest tests.test_repo_hygiene
    git diff --check
    git status --short

## 完成证据

    Task: 008
    Changed: app.py and only necessary backend integration tests
    Tests: exact commands and result
    Evidence: route registration / legacy PATCH / pipeline identity / feedback compatibility / boss-zhilian / restart
    Service: restart command, health result, or documented environment blocker
    Git: commit hash and subject, or blocked reason
    Blocked: none or reproducible blocker

完成后通知主会话解锁最终 HTTP 联调，不执行 Converge。
