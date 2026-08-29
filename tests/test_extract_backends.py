"""The model backends in bench/extract.py.

Until now the Anthropic path had no test of any kind. That is the path most likely to
be exercised first by someone evaluating this project, and the one whose failure mode
is least obvious: an identity-linked key returns a 400 whose message does not say what
to do about it, and the wrapper's whole job is to turn that into an instruction.

Almost all of this is testable without a credential — backend selection, the workspace
header, the error translation — so it runs in ordinary CI and costs nothing. The single
test that needs a real key is marked `live` and skipped unless PYLIGENT_LIVE_MODEL=1,
so a developer who happens to have a key exported does not silently pay for a test run.
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

import extract  # noqa: E402

# --------------------------------------------------------------------------- offline


def test_backend_selection_by_model_prefix():
    assert extract.backend_for("claude-opus-5") is extract.call_anthropic
    assert extract.backend_for("gpt-4o") is extract.call_openai
    assert extract.backend_for("gemini-3.6-flash") is extract.call_gemini


def test_unknown_model_names_the_accepted_prefixes():
    """A typo should not look like an outage."""
    with pytest.raises(SystemExit) as exc:
        extract.backend_for("llama-3")
    message = str(exc.value)
    assert "llama-3" in message
    for prefix in ("claude-", "gpt-", "gemini-"):
        assert prefix in message


class _FakeBadRequest(Exception):
    pass


def _fake_anthropic(monkeypatch, *, blocks=None, raises=None, capture=None):
    """Install a stand-in `anthropic` module and record how it was constructed."""

    module = types.ModuleType("anthropic")
    module.BadRequestError = _FakeBadRequest

    class _Messages:
        def create(self, **kwargs):
            if capture is not None:
                capture["create"] = kwargs
            if raises is not None:
                raise raises
            return types.SimpleNamespace(content=blocks or [])

    class _Client:
        def __init__(self, default_headers=None):
            if capture is not None:
                capture["headers"] = default_headers
            self.messages = _Messages()

    module.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return module


def test_workspace_header_sent_only_when_configured(monkeypatch):
    """An empty header is worse than no header, so it must be omitted entirely."""
    capture: dict = {}
    _fake_anthropic(monkeypatch, blocks=[], capture=capture)

    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    extract.call_anthropic("claude-opus-5", "hi")
    assert capture["headers"] is None

    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "  wrkspc_abc  ")
    extract.call_anthropic("claude-opus-5", "hi")
    assert capture["headers"] == {"anthropic-workspace-id": "wrkspc_abc"}


def test_identity_linked_key_error_becomes_an_instruction(monkeypatch):
    """The upstream 400 explains nothing. The wrapper must say what to do."""
    _fake_anthropic(
        monkeypatch,
        raises=_FakeBadRequest("400 workspace-id is required for identity-linked keys"),
    )
    with pytest.raises(SystemExit) as exc:
        extract.call_anthropic("claude-opus-5", "hi")
    message = str(exc.value)
    assert "ANTHROPIC_WORKSPACE_ID" in message
    assert "wrkspc_" in message


def test_unrelated_bad_request_is_not_swallowed(monkeypatch):
    """Only the workspace case is translated; everything else propagates."""
    _fake_anthropic(monkeypatch, raises=_FakeBadRequest("400 max_tokens too large"))
    with pytest.raises(_FakeBadRequest):
        extract.call_anthropic("claude-opus-5", "hi")


def test_only_text_blocks_are_joined(monkeypatch):
    """A response may carry non-text blocks; concatenating them would corrupt the JSON."""
    blocks = [
        types.SimpleNamespace(type="text", text='{"a":'),
        types.SimpleNamespace(type="thinking", text="ignored"),
        types.SimpleNamespace(type="text", text=" 1}"),
    ]
    _fake_anthropic(monkeypatch, blocks=blocks)
    assert extract.call_anthropic("claude-opus-5", "hi") == '{"a": 1}'


# ------------------------------------------------------------------------------ live

live = pytest.mark.skipif(
    os.getenv("PYLIGENT_LIVE_MODEL") != "1",
    reason="live model test; set PYLIGENT_LIVE_MODEL=1 and provide ANTHROPIC_API_KEY",
)


@live
@pytest.mark.live
def test_anthropic_returns_parseable_extraction_for_a_real_document():
    """One real call, on the smallest input that still exercises the whole path.

    This asserts the contract the benchmark depends on — a reply that survives
    parse_json and carries the requested fields — not the model's accuracy. Accuracy is
    what bench/run.py measures, over a corpus, with evidence.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
    from _env import load_env

    load_env()
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.fail("PYLIGENT_LIVE_MODEL=1 but ANTHROPIC_API_KEY is absent")

    document = (
        "CREDIT SUPPORT ANNEX to the Schedule to the ISDA Master Agreement dated as of "
        "1 March 2021 between ALPHA BANK PLC (\"Party A\") and BETA FUND LIMITED "
        "(\"Party B\").\n\n"
        "Paragraph 13. Elections and Variables.\n"
        "(b)(iv)(A) Threshold. Party A: GBP 10,000,000. Party B: GBP 10,000,000.\n"
        "(b)(iv)(B) Minimum Transfer Amount. Party A: GBP 500,000. Party B: GBP 500,000.\n"
        "(b)(ii) Eligible Credit Support. Cash in the Base Currency: Valuation "
        "Percentage 100%.\n"
    )
    prompt = extract.PROMPT.format(
        fields=", ".join(extract.DEFAULT_FIELDS), document=document
    )

    raw = extract.call_anthropic("claude-sonnet-5", prompt)
    payload = extract.parse_json(raw)
    assert isinstance(payload, dict) and payload, "model returned no usable object"

    # The contract is not "a JSON object" but "an object this project can score".
    # normalise_extraction is what the benchmark and the CLI both run, so asserting
    # against it tests the boundary that actually matters.
    from evidencecheck.cli import normalise_extraction

    fields = normalise_extraction(payload)
    assert any(name in fields for name in extract.DEFAULT_FIELDS), (
        f"reply carried none of the requested fields; got {sorted(fields)[:10]}"
    )

    # And the claim this whole project exists to check: a quote must be *in* the
    # document. A live test that accepted an invented quote would be worse than none.
    from evidencecheck.sources import find_flexible

    # normalise_extraction emits "quote"; reading "evidence_quote" here would make
    # this loop a no-op that passes for the wrong reason.
    checked = 0
    for name, entry in fields.items():
        quote = (entry.get("quote") or "").strip()
        if not quote:
            continue
        checked += 1
        assert find_flexible(document, quote) >= 0, (
            f"field {name!r} cited a quote absent from the document: {quote!r}"
        )

    assert checked, "no field carried a quote, so nothing was actually verified"

    print("\nlive Anthropic reply (normalised):\n" + json.dumps(fields, indent=2)[:900])
