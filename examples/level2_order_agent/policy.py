"""Scripted behaviour for the Level 2 example.

Conditional, not canned: the policy reads the tool results already in the
conversation and changes course when one errors. That is what makes the
error-recovery demo a demonstration rather than a performance.
"""

from __future__ import annotations

import json
import re
from typing import Any

from trellis.testing import ScriptedCall, ScriptedTurn, calls, parallel, turn

ORDER_RE = re.compile(r"\bA-\d{4}\b")


def _order(call: ScriptedCall, default: str = "A-1207") -> str:
    for m in call.messages:
        c = m.get("content")
        if isinstance(c, str):
            hit = ORDER_RE.search(c)
            if hit:
                return hit.group(0)
    return default


def _field(call: ScriptedCall, key: str) -> Any:
    """Most recent value of `key` across ALL tool results in the conversation.

    Scanning only the last message is a common and subtle bug: by the final turn
    the newest result is whatever tool ran last, and the field you need was
    fetched two turns ago.
    """
    for message in reversed(call.messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_result" or b.get("is_error"):
                continue
            try:
                payload = json.loads(b.get("content", ""))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict) and key in payload:
                return payload[key]
    return None


def _error(call: ScriptedCall) -> str:
    for b in call.last_tool_results():
        if b.get("is_error"):
            return str(b.get("content", ""))
    return ""


def order_agent_policy(call: ScriptedCall) -> ScriptedTurn:
    order = _order(call)
    err = _error(call)

    # Carrier outage: say so, offer what we still can, invent nothing.
    if "carrier_unavailable" in err:
        return turn(
            f"I cannot confirm where {order} is right now — the carrier's API has "
            f"been returning errors for the last three polls, so any date I gave "
            f"you would be a guess. I have flagged it for manual tracing and we "
            f"will come back within the hour. If it has not moved by tomorrow we "
            f"will treat it as lost and replace it at our cost.",
            output_tokens=130)

    # Policy refusal: explain it in the customer's terms.
    if "refund_not_permitted" in err:
        reason = err.split(": ", 2)[-1]
        return turn(
            f"We cannot refund {order}: {reason} I have set out the position "
            f"for the customer and offered a goodwill credit instead, which "
            f"needs a supervisor to approve.",
            output_tokens=110)

    if "order_not_found" in err and not call.called("get_order"):
        return calls("get_order", _text="Wrong reference. Retrying.", order_id="A-1207")

    # Opening move: two independent reads, requested in ONE turn.
    if not call.called("get_order"):
        return parallel(("get_order", {"order_id": order}),
                        ("get_tracking", {"order_id": order}),
                        text=f"Reading the order and its tracking for {order}.")

    late = _field(call, "late_by_days")
    if not call.called("quote_refund"):
        fault = "seller" if (late or 0) > 0 else "customer"
        return calls("quote_refund",
                     _text=f"Delivered {late} days late — treating fault as {fault}.",
                     order_id=order, fault=fault)

    refundable = _field(call, "refundable")
    if refundable is not None:
        return turn(
            f"Order {order} arrived {late} days after the promised date, so the "
            f"fault is ours and shipping is refundable. We can refund "
            f"£{float(refundable):,.2f} to the original payment method. Issuing "
            f"it needs supervisor approval, so I have queued it rather than "
            f"sending it.",
            output_tokens=140)

    return turn("I have what I need; no further tools required.")


def ungrounded_policy(call: ScriptedCall) -> ScriptedTurn:
    """Quotes a refund it never calculated — then corrects itself when told.

    This is the failure the grounding stop condition exists to catch: fluent,
    confident, and entirely invented. Nothing in the text signals it.
    """
    if not call.called("quote_refund"):
        pushed = any(isinstance(m.get("content"), str) and "NOT DONE YET" in m["content"]
                     for m in call.messages)
        if pushed:
            return calls("quote_refund", _text="Fair. Calculating it properly.",
                         order_id="A-1207", fault="seller")
        return turn("We can refund you £310.00 for order A-1207, including shipping.",
                    output_tokens=40)
    amount = _field(call, "refundable")
    return turn(f"Correction: the refundable amount for A-1207 is "
                f"£{float(amount or 0):,.2f}, from quote_refund.", output_tokens=50)


def restricted_policy(call: ScriptedCall) -> ScriptedTurn:
    """Grounds the figure, tries to refund, accepts the denial gracefully."""
    if not call.called("quote_refund"):
        return calls("quote_refund", _text="Working out what we owe first.",
                     order_id="A-1207", fault="seller")
    if not call.called("issue_refund"):
        amount = _field(call, "refundable") or 257.99
        return calls("issue_refund", _text="Refunding now.",
                     order_id="A-1207", amount=float(amount),
                     reason="delivered 8 days late; shipping refundable")
    return turn(
        "I could not issue the refund: that action is restricted and no approver "
        "is attached to this session. The refund I would issue is £257.99 to the "
        "original payment method for order A-1207, on the grounds that it arrived "
        "eight days late. It needs a supervisor to release it.",
        output_tokens=120)


def tool_search_policy(call: ScriptedCall) -> ScriptedTurn:
    """Needs a capability it cannot see, so it searches for it."""
    if not call.called("search_tools"):
        return calls("search_tools", _text="I need to compare two orders; that is not loaded.",
                     query="compare two orders side by side")
    if not call.called("compare_orders"):
        return calls("compare_orders", _text="Found it.", order_a="A-1207", order_b="A-1588")
    return turn("A-1207 has had nothing refunded; A-1588 has already been refunded in full.",
                output_tokens=60)
