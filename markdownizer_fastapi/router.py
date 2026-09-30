"""APIRouter variant of the Markdownizer endpoints.

Use this when middleware cannot be added (e.g. middleware order constraints)::

    app.include_router(MarkdownizerRouter(project_root="."), prefix="/_markdownizer")

The router and the middleware share the exact same request handling through
:func:`markdownizer_fastapi.http.prepare_response`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from markdownizer_fastapi.config import MarkdownizerConfig, resolve_config
from markdownizer_fastapi.http import prepare_response
from markdownizer_fastapi.service import ArtifactService


class MarkdownizerRouter(APIRouter):
    """An ``APIRouter`` exposing the Markdownizer artifact endpoints.

    Args:
        config: Optional pre-built configuration.
        service: Optional shared :class:`ArtifactService`.
        **kwargs: Configuration overrides when ``config``/``service`` are not
            given.
    """

    def __init__(
        self,
        config: MarkdownizerConfig | None = None,
        service: ArtifactService | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        if service is not None:
            if config is not None or kwargs:
                raise ValueError("pass either 'service' or 'config'/keyword options, not both")
            self.service = service
        else:
            self._config = resolve_config(config, kwargs)
            self.service = ArtifactService(self._config)
        self._config = self.service.config

        self.add_api_route("/manifest", self._manifest, methods=["GET"])
        self.add_api_route("/stats", self._stats, methods=["GET"])
        self.add_api_route("/context.md", self._context_md, methods=["GET"])
        self.add_api_route("/project.json", self._project_json, methods=["GET"])
        self.add_api_route("/routes.json", self._routes_json, methods=["GET"])
        self.add_api_route("/llms.txt", self._llms_txt, methods=["GET"])
        self.add_api_route("/openapi-ref.json", self._openapi_ref, methods=["GET"])
        self.add_api_route("/refresh", self._refresh, methods=["POST"])

    def _respond(self, request: Request, path: str) -> Response:
        prepared = prepare_response(
            self.service,
            self._config,
            method=request.method,
            path=path,
            query_string=request.url.query,
            headers={key.lower(): value for key, value in request.headers.items()},
            client_host=request.client.host if request.client else None,
        )
        return Response(
            content=prepared.body,
            status_code=prepared.status_code,
            media_type=prepared.content_type,
            headers=dict(prepared.headers),
        )

    def _manifest(self, request: Request) -> Response:
        return self._respond(request, "/manifest")

    def _stats(self, request: Request) -> Response:
        return self._respond(request, "/stats")

    def _context_md(self, request: Request) -> Response:
        return self._respond(request, "/context.md")

    def _project_json(self, request: Request) -> Response:
        return self._respond(request, "/project.json")

    def _routes_json(self, request: Request) -> Response:
        return self._respond(request, "/routes.json")

    def _llms_txt(self, request: Request) -> Response:
        return self._respond(request, "/llms.txt")

    def _openapi_ref(self, request: Request) -> Response:
        return self._respond(request, "/openapi-ref.json")

    def _refresh(self, request: Request) -> Response:
        return self._respond(request, "/refresh")
