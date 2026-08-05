"""Helper functions and constants for the Microsoft Graph API client.

Extracted from graph_client.py to keep that module focused on the
GraphClient class and under the 300-line limit.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

# Domains trusted for binary downloads (e.g., @microsoft.graph.downloadUrl CDN URLs).
ALLOWED_DOWNLOAD_DOMAINS: frozenset[str] = frozenset(
    {
        ".sharepoint.com",
        ".1drv.com",
        ".microsoft.com",
        ".office.com",
        ".office365.com",
        ".cdn.office.net",
        ".svc.ms",
    }
)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Maps Graph error codes to actionable CLI hints.
_ERROR_HINTS: dict[str, str] = {
    "Authorization_RequestDenied": (
        "The app lacks the required permission. "
        "Go to Entra > App registrations > API permissions and grant the missing scope, then re-consent."
    ),
    "InsufficientPrivileges": (
        "Admin consent is required for this permission. "
        "Ask your tenant admin to grant consent in Entra > App registrations > API permissions."
    ),
    "InvalidAuthenticationToken": (
        "The access token is invalid or expired. Run: m365-brain --config config.yaml auth login"
    ),
    "OrganizationFromTenantGuidNotFound": (
        "The tenant ID in your config does not match a valid Entra tenant. Check MSAL_TENANT_ID in .env."
    ),
    "AuthenticationError": (
        "Authentication failed. Verify MSAL_CLIENT_ID and MSAL_TENANT_ID in .env, "
        "then run: m365-brain --config config.yaml auth login"
    ),
    "ErrorAccessDenied": ("Access denied for this resource. The signed-in user may lack the required role or license."),
}


def _extract_graph_error(body: str, max_message_length: int) -> tuple[str, str]:
    """Extract error code and message from a Graph API error response.

    Parses the standard ``{"error": {"code": "...", "message": "..."}}``
    envelope. Returns ``("unknown", "non-json response")`` if the body
    is not valid JSON or lacks the expected structure.

    The message is truncated to ``max_message_length`` characters
    to prevent PII leakage through verbose error descriptions.
    """
    try:
        data = json.loads(body)
        error = data["error"]
        code = error["code"]
        message = error["message"][:max_message_length]
        return code, message
    except (json.JSONDecodeError, KeyError, TypeError):
        return "unknown", "non-json response"


def _is_allowed_download_domain(url: str) -> bool:
    """Check whether a URL's host matches an allowed Microsoft CDN domain."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in ALLOWED_DOWNLOAD_DOMAINS)


def _sanitize_log_url(url: str) -> str:
    """Strip query parameters from a URL to avoid logging SAS tokens."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _friendly_error(status: int, error_code: str, error_message: str, path: str) -> str:
    """Build a human-readable error message with an actionable hint if available."""
    hint = _ERROR_HINTS.get(error_code, "")
    parts = [f"Graph API error on {path}: HTTP {status} — {error_code}: {error_message}"]
    if hint:
        parts.append(f"  Hint: {hint}")
    return "\n".join(parts)
