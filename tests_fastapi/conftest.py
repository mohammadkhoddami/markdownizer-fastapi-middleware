"""Fixtures for the FastAPI integration tests.

Each test gets a fresh demo package on a temporary directory, written to disk
so the static analyzer sees real sources, then imported so the endpoint
``__module__``/``__qualname__`` values can be matched against the IR.
"""

from __future__ import annotations

import importlib
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from markdownizer_fastapi import ArtifactService, MarkdownizerConfig  # noqa: E402
from markdownizer_fastapi.enrich import EnrichedProject  # noqa: E402

_API_SOURCE = '''\
"""User API endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel


class UserOut(BaseModel):
    """A single user."""

    id: int


def get_db():
    """Database dependency."""
    return None


def get_current_user():
    """Authentication dependency."""
    return None


class UserHandlers:
    """Bound-method endpoint container."""

    def me(self) -> UserOut:
        """Return the current user."""
        return UserOut(id=1)


handlers = UserHandlers()

router = APIRouter(tags=["users"], dependencies=[Depends(get_db)])


@router.get("/users", response_model=list[UserOut])
def list_users() -> list[UserOut]:
    """List users for the current tenant."""
    return []


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(payload: UserOut) -> UserOut:
    """Create a user."""
    return payload


@router.get(
    "/users/{user_id}",
    response_model=UserOut,
    dependencies=[Depends(get_current_user)],
)
def get_user(user_id: int) -> UserOut:
    """Fetch a single user."""
    return UserOut(id=user_id)


router.add_api_route("/me", handlers.me, methods=["GET"])
'''

_APP_SOURCE = '''\
"""Demo FastAPI application for the Markdownizer tests."""

from functools import partial
from typing import Any

from fastapi import FastAPI

from .api import router

app = FastAPI(title="Demo API")
app.include_router(router, prefix="/v1")


def dynamic_endpoint() -> dict[str, Any]:
    """Endpoint inspected through a partial (no qualname)."""
    return {"status": "ok"}


app.add_api_route("/dynamic", partial(dynamic_endpoint), methods=["GET"])
app.add_api_route("/hooks/github", lambda: {"ok": True}, methods=["POST"])
'''


@dataclass
class Demo:
    """A demo project plus its imported FastAPI app."""

    root: Path
    package: str
    app: Any


class BrokenService(ArtifactService):
    """A service whose build always fails, for error-path tests."""

    def __init__(
        self,
        root: Path,
        mode: str = "lazy",
        error: type[Exception] = RuntimeError,
    ) -> None:
        super().__init__(MarkdownizerConfig(project_root=str(root), debug=True, mode=mode))
        self._error = error

    def project(self, refresh: bool = False) -> EnrichedProject:
        raise self._error("boom")


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Demo]:
    suffix = uuid.uuid4().hex[:8]
    package = f"demo_{suffix}"
    package_dir = tmp_path / package
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text(f'"""{package} demo package."""\n')
    (package_dir / "api.py").write_text(_API_SOURCE)
    (package_dir / "app.py").write_text(_APP_SOURCE)

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("MARKDOWNIZER_FASTAPI_ENABLED", "1")

    app_module = importlib.import_module(f"{package}.app")
    yield Demo(root=tmp_path, package=package, app=app_module.app)

    for name in list(sys.modules):
        if name == package or name.startswith(package + "."):
            del sys.modules[name]
