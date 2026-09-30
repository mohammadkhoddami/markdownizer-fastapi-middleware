"""Determinism tests: same project + same app → same bytes, same ETag."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from markdownizer_fastapi import ArtifactService, MarkdownizerConfig, MarkdownizerMiddleware

PREFIX = "/_markdownizer"


def _service(demo: Any) -> ArtifactService:
    config = MarkdownizerConfig(project_root=str(demo.root), debug=True)
    return ArtifactService(config, app=demo.app)


def test_project_json_is_byte_identical(demo: Any) -> None:
    first = json.dumps(_service(demo).project_dict(), sort_keys=True, indent=2, ensure_ascii=False)
    second = json.dumps(_service(demo).project_dict(), sort_keys=True, indent=2, ensure_ascii=False)
    assert first == second


def test_context_text_is_identical(demo: Any) -> None:
    first = _service(demo).context_text(max_tokens=4000)
    second = _service(demo).context_text(max_tokens=4000)
    assert first == second


def test_enriched_hash_is_stable_across_services(demo: Any) -> None:
    assert _service(demo).project().enriched_hash == _service(demo).project().enriched_hash


def test_etag_is_stable_across_app_restarts(demo: Any) -> None:
    demo.app.add_middleware(MarkdownizerMiddleware, service=_service(demo))
    with TestClient(demo.app) as client:
        first = client.get(f"{PREFIX}/manifest").headers["etag"]
    demo.app.middleware_stack = None
    demo.app.add_middleware(MarkdownizerMiddleware, service=_service(demo))
    with TestClient(demo.app) as client:
        second = client.get(f"{PREFIX}/manifest").headers["etag"]
    assert first == second
