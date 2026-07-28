# Tasks: 健康流程补救与优化

**Source**: [plan.md](./plan.md) | **Spec**: [spec.md](./spec.md)

## 切片 1：状态与完成判定

- [X] 1.1 store.py: 将 `paused` 加入 `RUN_STATUSES` 和 `RUN_TRANSITIONS`；定义 `TASK_STATUSES={waiting,running,paused,completed,completed_with_pending,failed,cancelled}`
- [X] 1.2 store.py: `update_screening_run` 加入状态机校验，非法迁移拒绝
- [X] 1.3 store.py: 新增 `finalize_run_status(run_id)` — processed+dropped==source 且无未开始才 completed；有 pending 无阻断标 completed_with_pending；有未开始或阻断标 paused
- [X] 1.4 app.py: 所有 `task["status"]` 内存字典写入同步落 DB
- [X] 1.5 失败测试: `test_finalize_status_no_unstarted_must_not_complete`
- [X] 1.6 失败测试: `test_systemic_block_must_pause_not_complete`
- [X] 1.7 失败测试: `test_all_processed_with_few_pending_completes_with_pending`
- [X] 1.8 失败测试: `test_state_machine_rejects_illegal_transition`
- [X] 1.9 失败测试: `test_cancel_preserves_results_no_auto_resume` (FR-024)

## 切片 2：持久化任务和岗位进度

- [X] 2.1 store.py: 实现 `insert_pending_result(run_id, job_id, failure_stage, retryable, attempts, origin_zone, ai_payload_json)`
- [X] 2.2 store.py: 实现 `update_pending_count(run_id)` — 从 screening_pending_results 实时计数
- [X] 2.3 store.py: 新增 `pipeline_checkpoints` 表 (run_id, stage, completed_keys_json, saved_at)
- [X] 2.4 store.py: 实现 `save_checkpoint(run_id, stage, keys)` 和 `load_checkpoint(run_id, stage)`
- [X] 2.5 store.py: 实现 `append_task_event(run_id, event_type, payload_json)` — task_logs 写入 (FR-038)
- [X] 2.6 app.py: JD 抓取失败/未开始岗位写入 screening_pending_results
- [X] 2.7 app.py: 每次暂停时保存 checkpoint；继续时加载 checkpoint 跳过已完成
- [X] 2.8 app.py: 阶段开始/完成/岗位成功失败/暂停/继续/取消/阻断检查写入 task_logs
- [X] 2.9 失败测试: `test_pending_results_actually_written`
- [X] 2.10 失败测试: `test_pending_count_reflects_reality`
- [X] 2.11 失败测试: `test_checkpoint_saved_on_pause`
- [X] 2.12 失败测试: `test_continue_skips_checkpoint_keys`
- [X] 2.13 失败测试: `test_task_events_recorded` (FR-038)

## 切片 3：统一错误分类

- [X] 3.1 pipeline_exec.py: 扩展 `ERROR_TAXONOMY` 覆盖 13 类错误（含 impact/blocking/retryable/reason/resume_condition）
- [X] 3.2 ai.py: 区分 ai_rate_limited(429) / ai_quota_exhausted(402) / ai_key_invalid(401) / ai_network_error(超时)
- [X] 3.3 source.py: 新增 `job_offline` / `detail_timeout` 分类
- [X] 3.4 app.py: 所有失败岗位写入具体 failed_code + 用户可读原因，禁止仅用"待确认"/"未抓到 JD"
- [X] 3.5 定义 `SYSTEMIC_BLOCK_CODES` 和 `INDEPENDENT_FAILURE_CODES` 集合
- [X] 3.6 失败测试: `test_ai_rate_limit_classified_as_systemic_block`
- [X] 3.7 失败测试: `test_job_offline_is_independent_failure`
- [X] 3.8 失败测试: `test_no_bare_uncertain_without_reason`

## 切片 4：列表抓取暂停/继续

- [X] 4.1 app.py: 列表抓取 hard_stop 时写 `screening_runs.status=paused` + error_code + checkpoint(completed_combos)
- [X] 4.2 app.py: `/api/execute-search/continue` 允许 paused 状态调用，从 checkpoint 恢复 completed_combos
- [X] 4.3 app.py: 继续前检查阻断条件是否解除（FR-020，scrape 阶段）
- [X] 4.4 失败测试: `test_scrape_pause_persists_to_db`
- [X] 4.5 失败测试: `test_scrape_continue_restores_combos`
- [X] 4.6 失败测试: `test_scrape_continue_checks_block_resolved` (FR-020)

## 切片 5：JD 抓取暂停/继续与短 JD

- [X] 5.1 app.py: JD 抓取 hard_stop 时保存 checkpoint(已抓 JD 的 job_id 列表)；继续时跳过
- [X] 5.2 app.py: JD 继续前检查阻断条件是否解除（FR-020，jd_detail 阶段）
- [X] 5.3 boss_cdp_raw.py: 删除 `MIN_DETAIL_TEXT_LENGTH=120` 硬截断
- [X] 5.4 boss_cdp_raw.py: 保留登录墙/导航壳/风控检测；新增"剩余内容是否为真实 JD 段落"判断（含岗位职责/要求/技能等语义标记）
- [X] 5.5 boss_cdp_raw.py: 30/80/119 字真实短 JD 必须通过
- [X] 5.6 失败测试: `test_jd_pause_checkpoint_saved`
- [X] 5.7 失败测试: `test_jd_continue_checks_block_resolved` (FR-020)
- [X] 5.8 失败测试: `test_short_jd_30_chars_accepted_if_real`
- [X] 5.9 失败测试: `test_short_jd_80_chars_accepted_if_real`
- [X] 5.10 失败测试: `test_short_jd_119_chars_accepted_if_real`
- [X] 5.11 失败测试: `test_login_wall_still_rejected`

## 切片 6：AI 粗筛/精筛暂停/继续

- [X] 6.1 app.py: AI 调用命中限流/额度耗尽/密钥失效时立即暂停整个 run；保存 checkpoint(已判定 job_id 列表)
- [X] 6.2 app.py: 继续时从 checkpoint 跳过已判定岗位；AI 漏回单岗位标独立失败
- [X] 6.3 app.py: AI 粗筛限流时暂停（不默认全部保留并继续）
- [X] 6.4 app.py: AI 粗筛/精筛继续前检查阻断条件是否解除（FR-020，ai_rough/ai_fine 阶段）
- [X] 6.5 失败测试: `test_ai_rough_filter_rate_limit_pauses`
- [X] 6.6 失败测试: `test_ai_fine_filter_rate_limit_pauses`
- [X] 6.7 失败测试: `test_ai_continue_skips_done_verdicts`
- [X] 6.8 失败测试: `test_ai_missing_single_job_marked_independent`
- [X] 6.9 失败测试: `test_ai_continue_checks_block_resolved` (FR-020)

## 切片 7：页面进度、暂停原因和继续操作

- [X] 7.1 app.py: 新增 `/api/task-state/<run_id>` 统一状态接口（返回 status/stage/progress/error_code/error_reason/success/fail/unstarted/total/pause_info）
- [X] 7.2 app.py: 新增 `/api/task/continue/<run_id>` 统一继续接口（不再分抓取/AI/重抓三条路径）
- [X] 7.3 app.py: 新增 `/api/task/cancel/<run_id>` 取消接口（FR-024）
- [X] 7.4 DiscoveryView.vue: 3 个 snapshot 统一从后端 `/api/task-state/<run_id>` 拉取
- [X] 7.5 DiscoveryView.vue: 继续按钮统一调 `/api/task/continue/<run_id>`；取消按钮调 `/api/task/cancel/<run_id>`
- [X] 7.6 TaskProgress.vue: 显示当前阶段、成功数、失败数、未开始数、总数、具体暂停原因
- [X] 7.7 失败测试: `test_task_state_api_returns_complete_picture`
- [X] 7.8 失败测试: `test_continue_api_unified_for_all_stages`
- [X] 7.9 失败测试: `test_cancel_api_preserves_results` (FR-024)
- [X] 7.10 失败测试: `test_pause_reason_specific_not_generic`
- [X] 7.11 失败测试: `test_concurrent_continue_rejected` (FR-022)

## 切片 8：批量与单条补救

- [X] 8.1 app.py: `/api/pipeline/recrawl` 只取 screening_pending_results 中的岗位（不再取 verdict="uncertain"）
- [X] 8.2 app.py: 单条补抓新增暂停机制（命中阻断时暂停，保存 checkpoint）
- [X] 8.3 app.py: 重抓"继续"从 checkpoint 恢复（不再重发全部 uncertain）
- [X] 8.4 app.py: 补救成功后更新 screening_pending_results 状态 + screening_results 分类
- [X] 8.5 app.py: 重复点击继续或重抓不产生重复后台工作（FR-022）
- [X] 8.6 失败测试: `test_recrawl_only_processes_pending_table`
- [X] 8.7 失败测试: `test_single_retry_has_pause_capability`
- [X] 8.8 失败测试: `test_recrawl_continue_from_checkpoint_not_resend`
- [X] 8.9 失败测试: `test_recrawl_success_updates_classification`
- [X] 8.10 失败测试: `test_single_retry_does_not_change_other_jobs`
- [X] 8.11 失败测试: `test_duplicate_continue_no_concurrent_work` (FR-022)

## 切片 9：页面刷新、服务重启和版本标识

- [X] 9.1 app.py: `/api/latest-running-task` 扩展返回 paused 状态任务（从 DB 读取，不仅限内存）
- [X] 9.2 app.py: 新增 `/api/version` 接口返回 `{backend_version, build_hash, build_time}`
- [X] 9.3 app.py: 启动时在 screening_runs 写入 backend_version；继续任务时校验版本一致
- [X] 9.4 DiscoveryView.vue: 页面加载时拉取 `/api/latest-running-task` 恢复 paused 任务；页脚显示版本
- [X] 9.5 DiscoveryView.vue: 版本不匹配时提示用户刷新
- [X] 9.6 失败测试: `test_paused_task_recovered_after_refresh`
- [X] 9.7 失败测试: `test_paused_task_recovered_after_restart`
- [X] 9.8 失败测试: `test_version_api_returns_hash`
- [X] 9.9 失败测试: `test_old_service_cannot_process_new_tasks`

## 切片 10：历史恢复工具和只读预演

- [X] 10.1 新增 `webui/historical_recovery.py`: `preview_recovery(rough_run_id, fine_run_id)` 只读预演
- [X] 10.2 historical_recovery.py: 15847d27 的 50 条识别（17 match + 33 not_match，纯字符串 verdict）
- [X] 10.3 historical_recovery.py: e6250f0e 的 50 条识别（uncertain AI 超时）
- [X] 10.4 historical_recovery.py: 646 条识别（15847d27 kept-uncertain 未进 e6250f0e，不猜测 30/8/608）
- [X] 10.5 historical_recovery.py: 762 条 JD 文件存在性核对（pipeline_detail_*.json）
- [X] 10.6 app.py: 新增 `/api/recovery/preview/<run_id>` 只读接口
- [X] 10.7 失败测试: `test_preview_15847d27_50_split_17_33`
- [X] 10.8 失败测试: `test_preview_e6250f0e_50_uncertain`
- [X] 10.9 失败测试: `test_preview_646_identified_not_split_30_8_608`
- [X] 10.10 失败测试: `test_preview_762_jd_files_exist`
- [X] 10.11 失败测试: `test_preview_conservation_check`
- [X] 10.12 失败测试: `test_preview_does_not_write`

## 切片 11：集成验收与正式恢复

- [X] 11.1 app.py: 新增 `/api/recovery/execute/<run_id>` 写入接口（门禁：备份+预演数字+测试+小规模验收+版本）
- [X] 11.2 app.py: 恢复动作 1 — 15847d27 的 50 条格式统一为枚举 verdict（不调 AI）
- [X] 11.3 app.py: 恢复动作 2 — e6250f0e 的 50 条 uncertain 标记待重新判定
- [X] 11.4 app.py: 恢复动作 3 — 762 条 JD 从 pipeline_detail 文件回填到 screening_results.jd
- [X] 11.5 app.py: 恢复动作 4 — 646 条写入 screening_pending_results 交给新流程
- [X] 11.6 app.py: 恢复后核对总数守恒、无重复、无丢失
- [X] 11.7 失败测试: `test_recovery_gate_blocks_if_backup_missing`
- [X] 11.8 失败测试: `test_recovery_gate_blocks_if_numbers_mismatch`
- [X] 11.9 失败测试: `test_recovery_fixes_15847d27_50_without_ai_call`
- [X] 11.10 失败测试: `test_recovery_backfills_762_jd_from_files`
- [X] 11.11 失败测试: `test_recovery_moves_646_to_pending_table`
- [X] 11.12 失败测试: `test_recovery_marks_e6250f0e_50_for_rejudge`
- [X] 11.13 失败测试: `test_recovery_conservation_final`
- [X] 11.14 SC-006 端到端: 第 800/1,408 触发验证码 → 762/38/608 + paused + captcha_required
- [X] 11.15 SC-015 真实渲染复验: 仅替换隔离 5050 服务后，在 375/768/1440 三种视口核对暂停原因、进度、继续操作和待确认原因

## Phase 12: Convergence

- [X] T001 将主流程独立失败原子写入 `screening_pending_results`，让批量补救绑定并持久化 `source_run_id`，只接受真实 pending 岗位，并修正 uncertain/pending 的最终统计与 `finalize_run_status` 守恒公式 per FR-025~FR-031, FR-034~FR-037
- [X] T002 将单条补抓改为具有 task_id、持久化状态、checkpoint、具体失败原因和继续能力的补救任务，系统性阻断时保持浏览器并暂停 per FR-028~FR-029, US4/AC2-AC5
- [X] T003 为 scrape、JD、AI 和 recrawl 的继续操作增加阶段化阻断解除复核；复核失败写 `block_check` 事件并保持 paused，不提交后台工作 per FR-020
- [X] T004 修复历史恢复 HTTP 链路：增加 prepare 路由，execute 路由只接收 `backup_id` 并调用 `execute_recovery(backup_id, store=store)`；同步 API contract 和路由级测试 per FR-041~FR-047, plan: slices 10-11
- [X] T005 让 `/api/task/continue/<run_id>` 实际分派 scrape、AI、recrawl 三类恢复，前端统一使用该接口；旧继续路由仅作兼容转发并共享同一防并发实现 per plan: slice 7 unified continue decision
- [X] T006 在阶段开始/完成、岗位成功/失败、暂停、继续、取消和阻断复核处写入结构化 task events，并增加完整事件序列测试 per FR-038
- [X] T007 为所有状态变更请求增加前后端构建身份校验，使旧后端拒绝新页面发起的任务 per FR-040, SC-014
- [X] T008 正式恢复与元数据补正已在此前明确授权下执行，并已只读核对 17/33/50/646/762、总数守恒、零重复和零丢失；本轮禁止重跑 per US6, SC-011~SC-013

## Phase 13: Final Review Gates

- [X] F001 针对 10 条双轴审查意见完成 RED→GREEN；该门禁当时健康流程 132 项与历史恢复 57 项通过，最终窄修复新增 5 项后健康流程累计 137 项通过
- [X] F002 执行 Python 全仓、Vitest 全量、production build、相关 Python `py_compile` 与 `git diff --check`
- [X] F003 仅替换 5050 服务，确认 5000/5051 未监听，并完成 375/768/1440 三视口真实 SC-015
- [X] F004 按 2026-07-28 验收修订，以 6 项确定性测试验证暂停状态与 checkpoint 持久化、真实应用重启、刷新恢复、canonical 任务身份、手动继续零重复及重启状态迁移；6/6 通过。原 24 小时静态数据库轮询退役，不声称取得过该墙钟证据
- [X] F005 记录最终审查收口决策：既有至少 5 轮独立审查后，最后 4 项 finding 已完成 5 个 RED→GREEN 回归并通过健康流程 137/137、直接影响 73/73、Python 全仓 761/761；用户按收益边界递减原则明确豁免第 6 次全量审查，不将该豁免伪装为 reviewer PASS
