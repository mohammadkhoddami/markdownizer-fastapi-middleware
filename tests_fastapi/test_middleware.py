"""End-to-end middleware tests through the FastAPI test client."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from markdownizer_fastapi import ArtifactService, MarkdownizerConfig, MarkdownizerMiddleware
from markdownizer_fastapi.http import prepare_response
from tests_fastapi.conftest import BrokenService

PREFIX = "/_markdownizer"


@pytest.fixture
def client(demo: Any) -> Iterator[TestClient]:
    def health() -> dict[str, bool]:
        return {"ok": True}

    demo.app.add_api_route("/health", health, methods=["GET"])
    demo.app.add_middleware(
        MarkdownizerMiddleware,
        project_root=str(demo.root),
        debug=True,
        include_source=False,
    )
    with TestClient(demo.app) as test_client:
        yield test_client


def _service(demo: Any, **overrides: Any) -> ArtifactService:
    config = MarkdownizerConfig(project_root=str(demo.root), debug=True, **overrides)
    return ArtifactService(config, app=demo.app)


def _add_service_middleware(demo: Any, service: ArtifactService) -> None:
    demo.app.add_middleware(MarkdownizerMiddleware, service=service)


def test_passthrough_does_not_build(demo: Any) -> None:
    def health() -> dict[str, bool]:
        return {"ok": True}

    demo.app.add_api_route("/health", health, methods=["GET"])
    service = _service(demo)
    _add_service_middleware(demo, service)
    with TestClient(demo.app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert service.built is False


def test_disabled_by_default_returns_404(demo: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKDOWNIZER_FASTAPI_ENABLED", raising=False)
    demo.app.add_middleware(MarkdownizerMiddleware, project_root=str(demo.root))
    with TestClient(demo.app) as client:
        response = client.get(f"{PREFIX}/manifest")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_manifest(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/manifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["ir_version"] == 1
    assert payload["hash"]
    assert payload["enriched_hash"] != payload["hash"]
    assert payload["route_count"] > 0
    assert payload["built_at"]


def test_context_markdown(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/context.md?max_tokens=4000")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "# Context:" in response.text
    assert "## HTTP API" in response.text
    assert "GET /v1/users" in response.text
    assert "Runtime-only routes" in response.text


def test_context_query_filter(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/context.md?q=list_users&max_tokens=4000")
    assert response.status_code == 200
    assert "list_users" in response.text


def test_project_json(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/project.json")
    assert response.status_code == 200
    payload = response.json()
    assert "symbols" in payload
    assert "routes" in payload
    assert payload["enriched_hash"]
    assert any(route["path"] == "/v1/users" for route in payload["routes"])


def test_routes_json(client: TestClient, demo: Any) -> None:
    response = client.get(f"{PREFIX}/routes.json")
    assert response.status_code == 200
    payload = response.json()
    assert payload["route_count"] > 0
    users = next(route for route in payload["routes"] if route["path"] == "/v1/users")
    assert users["symbol_id"] == f"{demo.package}/api.py::list_users"
    assert users["matched"] is True


def test_stats_json_and_text(client: TestClient) -> None:
    json_response = client.get(f"{PREFIX}/stats")
    assert json_response.status_code == 200
    payload = json_response.json()
    assert payload["stats"]["module_count"] >= 3
    assert payload["top_files"]

    text_response = client.get(f"{PREFIX}/stats?format=text")
    assert text_response.status_code == 200
    assert text_response.headers["content-type"].startswith("text/plain")
    assert "Top files by importance:" in text_response.text


def test_invalid_stats_format(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/stats?format=xml")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_parameter"


def test_llms_txt(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/llms.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.startswith("# ")
    assert "## Packages" in response.text


def test_openapi_reference(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/openapi-ref.json")
    assert response.status_code == 200
    assert response.json() == {"openapi_url": "/openapi.json"}


def test_etag_and_not_modified(client: TestClient) -> None:
    first = client.get(f"{PREFIX}/manifest")
    etag = first.headers["etag"]
    assert first.headers["x-markdownizer-ir-version"] == "1"
    assert first.headers["x-markdownizer-ir-hash"]

    second = client.get(f"{PREFIX}/manifest", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["etag"] == etag


def test_head_request(client: TestClient) -> None:
    response = client.head(f"{PREFIX}/manifest")
    assert response.status_code == 200
    assert response.content == b""
    assert int(response.headers["content-length"]) > 0


def test_unknown_endpoint(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_method_not_allowed(client: TestClient) -> None:
    response = client.put(f"{PREFIX}/manifest")
    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"


def test_refresh_requires_auth_token(client: TestClient) -> None:
    response = client.post(f"{PREFIX}/refresh")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_refresh_get_not_allowed(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/refresh")
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


@pytest.mark.parametrize(
    "query",
    [
        "profile=nope",
        "rank=weighted",
        "max_tokens=0",
        "max_tokens=abc",
        "max_tokens=1000000",
    ],
)
def test_invalid_context_parameters(client: TestClient, query: str) -> None:
    response = client.get(f"{PREFIX}/context.md?{query}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_parameter"


def test_token_auth(demo: Any) -> None:
    demo.app.add_middleware(
        MarkdownizerMiddleware,
        project_root=str(demo.root),
        debug=True,
        auth_token="secret",
    )
    with TestClient(demo.app) as client:
        assert client.get(f"{PREFIX}/manifest").status_code == 401
        wrong = client.get(f"{PREFIX}/manifest", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        assert wrong.headers["www-authenticate"] == "Bearer"
        allowed = client.get(f"{PREFIX}/manifest", headers={"Authorization": "Bearer secret"})
        assert allowed.status_code == 200
        assert allowed.headers["cache-control"] == "no-store"
        assert client.post(f"{PREFIX}/refresh").status_code == 401
        refreshed = client.post(f"{PREFIX}/refresh", headers={"X-Markdownizer-Token": "secret"})
        assert refreshed.status_code == 200


def test_client_allowlist_denies_unknown_host(demo: Any) -> None:
    demo.app.add_middleware(
        MarkdownizerMiddleware,
        project_root=str(demo.root),
        debug=True,
        allow_clients=("10.0.0.0/8",),
    )
    with TestClient(demo.app) as client:
        response = client.get(f"{PREFIX}/manifest")
    assert response.status_code == 403


def test_llms_at_root(demo: Any) -> None:
    demo.app.add_middleware(
        MarkdownizerMiddleware,
        project_root=str(demo.root),
        debug=True,
        llms_at_root=True,
    )
    with TestClient(demo.app) as client:
        assert client.get("/llms.txt").status_code == 200
        assert client.get(f"{PREFIX}/llms.txt").status_code == 200


def test_startup_mode_warms_up(demo: Any) -> None:
    config = MarkdownizerConfig(
        project_root=str(demo.root),
        debug=True,
        mode="startup",
    )
    service = ArtifactService(config, app=demo.app)
    _add_service_middleware(demo, service)
    with TestClient(demo.app):
        pass
    assert service.built is True


def test_dev_reload_rebuilds_on_change(demo: Any) -> None:
    config = MarkdownizerConfig(
        project_root=str(demo.root),
        debug=True,
        mode="dev_reload",
    )
    service = ArtifactService(config, app=demo.app)
    _add_service_middleware(demo, service)
    api_file = Path(demo.root) / demo.package / "api.py"
    with TestClient(demo.app) as client:
        first = client.get(f"{PREFIX}/manifest").json()["hash"]
        api_file.write_text(api_file.read_text() + '\n\ndef added_later():\n    """New."""\n')
        second = client.get(f"{PREFIX}/manifest").json()["hash"]
    assert first != second


def test_conflicting_middleware_arguments(demo: Any) -> None:
    service = _service(demo)
    with pytest.raises(ValueError, match="not both"):
        MarkdownizerMiddleware(demo.app, config=service.config, service=service)


def test_routes_endpoint_disabled(demo: Any) -> None:
    demo.app.add_middleware(
        MarkdownizerMiddleware,
        project_root=str(demo.root),
        debug=True,
        include_routes=False,
    )
    with TestClient(demo.app) as client:
        assert client.get(f"{PREFIX}/routes.json").status_code == 404


def test_openapi_reference_without_schema(demo: Any) -> None:
    service = ArtifactService(MarkdownizerConfig(project_root=str(demo.root), debug=True))
    response = prepare_response(service, service.config, method="GET", path="/openapi-ref.json")
    assert response.status_code == 404
    assert response.body


def test_build_failure_returns_500(demo: Any) -> None:
    service = BrokenService(demo.root, error=OSError)
    response = prepare_response(service, service.config, method="GET", path="/manifest")
    assert response.status_code == 500
    assert b"build_failed" in response.body


def test_unexpected_error_returns_500(demo: Any) -> None:
    service = BrokenService(demo.root)
    _add_service_middleware(demo, service)
    with TestClient(demo.app, raise_server_exceptions=False) as client:
        response = client.get(f"{PREFIX}/manifest")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


def test_startup_warmup_failure_is_tolerated(demo: Any) -> None:
    service = BrokenService(demo.root, mode="startup")
    _add_service_middleware(demo, service)
    with TestClient(demo.app):
        pass


def test_non_http_scope_passthrough(demo: Any) -> None:
    async def fake_app(scope: Any, receive: Any, send: Any) -> None:
        scope["handled"] = True

    async def noop_receive() -> dict[str, Any]:
        return {"type": "websocket.disconnect"}

    async def noop_send(message: Any) -> None:
        pass

    service = _service(demo)
    middleware = MarkdownizerMiddleware(fake_app, service=service)
    scope: dict[str, Any] = {"type": "websocket", "path": "/ws"}
    asyncio.run(middleware(scope, noop_receive, noop_send))
    assert scope["handled"] is True
    assert service.built is False


def test_post_to_read_endpoint_rejected(client: TestClient) -> None:
    response = client.post(f"{PREFIX}/manifest")
    assert response.status_code == 405
