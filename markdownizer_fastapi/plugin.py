"""Plugin convenience wrapper: route enrichment plus middleware installation.

The plugin is the richer integration: it introspects the live application's
route table and joins it with the static Project IR, then can install the
middleware (or provide a router) sharing the same cache.
"""

from __future__ import annotations

from typing import Any

from markdownizer_fastapi.config import MarkdownizerConfig, resolve_config
from markdownizer_fastapi.enrich import EnrichedProject
from markdownizer_fastapi.middleware import MarkdownizerMiddleware
from markdownizer_fastapi.router import MarkdownizerRouter
from markdownizer_fastapi.routes import RouteInfo
from markdownizer_fastapi.service import ArtifactService


class MarkdownizerPlugin:
    """FastAPI integration that enriches context with the live route table.

    Usage::

        plugin = MarkdownizerPlugin(app, project_root="src").install()

    Args:
        app: The FastAPI/Starlette application to introspect.
        config: Optional pre-built configuration.
        service: Optional shared :class:`ArtifactService`.
        **kwargs: Configuration overrides when ``config``/``service`` are not
            given.
    """

    def __init__(
        self,
        app: Any,
        config: MarkdownizerConfig | None = None,
        service: ArtifactService | None = None,
        **kwargs: Any,
    ) -> None:
        self.app = app
        if service is not None:
            if config is not None or kwargs:
                raise ValueError("pass either 'service' or 'config'/keyword options, not both")
            self.service = service
        else:
            self.service = ArtifactService(resolve_config(config, kwargs), app=app)

    @property
    def config(self) -> MarkdownizerConfig:
        """The resolved configuration."""
        return self.service.config

    @property
    def routes(self) -> list[RouteInfo]:
        """The matched live route table (builds on first access)."""
        return list(self.service.project().routes)

    def project(self) -> EnrichedProject:
        """Return the cached enriched project, building it when needed."""
        return self.service.project()

    def context_text(self, **kwargs: Any) -> str:
        """Return the context artifact, including the HTTP API layer."""
        return self.service.context_text(**kwargs)

    def install(self) -> MarkdownizerPlugin:
        """Register the middleware on the application and return the plugin."""
        self.app.add_middleware(MarkdownizerMiddleware, service=self.service)
        return self

    def router(self) -> MarkdownizerRouter:
        """Return a router sharing this plugin's service and cache."""
        return MarkdownizerRouter(service=self.service)

    def warmup(self) -> EnrichedProject:
        """Build eagerly, for explicit startup hooks."""
        return self.service.warmup()
