"""Configuration validation and environment resolution tests."""

from __future__ import annotations

import pytest

from markdownizer_fastapi import MarkdownizerConfig
from markdownizer_fastapi.config import resolve_config


def test_defaults() -> None:
    config = MarkdownizerConfig()
    assert config.prefix == "/_markdownizer"
    assert config.include_source is False
    assert config.profile == "api"
    assert config.mode == "lazy"
    assert config.require_enable_env is True
    assert config.auth_token is None


def test_prefix_is_normalized() -> None:
    assert MarkdownizerConfig(prefix="context/").prefix == "/context"
    assert MarkdownizerConfig(prefix="/context/").prefix == "/context"


@pytest.mark.parametrize("prefix", ["/", "/a?b", "/a#b", "/a b"])
def test_invalid_prefix_rejected(prefix: str) -> None:
    with pytest.raises(ValueError):
        MarkdownizerConfig(prefix=prefix)


def test_unknown_profile_rejected() -> None:
    with pytest.raises(ValueError, match="unknown profile"):
        MarkdownizerConfig(profile="nope")


def test_invalid_include_source_rejected() -> None:
    with pytest.raises(ValueError, match="include_source"):
        MarkdownizerConfig(include_source="full")  # type: ignore[arg-type]


def test_invalid_mode_rejected() -> None:
    with pytest.raises(ValueError, match="mode"):
        MarkdownizerConfig(mode="watch")


def test_invalid_rank_method_rejected() -> None:
    with pytest.raises(ValueError, match="rank method"):
        MarkdownizerConfig(rank_method="weighted")


@pytest.mark.parametrize("value", [0, -1.5])
def test_non_positive_tokens_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        MarkdownizerConfig(max_tokens=value)


def test_max_context_tokens_must_cover_max_tokens() -> None:
    with pytest.raises(ValueError, match="max_context_tokens"):
        MarkdownizerConfig(max_tokens=100, max_context_tokens=10)


def test_allow_clients_normalized() -> None:
    config = MarkdownizerConfig(allow_clients=("127.0.0.1", "10.0.0.0/8"))
    assert config.allow_clients == ("127.0.0.1/32", "10.0.0.0/8")


def test_invalid_allow_clients_rejected() -> None:
    with pytest.raises(ValueError, match="allow_clients"):
        MarkdownizerConfig(allow_clients=("not-an-ip",))


def test_empty_auth_token_rejected() -> None:
    with pytest.raises(ValueError, match="auth_token"):
        MarkdownizerConfig(auth_token="   ")


def test_empty_enable_env_rejected() -> None:
    with pytest.raises(ValueError, match="enable_env"):
        MarkdownizerConfig(enable_env="")


def test_from_env_reads_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKDOWNIZER_FASTAPI_PREFIX", "/env")
    monkeypatch.setenv("MARKDOWNIZER_FASTAPI_MAX_TOKENS", "1234")
    monkeypatch.setenv("MARKDOWNIZER_FASTAPI_INCLUDE_SOURCE", "signature")
    monkeypatch.setenv("MARKDOWNIZER_FASTAPI_ALLOW_CLIENTS", "10.0.0.0/8")
    config = MarkdownizerConfig.from_env()
    assert config.prefix == "/env"
    assert config.max_tokens == 1234
    assert config.include_source == "signature"
    assert config.allow_clients == ("10.0.0.0/8",)


def test_from_env_overrides_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKDOWNIZER_FASTAPI_PREFIX", "/env")
    config = MarkdownizerConfig.from_env({"prefix": "/explicit"})
    assert config.prefix == "/explicit"


def test_from_env_include_source_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKDOWNIZER_FASTAPI_INCLUDE_SOURCE", "false")
    assert MarkdownizerConfig.from_env().include_source is False
    monkeypatch.setenv("MARKDOWNIZER_FASTAPI_INCLUDE_SOURCE", "true")
    assert MarkdownizerConfig.from_env().include_source is True
    monkeypatch.setenv("MARKDOWNIZER_FASTAPI_INCLUDE_SOURCE", "full")
    with pytest.raises(ValueError, match="include_source"):
        MarkdownizerConfig.from_env()


def test_resolve_config_keeps_explicit_config() -> None:
    config = MarkdownizerConfig(prefix="/explicit")
    assert resolve_config(config, None) is config
    assert resolve_config(config, {"prefix": "/other"}).prefix == "/other"
