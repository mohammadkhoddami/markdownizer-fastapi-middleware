"""HTTP request handling shared by the middleware and the router.

``prepare_response`` is transport-agnostic: it takes decoded request facts and
returns a :class:`PreparedResponse`. The ASGI middleware and the FastAPI router
are thin adapters over it, so both integrations behave identically.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

from markdownizer.optimizer.profiles import get_profile
from markdownizer_fastapi.config import VALID_RANK_METHODS, MarkdownizerConfig
from markdownizer_fastapi.security import (
    client_allowed,
    extract_token,
    is_enabled,
    token_valid,
)
from markdownizer_fastapi.service import ArtifactService

logger = logging.getLogger(__name__)

_JSON = "application/json"
_MARKDOWN = "text/markdown; charset=utf-8"
_TEXT = "text/plain; charset=utf-8"


@dataclass(frozen=True)
class PreparedResponse:
    """A fully prepared HTTP response, independent of the web framework."""

    status_code: int
    content_type: str
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


def _json_body(payload: Any) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _error(
    status_code: int,
    code: str,
    message: str,
    headers: Mapping[str, str] | None = None,
) -> PreparedResponse:
    return PreparedResponse(
        status_code=status_code,
        content_type=_JSON,
        body=_json_body({"error": {"code": code, "message": message}}),
        headers=dict(headers or {}),
    )


def _artifact_headers(service: ArtifactService) -> dict[str, str]:
    project = service.project()
    cache_control = (
        "no-store" if service.config.auth_token else "public, max-age=0, must-revalidate"
    )
    return {
        "ETag": f'"{project.enriched_hash}"',
        "X-Markdownizer-IR-Version": str(project.ir.ir_version),
        "X-Markdownizer-IR-Hash": project.ir.hash,
        "X-Markdownizer-Enriched-Hash": project.enriched_hash,
        "Cache-Control": cache_control,
    }


def _finalize(
    service: ArtifactService,
    body: bytes,
    content_type: str,
    request_headers: Mapping[str, str],
) -> PreparedResponse:
    headers = _artifact_headers(service)
    etag = headers["ETag"]
    if request_headers.get("if-none-match") in {etag, f"W/{etag}"}:
        return PreparedResponse(status_code=304, content_type=_JSON, body=b"", headers=headers)
    return PreparedResponse(status_code=200, content_type=content_type, body=body, headers=headers)


def _positive_float(params: Mapping[str, list[str]], name: str, default: float) -> float:
    raw_values = params.get(name)
    if not raw_values or raw_values[0] == "":
        return default
    try:
        value = float(raw_values[0])
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _first(params: Mapping[str, list[str]], name: str) -> str | None:
    values = params.get(name)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _dispatch(
    service: ArtifactService,
    config: MarkdownizerConfig,
    normalized: str,
    params: Mapping[str, list[str]],
    request_headers: Mapping[str, str],
) -> PreparedResponse:
    if normalized == "/manifest":
        return _finalize(service, _json_body(service.manifest()), _JSON, request_headers)

    if normalized == "/project.json":
        return _finalize(service, _json_body(service.project_dict()), _JSON, request_headers)

    if normalized == "/routes.json":
        if not config.include_routes:
            return _error(404, "not_found", "Route extraction is disabled.")
        return _finalize(service, _json_body(service.routes_dict()), _JSON, request_headers)

    if normalized == "/stats":
        output_format = _first(params, "format") or "json"
        if output_format == "text":
            return _finalize(
                service,
                service.stats_report().encode("utf-8"),
                _TEXT,
                request_headers,
            )
        if output_format != "json":
            raise ValueError("format must be 'json' or 'text'")
        return _finalize(service, _json_body(service.stats_dict()), _JSON, request_headers)

    if normalized == "/llms.txt":
        return _finalize(service, service.llms_txt().encode("utf-8"), _TEXT, request_headers)

    if normalized == "/openapi-ref.json":
        if service.openapi_url is None:
            return _error(404, "not_found", "The application exposes no OpenAPI schema.")
        return _finalize(
            service,
            _json_body({"openapi_url": service.openapi_url}),
            _JSON,
            request_headers,
        )

    if normalized == "/context.md":
        max_tokens = _positive_float(params, "max_tokens", config.max_tokens)
        if max_tokens > config.max_context_tokens:
            raise ValueError(
                f"max_tokens exceeds max_context_tokens ({config.max_context_tokens:g})"
            )
        profile = _first(params, "profile")
        if profile is not None:
            get_profile(profile)
        rank_method = _first(params, "rank")
        if rank_method is not None and rank_method not in VALID_RANK_METHODS:
            choices = ", ".join(VALID_RANK_METHODS)
            raise ValueError(f"rank must be one of: {choices}")
        context = service.context_text(
            max_tokens=max_tokens,
            profile=profile,
            rank_method=rank_method,
            query=_first(params, "q"),
        )
        return _finalize(service, context.encode("utf-8"), _MARKDOWN, request_headers)

    return _error(404, "not_found", f"No Markdownizer endpoint at {normalized!r}.")


def prepare_response(
    service: ArtifactService,
    config: MarkdownizerConfig,
    *,
    method: str,
    path: str,
    query_string: str = "",
    headers: Mapping[str, str] | None = None,
    client_host: str | None = None,
) -> PreparedResponse:
    """Gate, route, and render one request into a :class:`PreparedResponse`."""
    request_headers = {key.lower(): value for key, value in (headers or {}).items()}
    method = method.upper()
    normalized = path.rstrip("/") or "/"
    params = parse_qs(query_string, keep_blank_values=True)

    if not is_enabled(config):
        return _error(404, "not_found", "Markdownizer endpoints are disabled.")
    if not client_allowed(client_host, config.allow_clients):
        return _error(403, "forbidden", "Client address is not allowed.")
    if not token_valid(extract_token(request_headers), config.auth_token):
        return _error(
            401,
            "unauthorized",
            "A valid token is required.",
            {"WWW-Authenticate": "Bearer"},
        )

    if method == "POST":
        if normalized != "/refresh":
            return _error(
                405,
                "method_not_allowed",
                f"Method {method} is not allowed.",
                {"Allow": "GET, HEAD"},
            )
        if config.auth_token is None:
            return _error(403, "forbidden", "Refresh requires auth_token to be configured.")
        service.project(refresh=True)
        return _finalize(service, _json_body(service.manifest()), _JSON, request_headers)

    if method not in ("GET", "HEAD"):
        allow = "GET, HEAD, POST" if normalized == "/refresh" else "GET, HEAD"
        return _error(
            405,
            "method_not_allowed",
            f"Method {method} is not allowed.",
            {"Allow": allow},
        )

    if normalized == "/refresh":
        return _error(
            405,
            "method_not_allowed",
            "Use POST to refresh.",
            {"Allow": "POST"},
        )

    try:
        return _dispatch(service, config, normalized, params, request_headers)
    except ValueError as exc:
        return _error(400, "invalid_parameter", str(exc))
    except OSError:
        logger.exception("Markdownizer failed to build the project context")
        return _error(500, "build_failed", "Failed to build the project context.")
