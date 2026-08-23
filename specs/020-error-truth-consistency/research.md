# Research: 错误如实呈现与数据口径一致（020）

**Created**: 2026-08-23 | 输入：接手提示词（需求冻结）+ 本会话逐条代码级复核。

## 1. 证据复核结论（全部属实）

| # | 缺陷 | 复核后的证据锚点（行号以工作区为准） |
|---|---|---|
| 1 | 熔断器误报+永不复位 | `webui/source.py`：SIGNAL_CODES 含 `source_login_required`（184-189）；连续 2 次开闸（206-215）；`try_reset` 全仓库无调用方（230-248，仅定义与 docstring）；开闸硬编码 `source_blocked` 共 **5 处**（列表 599-603、单岗位详情 724-732、JD 批量 930-940、智联串行 2441-2455、智联并行 2521-2536——比接手提示词多出 724/2441/2521 三处同款）；JD 批内逐岗位推进计数（1090-1097）。`webui/error_registry.py:106-111` source_blocked=「IP 级风控拦截」。`webui/pipeline_exec.py:1012-1013` 登录二次复核只匹配 `source_login_required`（透传后自然覆盖开闸场景，pipeline_exec 预期零改动） |
| 2 | 错误码映射死代码 | `webui/src/errorCodes.ts:77-148` ERROR_MESSAGES 全库仅定义处 + 自检 spec 引用；`webui/src/api.ts:73-77` ApiError 兜底链不含查表 |
| 3 | 续跑重复岗位双列表 | `webui/app.py:3195-3199` `_rough_kept_from_resume` 不排除 `_dup_ids` → 3274 并入 kept_ids → 3287-3288 dup 条目强制覆盖剔除表 → 3295 幸存者仍含它 |
| 4 | 画像删除 FK RESTRICT | `webui/store.py:1685-1704` delete_profile 只删主表（docstring 谎称级联）；`store_migrations.py:1957-1958/1973-1974` 两子表 ON DELETE RESTRICT；`profile_jobs`→`candidate_profiles` 是 CASCADE（233 行），故只需显式删两张 RESTRICT 子表 |
| 5 | scraped_only 吞运行态 | `useScreenRoundFlow.ts:100-101` scraped_only 判断先于 raw 运行态；`DiscoveryView.vue:1801` startAiScreen 清 roundContext、不重置 `currentRoundStatus`（2002/2019/2119/2275 置 scraped_only）；`screenFlow.ts:87-88` scraped_only → start 按钮 |
| 6 | 合并门槛数量比较 | `webui/screen_flow.py:130` `len(verdicts) >= len(checkpoint_ids)` 即跳过合并；`webui/task_runners.py:166` `_FINE_VERDICTS` 证明精筛判定计入总数；`app.py:3203-3207` 护栏同口径；018 spec US2 场景 1 / FR-004 原文即数量比较 |
| 7 | 终态后写轮失败被吞 | `app.py:3707` finalize 在前、3722 save_finished_round 在后；异常落 3788 通用分支 → 3799 `_write_run_unless_finished("failed")` → `store.py:2147-2152` succeeded→failed 抛 ValueError → `app.py:1917-1925` 只捕获 DiscoveryStoreConflictError → ValueError 被 3803 `_OPERATIONAL_ERRORS`（120-128 含 ValueError）吞掉。结果：DB succeeded、内存 failed、零结果轮 |

## 2. 设计决策与理由

### D1 复位接线放 source 内部，而非编排层
- **Decision**: 在 `fetch_list` / `fetch_detail_batch`（含 Boss 单岗位详情、智联并行批量）的开闸检查点内部：冷却期满 → `self.preflight()` → `try_reset(outcome.ok)`，成功则继续本次抓取。
- **Rationale**: 熔断器属主是 source（类 docstring 明言 orchestrator 调 try_reset，但现状 orchestrator 无一处接线；在 source 门点接线等价于"批次发起前"且不扩 app.py/pipeline_exec.py）。冷却未满直接失败，不浪费探测。逐岗位门点（智联串行 2441）只透传不复位，避免批内 N 次探测。
- **Alternatives**: 在 pipeline_exec 每个调用点前接线——需改 4+ 处编排代码且引 source 内部状态，拒绝。

### D2 开闸失败码 = `open_failure_code()`
- **Decision**: breaker 新增只读辅助返回 `last_signal`（∈ SIGNAL_CODES 时），否则回落 `source_blocked`。5 处门点统一改用。
- **Rationale**: last_signal 只可能是 4 个 SIGNAL_CODES 成员，error_registry 均有正确文案与 resume_condition；`source_cdp_unavailable` 不在 SIGNAL_CODES、不进 breaker，浏览器自动重启链（pipeline_exec recovery.is_browser_lost）不受影响。
- **已核实的行为边界**（实施时不必意外）：`source_login_required` 本身就是 blocking+systemic（∈ SYSTEMIC_BLOCK_CODES）。列表层开闸登录码先被 `pipeline_exec:1012` 登录二次复核拦截（probe 通过重试 / 仍失效跳过组合），不会落到硬停分支；JD 批量层开闸登录码按既有 JD 硬停口径停任务（今天 source_blocked 同样硬停），只是文案如实——行为类别不变、语义变准。

### D3 重试放 `save_finished_round` 内部
- **Decision**: 仅对 `sqlite3.OperationalError`（busy/locked）重试 2 次（短退避），共 3 次尝试；其他异常不重试直接抛。
- **Rationale**: 主因是瞬时锁；save_pipeline_result 是单事务插入（失败即未提交），重试无重复轮风险；upgrade 路径是幂等更新。两处调用方（自然完成 + 结束保存）同时受益，app.py 零接线。
- **Alternatives**: 调用点包 retry——需在 app.py 加包装逻辑，扩大超大文件，拒绝。

### D4 条件降级仿 `finish_screening_run` 事务模式
- **Decision**: store 新增 `downgrade_succeeded_if_no_result_round`：`_BEGIN_IMMEDIATE` + 校验（status==succeeded 且同流程无可见 result_snapshot 轮）→ UPDATE failed；任一条件不满足返回 False。同流程判定复用 result_rounds 的可见轮语义（done/partial/scraped_only + scrape_task_id+platform 匹配）。
- **Rationale**: 仿既有 `_BEGIN_IMMEDIATE` 原子改终态模式（finish_screening_run/claim_paused_screening_run），事务内校验杜绝「已有轮还降级」的竞态。
- **恢复路径验证**（测试必须证明）：failed run 被 `find_resumable_screen_run` 命中（paused→failed 优先级）→ 新 run 继承断点/判定/JD（`_rough_done_ids` 来自旧 run checkpoint、resume_fine_verdicts 覆盖精筛 todo、resume_jd 内存持有）→ 粗筛 todo 空、精筛 todo 空 → 直达 finalize → save_finished_round 新轮 → succeeded。

### D5 前端运行态优先 + 状态重置双保险
- **Decision**: `screenStatus` 中「快照 running 或 screenBusy」先于 scraped_only 判断返回 running；`startAiScreen` 发起时置 `currentRoundStatus="screened"`（一行）。
- **Rationale**: 计算优先级修复整类问题（无论谁残留 scraped_only）；显式重置保证终态展示不被次级状态遮蔽。既有用例「scraped_only + completed 快照 → start」不受影响（completed 非运行态）。
- **Alternatives**: 只做重置——若其他路径（一键筛选对话框等）置 scraped_only 后发起筛选仍复现，不彻底，拒绝。

### D6 判定覆盖比较
- **Decision**: 触发条件改 `set(checkpoint_ids) - set(verdicts)` 非空；护栏事件同口径记 `missing` 数。合并算法（同源校验、旧→新覆盖）零改动。
- **Rationale**: 数量比较在"精筛判定计入总数"（task_runners `_FINE_VERDICTS` 分流前的 load 总数）下天然失真。既有用例 `test_returns_own_verdicts_when_checkpoint_covered`（断点["a"] 判定{"a"}，全覆盖）在新口径下仍跳过合并，无需改写。

### D7 018 契约修订方式
- **Decision**: 直接改 018 spec 原文并在该处注明「修订自 020」；不改 019（019 契约本身正确，是实现漏了续跑反向边）。
- **Rationale**: 接手提示词明令「修订时须堂堂正正改 spec，避免 spec 与实现漂移」。

## 3. 风险与缓解

- **既有熔断器测试可能被复位接线影响**：现有测试开闸后冷却默认 60s 未满 → 不触发探测，行为不变；逐个核跑（见 tasks）。
- **降级与 user_finished 竞态**：finish_screening_run 会把 running→interrupted(user_finished)；若用户在降级前结束，run 已非 succeeded → 降级守卫拒绝，行为安全（测试覆盖）。
- **`screen_jobs`/`match_jds` 空列表行为**：018 已有全断点续跑用例证明空 todo 直达收尾，缺陷 7 恢复测试复用该机制。
