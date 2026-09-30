"""Security helpers for the Markdownizer FastAPI integration.

Everything here is conservative: the endpoints are disabled unless explicitly
enabled, source is excluded by default, auth uses constant-time comparison, and
the client allowlist is CIDR-based.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
from collections.abc import Mapping

from markdownizer_fastapi.config import MarkdownizerConfig, parse_bool


def is_enabled(config: MarkdownizerConfig) -> bool:
    """Whether the endpoints may answer at all.

    ``debug=True`` and ``require_enable_env=False`` bypass the environment
    gate; otherwise the configured enable variable must be truthy.
    """
    if config.debug or not config.require_enable_env:
        return True
    return parse_bool(os.environ.get(config.enable_env, ""))


def client_allowed(client_host: str | None, allow_clients: tuple[str, ...]) -> bool:
    """Whether a client address passes the optional CIDR allowlist."""
    if not allow_clients:
        return True
    if not client_host:
        return False
    try:
        address = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(network, strict=False) for network in allow_clients)


def extract_token(headers: Mapping[str, str]) -> str | None:
    """Extract a bearer or ``X-Markdownizer-Token`` credential from headers."""
    authorization = headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[len("bearer ") :].strip()
        return bearer or None
    header_token = headers.get("x-markdownizer-token")
    if header_token:
        return header_token.strip() or None
    return None


def token_valid(provided: str | None, expected: str | None) -> bool:
    """Constant-time token check; auth is disabled when ``expected`` is None."""
    if expected is None:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)
