# Implementation Plan: 执行搜索路由与 Discovery 门面薄化（047）

**Branch**: `codex/feature/b096-parallel-platform-flow` | **Date**: 2026-09-26
**Spec**: [spec.md](spec.md)

## Why this Spec is separate

021 已完成 app 路由外迁和 DiscoveryView 的第一次拆分，但本次实测基线为 `exec_search_api.py` 901 行、`DiscoveryView.vue` 1202 行；021 的工件没有为后续超限的执行路由文件提供独立拆分范围与收口证据。047 只补这两个现状门禁，不把 B096 用户行为塞入拆分批次。

## Design

### Search execution API

保留 `register_exec_search_routes(app, ctx)` 作为唯一公开注册入口。把纯路由内部的可复用校验、请求快照构造和响应收口按职责下沉到新模块；新模块只接收显式上下文和既有依赖，不反向 import API，不改变 app 注册方式、路径、请求和响应。

### Discovery view

保留 DiscoveryView 的页面壳、props、emits 和现有页面顺序。把岗位轨迹高内聚弹窗提取为独立组件，事件仍由现有父层处理；提取后的组件只接受现有值和回调，不新增后端请求或状态语义。结果列表、搜索条件等更大区块继续由既有 composable/组件承载，B096 只能在门面做薄接线。

## File Boundaries

允许修改：

- `webui/exec_search_api.py`
- `webui/src/views/DiscoveryView.vue`
- 为上述拆分新增的 `webui/exec_search_*.py`、`webui/src/components/*` 或 `webui/src/composables/*`
- 搜索执行与 Discovery 的聚焦/直接相邻测试
- 本 Spec 工件与 B096 v1 Plan 的门禁证据记录

禁止修改：

- `scripts/boss/`、`scripts/zhilian/`
- `webui/cross_platform_dedupe.py`
- `specs/019-cross-platform-job-dedup/`
- B096 产品模块、迁移、API、并发协调代码（047 收口前不进入 B096 T004）

引用方向保持：路由门面 → 提取模块；DiscoveryView → composable/component；不允许 store 反向依赖 API。

## Verification Gate

1. 先新增能证明行数/兼容目标的失败 characterization test，确认因拆分缺失失败。
2. 最小提取后运行搜索执行 API 聚焦、DiscoveryView 聚焦和直接相邻回归。
3. 运行 `git diff --check`、行数检查、禁区检查；真实账号 E2E 不属于本 Spec。
4. 将实际文件、行数、测试结果写回本 Plan 和 B096 Plan 的 T001–T003 记录。

## Completed evidence (2026-09-26)

- `webui/exec_search_api.py`: 901 → **696** 行；续跑职责在 `webui/exec_search_resume.py`（**401** 行），范围预览在 `webui/exec_search_scope.py`（**98** 行），取消在 `webui/exec_search_cancel.py`（**65** 行）。B096 增加真实 Flow 提交边界和安全 Future 失败收口后曾使门面短暂回升，随后按本 Spec 的 `exec_search_*.py` 范围再次下沉，当前门面仍只负责注册/组装。
- `webui/src/views/DiscoveryView.vue`: 1202 → **1136** 行；新增 `webui/src/components/JobLifecycleDialog.vue`: **48** 行。B096 当前薄装配后的实际行数为 **1199** 行。
- `uv run python -m unittest tests.test_047_split_compat`: **5/5** 通过；该测试在拆分前按预期失败，拆分后转绿。
- `uv run python -m unittest tests.test_047_split_compat tests.test_resume_continue tests.webui_app.test_webui_app_platform`: **106/106** 通过。
- `npm test -- src/views/__tests__/DiscoveryView.spec.ts`: **117/117** 通过。
- `npm run build`: 通过（Vite 2292 modules transformed；仅保留既有 chunk size warning）。`git diff --check` 通过。

## Current compatibility correction (2026-09-27)

`exec_search_resume.py` 在 B096 第三轮返修中增加了抓取 Future 失败的持久化回调，当前实测为 **401** 行；该增量只补充 Flow Track/run/task 的安全失败收口，不改变 047 的路由、请求、响应或 Discovery 页面契约。当前硬门禁为 `exec_search_api.py` 637 行、`exec_search_resume.py` 401 行、`DiscoveryView.vue` 1199 行。
