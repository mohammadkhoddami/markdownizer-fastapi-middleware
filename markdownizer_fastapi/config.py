"""Validated configuration for the Markdownizer FastAPI integration.

The configuration is a frozen dataclass: every field is validated once at
construction time, so request handling never has to re-check static options.
Environment variables prefixed with ``MARKDOWNIZER_FASTAPI_`` provide defaults;
explicit arguments always win.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from markdownizer.optimizer.profiles import PROFILES
from markdownizer.renderer import SourceMode

DEFAULT_PREFIX = "/_markdownizer"
ENV_PREFIX = "MARKDOWNIZER_FASTAPI_"
VALID_MODES = ("lazy", "startup", "dev_reload")
VALID_RANK_METHODS = ("pagerank", "fanout", "simple")


def parse_bool(value: str) -> bool:
    """Parse a permissive boolean from a string value."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_source(value: str) -> SourceMode:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    if normalized == "signature":
        return "signature"
    raise ValueError(f"invalid include_source value: {value!r}")


def _parse_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class MarkdownizerConfig:
    """Configuration for :class:`MarkdownizerMiddleware` and friends.

    Attributes:
        project_root: Directory scanned by the upstream ``markdownizer``
            analysis. Relative paths are resolved against the process working
            directory.
        prefix: URL prefix serving the artifacts. Normalized to ``/name``.
        include_source: ``True`` (full source), ``False`` (no source), or
            ``"signature"`` (declaration lines only). Defaults to ``False``:
            metadata only.
        include_comments: Forwarded to the upstream render options.
        profile: Default context profile (any upstream profile name).
        max_tokens: Default token budget for ``context.md``.
        rank_method: ``pagerank`` (default), ``fanout``, or ``simple``.
        exclude: Glob patterns forwarded to the upstream scanner.
        include_routes: Serve the live HTTP route table and append it to the
            context artifact. Requires the app to be passed to the service.
        enable_env: Environment variable that must be truthy for the endpoints
            to answer when ``require_enable_env`` is set.
        require_enable_env: When true (default), endpoints return 404 unless
            the enable environment variable is set or ``debug`` is true.
        debug: Bypasses the enable gate and enables ``dev_reload`` behavior.
        auth_token: When set, requests must present this token via
            ``Authorization: Bearer`` or ``X-Markdownizer-Token``. Required for
            ``POST /refresh``.
        allow_clients: Optional CIDR allowlist for client addresses.
        max_context_tokens: Hard ceiling for ``?max_tokens=`` overrides.
        mode: ``lazy`` (build on first request), ``startup`` (warm up during
            the ASGI lifespan startup), or ``dev_reload`` (rebuild when source
            files change).
        llms_at_root: Also serve ``/llms.txt`` outside the prefix.
    """

    project_root: str = "."
    prefix: str = DEFAULT_PREFIX
    include_source: SourceMode = False
    include_comments: bool = True
    profile: str = "api"
    max_tokens: float = 20000.0
    rank_method: str = "pagerank"
    exclude: tuple[str, ...] = ()
    include_routes: bool = True
    enable_env: str = ENV_PREFIX + "ENABLED"
    require_enable_env: bool = True
    debug: bool = False
    auth_token: str | None = None
    allow_clients: tuple[str, ...] = ()
    max_context_tokens: float = 100000.0
    mode: str = "lazy"
    llms_at_root: bool = False

    def __post_init__(self) -> None:
        prefix = self.prefix.strip()
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        prefix = "/" + prefix.strip("/")
        if prefix == "/":
            raise ValueError("prefix must not be '/'")
        if "?" in prefix or "#" in prefix or " " in prefix:
            raise ValueError(f"prefix must be a URL path: {self.prefix!r}")
        object.__setattr__(self, "prefix", prefix)

        if self.profile not in PROFILES:
            choices = ", ".join(sorted(PROFILES))
            raise ValueError(f"unknown profile: {self.profile!r} (expected one of: {choices})")

        if not isinstance(self.include_source, bool) and self.include_source != "signature":
            raise ValueError(
                f"include_source must be True, False, or 'signature': {self.include_source!r}"
            )

        if self.mode not in VALID_MODES:
            choices = ", ".join(VALID_MODES)
            raise ValueError(f"unknown mode: {self.mode!r} (expected one of: {choices})")
        if self.rank_method not in VALID_RANK_METHODS:
            choices = ", ".join(VALID_RANK_METHODS)
            raise ValueError(
                f"unknown rank method: {self.rank_method!r} (expected one of: {choices})"
            )
        if not self.max_tokens > 0:
            raise ValueError(f"max_tokens must be positive: {self.max_tokens!r}")
        if not self.max_context_tokens > 0:
            raise ValueError(f"max_context_tokens must be positive: {self.max_context_tokens!r}")
        if self.max_context_tokens < self.max_tokens:
            raise ValueError("max_context_tokens must be greater than or equal to max_tokens")
        if self.auth_token is not None and not self.auth_token.strip():
            raise ValueError("auth_token must not be empty when provided")
        if not self.enable_env.strip():
            raise ValueError("enable_env must not be empty")

        normalized_clients: list[str] = []
        for entry in self.allow_clients:
            try:
                network = ipaddress.ip_network(entry, strict=False)
            except ValueError as exc:
                raise ValueError(f"invalid allow_clients entry {entry!r}: {exc}") from None
            normalized_clients.append(str(network))
        object.__setattr__(self, "allow_clients", tuple(normalized_clients))
        object.__setattr__(self, "exclude", tuple(self.exclude))

    @classmethod
    def from_env(cls, overrides: Mapping[str, Any] | None = None) -> MarkdownizerConfig:
        """Build a configuration from environment variables and overrides.

        Explicit ``overrides`` win over environment variables.
        """
        env = os.environ
        kwargs: dict[str, Any] = {}

        def take(name: str, key: str, cast: Callable[[str], Any]) -> None:
            raw = env.get(ENV_PREFIX + name)
            if raw is not None:
                kwargs[key] = cast(raw)

        take("PROJECT_ROOT", "project_root", str)
        take("PREFIX", "prefix", str)
        take("INCLUDE_SOURCE", "include_source", _parse_source)
        take("INCLUDE_COMMENTS", "include_comments", parse_bool)
        take("PROFILE", "profile", str)
        take("MAX_TOKENS", "max_tokens", float)
        take("RANK_METHOD", "rank_method", str)
        take("EXCLUDE", "exclude", _parse_csv)
        take("INCLUDE_ROUTES", "include_routes", parse_bool)
        take("ENABLE_ENV", "enable_env", str)
        take("REQUIRE_ENABLE_ENV", "require_enable_env", parse_bool)
        take("DEBUG", "debug", parse_bool)
        take("AUTH_TOKEN", "auth_token", str)
        take("ALLOW_CLIENTS", "allow_clients", _parse_csv)
        take("MAX_CONTEXT_TOKENS", "max_context_tokens", float)
        take("MODE", "mode", str)
        take("LLMS_AT_ROOT", "llms_at_root", parse_bool)

        if overrides:
            kwargs.update(overrides)
        return cls(**kwargs)


def resolve_config(
    config: MarkdownizerConfig | None,
    overrides: Mapping[str, Any] | None = None,
) -> MarkdownizerConfig:
    """Resolve an explicit config plus keyword overrides into a config.

    When ``config`` is ``None`` the environment provides the defaults; when a
    config is given, only the overrides replace fields.
    """
    if config is None:
        return MarkdownizerConfig.from_env(overrides)
    if overrides:
        return replace(config, **overrides)
    return config
