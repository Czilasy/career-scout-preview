# Task 004：前端类型、API client 与并发保护

## 新会话启动指令

你是 Wave 1 前端合同执行会话。只编辑本任务允许的两个文件，完成后停止。组件和共享入口由后续任务负责；不要修改 `App.vue`、`DiscoveryView.vue`、`types.ts` 或共享 CSS。

## 目标

创建 `webui/src/jobFeedback.ts` 和对应 Vitest 测试，封装冻结 HTTP 合同中的 state/actions/events/reminders/advice 请求、状态标签、安全 canonical URL 校验、权威身份 payload、request ID 生命周期和 revision 防倒退。前端只能使用服务端 state；失败不做乐观提交；同一不确定网络请求重试复用 request ID，用户明确再次确认生成新的 request ID。

## 必读文件

- `AGENTS.md`
- `specs/002-job-feedback-reminders/spec.md`
- `specs/002-job-feedback-reminders/contracts/http-api.md`
- `specs/002-job-feedback-reminders/contracts/ui-interaction.md`
- `specs/002-job-feedback-reminders/data-model.md`
- `webui/src/types.ts`
- `webui/src/api.ts`（如存在）
- `webui/src/views/DiscoveryView.vue`
- `webui/src/components/JobWorkspace.vue`

若某路径不存在，记录实际情况后使用现有 API helper，不凭空引入第二套网络封装。

## 允许写入

- `webui/src/jobFeedback.ts`
- `webui/src/__tests__/jobFeedback.spec.ts`

## 禁止写入与行为

- 禁止修改共享 `types.ts`；只有确实无法编译且主会话批准后才由 Task 009 处理类型整合。
- 禁止修改任何 Vue 组件、`styles.css`、后端或 contracts。
- 禁止把 platform 过滤传给 reminder count/list；platform 只用于身份/URL 安全校验。
- 禁止在 client 中自行计算提醒资格或把建议结果写入岗位状态。
- 禁止每次 retry 重新生成 request ID；禁止用低 revision 响应覆盖较新 state。

## 工作区与测试门禁

1. 读取当前 API helper 和 TypeScript 配置，记录 import 风格和错误响应形状。
2. 先写失败测试：类型/错误、提醒 total/list、advice allowlist、request ID、revision、身份 payload、安全 URL。
3. 不依赖尚未实现的后端；mock `fetch`，但测试请求方法、URL、body 和错误映射。
4. 用 `npm run build` 检查新增模块可被 TypeScript 解析；不要为通过构建改动其它文件。
5. 本任务与 Wave 1 其它会话并行时，不运行 broad `git add` 或提交；只回报本任务改动，主会话在 Wave 1 汇合后统一执行 hygiene 和 commit。

## 执行清单

- [ ] T029 写生命周期 state/action/event 类型和错误响应映射的失败测试。
- [ ] T030 写 reminder count/list、真实 total、最多 100 条投影测试。
- [ ] T031 写 advice action/source allowlist 与服务端错误投影测试。
- [ ] T032 写一次确认 ID、网络不确定 retry 复用 ID、明确再次确认新 ID 测试。
- [ ] T033 写低 revision response 不覆盖较新 state 测试。
- [ ] T034 写内部 ID/权威三元组 payload、身份不完整阻断和平台安全 URL 测试。
- [ ] T035 实现冻结类型、API client、错误类、request context 和 revision merge helper。
- [ ] T036 运行测试与 build；仅提交允许文件，提交 `feat: add job feedback client`。

## 精确验证命令

```powershell
Set-Location webui
npm test -- src/__tests__/jobFeedback.spec.ts
npm run build
Set-Location ..
uv run python -m unittest tests.test_repo_hygiene
git diff --check
git status --short
```

## 完成证据与解锁

```text
Task: 004
Changed: webui/src/jobFeedback.ts; webui/src/__tests__/jobFeedback.spec.ts
Tests: exact commands and result
Evidence: contract requests / request ID retry / revision / identity / URL safety
Git: commit hash and subject, or blocked reason
Blocked: none or reproducible blocker
```

完成后解锁 Task 006、007、009；不得执行这些任务。
