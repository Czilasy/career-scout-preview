# Implementation Plan: 健康流程补救与优化

**Branch**: `master` | **Date**: 2026-07-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/010-healthy-pipeline-recovery/spec.md`

## Summary

将当前 BOSS 求职流程（列表抓取 → JD 详情 → AI 粗筛 → AI 精筛）补救并优化为真实、透明、可暂停、可恢复、不重复工作、不伪装完成的健康流程。核心问题：当前流程存在两套不对齐的状态机、paused 状态不持久化、screening_pending_results 表从未被写入、pending_count 永远为 0、短 JD 硬截断 120 字、重抓"继续"重发全部 uncertain、无版本标识。历史 696 条异常结果需在新流程验收通过后安全恢复。

技术方案：统一任务状态权威到 screening_runs + 新增 pipeline_checkpoints 表；启用 screening_pending_results 表记录待确认岗位；引入统一错误分类码表；将 paused 状态合法化并持久化；主流程/批量补救/单条补救复用同一暂停继续规则；短 JD 改为内容真实性判断；新增版本标识接口与前端展示。

## Technical Context

**Language/Version**: Python 3.11（后端，uv cpython-3.11）、TypeScript + Vue 3.5（前端，Vite 8）

**Primary Dependencies**: Flask（后端）、Vue 3 + Vite + Vitest（前端）、requests + websocket-client（CDP 抓取）、sqlite3（存储）

**Storage**: SQLite（`~/.career-scout\webui\webui.db`），已有 37 张表；关键表：screening_runs、screening_results、screening_pending_results、tasks、task_logs

**Testing**: Python `unittest`（563 测试基线）、Vitest（8 测试基线）、`py_compile` 语法检查

**Target Platform**: Windows 本地单用户，Chrome CDP 远程调试

**Project Type**: Web 服务（Flask 后端 + Vue SPA 前端），本地运行

**Performance Goals**: 不以提高并发/批量/缩短等待为前提（FR-048）；1,926 条岗位全流程可暂停恢复

**Constraints**: 不删除原始数据；不回退用户未提交改动；不提交/推送；不降低验收标准；正式数据库写入前必须只读预演

**Scale/Scope**: 696 条历史异常恢复 + 全流程健康化改造；涉及 webui/app.py（~2600 行）、pipeline_exec.py、store.py、source.py、boss_cdp_raw.py、前端 3 个组件

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

项目无 `.specify/memory/constitution.md`，无既定宪法门禁。本计划以 spec.md 的 FR-001~FR-050 和 SC-001~SC-015 作为事实宪法，所有设计决策不得削弱这些要求。

**关键门禁**：
- 不得用"待确认"掩盖系统性阻断或未执行工作（FR-016, FR-036）
- 不得仅因字数判死短 JD（FR-032）
- 不得以提高并发作为健康流程前提（FR-048）
- 正式数据写入前必须只读预演（FR-041）
- 状态只能按合法路径变化（FR-005）

## Project Structure

### Documentation (this feature)

```text
specs/010-healthy-pipeline-recovery/
├── plan.md              # 本文件
├── research.md          # 代码库研究结论
├── data-model.md        # 状态机、错误分类、实体定义
├── quickstart.md        # 验证指南
├── contracts/           # 接口契约
│   └── api-contracts.md # 后端 API 契约
└── tasks.md             # 任务清单（speckit-tasks 生成）
```

### Source Code (repository root)

```text
webui/
├── app.py               # Flask 后端：路由、任务调度、暂停继续
├── pipeline_exec.py     # 流程编排：列表抓取、JD 抓取、错误分类
├── store.py             # SQLite 持久化：状态机、表读写
├── source.py            # CDP 抓取封装、失败码分类
├── ai.py                # AI 筛选调用
├── constants.py         # 常量定义
├── src/
│   ├── views/DiscoveryView.vue      # 主视图：任务控制、进度、暂停继续
│   ├── components/TaskProgress.vue  # 进度条组件
│   ├── components/JobWorkspace.vue  # 岗位结果展示
│   ├── discovery.ts                 # 前端状态管理、轮询
│   └── api.ts                       # API 调用封装
scripts/
└── boss_cdp_raw.py      # CDP 核心抓取：短 JD 判定（MIN_DETAIL_TEXT_LENGTH）
tests/
├── test_chrome_setup.py # Python 测试（含版本一致性）
└── test_pipeline_tasks_cleanup.py  # 流程清理测试
```

**Structure Decision**: 采用现有单仓结构，不新建目录。核心逻辑改动集中在 webui/ 后端 4 个文件 + 前端 3 个组件 + scripts/boss_cdp_raw.py 短 JD 判定。遵循 CONTRIBUTING.md 单文件原则，不随手建新文件。

## 实施切片划分

> 切片按 FULL_EXECUTION_PROMPT 第七节建议顺序，每个切片可独立验收。每个切片先写失败测试（RED），再实现（GREEN），再运行检查。

### 切片 1：状态与完成判定

**目标**：统一任务状态权威，定义合法状态转换，修正完成判定逻辑。

**改动范围**：
- `store.py`：将 `paused` 加入 `RUN_STATUSES` 和 `RUN_TRANSITIONS`；定义统一状态机 `TASK_STATUSES = {waiting, running, paused, completed, failed, cancelled}`；`update_screening_run` 加入状态机校验。
- `store.py`：新增 `finalize_run_status()` 函数——只有当 `processed_count + dropped_count == source_count` 且无未开始岗位时才允许 `completed`；存在 pending 但无系统性阻断时标 `completed_with_pending`；存在未开始或系统性阻断时标 `paused`。
- `app.py`：所有 `task["status"]` 内存字典写入同步落 DB。

**验收**：
- 失败测试：`test_finalize_status_no_unstarted_must_not_complete`
- 失败测试：`test_systemic_block_must_pause_not_complete`
- 失败测试：`test_all_processed_with_few_pending_completes_with_pending`
- 状态机非法迁移被拒绝

### 切片 2：持久化任务和岗位进度

**目标**：启用 screening_pending_results 表；修复 pending_count 更新；新增 pipeline_checkpoints 表保存断点。

**改动范围**：
- `store.py`：实现 `insert_pending_result(run_id, job_id, failure_stage, retryable, attempts, origin_zone, ai_payload_json)`、`update_pending_count(run_id)`。
- `store.py`：新增 `pipeline_checkpoints` 表（run_id, stage, completed_keys_json, saved_at），实现 `save_checkpoint(run_id, stage, keys)` 和 `load_checkpoint(run_id, stage)`。
- `app.py`：JD 抓取失败/未开始的岗位写入 screening_pending_results；每次暂停时保存 checkpoint；继续时加载 checkpoint 跳过已完成。

**验收**：
- 失败测试：`test_pending_results_actually_written`
- 失败测试：`test_pending_count_reflects_reality`
- 失败测试：`test_checkpoint_saved_on_pause`
- 失败测试：`test_continue_skips_checkpoint_keys`

### 切片 3：统一错误分类

**目标**：建立覆盖 12 类错误的统一分类码表，每类含影响范围/是否阻断/是否可重试/用户可读原因/继续条件。

**改动范围**：
- `pipeline_exec.py`：扩展 `ERROR_TAXONOMY` 字典，覆盖：验证码、登录失效、AI 限流、AI 额度耗尽、AI 密钥失效、AI 网络/服务故障、IP/整体风控、调试浏览器不可用、岗位下架、单岗位详情超时、详情结构无效、AI 漏回单岗位、内部状态错误。
- `ai.py`：区分 AI 限流（429）、额度耗尽（402/特定错误）、密钥失效（401）、网络故障（超时/连接错误）。
- `source.py`：新增 `job_offline`（岗位下架）、`detail_timeout`（单岗位超时）分类。
- `app.py`：所有失败岗位写入具体 failed_code + 用户可读原因，禁止仅用"待确认"/"未抓到 JD"。

**验收**：
- 失败测试：`test_ai_rate_limit_classified_as_systemic_block`
- 失败测试：`test_job_offline_is_independent_failure`
- 失败测试：`test_no_bare_uncertain_without_reason`

### 切片 4：列表抓取暂停/继续

**目标**：列表抓取命中系统性阻断时暂停并持久化；继续时从断点恢复。

**改动范围**：
- `app.py`：列表抓取 hard_stop 时写 `screening_runs.status=paused` + `error_code` + checkpoint（completed_combos）。
- `app.py`：`/api/execute-search/continue` 允许 paused 状态调用，从 checkpoint 恢复 completed_combos。
- `app.py`：继续前检查阻断条件是否解除（如验证码是否仍存在）。

**验收**：
- 失败测试：`test_scrape_pause_persists_to_db`
- 失败测试：`test_scrape_continue_restores_combos`
- 失败测试：`test_scrape_continue_checks_block_resolved`

### 切片 5：JD 抓取暂停/继续与短 JD

**目标**：JD 抓取暂停持久化、断点续抓；短 JD 改为内容真实性判断。

**改动范围**：
- `app.py`：JD 抓取 hard_stop 时保存 checkpoint（已抓 JD 的 job_id 列表）；继续时跳过。
- `boss_cdp_raw.py`：删除 `MIN_DETAIL_TEXT_LENGTH = 120` 硬截断；保留登录墙/导航壳/风控检测；新增"剩余内容是否为真实 JD 段落"判断（检查是否含岗位职责/要求/技能等语义标记）。
- `boss_cdp_raw.py`：30/80/119 字真实短 JD 必须通过。

**验收**：
- 失败测试：`test_jd_pause_checkpoint_saved`
- 失败测试：`test_short_jd_30_chars_accepted_if_real`
- 失败测试：`test_short_jd_80_chars_accepted_if_real`
- 失败测试：`test_short_jd_119_chars_accepted_if_real`
- 失败测试：`test_login_wall_still_rejected`

### 切片 6：AI 粗筛/精筛暂停/继续

**目标**：AI 粗筛和精筛命中限流/额度耗尽时暂停；继续时跳过已有判定。

**改动范围**：
- `app.py`：AI 调用命中限流/额度耗尽/密钥失效时立即暂停整个 run；保存 checkpoint（已判定 job_id 列表）。
- `app.py`：继续时从 checkpoint 跳过已判定岗位；AI 漏回单岗位标独立失败。
- `app.py`：AI 粗筛限流时暂停（不默认全部保留并继续）。

**验收**：
- 失败测试：`test_ai_rough_filter_rate_limit_pauses`
- 失败测试：`test_ai_fine_filter_rate_limit_pauses`
- 失败测试：`test_ai_continue_skips_done_verdicts`
- 失败测试：`test_ai_missing_single_job_marked_independent`

### 切片 7：页面进度、暂停原因和继续操作

**目标**：前端统一展示步骤、进度、具体原因、继续按钮；暂停信息持久化到后端。

**改动范围**：
- `DiscoveryView.vue`：3 个 snapshot 统一从后端 `/api/task-state/<run_id>` 拉取（含 status/stage/progress/error_code/error_reason/success_count/fail_count/unstarted_count/total）。
- `DiscoveryView.vue`：继续按钮统一调 `/api/task/continue/<run_id>`（不再分抓取/AI/重抓三条路径）。
- `TaskProgress.vue`：显示当前阶段、成功数、失败数、未开始数、总数、具体暂停原因。
- `app.py`：新增 `/api/task-state/<run_id>` 统一状态接口；新增 `/api/task/continue/<run_id>` 统一继续接口。

**验收**：
- 失败测试：`test_task_state_api_returns_complete_picture`
- 失败测试：`test_continue_api_unified_for_all_stages`
- 失败测试：`test_pause_reason_specific_not_generic`

### 切片 8：批量与单条补救

**目标**：全部重抓只处理待确认；单条补抓只处理选中岗位；补救复用同一暂停/继续/进度规则。

**改动范围**：
- `app.py`：`/api/pipeline/recrawl` 只取 screening_pending_results 中的岗位（不再取 verdict="uncertain"）。
- `app.py`：单条补抓新增暂停机制（命中阻断时暂停，保存 checkpoint）。
- `app.py`：重抓"继续"从 checkpoint 恢复（不再重发全部 uncertain）。
- `app.py`：补救成功后更新 screening_pending_results 状态 + screening_results 分类。

**验收**：
- 失败测试：`test_recrawl_only_processes_pending_table`
- 失败测试：`test_single_retry_has_pause_capability`
- 失败测试：`test_recrawl_continue_from_checkpoint_not_resend`
- 失败测试：`test_recrawl_success_updates_classification`

### 切片 9：页面刷新、服务重启和版本标识

**目标**：暂停任务在刷新/重启后恢复；新增版本标识。

**改动范围**：
- `app.py`：`/api/latest-running-task` 扩展返回 paused 状态任务（从 DB 读取，不仅限内存）。
- `app.py`：新增 `/api/version` 接口返回 `{backend_version, build_hash, build_time}`。
- `app.py`：启动时在 screening_runs 写入 backend_version；继续任务时校验版本一致。
- `DiscoveryView.vue`：页面加载时拉取 `/api/latest-running-task` 恢复 paused 任务；页脚显示版本。
- `DiscoveryView.vue`：版本不匹配时提示用户刷新。

**验收**：
- 失败测试：`test_paused_task_recovered_after_refresh`
- 失败测试：`test_paused_task_recovered_after_restart`
- 失败测试：`test_version_api_returns_hash`
- 失败测试：`test_old_service_cannot_process_new_tasks`

### 切片 10：历史恢复工具和只读预演

**目标**：实现只读预演脚本，核对 696 条异常的分类守恒（基于 2026-07-28 数据库实测修正）。

**改动范围**：
- 新增 `webui/historical_recovery.py`：只读预演函数 `preview_recovery(rough_run_id, fine_run_id)` 返回完整核对结果。
- `webui/historical_recovery.py`：15847d27 的 50 条识别——纯字符串 verdict `match`(17) / `not_match`(33)，有有效判定。
- `webui/historical_recovery.py`：e6250f0e 的 50 条识别——JSON verdict inner=`uncertain`(AI 超时)，无有效判定。
- `webui/historical_recovery.py`：646 条识别——15847d27 中 kept-uncertain 未进 e6250f0e 的岗位。**不猜测 30/8/608**；写入 `failed_code='historical_reason_unavailable'`、可确认的历史证据和 `recrawl_jd` 下一步，由新流程实时分类，禁止空原因。
- `webui/historical_recovery.py`：762 条 JD 回填核对——检查 pipeline_detail_*.json 文件存在性（337 个文件）。
- `app.py`：新增 `/api/recovery/preview/<run_id>` 只读接口。

**验收**：
- 失败测试：`test_preview_15847d27_50_split_17_33`
- 失败测试：`test_preview_e6250f0e_50_uncertain`
- 失败测试：`test_preview_646_identified_not_split_30_8_608`
- 失败测试：`test_preview_762_jd_files_exist`
- 失败测试：`test_preview_conservation_check`
- 失败测试：`test_preview_does_not_write`

### 切片 11：集成验收与正式恢复

**目标**：端到端集成验收（约 90 条 + SC-006/SC-015）；正式恢复 696 条（门禁通过后）。

**改动范围**：
- `app.py`：新增 `/api/recovery/execute/<run_id>` 写入接口（门禁：备份存在 + 预演数字一致 + 测试通过 + 小规模验收通过 + 版本确认）。
- `app.py`：恢复动作（基于修正后 data-model）：
  1. 15847d27 的 50 条（17 match + 33 not_match）格式统一为枚举 verdict（不调 AI）
  2. e6250f0e 的 50 条 uncertain 标记为待重新判定，交给新流程
  3. 762 条 JD 从 pipeline_detail_*.json 文件回填到 screening_results.jd
  4. 646 条从 15847d27 uncertain 中识别，写入 screening_pending_results，交给新流程
- `app.py`：恢复后核对总数守恒、无重复、无丢失。

**验收**：
- 失败测试：`test_recovery_gate_blocks_if_backup_missing`
- 失败测试：`test_recovery_gate_blocks_if_numbers_mismatch`
- 失败测试：`test_recovery_fixes_15847d27_50_without_ai_call`
- 失败测试：`test_recovery_backfills_762_jd_from_files`
- 失败测试：`test_recovery_moves_646_to_pending_table`
- 失败测试：`test_recovery_marks_e6250f0e_50_for_rejudge`
- 失败测试：`test_recovery_conservation_final`
- **SC-006 端到端**：第 800/1,408 触发验证码 → 762 成功、38 失败、608 未开始、状态 paused、pause_info.error_code="captcha_required"
- **SC-015 窄屏验收**：断点 375px + 768px 下暂停原因、进度、继续按钮完整可读（真实渲染）

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 新增 pipeline_checkpoints 表 | 暂停断点需持久化到 DB，服务重启后恢复 | 内存字典重启即丢，不符合 FR-023 |
| 新增 historical_recovery.py | 历史恢复是独立可审计动作，需预演/门禁/回滚 | 内联到 app.py 会让路由文件过长且无法独立审计 |
| 统一继续接口 /api/task/continue | 3 条路径分叉导致重抓"继续"重发全部 | 保持分叉会持续违反 FR-022 和 SC-004 |

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 用户未提交改动与计划改动冲突 | 只改 Spec 必需文件；改动前 git status 已记录；不回退用户改动 |
| 正式数据库写入破坏原始数据 | 阶段 0 已备份 + 哈希；阶段 5 写入门禁；回滚条件明确 |
| 646 条无 JD 岗位信息不足无法区分 30/8/608 | 预演先核对，数字不一致则阻断，不猜测补齐 |
| 前端改动后旧缓存生效 | dist 文件名带 hash；FR-039 版本标识辅助确认 |
| 服务重启时端口被旧进程占用 | 严格遵循五步重启流程 |

## 切片依赖关系

```
切片1(状态) ──→ 切片2(持久化) ──→ 切片3(错误分类)
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
              切片4(列表)        切片5(JD+短JD)     切片6(AI)
                    │                  │                  │
                    └──────────────────┼──────────────────┘
                                       ▼
                                切片7(前端统一)
                                       │
                              ┌────────┴────────┐
                              ▼                 ▼
                        切片8(补救)       切片9(刷新+版本)
                              │                 │
                              └────────┬────────┘
                                       ▼
                                切片10(预演)
                                       │
                                       ▼
                                切片11(集成+恢复)
```

切片 1-3 串行（状态机→持久化→错误分类依赖前置）；切片 4-6 可并行（各阶段暂停继续）；切片 7 依赖 4-6；切片 8-9 依赖 7；切片 10-11 串行收尾。
