"""Discovery application services (feature 004).

Hosts the discovery domain logic that orchestrates analysis, confirmation,
search plan compilation, evaluation policy, portfolio assembly and the
unified error envelope. Pure application services that depend on the
store and candidate/source modules; no Flask request handling here.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

from webui.candidate import (
    MAX_DIRECTIONS,
    MAX_SEARCH_TERMS,
    merge_directions,
    enforce_direction_policy,
    normalize_evidence,
    redact_pii,
    validate_candidate_analysis,
)
from webui.ai import AISecurityError as AIProviderError


# ---------------------------------------------------------------------------
# T017: Unified error envelope (openapi Error schema)
# ---------------------------------------------------------------------------

# Safe failure codes and their retryable / stage mapping. Stage is the
# workflow stage the failure originated from (None when not applicable).
ERROR_CODE_MAP: dict[str, dict] = {
    "ai_timeout": {"retryable": True, "stage": "analyzing"},
    "ai_auth_failed": {"retryable": False, "stage": "analyzing"},
    "ai_network_error": {"retryable": True, "stage": "analyzing"},
    "ai_invalid_output": {"retryable": False, "stage": "analyzing"},
    "ai_uncertain": {"retryable": True, "stage": "evaluating"},
    "evidence_reference_invalid": {"retryable": False, "stage": "analyzing"},
    "input_incomplete": {"retryable": False, "stage": None},
    "verification_error": {"retryable": True, "stage": "evaluating"},
    "ai_unavailable": {"retryable": True, "stage": "analyzing"},
    "snapshot_unavailable": {"retryable": True, "stage": "evaluating"},
    "snapshot_expired": {"retryable": False, "stage": "evaluating"},
    "experience_level_conflict": {"retryable": False, "stage": "evaluating"},
    "hard_rule_unknown": {"retryable": False, "stage": "evaluating"},
    "state_conflict": {"retryable": False, "stage": None},
    "not_found": {"retryable": False, "stage": None},
    "cancelled": {"retryable": False, "stage": None},
}

DEFAULT_USER_MESSAGES: dict[str, str] = {
    "ai_timeout": "AI 服务响应超时，请稍后重试。",
    "ai_auth_failed": "AI 凭据无效或已过期，请在设置中检查。",
    "ai_network_error": "无法连接 AI 服务，请检查网络后重试。",
    "ai_invalid_output": "AI 返回内容无法解析，请重试或更换模型。",
    "ai_uncertain": "AI 评估置信度不足，已转入待确认。",
    "evidence_reference_invalid": "AI 引用的证据不存在，已拒绝该结果。",
    "input_incomplete": "输入信息不完整，请补充必要字段。",
    "verification_error": "校验过程发生错误，请重试。",
    "ai_unavailable": "AI 服务当前不可用，已降级处理。",
    "snapshot_unavailable": "岗位详情不可用，已转入待确认。",
    "snapshot_expired": "岗位详情已失效，已转入待确认。",
    "experience_level_conflict": "岗位级别与候选人经历存在明显冲突，已转入待确认。",
    "hard_rule_unknown": "岗位硬条件缺少可验证字段，已转入待确认。",
    "state_conflict": "当前状态不支持该操作。",
    "not_found": "请求的资源不存在。",
    "cancelled": "操作已取消。",
}


class DiscoveryError(Exception):
    """Domain error carrying a safe error code, stage and retryable flag.

    The error message stored on the exception is for logs only; the public
    ``user_message`` is what surfaces to the browser via ``to_envelope()``.
    """

    def __init__(
        self,
        error_code: str,
        *,
        stage: str | None = None,
        retryable: bool | None = None,
        user_message: str | None = None,
        log_detail: str | None = None,
    ):
        if error_code not in ERROR_CODE_MAP:
            # Unknown codes are coerced to verification_error to avoid
            # leaking internal taxonomy through the API surface.
            error_code = "verification_error"
        mapping = ERROR_CODE_MAP[error_code]
        self.error_code = error_code
        self.stage = stage if stage is not None else mapping["stage"]
        self.retryable = retryable if retryable is not None else mapping["retryable"]
        self.user_message = user_message or DEFAULT_USER_MESSAGES.get(error_code, "发生未知错误。")
        self.log_detail = log_detail
        super().__init__(log_detail or self.user_message)

    def to_envelope(self) -> dict:
        """Return the openapi Error schema dict."""
        return {
            "error_code": self.error_code,
            "user_message": self.user_message,
            "stage": self.stage,
            "retryable": self.retryable,
        }


class AISecurityError(DiscoveryError):
    """Raised when an AI response fails contract/security validation.

    Maps to ``ai_invalid_output`` (non-retryable) by default to prevent
    silent persistence of unvalidated model output. Callers may override
    the code when the failure is environmental (timeout/network).
    """

    def __init__(self, error_code: str = "ai_invalid_output", **kwargs):
        kwargs.setdefault("stage", "analyzing")
        super().__init__(error_code, **kwargs)


# ---------------------------------------------------------------------------
# T031/T032: Search plan compilation
# ---------------------------------------------------------------------------


def _input_hash(payload: Any) -> str:
    """Stable input hash for checkpoint integrity (not a security hash)."""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


MAX_GLOBAL_SEARCH_ITEMS = 12


def compile_search_plan(confirmation: dict) -> dict:
    """Compile a confirmation into a de-duplicated multi-direction search plan.

    - Each direction contributes at most ``MAX_SEARCH_TERMS`` terms.
    - Global de-duplication keeps at most ``MAX_GLOBAL_SEARCH_ITEMS`` items.
    - Shared terms retain multi-direction attribution.
    - Every enabled direction gets at least one item.
    - ``hard_constraints`` propagate to each item as boundaries.
    - Returns ``{items, detail_budget, hard_constraints, input_hash}``.

    Raises :class:`DiscoveryError` (input_incomplete) when the confirmation
    has no enabled directions.
    """
    if not isinstance(confirmation, dict):
        raise DiscoveryError("input_incomplete", user_message="确认信息无效。")
    enabled_directions = confirmation.get("enabled_directions", []) or []
    if not enabled_directions:
        raise DiscoveryError("input_incomplete", user_message="未启用任何方向。")
    hard_constraints = confirmation.get("hard_constraints", {}) or {}
    safe_limits = confirmation.get("safe_limits", {}) or {}
    detail_budget = int(safe_limits.get("max_details", 60))

    seen_terms: dict[str, list[str]] = {}
    items: list[dict] = []
    for direction in enabled_directions:
        direction_id = direction.get("id") or direction.get("direction_id", "")
        if not direction_id:
            continue
        terms = list(direction.get("search_terms", []))[:MAX_SEARCH_TERMS]
        if not terms:
            # Fall back to the direction name itself.
            name = direction.get("name", "").strip()
            if name:
                terms = [name]
        if not terms:
            continue
        for term in terms:
            if term not in seen_terms:
                if len(items) >= MAX_GLOBAL_SEARCH_ITEMS:
                    break
                seen_terms[term] = [direction_id]
                items.append({
                    "term": term,
                    "direction_ids": [direction_id],
                    "hard_constraints": dict(hard_constraints),
                    "status": "pending",
                    "page_cursor": 0,
                })
            else:
                if direction_id not in seen_terms[term]:
                    seen_terms[term].append(direction_id)
                # Update existing item's direction attribution.
                for item in items:
                    if item["term"] == term:
                        if direction_id not in item["direction_ids"]:
                            item["direction_ids"].append(direction_id)
                        break

    if not items:
        raise DiscoveryError("input_incomplete", user_message="无法生成搜索项。")

    # Ensure every enabled direction has at least one item.
    covered = {did for item in items for did in item["direction_ids"]}
    for direction in enabled_directions:
        direction_id = direction.get("id") or direction.get("direction_id", "")
        if direction_id and direction_id not in covered:
            raise DiscoveryError(
                "input_incomplete",
                user_message=f"方向 {direction_id} 未分配到搜索项。",
            )

    plan = {
        "items": items,
        "detail_budget": detail_budget,
        "hard_constraints": dict(hard_constraints),
        "input_hash": _input_hash({
            "items": [{k: v for k, v in item.items() if k != "status"} for item in items],
            "detail_budget": detail_budget,
            "hard_constraints": dict(hard_constraints),
        }),
    }
    return plan


# ---------------------------------------------------------------------------
# T039/T040: Job detail snapshot completeness
# ---------------------------------------------------------------------------

REQUIRED_SNAPSHOT_FIELDS = ("title", "company", "jd")
SNAPSHOT_COMPLETENESS = ("complete", "partial", "unavailable", "expired")
SNAPSHOT_SOURCE_STATUS = ("active", "unknown", "closed", "unreachable")


def build_snapshot(job: dict, detail: dict | None) -> dict:
    """Build a job detail snapshot with completeness and missing_fields.

    - ``complete``: title/company/jd all present and non-empty.
    - ``partial``: at least one required field present but some missing.
    - ``unavailable``: only title or nothing usable.
    - ``expired``: source reports the job has been removed.
    """
    if not isinstance(job, dict):
        raise DiscoveryError("input_incomplete", user_message="岗位数据无效。")
    detail = detail or {}
    merged = {**job, **{k: v for k, v in detail.items() if v}}
    missing_fields: list[str] = []
    for field in REQUIRED_SNAPSHOT_FIELDS:
        value = merged.get(field)
        if not value or not str(value).strip():
            missing_fields.append(field)

    source_status = "active"
    if detail.get("expired") or detail.get("source_status") == "expired":
        source_status = "closed"
        completeness = "expired"
    elif not missing_fields:
        completeness = "complete"
    elif len(missing_fields) >= len(REQUIRED_SNAPSHOT_FIELDS) - 1:
        # Spec: "only title or nothing usable" -> unavailable.
        completeness = "unavailable"
        source_status = "unreachable"
    else:
        completeness = "partial"
        source_status = "unknown"

    # content_hash over the merged fields actually present
    content_blob = json.dumps(
        {k: merged.get(k, "") for k in REQUIRED_SNAPSHOT_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
    )
    content_hash = hashlib.sha256(content_blob.encode("utf-8")).hexdigest()

    return {
        "job_id": str(job.get("job_id", "")),
        "title": merged.get("title", ""),
        "company": merged.get("company", ""),
        "jd": merged.get("jd", ""),
        "salary": merged.get("salary", ""),
        "location": merged.get("location", ""),
        "tags": merged.get("tags", ""),
        "completeness": completeness,
        "missing_fields": missing_fields,
        "source_status": source_status,
        "content_hash": content_hash,
        "fields": {k: merged.get(k, "") for k in ("title", "company", "jd", "salary", "location", "tags")},
    }


# ---------------------------------------------------------------------------
# T041/T042: Assessment policy (tri-state hard rules + AI -> category)
# ---------------------------------------------------------------------------

EVALUATION_POLICY_VERSION = "v1"
JOB_CATEGORIES = ("high_match", "adjacent_match", "growth_match", "needs_review", "not_suitable")

_ENTRY_LEVEL_TITLE_TERMS = ("实习", "校招", "应届", "在校生", "毕业生")
_ENTRY_LEVEL_JD_TERMS = ("面向在校生", "应届生", "校招", "实习岗位", "实习生")


def _has_substantial_experience(candidate_profile: dict | None) -> bool:
    """Return whether the candidate profile signals at least two years' work."""
    if not isinstance(candidate_profile, dict):
        return False
    years = candidate_profile.get("years_experience")
    if isinstance(years, (int, float)) and not isinstance(years, bool) and years >= 2:
        return True
    text = " ".join(
        str(candidate_profile.get(key, "") or "")
        for key in ("experience_level", "headline", "summary")
    )
    if any(term in text for term in ("多年", "高级", "资深")):
        return True
    matches = re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:年|years?)", text, re.IGNORECASE)
    return any(float(value) >= 2 for value in matches)


def _has_entry_level_job(snapshot: dict) -> bool:
    """Return whether title/JD explicitly signals an entry-level role."""
    fields = snapshot.get("fields") if isinstance(snapshot, dict) else {}
    if not isinstance(fields, dict):
        fields = snapshot if isinstance(snapshot, dict) else {}
    title = str(fields.get("title", "") or "")
    jd = str(fields.get("jd", "") or "")
    return (
        any(term in title for term in _ENTRY_LEVEL_TITLE_TERMS)
        or any(term in jd for term in _ENTRY_LEVEL_JD_TERMS)
    )


def assess_job_direction(
    snapshot: dict,
    direction: dict,
    ai_proposal: dict | None,
    *,
    hard_constraints: dict | None = None,
    candidate_profile: dict | None = None,
) -> dict:
    """Assess a job against a direction: tri-state hard rules + AI contract.

    Returns a dict with ``category``, ``policy_version``, ``hard_rule_outcome``,
    ``ai_assessment`` (sanitized) and ``gaps``.

    Category derivation:
      - hard rule ``violation`` -> ``not_suitable``
      - hard rule ``unknown`` OR snapshot incomplete/unavailable -> ``needs_review``
      - AI ``needs_review`` (low confidence / invalid) -> ``needs_review``
      - AI valid + proposed_band ``high`` + all dims >= MIN_DIMENSION_SCORE
        + match_score >= MIN_MATCH_SCORE -> ``high_match``
      - proposed_band ``adjacent`` -> ``adjacent_match``
      - proposed_band ``growth`` -> ``growth_match``
      - proposed_band ``unsuitable`` -> ``not_suitable``
      - an entry-level job conflicting with substantial experience prevents
        ``high_match`` and ``adjacent_match``
    """
    from webui.screening import verify_hard_rules_tri_state
    from webui.semantic import (
        DIMENSIONS,
        MIN_CONFIDENCE,
        MIN_DIMENSION_SCORE,
        MIN_MATCH_SCORE,
        validate_job_assessment,
    )

    # T116: snapshot 可能是扁平结构（含 title/company/jd 顶层字段）
    # 或 contract view（字段在 snapshot["fields"] 中）
    snap_fields = snapshot.get("fields") if isinstance(snapshot, dict) and isinstance(snapshot.get("fields"), dict) else snapshot
    hard = verify_hard_rules_tri_state(
        {**{k: v for k, v in snap_fields.items() if k in ("title", "company", "jd", "salary", "location", "tags")},
         **({"company_scale": snap_fields.get("company_scale", ""),
             "company_stage": snap_fields.get("company_stage", ""),
             "company_industry": snap_fields.get("company_industry", "")} if snap_fields else {})},
        hard_constraints or {},
    )
    hard_outcome = hard["outcome"]

    snapshot_completeness = snapshot.get("completeness", "unavailable") if isinstance(snapshot, dict) else "unavailable"

    if hard_outcome == "violation":
        return {
            "category": "not_suitable",
            "policy_version": EVALUATION_POLICY_VERSION,
            "hard_rule_outcome": hard_outcome,
            "hard_rule_checks": hard["checks"],
            "ai_assessment": None,
            "gaps": [],
        }

    if hard_outcome == "unknown" or snapshot_completeness in ("unavailable", "expired"):
        # Hard rules unknown OR snapshot unavailable/expired -> cannot confidently
        # assign to high_match; route to needs_review. Partial snapshots may still
        # be assessed into adjacent/growth (M1: per data-model.md:252).
        return {
            "category": "needs_review",
            "policy_version": EVALUATION_POLICY_VERSION,
            "hard_rule_outcome": hard_outcome,
            "hard_rule_checks": hard["checks"],
            "ai_assessment": None,
            "gaps": [],
            "reason": f"snapshot_{snapshot_completeness}" if hard_outcome == "pass" else f"hard_rule_{hard_outcome}",
        }

    # AI proposal is required for confident categorization.
    if not ai_proposal:
        return {
            "category": "needs_review",
            "policy_version": EVALUATION_POLICY_VERSION,
            "hard_rule_outcome": hard_outcome,
            "hard_rule_checks": hard["checks"],
            "ai_assessment": None,
            "gaps": [],
            "reason": "ai_unavailable",
        }

    # Validate the AI proposal against the contract.
    analysis_evidence_ids = set(direction.get("analysis_evidence_ids", []) or [])
    direction_evidence_ids = set(direction.get("evidence_refs", []) or [])
    snapshot_fields = set(snapshot.get("fields", {}).keys()) if isinstance(snapshot, dict) else set()
    validated = validate_job_assessment(
        ai_proposal,
        analysis_evidence_ids,
        direction_evidence_ids,
        snapshot_fields,
    )

    if validated.get("failure_stage"):
        return {
            "category": "needs_review",
            "policy_version": EVALUATION_POLICY_VERSION,
            "hard_rule_outcome": hard_outcome,
            "hard_rule_checks": hard["checks"],
            "ai_assessment": validated,
            "gaps": validated.get("gaps", []),
            "reason": validated["failure_stage"],
        }

    # Apply program policy (proposed_band is advisory).  The level-conflict
    # guard is deliberately program-owned so a model cannot override it.
    proposed_band = validated.get("proposed_band", "uncertain")
    match_score = validated.get("match_score", 0)
    dimensions = validated.get("dimensions", {})
    all_dims_pass = all(
        dim.get("score", 0) >= MIN_DIMENSION_SCORE for dim in dimensions.values()
    ) if dimensions else False

    level_conflict = False
    if (
        _has_substantial_experience(candidate_profile)
        and _has_entry_level_job(snapshot)
        and proposed_band in {"high", "adjacent"}
    ):
        category = "needs_review"
        level_conflict = True
    elif proposed_band == "high" and match_score >= MIN_MATCH_SCORE and all_dims_pass:
        category = "high_match"
    elif proposed_band == "adjacent" and match_score >= MIN_MATCH_SCORE and all_dims_pass:
        # M2: adjacent also requires dimension verification, not just AI advisory.
        category = "adjacent_match"
    elif proposed_band == "growth" and all_dims_pass:
        # M2: growth requires dimensions pass but allows lower match_score.
        category = "growth_match"
    elif proposed_band == "unsuitable":
        category = "not_suitable"
    else:
        category = "needs_review"

    return {
        "category": category,
        "policy_version": EVALUATION_POLICY_VERSION,
        "hard_rule_outcome": hard_outcome,
        "hard_rule_checks": hard["checks"],
        "ai_assessment": validated,
        "gaps": validated.get("gaps", []),
        **({"reason": "experience_level_conflict"} if level_conflict else {}),
    }


# ---------------------------------------------------------------------------
# T043/T044: Portfolio assembly
# ---------------------------------------------------------------------------

CATEGORY_PRIORITY = {
    "high_match": 0,
    "adjacent_match": 1,
    "growth_match": 2,
    "needs_review": 3,
    "not_suitable": 4,
}


def build_portfolio(
    run_id: str,
    assessments: list[dict],
    directions: list[dict],
    *,
    resume_id: str = "",
    analysis_id: str = "",
    confirmation_id: str = "",
) -> dict:
    """Assemble direction-aware job portfolio from assessments.

    - Groups by direction_id + category.
    - Selects primary assessment per (job, direction) by category priority.
    - De-duplicates the same company within a direction (keeps best).
    - Reports per-direction counts and no-result reasons.
    - Safe explanations reference evidence IDs and safe_excerpt only.
    - Carries traceability refs: resume_id/analysis_id/confirmation_id/run_id.
    """
    directions_by_id = {d.get("id") or d.get("direction_id", ""): d for d in (directions or [])}
    normalized_assessments = [normalize_portfolio_assessment(a) for a in (assessments or [])]
    grouped: dict[tuple[str, str], list[dict]] = {}
    for assessment in normalized_assessments:
        direction_id = assessment.get("direction_id", "")
        category = assessment.get("category", "needs_review")
        grouped.setdefault((direction_id, category), []).append(assessment)

    items: list[dict] = []
    counts: dict[str, int] = {cat: 0 for cat in JOB_CATEGORIES}
    direction_results: dict[str, dict] = {}
    seen_companies_per_direction: dict[str, set[str]] = {}

    for (direction_id, category) in sorted(grouped.keys(), key=lambda k: CATEGORY_PRIORITY.get(k[1], 99)):
        group = grouped[(direction_id, category)]
        # Sort by match_score desc (None last)
        group.sort(key=lambda a: (a.get("ai_assessment") or {}).get("match_score") or 0, reverse=True)
        for assessment in group:
            company = assessment.get("company", "")
            if direction_id not in seen_companies_per_direction:
                seen_companies_per_direction[direction_id] = set()
            if company and company in seen_companies_per_direction[direction_id]:
                continue
            seen_companies_per_direction[direction_id].add(company)
            item = _build_portfolio_item(assessment, direction_id, category, run_id)
            items.append(item)
            counts[category] = counts.get(category, 0) + 1

    # Ensure every direction appears in direction_results
    for direction_id, direction in directions_by_id.items():
        direction_assessments = [a for a in normalized_assessments if a.get("direction_id") == direction_id]
        if not direction_assessments:
            direction_results[direction_id] = {
                "name": direction.get("name", ""),
                "counts": {cat: 0 for cat in JOB_CATEGORIES},
                "reason": "not_found",
            }
        else:
            dir_counts: dict[str, int] = {cat: 0 for cat in JOB_CATEGORIES}
            for a in direction_assessments:
                dir_counts[a.get("category", "needs_review")] = dir_counts.get(a.get("category", "needs_review"), 0) + 1
            result = {
                "name": direction.get("name", ""),
                "counts": dir_counts,
            }
            if not any(dir_counts.get(cat, 0) for cat in ("high_match", "adjacent_match", "growth_match")):
                result["reason"] = _portfolio_no_result_reason(direction_assessments)
            direction_results[direction_id] = result

    return {
        "run_id": run_id,
        "resume_id": resume_id,
        "analysis_id": analysis_id,
        "confirmation_id": confirmation_id,
        "items": items,
        "counts": counts,
        "directions": direction_results,
        "policy_version": EVALUATION_POLICY_VERSION,
    }


def normalize_portfolio_assessment(assessment: dict) -> dict:
    """Normalize both service-layer and persisted assessment shapes.

    The evaluator returns ``ai_assessment`` while ``TaskStore`` persists its
    fields at the row's top level.  Portfolio policy must see the same evidence
    and scores on both paths.
    """
    normalized = dict(assessment)
    ai = assessment.get("ai_assessment") or {}
    normalized["hard_rule_outcome"] = assessment.get(
        "hard_rule_outcome", assessment.get("hard_outcome", "unknown")
    )
    normalized["match_score"] = ai.get("match_score", assessment.get("match_score"))
    normalized["confidence"] = ai.get("confidence", assessment.get("confidence"))
    normalized["dimensions"] = ai.get("dimensions") or assessment.get("dimensions") or {}
    normalized["gaps"] = ai.get("gaps") or assessment.get("gaps") or []

    category = assessment.get("category", "needs_review")
    completeness = assessment.get("snapshot_completeness", assessment.get("completeness", "unavailable"))
    dimensions = normalized["dimensions"]
    two_sided_evidence = bool(dimensions) and all(
        dim.get("candidate_evidence_refs") and dim.get("job_evidence_refs")
        for dim in dimensions.values()
    )
    recommended = category in {"high_match", "adjacent_match", "growth_match"}
    invalid_recommendation = recommended and (
        normalized["hard_rule_outcome"] != "pass" or not two_sided_evidence
    )
    if category == "high_match" and completeness != "complete":
        invalid_recommendation = True
    if category == "growth_match" and not normalized["gaps"]:
        invalid_recommendation = True
    if invalid_recommendation:
        category = "needs_review"
        normalized.setdefault("portfolio_guard_reason", "recommendation_evidence_incomplete")
    normalized["category"] = category
    normalized["snapshot_completeness"] = completeness
    return normalized


def _portfolio_no_result_reason(assessments: list[dict]) -> str:
    if any(a.get("failure_code") for a in assessments):
        return "execution_failed"
    if any(a.get("hard_rule_outcome") == "violation" for a in assessments):
        return "hard_constraints_excluded"
    if any(a.get("snapshot_completeness") != "complete" for a in assessments):
        return "insufficient_detail"
    return "insufficient_match"


def _build_portfolio_item(assessment: dict, direction_id: str, category: str, run_id: str) -> dict:
    return {
        "run_id": run_id,
        "direction_id": direction_id,
        "job_id": assessment.get("job_id", ""),
        "title": assessment.get("title", ""),
        "company": assessment.get("company", ""),
        "salary": assessment.get("salary", ""),
        "location": assessment.get("location", ""),
        "category": category,
        "match_score": assessment.get("match_score"),
        "confidence": assessment.get("confidence"),
        "explanation": build_safe_explanation(assessment),
        "snapshot_completeness": assessment.get("snapshot_completeness", "unavailable"),
    }


# ---------------------------------------------------------------------------
# T045/T046: Safe explanation generation
# ---------------------------------------------------------------------------


def build_safe_explanation(assessment: dict) -> dict:
    """Build an explanation that references evidence IDs + safe_excerpt only.

    Never includes resume body text, raw model responses, or PII. Gaps are
    surfaced as plain text without leaking the JD body.
    """
    ai = assessment.get("ai_assessment") or {}
    dimensions = ai.get("dimensions") or assessment.get("dimensions") or {}
    safe_dims: list[dict] = []
    for name, dim in dimensions.items():
        safe_dims.append({
            "dimension": name,
            "score": dim.get("score"),
            "candidate_evidence_refs": list(dim.get("candidate_evidence_refs", [])),
            "job_evidence_refs": list(dim.get("job_evidence_refs", [])),
        })
    gaps = []
    for gap in ai.get("gaps") or assessment.get("gaps") or []:
        gaps.append({"text": redact_pii(gap.get("text", "")), "job_evidence_refs": list(gap.get("job_evidence_refs", []))})
    transferable = [
        {
            "dimension": dim["dimension"],
            "candidate_evidence_refs": dim["candidate_evidence_refs"],
        }
        for dim in safe_dims if dim["candidate_evidence_refs"]
    ]
    differences = list(gaps)
    if not differences:
        differences = [
            {"dimension": dim["dimension"], "job_evidence_refs": dim["job_evidence_refs"]}
            for dim in safe_dims if dim["job_evidence_refs"]
        ]
    return {
        "dimensions": safe_dims,
        "transferable": transferable,
        "differences": differences,
        "gaps": gaps,
        "hard_rule_outcome": assessment.get("hard_rule_outcome"),
        "policy_version": EVALUATION_POLICY_VERSION,
    }


# ---------------------------------------------------------------------------
# T026/T027: confirm_directions application service
# ---------------------------------------------------------------------------


def confirm_directions(
    store,
    analysis_id: str,
    enabled_direction_ids: list[str],
    *,
    hard_constraints: dict | None = None,
    soft_preferences: dict | None = None,
    safe_limits: dict | None = None,
    user_directions: list[dict] | None = None,
) -> dict:
    """Freeze enabled directions and current user intent into an immutable
    confirmation version.

    - Analysis must be ``ready``.
    - At least one direction must be enabled.
    - Enabled directions must belong to this analysis.
    - Hard constraints only include fields the user explicitly set.
    - Editing creates a new version; old versions remain immutable.
    """
    try:
        analysis = store.get_analysis(analysis_id)
    except KeyError:
        raise DiscoveryError("not_found", user_message="分析不存在。")
    if analysis.get("status") != "ready":
        raise DiscoveryError(
            "state_conflict",
            user_message=f"分析状态为 {analysis.get('status')}，无法确认。",
            stage="analyzing",
        )
    if not enabled_direction_ids:
        raise DiscoveryError("input_incomplete", user_message="至少需要启用一个方向。")

    analysis_directions = store.list_directions(analysis_id)
    analysis_direction_ids = {d["id"] for d in analysis_directions}
    for direction_id in enabled_direction_ids:
        if direction_id not in analysis_direction_ids:
            raise DiscoveryError(
                "state_conflict",
                user_message=f"方向 {direction_id} 不属于该分析。",
            )

    # Only keep hard constraints the user explicitly set (non-empty).
    clean_hard = {}
    for key, value in (hard_constraints or {}).items():
        if value not in (None, "", []):
            clean_hard[key] = value

    user_added_ids = {d.get("id") for d in (user_directions or [])}
    directions_payload = [
        {
            "direction_id": did,
            "enabled": True,
            "user_added": did in user_added_ids,
            "user_label": None,
        }
        for did in enabled_direction_ids
    ]
    confirmation = store.create_confirmation(
        profile_id=analysis["profile_id"],
        resume_id=analysis["resume_id"],
        analysis_id=analysis_id,
        hard_constraints=clean_hard,
        soft_preferences=dict(soft_preferences or {}),
        safe_limits=dict(safe_limits or {}),
        directions=directions_payload,
    )
    return confirmation


# ---------------------------------------------------------------------------
# T024/T025: analyze_resume application service
# ---------------------------------------------------------------------------


def analyze_resume(
    store,
    resume_id: str,
    *,
    ai_consent: bool,
    ai_provider=None,
    model_name: str = "",
    analysis_id: str = "",
) -> dict:
    """Orchestrate candidate analysis: consent -> read resume -> AI -> validate -> persist.

    - ``ai_consent`` False: only local validation, no remote AI call. The
      analysis is created in ``queued`` status and never transitions to
      ``ready`` without consent.
    - Empty resume text blocks the analysis (``input_incomplete``).
    - AI contract failures raise :class:`AISecurityError` and persist the
      analysis as ``failed`` with a failure_code.
    - Raw model responses are never persisted.
    - T109: If ``analysis_id`` is provided, use that existing analysis
      (created by the HTTP route) instead of creating a new one. This
      enables the async submit pattern: route creates queued attempt,
      runtime calls analyze_resume with analysis_id to do the AI work.
    """
    if not resume_id:
        raise DiscoveryError("input_incomplete", user_message="缺少简历标识。")
    try:
        resume = store.get_resume(resume_id)
    except KeyError:
        raise DiscoveryError("not_found", user_message="简历不存在。")
    resume_text = (resume.get("extracted_text") or "").strip()
    profile_id = resume.get("profile_id")
    if not profile_id:
        raise DiscoveryError("input_incomplete", user_message="简历未关联候选人档案。")

    if analysis_id:
        # T109: Use existing analysis created by the HTTP route.
        analysis = store.get_analysis(analysis_id)
    else:
        analysis = store.create_analysis(
            resume_id, profile_id,
            model_name=model_name, contract_version="v2",
        )

    if not resume_text:
        store.update_analysis_status(
            analysis["id"], "failed", failure_code="input_incomplete",
        )
        raise DiscoveryError("input_incomplete", user_message="简历正文为空，无法分析。")

    if not ai_consent:
        # Without consent we never call the remote AI. Leave analysis queued.
        return store.get_analysis(analysis["id"])

    if ai_provider is None:
        store.update_analysis_status(
            analysis["id"], "failed", failure_code="ai_unavailable",
        )
        raise DiscoveryError("ai_unavailable", user_message="AI 服务未配置。")

    store.update_analysis_status(analysis["id"], "analyzing")

    try:
        raw = ai_provider.analyze(resume_text=resume_text)
    except TimeoutError:
        store.update_analysis_status(analysis["id"], "failed", failure_code="ai_timeout")
        raise DiscoveryError("ai_timeout")
    except ConnectionError:
        store.update_analysis_status(analysis["id"], "failed", failure_code="ai_network_error")
        raise DiscoveryError("ai_network_error")
    except AIProviderError as exc:
        # T111: webui.ai.AISecurityError — provider 抛出，已携带 feature-safe 码
        # （ai_timeout/ai_auth_failed/ai_network_error/ai_invalid_output）。
        # 重新包装为 webui.discovery.AISecurityError 以保持调用方契约
        # （_safe_execute_analysis 捕获 DiscoveryError/AISecurityError）。
        code = exc.error_code if exc.error_code in ERROR_CODE_MAP else "ai_invalid_output"
        store.update_analysis_status(analysis["id"], "failed", failure_code=code)
        raise AISecurityError(code) from None
    except AISecurityError as exc:
        # webui.discovery.AISecurityError — 本地抛出（如 validate_candidate_analysis
        # 失败）。保留 provider/本地映射后的 feature-safe error_code。
        code = exc.error_code if exc.error_code in ERROR_CODE_MAP else "ai_invalid_output"
        store.update_analysis_status(analysis["id"], "failed", failure_code=code)
        raise
    except Exception as exc:  # noqa: BLE001 - provider adapter boundary
        store.update_analysis_status(analysis["id"], "failed", failure_code="ai_invalid_output")
        raise AISecurityError("ai_invalid_output", log_detail=str(exc))

    try:
        validated = validate_candidate_analysis(raw, resume_text)
    except ValueError as exc:
        store.update_analysis_status(
            analysis["id"], "failed", failure_code="ai_invalid_output",
        )
        raise AISecurityError("ai_invalid_output", log_detail=str(exc))

    # Normalize and merge evidence/directions.
    normalized_evidence = normalize_evidence(validated["evidence"], resume_text)
    merged_directions = merge_directions(validated["directions"])
    evidence_by_id = {e["id"]: e for e in normalized_evidence}
    final_directions = enforce_direction_policy(merged_directions, evidence_by_id)

    # Persist evidence.
    evidence_id_map: dict[str, str] = {}
    for item in normalized_evidence:
        stored = store.add_evidence(
            analysis["id"],
            item["evidence_type"],
            item["normalized_value"],
            safe_excerpt=item.get("safe_excerpt", ""),
            source_locator=item.get("source_locator"),
            assertion_type=item.get("assertion_type", "explicit"),
            confidence=item.get("confidence", 0),
            sensitive=item.get("sensitive", False),
        )
        evidence_id_map[item["id"]] = stored["id"]

    # Persist directions and link evidence.
    for direction in final_directions:
        stored_dir = store.add_direction(
            analysis["id"],
            direction["name"],
            direction["type"],
            rationale=direction.get("rationale", ""),
            gaps=direction.get("gaps", []),
            confidence=direction.get("confidence", 0),
            default_enabled=direction.get("default_enabled", False),
            search_terms=direction.get("search_terms", []),
        )
        for ref in direction.get("evidence_refs", []):
            actual_eid = evidence_id_map.get(ref, ref)
            store.link_direction_evidence(stored_dir["id"], actual_eid)

    # HI-2: redact PII from summary string fields before persisting (FR-066).
    raw_summary = validated["summary"]
    redacted_summary = {
        k: redact_pii(v) if isinstance(v, str)
        else [redact_pii(item) if isinstance(item, str) else item for item in v]
        if isinstance(v, list) else v
        for k, v in raw_summary.items()
    }
    store.update_analysis_status(
        analysis["id"], "ready",
        summary=redacted_summary,
        unknowns=validated["unknowns"],
    )
    return store.get_analysis(analysis["id"])


# ---------------------------------------------------------------------------
# T066/T067: Run completion calculation
# ---------------------------------------------------------------------------


def calculate_run_completion(run: dict, plan_items: list[dict], assessments: list[dict]) -> dict:
    """Determine terminal status for a discovery run.

    - All plan items terminal + no blockers + at least one usable result
      -> ``succeeded``.
    - Some usable results but remaining items cannot be completed -> ``partial``.
    - No usable results and all items blocked -> ``failed``.
    """
    if not isinstance(run, dict):
        raise DiscoveryError("input_incomplete", user_message="运行信息无效。")
    terminal_items = {"completed", "failed", "cancelled", "skipped"}
    pending_items = [item for item in (plan_items or []) if item.get("status") not in terminal_items]
    completed_items = [item for item in (plan_items or []) if item.get("status") == "completed"]
    blocked_items = [item for item in (plan_items or []) if item.get("status") == "failed"]

    usable_categories = {"high_match", "adjacent_match", "growth_match"}
    usable_results = [a for a in (assessments or []) if a.get("category") in usable_categories]

    if pending_items:
        return {
            "status": run.get("status"),
            "reason": "items_pending",
            "pending_count": len(pending_items),
        }

    if usable_results:
        if blocked_items:
            return {"status": "partial", "reason": "some_branches_blocked", "usable_count": len(usable_results)}
        return {"status": "succeeded", "reason": "all_complete", "usable_count": len(usable_results)}

    if blocked_items and not completed_items:
        return {"status": "failed", "reason": "all_blocked", "blocked_count": len(blocked_items)}

    if blocked_items:
        return {
            "status": "failed",
            "reason": "some_branches_blocked_no_usable",
            "blocked_count": len(blocked_items),
        }

    # Completed items but no usable results (all needs_review / not_suitable)
    if completed_items:
        return {"status": "succeeded", "reason": "no_usable_results", "usable_count": 0}

    return {"status": "failed", "reason": "no_results", "blocked_count": len(blocked_items)}


# ---------------------------------------------------------------------------
# T058: Feedback application to next run
# ---------------------------------------------------------------------------


def apply_feedback_to_next_run(
    store,
    confirmation: dict,
    *,
    profile_id: str,
) -> dict:
    """Adjust a confirmation based on prior feedback before starting a run.

    - Direction-disable feedback removes a direction from enabled list.
    - Job not_interested feedback does NOT expand to company-wide exclusion
      (per spec: only the exact job is excluded).
    - History run snapshots are immutable; feedback only affects future runs.
    - Effective feedback only (revoked_at IS NULL).
    """
    try:
        feedbacks = store.list_discovery_feedback(profile_id, effective_only=True)
    except (TypeError, AttributeError):
        # Backwards-compatible fallback for older store signatures.
        feedbacks = store.list_discovery_feedback(profile_id) if hasattr(store, "list_discovery_feedback") else []
    disabled_direction_ids = {
        f.get("direction_id")
        for f in feedbacks
        if f.get("target_type") == "direction"
        and f.get("action") == "direction_disable"
        and f.get("direction_id")
    }
    excluded_job_ids = {
        f.get("job_id")
        for f in feedbacks
        if f.get("target_type") == "job"
        and f.get("action") == "not_interested"
        and f.get("job_id")
    }
    enabled = [
        d for d in confirmation.get("enabled_directions", [])
        if (d.get("id") or d.get("direction_id", "")) not in disabled_direction_ids
    ]
    if not enabled:
        raise DiscoveryError(
            "input_incomplete",
            user_message="所有方向已被反馈禁用，请至少启用一个方向。",
        )
    adjusted = dict(confirmation)
    adjusted["enabled_directions"] = enabled
    adjusted["excluded_job_ids"] = list(excluded_job_ids)
    return adjusted
