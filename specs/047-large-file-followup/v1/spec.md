# Feature Specification: 执行搜索路由与 Discovery 门面薄化（047）

**Feature Branch**: `codex/feature/b096-parallel-platform-flow`
**Created**: 2026-09-26
**Status**: 内部拆分已完成；B096 后续仅保留兼容薄接线

## Scope

本 Spec 只处理现有两个红线/预警文件的职责拆分和兼容性证据，不增加接口、字段、状态、平台、交互或用户可感知行为。它是 B096 T001 的前置技术门禁补充。

拆分基线实测：`webui/exec_search_api.py` 为 901 行，超过 Python 800 行红线；`webui/src/views/DiscoveryView.vue` 为 1202 行，超过 Vue 1200 行红线。拆分后 B096 薄接线曾使搜索门面达到 906 行，已将续跑职责继续提取到 `webui/exec_search_resume.py`；当前 B096 实测为 696 行和 1199 行，续跑模块为 401 行，仍低于门禁。后续 B096 只能把这两个文件作为薄装配门面使用。

## User Stories & Compatibility Tests

### Story 1 - 既有搜索执行接口保持兼容（P1）

把搜索范围预览、搜索提交、续跑和取消路由的内部职责提取到独立模块；所有既有路径、方法、请求校验、状态码、错误码、响应字段、任务启动和 monkeypatch 入口保持不变。

独立验证：运行搜索执行路由聚焦测试、现有 WebUI 平台测试和直接相邻任务生命周期回归，确认拆分前后公开 HTTP 行为一致。

### Story 2 - Discovery 视图保持兼容并变薄（P1）

把高内聚的模板/事件区块移到独立组件或 composable；DiscoveryView 保持既有 props、emits、DOM 语义、按钮测试标识、键盘行为和页面顺序。拆分不引入新用户行为，B096 后续只能向新域和薄装配入口接线。

独立验证：运行 DiscoveryView 及直接相关 composable/component 测试，核对模板测试标识、现有交互事件和构建类型检查。

## Requirements

- **FR-001**：既有 `/api/search-scope/preview`、`/api/execute-search`、续跑和取消路由的公开契约保持不变。
- **FR-002**：`webui/exec_search_api.py` 完成职责提取后少于 800 行；门面只保留注册与必要兼容导出。
- **FR-003**：DiscoveryView 的现有用户可见 DOM、事件、文案、状态和响应式布局保持不变；提取模块不直接改变业务判定。
- **FR-004**：DiscoveryView 后续扩展只允许薄装配，新增业务逻辑落在 composable/component/domain 模块。
- **FR-005**：拆分不修改 B096 之外的业务逻辑，不修改 B106 去重策略，不读取或写入凭据。
- **FR-006**：所有新增 Python 文件少于 800 行，DiscoveryView 与新增 Vue 文件少于 1200 行；DiscoveryView 的高内聚区块必须完成职责提取，使后续 B096 只做薄装配；测试与文档不产生项目根临时产物。

## Forbidden Scope

禁止修改 `scripts/boss/`、`scripts/zhilian/`、`webui/cross_platform_dedupe.py`、`specs/019-cross-platform-job-dedup/`；不新增平台，不改变用户行为，不把拆分顺手变成 B096 功能实现。

## Acceptance

- 搜索执行 API 聚焦兼容测试通过，DiscoveryView 聚焦测试通过，前端构建通过。
- 真实行数记录到 Plan 和 B096 T001–T003 门禁记录。
- `git diff --check` 通过，禁区未改，测试输出与临时产物不进入仓库。
