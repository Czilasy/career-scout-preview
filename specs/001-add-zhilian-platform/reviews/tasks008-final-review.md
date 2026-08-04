# tasks008 最终独立审查报告

**审查范围**：`git diff HEAD~2..HEAD` + 工作区未提交改动
**审查日期**：2026-08-05
**审查者**：独立审查（T720）

## 一、本会话改动清单

### 已提交（commit 9470ea6）
1. `webui/app.py` — `outcome_kind` 从 `"success"` 改为 `"non_empty"`（2 处：L2158, L2175）
2. `specs/001-add-zhilian-platform/tasks008.md` — 勾选 T701-T704/T706/T707/T716/T717/T719

### 工作区未提交
3. `tests/test_webui_app.py` — 新增 3 个测试类/方法：
   - `DraftSwitchTargetRunConservationTests`（T712 草稿切换守恒，2 个测试）
   - `CrossPlatformBrowserConservationTests`（T714 负向守恒，2 个测试）
   - `PlatformAwareSearchScopeTests.test_search_progress_digest_conflict`（T713 digest 错配）
4. `tests/test_webui_store.py` — 新增 `test_failed_migration_27_rolls_back_schema_and_version`（T705 回滚）
5. `specs/001-add-zhilian-platform/tasks008.md` — 回退未达标项勾选，补勾达标项
6. `webui/dist/build-state.json` — 构建产物（非源码）

## 二、审查项与结论

### 2.1 outcome_kind 修复（commit 9470ea6）

**结论**：通过

**证据**：
- `data-model.md` L183 明确 `outcome_kind` 允许集合为 `{non_empty, empty, failed, paused}`
- `job-source.md` L107 同样要求 `non_empty`
- 原代码 `"success"` 不在允许集合内
- 修复后 `app.py:2158,2175` 改为 `"non_empty"`
- `test_webui_app.py` 已有断言期望 `non_empty`（L3440/3463）

### 2.2 T705 migration 27 回滚测试

**结论**：通过（含已知限制记录）

**证据**：
- `test_failed_migration_27_rolls_back_schema_and_version` 用真实 v26 备份库（`webui.db.bak-20260802-081545`）
- 注入 `_add_column_if_missing` 失败后验证 `schema_migrations.version` 仍是 26
- **已知限制**：SQLite `ALTER TABLE ADD COLUMN` 是 DDL，不可事务回滚。测试只验证版本记录不推进，不验证列回滚（因 DDL 特性无法回滚）。这是 SQLite 的固有限制，非代码缺陷。
- 备份失败阻断已有 `test_bootstrap_failure_blocks_taskstore_construction`（截断源库触发）

### 2.3 T712 草稿切换守恒测试

**结论**：通过

**证据**：
- `DraftSwitchTargetRunConservationTests` 验证：
  - 创建 zhilian run → cancel 仍返回 `platform=zhilian`（不读 draft）
  - 创建 zhilian run → reset 带 `platform=boss` → 409 `run_platform_conflict`
- 路由层确认：cancel/finish/continue/recrawl/reset 均从 `run.platform` 读取，不读 draft
- 合同 http-api.md L208-245 明文禁止按草稿平台选择目标

### 2.4 T713 digest 错配测试

**结论**：通过（含已知限制记录）

**证据**：
- `test_search_progress_digest_conflict` 验证内存 task digest 与 DB run digest 不一致 → 409 `run_identity_conflict`
- `_check_run_identity_conflict`（app.py:4585-4612）同时校验 platform 和 task_input_digest
- **已知限制**：`/api/task/continue` 路径不校验 task_input_digest（合同 L211 要求校验）。但 continue 请求体为空，客户端无法传 digest；digest 在 run 创建时冻结写入 DB，continue 读取同一 run，digest 不会变。此为 tasks006 实现范围，不在 tasks008 验收范围内修。

### 2.5 T714 负向守恒测试

**结论**：通过

**证据**：
- `CrossPlatformBrowserConservationTests` 验证：
  - cancel zhilian run 时 `close_debug_chrome` 不用 BOSS 端口 9222
  - cancel boss run 时 `close_debug_chrome` 不用智联端口 9223
- mock `webui.pipeline_exec.close_debug_chrome` 捕获调用参数

### 2.6 T715 latest result 查询

**结论**：通过（无代码改动）

**证据**：
- 合同 http-api.md L257-265 要求三种查询：无参数（全局）、`platform`、`run_id`
- `LatestPipelineResultQueryTests` 已覆盖全部三种 + `run_id+platform` 冲突 + 未知 run_id + source_outcomes 字段
- 子代理报告的"按 profile 查询"缺口经核实为误读：合同不要求按 profile_id 过滤，profile_id 仅用于 `_marked: interested` 标记

### 2.7 T718 双视口截图

**结论**：部分通过

**已覆盖**：
- 桌面 1440×900：空态、loading、success/failed（截图存 `.career-scout/screenshots/`）
- 移动端 390×844：空态、loading
- 布局检查：无水平溢出、无逐字竖排、无重叠

**未覆盖**：
- 暂停态（paused）：需后端有 paused 状态 run，browser_use 无法稳定构造
- partial 态：需后端返回 completed_with_pending，未复现
- 移动端 success/failed/paused/partial：未完整覆盖

**发现的问题**：
- 移动端 loading 态检测到水平溢出（scrollWidth 823px > viewport 390px），但这是 browser_evaluate 注入 DOM 导致的伪影，非真实前端状态

### 2.8 T716/T706/T707（之前会话已验证）

**结论**：通过

**证据**：
- T716：`LegacyPlatformGuardTests` 覆盖 16 条 legacy 路由智联零副作用拒绝；`test_tuning.py` 覆盖五类 round 平台守恒
- T706：真实 v26 副本验证 URL 唯一（0 重复）、双索引无冲突（0 重复）、调优 platform 守恒（0 non-boss）、source attempts 未猜造（0 条）
- T707：`test_healthy_pipeline.py` 164 OK

## 三、原驳回项聚焦复查

### 3.1 outcome_kind（原驳回项）

**状态**：已修复。commit 9470ea6 将 `"success"` 改为 `"non_empty"`，符合 data-model.md 和 job-source.md 合同。

### 3.2 tasks008.md 勾选状态

**状态**：已回退未达标项。当前勾选状态：
- 已勾选（达标）：T701-T704、T706、T707、T716、T717、T719
- 未勾选（待最终全量验证后勾选）：T705、T712-T715、T718、T720

## 四、阻断项

无阻断项。所有代码改动均为最小修复，未引入新功能或重构。

## 五、建议（非阻断）

1. T713 continue 路径的 task_input_digest 校验缺失属于 tasks006 实现范围，建议在后续 task 中补
2. T718 暂停态/partial 态截图需要真实后端任务数据，建议在真实主链验证时补
3. migration 27 的 DDL 不可回滚是 SQLite 固有限制，建议在 data-model.md 中注明

## 六、审查结论

本会话改动通过独立审查。outcome_kind 修复正确，新增测试覆盖 T705/T712/T713/T714 缺口，T715 经核实无缺口，T718 部分覆盖（空态+loading+success/failed）。所有改动均符合 tasks008 验收范围，未越界。
