# Task 006：提醒抽屉组件

## 新会话启动指令

你是 Wave 2 前端提醒抽屉执行会话。Task 004 已通过后执行。只编辑本组件和本组件测试；App 顶部入口、profile 生命周期和跨组件刷新留给 Task 009。完成后停止。

## 目标与合同

实现 ReminderDrawer.vue 的 loading/error/empty/populated/action-busy/advice 状态、最多 100 条提醒、真实 total、单项快捷动作、安全岗位链接、Escape/焦点管理和响应式布局。查看、关闭、打开详情和请求 advice 均不得清除提醒；成功退出由父层使用服务端刷新决定。

## 必读文件

- AGENTS.md
- specs/002-job-feedback-reminders/spec.md
- specs/002-job-feedback-reminders/contracts/http-api.md
- specs/002-job-feedback-reminders/contracts/ui-interaction.md
- webui/src/jobFeedback.ts
- webui/src/components/JobWorkspace.vue
- webui/src/styles.css
- webui/src/__tests__/App.spec.ts（只读）

## 允许写入

- webui/src/components/ReminderDrawer.vue
- webui/src/components/__tests__/ReminderDrawer.spec.ts

## 禁止写入与行为

- 禁止修改 App.vue、DiscoveryView.vue、types.ts、共享 styles.css 或后端。
- 禁止本地删除提醒项模拟成功；必须由服务端刷新结果决定。
- 禁止全列表锁定；只锁目标项动作，advice loading 不锁跟进/荒废。
- 禁止跳转未通过 can_open 和所属平台安全复验的 URL，不拼裸平台 ID。

## 执行清单

- [ ] T046 写 loading/error/empty/populated/旧数据保留和重试测试。
- [ ] T047 实现稳定尺寸 drawer、标题、关闭、Escape、打开焦点和关闭焦点恢复。
- [ ] T048 实现真实 total、最多 100 项、最长未活动优先和唯一内部滚动区。
- [ ] T049 实现岗位信息、来源、天数、查看、跟进、荒废和建议操作。
- [ ] T050 实现逐项 action busy、refresh 驱动退出、失败保留原项和真实错误。
- [ ] T051 实现逐项 advice loading/result/error、AI/规则 source 和不自动执行。
- [ ] T052 实现 can_open + platform URL 复验，无效链接禁用但其它操作可用。
- [ ] T053 用 scoped CSS 完成 1440×900 与 390×844，44px 点击区、可换行、无横向溢出。
- [ ] T054 运行组件/client 测试和 build，只提交允许路径。

## 验证命令

    Set-Location webui
    npm test -- src/components/__tests__/ReminderDrawer.spec.ts src/__tests__/jobFeedback.spec.ts
    npm run build
    Set-Location ..
    uv run python -m unittest tests.test_repo_hygiene
    git diff --check
    git status --short

## 完成证据

    Task: 006
    Changed: ReminderDrawer.vue; ReminderDrawer.spec.ts
    Tests: exact commands and result
    Evidence: all UI states / per-item busy / refresh-driven removal / safe links / desktop-mobile layout
    Git: commit hash and subject, or blocked reason
    Blocked: none or reproducible blocker

完成后通知主会话解锁 Task 009，不修改 App 或开始 Task 009。
