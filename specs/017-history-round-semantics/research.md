# Research: 历史轮次与流程终结语义修复

**Date**: 2026-08-22 | **Status**: Complete（全部未知项已核实）

## R1 状态映射统一方向

- **Decision**: 保留 `_public_task_status`（http-api.md 公共词汇），删除 `_run_to_task_status`，其调用点全部替换。
- **Rationale**: 前端消费的是公共词汇——`useScreenRoundFlow.ts:56` 与 `DiscoveryView.vue` 多处检查 `queued/completed/completed_with_pending/paused/failed/cancelled/interrupted`；`_run_to_task_status` 独有的 `waiting` 全前端无人消费。统一后前端只需核对无需改词。
- **Alternatives**: 反向统一到 `_run_to_task_status`（要求前端全量换词，放弃）；两套并存（本 spec 要消除的对象）。

## R2 `get_latest_done_run_id` 拆除范围

- **Decision**: 函数删除，三个调用点全部改造：
  - 批量重抓 `app.py:7038-7042`：删回退，缺 `source_run_id` 直接 409（现状已有该错误分支，只需删回退）。
  - 单岗位重抓 `app.py:6909-6913`：同上。
  - 单 JD 回写 `app.py:6829-6833`：该端点请求本就携带 `source_run_id`（`app.py:6575`），回写目标改用请求值，不再二次询问最新。
  - `_run_recrawl_task` 内部 `app.py:7360` 的 `or` 回退随参数必传化消失。
  - `tests/test_webui_store.py:1659` 对应断言随函数删除。
- **Rationale**: 三处都是"猜最新"的隐式目标，是 A4 隐患的全部入口。
- **Alternatives**: 保留函数但修过滤口径（保留"猜最新"语义，违背 FR-010，放弃）。

## R3 快照写入收口形态

- **Decision**: 新服务 `webui/result_rounds.py` 承载全部历史轮写入与更新：
  - `save_finished_round`：自然完成与结束保存共用（构建好的 result 传入，服务内做防重与落库/原地升级）。
  - `save_scraped_only_round`：跳过筛选建轮（幂等，沿用现有 `latest_scraped_only_for_source` 查重）。
  - `apply_recrawl_writeback`：重抓判定/JD 回写 + 计数重算 + 定稿时间刷新。
  - 防重规则：落库前按 `scrape_task_id + platform` 查现存轮，同流程已有轮则原地升级（镜像现有 `upgrade_scraped_run` 模式），保证一条流程一条轮的唯一执行点。
- **Rationale**: 镜像已验证的升级模式；app.py 只删不增；`scrape_only.py` 的 `save_screen_result` 分流逻辑删除并入，消除第二套写入。
- **Alternatives**: 防重靠任务事件（历史方案，事件只防不收，已证明漏）；在 store 层做（服务编排职责下沉，store 越界）。

## R4 存量清空迁移形态

- **Decision**: `store_migrations.py` 新增版本化迁移（版本号顺延），删除全部 `record_kind='result_snapshot'` 轮及其子表行，表集合与 `delete_history_result_preserving_logs` 一致（screening_results / screening_pending_results / pipeline_checkpoints / scrape_page_progress / screening_runs 行），任务日志与活动任务行不动。迁移幂等（版本号只跑一次）。
- **Rationale**: 与现有删除路径同一表集，最小惊讶；无 DDL。
- **Alternatives**: 精细识别保留部分轮（用户明确拒绝，全清）；TRUNCATE 全表（误伤活动任务与日志，放弃）。

## R5 定稿时间语义实现

- **Decision**: `recount_pipeline_result` 重算时同步刷新 `finished_at`（`store.py:1041-1048` 现 UPDATE 增加 finished_at）；`upgrade_scraped_run` 现已刷新 finished_at（保持）；历史列表已返回 `finished_at`（`result_history.py:118`），抽屉改显示该字段；`list_history_rounds` 排序继续用 created_at 不变。
- **Rationale**: 一行级改动达成 FR-007；排序稳定与时间诚实兼得。

## R6 历史标签收敛

- **Decision**: `historyStatusLabel` 仅保留三种映射；未知状态不渲染标签（存量已清，理论不出现，防御性空白优于错误文案）。
- **Rationale**: "失败但有 N 个岗位"随取消/失败轮消失而失去存在基础。

## R7 finish 端点内结果构建的去留

- **Decision**: finish 端点的 result 构建逻辑（`app.py:8985-9103`）留在原位，只把"写入"换成 `result_rounds.save_finished_round` 调用；构建与写入分离，写入收口。
- **Rationale**: 整段搬迁属纯迁移且体量大（~120 行高分支逻辑），混入会放大回归面；宪法禁止的是追加新逻辑，保留既有构建位置合规。

## R8 前端状态消费核对点

- **Decision**: 统一词汇后，前端已包含 `queued` 检查集无需换词；tasks 中列核对项（`waiting` 若有残留消费改为 `queued`，目前检索为零）。

## Agent 上下文

- `.trae/rules/project_rules.md` 本仓库不存在（016 亦无），该步骤按仓库先例跳过。
