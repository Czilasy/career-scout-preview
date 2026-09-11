# Implementation Plan: 偶发失败重试与失败展示优化

**Branch**: `039-transient-failure-handling` | **Date**: 2026-09-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/039-transient-failure-handling/spec.md`（已冻结）

**Note**: 本计划严格对齐冻结规格，不引入规格外功能。

## Summary

为列表抓取的偶发失败接入“一次重试 → 仍失败则记录并跳过”通道：重试从断点续抓、与既有失联重启重试/登录复核重试共享一次额度；失败展示降噪为“数量 + 悬停小浮窗（简单原因）”；补齐列表抓取“连不上浏览器”类原因名称并恢复其自动恢复资格；修正任务收尾后计数口径。全部复用现有零件（断点续抓、浏览器就绪检查/重启、白箱 `record_fact` 留痕），不新增数据表、不改抓取逻辑。

## Technical Context

**Language/Version**: Python 3.11（uv 管理）；TypeScript + Vue 3（Vite）

**Primary Dependencies**: Flask（本地服务）、Vue 3（前端）、SQLite（既有 webui.db）

**Storage**: SQLite（`webui.db`）；**本特性不新增表、不新增字段、不写迁移**

**Testing**: 后端 `uv run python -m unittest discover -s tests`；前端 `cd webui && npm test`；构建 `npm run build`

**Target Platform**: Windows/macOS 桌面应用（pywebview 壳 + 本地 Flask 服务）

**Project Type**: web application（本地桌面单用户）

**Performance Goals**: 无新增性能目标；单组重试仅多一次抓取尝试的耗时

**Constraints**: Python 文件 ≤800 行、Vue 文件 ≤1200 行；预警线 600/900 之后改动必须开新模块分流。实测：`webui/pipeline_exec_search.py`=728、`webui/source_zhilian_cdp.py`=648、`webui/runners/pipeline_task.py`=616、`webui/src/views/DiscoveryView.vue`=1165、`webui/exec_search_api.py`=889

**Scale/Scope**: 单用户、单任务；一次抓取 16 组量级

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 检查 | 结论 |
|---|---|---|
| I 职责分层 | 新增业务逻辑落 `webui/pipeline_exec_retry.py`（pipeline 执行域新模块）；路由层不动 | PASS |
| II 尺寸边界 | `pipeline_exec_search.py` 728→目标 ≤745（<800）；`source_zhilian_cdp.py` 648→≤651；`TaskProgress.vue` 572→≤600（<1200） | PASS（含缓和说明，见 Complexity Tracking） |
| III 引用方向 | `pipeline_exec_search.py → pipeline_exec_retry.py` 单向；`source_zhilian_cdp.py → source_zhilian_runtime_adapter.py` 单向（既有方向）；前端 view 不动 | PASS |
| IV 拆分纪律 | 本 spec 为功能 Spec，不做重构、不改门面文件 | PASS |
| V 验证门禁 | 聚焦测试 + 后端全量 + 前端测试 + `npm run build` + 仓库卫生 | PASS |
| VI 模块地图 | 新文件 `webui/pipeline_exec_retry.py` 同批次登记进 constitution 模块地图 | PASS（任务含登记项） |

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`.*

- **Allowed files**（允许修改）:
  - `webui/pipeline_exec_search.py` — 接线重试通道（净增长 ≤20 行，禁越 800 红线）
  - `webui/source_zhilian_runtime_adapter.py` — 新增列表侧 signal 补映射（镜像详情侧既有模式）
  - `webui/source_zhilian_cdp.py` — 列表映射改为经 adapter 构造（净增长 ≤3 行）
  - `webui/src/components/TaskProgress.vue` — 删成排明细、失败数字 hover 浮窗、收尾计数口径（≤600 行）
  - `tests/test_transient_retry.py` — 新增后端聚焦测试
  - `webui/src/components/__tests__/TaskProgress.spec.ts` — 追加展示与计数用例
  - `.specify/memory/constitution.md` — 模块地图登记一行（不改原则、不改版本号）
  - `CHANGELOG.md` — 追加用户可感知条目（按更新说明写作规范）
  - （2026-09-11 范围增补 FR-016，用户指示）`webui/src/composables/useDiscoveryResults.ts` — 恢复上一轮时按真实任务快照补齐计数与失败留痕；`webui/src/composables/useDiscoveryExecution.ts` — 「结束并保存结果」合成快照保留该轮真实计数；`webui/src/composables/useDiscoveryState.ts` — 内部快照类型补 `combo_issues`；`webui/src/types.ts` — 共享快照类型补 `scraped_count`
- **Forbidden files**（禁止修改）:
  - `webui/app.py`、`webui/store.py`、`scripts/boss_cdp_raw.py`、`webui/source.py`（门面）
  - `webui/src/views/DiscoveryView.vue`（1165 行，预警线以上）
  - `webui/exec_search_api.py`（889 行，超红线）
  - `webui/runners/*.py`（616 行预警线以上/非本范围）
  - `scripts/zhilian/search.py`、`scripts/zhilian/cdp.py`（抓取逻辑本身，spec FR-013 禁止）
  - `webui/store_migrations*.py`（不新增表/字段）
  - `webui/error_registry.py`（原因名称已具备，不需要改）
- **New files**（新增文件）:
  - `webui/pipeline_exec_retry.py` — 偶发失败一次重试的判定与白箱事件构造（纯逻辑，约 60–90 行）
- **Reference direction**: `webui/pipeline_exec_search.py → webui/pipeline_exec_retry.py`（单向，无反向 import）；`webui/source_zhilian_cdp.py → webui/source_zhilian_runtime_adapter.py`（单向，既有）；前端 `TaskProgress.vue` 仅用 `snapshot` props，不新增跨层依赖
- **Line gate**: 上述目标文件改造后均不越各自红线（Python 800 / Vue 1200），且预警线以上文件净增长受控
- **Rationale**: `pipeline_exec_search.py`（728）与 `source_zhilian_cdp.py`（648）均在预警线以上，按宪法 VI 必须开新模块分流——重试判定落新模块、映射补齐落既有 adapter 扩展点；前端只改 `TaskProgress.vue`（572，可改），不改 `DiscoveryView.vue`（1165）

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能交付门禁：相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- 本特性属功能开发，适用全量门禁；收口发布不自动执行全量测试。

## Project Structure

### Documentation (this feature)

```text
specs/039-transient-failure-handling/
├── spec.md              # 已冻结
├── plan.md              # 本文件
├── research.md          # Phase 0 输出
├── data-model.md        # Phase 1 输出
├── quickstart.md        # Phase 1 输出
├── contracts/
│   └── retry-and-display.md
└── tasks.md             # Phase 2 输出（/speckit-tasks）
```

### Source Code (repository root)

```text
webui/
├── pipeline_exec_search.py       # 修改：重试通道接线（净增长受控）
├── pipeline_exec_retry.py        # 新增：偶发失败重试判定与事件构造
├── source_zhilian_cdp.py         # 修改：列表映射经 adapter 构造（+2~3 行）
├── source_zhilian_runtime_adapter.py  # 修改：新增列表侧补映射
├── whitebox_evidence.py          # 不修改（复用既有 record_fact）
└── src/components/
    ├── TaskProgress.vue          # 修改：展示降噪 + 悬停浮窗 + 计数口径
    └── __tests__/TaskProgress.spec.ts  # 修改：追加用例

tests/
└── test_transient_retry.py       # 新增：重试通道与映射聚焦测试
```

**Structure Decision**: 沿用既有单仓结构（`webui/` 后端域模块 + `webui/src/` 前端组件）；不新增目录层级。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `webui/pipeline_exec_search.py`（728 行，预警线以上）仍需改动 | 组合失败分支是重试的唯一接入点，无法绕开 | 重写整个失败分支到新模块属重构（违反原则 IV：重构须单独立项），且风险远超收益；改为“新模块承载判定 + 本文件最小接线（净增长 ≤20 行）” |
| `webui/source_zhilian_cdp.py`（648 行，预警线以上）仍需改动 | 列表映射字典定义在该文件，是消费点 | 把整个映射迁到 adapter 属结构调整，波及面大；改为“一行构造调用 + adapter 承载新增映射”，净增长 ≤3 行 |
