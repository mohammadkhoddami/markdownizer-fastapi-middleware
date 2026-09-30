"""FastAPI integration for Markdownizer.

Serve a deterministic, token-budgeted, hash-stable view of a Python project
over HTTP — and, when the live application is available, enrich it with the
composed FastAPI route table (router prefixes, dependency chains, response
models) that static analysis alone cannot see.

The upstream ``markdownizer`` contract is preserved: no LLM, no cloud, no
runtime dependencies beyond FastAPI/Starlette, no project code execution
beyond what the host application already does.
"""

from markdownizer_fastapi.config import MarkdownizerConfig
from markdownizer_fastapi.enrich import EnrichedProject
from markdownizer_fastapi.middleware import MarkdownizerMiddleware
from markdownizer_fastapi.plugin import MarkdownizerPlugin
from markdownizer_fastapi.router import MarkdownizerRouter
from markdownizer_fastapi.routes import RouteInfo
from markdownizer_fastapi.service import ArtifactService

__version__ = "0.1.0"

__all__ = [
    "ArtifactService",
    "EnrichedProject",
    "MarkdownizerConfig",
    "MarkdownizerMiddleware",
    "MarkdownizerPlugin",
    "MarkdownizerRouter",
    "RouteInfo",
]
