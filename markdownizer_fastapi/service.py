"""Artifact service: build, cache, enrich, and render Markdownizer artifacts.

The service is the single source of truth for a running application. It never
writes to disk and never returns a partially built artifact: builds happen
under a lock and the result is cached until the process restarts, or until
source files change in ``dev_reload`` mode.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from markdownizer.ir import ProjectIR, build_project_ir
from markdownizer.optimizer import OptimizedContext, count_tokens, optimize_context
from markdownizer.optimizer.rank import rank_ir
from markdownizer.scanner import scan_python_files
from markdownizer_fastapi.config import MarkdownizerConfig
from markdownizer_fastapi.enrich import EnrichedProject, enriched_hash, match_routes
from markdownizer_fastapi.render import render_llms_txt, render_routes_markdown
from markdownizer_fastapi.routes import RouteInfo, extract_routes

logger = logging.getLogger(__name__)

_MAX_TOP = 10


def _resolve_introspection_app(app: Any | None) -> Any | None:
    """Find the first object in the ASGI wrapper chain exposing ``routes``.

    Starlette wraps user middleware differently across versions; the route
    table lives on the innermost router (or on the top-level application).
    """
    node = app
    for _ in range(8):
        if node is None:
            return None
        if getattr(node, "routes", None):
            return node
        node = getattr(node, "app", None)
    return None


def _discover_openapi_url(app: Any | None) -> str | None:
    if app is None:
        return None
    value = getattr(app, "openapi_url", None)
    if isinstance(value, str) and value:
        return value
    for route in getattr(app, "routes", ()) or ():
        if getattr(route, "name", None) == "openapi":
            path = getattr(route, "path", None)
            if isinstance(path, str) and path:
                return path
    return None


def _top_files(ir: ProjectIR, limit: int) -> list[tuple[str, float]]:
    ranked = sorted(ir.file_ranks.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:limit]


def _top_symbols(ir: ProjectIR, limit: int) -> list[tuple[str, float]]:
    ranked = sorted(ir.symbols, key=lambda symbol: (-symbol.rank, symbol.file_path, symbol.lineno))
    return [
        (f"{symbol.file_path}::{symbol.qualified_name}", symbol.rank) for symbol in ranked[:limit]
    ]


class ArtifactService:
    """Builds and caches the enriched project for one configuration.

    Args:
        config: Validated configuration.
        app: Optional FastAPI/Starlette application. When provided and
            ``config.include_routes`` is true, the live route table is
            extracted and joined with the static IR.
    """

    def __init__(self, config: MarkdownizerConfig, app: Any | None = None) -> None:
        self.config = config
        self._app = _resolve_introspection_app(app)
        self._lock = threading.RLock()
        self._project: EnrichedProject | None = None
        self._fingerprint: tuple[tuple[str, int, int], ...] | None = None
        self._built_at: str | None = None
        self.openapi_url: str | None = _discover_openapi_url(self._app)

    @property
    def built(self) -> bool:
        """Whether a build is currently cached."""
        return self._project is not None

    @property
    def built_at(self) -> str | None:
        """ISO-8601 UTC timestamp of the last build, if any."""
        return self._built_at

    def attach_app(self, app: Any | None) -> None:
        """Attach the application for route introspection when unavailable.

        Starlette instantiates user middleware before the route table is
        reachable in some versions; the middleware passes the request's
        ``scope["app"]`` here as a fallback. A service that already has an
        introspectable application is never replaced.
        """
        if app is None or getattr(self._app, "routes", None):
            return
        resolved = _resolve_introspection_app(app)
        if resolved is None:
            return
        with self._lock:
            if getattr(self._app, "routes", None):
                return
            self._app = resolved
            self.openapi_url = _discover_openapi_url(resolved)
            self._project = None

    def warmup(self) -> EnrichedProject:
        """Build eagerly (used by the ``startup`` mode)."""
        return self.project()

    def project(self, refresh: bool = False) -> EnrichedProject:
        """Return the cached enriched project, rebuilding when needed."""
        with self._lock:
            return self._project_locked(refresh)

    def _project_locked(self, refresh: bool = False) -> EnrichedProject:
        if refresh or self._project is None or self._stale_locked():
            self._rebuild_locked()
        assert self._project is not None
        return self._project

    def _stale_locked(self) -> bool:
        if self.config.mode != "dev_reload" or self._project is None:
            return False
        return self._fingerprint != self._scan_fingerprint()

    def _scan_fingerprint(self) -> tuple[tuple[str, int, int], ...]:
        root = Path(self.config.project_root).resolve()
        entries: list[tuple[str, int, int]] = []
        for path in scan_python_files(root, exclude=list(self.config.exclude) or None):
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((path.as_posix(), stat.st_mtime_ns, stat.st_size))
        return tuple(entries)

    def _rebuild_locked(self) -> None:
        ir = build_project_ir(
            Path(self.config.project_root),
            exclude=list(self.config.exclude) or None,
        )
        routes: list[RouteInfo] = []
        if self.config.include_routes and self._app is not None:
            routes = match_routes(ir, extract_routes(self._app))
        self._project = EnrichedProject(
            ir=ir,
            routes=routes,
            enriched_hash=enriched_hash(ir, routes),
        )
        self._built_at = datetime.now(timezone.utc).isoformat()
        self._fingerprint = self._scan_fingerprint() if self.config.mode == "dev_reload" else None
        logger.info(
            "Markdownizer built %s (%d modules, %d symbols, %d routes) hash=%s",
            self.config.project_root,
            ir.stats.module_count,
            ir.stats.symbol_count,
            len(routes),
            self._project.enriched_hash,
        )

    def _optimize_locked(
        self,
        project: EnrichedProject,
        *,
        max_tokens: float | None,
        profile: str | None,
        rank_method: str | None,
        query: str | None,
    ) -> OptimizedContext:
        return optimize_context(
            project.ir,
            max_tokens=self.config.max_tokens if max_tokens is None else max_tokens,
            profile=profile if profile is not None else self.config.profile,
            query=query,
            rank_method=rank_method if rank_method is not None else self.config.rank_method,
        )

    def context(
        self,
        *,
        max_tokens: float | None = None,
        profile: str | None = None,
        rank_method: str | None = None,
        query: str | None = None,
    ) -> OptimizedContext:
        """Produce a budgeted, ranked context artifact."""
        with self._lock:
            project = self._project_locked()
            return self._optimize_locked(
                project,
                max_tokens=max_tokens,
                profile=profile,
                rank_method=rank_method,
                query=query,
            )

    def context_text(
        self,
        *,
        max_tokens: float | None = None,
        profile: str | None = None,
        rank_method: str | None = None,
        query: str | None = None,
    ) -> str:
        """Produce context text, with the HTTP API layer appended when available.

        The route section is informational and is not counted against the
        token budget.
        """
        with self._lock:
            project = self._project_locked()
            text = self._optimize_locked(
                project,
                max_tokens=max_tokens,
                profile=profile,
                rank_method=rank_method,
                query=query,
            ).text
            if project.routes:
                text = text.rstrip() + "\n\n" + render_routes_markdown(project.routes)
            return text

    def manifest(self) -> dict[str, Any]:
        """Machine-readable build manifest."""
        project = self.project()
        return {
            "name": project.ir.name,
            "hash": project.ir.hash,
            "enriched_hash": project.enriched_hash,
            "ir_version": project.ir.ir_version,
            "built_at": self._built_at,
            "python_version": project.ir.python_version,
            "route_count": len(project.routes),
            "stats": project.ir.stats.to_dict(),
        }

    def project_dict(self) -> dict[str, Any]:
        """Full IR serialization plus the enriched route table."""
        return self.project().to_dict()

    def routes_dict(self) -> dict[str, Any]:
        """The live route table."""
        project = self.project()
        return {
            "route_count": len(project.routes),
            "routes": [route.to_dict() for route in project.routes],
        }

    def stats_dict(self) -> dict[str, Any]:
        """Project statistics and top-ranked files/symbols."""
        with self._lock:
            project = self._project_locked()
            rank_ir(project.ir, method=self.config.rank_method)
            return {
                "name": project.ir.name,
                "ir_version": project.ir.ir_version,
                "hash": project.ir.hash,
                "enriched_hash": project.enriched_hash,
                "rank_method": self.config.rank_method,
                "stats": project.ir.stats.to_dict(),
                "top_files": [
                    {"path": path, "rank": score}
                    for path, score in _top_files(project.ir, _MAX_TOP)
                ],
                "top_symbols": [
                    {"symbol": name, "rank": score}
                    for name, score in _top_symbols(project.ir, _MAX_TOP)
                ],
            }

    def stats_report(self) -> str:
        """Human-readable statistics report."""
        data = self.stats_dict()
        project = self.project()
        token_estimate = sum(count_tokens(module.source) for module in project.ir.modules)
        lines = [
            f"Project: {project.ir.name} (IR v{project.ir.ir_version})",
            f"Hash: {project.ir.hash}",
            f"Enriched hash: {project.enriched_hash}",
            f"Python files: {project.ir.stats.module_count}",
            f"Packages: {project.ir.stats.package_count}",
            (
                f"Symbols: {project.ir.stats.symbol_count} "
                f"(public: {project.ir.stats.public_count}, "
                f"documented: {project.ir.stats.documented_count})"
            ),
            f"HTTP routes: {len(project.routes)}",
            f"Lines: {project.ir.stats.line_count}",
            f"Estimated tokens (chars/3.3): ~{token_estimate:.0f}",
            "",
            "Top files by importance:",
        ]
        for entry in data["top_files"]:
            lines.append(f"  {entry['rank']:.3f}  {entry['path']}")
        lines.append("")
        lines.append("Top symbols:")
        for entry in data["top_symbols"]:
            lines.append(f"  {entry['rank']:.3f}  {entry['symbol']}")
        return "\n".join(lines) + "\n"

    def llms_txt(self) -> str:
        """Render the ``llms.txt`` index."""
        return render_llms_txt(self.project(), self.config.prefix)
