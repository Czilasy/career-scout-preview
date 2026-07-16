# AI Contract: Candidate Analysis and Job Assessment

AI contracts are versioned, deterministic where provider support allows, and validated before use. Unknown fields are omitted or explicit; no default score is inserted.

## Candidate analysis provider output v3

Version 3 is the backend-owned canonical result. The backend supplies typed empty values and quarantines invalid fields independently; one invalid evidence item never discards a valid summary. `contract_version` is always `v3`. The canonical quality shape is `quality {status: "complete|partial|manual_required", warnings: []}`.

The canonical empty result is exact JSON: `{"contract_version":"v3","summary":{"headline":"","experience_level":"","domains":[],"strengths":[]},"evidence":[],"unknowns":[],"directions":[],"quality":{"status":"complete","warnings":[]}}`. Backend normalization owns this shape; provider omissions are filled with these typed empties.

Required top-level fields (backend-owned typed empty shape; backend-owned typed empty values):

```json
{
  "contract_version": "v3",
  "summary": {
    "headline": "",
    "experience_level": "",
    "domains": [],
    "strengths": []
  },
  "evidence": [],
  "unknowns": [],
  "directions": [],
  "quality": {"status": "complete", "warnings": []}
}
```

Program validation:

- JSON types are strict: strings are JSON strings; lists contain only strings; `confidence` is an integer from 0 through 100; `default_enabled` is boolean. Allowed enums are evidence `type` = `skill|responsibility|project|industry|seniority|education|achievement|other`, assertion `explicit|inferred`, unknown field `current_city|min_salary|career_intent|other`, direction `type` = `core|adjacent|growth`, and quality status `complete|partial|manual_required`. At most 5 directions and 3 search terms per direction are allowed.
- A warning object is exactly `{`code`, `path`}` with both non-empty strings; no other keys or raw provider/resume text are allowed. Allowed codes are `invalid_type`, `invalid_enum`, `invalid_evidence`, `sensitive_value`, `unverified_field`, `missing_required`, and `reference_invalid`.
- `warnings` is an array of warning objects.

Object-array schemas (the generic string-list rule does not apply to these objects):

object arrays are never coerced from scalars or generic string lists.

- `evidence` items require `client_ref` (non-empty string), `type` (required enum `skill|responsibility|project|industry|seniority|education|achievement|other`), `normalized_value` (string), `source_quote` (non-empty string), `assertion_type` (required enum `explicit|inferred`), and `confidence` (required integer 0–100). Invalid required fields or quotes quarantine and drop the entire item from normalized `evidence`; persist only `{code,path}`. Every accepted/persisted evidence item has a unique source quote; `source_quote` cannot be empty for an accepted evidence item.
- `unknowns` items require `field` (required enum `current_city|min_salary|career_intent|other`) and `message` (string, typed empty `""` when unavailable). No additional keys are permitted.
- `directions` items require `client_ref` (non-empty string), `name` (string, typed empty `""` when quarantined), `type` (required enum `core|adjacent|growth`), `rationale` (string, typed empty `""` when unavailable), `evidence_refs` (array of strings, default `[]`), `gaps` (array of strings, default `[]`), `confidence` (integer 0–100, default `0`), `default_enabled` (boolean, default `false`), and `search_terms` (array of strings, default `[]`, maximum 3). The directions array has a maximum of 5 items. Directions referencing dropped evidence lose those refs and cannot be default-enabled.

- Canonical resume text is derived with the contract's versioned Unicode normalization rule; offsets are Unicode code-point indexes into this exact text.
- Every `source_quote` must be a minimal exact substring of canonical resume text and must resolve uniquely. A repeated quote requires additional surrounding text; fuzzy or arbitrary first-match selection is forbidden.
- Program code generates `source_locator.start/end` from the unique match and verifies that the resolved slice equals `source_quote` before redaction.
- `safe_excerpt` is derived locally from the resolved quote, minimized and redacted before persistence. The model does not author the persisted safe excerpt or locator.
- Every direction evidence reference must exist in the same response.
- Default-enabled directions require evidence and the configured confidence gate.
- Normalize and merge synonymous directions; retain at most five default-visible directions and at most three terms each.
- Contact details, identity numbers and exact addresses are rejected as evidence.
- An invalid evidence item is quarantined with a field-level warning code and dropped entirely from normalized `evidence`; unrelated valid summary, unknowns and directions remain usable (invalid evidence item is quarantined without discarding the valid summary).
- `quality.status` is one of `complete|partial|manual_required`; `warnings` is always a typed list of field-level warning objects. A `ready` analysis may be `partial` or `manual_required` after quarantine.
- Identity fields (name, gender, age, phone, ID number, exact address and similar contact identifiers) are excluded from candidate and search fields.
- quarantined fields cannot influence confirmation, SearchPlan compilation, matching, or scraper inputs; unverified search fields never become confirmed constraints.
- At most one corrective retry is allowed for a successfully returned but structurally invalid response. Missing evidence, values, scores or references are never guessed locally.

## Job-direction assessment input v1

The AI receives only:

- sanitized candidate summary;
- evidence IDs, normalized values and minimal safe excerpts relevant to one direction;
- selected direction name/type/rationale/gaps;
- one sanitized job detail snapshot;
- fixed dimension names and the output contract.

The AI does not receive credentials, unrelated feedback, other users' data, raw run artifacts or workflow state mutation instructions.

Required input shape:

```json
{
  "candidate_summary": {
    "headline": "string",
    "experience_level": "string",
    "domains": ["string"],
    "strengths": ["string"]
  },
  "direction": {
    "id": "direction-id",
    "name": "string",
    "type": "core|adjacent|growth",
    "rationale": "string",
    "gaps": ["string"],
    "evidence": [
      {
        "id": "evidence-id",
        "type": "skill",
        "normalized_value": "Python",
        "safe_excerpt": "Python 后端经验",
        "assertion_type": "explicit|inferred"
      }
    ]
  },
  "job": {
    "job_id": "job-id",
    "completeness": "complete|partial|unavailable|expired",
    "fields": {
      "title": "string",
      "company": "string",
      "jd": "string",
      "salary": "string",
      "location": "string",
      "tags": "string"
    }
  }
}
```

Candidate evidence IDs are the IDs in `direction.evidence`. Job evidence IDs are the keys explicitly supplied in `job.fields` (`title`, `company`, `jd`, `salary`, `location`, `tags`), not model-created aliases.

## Job-direction assessment output v1

```json
{
  "dimensions": {
    "direction_alignment": {"score": 0, "candidate_evidence_refs": ["evidence-id"], "job_evidence_refs": ["title", "jd"]},
    "skill_coverage": {"score": 0, "candidate_evidence_refs": ["evidence-id"], "job_evidence_refs": ["jd"]},
    "experience_match": {"score": 0, "candidate_evidence_refs": ["evidence-id"], "job_evidence_refs": ["jd"]},
    "industry_relevance": {"score": 0, "candidate_evidence_refs": ["evidence-id"], "job_evidence_refs": ["company", "jd"]}
  },
  "match_score": 0,
  "confidence": 0,
  "gaps": [
    {"text": "string", "job_evidence_refs": ["jd"]}
  ],
  "proposed_band": "high|adjacent|growth|unsuitable|uncertain"
}
```

Program validation:

- Numeric fields must be real numbers from 0 to 100; booleans are invalid.
- Candidate evidence references must belong to the run analysis and selected direction.
- Job evidence references must resolve to the supplied snapshot fields/excerpts.
- Missing required dimensions, unknown keys, invalid references or low confidence result in `needs_review`.
- `proposed_band` is advisory. Hard rules and versioned evaluation policy determine the stored category.
- Free-form model reasons and the raw response are neither returned to the browser nor persisted.
- One job-assessment failure is isolated to that assessment and persisted with a safe failure code; it must not abort unrelated jobs.

## Safe failure codes

```text
ai_timeout
ai_auth_failed
ai_network_error
ai_invalid_output
ai_uncertain
evidence_reference_invalid
input_incomplete
verification_error
```
