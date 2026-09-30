"""Unit tests for the security helpers."""

from __future__ import annotations

import pytest

from markdownizer_fastapi import MarkdownizerConfig
from markdownizer_fastapi.security import (
    client_allowed,
    extract_token,
    is_enabled,
    token_valid,
)


def test_is_enabled_debug_bypasses_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKDOWNIZER_FASTAPI_ENABLED", raising=False)
    assert is_enabled(MarkdownizerConfig(debug=True)) is True


def test_is_enabled_require_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKDOWNIZER_FASTAPI_ENABLED", raising=False)
    assert is_enabled(MarkdownizerConfig()) is False
    monkeypatch.setenv("MARKDOWNIZER_FASTAPI_ENABLED", "yes")
    assert is_enabled(MarkdownizerConfig()) is True


def test_is_enabled_opt_out() -> None:
    assert is_enabled(MarkdownizerConfig(require_enable_env=False)) is True


def test_is_enabled_custom_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_GATE", "1")
    config = MarkdownizerConfig(enable_env="MY_GATE")
    assert is_enabled(config) is True


def test_client_allowed_without_allowlist() -> None:
    assert client_allowed(None, ()) is True
    assert client_allowed("203.0.113.4", ()) is True


def test_client_allowed_matching_network() -> None:
    assert client_allowed("10.1.2.3", ("10.0.0.0/8",)) is True
    assert client_allowed("192.0.2.1", ("10.0.0.0/8",)) is False
    assert client_allowed(None, ("10.0.0.0/8",)) is False
    assert client_allowed("not-an-ip", ("10.0.0.0/8",)) is False


def test_extract_token() -> None:
    assert extract_token({"authorization": "Bearer abc"}) == "abc"
    assert extract_token({"x-markdownizer-token": " abc "}) == "abc"
    assert extract_token({"authorization": "Basic abc"}) is None
    assert extract_token({}) is None


def test_token_valid() -> None:
    assert token_valid(None, None) is True
    assert token_valid("abc", "abc") is True
    assert token_valid("abc", "def") is False
    assert token_valid(None, "def") is False
    assert token_valid("", "def") is False
