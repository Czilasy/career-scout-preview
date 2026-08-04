# Task 007：岗位详情生命周期控件

## 新会话启动指令

你是 Wave 2 前端岗位详情控件执行会话。Task 004 已通过后执行。只编辑本组件及其测试；DiscoveryView.vue 的 slot 接线由 Task 009 负责。完成后停止。

## 目标与合同

实现详情内唯一当前状态、已读/已投递/跟进/荒废/恢复/纠正、缺失投递时间入口、事件历史、request ID 重试复用、revision 防倒退、失败保留原 state 和身份不完整阻断。详情初始化只 GET state，绝不自动 mark_read。

## 必读文件

- AGENTS.md
- specs/002-job-feedback-reminders/spec.md
- specs/002-job-feedback-reminders/data-model.md
- specs/002-job-feedback-reminders/contracts/http-api.md
- specs/002-job-feedback-reminders/contracts/ui-interaction.md
- webui/src/jobFeedback.ts
- webui/src/components/JobWorkspace.vue
- webui/src/views/DiscoveryView.vue（只读）

## 允许写入

- webui/src/components/JobLifecycleActions.vue
- webui/src/components/__tests__/JobLifecycleActions.spec.ts

## 禁止写入与行为

- 禁止修改 DiscoveryView.vue、App.vue、types.ts、共享 CSS、后端或合同。
- 禁止打开详情自动写状态，禁止混入 feedback events，禁止未经确认乐观更新。
- 禁止用 UI platform 补齐身份或使用不安全 URL。

## 执行清单

- [ ] T055 写详情变化读取 state、只读无 action、loading/error 测试。
- [ ] T056 实现内部 job ID 或完整三元组 state/action payload，成功采用服务端 job ID。
- [ ] T057 实现唯一当前状态和五类主命令。
- [ ] T058 实现目标状态/带时区时间纠正表单、future/缺失提示和 applied 缺时间补入口。
- [ ] T059 实现同岗位写锁、retry ID 复用、再次确认新 ID、无乐观写和 revision 防倒退。
- [ ] T060 实现按需 events 展开和 sequence 分页，不加载偏好事件。
- [ ] T061 实现身份不完整阻断、安全 URL 禁用、不从 UI platform 猜身份。
- [ ] T062 用 scoped CSS 完成桌面/窄屏可达操作、焦点、错误文案和无横向溢出。
- [ ] T063 运行组件/client 测试和 build，只提交允许路径。

## 验证命令

    Set-Location webui
    npm test -- src/components/__tests__/JobLifecycleActions.spec.ts src/__tests__/jobFeedback.spec.ts
    npm run build
    Set-Location ..
    uv run python -m unittest tests.test_repo_hygiene
    git diff --check
    git status --short

## 完成证据

    Task: 007
    Changed: JobLifecycleActions.vue; JobLifecycleActions.spec.ts
    Tests: exact commands and result
    Evidence: read-only initial load / action matrix / correction / idempotency / revision / events / responsive UI
    Git: commit hash and subject, or blocked reason
    Blocked: none or reproducible blocker

完成后通知主会话解锁 Task 009，不接 slot 或修改共享入口。
