"""Pure ASGI middleware serving Markdownizer artifacts.

The middleware handles only its configured prefix (and optionally
``/llms.txt``); every other request is passed through untouched. It never
imports or executes project code beyond what the running application already
did.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from markdownizer_fastapi.config import MarkdownizerConfig, resolve_config
from markdownizer_fastapi.http import PreparedResponse, prepare_response
from markdownizer_fastapi.service import ArtifactService

logger = logging.getLogger(__name__)

_INTERNAL_ERROR = b'{"error": {"code": "internal_error", "message": "Internal error."}}'


class MarkdownizerMiddleware:
    """Serve deterministic project context under ``config.prefix``.

    Usage::

        app.add_middleware(
            MarkdownizerMiddleware,
            project_root=".",
            include_source=False,
        )

    Args:
        app: The wrapped ASGI application.
        config: Optional pre-built configuration.
        service: Optional shared :class:`ArtifactService` (used by
            :class:`~markdownizer_fastapi.plugin.MarkdownizerPlugin` so the
            middleware and the plugin share one cache).
        **kwargs: Configuration overrides when ``config``/``service`` are not
            given.
    """

    def __init__(
        self,
        app: ASGIApp,
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
            self.config = resolve_config(config, kwargs)
            self.service = ArtifactService(self.config, app=app)
        self.config = self.service.config
        self._warmup = self.config.mode == "startup"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            scope_app = scope.get("app")
            if scope_app is not None:
                self.service.attach_app(scope_app)
            if self._warmup:
                await self.app(scope, receive, self._lifespan_send(send))
            else:
                await self.app(scope, receive, send)
            return
        if scope_type != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        if not self._owns_path(path):
            await self.app(scope, receive, send)
            return
        await self._handle_http(scope, send)

    def _owns_path(self, path: str) -> bool:
        if path == self.config.prefix or path.startswith(self.config.prefix + "/"):
            return True
        return self.config.llms_at_root and path == "/llms.txt"

    def _lifespan_send(self, send: Send) -> Send:
        async def wrapped(message: Message) -> None:
            if message["type"] == "lifespan.startup.complete":
                try:
                    await run_in_threadpool(self.service.warmup)
                except Exception:
                    logger.exception("Markdownizer startup warmup failed")
            await send(message)

        return wrapped

    async def _handle_http(self, scope: Scope, send: Send) -> None:
        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", ""))
        scope_app = scope.get("app")
        if scope_app is not None:
            self.service.attach_app(scope_app)
        if self.config.llms_at_root and path == "/llms.txt":
            relative = "/llms.txt"
        else:
            relative = path[len(self.config.prefix) :] or "/"

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        client = scope.get("client")
        client_host = client[0] if client else None
        query_string = scope.get("query_string", b"").decode("latin-1")

        try:
            response = await run_in_threadpool(
                prepare_response,
                self.service,
                self.config,
                method=method,
                path=relative,
                query_string=query_string,
                headers=headers,
                client_host=client_host,
            )
        except Exception:
            logger.exception("Markdownizer request handling failed")
            response = PreparedResponse(500, "application/json", _INTERNAL_ERROR)

        raw_headers = [
            (b"content-type", response.content_type.encode("latin-1")),
            (b"content-length", str(len(response.body)).encode("latin-1")),
        ]
        raw_headers.extend(
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in response.headers.items()
        )
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": raw_headers,
            }
        )
        body = b"" if method == "HEAD" else response.body
        await send({"type": "http.response.body", "body": body})
