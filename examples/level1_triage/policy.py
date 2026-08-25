"""Scripted behaviour for the Level 1 example.

Keyword triage standing in for the model, so the example runs offline and its
tests assert on behaviour rather than on a live model's mood.
"""

from __future__ import annotations

import json

from pyligent_agents.testing import ScriptedCall, ScriptedTurn, turn

RULES = (
    ("missing_order", "critical", ("chargeback", "nobody answers", "where is my order")),
    ("late_delivery", "high", ("late", "promised for", "turned up on")),
    ("return_request", "normal", ("send it back", "return", "don't need")),
    ("acknowledgement", "low", ("confirming", "nothing further", "close this")),
)


def triage_policy(call: ScriptedCall) -> ScriptedTurn:
    text = " ".join(
        m.get("content", "") for m in call.messages if isinstance(m.get("content"), str)
    ).lower()
    for category, urgency, needles in RULES:
        if any(n in text for n in needles):
            return ScriptedTurn(
                text=json.dumps({"category": category, "urgency": urgency,
                                 "reason": f"Language matches the {category} pattern."}),
                input_tokens=480, output_tokens=44)
    return ScriptedTurn(
        text=json.dumps({"category": "other", "urgency": "normal", "reason": "No pattern matched."}),
        input_tokens=480, output_tokens=38)


def malformed_policy(_call: ScriptedCall) -> ScriptedTurn:
    """A model that answers in prose. Exercises the degradation path."""
    return turn("Probably a late delivery, and they sound quite cross about it.")


def off_vocabulary_policy(_call: ScriptedCall) -> ScriptedTurn:
    """Valid JSON, invented label. Exercises the vocabulary check."""
    return turn('{"category": "escalate_to_legal", "urgency": "high", "reason": "x"}')
