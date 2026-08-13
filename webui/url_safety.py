"""Shared HTTPS URL authority checks used by link allowlists.

Keeps the port / userinfo rules in one place so the scraper, web API,
desktop shell and updater cannot drift apart.
"""

from __future__ import annotations

from urllib.parse import ParseResult, urlparse, urlunparse


def is_safe_https_authority(
    parsed: ParseResult,
    *,
    allowed_hosts: set[str] | frozenset[str] | tuple[str, ...],
    allow_subdomains: bool = False,
) -> bool:
    """Return True only for HTTPS, no userinfo and port 443 (or none)."""
    if parsed.scheme != "https":
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if port not in (None, 443):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if allow_subdomains:
        return host in allowed_hosts or any(
            host.endswith("." + hostname) for hostname in allowed_hosts
        )
    return host in allowed_hosts


def clean_https_url(parsed: ParseResult, *, drop_params: bool = False) -> str:
    """Re-emit an HTTPS URL without query/fragment (and params when asked)."""
    cleaned = parsed._replace(query="", fragment="")
    if drop_params:
        cleaned = cleaned._replace(params="")
    return urlunparse(cleaned)


def parse_https_url(raw: str) -> ParseResult | None:
    """Parse *raw* and return the result only when it is a valid HTTPS URL."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    return parsed if parsed.scheme == "https" else None
