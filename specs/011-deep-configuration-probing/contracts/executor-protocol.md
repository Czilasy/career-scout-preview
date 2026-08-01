# Executor Protocol Contract

**Feature**: 011 Deep Configuration Probing

**Date**: 2026-07-29

**Purpose**: Make each experiment executable by an AI that has no conversation history and no design authority.

## 1. Roles

### Controller

The controller alone may:

- choose the next candidate and all parameter values;
- choose workload, stage, repetition count and comparison reference;
- promote, reject or refine a candidate;
- change search step size;
- declare stable, balanced or extreme mode candidates;
- approve a corrected task after a block.

### Executor AI

The executor may only:

- read one issued task document;
- perform its ordered preflight checks;
- execute the exact commands/actions listed;
- monitor the exact task identity;
- stop, retry or block only when the task document defines that action;
- return one report in the required structure.

The executor must not infer a missing value or use chat history to fill a gap.

### Application

The application owns:

- configuration and input validation;
- task/round state;
- exclusive lease;
- objective measurements and evidence artifacts;
- terminal-count and digest checks;
- acceptance/rejection of executor reports.

## 2. Task Manifest Requirements

An issued manifest is immutable. Every field below is required unless explicitly marked nullable.

```json
{
  "schema_version": 1,
  "task_id": "tune-task-unique-id",
  "experiment_id": "tune-exp-unique-id",
  "candidate_id": "candidate-unique-id",
  "round_id": "round-unique-id",
  "spec_version": "011-deep-configuration-probing",
  "manifest_digest": "sha256-of-canonical-manifest-without-this-field",
  "objective": "One concrete sentence describing only this round",
  "round_kind": "list|detail|rough|fine|end_to_end",
  "strategy_step": "single_field|combination|boundary|final_validation",
  "repetition_index": 1,
  "preconditions": [],
  "frozen_input": {},
  "execution_config": {},
  "fixed_fields": {},
  "execution_steps": [],
  "monitoring": {},
  "retry_policy": {},
  "stop_conditions": [],
  "allowed_writes": [],
  "required_artifacts": [],
  "forbidden_actions": [],
  "report_contract": {},
  "issued_at": "RFC3339 timestamp"
}
```

### 2.1 Preconditions

Each entry must contain:

| Field | Meaning |
|---|---|
| `id` | stable check ID |
| `instruction` | exact action or command, with no placeholders |
| `expected` | exact success condition |
| `on_failure` | always `block_and_report` unless controller explicitly provides a safe retry |
| `evidence_field` | report field that records result |

Every real task must cover at least:

- active experiment and manifest identity;
- manifest digest verification;
- exclusive lease ownership;
- fixed input/reference digest availability;
- no conflicting ordinary or experiment task;
- required browser/login/AI readiness for the selected round kind;
- required artifact directory existence and write-boundary validation.

### 2.2 Frozen Input

```json
{
  "input_version_id": "...",
  "workload_id": "...",
  "task_size": "small|medium|large",
  "structure_index": 1,
  "scope_digest": "...",
  "artifact_manifest_path": "experiment-relative/path.json",
  "artifact_digest": "...",
  "quality_context_digest": "sha256-of-frozen-quality-context",
  "source_artifact_id": "required-for-detail-rough-fine-only",
  "source_artifact_path": "required-for-detail-rough-fine-only",
  "source_artifact_digest": "required-for-detail-rough-fine-only",
  "quality_reference_id": "nullable-for-non-quality-rounds",
  "quality_reference_digest": "nullable-for-non-quality-rounds",
  "expected_input_count": 0,
  "planned_pages": 0
}
```

The executor must not open a different input or replace a missing artifact.
`detail` and `rough` must reference one ready `list` artifact from the exact same
experiment, input version and workload. `fine` must reference one matching ready
`detail` artifact. `list` and `end_to_end` must omit all three `source_artifact_*`
fields so they always execute from the immutable base input. Both the base input
and source artifact bytes are SHA-256 verified before stage execution.

### 2.3 Exact Execution Config

Every manifest contains all speed fields, even when only one field changes:

```json
{
  "schema_version": 1,
  "inter_combo_delay": 0.0,
  "detail_batch_size": 1,
  "detail_interval": 0.0,
  "detail_reset_every": 1,
  "detail_batch_cooldown": 0.0,
  "detail_tab_pool_size": 1,
  "screen_batch_size": 1,
  "screen_concurrency": 1,
  "match_batch_size": 1,
  "match_concurrency": 1,
  "config_digest": "..."
}
```

The values above describe types only and are not executable defaults. A real issued manifest must contain controller-selected values and pass application validation.

### 2.4 Fixed Fields

The manifest repeats all non-tunable scope fields so the executor can verify them:

- ordered canonical keywords;
- scope kind and canonical cities/nationwide;
- pages per combination;
- expected planned pages and size class;
- candidate profile/reference identity;
- model and endpoint identity by safe reference, never credential;
- hidden safety-policy version;
- code/build identity.

### 2.5 Execution Steps

Each ordered step contains:

```json
{
  "seq": 1,
  "action": "exact action name",
  "instruction": "fully resolved instruction or command",
  "expected_status": "exact expected state/result",
  "timeout_seconds": 60,
  "on_timeout": "block_and_report|execute_named_retry",
  "named_retry": null,
  "evidence_field": "steps[0].evidence"
}
```

Requirements:

- No `<placeholder>`, “as appropriate”, “if needed”, “choose”, or equivalent discretionary language.
- A step cannot tell the executor to edit source code, alter acceptance criteria or select another candidate.
- The task identity returned by start must be used for all polling; “latest task” endpoints are forbidden.
- Polling interval, timeout and terminal states must be explicit.

### 2.6 Monitoring Contract

The manifest specifies:

- exact status endpoint/action and task identity;
- polling interval;
- maximum observation interval before block;
- expected stage sequence;
- counters that must be monotonic;
- hard error codes that stop immediately;
- recoverable error rule and exact allowed retry count;
- evidence snapshot interval;
- final artifact and program-measurement locations.

The executor records what the application reports; it does not estimate missing counters or durations.

### 2.7 Stop Conditions

Every stop condition has one unique action:

```json
{
  "code": "source_blocked",
  "match": "program error_code equals source_blocked",
  "severity": "hard",
  "action": "stop_new_work_and_block_report",
  "required_evidence": ["status_snapshot", "program_report_path"]
}
```

Hard integrity/login/verification/source-block conditions always stop. Recoverable conditions must state a numeric count/window and a named retry; an executor cannot invent a retry.

### 2.8 Write Boundary

`allowed_writes` lists resolved experiment-relative paths and artifact types. The application validates that every resolved path remains under the experiment artifact root.

Forbidden writes include:

- source files;
- user mode/custom settings;
- arbitrary files outside the experiment root;
- database updates not performed by the application interface;
- prior manifest/report/artifact overwrites.

### 2.9 Required Artifacts

Each artifact entry defines:

- artifact type;
- exact path;
- producer (`application` or `executor`);
- existence requirement;
- content digest requirement;
- minimum required fields;
- whether absence makes the round invalid or blocked.

## 3. Executor Report Requirements

```json
{
  "schema_version": 1,
  "report_id": "unique-id",
  "task_id": "exact-issued-task-id",
  "experiment_id": "...",
  "candidate_id": "...",
  "round_id": "...",
  "manifest_digest": "exact-issued-digest",
  "status": "completed|blocked|invalid|cancelled",
  "preflight": [],
  "steps": [],
  "observations": {},
  "program_evidence": {},
  "artifacts": [],
  "stop_reason": null,
  "unexecuted_steps": [],
  "executor_notes": [],
  "started_at": "RFC3339 timestamp",
  "finished_at": "RFC3339 timestamp"
}
```

### 3.1 Program Evidence

```json
{
  "program_report_path": "experiment-relative/path.json",
  "program_report_digest": "...",
  "config_digest": "...",
  "scope_digest": "...",
  "input_artifact_digest": "...",
  "total_duration_ms": 0,
  "stage_durations_ms": {},
  "wait_duration_ms": 0,
  "retry_duration_ms": 0,
  "attempt_count": 0,
  "retry_count": 0,
  "input_count": 0,
  "terminal_count": 0,
  "success_count": 0,
  "failed_count": 0,
  "missing_count": 0,
  "duplicate_count": 0,
  "quality_diff_count": 0,
  "error_counts": {}
}
```

These values must be copied from the program report and verified by digest. The executor cannot calculate replacements.

### 3.2 Blocked Report

A blocked report must include:

- exact last successful preflight/step;
- exact failed or unknown condition;
- latest task status snapshot;
- already-created artifact references and digests;
- unexecuted step IDs;
- confirmation that no parameters, inputs or acceptance rules were changed.

### 3.3 Executor Notes

Notes may describe observable facts only. Recommendations, candidate rankings, parameter suggestions and statements such as “the experiment is successful overall” are rejected.

## 4. Validation and Acceptance

A report is accepted only when:

1. task, experiment, candidate and round IDs match;
2. manifest digest matches the issued immutable manifest;
3. actual config/scope/input digests match;
4. all required preflight and executed-step evidence is present;
5. program evidence exists and its digest matches;
6. terminal counts and error fields are internally consistent;
7. report status is compatible with program task status;
8. all required artifacts exist inside the allowed root;
9. no forbidden action is reported or detected.

Failure of any check makes the report `invalid` or `blocked`; it cannot advance the candidate.

## 5. Fixed Human-Readable Return Format

The rendered task instructs the executor to end with exactly these sections:

```markdown
# Execution Result

Task ID: ...
Status: completed | blocked | invalid | cancelled
Manifest digest: ...
Program evidence: path + digest
Executor report: path + digest

## Completed Steps
[ordered IDs only]

## Stop Reason
[exact code and observed fact, or none]

## Unexecuted Steps
[ordered IDs only, or none]
```

The controller reads the structured JSON report and program evidence. This human-readable return is only a navigation aid.
