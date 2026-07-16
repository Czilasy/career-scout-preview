"""Candidate analysis domain (feature 004).

Hosts resume evidence normalization, career direction merging and the
candidate analysis AI contract validation. All functions here operate on
sanitized data only; raw model output and full resume text are never
persisted or returned to the browser.
"""

from __future__ import annotations

import re
from typing import Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CANDIDATE_CONTRACT_VERSION = "v1"
DIRECTION_CONTRACT_VERSION = "v1"

EVIDENCE_TYPES = (
    "skill", "responsibility", "project", "industry",
    "seniority", "education", "achievement", "other",
)
ASSERTION_TYPES = ("explicit", "inferred")
DIRECTION_TYPES = ("core", "adjacent", "growth")
UNKNOWN_FIELDS = ("current_city", "min_salary", "career_intent", "other")

MAX_DIRECTIONS = 5
MAX_SEARCH_TERMS = 3
MIN_DEFAULT_ENABLED_CONFIDENCE = 60

# Sensitive patterns rejected as evidence. The patterns are intentionally
# broad: phone-like, ID-card-like, address-like, and explicit redaction
# markers used by the test fixtures.
SENSITIVE_PATTERNS = [
    re.compile(r"\b1[3-9]\d{9}\b"),                       # 11-digit CN mobile
    re.compile(r"\b\d{17}[\dXx]\b"),                      # 18-digit ID card
    re.compile(r"\b\w*[放红黄]\w*路\s*\d+号"),              # address-like
    re.compile(r"\[REDACTED-PII-[^\]]+\]"),               # explicit fixture marker
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),          # email
]


def _is_sensitive(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in SENSITIVE_PATTERNS)


def _confidence(value) -> int:
    """Validate confidence: real int/float 0-100; bool rejected."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid_confidence")
    number = float(value)
    if number < 0 or number > 100:
        raise ValueError("invalid_confidence")
    return int(number)


def _require_str(value, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid_type:{field}")
    return value


def _require_str_list(value, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"invalid_type:{field}")
    return value


def _locator_in_range(locator: dict, resume_text: str) -> bool:
    """Return True iff locator.start/end fall inside resume_text.

    Accepts page/paragraph/start/end style locators. Missing numeric
    bounds are treated as out-of-range.
    """
    if not isinstance(locator, dict):
        return False
    start = locator.get("start")
    end = locator.get("end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
        return False
    return end <= len(resume_text or "")


# ---------------------------------------------------------------------------
# T104: v2 exact-quote locator (canonicalize + resolve)
# ---------------------------------------------------------------------------

CANDIDATE_CONTRACT_VERSION_V2 = "v2"


def canonicalize_resume_text_v2(resume_text: str) -> str:
    """Canonicalize resume text for v2 exact-quote locator.

    Normalizes line endings (``\\r\\n`` / ``\\r`` → ``\\n``) so that
    offsets are stable regardless of the source platform. The returned
    text is the exact text into which Unicode code-point offsets apply.

    The function does NOT truncate, redact or alter semantic content —
    it only normalizes whitespace representation so that ``find`` and
    slicing produce consistent locators.
    """
    if not resume_text:
        return ""
    text = str(resume_text)
    # Normalize CRLF and CR to LF
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def resolve_evidence_quote(quote: str, canonical_text: str) -> dict:
    """Resolve a minimal exact ``source_quote`` against canonical resume text.

    Implements the v2 exact-quote contract:

    - Finds the **unique** precise substring match in ``canonical_text``.
    - Returns ``{"start": int, "end": int}`` where start/end are Unicode
      code-point offsets into ``canonical_text``.
    - Verifies that ``canonical_text[start:end] == quote`` before returning.
    - Rejects empty quotes, sensitive quotes, not-found quotes and
      ambiguous (multiply-occurring) quotes.
    - Never uses fuzzy matching or arbitrary first-match selection.

    Raises ``ValueError`` with a safe code on any failure.
    """
    if not isinstance(quote, str) or not quote:
        raise ValueError("evidence_quote_empty")
    if not isinstance(canonical_text, str):
        raise ValueError("evidence_quote_not_found")
    # Reject sensitive quotes before searching — a sensitive quote must
    # never be persisted as a locator even if it appears in the resume.
    if _is_sensitive(quote):
        raise ValueError("sensitive_evidence_rejected")
    # Find all exact match positions (Unicode code-point offsets).
    positions: list[int] = []
    start = 0
    while True:
        idx = canonical_text.find(quote, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1  # advance by 1 to catch overlapping occurrences
    if not positions:
        raise ValueError("evidence_quote_not_found")
    if len(positions) > 1:
        raise ValueError("evidence_quote_ambiguous")
    start_pos = positions[0]
    end_pos = start_pos + len(quote)  # len() on str == Unicode code-point count
    # Re-verify slice equality (defensive; should always hold for str.find).
    if canonical_text[start_pos:end_pos] != quote:
        raise ValueError("evidence_quote_not_found")
    return {"start": start_pos, "end": end_pos}


# ---------------------------------------------------------------------------
# Sensitive-field redaction (US5)
# ---------------------------------------------------------------------------


def redact_pii(text: str) -> str:
    """Replace sensitive substrings with a fixed marker; never returns PII."""
    if not text:
        return ""
    redacted = text
    for pattern in SENSITIVE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


# ---------------------------------------------------------------------------
# T011: Candidate analysis contract v1 validation
# ---------------------------------------------------------------------------


def validate_candidate_analysis(data, resume_text: str) -> dict:
    """Validate an AI candidate-analysis response against contract v1.

    Returns a sanitized dict with ``summary``, ``evidence``, ``unknowns``,
    ``directions``. Raises :class:`ValueError` carrying a safe code on any
    structural, reference or sensitivity failure. The raw model output is
    never echoed back.
    """
    if not isinstance(data, dict):
        raise ValueError("invalid_response")

    resume_text = resume_text or ""

    # --- summary --------------------------------------------------------
    raw_summary = data.get("summary")
    if not isinstance(raw_summary, dict):
        raise ValueError("missing_field:summary")
    summary = {
        "headline": _require_str(raw_summary.get("headline", ""), "summary.headline"),
        "experience_level": _require_str(raw_summary.get("experience_level", ""), "summary.experience_level"),
        "domains": _require_str_list(raw_summary.get("domains", []), "summary.domains"),
        "strengths": _require_str_list(raw_summary.get("strengths", []), "summary.strengths"),
    }

    # --- evidence -------------------------------------------------------
    raw_evidence = data.get("evidence")
    if not isinstance(raw_evidence, list):
        raise ValueError("missing_field:evidence")
    canonical = canonicalize_resume_text_v2(resume_text)
    evidence_by_ref: dict[str, dict] = {}
    for item in raw_evidence:
        if not isinstance(item, dict):
            raise ValueError("invalid_evidence")
        client_ref = item.get("client_ref")
        if not isinstance(client_ref, str) or not client_ref:
            raise ValueError("invalid_evidence_ref")
        if client_ref in evidence_by_ref:
            raise ValueError("duplicate_evidence_ref")
        etype = item.get("type")
        if etype not in EVIDENCE_TYPES:
            raise ValueError("invalid_evidence_type")
        normalized_value = _require_str(item.get("normalized_value", ""), "evidence.normalized_value")
        safe_excerpt = _require_str(item.get("safe_excerpt", ""), "evidence.safe_excerpt")
        if _is_sensitive(normalized_value) or _is_sensitive(safe_excerpt):
            raise ValueError("sensitive_evidence_rejected")
        locator = item.get("source_locator") or {}
        # T107/P7: v2 exact-quote locator 校验 — source_quote 必填
        if "source_quote" not in item:
            # v2 契约要求每条 evidence 必须有 source_quote（ai-contracts.md:51）
            raise ValueError("missing_field:evidence.source_quote")
        source_quote = item.get("source_quote")
        if not isinstance(source_quote, str) or not source_quote:
            raise ValueError("missing_field:evidence.source_quote")
        if _is_sensitive(source_quote):
            raise ValueError("sensitive_evidence_rejected")
        # 验证 locator 切片 == source_quote
        if not isinstance(locator, dict) or "start" not in locator or "end" not in locator:
            raise ValueError("evidence_locator_missing")
        start = locator.get("start")
        end = locator.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            raise ValueError("evidence_locator_invalid")
        if start < 0 or end <= start or end > len(canonical):
            raise ValueError("evidence_locator_out_of_range")
        if canonical[start:end] != source_quote:
            raise ValueError("evidence_locator_slice_mismatch")
        # The model-provided locator is only a claim. Re-resolve the quote
        # against the complete canonical text so a repeated phrase cannot be
        # accepted by pointing at an arbitrary occurrence.
        resolved_locator = resolve_evidence_quote(source_quote, canonical)
        if locator != resolved_locator:
            raise ValueError("evidence_locator_mismatch")
        # 验证 safe_excerpt == redact_pii(source_quote)
        expected_excerpt = redact_pii(source_quote)
        if safe_excerpt != expected_excerpt:
            raise ValueError("safe_excerpt_mismatch")
        assertion_type = item.get("assertion_type")
        if assertion_type not in ASSERTION_TYPES:
            raise ValueError("invalid_assertion_type")
        confidence = _confidence(item.get("confidence", 0))
        evidence_entry = {
            "id": client_ref,
            "type": etype,
            "normalized_value": normalized_value,
            "safe_excerpt": safe_excerpt,
            "source_locator": resolved_locator,
            "assertion_type": assertion_type,
            "confidence": confidence,
            "sensitive": False,
        }
        if source_quote is not None:
            evidence_entry["source_quote"] = source_quote
        evidence_by_ref[client_ref] = evidence_entry

    # --- unknowns -------------------------------------------------------
    raw_unknowns = data.get("unknowns")
    if not isinstance(raw_unknowns, list):
        raise ValueError("missing_field:unknowns")
    unknowns: list[dict] = []
    for item in raw_unknowns:
        if not isinstance(item, dict):
            raise ValueError("invalid_unknown")
        field = item.get("field")
        if field not in UNKNOWN_FIELDS:
            raise ValueError("invalid_unknown_field")
        message = _require_str(item.get("message", ""), "unknowns.message")
        unknowns.append({"field": field, "message": message})

    # --- directions -----------------------------------------------------
    raw_directions = data.get("directions")
    if not isinstance(raw_directions, list):
        raise ValueError("missing_field:directions")
    if len(raw_directions) > MAX_DIRECTIONS:
        raise ValueError("too_many_directions")
    directions: list[dict] = []
    direction_refs: set[str] = set()
    for item in raw_directions:
        if not isinstance(item, dict):
            raise ValueError("invalid_direction")
        client_ref = item.get("client_ref")
        if not isinstance(client_ref, str) or not client_ref:
            raise ValueError("invalid_direction_ref")
        if client_ref in direction_refs:
            raise ValueError("duplicate_direction_ref")
        direction_refs.add(client_ref)
        name = _require_str(item.get("name", ""), "direction.name")
        dtype = item.get("type")
        if dtype not in DIRECTION_TYPES:
            raise ValueError("invalid_direction_type")
        rationale = _require_str(item.get("rationale", ""), "direction.rationale")
        evidence_refs = _require_str_list(item.get("evidence_refs", []), "direction.evidence_refs")
        for ref in evidence_refs:
            if ref not in evidence_by_ref:
                raise ValueError("unknown_direction_evidence_ref")
        gaps = _require_str_list(item.get("gaps", []), "direction.gaps")
        confidence = _confidence(item.get("confidence", 0))
        default_enabled = bool(item.get("default_enabled", False))
        if default_enabled:
            if not evidence_refs or confidence < MIN_DEFAULT_ENABLED_CONFIDENCE:
                raise ValueError("default_enabled_without_gate")
        search_terms = _require_str_list(item.get("search_terms", []), "direction.search_terms")
        if len(search_terms) > MAX_SEARCH_TERMS:
            raise ValueError("too_many_search_terms")
        if not search_terms:
            raise ValueError("missing_search_terms")
        directions.append({
            "id": client_ref,
            "name": name,
            "type": dtype,
            "rationale": rationale,
            "evidence_refs": evidence_refs,
            "gaps": gaps,
            "confidence": confidence,
            "default_enabled": default_enabled,
            "search_terms": search_terms,
        })

    return {
        "summary": summary,
        "evidence": list(evidence_by_ref.values()),
        "unknowns": unknowns,
        "directions": directions,
        "contract_version": CANDIDATE_CONTRACT_VERSION_V2,
    }


# ---------------------------------------------------------------------------
# T021: Evidence normalization and dedup
# ---------------------------------------------------------------------------


def normalize_evidence(raw_evidence: Iterable[dict], resume_text: str) -> list[dict]:
    """Normalize, dedup and redact evidence items.

    Items with the same ``normalized_value`` (case-insensitive) and
    ``evidence_type`` are merged; multiple source locators are retained.
    Sensitive items are dropped. ``assertion_type`` is validated.
    Unknowns (no locator) are not fabricated as evidence.
    """
    merged: dict[tuple[str, str], dict] = {}
    for item in raw_evidence:
        if not isinstance(item, dict):
            continue
        etype = item.get("evidence_type") or item.get("type")
        if etype not in EVIDENCE_TYPES:
            continue
        normalized_value = str(item.get("normalized_value", "")).strip()
        if not normalized_value:
            continue
        if _is_sensitive(normalized_value):
            continue
        safe_excerpt = str(item.get("safe_excerpt", ""))
        if _is_sensitive(safe_excerpt):
            safe_excerpt = "[REDACTED]"
        assertion_type = item.get("assertion_type")
        if assertion_type not in ASSERTION_TYPES:
            assertion_type = "explicit"
        confidence = item.get("confidence", 0)
        try:
            confidence = _confidence(confidence)
        except ValueError:
            confidence = 0
        locator = item.get("source_locator") or item.get("source_locator_json") or {}
        if not _locator_in_range(locator, resume_text):
            # Drop items whose locator does not resolve into the resume text;
            # do not fabricate evidence for unknowns.
            continue
        key = (etype, normalized_value.lower())
        if key in merged:
            existing = merged[key]
            existing_locators = existing.get("source_locators", [existing.get("source_locator", {})])
            existing_locators.append(locator)
            existing["source_locators"] = existing_locators
            existing["confidence"] = max(existing["confidence"], confidence)
        else:
            merged[key] = {
                "id": item.get("id") or f"{etype}:{normalized_value.lower()}",
                "evidence_type": etype,
                "normalized_value": normalized_value,
                "safe_excerpt": safe_excerpt,
                "source_locator": locator,
                "source_locators": [locator],
                "assertion_type": assertion_type,
                "confidence": confidence,
                "sensitive": False,
            }
    return list(merged.values())


# ---------------------------------------------------------------------------
# T023: Direction merging and policy enforcement
# ---------------------------------------------------------------------------


# Synonymous direction names that should be merged into a single canonical
# direction. Keys and values are lower-cased for comparison.
_SYNONYMOUS_DIRECTIONS = {
    "后端开发": "后端开发工程师",
    "backend": "后端开发工程师",
    "backend engineer": "后端开发工程师",
    "前端开发": "前端开发工程师",
    "frontend": "前端开发工程师",
    "数据工程师": "数据工程师",
    "data engineer": "数据工程师",
    "风控算法": "风控算法工程师",
    "推荐算法": "推荐算法工程师",
}


def _canonical_direction_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    key = name.strip().lower()
    return _SYNONYMOUS_DIRECTIONS.get(key, name.strip())


def merge_directions(directions: Iterable[dict]) -> list[dict]:
    """Merge synonymous directions, retaining the union of search terms and
    evidence refs. Confidence takes the max; default_enabled requires the
    gate on at least one source direction.
    """
    merged: dict[str, dict] = {}
    for d in directions:
        if not isinstance(d, dict):
            continue
        canonical = _canonical_direction_name(d.get("name", ""))
        if not canonical:
            continue
        if canonical in merged:
            existing = merged[canonical]
            existing["search_terms"] = list(dict.fromkeys(
                existing.get("search_terms", []) + d.get("search_terms", [])
            ))[:MAX_SEARCH_TERMS]
            existing["evidence_refs"] = list(dict.fromkeys(
                existing.get("evidence_refs", []) + d.get("evidence_refs", [])
            ))
            existing["gaps"] = list(dict.fromkeys(
                existing.get("gaps", []) + d.get("gaps", [])
            ))
            existing["confidence"] = max(existing.get("confidence", 0), d.get("confidence", 0))
            existing["default_enabled"] = bool(existing.get("default_enabled") or d.get("default_enabled"))
        else:
            merged[canonical] = {
                "name": canonical,
                "type": d.get("type", "adjacent"),
                "rationale": d.get("rationale", ""),
                "evidence_refs": list(d.get("evidence_refs", [])),
                "gaps": list(d.get("gaps", [])),
                "confidence": d.get("confidence", 0),
                "default_enabled": bool(d.get("default_enabled", False)),
                "search_terms": list(d.get("search_terms", []))[:MAX_SEARCH_TERMS],
            }
    return list(merged.values())


def enforce_direction_policy(directions: Iterable[dict], evidence_by_id: dict[str, dict] | None = None) -> list[dict]:
    """Apply the default-enabled gate, evidence linkage and max-5 cap.

    - At most ``MAX_DIRECTIONS`` directions are returned.
    - A direction is default-enabled only if it has at least one evidence
      link and confidence >= ``MIN_DEFAULT_ENABLED_CONFIDENCE``.
    - Every default-enabled direction must have at least one evidence
      reference resolvable in *evidence_by_id* when provided.
    """
    has_evidence_map = evidence_by_id is not None
    evidence_by_id = evidence_by_id or {}
    result: list[dict] = []
    for d in directions:
        if not isinstance(d, dict):
            continue
        if has_evidence_map:
            evidence_refs = [r for r in d.get("evidence_refs", []) if r in evidence_by_id]
        else:
            evidence_refs = list(d.get("evidence_refs", []))
        confidence = d.get("confidence", 0)
        default_enabled = bool(d.get("default_enabled", False))
        if default_enabled:
            if not evidence_refs or confidence < MIN_DEFAULT_ENABLED_CONFIDENCE:
                default_enabled = False
        result.append({
            "name": d["name"],
            "type": d.get("type", "adjacent"),
            "rationale": d.get("rationale", ""),
            "evidence_refs": evidence_refs,
            "gaps": list(d.get("gaps", [])),
            "confidence": confidence,
            "default_enabled": default_enabled,
            "search_terms": list(d.get("search_terms", []))[:MAX_SEARCH_TERMS],
        })
    # Cap at MAX_DIRECTIONS, preferring default-enabled then confidence.
    result.sort(key=lambda x: (not x["default_enabled"], -x["confidence"]))
    return result[:MAX_DIRECTIONS]
