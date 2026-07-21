# Data Model: 快速简历驱动岗位推荐收口

**Feature**: `005-fast-resume-discovery`  
**Date**: 2026-07-20  
**Migration strategy**: additive migration 015；不重命名、不删除、不回填改写 001–004 业务事实

## Design Rules

1. AI analysis、用户修改、用户确认和发现运行输入是不同版本，不能互相覆盖。
2. 候选人事实、用户当前意愿、AI 推断和未知项必须可区分。
3. 列表候选池是恢复和详情优先决策的持久化事实，临时 JSON 文件不是状态真值。
4. 每个详情、评估和推荐变化都有稳定岗位身份、input hash、状态和安全失败码。
5. 进度计数从持久化 work units 派生或事务内更新，不使用进程局部计数冒充事实。
6. 推荐是 snapshots + assessments + feedback + policy 的 canonical projection，不复制成容易漂移的第二套事实。
7. 旧运行保持 policy v1；005 新运行使用 policy v2，不重新分类历史结果。
8. Raw prompt、raw model output、API key、完整简历正文和敏感字段不进入新增表。

## Existing Entities Retained

| Existing entity | 005 use |
|---|---|
| `candidate_profiles` | 用户级 owner；保留旧 confirmed fields/AI preference 兼容，不作为 005 画像版本真值。 |
| `resumes` | 原始简历及提取正文；父级删除继续触发隐私清理。 |
| `candidate_analyses` | AI 分析 attempt；v4 分析仍是 immutable attempt。 |
| `resume_evidence` | 可验证简历证据；v4 fact 通过 join 引用。 |
| `career_directions` / `direction_evidence` | AI 或用户方向及证据；继续按 analysis 版本保存。 |
| `direction_confirmations` | 扩展为 profile version + typed intent 的不可变确认快照。 |
| `discovery_runs` / `discovery_run_events` | 扩展 policy v2 stages、progress、revision 和 timing。 |
| `search_plans` / `search_plan_items` | 保存查询级计划与 checkpoint；`source_count` 继续表示查询数。 |
| `jobs` | canonical job identity。 |
| `discovery_job_snapshots` | run-specific 详情输入；增加 reuse/freshness/timing。 |
| `job_direction_assessments` | 每岗位每方向的 program-validated 评估；增加 v2 group/input identity/timing。 |
| `discovery_feedback` | 继续保存岗位、方向、评估和约束反馈。 |

## Migration 015

### `candidate_profile_versions`

一份可编辑 draft 或已冻结的完整候选人画像版本。

| Field | Meaning / validation |
|---|---|
| `id` | 稳定 UUID。 |
| `profile_id` | 必需 owner；引用 `candidate_profiles`。 |
| `resume_id` | 产生该版本的 resume；用户纯手工版本仍要求关联当前 resume。 |
| `analysis_id` | 基础 AI analysis，可为空仅限人工恢复场景。 |
| `version` | profile-local 正整数，`(profile_id, version)` 唯一。 |
| `status` | `draft`, `confirmed`, `superseded`, `deleted`。 |
| `summary_json` | 脱敏 headline/experience/domains/strengths；不含事实详情。 |
| `unknowns_json` | 仍需用户确认的 typed unknowns。 |
| `contract_version` | `candidate_profile_v1`。 |
| `content_hash` | facts + summary + unknowns 的稳定 hash；不含 timestamps。 |
| `created_at`, `updated_at` | draft 生命周期。 |
| `confirmed_at` | confirmed 后非空。 |
| `supersedes_version_id` | 可空；指向上一个 confirmed/draft version。 |

Validation:

- 只有 `draft` 可修改。
- confirmed version 的 facts、summary、unknowns、content_hash 不可更新。
- 修改 confirmed version 必须复制为新 draft；旧 version 不变。
- resume/analysis/profile 必须互相一致。
- 删除 resume 后 version 状态变 `deleted`，summary/unknowns 清空；run 仅保留安全 identity。

### `candidate_fact_items`

画像版本中的一个结构化事实或有边界的推断。

| Field | Meaning / validation |
|---|---|
| `id` | 稳定 UUID。 |
| `profile_version_id` | 必需 parent；cascade-delete draft/deleted cleanup。 |
| `fact_type` | `work`, `project`, `skill`, `industry`, `education`, `achievement`, `seniority`。 |
| `stable_key` | 版本内稳定键；同 version 唯一，用于 UI PATCH 和 diff。 |
| `value_json` | 对应 fact type 的 bounded typed value。 |
| `normalized_value` | 短字符串，用于搜索、比较和展示；不替代 typed value。 |
| `source_kind` | `resume_explicit`, `resume_inferred`, `user_added`, `user_corrected`。 |
| `assertion_type` | `explicit`, `inferred`。用户明确补充为 explicit。 |
| `confidence` | 0–100；用户确认事实保存 100，但不伪造 resume evidence。 |
| `verification_status` | `extracted`, `confirmed`, `corrected`, `rejected`, `unknown`。 |
| `supersedes_fact_id` | 用户纠正时引用原 fact；可空。 |
| `created_at`, `updated_at` | 生命周期。 |

Typed `value_json` shapes:

- `work`: employer, title, start_date, end_date/current, responsibilities[], achievements[], industry。
- `project`: name, role, start_date, end_date, responsibilities[], technologies[], outcomes[]。
- `skill`: name, usage_years, last_used, level, contexts[]；未明确字段保持 null。
- `industry`: name, duration_years, contexts[]。
- `education`: school, degree, major, start_date, end_date。
- `achievement`: statement, metric, context。
- `seniority`: level, management_scope, years；未确认 management 不补造。

Validation:

- provider 列表和字符串均有 contract 上限；超限字段单独 quarantine。
- `user_corrected` 必须有 `supersedes_fact_id` 或明确新增理由。
- rejected fact 不进入 confirmation、priority 或 AI assessment 输入。
- inferred fact 默认需要用户确认，不能单独形成硬约束。
- value 不得包含 phone/email/id/address pattern。

### `candidate_fact_evidence`

| Field | Meaning / validation |
|---|---|
| `fact_id`, `evidence_id` | 复合主键；必须属于同一 resume/analysis lineage。 |
| `role` | `primary`, `supporting`。 |

Validation:

- `resume_explicit` 和 `resume_inferred` facts 至少一个 evidence link；用户新增事实可以没有 resume evidence。
- fact 引用的 evidence 必须属于基础 analysis 且 `sensitive=false`。

### `direction_confirmations` additive fields

| New field | Meaning / validation |
|---|---|
| `candidate_profile_version_id` | 005 必需，旧 rows 可 null。 |
| `intent_contract_version` | 005 为 `intent_v2`，旧 rows 为 `v1`/null。 |
| `intent_hash` | profile version + hard + soft + dirs + safe limits 的稳定 hash。 |

`hard_constraints_json` v2 canonical shape:

```json
{
  "city": "上海",
  "min_salary": {
    "amount": 20,
    "currency": "CNY",
    "pay_period": "month",
    "unit": "K",
    "semantics": "monthly_floor",
    "source": "user_confirmed"
  },
  "excluded_directions": [],
  "work_modes": [],
  "other": []
}
```

Validation:

- `min_salary.amount` > 0 且只接受 user-confirmed source。
- 旧 `salary` source code 与 v2 `min_salary` 并存但不互相转换。
- user intent 缺失时省略字段，不写 0/空值作为硬约束。
- confirmation 只能引用 confirmed profile version。

### `discovery_run_candidates`

一个 run 中一个 canonical job 的列表候选、预检、详情选择和渐进工作状态。

| Field | Meaning / validation |
|---|---|
| `id` | 稳定 UUID。 |
| `run_id`, `job_id` | `UNIQUE(run_id, job_id)`；job 必须已进入 canonical jobs。 |
| `source_url` | validated BOSS HTTPS canonical URL。 |
| `direction_ids_json` | 一个或多个启用方向；并集合并。 |
| `search_terms_json` | 命中的计划 search terms；安全短值。 |
| `source_positions_json` | query item/page/rank provenance；不含页面正文。 |
| `list_fields_json` | title/company/salary/location/tags 等 bounded list view。 |
| `dedupe_key` | canonical URL/job id identity hash。 |
| `precheck_outcome` | `pass`, `violation`, `unknown`。 |
| `precheck_json` | 每项硬规则结果。 |
| `priority_components_json` | direction relevance、completeness、soft preference、coverage 信息。 |
| `selection_decision` | `pending`, `selected`, `deferred`, `excluded`, `blocked`。 |
| `selection_reason` | safe enum，如 `hard_violation`, `budget_deferred`, `invalid_source`, `feedback_excluded`。 |
| `selection_rank` | selected 内从 1 开始；同 run 唯一。 |
| `state` | 见 work-unit 状态机。 |
| `snapshot_id` | 当前 run snapshot；详情完成后非空。 |
| `attempt_count`, `failure_code` | unit retry metadata。 |
| `input_hash` | confirmation + job identity + selection policy v2。 |
| `discovered_at`, `selected_at`, `updated_at`, `completed_at` | timing。 |

Validation:

- 重复列表结果只合并 direction/search/source provenance，不重复创建 job/candidate。
- precheck violation 强制 excluded，不得 selected。
- precheck unknown 可 selected，但最终不能因 unknown 进入 high_match。
- 至少为每个存在 eligible candidates 的 enabled direction 分配 floor，预算不足时按 direction confidence + stable id 决定。
- 所有 state transitions 使用 expected-state compare-and-set。

### `discovery_runs` additive fields

| New field | Meaning |
|---|---|
| `candidate_profile_version_id` | 冻结的画像版本；旧 run 可 null。 |
| `list_candidate_count` | 去重 candidate 数，不是 query 数。 |
| `detail_selected_count` | 本 run selected 详情数。 |
| `detail_completed_count` | complete/partial/unavailable terminal snapshot units。 |
| `assessment_completed_count` | terminal assessment rows。 |
| `recommendation_count` | 当前 projector 中推荐类别岗位数。 |
| `detail_reused_count` | 从新鲜历史详情复制的数量。 |
| `ai_call_count` | 已发起 job assessment provider 请求数。 |
| `result_revision` | 每次可见 recommendation snapshot 变化时单调 +1。 |
| `first_result_at`, `first_batch_at` | 首个结果、首批 5 结果时间。 |
| `list_completed_at`, `processing_completed_at` | 性能阶段边界。 |

Compatibility:

- `source_count` 继续表示完成的 search plan query 数，不能改义。
- 旧 `detail_count/evaluated_count/category counts` 保留，v2 adapter 同步为兼容 aliases。
- policy v1 run 不要求新 counters 回填。

### `discovery_job_snapshots` additive fields

| New field | Meaning |
|---|---|
| `run_candidate_id` | 产生 snapshot 的 run candidate。 |
| `reused_from_snapshot_id` | 复用来源；新抓取为 null。 |
| `fresh_until` | 允许复用的上限，默认 fetched_at + 12h。 |
| `fetch_duration_ms` | 当前 unit 端到端详情耗时。 |
| `wait_duration_ms` | readiness/gap 等来源等待时间。 |
| `fetch_policy_version` | `detail_v2`。 |

Existing `fetched_at` must be written for both fresh and reused snapshot. Reused snapshot keeps original source fetch time separately in safe metadata and gets its own run snapshot content hash.

### `job_direction_assessments` additive fields

| New field | Meaning |
|---|---|
| `evaluation_group_id` | 同一次 job-assessment v2 请求的最多两个方向共享。 |
| `input_hash` | profile version + direction + evidence + snapshot content + contract/policy。 |
| `evaluation_duration_ms` | group 或方向 validated processing duration。 |
| `ai_call_count` | 此 group 发起 provider 调用数（首个方向记录 group count，其他为 0）。 |
| `result_revision` | 第一次进入用户结果 projector 的 run revision。 |

Validation:

- 唯一 `(run_id, snapshot_id, direction_id)` 保持不变。
- resume 时同 input_hash + completed 直接跳过。
- v2 group 一个方向无效时，该方向 needs_review；其他合法方向不回滚。

## Program-owned Projections

### `RecommendationItem`

不建表；每次从当前持久化事实生成。

Fields:

- recommendation_id = `{run_id}:{job_id}`
- run_id, job_id, snapshot_id
- title, company, salary, location, tags
- jd / bounded jd_excerpt
- source_url, source_status, fetched_at, reused
- primary_assessment
- assessments[]（所有有效方向）
- matched_direction_ids[]
- category, match_score, confidence, completeness
- soft_preference_score
- stable_rank, sort_components
- explanation {positive[], gaps[], candidate_refs[], job_refs[]}
- interest_state
- visible, removal_reason

Canonical sort tuple:

1. category priority: high, adjacent, growth, review, unsuitable；
2. match score descending；
3. confidence descending；
4. completeness complete before partial/unavailable；
5. soft preference score descending；
6. canonical job id ascending。

Projection guards:

- hard violation always unsuitable。
- hard unknown cannot high_match。
- high_match requires complete detail and two-sided evidence。
- growth requires at least one explicit gap。
- feedback `not_interested` hides from default but remains in trash/history。
- direction filter selects jobs with any matching assessment and still returns all assessments。

## State Machines

### Candidate profile version

```text
draft ──confirm──> confirmed ──new edit──> superseded
  │                     │
  └────delete───────────┴───────────────> deleted
```

### Policy v2 run

```text
created → planning → fetching_lists → prioritizing → processing_jobs → assembling
    │          │              │              │               │
    ├──────────┴──────────────┴──────────────┴───────────────┼→ interrupted
    ├─────────────────────────────────────────────────────────┼→ cancelled
    └─────────────────────────────────────────────────────────┴→ failed

assembling → succeeded | partial | failed
```

`fetching_details` and `evaluating` remain valid v1 stages. A v2 resume from either legacy stage is adapted to `processing_jobs` only after input identity validation.

### Run candidate work unit

```text
discovered
  → precheck_pass | precheck_unknown | excluded
  → selected | deferred
selected
  → detail_fetching
  → detail_ready | detail_failed | cancelled
detail_ready
  → evaluating
  → recommended | needs_review | unsuitable | evaluation_failed
recommended | needs_review | unsuitable
  → reordered | withdrawn
```

- `withdrawn` requires source closed, hard-rule change in a new run, or explicit user feedback；历史 run assessment 不删除。
- cancellation only changes nonterminal units；terminal units remain terminal。
- resume requeues selected/detail_failed/evaluation_failed retryable units only；completed input_hash units skip。

## Progress Derivation

| User label | Source of truth |
|---|---|
| 列表候选 | count of `discovery_run_candidates` for run |
| 已选详情 | selection_decision = selected |
| 已完成详情 | candidate state at/after detail_ready, detail_failed terminal or cancelled terminal |
| 已完成评估 | terminal assessments for run |
| 推荐结果 | current canonical projection categories high/adjacent/growth |

Run counters are updated in the same transaction as work-unit transition. Periodic reconciliation recalculates from rows and emits a safe correction event if mismatch is found.

## Detail Reuse

A previous snapshot may be reused only when:

1. canonical source URL/job identity matches；
2. previous completeness = complete；
3. source_status = active；
4. previous fetched_at is within 12 hours；
5. current list fields do not indicate title/company/salary/location identity drift；
6. user did not request refresh。

Reuse creates a new run snapshot with identical content hash, new `run_candidate_id`, `reused_from_snapshot_id`, current run timestamps and explicit `reused=true` projection metadata.

## Deletion and Retention

- 删除 resume 继续清理 analysis/evidence/directions；新增 profile versions/facts/fact evidence 同步清理或 tombstone。
- 历史 runs 保留安全 operational metadata、job identity、counts 和 failure codes；证据解释变 unavailable。
- Run candidates、snapshots、assessments follow existing 30-day temporary retention unless protected by interest/trash state。
- Reused snapshot chain 在 parent 清理前必须把当前 snapshot 内容保持自足；不能依赖 parent row 才能解释当前 run。
- Canonical jobs 和显式 feedback 按既有长期规则保留。

## Migration Verification

1. schema 14 → 15 upgrade，旧表 row counts/representative values 不变。
2. migration 015 reopen idempotent。
3. old confirmation/run 的 nullable profile version 可正常读取 v1 results。
4. confirmed profile version update 被拒绝；新 edit 创建更高 version。
5. cross-analysis candidate_fact_evidence 被拒绝。
6. duplicate `(run,job)` candidate 被合并，方向 provenance 保留。
7. candidate CAS transition 防止 detail/evaluation 重复提交。
8. interrupted processing_jobs resume 后 completed detail/assessment external calls = 0。
9. run counters 与 persisted units reconciliation 一致。
10. 删除 resume 清理 facts/explanations，但不删除 canonical job 和显式 feedback。

