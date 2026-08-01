# Data Model: 高级设置深度自动调优

**Date**: 2026-07-29

## 1. Value Objects

### 1.1 ExecutionConfigSnapshot

一次普通任务、实验候选或运行中配置段落使用的不可变设置。

| Field | Type | Rule |
|---|---|---|
| `inter_combo_delay` | decimal seconds | `>= 0`; only between combinations |
| `detail_batch_size` | integer | `>= 1`; cannot exceed remaining detail items for an issued round |
| `detail_interval` | decimal seconds | `>= 0`; actual randomized range is recorded separately |
| `detail_reset_every` | integer | `>= 1`; applies per detail worker/session |
| `detail_batch_cooldown` | decimal seconds | `>= 0` |
| `detail_tab_pool_size` | integer | `1..5`; concurrent resident browser tabs for JD fetching |
| `screen_batch_size` | integer | `>= 1`; cannot exceed actual rough-screen input without normalization |
| `screen_concurrency` | integer | `>= 1`; effective concurrency cannot exceed batch count |
| `match_batch_size` | integer | `>= 1`; cannot exceed actual fine-screen input without normalization |
| `match_concurrency` | integer | `>= 1`; effective concurrency cannot exceed batch count |
| `schema_version` | integer | positive, required |
| `config_digest` | string | digest of canonical field/value JSON |

`pages` is not part of this value object. Randomization policy, retry policy and source safety rules are also excluded.

### 1.2 FrozenTaskScope

| Field | Type | Rule |
|---|---|---|
| `keywords` | ordered string list | normalized, non-empty, exact duplicates removed |
| `scope_kind` | enum | `cities` or `nationwide` |
| `cities` | ordered canonical city list | non-empty only for `cities`; each currently enabled |
| `pages_per_combination` | integer | positive; frozen after task start |
| `combination_count` | integer | keyword count × effective search-scope count |
| `planned_pages` | integer | combination count × pages per combination |
| `task_size` | enum | `small` 1-9, `medium` 10-49, `large` 50-200 |
| `scope_digest` | string | digest of canonical scope JSON |

`nationwide` has an effective search-scope count of 1 and cannot coexist with cities.

### 1.3 MeasurementSummary

| Field | Type | Rule |
|---|---|---|
| `total_duration_ms` | integer | includes work, waits, cooldowns, retries and recovery |
| `stage_durations_ms` | stage-to-integer map | all executed stages represented |
| `work_duration_ms` | integer | measured active work |
| `wait_duration_ms` | integer | controlled gaps and cooldowns |
| `retry_duration_ms` | integer | backoff and repeated attempts |
| `attempt_count` | integer | all external attempts |
| `retry_count` | integer | attempts after first |
| `error_counts` | code-to-count map | no free-form-only error |
| `input_count` | integer | exact frozen input count |
| `terminal_count` | integer | unique terminal items |
| `success_count` | integer | successful terminal items |
| `failed_count` | integer | failed terminal items |
| `missing_count` | integer | must be 0 for a valid final candidate |
| `duplicate_count` | integer | must be 0 |
| `quality_diff_count` | integer | item-level differences from reference |
| `artifact_digest` | string | digest over required evidence manifest |

## 2. Persistent Entities

### 2.1 tuning_experiments

Parent record for one complete deep test.

| Field | Type | Description |
|---|---|---|
| `id` | TEXT PK | experiment identity |
| `spec_version` | TEXT | fixed as feature/runbook version |
| `status` | TEXT | experiment state machine |
| `input_version_id` | TEXT FK | frozen workload set |
| `quality_reference_id` | TEXT FK nullable | active reference version |
| `baseline_config_json` | TEXT | newly qualified low-pressure baseline |
| `baseline_config_digest` | TEXT | canonical digest |
| `current_stage` | TEXT | list/detail/rough/fine/end-to-end |
| `current_candidate_id` | TEXT nullable | candidate under consideration |
| `estimated_remaining_seconds` | INTEGER nullable | updated after accepted reports |
| `blocked_code` | TEXT nullable | structured blocker |
| `blocked_reason` | TEXT nullable | readable reason |
| `created_at`, `updated_at` | TEXT | timestamps |
| `completed_at` | TEXT nullable | final completion time |

Only one experiment may own the execution lease. Incomplete experiments cannot produce an applicable mode version.

### 2.2 tuning_input_versions

Frozen set of representative workloads.

| Field | Type | Description |
|---|---|---|
| `id` | TEXT PK | version identity |
| `experiment_id` | TEXT FK | parent experiment |
| `scope_json` | TEXT | user-confirmed source keywords/cities |
| `scope_digest` | TEXT | canonical digest |
| `quality_context_json` | TEXT | user-confirmed profile and screening context |
| `quality_context_digest` | TEXT | canonical SHA-256 digest |
| `status` | TEXT | draft/confirmed/invalidated |
| `confirmed_at` | TEXT nullable | confirmation time |
| `created_at` | TEXT | timestamp |

### 2.3 tuning_workloads

One small/medium/large representative structure.

| Field | Type | Description |
|---|---|---|
| `id` | TEXT PK | workload identity |
| `input_version_id` | TEXT FK | frozen input version |
| `task_size` | TEXT | small/medium/large |
| `structure_index` | INTEGER | at least 1 and 2 per size |
| `frozen_scope_json` | TEXT | FrozenTaskScope |
| `planned_pages` | INTEGER | 1-200 |
| `expected_raw_jobs` | INTEGER | estimate only; not used to reclassify |
| `artifact_manifest_json` | TEXT | reusable frozen stage inputs |
| `artifact_digest` | TEXT | digest of artifacts |
| `status` | TEXT | pending/ready/insufficient/invalidated |

Unique constraint: `(input_version_id, task_size, structure_index)`.

### 2.4 tuning_stage_artifacts

Append-only results produced by successful list/detail/rough/fine/end-to-end
rounds. They are never written back into the immutable input manifest.

| Field | Type | Description |
|---|---|---|
| `id` | TEXT PK | artifact identity |
| `experiment_id` | TEXT FK | exact parent experiment |
| `input_version_id` | TEXT FK | exact frozen input version |
| `workload_id` | TEXT FK | exact representative workload |
| `producer_round_id` | TEXT FK UNIQUE | round that produced the artifact |
| `stage` | TEXT | list/detail/rough/fine/end_to_end |
| `source_artifact_id` | TEXT FK nullable | exact reused upstream artifact |
| `artifact_path` | TEXT UNIQUE | experiment-relative immutable JSON path |
| `artifact_digest` | TEXT | SHA-256 of persisted bytes |
| `item_count` | INTEGER | jobs or verdicts count |
| `status` | TEXT | ready/invalidated |
| `created_at` | TEXT | timestamp |

`detail` and `rough` may reference only a matching ready `list` artifact;
`fine` may reference only a matching ready `detail` artifact. `list` and
`end_to_end` do not reuse a stage artifact.

### 2.5 tuning_quality_references

| Field | Type | Description |
|---|---|---|
| `id` | TEXT PK | reference version |
| `experiment_id` | TEXT FK | parent experiment |
| `input_version_id` | TEXT FK | exact input used |
| `status` | TEXT | building/review_required/confirmed/superseded |
| `item_results_json` | TEXT | canonical item-level reference |
| `variation_summary_json` | TEXT | repeated baseline variation |
| `reviewed_item_ids_json` | TEXT | user-resolved differences |
| `reference_digest` | TEXT | canonical digest |
| `created_at`, `confirmed_at` | TEXT | timestamps |

A candidate can only compare against the exact active reference digest stored in its manifest.

### 2.6 tuning_candidates

| Field | Type | Description |
|---|---|---|
| `id` | TEXT PK | candidate identity |
| `experiment_id` | TEXT FK | parent experiment |
| `stage` | TEXT | list/detail/rough/fine/combined/end_to_end |
| `strategy_step` | TEXT | single_field/combination/boundary/final_validation |
| `parent_candidate_id` | TEXT nullable | predecessor used to derive candidate |
| `config_json` | TEXT | full ExecutionConfigSnapshot, never a partial patch |
| `config_digest` | TEXT | canonical digest |
| `status` | TEXT | candidate state |
| `pressure_rank` | INTEGER | deterministic ordering within one direction |
| `promotion_reason` | TEXT nullable | controller decision evidence |
| `rejection_code` | TEXT nullable | slow/no_gain/hard_error/quality/unstable/invalid |
| `aggregate_metrics_json` | TEXT | median, tail and variation after accepted rounds |
| `created_at`, `updated_at` | TEXT | timestamps |

Identical config digests in the same experiment are reused rather than executed twice for the same workload/reference version.

### 2.7 tuning_rounds

One candidate execution on one workload and repetition number.

| Field | Type | Description |
|---|---|---|
| `id` | TEXT PK | round identity |
| `experiment_id` | TEXT FK | parent experiment |
| `candidate_id` | TEXT FK | candidate |
| `workload_id` | TEXT FK | frozen workload |
| `quality_reference_id` | TEXT FK nullable | comparison reference |
| `round_kind` | TEXT | list/detail/rough/fine/end_to_end |
| `repetition_index` | INTEGER | starts at 1 |
| `status` | TEXT | round state machine |
| `manifest_id` | TEXT FK | exact issued task |
| `source_run_id` | TEXT nullable | underlying pipeline run |
| `metrics_json` | TEXT nullable | MeasurementSummary |
| `evidence_manifest_json` | TEXT nullable | artifact references and digests |
| `failure_code` | TEXT nullable | structured invalid/block reason |
| `started_at`, `finished_at`, `confirmed_at` | TEXT nullable | timestamps |

Unique constraint: `(candidate_id, workload_id, round_kind, repetition_index)`.

### 2.8 tuning_task_manifests

| Field | Type | Description |
|---|---|---|
| `id` | TEXT PK | task identity shown to executor |
| `experiment_id`, `candidate_id`, `round_id` | TEXT FK | exact ownership |
| `manifest_version` | INTEGER | schema version |
| `manifest_json` | TEXT | complete executor contract |
| `manifest_digest` | TEXT | canonical digest |
| `rendered_task_path` | TEXT | path inside experiment artifact root |
| `status` | TEXT | draft/issued/running/reported/accepted/invalid/blocked |
| `issued_at`, `updated_at` | TEXT | timestamps |

Issued manifests are immutable. Correction creates a new manifest and invalidates the old one before execution.

### 2.9 tuning_executor_reports

| Field | Type | Description |
|---|---|---|
| `id` | TEXT PK | report identity |
| `manifest_id` | TEXT FK UNIQUE | one accepted report per issued task |
| `report_version` | INTEGER | schema version |
| `report_json` | TEXT | structured executor observations |
| `reported_manifest_digest` | TEXT | must equal issued digest |
| `evidence_digest` | TEXT | must equal program evidence digest |
| `validation_status` | TEXT | pending/accepted/invalid/blocked |
| `validation_errors_json` | TEXT | machine-readable issues |
| `created_at`, `validated_at` | TEXT | timestamps |

### 2.10 tuning_measurement_events

| Field | Type | Description |
|---|---|---|
| `round_id` | TEXT FK | owning round |
| `seq` | INTEGER | monotonic within round |
| `event_type` | TEXT | stage/batch/request/wait/retry/item terminal |
| `stage` | TEXT | execution stage |
| `started_monotonic_ms` | INTEGER | relative duration source |
| `duration_ms` | INTEGER | non-negative |
| `counts_json` | TEXT | safe counts only |
| `error_code` | TEXT nullable | structured error |
| `metadata_json` | TEXT | allowlisted safe metadata |
| `created_at` | TEXT | wall-clock timestamp |

Primary key: `(round_id, seq)`. Credentials, raw resume, raw model response and JD body are forbidden.

### 2.11 tuning_execution_lease

Singleton row coordinating exclusive work.

| Field | Type | Description |
|---|---|---|
| `id` | INTEGER PK | always 1 |
| `owner_experiment_id` | TEXT FK nullable | lease owner |
| `owner_round_id` | TEXT nullable | active round |
| `owner_token_digest` | TEXT nullable | claim identity |
| `lease_until` | TEXT nullable | bounded stale detection |
| `heartbeat_at` | TEXT nullable | latest heartbeat |
| `updated_at` | TEXT | timestamp |

Claim, heartbeat, release and stale takeover are transactional. Ordinary pipeline start must reject while an experiment owns the lease.

### 2.12 mode_config_versions

| Field | Type | Description |
|---|---|---|
| `id` | TEXT PK | complete version identity |
| `source_experiment_id` | TEXT FK nullable | null only for seeded defaults |
| `status` | TEXT | candidate/active/superseded |
| `matrix_json` | TEXT | 3 modes × 3 sizes; each slot contains/refers to full config |
| `manual_ranges_json` | TEXT | user-editable discovered range metadata |
| `version_digest` | TEXT | digest over matrix and ranges |
| `created_at`, `applied_at` | TEXT nullable | timestamps |

Exactly one active version. Applying a version atomically supersedes the previous active reference.

### 2.13 advanced_config_state

Singleton user setting state.

| Field | Type | Description |
|---|---|---|
| `id` | INTEGER PK | always 1 |
| `active_selection` | TEXT | stable/balanced/extreme/custom |
| `active_mode_version_id` | TEXT FK | active complete mode version |
| `last_custom_config_json` | TEXT | full latest custom config |
| `last_custom_digest` | TEXT | canonical digest |
| `legacy_imported_at` | TEXT nullable | one-time JSON migration marker |
| `updated_at` | TEXT | timestamp |

## 3. Relationships

```text
tuning_experiment
├── tuning_input_version
│   └── tuning_workloads (>= 2 per size)
├── tuning_quality_references
├── tuning_candidates
│   └── tuning_rounds
│       ├── tuning_task_manifest
│       │   └── tuning_executor_report
│       └── tuning_measurement_events
└── mode_config_version (created only after full validation)

advanced_config_state ──→ active mode_config_version
tuning_execution_lease ──→ at most one experiment/round
```

Existing `screening_runs` remains the authoritative child pipeline run. It stores the round's frozen scope and execution-config digest in `execution_params_json`; the experiment tables store parent control state.

## 4. State Machines

### 4.1 Experiment

```text
draft → preflight → awaiting_instruction → queued → running
          │               ↑               │       │
          └→ blocked ─────┘               │       ├→ evaluating → awaiting_instruction
                                          │       ├→ blocked → awaiting_instruction
                                          │       ├→ failed
                                          │       └→ cancelled
                                          └──────────────────→ completed
```

Rules:
- `completed` requires all required final rounds accepted and a complete candidate mode version.
- `cancelled`, `failed`, and `completed` are terminal.
- `blocked` retains checkpoints and requires explicit resume after conditions are satisfied.
- No transition to `running` without an issued manifest and acquired lease.

### 4.2 Candidate

```text
proposed → probing → promising → validating → accepted
    │          │          │           │
    └──────────┴──────────┴───────────┴→ rejected
                         └──────────────→ boundary
```

`boundary` is terminal and never eligible for a mode slot. `accepted` means eligible evidence, not automatically applied.

### 4.3 Round

```text
planned → issued → running → reported → confirmed
                      │          │
                      ├→ uncertain ─→ issued (new manifest/retry)
                      ├→ blocked
                      ├→ invalid
                      └→ cancelled
```

Only `confirmed` contributes to aggregate metrics. An `uncertain` round retains evidence but is not counted.

### 4.4 Manifest

```text
draft → issued → running → reported → accepted
   │       │         │         ├→ invalid
   └→ invalid        └→ blocked└→ blocked
```

## 5. Transaction Boundaries

1. **Issue round**: create round + immutable manifest + claim lease in one transaction.
2. **Confirm report**: validate manifest digest, program evidence digest, terminal counts and required fields; insert report, metrics and round `confirmed` in one transaction.
3. **Apply mode version**: create/validate full matrix if needed, supersede old active, activate new, update config state in one transaction.
4. **Resume after restart**: mark stale running round `uncertain`, preserve confirmed rounds, clear or reclaim lease in one transaction.
5. **Custom save**: validate full config and replace last custom value atomically; never patch individual fields in storage.

## 6. Invariants

- `terminal_count == input_count`, `missing_count == 0`, `duplicate_count == 0` for final candidate acceptance.
- Manifest config digest equals candidate config digest and child run config digest.
- Manifest input digest equals workload artifact digest and quality reference input digest.
- All direct candidate comparisons share workload, input and reference digests.
- One experiment lease, one active pressure round, one active mode version.
- No partial mode matrix can become active.
- No executor report can create or modify a candidate config.
- No experiment artifact path may escape the experiment artifact root.
- A task already started cannot change FrozenTaskScope.
- A runtime configuration change creates a new segment snapshot; it never mutates a prior segment.
