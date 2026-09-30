"""Enrichment and rendering tests."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from markdownizer.ir import build_project_ir
from markdownizer_fastapi.enrich import EnrichedProject, enriched_hash, match_routes
from markdownizer_fastapi.render import render_llms_txt, render_routes_markdown
from markdownizer_fastapi.routes import RouteInfo, extract_routes


def _enriched(demo: Any) -> EnrichedProject:
    ir = build_project_ir(demo.root)
    routes = match_routes(ir, extract_routes(demo.app))
    return EnrichedProject(ir=ir, routes=routes, enriched_hash=enriched_hash(ir, routes))


def _key(route: Any) -> tuple[str, tuple[str, ...]]:
    return (route.path, route.methods)


def test_routes_match_ir_symbols(demo: Any) -> None:
    project = _enriched(demo)
    matched = {_key(route): route for route in project.routes if route.matched}
    users = matched[("/v1/users", ("GET",))]
    assert users.symbol_id == f"{demo.package}/api.py::list_users"
    me = matched[("/v1/me", ("GET",))]
    assert me.symbol_id == f"{demo.package}/api.py::UserHandlers.me"


def test_unmatched_routes_are_reported_not_dropped(demo: Any) -> None:
    project = _enriched(demo)
    unmatched = {route.path: route for route in project.routes if not route.matched}
    assert unmatched["/dynamic"].unmatched_reason == "dynamic"
    assert unmatched["/hooks/github"].unmatched_reason == "not_in_ir"


def test_enriched_hash_uses_routes(demo: Any) -> None:
    ir = build_project_ir(demo.root)
    routes = match_routes(ir, extract_routes(demo.app))
    assert enriched_hash(ir, routes) != ir.hash
    assert enriched_hash(ir, []) == ir.hash


def test_enriched_hash_is_deterministic(demo: Any) -> None:
    assert _enriched(demo).enriched_hash == _enriched(demo).enriched_hash


def test_render_routes_markdown(demo: Any) -> None:
    text = render_routes_markdown(_enriched(demo).routes)
    assert "## HTTP API" in text
    assert "`GET /v1/users`" in text
    assert "::list_users`" in text
    assert "Response model: `list[UserOut]`" in text
    assert "Dependencies: `get_db`" in text
    assert "Runtime-only routes" in text
    assert "`GET /dynamic`" in text


def test_render_routes_markdown_escapes_summaries(demo: Any) -> None:
    routes = match_routes(build_project_ir(demo.root), extract_routes(demo.app))
    replaced = [replace(route, summary="Line one\n`code` and more") for route in routes]
    text = render_routes_markdown(replaced)
    assert "Line one 'code' and more" in text


def test_render_llms_txt(demo: Any) -> None:
    project = _enriched(demo)
    text = render_llms_txt(project, "/_markdownizer")
    assert text.startswith(f"# {project.ir.name}")
    assert "## Context" in text
    assert "## HTTP API" in text
    assert "`GET /v1/users`" in text
    assert "def " not in text
    assert "return" not in text


def test_render_routes_markdown_edge_cases() -> None:
    matched = RouteInfo(path="/x", methods=(), name="x", matched=True)
    unmatched = RouteInfo(
        path="/y",
        methods=(),
        name="y",
        matched=False,
        unmatched_reason="dynamic",
        summary="Runtime route",
    )
    text = render_routes_markdown([matched, unmatched])
    assert "- `ANY /x`" not in text
    assert "### `ANY /x`" in text
    assert "- `ANY /y` (dynamic) — Runtime route" in text


def test_render_llms_txt_without_routes(demo: Any) -> None:
    ir = build_project_ir(demo.root)
    project = EnrichedProject(ir=ir, routes=[], enriched_hash=ir.hash)
    text = render_llms_txt(project, "/_markdownizer")
    assert "## HTTP API" not in text
    assert "## Packages" in text
