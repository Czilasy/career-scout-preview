# Implementation Plan: 错误如实呈现与数据口径一致（020）

**Branch**: `main`（沿仓库惯例直接交付） | **Date**: 2026-08-23 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/020-error-truth-consistency/spec.md`

## Summary

一个 Spec 修复 7 个已验证缺陷，全部先写失败测试再修：(1) 熔断器开闸失败码透传 `last_signal`（登录报登录、风控报风控），并在列表/JD 批次发起前接线 `try_reset`（冷却期满 + preflight 通过才复位）；(2) 前端 ApiError 兜底链在直出机器码前查 `ERROR_MESSAGES` 映射表；(3) 粗筛续跑幸存者计算排除本轮跨平台重复命中；(4) `delete_profile` 显式先删两张 RESTRICT 子表；(5) `screenStatus` 运行态优先于 `scraped_only`，`startAiScreen` 同步重置 `currentRoundStatus`；(6) 判定链合并门槛从数量比较改为覆盖比较（连 018 spec 文本一起修订）；(7) `save_finished_round` 加瞬时锁短重试，重试耗尽走 store 新增条件降级方法（事务内校验无结果轮才 succeeded→failed），内存报错明示可点继续重试保存，恢复路径验证续跑直达收尾（修订 018 FR-007）。

## Technical Context

**Language/Version**: Python 3.11（后端）、Vue 3 + TypeScript（前端）

**Primary Dependencies**: Flask（app.py 既有路由）、SQLite（screening_runs / screening_results / profile 域表）、vitest（前端测试）

**Storage**: SQLite，零 schema 变更（不改迁移、不改外键）

**Testing**: `uv run python -m unittest`（聚焦 + 全量）；前端 `npm test`、`npm run build`

**Target Platform**: Windows/macOS 本地桌面（pywebview 壳）与浏览器访问

**Project Type**: 本地 Web 应用（Flask + Vue）

**Performance Goals**: 熔断复位探测仅在开闸且冷却期满时发生（Boss preflight 为 CDP HTTP 轻探测 + 登录缓存，成本可忽略）；其余改动纯内存比较，无可感知影响

**Constraints**: 宪法禁止向 `webui/app.py`、`webui/store.py` 追加新职责——本批仅修改既有函数行为 + store 域内补一个方法（接手提示词预授权范围）；`webui/dist` 必须随前端源码改动重建并提交

**Scale/Scope**: 单用户本地库；单轮岗位量级 ~3000；历史轮 ~20

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 检查 | 结论 |
|---|---|---|
| I. 职责分层 | 熔断器语义留在 source.py（熔断器属主）；降级方法落 store.py 状态机域；重试落 result_rounds.py 服务层；app.py 只改既有函数内的行为与一处收尾接线；前端只动 composable 计算、一行状态重置与 api.ts 兜底链 | 通过 |
| II. 单文件尺寸 | source.py +~40 行、store.py +~45 行、app.py 净增 ≤25 行、result_rounds.py +~30 行、screen_flow.py ±0、api.ts +~5 行、useScreenRoundFlow.ts +~3 行——均在修改既有行为的预授权范围内，不新增职责 | 通过（附判断依据） |
| III. 引用方向 | api.ts → errorCodes.ts（同层模块，既有 import 方向一致）；result_rounds → store 公开方法；app.py → result_rounds/store 公开方法；无反向依赖 | 通过 |
| IV. 拆分与重构纪律 | 无拆分；行为变化全部由失败测试先行定义；018 契约修订堂堂正正改 spec 并注明出处 | 通过 |
| V. 验证门禁 | 聚焦测试 + 后端全量 + 前端测试 + `npm run build` + 卫生测试；dist 同步提交 | 通过（见 Verification Gate） |

Phase 1 复查：设计未引入反向依赖、未触碰 schema/migrations，结论不变。

## File Boundaries

*GATE: 接手提示词已预授权本批文件边界（「修改既有行为或 store 域内补方法」）；按宪法默认规则代确认如下。*

- **Allowed files**（允许修改，均为修改既有行为）:
  - `webui/source.py` — `SourceCircuitBreaker` 补两个查询辅助（`open_failure_code`、`cooldown_elapsed`）；Boss/智联 source 各补一个私有恢复方法（冷却期满→preflight→`try_reset`）；5 处开闸检查点（列表 ~599、单岗位详情 ~724、JD 批量 ~930、智联批量串行 ~2441 与并行 ~2521）失败码改透传；批量级检查点（599/724/930/2521）发起前先尝试恢复，逐岗位检查点（2441）只透传不复位（避免批内 N 次探测）。`SIGNAL_CODES`、开闸阈值、冷却语义不变。
  - `webui/screen_flow.py` — `load_resume_verdicts_with_fallback` 触发条件改覆盖比较（~130 行），合并算法本身不动。
  - `webui/app.py` — 仅三段既有行为：`_rough_kept_from_resume` 排除 `_dup_ids`（~3195）；resume_inconsistent 护栏事件改覆盖口径（~3203）；`_run_ai_screen_task` 收尾段加 `finalized` 局部标记 + 通用异常分支内先试条件降级、成功时内存文案改「筛选已完成但结果保存失败，点继续可重试保存」并落诊断事件（~3700/3788）。
  - `webui/result_rounds.py` — `save_finished_round` 内对 `sqlite3.OperationalError`（瞬时锁）做 2 次短退避重试（共 3 次尝试）；其余异常类型不重试。
  - `webui/store.py` — `delete_profile` 在删主表前显式删 `profile_job_command_receipts`、`profile_job_events` 两张 RESTRICT 子表（顺序：回执→事件→主行）；新增 `downgrade_succeeded_if_no_result_round(run_id, *, error_code, error_reason)`：`_BEGIN_IMMEDIATE` 事务内校验 run 仍为 succeeded 且同流程（scrape_task_id+platform）无可见 result_snapshot 轮才置 failed（仿 `finish_screening_run` 事务模式）。
  - `webui/src/api.ts` — `ApiError` 兜底链在 `error`/`error_code` 直出前插入 `ERROR_MESSAGES` 查表（import 自 `./errorCodes`）。
  - `webui/src/composables/useScreenRoundFlow.ts` — `screenStatus` 计算中「快照 running / screenBusy」优先于 `scraped_only` 次级状态。
  - `webui/src/views/DiscoveryView.vue` — `startAiScreen` 进入发起路径后重置 `currentRoundStatus`（一行）；若 3533 行 disabled 逻辑仍允许重复发起则同段修正。
  - `specs/018-screening-chain-bugfix/spec.md` — 两处契约修订：US2 验收场景 1 与 FR-004 的「判定数少于断点数」改覆盖比较表述；FR-007「纯换序，不新增补偿/回滚逻辑」改「换序 + scoped 重试与条件降级」；均注明「修订自 020」。
- **Forbidden files**（禁止修改）: `webui/store_migrations.py`（零 schema/FK 变更）、`webui/pipeline_exec.py`（登录二次复核链路随失败码透传自然修复，预期零改动）、`webui/task_runners.py`、`webui/ai.py`、`webui/cross_platform_dedupe.py`、`webui/job_fingerprint.py`、`webui/error_registry.py`（`source_blocked` 文案保留给真风控）、`scripts/**`、`hooks/**`、`packaging/**`、`specs/001-017`、`specs/019`（实现补洞，spec 不改）、版本与 CHANGELOG（bug 修复不发版，收口时用户决定）
- **New files**（新增）: 无生产代码新文件；测试落既有文件（`tests/test_source.py`、`tests/test_screen_flow.py`、`tests/test_webui_app.py`、`tests/test_webui_store.py`、`webui/src/__tests__/errorCodes.spec.ts`、`webui/src/composables/__tests__/useScreenRoundFlow.spec.ts`）；`webui/dist/**` 随构建更新。
- **Reference direction**: `app.py → result_rounds → store`；`app.py → screen_flow`（既有）；`api.ts → errorCodes.ts`；store 方法不反向依赖上层；前端 view → composable → 既有 helpers。
- **Line gate**: source.py ≤2790、store.py ≤4930、app.py ≤9400（均仍在超大文件存量之上、增量在预授权修改范围内；拆分由既有拆分 Spec 负责）；result_rounds.py ≤300（宪法上限 800）；前端单文件增量 ≤10 行。
- **Rationale**: 七条全部是对既有行为的缺陷修复，逻辑归属文件即缺陷所在文件；熔断器属主是 source、降级属主是 store 状态机、重试属主是 result_rounds 服务——不建新模块即可保持职责内聚，且避免把行为修复散落成新层。

## Verification Gate

- 功能/重构/拆分交付：最终门禁为相关模块聚焦测试（test_source、test_screen_flow、test_webui_app、test_webui_store、test_result_rounds、test_error_registry、前端 errorCodes/useScreenRoundFlow/screenFlow spec）、后端全量测试（`uv run python -m unittest discover -s tests`）、前端测试（`npm test`）、`npm run build`、仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- 前端源码有改动：`webui/dist` 必须重建并随源码一并提交（pre-push 拦截）。
- 收口发布任务：按根 `AGENTS.md`，不在本批默认执行。

## Project Structure

### Documentation (this feature)

```text
specs/020-error-truth-consistency/
├── plan.md              # 本文件
├── research.md          # 证据核对结论与设计决策
├── data-model.md        # 涉及实体与状态语义（零 schema 变更说明）
├── quickstart.md        # 端到端验证指南
├── contracts/           # 错误呈现契约（开闸失败码、降级守卫）
│   └── error-faithfulness.md
├── checklists/
│   └── requirements.md  # specify 阶段质量清单
└── tasks.md             # /speckit-tasks 产出
```

### Source Code (repository root)

```text
webui/
├── source.py            # 熔断器透传 + 复位接线（缺陷1）
├── screen_flow.py       # 合并门槛覆盖比较（缺陷6）
├── app.py               # 续跑去重排除 / 护栏口径 / 收尾降级接线（缺陷3/6/7）
├── result_rounds.py     # save_finished_round 瞬时锁重试（缺陷7）
├── store.py             # delete_profile 子表清理 + 条件降级方法（缺陷4/7）
└── src/
    ├── api.ts                       # ApiError 映射查表（缺陷2）
    ├── composables/useScreenRoundFlow.ts  # 运行态优先（缺陷5）
    └── views/DiscoveryView.vue      # startAiScreen 状态重置（缺陷5）

tests/
├── test_source.py       # 熔断器扩展用例
├── test_screen_flow.py  # 合并门槛覆盖用例
├── test_webui_app.py    # 续跑去重 / 多run链 / 降级恢复链
└── test_webui_store.py  # 画像删除 / 降级守卫

webui/dist/**            # 随 npm run build 更新并提交
```

**Structure Decision**: 全部为既有文件内行为修复，无新层；分层与引用方向见 File Boundaries。

## Complexity Tracking

> 无宪法违规需要豁免；`app.py`/`store.py` 的少量行数增加属接手提示词预授权的「修改既有行为 / store 域内补方法」范围，拆分另由既有拆分 Spec 承担。
