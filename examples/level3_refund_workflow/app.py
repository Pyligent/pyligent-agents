"""Level 3 — a refund, as a graph.

A refund is not one request. A ticket arrives, someone works out what is owed,
a reply gets drafted, a supervisor approves, money moves, the customer is told.
In between: a restart, a deploy, a shift change.

Read the node list and you know what the workflow does, what it touches, where a
human intervenes, and which step could fire twice if you got it wrong. That
readability *is* the argument for graph engineering.

    python examples/run.py refund
    python examples/run.py resume <run_id> --approve
"""

from __future__ import annotations

from typing import Any

from pyligent_agents import idempotency_key
from pyligent_agents.graph import AgentNode, Graph, HumanGate, RetryPolicy, Step
from pyligent_agents.graph.state import GraphState
from pyligent_agents.harness import Harness
from pyligent_agents.loop import (
    Agent,
    AgentContract,
    Budget,
    LoopState,
    ModelSaysDone,
    Predicate,
    no_verification,
)

from shopdesk import data, money

DRAFT_SYSTEM = """\
You draft replies from an online retailer's support desk to a customer who has
asked about a refund.

You are given their ticket and our calculated figures. Write a short, warm,
professional reply that states the exact amount we will refund, why it is that
amount (referring to the policy reasons given), and when they will see it.

Never introduce a number that is not in the figures you were given. Do not
apologise more than once. Five sentences maximum."""


# --- node bodies ----------------------------------------------------------


def _read_ticket(state: GraphState) -> dict[str, Any]:
    ticket = data.get_ticket(state.require("ticket_id"))
    return {"ticket": ticket.to_dict(), "order_id": ticket.order_id}


def _load_order(state: GraphState) -> dict[str, Any]:
    return {"order": data.get_order(state.require("order_id")).to_dict()}


def _quote(state: GraphState) -> dict[str, Any]:
    """Deterministic. The figure the customer will be told comes from here."""
    order = data.get_order(state.require("order_id"))
    fault = "seller" if order.late_by_days > 0 else "customer"
    quote = money.quote_refund(order, fault=fault, today=data.TODAY)
    return {"quote": {**quote.to_dict(), "fault": fault}}


def _refund_due(state: GraphState) -> bool:
    return float((state.get("quote") or {}).get("refundable", 0)) > 0


def _issue(state: GraphState) -> dict[str, Any]:
    quote = state.require("quote")
    return {"refund": {
        "status": "issued",
        "order_id": state.require("order_id"),
        "amount": quote["refundable"],
        "reason": quote["reason"],
    }}


def _refund_key(state: GraphState) -> str:
    """Derived from the FACTS of the refund. Never a clock, never a uuid.

    Same order, same amount, same reason -> same key. A key containing a
    timestamp changes on every attempt, which guarantees the double refund this
    ledger exists to prevent.
    """
    quote = state.require("quote")
    return idempotency_key(
        "refund",
        order=state.require("order_id"),
        amount=quote["refundable"],
        fault=quote["fault"],
    )


def _send_reply(state: GraphState) -> dict[str, Any]:
    return {"sent": {
        "to": data.get_order(state.require("order_id")).email,
        "subject": f"Your refund for order {state.require('order_id')}",
        "chars": len(state.require("draft")),
    }}


def _reply_key(state: GraphState) -> str:
    return idempotency_key("reply", order=state.require("order_id"),
                           amount=state.require("quote")["refundable"])


def _build_drafter(harness: Harness, state: GraphState) -> Agent:
    amount = f"{state.require('quote')['refundable']:,.2f}"

    def quotes_only_our_figure(s: LoopState) -> bool:
        """The draft may contain our figure and no other.

        A model that rounds "helpfully" in a customer email creates a second
        dispute, and the customer will hold us to whichever number is larger.
        """
        return not s.answer or amount in s.answer

    return Agent(
        harness,
        AgentContract(
            goal="Draft a refund reply using only the calculated figures.",
            stop=ModelSaysDone() & Predicate(quotes_only_our_figure,
                                             "quotes only the calculated amount"),
            verifier=no_verification(
                "The only figure permitted is the deterministically calculated "
                "one, and the stop condition enforces that."
            ),
            budget=Budget(max_turns=3, max_usd=0.15, max_seconds=45),
        ),
        model=harness.settings.worker_model,
        system=DRAFT_SYSTEM,
        # No tools. Drafting needs none, and giving it any would be a liability.
        tools=[],
        extractor=lambda s: {"draft": s.answer},
        name="drafter",
    )


# --- the graph ------------------------------------------------------------


def build_graph() -> Graph:
    return Graph(name="refund_workflow", seeds=("ticket_id",)).extend(
        Step(id="read_ticket", fn=_read_ticket,
             requires=("ticket_id",), provides=("ticket", "order_id"),
             description="Pull the inbound ticket."),

        Step(id="load_order", fn=_load_order,
             depends_on=("read_ticket",), requires=("order_id",), provides=("order",),
             retry=RetryPolicy(max_attempts=2),
             description="Read the order. Retryable — no side effect."),

        Step(id="quote", fn=_quote,
             depends_on=("load_order",), requires=("order_id",), provides=("quote",),
             description="Deterministic refund maths. No model involved."),

        AgentNode(id="draft_reply", build=_build_drafter,
                  task=lambda s: (f"Ticket: {s.require('ticket')['body']}\n\n"
                                  f"Our figures: {s.require('quote')}"),
                  depends_on=("quote",), requires=("quote", "ticket"),
                  provides=("draft",), tools=(),
                  description="The only model-dependent step in the workflow."),

        HumanGate(id="approve_refund",
                  prompt=lambda s: (
                      f"Approve a refund of £{s.require('quote')['refundable']:,.2f} "
                      f"on order {s.require('order_id')} "
                      f"({s.require('quote')['fault']} fault)?"),
                  payload=lambda s: {"quote": s.require("quote"),
                                     "draft": s.get("draft", "")},
                  depends_on=("quote", "draft_reply"), requires=("quote",),
                  when=_refund_due,
                  description="Pauses the run. Paused is not failed."),

        Step(id="issue_refund", fn=_issue,
             depends_on=("approve_refund",), requires=("quote", "order_id"),
             provides=("refund",), when=_refund_due,
             idempotency=_refund_key,
             description="MONEY MOVES HERE. Fires once across any number of resumes."),

        Step(id="send_reply", fn=_send_reply,
             depends_on=("issue_refund",), requires=("draft", "order_id"),
             provides=("sent",), when=_refund_due,
             idempotency=_reply_key,
             description="Also irreversible: an email cannot be unsent."),
    )
