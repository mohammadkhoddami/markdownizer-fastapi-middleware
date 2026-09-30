"""Unit tests for route extraction internals across FastAPI generations."""

from __future__ import annotations

import types
from typing import Any

from starlette.routing import Mount, Route

from markdownizer_fastapi.routes import (
    RouteInfo,
    _builtin_doc_paths,
    _flatten_dependencies,
    _join_path,
    _model_name,
    _summary,
    _walk,
    _walk_effective,
    extract_routes,
)


def ping() -> None:
    """Ping endpoint."""


async def _noop_app(scope: Any, receive: Any, send: Any) -> None:
    pass


def _dep_a() -> None:
    pass


def _dep_b() -> None:
    pass


class _FakeDependant:
    def __init__(self, call: Any = None, dependencies: Any = ()) -> None:
        self.call = call
        self.dependencies = list(dependencies)


def test_join_path_branches() -> None:
    assert _join_path("", "") == "/"
    assert _join_path("", "/x") == "/x"
    assert _join_path("/p", "") == "/p"
    assert _join_path("/p/", "/x") == "/p/x"


def test_model_name_variants() -> None:
    assert _model_name(None) is None
    assert _model_name(int) == "int"
    assert _model_name(list[int]) == "list[int]"
    name = _model_name(object())
    assert isinstance(name, str) and name.startswith("<object object")


def test_summary_variants() -> None:
    assert _summary(types.SimpleNamespace(summary="Explicit"), None) == "Explicit"
    assert _summary(types.SimpleNamespace(summary=None), None) == ""
    assert _summary(types.SimpleNamespace(), ping) == "Ping endpoint."
    assert _summary(types.SimpleNamespace(), object()) == ""

    def undocumented() -> None:
        pass

    assert _summary(types.SimpleNamespace(), undocumented) == ""


def test_flatten_dependencies_variants() -> None:
    route = types.SimpleNamespace(
        dependant=_FakeDependant(
            dependencies=[_FakeDependant(call=_dep_a, dependencies=[_FakeDependant(call=_dep_b)])]
        ),
        dependencies=[],
    )
    assert _flatten_dependencies(route) == ("_dep_a", "_dep_b")

    route_without_dependant = types.SimpleNamespace(
        dependant=None,
        dependencies=[
            types.SimpleNamespace(dependency=_dep_a),
            types.SimpleNamespace(dependency=object()),
        ],
    )
    assert _flatten_dependencies(route_without_dependant) == ("_dep_a",)


def test_builtin_doc_paths_reads_custom_urls() -> None:
    app = types.SimpleNamespace(
        openapi_url="/api/openapi.json",
        docs_url=None,
        redoc_url="/api/docs",
    )
    paths = _builtin_doc_paths(app)
    assert "/api/openapi.json" in paths
    assert "/api/docs" in paths


def test_walk_ignores_unknown_objects() -> None:
    out: list[RouteInfo] = []
    _walk([object(), types.SimpleNamespace(path=123)], "", out, set())
    assert out == []


def test_walk_traverses_mounts() -> None:
    out: list[RouteInfo] = []
    _walk([Mount("/sub", routes=[Route("/ping", ping)])], "/api", out, set())
    assert [route.path for route in out] == ["/api/sub/ping"]


def test_walk_skips_mounts_without_routes() -> None:
    out: list[RouteInfo] = []
    _walk([Mount("/empty", app=_noop_app)], "", out, set())
    assert out == []


def test_walk_effective_candidates() -> None:
    effective = types.SimpleNamespace(
        path="/full",
        original_route=types.SimpleNamespace(path="/users"),
        name="list_users",
        endpoint=ping,
        methods={"GET"},
        tags=None,
        deprecated=False,
        response_model=None,
        status_code=None,
        dependant=None,
        dependencies=[],
    )
    nested_included = types.SimpleNamespace(effective_candidates=lambda: [effective])
    skipped = types.SimpleNamespace(
        path="/skip", original_route=types.SimpleNamespace(path="/skip")
    )
    fallback = types.SimpleNamespace(path=None, original_route=types.SimpleNamespace(path="/rel"))
    out: list[RouteInfo] = []
    _walk_effective([nested_included, skipped, fallback], "/v1", out, {"/skip"})
    assert [(route.path, route.name) for route in out] == [
        ("/full", "list_users"),
        ("/v1/rel", ""),
    ]


def test_walk_effective_handles_mount_candidates() -> None:
    mount = Mount("/sub", routes=[Route("/ping", ping)])
    candidate = types.SimpleNamespace(path="/m/sub", original_route=None, starlette_route=mount)
    out: list[RouteInfo] = []
    _walk_effective([candidate], "", out, set())
    assert [route.path for route in out] == ["/m/sub/ping"]


def test_walk_effective_handles_plain_starlette_routes() -> None:
    route = Route("/plain", ping, name="plain")
    candidate = types.SimpleNamespace(path=None, original_route=None, starlette_route=route)
    out: list[RouteInfo] = []
    _walk_effective([candidate], "/v1", out, set())
    assert [route.path for route in out] == ["/v1/plain"]

    skipped = types.SimpleNamespace(path="/v1/plain", original_route=None, starlette_route=route)
    skipped_out: list[RouteInfo] = []
    _walk_effective([skipped], "/v1", skipped_out, {"/v1/plain"})
    assert skipped_out == []


def test_extract_routes_without_routes() -> None:
    assert extract_routes(object()) == []
