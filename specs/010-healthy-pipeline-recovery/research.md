# Research: 健康流程补救与优化

**Date**: 2026-07-28
**Source**: 代码库只读研究（file:line 证据）+ 阶段 0 数据库基线

## R1: 当前执行流程编排

**Decision**: 四阶段编排分散在两个后台任务，不是单一状态机。

**Rationale**:
- 列表抓取：`_run_pipeline_task`（app.py:1859）→ `run_search`（pipeline_exec.py:390-549），逐个 keyword×city 组合调 `source.fetch_list`。
- AI 粗筛 → JD 详情 → AI 精筛：全部在 `_run_ai_screen_task`（app.py:1480）一个函数顺序执行，非独立可调度阶段。
- 阶段间数据：列表用 `merged: dict[str,dict]`（pipeline_exec.py:460）以 job_id 去重；AI 阶段通过 `enriched=[dict(job) for job in survivors]`（app.py:1520）共享，原地写 `job["jd"]` 和 `job["verdict"]`。

**Alternatives considered**: 保持两任务结构——被否，因为 paused 状态在两套系统间不对齐。

## R2: 当前状态机

**Decision**: 存在两套不对齐的状态系统，需统一到 screening_runs。

**Rationale**:
- 内存态：`_pipeline_tasks[task_id]`（app.py:1869），status 取值 `queued/running/done/failed/cancelled/paused`。
- DB 态 tasks 表：`ALLOWED_TRANSITIONS`（store.py:33-40），状态 `queued/running/succeeded/failed/interrupted/partial`，无 `paused`。
- DB 态 screening_runs 表：`RUN_TRANSITIONS`（store.py:43-50），`RUN_STATUSES={queued,running,succeeded,partial,failed,interrupted}`（store.py:42），**无 `paused`**。但 app.py:1573, 2538 实际写入 `status="paused"`，违反状态机校验。`update_screening_run` 注释（store.py:2038）承认"宽松更新，不做状态机校验"。

**Alternatives considered**: 扩展 tasks 表——被否，因为新 pipeline 流程不写 tasks 表，只用 screening_runs。

## R3: 当前错误分类

**Decision**: 有基础分类但缺失 AI 限流/额度耗尽/岗位下架/详情超时等关键类别。

**Rationale**:
- 分类入口 `_classify_failed_code`（source.py:1165-1186）：returncode + stderr 关键词 → `source_cdp_unavailable/source_login_required/source_verification_required/source_rate_limited/source_blocked`。
- 硬停止码 `_HARD_STOP_CODES`（pipeline_exec.py:606-611）：`{source_login_required, source_verification_required, source_rate_limited, source_blocked}`。
- 用户可读标签 `_FAILED_CODE_LABELS`（pipeline_exec.py:100-108）。
- **缺失**：AI 限流/额度耗尽/密钥失效统一进 `except Exception`（app.py:1699+）；岗位下架/详情超时归到 `source_blocked` 兜底；错误码散落 3 处（内存 task["error"]、screening_runs.error_code、岗位级 jd_failed_code）。

**Alternatives considered**: 仅扩展现有 _FAILED_CODE_LABELS——被否，因为需要结构化属性（影响范围/是否阻断/可重试/继续条件），不只是标签。

## R4: 当前暂停/继续机制

**Decision**: 抓取和重抓的 paused 不持久化；重抓"继续"重发全部 uncertain。

**Rationale**:
- 抓取继续：`/api/execute-search/continue`（app.py:1862-1891），仅允许 `failed/cancelled`，**不允许 paused**。completed_combos 仅内存。
- AI 筛选继续：`/api/ai-screen`（app.py:1954+）检测 `resume_from_run_id`，跳过已抓 JD（app.py:1522）和已得判定（app.py:1610）。
- 重抓继续：`_run_recrawl_task` hard_stop 时 `task["status"]="paused"`（app.py:2538）仅内存；前端"继续"调 `recrawlUncertain()`（DiscoveryView.vue:1189）重发全部。
- JD checkpoint 文件 `_jd_checkpoint_path`（app.py:1521）存在但列表 completed_combos 不落盘。
- 服务重启后：`/api/latest-running-task`（app.py:2045-2087）只返回 running/queued 内存任务，paused 不被接回（仅 interrupted 的 DB 记录返回，且只对 AI 筛选有效）。

**Alternatives considered**: 保留三条分叉路径——被否，因为违反 FR-022（防并发重复）和 SC-004（零重复）。

## R5: screening_pending_results 从未写入

**Decision**: 启用该表记录待确认岗位。

**Rationale**:
- 表结构存在（migration_005，store.py:482-496），字段含 `failure_stage/retryable/attempts/origin_zone/ai_payload_json`。
- **全仓库无 `INSERT INTO screening_pending_results` 调用**。
- 646 条无 JD 岗位直接在 app.py:1644-1652 标 `verdict="uncertain"` + `verdict_reason="未抓到 JD（{label}）"`，通过 `save_pipeline_result` 落到 screening_results。
- `pending_count` 字段自 migration_007 建出后**永远 0**（无任何 `update_screening_run(pending_count=N)` 调用）。

**Alternatives considered**: 在 screening_results 增加 pending 标记——被否，因为已有专用表且更符合范式。

## R6: 696 异常的数据库实证（2026-07-28 重新核对修正）

**Decision**: 事故证据的"50 条 17 match + 33 mismatch"与 e6250f0e run 的 50 条 uncertain 是两回事，原 R6 把两个 run 搞混。696 = 646（未处理）+ 50（e6250f0e uncertain AI 超时）。

**Rationale**:

事故跨两个 run（均 source_count=1926）：
- `15847d27-...`（17:21，列表+粗筛阶段）：total_dropped=518, total_kept=1408, match_count=198, mismatch_count=1210
- `e6250f0e...`（14:58，JD+精筛阶段）：processed_count=762, match_count=198, mismatch_count=514, pending_count=0, status=done

**15847d27 run screening_results（1926 条，纯字符串 verdict 非 JSON）**：
| verdict 值 | 数量 | 说明 |
|---|---|---|
| `match` | 17 | 有效 AI 判定，部分有 JD |
| `not_match` | 33 | 有效 AI 判定，部分有 JD |
| `uncertain` | 1876 | parse 失败（非 JSON），含 518 dropped + 1358 kept 未精筛 |

**e6250f0e run screening_results（762 条，全部 JSON verdict，jd_len 全部=0）**：
| JSON inner verdict | 数量 | 说明 |
|---|---|---|
| `match` | 198 | 有效 AI 判定，JD 不在 DB（在 job-result 文件） |
| `not_match` | 514 | 有效 AI 判定，JD 不在 DB |
| `uncertain` | 50 | AI 响应超时，无有效判定，reason="AI 响应超时，请稍后重试，待人工确认" |

**守恒核对**：
- 1926 = 518(dropped) + 1408(kept) ✓
- 1408 = 762(精筛处理) + 646(未进精筛) ✓
- 762 = 198(match) + 514(not_match) + 50(uncertain) ✓
- 696 = 646(未进精筛，在 15847d27 作 uncertain) + 50(e6250f0e uncertain AI 超时) ✓

**事故证据"50 条已有 AI 判定但结构错误，17 match + 33 mismatch"真相**：
- 指的是 **15847d27 run** 的 50 条（17 match + 33 not_match），verdict 是纯字符串非 JSON，被前端当作"结构异常"
- 这 50 条**有有效 AI 判定**，恢复时只需统一格式（纯字符串 → 枚举/JSON），**不重新调 AI**
- **不是** e6250f0e 的 50 条 uncertain（那是 AI 超时，无有效判定，需重新调 AI）

**646 条无 JD 岗位**：
- 在 15847d27 screening_results 中作为 uncertain（1358 条 kept-uncertain 中的 646 条未进精筛）
- **不在** e6250f0e screening_results（只有 762 条）
- **不在** screening_pending_results（0 条）
- 岗位列表可从 job-result 的 `pipeline_*_*.json` 列表文件恢复（157 个列表文件，每个含 jobs 数组）

**e6250f0e 的 762 条 jd_len 全部=0**：
- JD 正文不在数据库 screening_results.jd 字段
- JD 在 job-result 的 `pipeline_detail_*.json` 文件中（337 个 detail 文件，对应成功抓取的 JD）
- 恢复时需从文件回填 JD 到 DB，或保留文件引用

| 指标 | 事故证据 | 数据库实测 | 一致性 |
|---|---|---|---|
| 原始岗位 | 1,926 | source_count=1926 | ✓ |
| 成功取得 JD | 762 | processed_count=762, screening_results=762 | ✓ |
| 50 条 17+33 | 15847d27 | 15847d27: 17 match + 33 not_match（纯字符串）| ✓ |
| e6250f0e uncertain | - | 50 条 uncertain（AI 超时）| 新发现 |
| run.pending_count | 应 696 | 0 | 伪装完成 |
| run.status | 应 paused | done | 伪装完成 |
| screening_pending_results | 应 696 | 0 | 重大缺口 |
| JD 正文 | 应在 DB | e6250f0e 全部 jd_len=0 | JD 只在文件 |

**Alternatives considered**: 假设 646 条数字准确直接恢复——被否，FR-041 要求只读预演核对，数字不一致必须阻断。

## R7: 短 JD 硬截断

**Decision**: 删除 MIN_DETAIL_TEXT_LENGTH 硬截断，改为内容真实性判断。

**Rationale**:
- `MIN_DETAIL_TEXT_LENGTH = 120`（boss_cdp_raw.py:476）。
- `extract_job_description`（boss_cdp_raw.py:600-645）在 line 641-644 硬截断：`if len(jd) < min_length: raise DetailExtractionError`。
- 已有真实性检查（line 613-622）：登录墙（`DETAIL_LOGIN_MARKER`）、导航壳（`_looks_like_navigation_page`）、风控（`looks_like_risk_control`）。
- **缺口**：无"剩余内容是否为真实 JD 段落"判断。30/80/119 字真实短 JD 会被一刀切拒绝。

**Alternatives considered**: 降低门槛到 30——被否，FR-033 要求按内容真实性判断，不是换一个更低的固定值。

## R8: 版本标识完全未实现

**Decision**: 从零搭建版本标识。

**Rationale**:
- 前端：grep `version|build|VERSION` 在 webui/src/ 下无版本常量、无页脚版本显示。
- 后端：grep `version|/api/version|__version__` 在 app.py 无版本接口。
- FR-039/FR-040/SC-014 完全未实现。

**Alternatives considered**: 仅前端显示 package.json 版本——被否，FR-040 要求阻止旧服务处理新任务，需后端版本校验。

## R9: 前端状态管理

**Decision**: 3 个独立 snapshot 统一为后端驱动的单一状态源。

**Rationale**:
- 3 个 `ref<TaskSnapshot>`：scrapeSnapshot/screenSnapshot/recrawlSnapshot（DiscoveryView.vue:95-102）。
- 轮询 `pollTask`（DiscoveryView.vue:593-668）1.8s 一次，指数退避 7 次/64s。
- 继续按钮分叉：抓取仅对 failed 显示（line 1111）；AI 对 paused 显示（line 1166）；重抓对 paused 显示但调 recrawlUncertain 重发（line 1189）。
- TaskProgress.vue 平滑动画已实现（displayPercent + requestAnimationFrame + 8% 卡顿，line 140-158）。

**Alternatives considered**: 保持 3 snapshot——被否，因为暂停信息不持久化、继续逻辑分叉。

## R10: 补救流程现状

**Decision**: 全部重抓复用主流程函数但"继续"重发全部；单条补抓无暂停。

**Rationale**:
- 全部重抓：`_run_recrawl_task`（app.py:2422-2627）从 `load_latest_pipeline_result` 取 jobs，筛 `not jd` 调 `fetch_job_details`，有 JD 调 `match_jds`，原地回写 screening_results。
- 单条补抓：`/api/pipeline/jobs/<id>/jd`（app.py:2350+）调 `boss.fetch_job_detail` + 可选单条 `match_jds`。
- **缺口**：重抓"继续"重发全部 uncertain（违反 FR-022/SC-004）；单条补抓无暂停（违反 FR-029）。
