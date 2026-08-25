"""Refund and invoice arithmetic.

**No model output is ever a monetary figure.** Every amount an agent quotes to a
customer comes from a function in this file, and every function here is covered
by a test. The model decides *which* calculation to run and *how to explain it*.

This is the first thing to build and the last thing to compromise on. Get it
right and the rest of the system has something true to stand on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .errors import RefundNotPermitted

RETURN_WINDOW_DAYS = 30
RESTOCKING_FEE_PCT = 15.0   # opened items, customer-fault returns only


@dataclass(frozen=True)
class RefundQuote:
    """A fully itemised refund. Every intermediate is retained on purpose.

    When a customer disputes the amount, "the agent said £42" is not an answer.
    This breakdown is.
    """

    order_id: str
    goods_value: float
    shipping_paid: float
    already_refunded: float
    days_since_delivery: int
    restocking_fee: float
    shipping_refundable: bool
    refundable: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "goods_value": self.goods_value,
            "shipping_paid": self.shipping_paid,
            "already_refunded": self.already_refunded,
            "days_since_delivery": self.days_since_delivery,
            "restocking_fee": self.restocking_fee,
            "shipping_refundable": self.shipping_refundable,
            "refundable": self.refundable,
            "reason": self.reason,
        }


def quote_refund(order: Any, *, fault: str, today: date) -> RefundQuote:
    """Compute what we may refund, and why.

    Rules, in the order a policy document states them:

      1. outside the return window -> nothing is refundable
      2. shipping is refunded only when the fault is ours
      3. a restocking fee applies to opened items returned at customer fault
      4. anything already refunded is deducted — you cannot refund twice

    Raises `RefundNotPermitted` rather than returning zero, so the agent gets a
    reason it can give the customer instead of a bare number.
    """
    if order.delivered_on is None:
        raise RefundNotPermitted(order.order_id, "the order has not been delivered yet")

    days = (today - order.delivered_on).days
    if days > RETURN_WINDOW_DAYS:
        raise RefundNotPermitted(
            order.order_id,
            f"delivered {days} days ago, outside the {RETURN_WINDOW_DAYS}-day return window",
        )

    shipping_refundable = fault == "seller"
    fee = 0.0
    if fault == "customer" and order.opened:
        fee = round(order.goods_value * RESTOCKING_FEE_PCT / 100.0, 2)

    gross = order.goods_value - fee + (order.shipping_paid if shipping_refundable else 0.0)
    refundable = round(max(0.0, gross - order.already_refunded), 2)

    if refundable <= 0:
        raise RefundNotPermitted(
            order.order_id,
            f"£{order.already_refunded:,.2f} has already been refunded, which covers "
            f"the eligible amount of £{round(gross, 2):,.2f}",
        )

    return RefundQuote(
        order_id=order.order_id,
        goods_value=order.goods_value,
        shipping_paid=order.shipping_paid,
        already_refunded=order.already_refunded,
        days_since_delivery=days,
        restocking_fee=fee,
        shipping_refundable=shipping_refundable,
        refundable=refundable,
        reason=(
            f"£{order.goods_value:,.2f} goods"
            + (f" less £{fee:,.2f} restocking fee" if fee else "")
            + (f" plus £{order.shipping_paid:,.2f} shipping" if shipping_refundable else "")
            + (f" less £{order.already_refunded:,.2f} already refunded"
               if order.already_refunded else "")
            + f" = £{refundable:,.2f}, within the return window ({days} days)."
        ),
    )


def invoice_total(lines: list[dict[str, Any]], *, tax_rate_pct: float) -> dict[str, float]:
    """Recompute an invoice from its lines. Used to check what was extracted.

    The gate built on this — *do the line items actually sum to the stated
    total?* — is the one no JSON schema can express, and the one that catches a
    plausible, well-formed, wrong extraction.
    """
    net = round(sum(float(line["quantity"]) * float(line["unit_price"]) for line in lines), 2)
    tax = round(net * tax_rate_pct / 100.0, 2)
    return {"net": net, "tax": tax, "gross": round(net + tax, 2)}
