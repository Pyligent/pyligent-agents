from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT / "examples"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """No network, no spend, isolated state per test."""
    monkeypatch.setenv("TRELLIS_BACKEND", "scripted")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("TRELLIS_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture
def registry():
    from shopdesk.tools import build_registry

    return build_registry()
