"""Credential detection, and the guidance that replaces an unhelpful SDK error.

The rule this module is built around: it reports whether a variable is *set* and
what it is called. It never reads, prints, stores or transmits the value. These
tests assert that boundary as much as the behaviour, because a setup helper that
starts handling secrets is how secrets reach repositories.
"""

from __future__ import annotations

import pytest

from pyligent_agents.credentials import (
    ANTHROPIC_VARS,
    detect,
    gitignore_protects,
    guidance,
    missing_credential_error,
)


def _clear(monkeypatch):
    for name in (*ANTHROPIC_VARS, "ANTHROPIC_WORKSPACE_ID"):
        monkeypatch.delenv(name, raising=False)


def test_detects_nothing_when_nothing_is_set(monkeypatch):
    _clear(monkeypatch)
    c = detect()
    assert not c.present and c.variable is None
    assert c.summary == "no credential found"


@pytest.mark.parametrize("var", ANTHROPIC_VARS)
def test_detects_each_variable_the_sdk_accepts(monkeypatch, var):
    _clear(monkeypatch)
    monkeypatch.setenv(var, "placeholder-value")
    c = detect()
    assert c.present and c.variable == var


def test_resolution_order_matches_the_sdk(monkeypatch):
    """API key wins over auth token, as the SDK resolves them."""
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "placeholder")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder")
    assert detect().variable == "ANTHROPIC_API_KEY"


def test_whitespace_only_is_not_a_credential(monkeypatch):
    """An exported-but-empty variable is the confusing case worth getting right."""
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert not detect().present


def test_the_value_never_appears_anywhere(monkeypatch):
    """The whole boundary, asserted directly."""
    secret = "sk-ant-thisexactstringmustnotescape"
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    c = detect()
    assert secret not in c.summary
    assert secret not in repr(c)
    assert secret not in guidance()
    assert secret not in str(missing_credential_error())


def test_guidance_names_the_variable_and_the_free_path():
    text = guidance()
    assert "ANTHROPIC_API_KEY" in text
    assert "console.anthropic.com" in text
    # The no-spend route must be offered, not buried: it is how someone evaluates
    # this project without a credential at all.
    assert "PYLIGENT_AGENTS_BACKEND=scripted" in text


def test_guidance_says_dotenv_is_not_read():
    """A key in an unread .env looks exactly like no key, and costs an hour."""
    assert ".env" in guidance()


def test_missing_credential_error_is_actionable():
    err = missing_credential_error()
    assert isinstance(err, RuntimeError)
    body = str(err)
    assert "ANTHROPIC_API_KEY" in body and "export" in body.lower()


def test_gitignore_detection(tmp_path):
    assert gitignore_protects(root=tmp_path) is None          # not a repo
    (tmp_path / ".git").mkdir()
    assert gitignore_protects(root=tmp_path) is False         # repo, no .gitignore
    (tmp_path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    assert gitignore_protects(root=tmp_path) is False         # present, not covered
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    assert gitignore_protects(root=tmp_path) is True
