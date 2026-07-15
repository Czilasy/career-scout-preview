# AI Contract: Candidate Analysis and Job Assessment

AI contracts are versioned, deterministic where provider support allows, and validated before use. Unknown fields are omitted or explicit; no default score is inserted.

## Candidate analysis provider output v2

Version 2 removes authoritative character offsets from the model contract. The model returns a minimal exact quote; program code resolves and verifies the locator against canonical resume text before the domain validator sees the response.

Required top-level fields:

```json
{
  "summary": {
    "headline": "string",
    "experience_level": "string or empty",
    "domains": ["string"],
    "strengths": ["string"]
  },
  "evidence": [
    {
      "client_ref": "e1",
      "type": "skill|responsibility|project|industry|seniority|education|achievement|other",
      "normalized_value": "string",
      "source_quote": "minimal exact substring copied from the supplied resume",
      "assertion_type": "explicit|inferred",
      "confidence": 0
    }
  ],
  "unknowns": [
    {"field": "current_city|min_salary|career_intent|other", "message": "string"}
  ],
  "directions": [
    {
      "client_ref": "d1",
      "name": "string",
      "type": "core|adjacent|growth",
      "rationale": "string",
      "evidence_refs": ["e1"],
      "gaps": ["string"],
      "confidence": 0,
      "default_enabled": true,
      "search_terms": ["string"]
    }
  ]
}
```

Program validation:

- Canonical resume text is derived with the contract's versioned Unicode normalization rule; offsets are Unicode code-point indexes into this exact text.
- Every `source_quote` must be a minimal exact substring of canonical resume text and must resolve uniquely. A repeated quote requires additional surrounding text; fuzzy or arbitrary first-match selection is forbidden.
- Program code generates `source_locator.start/end` from the unique match and verifies that the resolved slice equals `source_quote` before redaction.
- `safe_excerpt` is derived locally from the resolved quote, minimized and redacted before persistence. The model does not author the persisted safe excerpt or locator.
- Every direction evidence reference must exist in the same response.
- Default-enabled directions require evidence and the configured confidence gate.
- Normalize and merge synonymous directions; retain at most five default-visible directions and at most three terms each.
- Contact details, identity numbers and exact addresses are rejected as evidence.
- Any structural/reference failure invalidates the response; partial unvalidated output is not persisted as ready.
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
