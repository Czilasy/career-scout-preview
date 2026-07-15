"""Program-governed semantic verification for resume/JD screening.

The AI may propose scores and a verdict, but this module owns validation and
the final semantic outcome. Raw model responses and free-form reasons are
never returned to callers or persisted by this module.
"""

from __future__ import annotations

import json


DIMENSIONS = (
    "direction_alignment",
    "skill_coverage",
    "experience_match",
    "industry_relevance",
)
FAILURE_STAGES = (
    "ai_timeout",
    "ai_network_error",
    "ai_invalid_output",
    "ai_uncertain",
    "verification_error",
)
MIN_CONFIDENCE = 70
MIN_DIMENSION_SCORE = 50
MIN_MATCH_SCORE = 70


def _pending(stage: str) -> dict:
    return {
        "verdict": "pending",
        "confidence": None,
        "match_score": None,
        "dimensions": {},
        "failure_stage": stage,
    }


def build_semantic_prompt(resume_text: str, jd_text: str) -> str:
    """Build the fixed-dimension JSON contract sent to the configured AI."""
    contract = {
        "dimensions": {
            name: {"score": "integer 0-100", "reason": "brief evidence"}
            for name in DIMENSIONS
        },
        "match_score": "integer 0-100",
        "verdict": "match | mismatch | uncertain",
        "confidence": "integer 0-100",
    }
    return (
        "Compare the resume and job description only on the four fixed dimensions. "
        "Return one JSON object matching this contract; do not add fields.\n"
        f"CONTRACT={json.dumps(contract, ensure_ascii=False)}\n"
        f"RESUME={str(resume_text or '')}\nJD={str(jd_text or '')}"
    )


def _score(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid_score")
    number = float(value)
    if number < 0 or number > 100:
        raise ValueError("invalid_score")
    return int(number) if number.is_integer() else number


def validate_semantic_output(data) -> dict:
    """Validate and sanitize a model response before the program uses it."""
    if not isinstance(data, dict):
        return _pending("ai_invalid_output")
    try:
        verdict = data["verdict"]
        if verdict not in {"match", "mismatch", "uncertain"}:
            raise ValueError("invalid_verdict")
        confidence = _score(data["confidence"])
        match_score = _score(data["match_score"])
        raw_dimensions = data["dimensions"]
        if not isinstance(raw_dimensions, dict) or set(raw_dimensions) != set(DIMENSIONS):
            raise ValueError("invalid_dimensions")
        dimensions = {}
        for name in DIMENSIONS:
            item = raw_dimensions[name]
            if not isinstance(item, dict):
                raise ValueError("invalid_dimension")
            dimensions[name] = {"score": _score(item["score"])}
    except (KeyError, TypeError, ValueError):
        return _pending("ai_invalid_output")

    if verdict == "uncertain" or confidence < MIN_CONFIDENCE:
        result = _pending("ai_uncertain")
        result.update({
            "confidence": confidence,
            "match_score": match_score,
            "dimensions": dimensions,
        })
        return result
    program_verdict = (
        "match"
        if match_score >= MIN_MATCH_SCORE
        and all(item["score"] >= MIN_DIMENSION_SCORE for item in dimensions.values())
        else "mismatch"
    )
    return {
        "verdict": program_verdict,
        "confidence": confidence,
        "match_score": match_score,
        "dimensions": dimensions,
        "failure_stage": None,
    }


def assess_semantic_similarity_formal(
    resume_text: str,
    jd_text: str,
    *,
    ai_available: bool,
    call_ai_fn=None,
) -> dict:
    """Call the AI through an injected adapter, then apply the program gate."""
    if not ai_available:
        return {
            "verdict": "match",
            "confidence": None,
            "match_score": None,
            "dimensions": {},
            "failure_stage": None,
        }
    if call_ai_fn is None:
        return _pending("verification_error")
    try:
        raw = call_ai_fn(build_semantic_prompt(resume_text, jd_text))
    except TimeoutError:
        return _pending("ai_timeout")
    except ConnectionError:
        return _pending("ai_network_error")
    except (RuntimeError, ValueError, TypeError, KeyError):
        return _pending("verification_error")
    except Exception:
        return _pending("verification_error")
    return validate_semantic_output(raw)


# ---------------------------------------------------------------------------
# T013: Job-direction assessment contract v1 validation (feature 004)
# ---------------------------------------------------------------------------

JOB_ASSESSMENT_CONTRACT_VERSION = "v1"
JOB_PROPOSED_BANDS = ("high", "adjacent", "growth", "unsuitable", "uncertain")
JOB_ASSESSMENT_FAILURE_STAGE = "ai_invalid_output"


def _needs_review(reason: str) -> dict:
    return {
        "category": "needs_review",
        "match_score": None,
        "confidence": None,
        "dimensions": {},
        "gaps": [],
        "proposed_band": "uncertain",
        "failure_stage": reason,
        "contract_version": JOB_ASSESSMENT_CONTRACT_VERSION,
    }


def _validate_evidence_refs(refs, allowed_ids: set[str], field: str) -> list[str]:
    """Return validated refs; raise ValueError on any unknown ref."""
    if not isinstance(refs, list) or not all(isinstance(x, str) for x in refs):
        raise ValueError(f"invalid_refs:{field}")
    for ref in refs:
        if ref not in allowed_ids:
            raise ValueError(f"unknown_ref:{field}:{ref}")
    return list(refs)


def validate_job_assessment(
    data,
    analysis_evidence_ids,
    direction_evidence_ids,
    snapshot_fields,
) -> dict:
    """Validate an AI job-direction assessment response against contract v1.

    Parameters mirror the contract: candidate evidence must belong to the
    run analysis and the selected direction; job evidence must resolve to
    the supplied snapshot fields/excerpts. ``proposed_band`` is advisory
    only — the stored category is derived from hard rules and the versioned
    evaluation policy by the caller.

    Returns a sanitized dict. Any structural/reference/sensitivity failure
    yields ``needs_review`` with a safe failure stage; the raw model output
    is never echoed back.
    """
    if not isinstance(data, dict):
        return _needs_review(JOB_ASSESSMENT_FAILURE_STAGE)

    analysis_evidence_ids = set(analysis_evidence_ids or [])
    direction_evidence_ids = set(direction_evidence_ids or [])
    # Candidate evidence allowed in this assessment = evidence that belongs
    # to the analysis AND to the selected direction.
    allowed_candidate_refs = analysis_evidence_ids & direction_evidence_ids
    # Job evidence allowed = the snapshot field keys the caller supplied.
    allowed_job_refs = set(snapshot_fields or [])

    try:
        raw_dimensions = data.get("dimensions")
        if not isinstance(raw_dimensions, dict) or set(raw_dimensions) != set(DIMENSIONS):
            raise ValueError("invalid_dimensions")
        dimensions: dict[str, dict] = {}
        for name in DIMENSIONS:
            item = raw_dimensions[name]
            if not isinstance(item, dict):
                raise ValueError("invalid_dimension")
            score = _score(item.get("score"))
            cand_refs = _validate_evidence_refs(
                item.get("candidate_evidence_refs", []),
                allowed_candidate_refs,
                f"{name}.candidate_evidence_refs",
            )
            job_refs = _validate_evidence_refs(
                item.get("job_evidence_refs", []),
                allowed_job_refs,
                f"{name}.job_evidence_refs",
            )
            dimensions[name] = {
                "score": score,
                "candidate_evidence_refs": cand_refs,
                "job_evidence_refs": job_refs,
            }
        match_score = _score(data.get("match_score"))
        confidence = _score(data.get("confidence"))
        raw_gaps = data.get("gaps", [])
        if not isinstance(raw_gaps, list):
            raise ValueError("invalid_gaps")
        gaps: list[dict] = []
        for gap in raw_gaps:
            if not isinstance(gap, dict):
                raise ValueError("invalid_gap")
            text = gap.get("text", "")
            if not isinstance(text, str):
                raise ValueError("invalid_gap_text")
            gap_job_refs = _validate_evidence_refs(
                gap.get("job_evidence_refs", []),
                allowed_job_refs,
                "gap.job_evidence_refs",
            )
            gaps.append({"text": text, "job_evidence_refs": gap_job_refs})
        proposed_band = data.get("proposed_band", "uncertain")
        if proposed_band not in JOB_PROPOSED_BANDS:
            raise ValueError("invalid_proposed_band")
    except (KeyError, TypeError, ValueError):
        return _needs_review(JOB_ASSESSMENT_FAILURE_STAGE)

    if confidence < MIN_CONFIDENCE:
        result = _needs_review("ai_uncertain")
        result.update({
            "match_score": match_score,
            "confidence": confidence,
            "dimensions": dimensions,
            "gaps": gaps,
            "proposed_band": proposed_band,
        })
        return result

    return {
        "category": None,  # caller applies hard rules + policy to derive
        "match_score": match_score,
        "confidence": confidence,
        "dimensions": dimensions,
        "gaps": gaps,
        "proposed_band": proposed_band,
        "failure_stage": None,
        "contract_version": JOB_ASSESSMENT_CONTRACT_VERSION,
    }
