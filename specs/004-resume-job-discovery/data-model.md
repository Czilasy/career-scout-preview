# Data Model: 简历驱动的岗位发现

**Feature**: 004-resume-job-discovery  
**Date**: 2026-07-14  
**Migration strategy**: additive migrations 011–013; no legacy table rename or destructive backfill

## Design rules

1. Analysis, confirmation and evaluation inputs are immutable versions.
2. Candidate facts, AI inference, user confirmation and unknowns remain distinguishable.
3. Canonical jobs are reusable, but every run evaluates an immutable detail snapshot.
4. Every resumable unit has a persisted state, attempt count, failure code and input identity.
5. Raw model output, full prompts and credentials are not persisted.
6. Resume deletion removes resume-derived evidence and explanations while preserving canonical jobs and explicit user feedback.

## Existing entities retained

| Existing entity | Use in 004 |
|---|---|
| `candidate_profiles` | Owner of resumes, constraints, feedback and discovery history. |
| `resumes` | Source file and extracted text; parent of immutable analyses. |
| `ai_settings` | Existing provider URL/status; API key remains in keyring. |
| `jobs` | Canonical long-term job identity and latest known display fields. |
| `profile_jobs` | Existing interested/deleted/applied state where compatible. |
| `feedback_events` | Existing exact-job interest feedback remains valid. |
| `screening_trash_records` | Existing durable trash records remain visible. |
| `search_runs`, `screening_runs` | Legacy history only; not converted into discovery runs. |

## Migration 011: Candidate analysis

### `candidate_analyses`

One immutable analysis attempt for one resume.

| Field | Meaning / validation |
|---|---|
| `id` | Stable identifier. |
| `resume_id` | Required existing resume; cascade-delete with resume. |
| `profile_id` | Required owner profile. |
| `version` | Positive sequence unique within resume. |
| `status` | `queued`, `analyzing`, `ready`, `failed`, `deleted`. |
| `summary_json` | Sanitized candidate summary; no contact identifiers or full resume. |
| `unknowns_json` | Items the resume cannot establish, such as current city intention. |
| `model_name` | Provider model label used for audit, if available. |
| `contract_version` | Candidate-analysis contract version. |
| `failure_code` | Safe code only; null unless failed. |
| `created_at`, `completed_at` | Lifecycle timestamps. |

Validation:

- Only `ready` analyses may be confirmed.
- `(resume_id, version)` is unique.
- A failed analysis has no default-enabled directions.
- Candidate-analysis v2 derives one canonical analysis text from the immutable resume extraction using a versioned normalization rule. Evidence locators are relative to that canonical text, and the resume content hash plus contract version identify the input interpretation without persisting a duplicate full-text copy.
- A queued/analyzing attempt that cannot construct or call the configured provider becomes `failed` with a feature-safe failure code; it is never promoted to `ready` from partial output.

### `resume_evidence`

Normalized facts or bounded inferences extracted from a resume.

| Field | Meaning / validation |
|---|---|
| `id` | Stable evidence identifier. |
| `analysis_id` | Required parent analysis; cascade-delete. |
| `evidence_type` | `skill`, `responsibility`, `project`, `industry`, `seniority`, `education`, `achievement`, `other`. |
| `normalized_value` | Short normalized value used for comparison. |
| `safe_excerpt` | Minimal sanitized excerpt; never full resume text. |
| `source_locator_json` | Page/paragraph/character locator when extraction supports it. |
| `assertion_type` | `explicit` or `inferred`; unknowns are stored on analysis, not fabricated as evidence. |
| `confidence` | Number 0–100; explicit values still require a source locator. |
| `sensitive` | Must be false for evidence usable in direction/matching. |
| `created_at` | Creation timestamp. |

Validation:

- Direction and explanation references must resolve to evidence in the same analysis.
- Sensitive evidence is excluded from AI direction and job-evaluation inputs.
- Duplicate normalized evidence may be merged while retaining multiple source locators.
- For explicit evidence, the locator is program-generated from a unique exact source quote. `canonical_text[start:end]` must equal the accepted unsanitized quote before the stored excerpt is redacted/minimized.
- A quote with zero matches, multiple unresolved matches, out-of-range bounds or a mismatching resolved slice is not persisted as evidence and cannot support a default-enabled direction.
- Character offsets use Unicode code-point indexes into canonical text, not UTF-8 byte positions. Canonicalization changes require a new contract/analysis version.

### `career_directions`

An immutable direction proposal within an analysis.

| Field | Meaning / validation |
|---|---|
| `id` | Stable direction identifier. |
| `analysis_id` | Required parent analysis. |
| `name` | Canonical user-facing direction name. |
| `direction_type` | `core`, `adjacent`, `growth`. |
| `rationale` | Sanitized evidence-backed explanation. |
| `gaps_json` | Bounded capability gaps; may be empty for a core direction. |
| `confidence` | Number 0–100. |
| `default_enabled` | True only when evidence and confidence gates pass. |
| `search_terms_json` | 1–3 normalized candidate terms. |
| `contract_version` | Direction contract version. |
| `created_at` | Creation timestamp. |

Validation:

- Names are unique per analysis after normalization.
- No more than five directions are default-visible; insufficient evidence is never padded.
- Every default-enabled direction has at least one evidence link.

### `direction_evidence`

Many-to-many evidence link.

| Field | Meaning / validation |
|---|---|
| `direction_id`, `evidence_id` | Composite primary key; both must belong to the same analysis. |
| `role` | `primary` or `supporting`. |

## Migration 012: Confirmation and discovery execution

### `direction_confirmations`

Immutable snapshot of current user intention.

| Field | Meaning / validation |
|---|---|
| `id` | Stable confirmation identifier. |
| `profile_id`, `resume_id`, `analysis_id` | Required and mutually consistent. |
| `version` | Positive profile-local sequence. |
| `hard_constraints_json` | Only explicit user constraints; empty values are omitted. |
| `soft_preferences_json` | Ranking preferences and weights. |
| `safe_limits_json` | Approved query/page/detail limits. |
| `confirmed_at` | User confirmation time. |

Runtime adjustment (not persisted in confirmation row, computed by `apply_feedback_to_next_run`):

| Field | Meaning / validation |
|---|---|
| `excluded_job_ids` | Job IDs excluded by `not_interested` feedback; applied to the confirmation view before starting a run. Not stored in the confirmation row itself. |

Validation:

- At least one direction must be enabled.
- Historical rows are never updated; an edit creates a new version.
- City/salary absent from user confirmation do not become hard constraints.

### `confirmation_directions`

| Field | Meaning / validation |
|---|---|
| `confirmation_id`, `direction_id` | Composite primary key. |
| `enabled` | Only enabled rows are compiled into a plan. |
| `user_added` | Distinguishes a user-created direction from AI proposal. |
| `user_label` | Optional user label for a user-created direction. |

### `discovery_runs`

Parent state machine for one end-to-end discovery.

| Field | Meaning / validation |
|---|---|
| `id` | Stable run identifier. |
| `profile_id`, `resume_id`, `analysis_id`, `confirmation_id` | Immutable input references. |
| `status` | `created`, `planning`, `fetching_lists`, `fetching_details`, `evaluating`, `assembling`, `succeeded`, `partial`, `failed`, `interrupted`, `cancelled`. |
| `stage` | Current user-visible stage. |
| `policy_version` | Evaluation policy used by all assessments. |
| `input_hash` | Hash of confirmation, safe limits and policy version. |
| `source_count`, `detail_count`, `evaluated_count` | Monotonic progress counters. |
| `high_count`, `adjacent_count`, `growth_count`, `review_count`, `unsuitable_count` | Result counters. |
| `cancel_requested_at` | Null unless cancellation requested. |
| `failure_code`, `failure_stage` | Safe terminal/partial failure metadata. |
| `created_at`, `started_at`, `updated_at`, `completed_at` | Lifecycle timestamps. |

Validation:

- Input references and `input_hash` never change after creation.
- Terminal states cannot return to running; resume creates a guarded transition from `interrupted`/eligible `partial` into the saved stage.
- `succeeded` requires all required work units terminal and no unresolved blocker.

### `discovery_run_events`

Append-only state and audit events.

| Field | Meaning / validation |
|---|---|
| `id`, `run_id`, `sequence` | Ordered event identity; sequence unique per run. |
| `event_type` | Stage/status/progress/retry/cancel/partial event. |
| `safe_payload_json` | Counts, IDs and safe codes only. |
| `created_at` | Event time. |

Required runtime-closure events include submission accepted, dispatch failed, stage entered, cancellation requested, work unit skipped after cancellation, resume accepted and resume rejected. Payloads contain IDs/counts/safe codes only.

### `search_plans`

| Field | Meaning / validation |
|---|---|
| `id`, `run_id` | One active plan per run. |
| `plan_version` | Compiler version. |
| `status` | `draft`, `ready`, `running`, `completed`, `partial`, `failed`. |
| `item_count`, `detail_budget` | Bounded totals. |
| `created_at`, `completed_at` | Lifecycle timestamps. |

### `search_plan_items`

One deduplicated source query with direction provenance and checkpoint.

| Field | Meaning / validation |
|---|---|
| `id`, `plan_id` | Identity and parent. |
| `keyword`, `city` | Executable query values. |
| `source_filters_json` | Confirmed source-compatible filters only. |
| `direction_ids_json` | One or more originating enabled directions. |
| `input_hash` | Hash over keyword, city, filters, confirmation and compiler version. |
| `status` | `queued`, `fetching`, `completed`, `failed`, `cancelled`. |
| `page_cursor`, `target_pages` | Persisted page-level resume position. |
| `detail_budget` | Allocated share of global detail budget. |
| `attempt_count`, `failure_code` | Retry metadata. |
| `created_at`, `updated_at`, `completed_at` | Lifecycle timestamps. |

Validation:

- `input_hash` is unique within a plan after query deduplication.
- Resume/import is rejected if artifact input hash differs.
- Every enabled direction receives at least one plan item before extra allocation.

## Migration 013: Snapshots, assessments and structured feedback

### `discovery_job_snapshots`

The exact job detail used by one run.

| Field | Meaning / validation |
|---|---|
| `id`, `run_id`, `job_id` | Unique `(run_id, job_id)`. |
| `source_url` | Validated HTTPS BOSS URL. |
| `title`, `company`, `salary`, `location`, `tags` | Display and hard-rule fields at fetch time. |
| `jd` | Sanitized detail text required for semantic evaluation. |
| `company_json` | Scale/stage/industry fields when available. |
| `completeness` | `complete`, `partial`, `unavailable`, `expired`. |
| `missing_fields_json` | Explicit missing/parse-failure fields. |
| `source_status` | `active`, `unknown`, `closed`, `unreachable`. |
| `content_hash` | Reproducibility and duplicate-update check. |
| `fetch_status` | `queued`, `fetching`, `completed`, `failed`, `cancelled`. |
| `attempt_count`, `failure_code` | Per-job failure isolation and retry. |
| `fetched_at`, `updated_at` | Snapshot timestamps. |

### `job_direction_assessments`

| Field | Meaning / validation |
|---|---|
| `id`, `run_id`, `snapshot_id`, `direction_id` | Unique `(run_id, snapshot_id, direction_id)`. |
| `status` | `queued`, `evaluating`, `completed`, `pending`, `failed`. |
| `hard_outcome` | `pass`, `violation`, `unknown`. |
| `hard_checks_json` | Sanitized per-field outcomes; no resume text. |
| `dimensions_json` | Validated numeric dimensions and evidence reference IDs. |
| `match_score`, `confidence` | Nullable validated 0–100 values. |
| `category` | `high_match`, `adjacent_match`, `growth_match`, `needs_review`, `not_suitable`. |
| `candidate_evidence_ids_json` | Only IDs from the run's analysis. |
| `job_evidence_json` | Safe JD excerpts/field locators from this snapshot. |
| `gaps_json` | Validated user-facing gaps. |
| `policy_version`, `contract_version` | Reproducibility. |
| `failure_code`, `attempt_count` | Pending/retry metadata. |
| `created_at`, `updated_at`, `completed_at` | Lifecycle timestamps. |

Validation precedence:

1. `hard_outcome=violation` forces `not_suitable`.
2. `hard_outcome=unknown`, incomplete required input or invalid AI output prevents `high_match` and normally produces `needs_review`.
3. Evidence references outside the analysis/snapshot invalidate AI output.
4. Category is calculated by program policy, not accepted directly from AI.
5. The provider input view contains the sanitized analysis summary, selected direction metadata and only evidence linked to that direction. `candidate_evidence_ids_json` is a subset of those supplied IDs; `job_evidence_json` uses only supplied snapshot field keys/excerpts.
6. Authentication, timeout, network, invalid-output, uncertainty and evidence-reference failures remain distinguishable in `failure_code`. A failed provider call never stores default scores.

### `discovery_feedback`

| Field | Meaning / validation |
|---|---|
| `id`, `profile_id` | Feedback identity and owner. |
| `run_id`, `job_id`, `direction_id` | Nullable target references according to target type. |
| `target_type` | `job`, `direction`, `assessment`, `constraint`. |
| `action` | `interested`, `not_interested`, `direction_disable`, `direction_enable`, `assessment_wrong`, `constraint_wrong`, `restore`. |
| `reason_code` | Controlled reason vocabulary. |
| `scope` | `exact_job` by default; wider scopes require explicit user action. |
| `safe_note` | Optional sanitized user note. |
| `created_at`, `revoked_at` | Effective lifecycle. |

## Relationships

```text
candidate_profiles
└── resumes
    └── candidate_analyses
        ├── resume_evidence
        ├── career_directions ── direction_evidence ── resume_evidence
        └── direction_confirmations
            ├── confirmation_directions ── career_directions
            └── discovery_runs
                ├── discovery_run_events
                ├── search_plans
                │   └── search_plan_items
                └── discovery_job_snapshots ── jobs
                    └── job_direction_assessments ── career_directions

candidate_profiles
└── discovery_feedback ── jobs / career_directions / job_direction_assessments
```

## Deletion and retention

- Deleting a resume deletes analyses, evidence, AI directions and evidence links.
- Confirmations/runs referencing a deleted resume retain safe operational metadata and job identities but lose evidence-backed explanation payloads; UI marks explanations unavailable.
- Temporary run snapshots and assessments follow the existing 30-day cleanup policy unless the job is protected by durable interest/trash state.
- Canonical `jobs`, exact-job interest/trash state and explicit feedback are not deleted by temporary cleanup.
- Revocation marks feedback ineffective; it does not rewrite historical run snapshots.

## Legacy mapping

| Legacy state | New display behavior |
|---|---|
| `screening_results.match` | Historical “旧筛选：符合”; not reclassified as `high_match`. |
| `screening_results.mismatch` | Historical “旧筛选：不符合”. |
| `screening_pending_results` | Historical pending with existing retry/manual behavior. |
| `profile_jobs.interested` | New persistent interested view. |
| legacy deleted/trash record | New persistent trash view. |
| `search_runs` | Historical search run; no synthetic directions or assessments. |

## Migration verification

1. Upgrade a schema-version-10 database to 13.
2. Verify all old table row counts and representative values are unchanged.
3. Verify migrations are idempotent on reopen.
4. Verify foreign keys and uniqueness constraints reject cross-analysis evidence links and duplicate snapshots.
5. Verify running discovery work converges to `interrupted` on restart while checkpoints remain resumable.
6. Verify resume deletion removes derived evidence/explanations but preserves protected jobs and feedback.
