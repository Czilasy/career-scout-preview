# Research: 多轮结果历史与稳定性整修

**Date**: 2026-08-11

## Decision: 归档采用 `screening_runs.archived_at`

- **Decision**: 新增可空 `archived_at TEXT` 字段；非空表示该结果已从“默认最新”视图移除，但仍在历史列表中保留。
- **Rationale**: 现有结果全部是 `record_kind='result_snapshot'`，用单独标记比改 `record_kind` 更简单；`load_latest_*` 只需要加 `archived_at IS NULL`，历史查询不需要排除任何行。
- **Alternatives considered**: 把归档行改成 `record_kind='archived_result'`，但旧逻辑里同一平台可存在多个 `result_snapshot`，无法表达“谁是当前最新”；新增独立状态表则过度设计。

## Decision: 归档范围是全部非归档结果快照

- **Decision**: “开始新一轮”和“重新上传简历”时，把所有 `archived_at IS NULL` 的结果快照一次性归档。
- **Rationale**: 当前重置是全局行为，新简历会让 BOSS/智联旧结果都语义过期；只归档“最新一条”会导致下一条旧快照被 latest 查询重新当成最新。
- **Alternatives considered**: 只归档每平台最新一条；会残留旧快照被 latest 查询选中，与“暂无结果”预期冲突。

## Decision: 历史列表包含有岗位产出的所有结果快照

- **Decision**: 历史列表查询 `record_kind='result_snapshot'` 且 `EXISTS(SELECT 1 FROM screening_results WHERE run_id=screening_runs.id)`。
- **Rationale**: `save_pipeline_result` 会把 kept、dropped、pending 都写入 `screening_results`，与 04 页实际展示来源一致；没有岗位产出的运行不会出现。
- **Alternatives considered**: 用 `screening_pending_results` 计数；与 04 页展示来源不一致，且旧数据未必有 pending 行。

## Decision: 失败/中断/取消但有岗位的结束路径也要落快照

- **Decision**: 筛选任务以失败、中断或取消结束时，如果内存中已构建含岗位结果且尚未保存快照，在写终态前调用 `save_pipeline_result(status=<原始终态>)` 保存该轮；没有岗位产出则不保存。
- **Rationale**: 用户确认“失败、中途停止等，只要生成了 04 页岗位都算一轮”；仅靠查询无法找回从未落库的结果。
- **Alternatives considered**: 只展示已落库快照；会漏掉失败前已生成岗位但未保存的轮次，与已确认口径不符。

## Decision: 删除历史轮次保留任务日志与审计

- **Decision**: 新删除方法只删除该 run 的 `screening_results`、`screening_pending_results`、`pipeline_checkpoints`、`scrape_run_jobs`、`scrape_page_progress` 与 `screening_runs`，不删除 `tasks`/`task_logs`，也不删除 `screening_source_attempts`、`jobs`、`profile_jobs`、`feedback_events`。
- **Rationale**: 用户确认“只删轮次，保留审计”；现有 `clear_pipeline_result` 会删 `tasks` 并级联 `task_logs`，不能直接复用。
- **Alternatives considered**: 复用 `clear_pipeline_result`；会丢失任务日志，违反审计保留边界。
- **Decision**: 删除最新未归档轮次后，若该平台已无未归档快照，把最近一个归档轮置回未归档（`archived_at IS NULL`）恢复为最新；若仍有未归档旧快照，由其中最新一条接任；没有任何更早轮次时显示“暂无结果”。

## Decision: 历史数据访问使用 store mixin，而非直连新连接

- **Decision**: 新增 `ResultHistoryStoreMixin`，由 `TaskStore` 继承；所有历史读写都经过 `self._connection()` 与 `_assert_recovery_writes_allowed`。
- **Rationale**: 避免新模块另开 SQLite 连接绕过事务与 recovery lock，保持现有 store 的一致性保护。
- **Alternatives considered**: `result_history_store.py` 直连 `store.db_path`；会绕过现有锁与恢复期写入门禁，风险更高。

## Decision: AI 重试默认策略抽到 `ai_retry.py`

- **Decision**: 默认策略为首次 + 2 次重试共 3 次，每次失败固定 30 秒；`retry_limits` 显式配置时以调优 manifest 为准；默认路径不再用单次 timeout 预算截断等待。
- **Rationale**: 需求要求固定 30 秒节奏，与单次 timeout 无关；抽模块便于聚焦测试，避免继续膨胀 `ai.py`。
- **Alternatives considered**: 直接在 `ai.py` 改常量；测试分散且大文件继续膨胀。

## Decision: B037 继续前失效登录缓存

- **Decision**: 在 `/api/task/continue` 的阻断复查前，对 run 的账号 × 平台调用 `invalidate_login_state`，强制下一次 preflight 真实探测。
- **Rationale**: 不改 `webui/source.py` 也能实现“不信任缓存”；继续成功后 preflight 会写回新缓存，不影响后续展示。
- **Alternatives considered**: 给 `ZhilianSource.preflight` 加 `use_cache=False` 参数；需要改超大 `source.py`，收益相同但风险更高。

## Decision: 前端历史模式由 `DiscoveryView` 持有

- **Decision**: `ResultHistoryDrawer.vue` 放在 `DiscoveryView` 内，App 顶栏只通过暴露方法触发打开；历史模式、平台锁定、禁用改写动作都在 `DiscoveryView` 内由 `composable/resultHistory.ts` 管理。
- **Rationale**: 历史轮次最终要渲染到现有 04 页，状态与结果页同属 DiscoveryView；App 只负责入口。
- **Alternatives considered**: 抽屉放 App 并跨组件传结果；状态分散，且需要修改 App 与 DiscoveryView 两处大文件更多接线。

## Decision: 历史详情保留原始机器状态

- **Decision**: 单轮详情加载岗位明细可复用 `store.load_latest_pipeline_result(run_id)`，但 `status` 必须从 `screening_runs` 读取原始机器值并覆盖返回；该 loader 会把非 `partial` 归一为 `completed`、把 `partial` 归一为 `completed_with_pending`，不能直接作为历史展示状态。
- **Rationale**: 历史轮次包含失败/中断等状态，归一化会丢失“失败但有 N 个岗位”的语义。
- **Alternatives considered**: 新写完整详情 loader；岗位明细组装可复用现有逻辑，只需覆盖状态字段。
