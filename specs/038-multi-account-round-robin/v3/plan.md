# Implementation Plan: 多账号轮询弹窗信息架构与限流标记清除（B091 V3）

**Spec**: [spec.md](./spec.md)

## 实现路径

`BrowserAccountsDialog.vue` 保持 `BaseDialog` 载体、现有数据加载和既有操作函数。模板重排为：完整说明 → 抓取浏览器抽屉控制行 → 账号池紧凑清单 → 添加账号与反馈。现有浏览器选择器 `BrowserKernelPicker.vue` 原样复用；“重新探测”仅重新读取当前注册表，不创建另一套浏览器选择状态。

账号卡片采用紧凑网格：身份与标记、参与轮询、两个配额、平台清单、现有操作按钮。旧 CSS 被替换而不是追加，确保组件保持在 Vue 900 行预警线以内。限流标记内放可访问的按钮，CSS 仅控制该按钮在 hover/focus-within 时显示。

新增 `webui/browser_account_rate_limit_api.py` 注册专用 DELETE 路由。它在账号簿锁内读取并更新已有 `rate_limited` 字段，不接触 pool、账号资料、浏览器选择、Cookie 或登录缓存。`settings_api.py` 只调用注册函数，不承载新业务逻辑。

## 文件边界

**允许修改**:

- `webui/src/components/BrowserAccountsDialog.vue`
- `webui/src/components/__tests__/BrowserAccountsDialog.spec.ts`
- `webui/settings_api.py`（仅注册新路由模块）
- `webui/browser_account_rate_limit_api.py`（新增）
- `tests/webui_app/test_webui_app_runtime.py`
- `.specify/memory/constitution.md`（模块地图登记）
- `specs/038-multi-account-round-robin/INDEX.md` 与 `v3/` 文档

**禁止修改**:

- `webui/app.py`、`webui/pipeline_exec.py`、`webui/account_round_robin.py`
- `webui/pipeline_exec_search.py`、`webui/pipeline_exec_details.py`、`webui/resume_identity.py`
- `webui/src/views/DiscoveryView.vue`、灵动岛相关文件、数据库迁移、账号簿 schema

**引用方向**:

- `settings_api.py → browser_account_rate_limit_api.py → pipeline_exec_accounts.py`
- `BrowserAccountsDialog.vue → apiRequest → HTTP API`

## 验证计划

1. 前端先增加限流清除与信息保留的失败组件测试。
2. 后端先增加 DELETE 路由的失败 HTTP 测试。
3. 最小实现通过各自聚焦测试。
4. 跑后端全量、前端测试、前端构建、卫生检查；再由用户在正式 UI 中执行真实浏览器验收。
