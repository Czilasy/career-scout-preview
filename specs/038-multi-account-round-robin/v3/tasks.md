# Tasks: 多账号轮询弹窗信息架构与限流标记清除（B091 V3）

## Phase 1: 失败测试

- [x] T001 在 `BrowserAccountsDialog.spec.ts` 定义保留弹窗说明/浏览器抽屉/账号池内容和清除限流标记的组件行为。
- [x] T002 在 `test_webui_app_runtime.py` 定义 DELETE 限流标记接口的持久化与 404 行为。

## Phase 2: 实现

- [x] T003 新增 `browser_account_rate_limit_api.py`，并由 `settings_api.py` 注册专用 DELETE 路由。
- [x] T004 将 `BrowserAccountsDialog.vue` 重排为选定的弹窗紧凑清单方向，完整保留现有内容与操作。
- [x] T005 为限流标记补悬停/焦点清除按钮、成功刷新和失败反馈。
- [x] T006 登记新 API 路由模块到模块地图。

## Phase 3: 验证

- [x] T007 运行前端和后端聚焦测试。
- [ ] T008 运行后端全量、前端测试、构建与仓库卫生检查；后端/前端/构建已通过，卫生检查等待这些新增文件在获授权后提交；真实浏览器端到端验收仍由用户执行。
