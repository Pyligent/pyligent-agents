"""Domain errors.

These are *expected outcomes*, not crashes. Pyligent Agents turns each into a tool
observation the agent can read and route around — the difference between "the
request 500s because a tracking API blinked" and "the agent notices, says so,
and offers the customer something useful anyway".
"""

from __future__ import annotations

from pyligent_agents import DomainRefusal


class ShopDeskError(DomainRefusal):
    """Subclassing DomainRefusal is what puts these on the DOMAIN branch.

    Without it they would classify FATAL and the loop would escalate — the
    agent would never get the chance to explain the policy to the customer.
    """

    code = "shopdesk_error"


class OrderNotFound(ShopDeskError):
    code = "order_not_found"

    def __init__(self, order_id: str, known: list[str] | None = None):
        hint = f" Known orders: {', '.join(known)}." if known else ""
        super().__init__(f"No order '{order_id}'.{hint}")


class CarrierUnavailable(TimeoutError):
    """A transient upstream failure. Classified TRANSIENT, so it is retried."""

    code = "carrier_unavailable"

    def __init__(self, order_id: str, reason: str):
        super().__init__(f"Carrier API unavailable for '{order_id}': {reason}")


class RefundNotPermitted(ShopDeskError):
    """A policy refusal. DOMAIN — handed back to the agent, never retried."""

    code = "refund_not_permitted"

    def __init__(self, order_id: str, reason: str):
        self.order_id, self.reason = order_id, reason
        super().__init__(f"Refund not permitted for '{order_id}': {reason}")
