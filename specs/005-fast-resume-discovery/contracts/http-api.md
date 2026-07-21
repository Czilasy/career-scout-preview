# HTTP Contract: Fast Resume Discovery v2

**Base path**: `/api/discovery`  
**Compatibility**: additive to 004；existing fields remain unless marked v2-only  
**Authentication**: existing local session token and trusted-host/origin checks apply to every discovery read/write endpoint

## Common Rules

- All error responses use a safe envelope:

```json
{
  "error_code": "input_incomplete",
  "stage": "confirmation",
  "retryable": false,
  "user_message": "请确认候选人画像后再开始寻找岗位。"
}
```

- Raw prompts, raw model responses, resume body and credentials never appear in responses.
- Mutable draft endpoints require the latest `content_hash`; stale edits return `409 state_conflict`.
- `run_id`, `job_id`, `recommendation_id`, fact ids and direction ids are opaque strings.
- Result reads are allowed while a run is nonterminal.

## Resume Upload

### `POST /api/profiles/{profile_id}/resume`

Storage-only for the default discovery flow. It must not invoke legacy resume parsing and candidate analysis in the same request.

Request: multipart `file`; optional `flow=discovery`.

Response `201`:

```json
{
  "resume_id": "resume-1",
  "profile_id": "profile-1",
  "format": "pdf",
  "extraction_status": "ready",
  "privacy_notice": "..."
}
```

Compatibility fields such as `ai_suggestion` may remain as empty/null for legacy clients, but no remote call occurs for `flow=discovery`.

## Candidate Analysis and Profile Version

### `POST /api/discovery/analyses`

Request:

```json
{
  "resume_id": "resume-1",
  "ai_consent": true,
  "contract_version": "v4"
}
```

Response `202`:

```json
{
  "analysis_id": "analysis-1",
  "resume_id": "resume-1",
  "status": "queued",
  "stage": "queued",
  "contract_version": "v4"
}
```

### `GET /api/discovery/analyses/{analysis_id}`

When ready, response adds:

```json
{
  "analysis_id": "analysis-1",
  "status": "ready",
  "quality": {"status": "complete", "warnings": []},
  "candidate_profile_version_id": "cpv-1",
  "summary": {},
  "facts": [],
  "evidence": [],
  "unknowns": [],
  "directions": [],
  "failure": null
}
```

`facts[].source_kind`, `verification_status`, `evidence_ids` and typed `value` are required. PII and source quote bodies are excluded; browser receives safe excerpts only.

### `GET /api/discovery/candidate-versions/{version_id}`

Response `200`:

```json
{
  "id": "cpv-1",
  "profile_id": "profile-1",
  "resume_id": "resume-1",
  "analysis_id": "analysis-1",
  "version": 1,
  "status": "draft",
  "content_hash": "sha256...",
  "summary": {},
  "facts": [],
  "unknowns": []
}
```

### `PATCH /api/discovery/candidate-versions/{version_id}`

Draft-only. Request carries complete replacement operations for stable fact ids:

```json
{
  "expected_content_hash": "sha256...",
  "operations": [
    {"op": "correct", "fact_id": "fact-1", "value": {}},
    {"op": "add", "fact_type": "skill", "value": {}},
    {"op": "reject", "fact_id": "fact-2"}
  ],
  "unknown_resolutions": [
    {"field": "current_city", "value": "上海", "intent_only": true}
  ]
}
```

Response `200` returns the new draft view and new `content_hash`. Editing a confirmed version returns `409` with a safe instruction to create a new draft version.

## Confirmation

### `POST /api/discovery/confirmations`

Request:

```json
{
  "analysis_id": "analysis-1",
  "candidate_profile_version_id": "cpv-1",
  "expected_content_hash": "sha256...",
  "enabled_direction_ids": ["dir-1", "dir-2"],
  "hard_constraints": {
    "city": "上海",
    "min_salary": {
      "amount": 20,
      "currency": "CNY",
      "pay_period": "month",
      "unit": "K",
      "semantics": "monthly_floor",
      "source": "user_confirmed"
    }
  },
  "soft_preferences": {},
  "safe_limits": {},
  "user_directions": []
}
```

Response `201`:

```json
{
  "confirmation_id": "confirmation-1",
  "analysis_id": "analysis-1",
  "candidate_profile_version_id": "cpv-1",
  "intent_contract_version": "intent_v2",
  "intent_hash": "sha256...",
  "version": 1,
  "enabled_direction_ids": ["dir-1", "dir-2"],
  "confirmed_at": "2026-07-20T12:00:00Z"
}
```

The call atomically confirms the referenced draft version and freezes intent. Missing `min_salary` is omitted, never converted to zero.

## Discovery Run

### `POST /api/discovery/runs`

Request:

```json
{"confirmation_id": "confirmation-1", "policy_version": "discovery_v2"}
```

Response `202` uses the Run Summary below.

### `GET /api/discovery/runs/{run_id}`

Response `200`:

```json
{
  "run_id": "run-1",
  "confirmation_id": "confirmation-1",
  "candidate_profile_version_id": "cpv-1",
  "policy_version": "discovery_v2",
  "status": "processing_jobs",
  "stage": "processing_jobs",
  "progress": {
    "search_queries_completed": 5,
    "list_candidates": 132,
    "details_selected": 15,
    "details_completed": 4,
    "assessments_completed": 6,
    "recommendations": 3
  },
  "counts": {
    "high_match": 1,
    "adjacent_match": 2,
    "growth_match": 0,
    "needs_review": 1,
    "not_suitable": 2
  },
  "timing": {
    "first_result_at": "2026-07-20T12:02:10Z",
    "first_batch_at": null,
    "updated_at": "2026-07-20T12:02:20Z"
  },
  "result_revision": 7,
  "failure": null
}
```

Compatibility aliases `source_count`, `detail_count`, `evaluated_count` may be included but the v2 names above are authoritative. `source_count` continues to mean completed search queries.

### `GET /api/discovery/runs/{run_id}/candidates`

Diagnostic/advanced endpoint. Query: `decision`, `state`, `direction_id`, `limit<=100`.

Returns safe list fields, precheck, selection rank/reason and work state; never returns resume text or raw prompt.

### `GET /api/discovery/runs/{run_id}/events?after={sequence}`

Returns append-only safe events for diagnostics and recovery UI:

```json
{"items": [{"sequence": 10, "type": "detail_completed", "payload": {}}], "next": 10}
```

## Progressive Results

### `GET /api/discovery/runs/{run_id}/results`

Allowed for active and terminal runs.

Query parameters:

- `direction_id`: include a job when any assessment matches; still return all assessments.
- `category`: applies to the relevant/primary assessment.
- `limit`: 1–100, default 20.
- `after_revision`: optional client revision. If unchanged, response has `changed=false` and empty items.

Response `200`:

```json
{
  "run_id": "run-1",
  "run_status": "processing_jobs",
  "revision": 7,
  "changed": true,
  "complete": false,
  "items": [
    {
      "recommendation_id": "run-1:job-1",
      "rank": 1,
      "job_id": "job-1",
      "title": "Python 后端工程师",
      "company": "示例公司",
      "salary": "20-35K",
      "location": "上海",
      "tags": [],
      "jd": "...bounded sanitized JD...",
      "jd_excerpt": "...",
      "source_url": "https://www.zhipin.com/job_detail/...html",
      "source_status": "active",
      "fetched_at": "2026-07-20T12:01:30Z",
      "reused": false,
      "category": "high_match",
      "match_score": 88,
      "confidence": 84,
      "completeness": "complete",
      "primary_assessment": {},
      "assessments": [],
      "matched_direction_ids": ["dir-1"],
      "explanation": {
        "positive": [],
        "gaps": [],
        "candidate_evidence_refs": [],
        "job_evidence_refs": []
      },
      "interest_state": "none",
      "sort_components": {}
    }
  ],
  "counts": {},
  "updated_at": "2026-07-20T12:02:20Z"
}
```

JD length is bounded by the existing safe response limit. Explanation contains validated safe excerpts/refs, never model raw rationale.

## Cancel, Resume, Retry and Feedback

Existing endpoints remain:

- `POST /api/discovery/runs/{run_id}/cancel`
- `POST /api/discovery/runs/{run_id}/resume`
- `POST /api/discovery/runs/{run_id}/jobs/{job_id}/retry`
- `GET|POST /api/discovery/feedback`
- `POST /api/discovery/feedback/{feedback_id}/revoke`

V2 additions:

- cancel response includes `cancel_requested_at` and current four-part progress.
- resume rejects profile/confirmation/policy/input hash drift with `409`.
- retry only requeues the specified retryable candidate/snapshot/assessment units.
- feedback increments result revision when visibility or ordering changes.

## Required Safe Error Codes

Existing 004 codes remain. V2 adds:

- `candidate_version_conflict`
- `candidate_fact_invalid`
- `intent_invalid`
- `salary_unparseable`
- `candidate_pool_empty`
- `detail_budget_empty`
- `source_verification_required`
- `source_rate_limited`
- `detail_event_invalid`
- `detail_reuse_invalid`
- `assessment_group_invalid`
- `result_projection_invalid`
- `input_hash_mismatch`

