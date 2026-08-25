"""Level 1 — classify an inbound support ticket. One call. No loop.

**This is the correct final architecture for this task, and you should resist
making it anything else.** The work is bounded to a single turn and needs
nothing the model does not already have in front of it. A tool loop here would
buy latency, cost and failure surface for exactly nothing.

There is no `Agent` in this file. A loop with `max_turns=1` is a loop pretending
not to be one. The *contract* is still written down — it is what records that
nobody verifies this, and why that is acceptable.

    python examples/run.py triage
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pyligent_agents import Phase
from pyligent_agents.harness import Harness
from pyligent_agents.loop import AgentContract, Budget, ModelSaysDone, no_verification

from shopdesk.data import Ticket

CATEGORIES = ("late_delivery", "return_request", "missing_order",
              "acknowledgement", "other")
URGENCIES = ("low", "normal", "high", "critical")

SYSTEM = """\
You triage inbound tickets for an online retailer's support desk.

Classify the message into exactly one category and one urgency.

Categories:
- late_delivery    the order arrived after the promised date
- return_request   the customer wants to send something back
- missing_order    the order has not arrived and its whereabouts are unclear
- acknowledgement  the customer is confirming something; no action needed
- other            none of the above

Urgency:
- critical  a deadline is breached, or the customer threatens escalation
            (chargeback, ombudsman, legal, social media)
- high      action needed today to prevent that
- normal    routine, within the standard cycle
- low       informational

Respond with JSON only:
{"category": <category>, "urgency": <urgency>, "reason": <one short sentence>}

Do not quote or compute any monetary amount — you are triaging, not deciding."""

CONTRACT = AgentContract(
    goal="Classify one support ticket into a closed vocabulary.",
    stop=ModelSaysDone(),
    verifier=no_verification(
        "The output vocabulary is closed and validated deterministically after "
        "the model answers; anything outside it routes to manual triage."
    ),
    budget=Budget(max_turns=1, max_usd=0.02, max_seconds=20),
)


@dataclass(frozen=True)
class Triage:
    ticket_id: str
    order_id: str
    category: str
    urgency: str
    reason: str
    model: str
    fallback_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id, "order_id": self.order_id,
            "category": self.category, "urgency": self.urgency,
            "reason": self.reason, "model": self.model,
            "fallback_used": self.fallback_used,
        }


class TriageService:
    """Stateless. Construct once, call many times, share freely."""

    def __init__(self, harness: Harness, *, model: str | None = None):
        self.h = harness
        # The cheapest model that does the job. At 400 tickets a day this is the
        # largest line on the monthly bill despite being cheapest per call —
        # reaching for the biggest model here multiplies your biggest line by
        # five, to classify emails.
        self.model = model or harness.settings.cheap_model

    def classify(self, ticket: Ticket) -> Triage:
        ctx = self.h.new_context(model=self.model, system=SYSTEM)
        ctx.append_user(
            f"Order: {ticket.order_id}\nReceived: {ticket.received_at}\n"
            f"Subject: {ticket.subject}\n\n{ticket.body}"
        )
        r = self.h.call_model(phase=Phase.ACT, model=self.model, context=ctx, max_tokens=250)
        parsed, fell_back = self._parse(r.text)
        return Triage(ticket.ticket_id, ticket.order_id, parsed["category"],
                      parsed["urgency"], parsed["reason"], r.model or self.model, fell_back)

    @staticmethod
    def _parse(text: str) -> tuple[dict[str, str], bool]:
        """Two independent failures; both route to a human.

        Not JSON, or JSON naming a category with no queue behind it. A label
        nothing consumes is worse than no label, because it looks like it worked.
        A classifier that *raises* on bad output takes the whole inbox down —
        far worse than one ticket triaged by hand.
        """
        fallback = {"category": "other", "urgency": "normal",
                    "reason": "Output could not be validated; routed for manual triage."}
        body = (text or "").strip()
        if body.startswith("```"):
            body = body.strip("`")
            body = body.split("\n", 1)[-1] if "\n" in body else body
            body = body.rsplit("```", 1)[0]
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return fallback, True
        if not isinstance(parsed, dict):
            return fallback, True
        cat, urg = str(parsed.get("category", "")).strip(), str(parsed.get("urgency", "")).strip()
        if cat not in CATEGORIES or urg not in URGENCIES:
            return fallback, True
        return {"category": cat, "urgency": urg,
                "reason": str(parsed.get("reason", "")).strip()[:240]}, False
