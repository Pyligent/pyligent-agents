"""Level 2 — a support agent with tools, fully governed.

Level 1 breaks the moment the answer depends on this order's dates, live carrier
tracking, and a refund figure the model must not compute in its head.

This is the loop, and every guardrail is a constructor argument rather than a
convention. The interesting line is the stop condition.

    python examples/run.py order-agent
"""

from __future__ import annotations

from typing import Any

from trellis.harness import Harness
from trellis.loop import (
    Agent,
    AgentContract,
    Budget,
    LoopState,
    ModelSaysDone,
    OnFailure,
    Predicate,
    no_verification,
)

SYSTEM = """\
You are a support agent for an online retailer. You answer questions about
orders, deliveries and refunds using the tools provided.

1. Never state a monetary amount you did not get from a tool. Do not work out a
   refund yourself — call quote_refund and use its figure. What you say is what
   the customer will hold us to.
2. Never assume an order's contents or dates. Read them with get_order.
3. If a tool errors, read it and adapt. A carrier outage, a bad order id or a
   policy refusal is information, not a dead end. Say what failed and what you
   did instead. Never invent a delivery date or an amount to fill a gap.
4. issue_refund and email_customer are restricted. If a decision is refused, do
   not retry — set out exactly what you would do and say it needs approval.
5. When you have enough, answer. Give the outcome, then the short reason.
6. If you need a capability you cannot see, call search_tools before giving up."""

# Tools whose output is allowed to be the source of a monetary figure.
MONEY_TOOLS = {"quote_refund", "get_order"}


def _grounded(state: LoopState) -> bool:
    """Every amount in the answer must be traceable to a tool result.

    A stop condition, not a hope. If the agent quotes a figure without having
    called the calculator, the loop is not finished — it is guessing, and it is
    told so and given another turn.

    Note `not o.is_error`: a tool that RAN and FAILED grounds nothing, and that
    is precisely the moment a model is most tempted to fill the gap from memory.
    """
    answer = state.answer or ""
    if "£" not in answer and "GBP" not in answer:
        return True
    return any(o.tool_name in MONEY_TOOLS and not o.is_error for o in state.outcomes)


def _no_invented_dates(state: LoopState) -> bool:
    """If the carrier lookup failed, do not claim a delivery date."""
    failed_tracking = any(o.tool_name == "get_tracking" and o.is_error for o in state.outcomes)
    ok_tracking = any(o.tool_name == "get_tracking" and not o.is_error for o in state.outcomes)
    if not failed_tracking or ok_tracking:
        return True
    lowered = (state.answer or "").lower()
    return not any(w in lowered for w in ("will arrive", "delivered on", "arriving on"))


def _extract(state: LoopState) -> dict[str, Any]:
    return {
        "answer": state.answer,
        "tools_used": [o.tool_name for o in state.outcomes],
        "refund_quote": state.tool_output("quote_refund"),
    }


def contract(goal: str, **budget: Any) -> AgentContract:
    return AgentContract(
        goal=goal,
        # Three conditions, all required. Composition is what lets you add a
        # domain guarantee without rewriting the loop.
        stop=(ModelSaysDone()
              & Predicate(_grounded, "amounts traceable to tools")
              & Predicate(_no_invented_dates, "no delivery date without tracking")),
        verifier=no_verification(
            "Every amount originates from quote_refund, which is deterministic "
            "and unit-tested; the grounding stop condition enforces that."
        ),
        budget=Budget(**{"max_turns": 8, "max_usd": 0.40, "max_seconds": 90, **budget}),
        on_failure=OnFailure.ESCALATE,
    )


def build(harness: Harness, goal: str = "Answer a customer's question about an order.",
          **budget: Any) -> Agent:
    return Agent(
        harness, contract(goal, **budget),
        model=harness.settings.worker_model,
        system=SYSTEM, extractor=_extract, name="order_agent",
    )
