# HTTP API Contract: 高级设置深度自动调优

**Date**: 2026-07-29

All mutating routes use the existing same-origin, build-identity and write-token protections. Experiment reads contain input/config evidence and must use the existing protected-read policy. Credentials and raw resume text are never returned.

## 1. Search Scope Preview

### POST `/api/search-scope/preview`

Normalizes and validates the task scope before start.

**Request**:

```json
{
  "keywords": ["AI应用开发", " ai应用开发 "],
  "scope_kind": "cities",
  "cities": ["东莞", "东莞市"],
  "pages_per_combination": 3
}
```

**Response 200**:

```json
{
  "ok": true,
  "scope": {
    "keywords": ["AI应用开发"],
    "scope_kind": "cities",
    "cities": ["东莞"],
    "pages_per_combination": 3,
    "combination_count": 1,
    "planned_pages": 3,
    "task_size": "small",
    "scope_digest": "sha256..."
  },
  "deduplicated": {
    "keywords": ["ai应用开发"],
    "cities": ["东莞市"]
  }
}
```

**Response 422**: unknown/disabled city, nationwide mixed with cities, empty keyword, non-positive pages, or planned pages outside the feature's 1-30 validated range. The response includes structured field errors and suggestions; it never auto-selects a fuzzy city.

## 2. Advanced Configuration State

### GET `/api/advanced-settings`

Replaces the current response with versioned configuration state while keeping the numeric `settings` object for migration compatibility.

**Response 200**:

```json
{
  "ok": true,
  "selection": "custom",
  "settings": {
    "inter_combo_delay": 10,
    "detail_batch_size": 15,
    "detail_interval": 2,
    "detail_reset_every": 4,
    "detail_batch_cooldown": 5,
    "screen_batch_size": 50,
    "screen_concurrency": 5,
    "match_batch_size": 4,
    "match_concurrency": 10
  },
  "last_custom": {"config_digest": "sha256...", "settings": {}},
  "mode_version": {
    "id": "mode-version-id",
    "version_digest": "sha256...",
    "available_modes": ["stable", "balanced", "extreme"]
  },
  "manual_ranges": {},
  "config_schema_version": 1
}
```

`pages` is not returned as an execution-mode field.

### PUT `/api/advanced-settings/custom`

Saves a complete recent custom configuration. Partial patches are rejected.

**Request**: `{ "config_schema_version": 1, "settings": {all nine fields} }`

**Response 200**: normalized complete config + digest + `selection: custom`.

**Response 409**: an active task has frozen its scope/config; the custom value may still be saved for future tasks, but cannot mutate the active task. The response distinguishes `saved_for_future` from `applied_to_active`.

### POST `/api/advanced-settings/select-mode`

**Request**:

```json
{
  "mode": "stable|balanced|extreme|custom",
  "scope_digest": "sha256..."
}
```

**Response 200**: selected complete config, task size, mode version ID, config digest. The server recomputes/loads the canonical scope and does not trust a client-provided size string.

## 3. Experiment Lifecycle

### POST `/api/tuning/experiments`

Creates a draft experiment and stores user-proposed representative inputs. It does not start pressure work.

**Request**:

```json
{
  "spec_version": "011-deep-configuration-probing",
  "source_scope": {
    "keywords": ["AI应用开发", "智能体开发"],
    "scope_kind": "cities",
    "cities": ["东莞"],
    "pages_per_combination": 3
  },
  "quality_context": {
    "profile_summary": "用户确认的候选人画像摘要",
    "screening_fields": {
      "salary": ["403", "404", "405"],
      "experience": ["101", "103", "104"],
      "degree": ["202", "203"],
      "industry": [],
      "scale": ["301", "302", "303", "304", "305"],
      "stage": []
    },
    "profile_ref": "user-confirmed:2026-07-29"
  },
  "workloads": [
    {"task_size": "small", "structure_index": 1, "scope": {}},
    {"task_size": "small", "structure_index": 2, "scope": {}},
    {"task_size": "medium", "structure_index": 1, "scope": {}},
    {"task_size": "medium", "structure_index": 2, "scope": {}},
    {"task_size": "large", "structure_index": 1, "scope": {}},
    {"task_size": "large", "structure_index": 2, "scope": {}}
  ]
}
```

`quality_context` is mandatory. The application freezes its canonical JSON and
SHA-256 digest together with every workload artifact; missing or malformed
context returns `400 invalid_request`.

**Response 201**: experiment ID, normalized workload preview, estimated initial schedule, `status: draft`.

**Response 409**: another experiment or ordinary pressure task owns the lease.

### POST `/api/tuning/experiments/{experiment_id}/confirm-input`

Freezes the complete input version after user confirmation.

**Response 200**: input version ID, scope/workload digests, frozen quality-context
digest, `status: awaiting_instruction`
when no execution conflict exists. `preflight` is an internal transition inside the
atomic confirmation flow while frozen artifact paths, identities, existence, and
digests (including the quality-context digest) are checked; it is not the externally observed successful terminal state
of this request. A conflict returns the corresponding blocked state instead.

Once confirmed, input changes require a new experiment.

### GET `/api/tuning/experiments/{experiment_id}`

Returns the persisted experiment picture:

```json
{
  "id": "...",
  "status": "awaiting_instruction",
  "current_stage": "detail",
  "current_candidate_id": "...",
  "input_version": {},
  "quality_reference": {},
  "progress": {
    "confirmed_rounds": 12,
    "invalid_rounds": 1,
    "remaining_required_rounds": 18,
    "estimated_remaining_seconds": 8200
  },
  "active_manifest": null,
  "block": null,
  "candidate_summary": [],
  "can_resume": false,
  "can_cancel": true,
  "can_apply": false
}
```

### POST `/api/tuning/experiments/{experiment_id}/cancel`

Stops new work, preserves confirmed evidence, releases lease after current work is safely settled, and sets terminal `cancelled`. It never changes mode/custom settings.

### POST `/api/tuning/experiments/{experiment_id}/resume`

Allowed only from `blocked` or restart-interrupted state after preflight succeeds. It never auto-selects a new candidate.

## 4. Controller Task Issuance

### POST `/api/tuning/experiments/{experiment_id}/manifests`

Controller-only route. Validates and issues one exact executor manifest conforming to [executor-protocol.md](./executor-protocol.md).

**Request**: complete manifest without server-generated ID/digest/timestamps.

**Response 201**:

```json
{
  "manifest_id": "...",
  "manifest_digest": "sha256...",
  "rendered_task_path": "tuning/<experiment>/tasks/<id>.md",
  "round_id": "...",
  "status": "issued"
}
```

**Response 422**: missing field, placeholder/discretionary instruction, config/scope/reference mismatch, invalid path, invalid repetition, or rule not represented in stop conditions.

**Response 409**: experiment not awaiting instruction, another manifest active, or lease unavailable.

### GET `/api/tuning/manifests/{manifest_id}`

Returns safe structured manifest plus rendered task path. Secrets and raw sensitive content are never included.

### POST `/api/tuning/manifests/{manifest_id}/execute`

Starts the exact round after revalidating manifest digest, input artifacts, build identity and lease. The executor AI triggers this route but cannot alter the payload.

**Response 202**: round ID, child task ID, exact status URL.

### GET `/api/tuning/rounds/{round_id}`

Returns program-owned status, stage, counters, structured errors and program evidence readiness. This is the only polling target for that round.

## 5. Executor Report

### POST `/api/tuning/manifests/{manifest_id}/report`

Accepts one report conforming to [executor-protocol.md](./executor-protocol.md).

**Response 201**:

```json
{
  "report_id": "...",
  "validation_status": "accepted",
  "round_status": "confirmed",
  "experiment_status": "awaiting_instruction"
}
```

**Response 422**: report schema/digest/evidence mismatch. The round becomes `invalid`; the server does not fill missing values.

**Response 409**: report already accepted, manifest not issued/running/reported, or child round not in compatible terminal state.

### GET `/api/tuning/rounds/{round_id}/evidence`

Returns safe aggregate metrics and artifact references. Raw credentials, resume content and unrestricted model output are excluded.

## 6. Candidate Decisions

### POST `/api/tuning/experiments/{experiment_id}/decisions`

Controller-only route after an accepted report.

**Request examples**:

```json
{"candidate_id":"...","decision":"promote","reason_evidence":["round-id"]}
```

```json
{"candidate_id":"...","decision":"reject","code":"hard_error","reason_evidence":["round-id"]}
```

```json
{
  "candidate_id":"...",
  "decision":"refine",
  "next_config": {"all_nine_fields":"exact values"},
  "reason_evidence":["round-a","round-b"]
}
```

The application validates evidence ownership and full config. The executor AI cannot call this route.

## 7. Results, Apply and Rollback

### GET `/api/tuning/experiments/{experiment_id}/result`

Returns summary and expandable evidence. `can_apply` is true only when all required size/structure/final validation gates pass.

### POST `/api/tuning/experiments/{experiment_id}/apply`

**Request**: `{ "candidate_mode_version_digest": "sha256..." }`

Atomically activates the complete nine-slot version after explicit user confirmation.

**Response 409**: incomplete experiment, digest mismatch, invalid/blocked round, missing slot/evidence, or experiment not `completed`.

### POST `/api/advanced-settings/mode-versions/rollback`

**Request**: `{ "target_version_id": "previous-complete-version" }`

Atomically switches the complete active mode version. Recent custom config is untouched.

## 8. Common Error Shape

```json
{
  "ok": false,
  "error_code": "structured_code",
  "error": "用户可理解的原因",
  "details": {},
  "retryable": false,
  "required_action": "exact next action or null"
}
```

No route returns a success response when required artifact, report, mode slot or persisted state is absent.
