# AI Contracts: Candidate v4 and Job Assessment v2

AI is advisory. The program owns input selection, evidence locators, PII removal, hard rules, category, rank, state and terminal completion.

## Candidate Analysis v4

### Input

- Canonicalized resume text, maximum existing resume text limit.
- Contract schema/version.
- No profile database state, historical feedback, API key or file path.

### Provider-owned output

```json
{
  "contract_version": "v4",
  "summary": {
    "headline": "",
    "experience_level": "",
    "domains": [],
    "strengths": []
  },
  "evidence": [
    {
      "client_ref": "e1",
      "type": "skill",
      "normalized_value": "Python",
      "source_quote": "...exact quote...",
      "assertion_type": "explicit",
      "confidence": 90
    }
  ],
  "facts": [
    {
      "client_ref": "f1",
      "fact_type": "skill",
      "value": {"name": "Python", "usage_years": null, "last_used": null, "level": null, "contexts": []},
      "normalized_value": "Python",
      "evidence_refs": ["e1"],
      "assertion_type": "explicit",
      "confidence": 90
    }
  ],
  "unknowns": [{"field": "current_city", "message": "简历无法确认当前求职城市"}],
  "directions": [
    {
      "client_ref": "d1",
      "name": "Python 后端工程师",
      "type": "core",
      "rationale": "",
      "evidence_refs": ["e1"],
      "fact_refs": ["f1"],
      "gaps": [],
      "confidence": 85,
      "default_enabled": true,
      "search_terms": ["Python 后端", "后端开发"]
    }
  ]
}
```

### Program-owned fields

- evidence id, locator, safe excerpt, sensitive flag
- fact id, stable key, source kind, verification status
- profile version id/status/hash
- analysis quality/status/warnings
- direction ids and persisted evidence/fact links

### Validation

- Exact source quote must uniquely resolve to canonical resume text.
- Evidence/fact/direction refs must resolve within the same response lineage.
- Every resume-derived fact requires at least one evidence ref.
- User intent fields are not accepted as facts unless explicitly stated in resume; even then they remain unknown/current-intent pending until user confirmation.
- Unknown/extra/oversized fields are quarantined independently.
- PII facts/evidence are rejected.
- A direction cannot default-enable without valid fact/evidence links and executable search terms.
- Provider `quality`, locators, backend ids and user confirmation values are ignored/rejected.

### Retry

- One initial provider call.
- If parseable but partial/manual, at most one corrective call containing only safe warning codes/paths and the provider's prior structured JSON.
- Transport/auth/timeout or unparseable response does not trigger an unbounded retry.
- Best validated result wins; no merging of contradictory raw responses.
- `provider_call_count` is recorded in safe analysis metrics.

## Job Assessment v2

### Input

One job and at most two relevant enabled directions:

```json
{
  "contract_version": "job_assessment_v2",
  "candidate": {
    "profile_version_id": "cpv-1",
    "summary": {},
    "facts": [],
    "evidence": []
  },
  "job": {
    "snapshot_id": "snap-1",
    "content_hash": "sha256...",
    "fields": {
      "title": "",
      "company": "",
      "jd": "",
      "salary": "",
      "location": "",
      "tags": []
    }
  },
  "directions": [
    {
      "id": "dir-1",
      "name": "",
      "type": "core",
      "rationale": "",
      "gaps": [],
      "fact_refs": [],
      "evidence_refs": []
    }
  ]
}
```

Only facts/evidence linked to supplied directions are included. Full resume text is excluded.

### Provider-owned output

```json
{
  "contract_version": "job_assessment_v2",
  "assessments": [
    {
      "direction_id": "dir-1",
      "dimensions": {
        "direction_alignment": {"score": 0, "candidate_fact_refs": [], "candidate_evidence_refs": [], "job_evidence_refs": []},
        "skill_coverage": {"score": 0, "candidate_fact_refs": [], "candidate_evidence_refs": [], "job_evidence_refs": []},
        "experience_match": {"score": 0, "candidate_fact_refs": [], "candidate_evidence_refs": [], "job_evidence_refs": []},
        "industry_relevance": {"score": 0, "candidate_fact_refs": [], "candidate_evidence_refs": [], "job_evidence_refs": []}
      },
      "match_score": 0,
      "confidence": 0,
      "positive": [{"text": "", "candidate_fact_refs": [], "candidate_evidence_refs": [], "job_evidence_refs": []}],
      "gaps": [{"text": "", "candidate_fact_refs": [], "job_evidence_refs": []}],
      "proposed_band": "uncertain"
    }
  ]
}
```

### Validation

- `direction_id` must be one of the supplied directions and appear once.
- All four dimensions are required per returned direction.
- Scores/confidence are integer 0–100; booleans/floats/strings rejected.
- Candidate fact/evidence refs must belong to the supplied profile version and direction.
- Job refs must name supplied snapshot fields or validated bounded excerpts.
- `positive` requires both candidate-side and job-side evidence for a formal recommendation.
- Gaps cannot reference unknown fields or other jobs.
- `proposed_band` is advisory only: high, adjacent, growth, unsuitable, uncertain.
- One invalid direction is quarantined as needs_review; valid sibling direction remains usable.
- Hard-rule violation/unknown is evaluated before/after AI by program policy and cannot be overridden.

### Retry

- One initial call for one job and up to two directions.
- If the envelope is parseable and only some directions are invalid, at most one corrective call includes only invalid direction ids and safe validation paths.
- Provider call count for the evaluation group is 1–2.
- Timeout/auth/network failures create pending/needs_review assessments with safe codes; no default score is stored.

## Privacy and Logging

- Provider input may contain canonical resume text only for candidate analysis after explicit consent.
- Job assessment input contains sanitized facts/evidence, not full resume.
- Logs/events contain ids, counts, contract versions, durations and safe codes only.
- Raw output is validated in memory and discarded.
- Safe user explanations are rebuilt from validated refs and bounded excerpts; raw provider text is never displayed directly.

