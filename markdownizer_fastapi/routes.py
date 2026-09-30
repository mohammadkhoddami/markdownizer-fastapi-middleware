"""Live route extraction from a FastAPI/Starlette application.

The static IR can see handler functions, but only the running app knows the
composed HTTP surface: router prefixes, dependency chains, and response
models. This module reads ``app.routes`` without importing or executing any
project code beyond what the host application already did.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from starlette.routing import Mount, Route


@dataclass(frozen=True)
class RouteInfo:
    """One HTTP route exposed by the host application."""

    path: str
    methods: tuple[str, ...]
    name: str
    endpoint_module: str = ""
    endpoint_qualname: str = ""
    summary: str = ""
    tags: tuple[str, ...] = ()
    deprecated: bool = False
    status_code: int | None = None
    response_model: str | None = None
    dependencies: tuple[str, ...] = ()
    symbol_id: str | None = None
    matched: bool = False
    unmatched_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol_id": self.symbol_id,
            "path": self.path,
            "methods": list(self.methods),
            "name": self.name,
            "summary": self.summary,
            "tags": list(self.tags),
            "deprecated": self.deprecated,
            "status_code": self.status_code,
            "response_model": self.response_model,
            "dependencies": list(self.dependencies),
            "matched": self.matched,
            "unmatched_reason": self.unmatched_reason,
        }


def _join_path(prefix: str, path: str) -> str:
    if not prefix:
        return path or "/"
    if not path:
        return prefix or "/"
    return prefix.rstrip("/") + "/" + path.lstrip("/")


def _builtin_doc_paths(app: Any) -> set[str]:
    paths: set[str] = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    for attribute in ("openapi_url", "docs_url", "redoc_url"):
        value = getattr(app, attribute, None)
        if isinstance(value, str) and value:
            paths.add(value)
    return paths


def _model_name(model: Any) -> str | None:
    if model is None:
        return None
    origin = typing.get_origin(model)
    if origin is not None:
        origin_name = getattr(origin, "__name__", str(origin))
        arguments = ", ".join(_model_name(arg) or str(arg) for arg in typing.get_args(model))
        return f"{origin_name}[{arguments}]"
    name = getattr(model, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return str(model)


def _summary(route: Any, endpoint: Any) -> str:
    explicit = getattr(route, "summary", None)
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if endpoint is None or not (inspect.isroutine(endpoint) or inspect.isclass(endpoint)):
        return ""
    docstring = inspect.getdoc(endpoint)
    if not docstring:
        return ""
    return docstring.splitlines()[0].strip()


def _flatten_dependencies(route: Any) -> tuple[str, ...]:
    names: list[str] = []

    def visit(dependant: Any) -> None:
        for child in getattr(dependant, "dependencies", ()) or ():
            call = getattr(child, "call", None)
            name = getattr(call, "__name__", None)
            if isinstance(name, str) and name:
                names.append(name)
            visit(child)

    root = getattr(route, "dependant", None)
    if root is not None:
        visit(root)
    for dependency in getattr(route, "dependencies", ()) or ():
        target = getattr(dependency, "dependency", None)
        name = getattr(target, "__name__", None)
        if isinstance(name, str) and name:
            names.append(name)
    return tuple(sorted(dict.fromkeys(names)))


def _methods(route: Any) -> tuple[str, ...]:
    raw = getattr(route, "methods", None) or ()
    return tuple(sorted({method.upper() for method in raw if method.upper() != "HEAD"}))


def _route_info(route: Any, path: str, name: str) -> RouteInfo:
    endpoint = getattr(route, "endpoint", None)
    status_code = getattr(route, "status_code", None)
    return RouteInfo(
        path=path,
        methods=_methods(route),
        name=name,
        endpoint_module=str(getattr(endpoint, "__module__", "") or ""),
        endpoint_qualname=str(
            getattr(endpoint, "__qualname__", None) or getattr(endpoint, "__name__", "") or ""
        ),
        summary=_summary(route, endpoint),
        tags=tuple(sorted(str(tag) for tag in (getattr(route, "tags", None) or ()))),
        deprecated=bool(getattr(route, "deprecated", False)),
        status_code=status_code if isinstance(status_code, int) else None,
        response_model=_model_name(getattr(route, "response_model", None)),
        dependencies=_flatten_dependencies(route),
    )


def _walk(routes: Iterable[Any], prefix: str, out: list[RouteInfo], skip: set[str]) -> None:
    for route in routes:
        path = getattr(route, "path", None)
        if isinstance(route, Mount):
            mount_path = path if isinstance(path, str) else ""
            full_mount = _join_path(prefix, mount_path)
            nested = getattr(route, "routes", None)
            if not nested:
                nested = getattr(getattr(route, "app", None), "routes", None)
            if nested:
                _walk(nested, full_mount, out, skip)
            continue

        effective = getattr(route, "effective_candidates", None)
        if callable(effective):
            _walk_effective(effective(), prefix, out, skip)
            continue

        if isinstance(route, Route) and isinstance(path, str):
            full_path = _join_path(prefix, path)
            if full_path in skip:
                continue
            name = str(getattr(route, "name", "") or "")
            out.append(_route_info(route, full_path, name))


def _walk_effective(
    candidates: Iterable[Any], prefix: str, out: list[RouteInfo], skip: set[str]
) -> None:
    """Walk FastAPI's lazy include-router candidates (FastAPI >= 0.14x).

    Newer FastAPI versions no longer copy included routes into the parent;
    ``_IncludedRouter`` wrappers expose their effective routes lazily via
    ``effective_candidates()``.
    """
    for candidate in candidates:
        effective = getattr(candidate, "effective_candidates", None)
        if callable(effective):
            _walk_effective(effective(), prefix, out, skip)
            continue

        candidate_path = getattr(candidate, "path", None)
        original = getattr(candidate, "original_route", None)
        if original is not None:
            fallback = _join_path(prefix, str(getattr(original, "path", "") or ""))
            full_path = candidate_path if isinstance(candidate_path, str) else fallback
            if full_path in skip:
                continue
            out.append(_route_info(candidate, full_path, str(getattr(candidate, "name", "") or "")))
            continue

        starlette_route = getattr(candidate, "starlette_route", None)
        if isinstance(starlette_route, Mount):
            mount_path = str(getattr(starlette_route, "path", "") or "")
            full_mount = candidate_path if isinstance(candidate_path, str) else ""
            if not full_mount:
                full_mount = _join_path(prefix, mount_path)
            nested = getattr(starlette_route, "routes", None) or getattr(
                getattr(starlette_route, "app", None), "routes", None
            )
            if nested:
                _walk(nested, full_mount, out, skip)
            continue
        if starlette_route is not None:
            fallback = _join_path(prefix, str(getattr(starlette_route, "path", "") or ""))
            full_path = candidate_path if isinstance(candidate_path, str) else fallback
            if full_path in skip:
                continue
            name = str(getattr(starlette_route, "name", "") or "")
            out.append(_route_info(starlette_route, full_path, name))


def extract_routes(app: Any) -> list[RouteInfo]:
    """Return the deterministic HTTP route table of a FastAPI/Starlette app.

    FastAPI's built-in documentation routes (``/docs``, ``/openapi.json``, …)
    are filtered out. Mounted sub-applications are traversed recursively.
    """
    collected: list[RouteInfo] = []
    _walk(getattr(app, "routes", ()) or (), "", collected, _builtin_doc_paths(app))
    collected.sort(key=lambda route: (route.path, route.methods, route.name))
    return collected
