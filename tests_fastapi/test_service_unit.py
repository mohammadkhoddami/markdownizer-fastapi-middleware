"""Unit tests for service internals and edge cases."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

from markdownizer_fastapi import ArtifactService, MarkdownizerConfig
from markdownizer_fastapi.service import _discover_openapi_url, _resolve_introspection_app


class _Route:
    def __init__(self, name: str, path: Any) -> None:
        self.name = name
        self.path = path


class _Wrapper:
    def __init__(self, app: Any) -> None:
        self.app = app


def test_resolve_introspection_app_none() -> None:
    assert _resolve_introspection_app(None) is None


def test_resolve_introspection_app_chain() -> None:
    leaf = types.SimpleNamespace(routes=[1])
    assert _resolve_introspection_app(_Wrapper(_Wrapper(leaf))) is leaf
    assert _resolve_introspection_app(types.SimpleNamespace(routes=[])) is None


def test_resolve_introspection_app_gives_up() -> None:
    assert _resolve_introspection_app(types.SimpleNamespace(app=None)) is None


def test_discover_openapi_url_variants() -> None:
    assert _discover_openapi_url(None) is None
    assert _discover_openapi_url(types.SimpleNamespace(openapi_url="/o.json")) == "/o.json"
    app = types.SimpleNamespace(routes=[_Route("openapi", "/from-route.json")])
    assert _discover_openapi_url(app) == "/from-route.json"
    unnamed = types.SimpleNamespace(routes=[_Route("openapi", None)])
    assert _discover_openapi_url(unnamed) is None
    assert _discover_openapi_url(types.SimpleNamespace(routes=[])) is None


def test_attach_app_rebuilds_with_routes(demo: Any) -> None:
    config = MarkdownizerConfig(project_root=str(demo.root), debug=True)
    service = ArtifactService(config)
    assert service.project().routes == []
    service.attach_app(None)
    service.attach_app(object())
    assert service.project().routes == []
    service.attach_app(demo.app)
    assert service.project().routes


def test_attach_app_never_replaces_introspectable_app(demo: Any) -> None:
    config = MarkdownizerConfig(project_root=str(demo.root), debug=True)
    service = ArtifactService(config, app=demo.app)
    service.attach_app(demo.app)
    service.attach_app(object())
    assert service.project().routes


def test_include_routes_false_builds_static_only(demo: Any) -> None:
    config = MarkdownizerConfig(project_root=str(demo.root), debug=True, include_routes=False)
    service = ArtifactService(config, app=demo.app)
    assert service.project().routes == []


def test_built_at_and_context(demo: Any) -> None:
    service = ArtifactService(
        MarkdownizerConfig(project_root=str(demo.root), debug=True), app=demo.app
    )
    assert service.built_at is None
    context = service.context(max_tokens=1234, profile="architecture", rank_method="simple")
    assert context.text
    assert service.built_at is not None


def test_scan_fingerprint_skips_missing_files(demo: Any, monkeypatch: Any) -> None:
    service = ArtifactService(MarkdownizerConfig(project_root=str(demo.root), debug=True))
    monkeypatch.setattr(
        "markdownizer_fastapi.service.scan_python_files",
        lambda root, exclude=None: iter([Path(demo.root) / "missing.py"]),
    )
    assert service._scan_fingerprint() == ()
