"""Live route extraction tests."""

from __future__ import annotations

from typing import Any

from markdownizer_fastapi.routes import RouteInfo, extract_routes


def _by_path(routes: list[RouteInfo], path: str) -> RouteInfo:
    for route in routes:
        if route.path == path:
            return route
    raise AssertionError(f"no route at {path}: {[route.path for route in routes]}")


def test_documentation_routes_are_filtered(demo: Any) -> None:
    paths = [route.path for route in extract_routes(demo.app)]
    assert "/docs" not in paths
    assert "/redoc" not in paths
    assert "/openapi.json" not in paths


def test_router_prefix_is_reflected(demo: Any) -> None:
    paths = [route.path for route in extract_routes(demo.app)]
    assert "/v1/users" in paths
    assert "/v1/users/{user_id}" in paths
    assert "/v1/me" in paths


def test_get_route_metadata(demo: Any) -> None:
    routes = extract_routes(demo.app)
    users = _by_path(routes, "/v1/users")
    assert users.methods == ("GET",)
    assert users.endpoint_module == f"{demo.package}.api"
    assert users.endpoint_qualname == "list_users"
    assert users.summary == "List users for the current tenant."
    assert users.tags == ("users",)
    assert users.response_model == "list[UserOut]"
    assert users.dependencies == ("get_db",)
    assert users.status_code is None


def test_post_route_metadata(demo: Any) -> None:
    routes = extract_routes(demo.app)
    create = next(
        route for route in routes if route.path == "/v1/users" and route.methods == ("POST",)
    )
    assert create.endpoint_qualname == "create_user"
    assert create.response_model == "UserOut"
    assert create.status_code == 201


def test_dependency_chain(demo: Any) -> None:
    routes = extract_routes(demo.app)
    user = _by_path(routes, "/v1/users/{user_id}")
    assert user.dependencies == ("get_current_user", "get_db")


def test_bound_method_endpoint(demo: Any) -> None:
    routes = extract_routes(demo.app)
    me = _by_path(routes, "/v1/me")
    assert me.endpoint_qualname == "UserHandlers.me"


def test_dynamic_endpoints_have_empty_identity(demo: Any) -> None:
    routes = extract_routes(demo.app)
    dynamic = _by_path(routes, "/dynamic")
    assert dynamic.endpoint_qualname == ""
    lambda_route = _by_path(routes, "/hooks/github")
    assert lambda_route.endpoint_qualname == "<lambda>"


def test_routes_are_sorted_and_deterministic(demo: Any) -> None:
    routes = extract_routes(demo.app)
    keys = [(route.path, route.methods, route.name) for route in routes]
    assert keys == sorted(keys)
    assert routes == extract_routes(demo.app)


def test_route_info_serialization(demo: Any) -> None:
    users = _by_path(extract_routes(demo.app), "/v1/users")
    data = users.to_dict()
    assert data["methods"] == ["GET"]
    assert data["matched"] is False
    assert data["symbol_id"] is None
