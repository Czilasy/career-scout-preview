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

    if (
        verdict == "uncertain"
        or confidence < MIN_CONFIDENCE
        or any(item["score"] < MIN_DIMENSION_SCORE for item in dimensions.values())
    ):
        result = _pending("ai_uncertain")
        result.update({
            "confidence": confidence,
            "match_score": match_score,
            "dimensions": dimensions,
        })
        return result
    return {
        "verdict": verdict,
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
    except Exception:
        return _pending("verification_error")
    return validate_semantic_output(raw)
