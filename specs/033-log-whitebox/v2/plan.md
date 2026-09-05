# Implementation Plan: 任务完成证据白箱（033 V2）

**Branch**: `033-log-whitebox` | **Date**: 2026-09-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/033-log-whitebox/v2/spec.md`

**Status**: 已冻结，待其他 AI 实施；本轮编写者后续只负责审查

## Summary

V2 在 V1 日志接线基础上新增一套任务完成证据系统。抓取源、AI、恢复、浏览器、账号、调参和旧流程只报告实际事实；统一白箱服务保存计划、单元结果和追加事件，再由纯规则归并器计算唯一最终结论。所有任务查询和前端提示读取该结论，禁止根据 `ok`、岗位数量或旧状态旁路推断成功。

实施按五个可验证阶段推进：

1. 封住虚假成功；
2. 持久化分页、单元、数量和质量证据；
3. 接入恢复、AI、运行恢复与提交失败；
4. 接入旧工作台、旧任务、复抓和调参；
5. 统一查询、普通提示和开发者报告。

## Technical Context

**Language/Version**: Python 3（uv 管理）；Vue 3 + TypeScript

**Primary Dependencies**: 现有 Flask、SQLite、Python 标准库、现有前端状态与组件体系；不新增第三方依赖

**Storage**: 正式/测试 `TaskStore` SQLite；用户数据目录下追加式应急白箱文件；现有 `task_logs` 和 `career-scout.log` 保留为诊断投影

**Testing**: Python `unittest`、Vitest、现有跨层自动化冒烟、真实项目公开入口验证

**Target Platform**: Windows 桌面与源码模式；macOS 保持兼容

**Project Type**: 本地桌面应用，内嵌 Web UI 和后台任务执行器

**Performance Goals**: 任务事实写入为单条事务或小批事务；不因查询完整事件阻塞普通任务状态；普通状态查询不默认加载事件明细

**Constraints**:

- 当前活动任务 `45a2dcd730334002b21f30e18d5008e6` 禁止用于实现或验证。
- 正式历史数据只读，不回写 V2 结论。
- 必需白箱写入失败必须失败关闭。
- 新模块必须遵守 `api → service → store`。
- Python 文件不超过 800 行，达到 600 行的现有文件不得净增长新逻辑。
- 不提交、推送、发布或删除历史文件。

**Scale/Scope**: 单用户本地运行；一个任务可包含数十个组合、数百页、数千岗位及多个恢复尝试；事件查询需要分页

## Constitution Check

*GATE: Phase 0 前检查，Phase 1 设计后复查。*

- **原则 I 职责分层：PASS**。统一规则放 service，数据访问放 store，路由只组装返回。
- **原则 II 单文件尺寸：PASS WITH GATE**。新文件目标均低于 600 行；现有超过 600 行的文件只允许净减少或薄接入。
- **原则 III 引用方向：PASS**。`api/runner/source → whitebox service → store`；store 不引用 API/runner。
- **原则 IV 拆分纪律：PASS**。本版不是大文件重构；如果薄接入无法在不增长的情况下完成，停止并新建独立拆分 Spec，不在本功能中顺手重构。
- **原则 V 验证门禁：PASS**。任务包含聚焦测试、后端全量、前端测试、构建和卫生检查。
- **原则 VI 模块地图：PASS WITH FOLLOW-UP**。新增白箱模块后必须在同一批次登记模块地图。
- **原则 VII 错误与可观察性：PASS**。写入异常显式传播或持久化降级，不新增纯 `pass`。
- **宪法版本**：无需修改。V2 没有新增治理原则，只是执行现有原则。

## Clarification Result

结构化歧义扫描结果：

- 功能范围、失败处理、状态、数据、恢复、前端提示、安全和验收均明确。
- 性能采用本地应用合理默认：普通查询不加载完整事件，事件列表分页。
- 没有 `[NEEDS CLARIFICATION]`。
- 用户已明确要求直接完成全部前置文档和 Tasks，因此不再重复提问。

## Architecture

```text
source / runner / guard / api
              │ record facts
              ▼
       webui/whitebox.py
        ├─ validate + redact + idempotency
        ├─ required-write failure handling
        ├─ finalize/report public interface
        ▼
 webui/whitebox_rules.py ── pure conclusion reducer
        │
        ▼
 webui/store_whitebox.py ── SQLite persistence
        │
        ├─ whitebox_runs
        ├─ whitebox_units
        └─ whitebox_events

task_state/results/history ── report() ── frontend
```

### Public service shape

- `begin(owner_kind, owner_id, plan, parent_owner_id=None)`
- `record(run_ref, fact)`
- `finalize(run_ref, lifecycle_end=None)`
- `report(owner_kind, owner_id, include_events=False, cursor=None)`

The exact contract is frozen in [contracts/whitebox-service.md](contracts/whitebox-service.md).

### Canonical conclusion

`whitebox_runs.conclusion` is the authority for V2 task completeness. Existing business-table `status` fields remain lifecycle/compatibility storage where schema differences make direct replacement unsafe. Public task status, result history, final developer log and frontend completeness display must derive from the canonical conclusion, not from those legacy fields.

### Persistence failure path

1. Write the required fact transactionally.
2. If it fails, attempt `whitebox_incomplete` in the primary store.
3. If the primary store is unavailable, append a redacted emergency record in the user data directory.
4. If both fail, raise a hard error; caller cannot return or persist complete success.
5. Import emergency records idempotently when storage becomes available; imported evidence never silently upgrades a task without re-finalization.

## Data Design

See [data-model.md](data-model.md).

Schema migration 033 introduces:

- `whitebox_runs`
- `whitebox_units`
- `whitebox_events`
- indexes and uniqueness constraints
- schema version 33 entry

Migration does not backfill old business rows. V2 begins creating records for new tasks after deployment.

## Interface Design

See:

- [contracts/whitebox-service.md](contracts/whitebox-service.md)
- [contracts/task-report-api.md](contracts/task-report-api.md)

Developer report is added to the existing `task_state_api` route domain, avoiding new registration logic in `webui/app.py`.

## File Boundaries

*GATE: 用户已在 2026-09-05 授权按本计划直接写完 V2 前置内容和 Tasks。以下边界来自已确认方案，不再等待第二次确认。*

### New source files

| Path | Responsibility | Expected lines |
|---|---|---:|
| `webui/whitebox.py` | 唯一公共服务：开始、记录、收口、报告、脱敏和写入失败处理 | ≤500 |
| `webui/whitebox_rules.py` | 无副作用的计划/单元证据归并与原因选择 | ≤320 |
| `webui/store_whitebox.py` | 白箱总表、单元、事件、分页查询和应急导入的数据访问 mixin | ≤500 |
| `webui/store_migrations_v5.py` | 迁移 033 的表、约束和索引 | ≤260 |
| `tests/test_whitebox_rules.py` | 结论矩阵、幂等和边界纯单元测试 | ≤500 |
| `tests/webui_store/test_store_whitebox.py` | 迁移、事务、追加事件、并发和历史兼容测试 | ≤500 |
| `tests/test_whitebox_integration.py` | 主抓取、AI、恢复、旧流程和接口的一致性集成测试 | ≤700 |

### Allowed existing backend files

#### Composition and shared status

- `webui/store.py`：只增加 `StoreWhiteboxMixin` import 与 MRO 组装，不加业务逻辑。
- `webui/store_migrations.py`：只 re-export/组装 `StoreMigrationsV5Mixin`。
- `webui/store_migrations_v1.py`：迁移调度增加版本 33 调用，不放 DDL。
- `webui/store_constants.py`：增加白箱结论常量和对外映射；不得破坏旧生命周期状态。
- `webui/task_status.py`：统一把白箱结论映射成普通任务状态和文案。

#### Source and page evidence

- `webui/source_breaker.py`：强制空列表成功证据契约。
- `webui/source_boss_cdp.py`：传递并校验 BOSS 页面/结束事实；当前 627 行，修改后不得净增长。
- `webui/source_zhilian_cdp.py`：统一智联空结果和结束证据；当前 742 行，修改后不得净增长。
- `scripts/boss/search.py`：输出逐页返回数、唯一数、三值 `has_more` 和明确停止事件。
- `scripts/zhilian/search.py`：输出与 BOSS 同语义的完成/空结果事实。
- `webui/pipeline_exec_search.py`：保存全部计划组合，移除“有部分结果就整体 ok”的判定。
- `webui/runners/pipeline_task.py`：所有组合成功/失败/跳过均上报，最终调用统一收口。
- `webui/store_scrape_runs.py`：恢复点与历史证据解耦，不再因组合结束删除唯一分页证据。

#### Recovery, AI and runtime operations

- `webui/running_task_api.py`：孤儿任务通过白箱报告恢复，不凭岗位/检查点升级。
- `webui/app_support.py`：`_ensure_scrape_source` 改为薄委托且文件净减少，不加入新规则。
- `webui/ai_screening.py`：AI 全部保留返回明确降级事实；当前 668 行，修改后不得净增长。
- `webui/runners/ai_screen_rough.py`：上报粗筛正常完成或全部保留。
- `webui/runners/ai_screen_fine.py`：统一细筛失败/不确定事实。
- `webui/runners/ai_screen_task.py`：最终状态改由白箱收口；当前 653 行，修改后不得净增长。
- `webui/browser_recovery.py`：事件增加任务、阶段、单元和尝试上下文。
- `webui/pipeline_guard.py`：卡住、重试、放弃和暂停写统一事实。
- `webui/account_round_robin_observability.py`：账号快照、分配、切换和撞墙写统一事实。
- `webui/diagnostics.py`：诊断写入失败接入 `whitebox_incomplete`。

#### Submission and legacy/tuning integration

- `webui/exec_search_api.py`：首次及恢复提交失败统一终态；当前 674 行，修改后不得净增长。
- `webui/ai_screen_api.py`：AI 提交失败终态；当前 343 行。
- `webui/task_continue_api.py`：继续/恢复提交失败终态；当前 754 行，修改后不得净增长。
- `webui/pipeline_jobs_api.py`：任务化复抓提交及批次事实；当前 666 行，修改后不得净增长。
- `webui/tuning_api.py`：调参提交失败终态。
- `webui/workbench_runner.py`：旧工作台父子任务事实与统一收口。
- `webui/task_runners.py`：旧任务入口提交失败和统一收口。
- `webui/runners/tuning_manifest.py`：删除按结果数量补推测量成功。

#### Query and result projection

- `webui/task_state_api.py`：增加开发者白箱查询并复用统一报告。
- `webui/results_api.py`：返回统一 `integrity`。
- `webui/result_history.py`：历史结果读取统一 `integrity`。
- `webui/running_task_api.py`：活动任务状态与完整性结论分开。
- `webui/logging_setup.py`：仅在需要时为应急白箱记录复用现有脱敏函数；不建立第二套脱敏规则。

### Allowed frontend files

- `webui/src/types.ts`
- `webui/src/discovery.ts`
- `webui/src/api.ts`
- `webui/src/components/TaskProgress.vue`
- `webui/src/components/DynamicIsland.vue`（仅状态语义接入，禁止视觉改版）
- `webui/src/composables/useDiscoveryTasks.ts`
- `webui/src/composables/useDiscoveryState.ts`
- `webui/src/__tests__/types.spec.ts`
- `webui/src/__tests__/discovery.spec.ts`
- `webui/src/components/__tests__/TaskProgress.spec.ts`
- `webui/src/components/__tests__/DynamicIsland.spec.ts`
- `webui/src/composables/__tests__/useDiscoveryTasks.spec.ts`
- `webui/src/composables/__tests__/useDiscoveryState.spec.ts`

### Allowed existing tests and user docs

- `tests/webui_app/test_webui_app_runtime.py`
- `tests/webui_app/test_webui_app_taskrun.py`
- `tests/webui_app/test_webui_app_tuning.py`
- `tests/webui_store/test_store_domains.py`
- `tests/source/test_source_boss.py`
- `tests/source/test_source_zhilian.py`
- `tests/test_pipeline_guard.py`
- `tests/test_pipeline_exec_accounts.py`
- `tests/test_screen_flow.py`
- `tests/test_workbench.py`
- `tests/test_workbench_api.py`
- `tests/tuning/test_tuning_manifest.py`
- `README.md`（只同步用户可见状态含义）
- `CHANGELOG.md`（按项目用户可感知写法记录本次变化）
- `.specify/memory/constitution.md`（只登记新增模块地图，不修改原则或版本）

### Forbidden files and data

- `用户数据目录/.career-scout/webui/webui.db` 及其 WAL/SHM：禁止写入、迁移或实验。
- 当前活动任务 `45a2dcd730334002b21f30e18d5008e6`：禁止停止、暂停、重启、修改、清理或作为测试输入。
- `.webui-state/`、`.release/`、运行缓存和历史产物。
- `roadmap/`、`design/`。
- `specs/033-log-whitebox/v1/` 与 033 根目录历史平铺文档：实施阶段只读。
- `webui/app.py`：禁止增加业务逻辑或路由注册。
- `webui/account_round_robin.py`：798 行红线文件，禁止修改。
- `webui/source.py`、`scripts/boss_cdp_raw.py`：兼容门面，禁止增加逻辑。
- `webui/resume_fields_api.py`、`webui/settings_api.py` 和 `webui/pipeline_jobs_api.py` 中即时 `/api/job-detail` 路径：任务白箱范围外；同文件的任务化职位复抓路径仍按 Allowed 范围修改。
- 其他未列入 Allowed 的业务文件。
- 禁止提交、推送、发布、打 tag、删除文件或清理正式数据。

### Reference direction

```text
API / runner / source / runtime adapter
                  ↓
          webui.whitebox
          ↓             ↓
store_whitebox     whitebox_rules
          ↓
        SQLite
```

Frontend direction remains:

```text
component/view → composable → api.ts
```

### Line gate

- 新 Python 文件以 600 行预警线为设计上限，绝不超过 800。
- `source_boss_cdp.py`、`source_zhilian_cdp.py`、`ai_screening.py`、`ai_screen_task.py`、`exec_search_api.py`、`task_continue_api.py`、`pipeline_jobs_api.py`、`app_support.py` 已超过 600 行：只允许替换/委托并保证文件净不增长。
- `account_round_robin.py` 禁止修改。
- 若实现需要在任何预警文件增加成段逻辑，停止并建立独立拆分 Spec。

## Phase 0 Decisions

研究结论见 [research.md](research.md)：

- 统一深模块而非分散补条件；
- 三层数据模型；
- 保守结论优先级；
- 计划范围与平台末页分离；
- 四层数量命名；
- 写入失败关闭；
- 恢复只补证据；
- 低数量不参与成功判定；
- 分阶段接入；
- 明确同步接口范围。

所有技术未知均已解决，无 `NEEDS CLARIFICATION`。

## Phase 1 Artifacts

- [data-model.md](data-model.md)
- [contracts/whitebox-service.md](contracts/whitebox-service.md)
- [contracts/task-report-api.md](contracts/task-report-api.md)
- [quickstart.md](quickstart.md)
- `.trae/rules/project_rules.md` 不存在，因此没有可更新的 Spec Kit 标记。

## Rollout Strategy

### Stage 1：成功判定

先上线规则和主抓取接入。任何缺证据的任务转为无法确认，部分失败转为部分完成。该阶段独立阻止虚假成功。

### Stage 2：完整抓取证据

补逐页事实、组合结束、数量层次和字段质量。完成后能解释空结果、翻页停止和 4044/3419 类差异。

### Stage 3：恢复和降级

接入恢复、AI、浏览器、账号、管线守卫、提交失败和写入失败。

### Stage 4：旁路收口

接入旧工作台、旧任务、任务化复抓和调参，移除按数量推断。

### Stage 5：统一展示

所有查询返回同一 `integrity`；普通前端显示简短提示；开发者按任务查看完整报告。

## Compatibility and Migration

- 迁移只创建新表和索引，不改写历史业务数据。
- 新任务在创建业务 run 后立即 `begin`；提交失败同样有计划和终态。
- 旧任务无 V2 记录时由查询适配返回历史证据不足。
- 现有 `task_logs` 和业务 run 表继续保留，不作为 V2 成功权威。
- 当前运行中的任务不在部署中途补接 V2；它结束后仍按历史兼容口径读取。

## Verification Gate

### Test-first checkpoints

实施者必须先使以下旧测试变红，再修改实现：

- `tests/webui_app/test_webui_app_runtime.py`：部分失败不再 `ok=True`。
- `tests/webui_store/test_store_domains.py`：组合完成后历史分页证据仍可查。
- `tests/webui_app/test_webui_app_taskrun.py`：有岗位和检查点不能把孤儿任务升级成功。

每个阶段先运行新增/修改的聚焦测试。失败原因相同且边界清晰时才批量返修。

### Final automated gate

1. `uv run python -m unittest tests.test_whitebox_rules`
2. `uv run python -m unittest tests.webui_store.test_store_whitebox`
3. `uv run python -m unittest tests.test_whitebox_integration`
4. 运行本任务涉及的 source、pipeline、screen、workbench、tuning 聚焦测试
5. `uv run python -m unittest discover -s tests`
6. 在 `webui/` 执行 `npm test`
7. 在 `webui/` 执行 `npm run build`
8. `uv run python -m unittest tests.test_repo_hygiene`
9. `git diff --check`
10. `git status --short`

测试输出和日志只能写系统临时目录。

### Real validation

- 自动化冒烟不冒充真实端到端。
- 真实验证必须新建一个与活动任务无关的最小任务，并由用户另行确认真实输入与允许写入边界。
- 若没有安全真实环境，交付报告必须写“真实端到端未执行”，不得写已验收。

## Documentation

- `README.md` 只说明用户会看到的完整性状态。
- `CHANGELOG.md` 使用增加/优化/修复开头的一句话条目，不写内部表名和技术细节。
- 实施完成时更新 `.specify/memory/constitution.md` 的模块地图，登记 `whitebox.py`、`whitebox_rules.py`、`store_whitebox.py` 和 `store_migrations_v5.py`；这属于模块地图同步，不改变宪法版本原则。

## Complexity Tracking

无宪法违规。新模块是全新白箱领域的必要落位；把逻辑继续追加到现有 runner、API 和 store 大文件会违反职责与行数规则。
