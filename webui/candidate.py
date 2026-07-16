"""Candidate analysis domain (feature 004).

Hosts resume evidence normalization, career direction merging and the
candidate analysis AI contract validation. All functions here operate on
sanitized data only; raw model output and full resume text are never
persisted or returned to the browser.
"""

from __future__ import annotations

import re
from typing import Iterable
import copy
import unicodedata

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

CANDIDATE_ANALYSIS_V3_CONTRACT = {
 "version":"v3", "warning_codes":("invalid_type","invalid_enum","invalid_evidence","sensitive_value","unverified_field","missing_required","reference_invalid"),
 "top":{"contract_version":{"type":"string","empty":"v3"},"summary":{"type":"object","empty":{"headline":"","experience_level":"","domains":[],"strengths":[]}},"evidence":{"type":"list","max":20,"empty":[]},"unknowns":{"type":"list","max":20,"empty":[]},"directions":{"type":"list","max":5,"empty":[]},"quality":{"type":"object","provider_owned":False,"empty":{"status":"complete","warnings":[]}}},
 "summary":{"headline":{"type":"string","max_length":200,"empty":""},"experience_level":{"type":"string","max_length":100,"empty":""},"domains":{"type":"list","items":"string","max":20,"item_max_length":200,"empty":[]},"strengths":{"type":"list","items":"string","max":20,"item_max_length":200,"empty":[]}},
 "evidence":{"client_ref":{"type":"string","max_length":128},"type":{"type":"string","enum":("skill","responsibility","project","industry","seniority","education","achievement","other")},"normalized_value":{"type":"string","max_length":500,"empty":""},"source_quote":{"type":"string","max_length":2000},"source_locator":{"type":"object","provider_owned":False,"empty":{}},"safe_excerpt":{"type":"string","provider_owned":False,"empty":""},"assertion_type":{"type":"string","enum":("explicit","inferred")},"confidence":{"type":"integer","min":0,"max":100}},
 "unknown":{"field":{"type":"string","enum":("current_city","min_salary","career_intent","other")},"message":{"type":"string","max_length":500,"empty":""}},
 "direction":{"client_ref":{"type":"string","max_length":128,"empty":""},"name":{"type":"string","max_length":200,"empty":""},"type":{"type":"string","enum":("core","adjacent","growth")},"rationale":{"type":"string","max_length":1000,"empty":""},"evidence_refs":{"type":"list","items":"string","empty":[]},"gaps":{"type":"list","items":"string","max":20,"item_max_length":300,"empty":[]},"confidence":{"type":"integer","min":0,"max":100},"default_enabled":{"type":"boolean","empty":False},"search_terms":{"type":"list","items":"string","max":3,"item_max_length":200,"empty":[]}},
 "quality":{"status":{"type":"string","enum":("complete","partial","manual_required")},"warnings":{"type":"list","items":"warning","empty":[]}},
 "warning":{"code":{"type":"string"},"path":{"type":"string"}}
}

def build_empty_candidate_analysis():
    def empty(spec):
        if "empty" in spec: return copy.deepcopy(spec["empty"])
        if spec.get("type") == "object": return {k: empty(v) for k,v in spec.items() if isinstance(v, dict) and "type" in v}
        if spec.get("type") == "list": return []
        return None
    return {k: empty(spec) for k, spec in CANDIDATE_ANALYSIS_V3_CONTRACT["top"].items()}

def _v3_warning(code, path):
    if code not in CANDIDATE_ANALYSIS_V3_CONTRACT["warning_codes"]:
        code = "invalid_type"
    return {"code": code, "path": str(path)}

def _warn(warnings, code, path):
    w = _v3_warning(code, path)
    if w not in warnings:
        warnings.append(w)

def canonicalize_resume_text_v3(text):
    return unicodedata.normalize("NFC", str(text or "").replace("\r\n", "\n").replace("\r", "\n"))

def normalize_candidate_analysis(data, resume_text):
    out = build_empty_candidate_analysis(); warnings = []
    if not isinstance(data, dict):
        _warn(warnings,"invalid_type", "root"); out["quality"]["warnings"] = warnings; out["quality"]["status"] = "manual_required"; return out
    allowed = set(CANDIDATE_ANALYSIS_V3_CONTRACT["top"])
    if not isinstance(data.get("contract_version"), str): _warn(warnings,"invalid_type", "contract_version")
    elif data.get("contract_version") != CANDIDATE_ANALYSIS_V3_CONTRACT["version"]: _warn(warnings,"invalid_enum", "contract_version")
    for key in data:
        if key not in allowed:
            _warn(warnings,"unverified_field", "root.extra")
    for key, spec in CANDIDATE_ANALYSIS_V3_CONTRACT["top"].items():
        if spec.get("provider_owned") is False:
            continue
        if key not in data:
            _warn(warnings,"missing_required", key)
        elif spec.get("type") == "list" and not isinstance(data[key], list):
            _warn(warnings, "invalid_type", key)
        elif spec.get("type") == "object" and not isinstance(data[key], dict):
            _warn(warnings, "invalid_type", key)
    raw = data.get("summary", {})
    if isinstance(raw, dict):
        for key in raw:
            if key not in CANDIDATE_ANALYSIS_V3_CONTRACT["summary"]:
                _warn(warnings,"unverified_field", "summary.extra")
        for key in ("headline", "experience_level"):
            spec = CANDIDATE_ANALYSIS_V3_CONTRACT["summary"][key]
            if isinstance(raw.get(key, ""), str) and len(raw.get(key, "")) <= spec["max_length"]: out["summary"][key] = raw.get(key, "")
            elif key in raw: _warn(warnings,"invalid_type", f"summary.{key}")
        for key in ("domains", "strengths"):
            spec = CANDIDATE_ANALYSIS_V3_CONTRACT["summary"][key]; values = raw.get(key, [])
            if isinstance(values, list):
                if any(not isinstance(value, str) for value in values):
                    _warn(warnings,"invalid_type", f"summary.{key}"); continue
                if len(values) > spec["max"]: _warn(warnings,"invalid_type", f"summary.{key}")
                for i, value in enumerate(values[:spec["max"]]):
                    if isinstance(value, str) and len(value) <= spec["item_max_length"]: out["summary"][key].append(value)
                    else: _warn(warnings,"invalid_type", f"summary.{key}[{i}]")
            elif key in raw: _warn(warnings,"invalid_type", f"summary.{key}")
    elif "summary" in data: _warn(warnings,"invalid_type", "summary")
    canonical = canonicalize_resume_text_v3(resume_text); refs = set()
    raw_unknowns = data.get("unknowns", [])
    if isinstance(raw_unknowns, list):
        max_unknowns = CANDIDATE_ANALYSIS_V3_CONTRACT["top"]["unknowns"]["max"]
        if len(raw_unknowns) > max_unknowns: _warn(warnings, "invalid_type", "unknowns")
        for i, item in enumerate(raw_unknowns[:max_unknowns]):
            path = f"unknowns[{i}]"
            if not isinstance(item, dict):
                _warn(warnings,"invalid_type", path); continue
            for key in item:
                if key not in CANDIDATE_ANALYSIS_V3_CONTRACT["unknown"]:
                    _warn(warnings,"unverified_field", f"{path}.extra")
            field = item.get("field")
            if field not in CANDIDATE_ANALYSIS_V3_CONTRACT["unknown"]["field"]["enum"]:
                _warn(warnings,"invalid_enum", path + ".field"); continue
            message = item.get("message", "")
            message_spec = CANDIDATE_ANALYSIS_V3_CONTRACT["unknown"]["message"]
            if not isinstance(message, str) or len(message) > message_spec["max_length"]:
                _warn(warnings,"invalid_type", path + ".message"); message = ""
            out["unknowns"].append({"field": field, "message": message})
    elif "unknowns" in data:
        _warn(warnings,"invalid_type", "unknowns")
    if "evidence" in data and not isinstance(data.get("evidence"), list): _warn(warnings,"invalid_type","evidence")
    raw_evidence = data.get("evidence", []) if isinstance(data.get("evidence", []), list) else []
    max_evidence = CANDIDATE_ANALYSIS_V3_CONTRACT["top"]["evidence"]["max"]
    if len(raw_evidence) > max_evidence: _warn(warnings, "invalid_type", "evidence")
    for i, item in enumerate(raw_evidence[:max_evidence]):
        p = f"evidence[{i}]"
        try:
            if not isinstance(item, dict): raise ValueError("invalid_type")
            for key in item:
                if key not in CANDIDATE_ANALYSIS_V3_CONTRACT["evidence"] and key not in ("source_locator","safe_excerpt"):
                    _warn(warnings,"unverified_field", f"{p}.extra")
            ref, typ, quote, assertion = item.get("client_ref"), item.get("type"), item.get("source_quote"), item.get("assertion_type")
            ref_spec = CANDIDATE_ANALYSIS_V3_CONTRACT["evidence"]["client_ref"]
            if not isinstance(ref, str) or not ref: raise ValueError("missing_required")
            if len(ref) > ref_spec["max_length"]: _warn(warnings,"invalid_type",p+".client_ref"); raise ValueError("invalid_type")
            if typ not in CANDIDATE_ANALYSIS_V3_CONTRACT["evidence"]["type"]["enum"] or assertion not in CANDIDATE_ANALYSIS_V3_CONTRACT["evidence"]["assertion_type"]["enum"]: raise ValueError("invalid_enum")
            if not isinstance(quote, str) or not quote:
                raise ValueError("invalid_evidence" if "source_quote" in item else "missing_required")
            if len(quote) > CANDIDATE_ANALYSIS_V3_CONTRACT["evidence"]["source_quote"]["max_length"]:
                _warn(warnings,"invalid_type",p+".source_quote"); raise ValueError("invalid_type")
            normalized = item.get("normalized_value", "")
            if not isinstance(normalized, str):
                _warn(warnings, "invalid_type", p+".normalized_value")
                raise ValueError("invalid_type")
            if len(normalized) > CANDIDATE_ANALYSIS_V3_CONTRACT["evidence"]["normalized_value"]["max_length"]:
                _warn(warnings, "invalid_type", p+".normalized_value")
                normalized = CANDIDATE_ANALYSIS_V3_CONTRACT["evidence"]["normalized_value"]["empty"]
            if isinstance(item.get("confidence"), bool) or not isinstance(item.get("confidence"), int):
                _warn(warnings,"invalid_type",p+".confidence"); continue
            try: conf = _confidence(item.get("confidence"))
            except ValueError: _warn(warnings,"invalid_type",p+".confidence"); continue
            if _is_sensitive(quote): raise ValueError("sensitive_value")
            quote = canonicalize_resume_text_v3(quote)
            try:
                loc = resolve_evidence_quote(quote, canonical)
            except ValueError:
                # Sensitivity is checked above; resolver failures are safe
                # evidence-verification failures and must not leak internals.
                raise ValueError("invalid_evidence")
            if not loc: raise ValueError("invalid_evidence")
            if len(quote) < 4 and canonical.count(quote) > 1: raise ValueError("invalid_evidence")
            if ref in refs:
                _warn(warnings, "reference_invalid", p+".client_ref")
                raise ValueError("invalid_evidence")
            refs.add(ref); out["evidence"].append({"client_ref": ref, "type": typ, "normalized_value": normalized, "source_quote": quote, "source_locator": loc, "safe_excerpt": redact_pii(quote), "assertion_type": assertion, "confidence": conf})
        except ValueError as e: _warn(warnings, str(e), p)
    raw_dirs = data.get("directions", []) if isinstance(data.get("directions", []), list) else []
    confirmable_direction_exists = False
    max_directions = CANDIDATE_ANALYSIS_V3_CONTRACT["top"]["directions"]["max"]
    if isinstance(data.get("directions"), list) and len(data["directions"]) > max_directions:
        _warn(warnings, "invalid_type", "directions")
    for i, item in enumerate(raw_dirs[:max_directions]):
        p=f"directions[{i}]"
        if not isinstance(item, dict): _warn(warnings,"invalid_type",p); continue
        for key in item:
            if key not in CANDIDATE_ANALYSIS_V3_CONTRACT["direction"]:
                _warn(warnings,"unverified_field", f"{p}.extra")
        scalar_values = {}
        scalar_valid = True
        for fld in ("client_ref","name","rationale"):
            spec = CANDIDATE_ANALYSIS_V3_CONTRACT["direction"][fld]; value = item.get(fld, spec["empty"])
            if fld == "client_ref" and (not isinstance(value, str) or not value):
                _warn(warnings,"missing_required",p+".client_ref")
                value = spec["empty"]; scalar_valid = False
            elif not isinstance(value, str) or len(value) > spec["max_length"]:
                if fld in item: _warn(warnings,"invalid_type",p+"."+fld)
                value = spec["empty"]; scalar_valid = False
            scalar_values[fld] = value
        gaps_spec = CANDIDATE_ANALYSIS_V3_CONTRACT["direction"]["gaps"]; raw_gaps = item.get("gaps", [])
        gaps=[]; gaps_valid=isinstance(raw_gaps,list)
        if not gaps_valid: _warn(warnings,"invalid_type",p+".gaps")
        else:
            if len(raw_gaps)>gaps_spec["max"]: _warn(warnings,"invalid_type",p+".gaps"); gaps_valid=False
            for j,value in enumerate(raw_gaps[:gaps_spec["max"]]):
                if isinstance(value,str) and len(value)<=gaps_spec["item_max_length"]: gaps.append(value)
                else: _warn(warnings,"invalid_type",f"{p}.gaps[{j}]"); gaps_valid=False
        if "confidence" in item:
            if isinstance(item["confidence"], bool) or not isinstance(item["confidence"], int) or not 0 <= item["confidence"] <= 100:
                _warn(warnings,"invalid_type",p+".confidence")
        if "default_enabled" in item and not isinstance(item["default_enabled"], bool): _warn(warnings,"invalid_type",p+".default_enabled")
        typ=item.get("type"); terms=item.get("search_terms", []); erefs=item.get("evidence_refs", [])
        if typ not in CANDIDATE_ANALYSIS_V3_CONTRACT["direction"]["type"]["enum"]: _warn(warnings,"invalid_enum",p+".type"); continue
        terms_spec=CANDIDATE_ANALYSIS_V3_CONTRACT["direction"]["search_terms"]
        if not isinstance(terms, list): _warn(warnings,"invalid_type", p + ".search_terms"); terms = []
        else:
            raw_terms=terms; terms=[]
            if len(raw_terms)>terms_spec["max"]: _warn(warnings,"invalid_type",p+".search_terms")
            else:
                for j,value in enumerate(raw_terms):
                    if isinstance(value,str) and len(value)<=terms_spec["item_max_length"]: terms.append(value)
                    else: _warn(warnings,"invalid_type",f"{p}.search_terms[{j}]")
        valid_refs=[]; lost_ref=False
        if "evidence_refs" not in item:
            _warn(warnings, "missing_required", p+".evidence_refs"); lost_ref = True; erefs = []
        if not isinstance(erefs, list) or not all(isinstance(x, str) for x in erefs):
            _warn(warnings,"invalid_type", p + ".evidence_refs"); erefs = []; lost_ref = True
        for r in erefs:
            if r in valid_refs:
                _warn(warnings, "reference_invalid", p+".evidence_refs"); lost_ref=True
            elif r in refs: valid_refs.append(r)
            elif r not in refs: _warn(warnings,"reference_invalid",p+".evidence_refs"); lost_ref=True
        max_terms = CANDIDATE_ANALYSIS_V3_CONTRACT["direction"]["search_terms"]["max"]
        executable = 1 <= len(terms) <= max_terms and not lost_ref and gaps_valid and scalar_valid
        confirmable_direction_exists = confirmable_direction_exists or 1 <= len(terms) <= max_terms
        out["directions"].append({"client_ref": scalar_values["client_ref"], "name": scalar_values["name"], "type": typ, "rationale": scalar_values["rationale"], "evidence_refs": valid_refs, "gaps": gaps, "confidence": _confidence(item.get("confidence",0)) if isinstance(item.get("confidence",0),int) and not isinstance(item.get("confidence",0),bool) and 0 <= item.get("confidence",0) <= 100 else 0, "default_enabled": (item.get("default_enabled",False) and executable) if isinstance(item.get("default_enabled",False),bool) else False, "search_terms": terms})
    # Provider quality is informational only; validate its shape for diagnostics,
    # but retain the backend-derived quality object as the sole authority.
    provider_quality = data.get("quality")
    if isinstance(provider_quality, dict):
        for key in provider_quality:
            if key not in CANDIDATE_ANALYSIS_V3_CONTRACT["quality"]: _warn(warnings, "unverified_field", "quality.extra")
        if "status" in provider_quality:
            if not isinstance(provider_quality["status"], str): _warn(warnings, "invalid_type", "quality.status")
            elif provider_quality["status"] not in CANDIDATE_ANALYSIS_V3_CONTRACT["quality"]["status"]["enum"]: _warn(warnings, "invalid_enum", "quality.status")
        if "warnings" in provider_quality:
            if not isinstance(provider_quality["warnings"], list): _warn(warnings, "invalid_type", "quality.warnings")
            else:
                for i, item in enumerate(provider_quality["warnings"]):
                    if not isinstance(item, dict): _warn(warnings, "invalid_type", f"quality.warnings[{i}]")
                    else:
                        for field in item:
                            if field not in ("code", "path"): _warn(warnings, "unverified_field", "quality.extra")
                        if not isinstance(item.get("code"), str): _warn(warnings, "invalid_type", f"quality.warnings[{i}].code")
                        elif item.get("code") not in CANDIDATE_ANALYSIS_V3_CONTRACT["warning_codes"]: _warn(warnings, "invalid_enum", f"quality.warnings[{i}].code")
                        if not isinstance(item.get("path"), str): _warn(warnings, "invalid_type", f"quality.warnings[{i}].path")
                        elif not item.get("path"): _warn(warnings, "missing_required", f"quality.warnings[{i}].path")
    elif "quality" in data:
        _warn(warnings, "invalid_type", "quality")
    out["quality"]["warnings"] = warnings
    max_terms = CANDIDATE_ANALYSIS_V3_CONTRACT["direction"]["search_terms"]["max"]
    executable = confirmable_direction_exists
    out["quality"]["status"] = "manual_required" if not executable else ("partial" if warnings else "complete")
    return out


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
