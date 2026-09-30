"""Plugin and router integration tests."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from markdownizer_fastapi import (
    ArtifactService,
    MarkdownizerConfig,
    MarkdownizerMiddleware,
    MarkdownizerPlugin,
    MarkdownizerRouter,
)

PREFIX = "/_markdownizer"


def test_plugin_exposes_matched_routes(demo: Any) -> None:
    plugin = MarkdownizerPlugin(demo.app, project_root=str(demo.root), debug=True)
    routes = {(route.path, route.methods): route for route in plugin.routes}
    users = routes[("/v1/users", ("GET",))]
    assert users.matched is True
    assert users.symbol_id == f"{demo.package}/api.py::list_users"
    assert plugin.config.include_source is False


def test_plugin_context_text_includes_api(demo: Any) -> None:
    plugin = MarkdownizerPlugin(demo.app, project_root=str(demo.root), debug=True)
    text = plugin.context_text(max_tokens=4000)
    assert "## HTTP API" in text
    assert "GET /v1/users" in text


def test_plugin_install_serves_and_shares_cache(demo: Any) -> None:
    plugin = MarkdownizerPlugin(demo.app, project_root=str(demo.root), debug=True)
    plugin.install()
    with TestClient(demo.app) as client:
        response = client.get(f"{PREFIX}/routes.json")
    assert response.status_code == 200
    assert plugin.service.built is True


def test_plugin_warmup(demo: Any) -> None:
    plugin = MarkdownizerPlugin(demo.app, project_root=str(demo.root), debug=True)
    project = plugin.warmup()
    assert project.enriched_hash
    assert plugin.service.built is True


def test_plugin_router(demo: Any) -> None:
    plugin = MarkdownizerPlugin(demo.app, project_root=str(demo.root), debug=True)
    app = FastAPI()
    app.include_router(plugin.router(), prefix=PREFIX)
    with TestClient(app) as client:
        manifest = client.get(f"{PREFIX}/manifest")
        routes = client.get(f"{PREFIX}/routes.json")
    assert manifest.status_code == 200
    assert routes.status_code == 200
    assert routes.json()["route_count"] > 0


def test_router_standalone(demo: Any) -> None:
    config = MarkdownizerConfig(project_root=str(demo.root), debug=True)
    router = MarkdownizerRouter(service=ArtifactService(config, app=demo.app))
    app = FastAPI()
    app.include_router(router, prefix=PREFIX)
    with TestClient(app) as client:
        assert client.get(f"{PREFIX}/manifest").status_code == 200
        assert client.get(f"{PREFIX}/stats").status_code == 200
        assert client.get(f"{PREFIX}/stats?format=text").status_code == 200
        assert client.get(f"{PREFIX}/context.md?max_tokens=3000").status_code == 200
        assert client.get(f"{PREFIX}/project.json").status_code == 200
        assert client.get(f"{PREFIX}/routes.json").status_code == 200
        assert client.get(f"{PREFIX}/llms.txt").status_code == 200
        assert client.get(f"{PREFIX}/openapi-ref.json").status_code == 200
        assert client.post(f"{PREFIX}/refresh").status_code == 403


def test_router_conflicting_arguments(demo: Any) -> None:
    service = ArtifactService(
        MarkdownizerConfig(project_root=str(demo.root), debug=True), app=demo.app
    )
    with pytest.raises(ValueError, match="not both"):
        MarkdownizerRouter(config=service.config, service=service)


def test_plugin_accepts_shared_service(demo: Any) -> None:
    service = ArtifactService(
        MarkdownizerConfig(project_root=str(demo.root), debug=True), app=demo.app
    )
    plugin = MarkdownizerPlugin(demo.app, service=service)
    assert plugin.config is service.config
    assert plugin.project().routes
    assert plugin.context_text(max_tokens=3000)


def test_plugin_conflicting_arguments(demo: Any) -> None:
    service = ArtifactService(
        MarkdownizerConfig(project_root=str(demo.root), debug=True), app=demo.app
    )
    with pytest.raises(ValueError, match="not both"):
        MarkdownizerPlugin(demo.app, config=service.config, service=service)


def test_middleware_service_and_kwargs_conflict(demo: Any) -> None:
    service = ArtifactService(
        MarkdownizerConfig(project_root=str(demo.root), debug=True), app=demo.app
    )
    with pytest.raises(ValueError, match="not both"):
        MarkdownizerMiddleware(demo.app, service=service, include_source=True)
