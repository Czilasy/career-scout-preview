# Task 009：前端共享入口与跨组件集成

## 新会话启动指令

你是 Wave 3 前端集成所有者。Task 004、006、007 完成后执行；Task 008 不阻塞编码启动，但真实 HTTP 联调必须等待 Task 008。你是唯一允许修改 App.vue 和 DiscoveryView.vue 的会话。完成后停止，主会话负责 Converge。

## 目标

把提醒入口、count、抽屉、profile 切换、详情生命周期控件和跨组件刷新接入现有工作台。保留已有 App/Discovery/types/test 改动；App 持有提醒状态，详情通过 JobWorkspace actions slot 挂载 JobLifecycleActions；不复制业务规则、不加入平台筛选。

## 必读文件

- AGENTS.md
- specs/002-job-feedback-reminders/spec.md
- specs/002-job-feedback-reminders/plan.md
- specs/002-job-feedback-reminders/contracts/http-api.md
- specs/002-job-feedback-reminders/contracts/ui-interaction.md
- specs/002-job-feedback-reminders/quickstart.md
- webui/src/App.vue
- webui/src/views/DiscoveryView.vue
- webui/src/types.ts
- webui/src/styles.css
- webui/src/components/JobWorkspace.vue
- webui/src/components/ReminderDrawer.vue
- webui/src/components/JobLifecycleActions.vue
- webui/src/jobFeedback.ts
- webui/src/__tests__/App.spec.ts
- webui/src/views/__tests__/DiscoveryView.spec.ts

## 允许写入

- webui/src/App.vue
- webui/src/views/DiscoveryView.vue
- webui/src/types.ts（仅编译确需）
- webui/src/styles.css（仅共享集成布局确需）
- webui/src/__tests__/App.spec.ts
- webui/src/views/__tests__/DiscoveryView.spec.ts

## 禁止写入与行为

- 禁止覆盖用户已有改动，先读 diff 后最小合并。
- 禁止修改 ReminderDrawer、JobLifecycleActions、jobFeedback.ts；它们由前置任务所有。
- 禁止详情自动 mark_read、查看提醒/建议自动清除、失败乐观更新。
- 禁止 local timer 判断 720h、传 platform filter、猜身份或拼裸平台 URL。
- 禁止为了接线重构整个 App/Discovery。

## 集成门禁

1. 先检查 dirty worktree 和现有测试快照，标出用户改动。
2. 先补 App/Discovery 失败测试，再接组件；服务端响应是唯一 state 来源。
3. 用 AbortController 或请求序号，profile 切换后旧 count/list/state/action 响应不得覆盖新 profile。
4. Task 008 可用后做真实 HTTP 冒烟，再用 Playwright/Chrome 进行双视口检查。

## 执行清单

- [ ] T074 读取已有 App/Discovery/types/styles/tests 改动并记录必须保留行为。
- [ ] T075 接入 Lucide Bell、真实 total badge、0 隐藏、99+ 显示和可访问名称。
- [ ] T076 让 App 持有 ReminderDrawer，打开加载 list，profile 空/切换关闭或重置旧抽屉。
- [ ] T077 通过现有 JobWorkspace actions slot 接入 JobLifecycleActions。
- [ ] T078 传递内部 ID/权威三元组，成功采用服务端 job ID，不从 UI platform 补值。
- [ ] T079 接通 job-feedback-changed，action 成功后刷新当前 profile count/list/state。
- [ ] T080 丢弃 profile 切换后的旧 count/list/state/action 响应，包括 action 触发的刷新。
- [ ] T081 验证查看/关闭/建议不清除，跟进/荒废成功后按服务端结果更新 badge/list。
- [ ] T082 验证只打开详情不 mark_read、失败保留原状态、成功刷新且既有详情不回归。
- [ ] T083 仅必要时改 shared types/styles，完成 1440×900 与 390×844 无重叠、无横向溢出、焦点可达。
- [ ] T084 运行组件/集成测试和 build；等待 Task 008 后真实 HTTP 冒烟；仅提交允许路径，提交 feat: integrate job feedback frontend。

## 验证命令

    Set-Location webui
    npm test -- src/components/__tests__/ReminderDrawer.spec.ts src/components/__tests__/JobLifecycleActions.spec.ts src/__tests__/jobFeedback.spec.ts src/__tests__/App.spec.ts src/views/__tests__/DiscoveryView.spec.ts
    npm run build
    Set-Location ..
    uv run python -m unittest tests.test_repo_hygiene
    git diff --check
    git status --short

Task 008 完成后，用本地服务和临时数据执行 HTTP 冒烟；用 Playwright/真实 Chrome 检查 1440x900、390x844，记录截图、console/network 和横向溢出结果。

## 完成证据

    Task: 009
    Changed: App.vue / DiscoveryView.vue / only necessary shared types/styles/tests
    Tests: exact commands and result
    Evidence: badge / profile switch / drawer / slot / stale response / action refresh / desktop-mobile render
    Service: backend integration result or blocker
    Git: commit hash and subject, or blocked reason
    Blocked: none or reproducible blocker

完成后停止。不要运行最终全量、不要做独立审查、不要宣布整项功能已交付；这些属于主会话 Converge。

