"""A seeded book of records: orders, tickets, and a supplier invoice.

Fixed, in-memory, deterministic. It stands in for the four systems a support
desk queries — order management, the carrier API, the refunds ledger and the
mailbox — without needing any of them to exist.

The seed contains the awkward cases **on purpose**:

    A-1207  clean, delivered late, fully refundable
    A-1310  outside the return window — the domain refuses
    A-1422  the carrier API is down — the tool *raises*
    A-1588  partially refunded already — a naive full refund would over-refund

A demo that only contains the happy path teaches nothing about production.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .errors import CarrierUnavailable, OrderNotFound

TODAY = date(2026, 8, 24)


@dataclass(frozen=True)
class Order:
    order_id: str
    customer: str
    email: str
    placed_on: date
    delivered_on: date | None
    promised_on: date
    goods_value: float
    shipping_paid: float
    already_refunded: float
    opened: bool
    items: tuple[str, ...]

    @property
    def late_by_days(self) -> int:
        if self.delivered_on is None:
            return 0
        return max(0, (self.delivered_on - self.promised_on).days)

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id, "customer": self.customer,
            "placed_on": self.placed_on.isoformat(),
            "delivered_on": self.delivered_on.isoformat() if self.delivered_on else None,
            "promised_on": self.promised_on.isoformat(),
            "late_by_days": self.late_by_days,
            "goods_value": self.goods_value, "shipping_paid": self.shipping_paid,
            "already_refunded": self.already_refunded, "opened": self.opened,
            "items": list(self.items),
        }


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    order_id: str
    subject: str
    body: str
    received_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id, "order_id": self.order_id,
            "subject": self.subject, "body": self.body, "received_at": self.received_at,
        }


ORDERS: dict[str, Order] = {
    "A-1207": Order("A-1207", "R. Okonjo", "r.okonjo@example.com",
                    date(2026, 8, 1), date(2026, 8, 14), date(2026, 8, 6),
                    goods_value=248.00, shipping_paid=9.99, already_refunded=0.0,
                    opened=False, items=("Aeropress Go", "Burr grinder")),
    "A-1310": Order("A-1310", "M. Lindqvist", "m.lindqvist@example.com",
                    date(2026, 6, 2), date(2026, 6, 9), date(2026, 6, 8),
                    goods_value=89.50, shipping_paid=4.99, already_refunded=0.0,
                    opened=True, items=("Desk lamp",)),
    "A-1422": Order("A-1422", "S. Whitfield", "s.whitfield@example.com",
                    date(2026, 8, 18), None, date(2026, 8, 22),
                    goods_value=412.00, shipping_paid=0.0, already_refunded=0.0,
                    opened=False, items=("Standing desk converter",)),
    "A-1588": Order("A-1588", "T. Baptiste", "t.baptiste@example.com",
                    date(2026, 8, 3), date(2026, 8, 7), date(2026, 8, 7),
                    goods_value=150.00, shipping_paid=5.99, already_refunded=150.00,
                    opened=True, items=("Noise-cancelling headphones",)),
}

TICKETS: tuple[Ticket, ...] = (
    Ticket("T-9001", "A-1207",
           "Order arrived over a week late",
           "My order was promised for the 6th and turned up on the 14th. I had "
           "already bought a replacement locally. I would like to return it and "
           "get my money back, including what I paid for the shipping.",
           "2026-08-24T08:14:00Z"),
    Ticket("T-9002", "A-1310",
           "Return request",
           "The lamp works fine, I just don't need it any more. Can I send it "
           "back? I opened the box but everything is there.",
           "2026-08-24T09:31:00Z"),
    Ticket("T-9003", "A-1422",
           "URGENT — where is my order?? Nothing since Tuesday",
           "This was supposed to arrive on the 22nd. The tracking page has not "
           "updated in three days and nobody answers the phone. If it is not "
           "here tomorrow I am raising a chargeback with my bank.",
           "2026-08-24T14:47:00Z"),
    Ticket("T-9004", "A-1588",
           "Thanks — refund received",
           "Just confirming the £150 landed this morning. Nothing further "
           "needed, you can close this one.",
           "2026-08-24T10:02:00Z"),
)

# --- Level 4: a supplier invoice to extract -------------------------------
# Short enough to read, structured enough that an extractor has to find things.
# Note the arithmetic: 6×82.50 + 2×145.00 + 1×39.99 = 824.99 net, 20% VAT.

SUPPLIER_INVOICE_TEXT = """\
NORTHWIND SUPPLY CO.
Unit 4, Kestrel Park, Bristol BS11 9QD
VAT Registration No. GB 418 2290 55

INVOICE

Invoice number:   NW-2026-04417
Invoice date:     14 August 2026
Payment due:      13 September 2026 (30 days net)
Bill to:          ShopDesk Retail Ltd, 12 Halyard Street, Bristol BS1 4RN
Purchase order:   PO-88231

------------------------------------------------------------------------
Description                          Qty     Unit price        Line total
------------------------------------------------------------------------
Aeropress Go, boxed                    6          82.50            495.00
Burr grinder, model BG-2               2         145.00            290.00
Filter papers, pack of 350             1          39.99             39.99
------------------------------------------------------------------------
                                              Net total            824.99
                                           VAT at 20%             165.00
                                          TOTAL DUE            GBP 989.99
------------------------------------------------------------------------

Payment terms: Net 30 days from the invoice date. A late payment charge of
2% per month applies to overdue balances.

Remittance: Please quote invoice number NW-2026-04417 with all payments.
"""

INVOICE_DOC = {
    "document_id": "DOC-NW-2026-04417",
    "file_name": "northwind_invoice_NW-2026-04417.pdf",
    "supplier": "Northwind Supply Co.",
    "text": SUPPLIER_INVOICE_TEXT,
}


# --- accessors ------------------------------------------------------------
# Every read goes through a function, so the tool layer never reaches into a
# module-level dict and mutates the seed by accident.


def get_order(order_id: str) -> Order:
    key = order_id.strip().upper()
    if key not in ORDERS:
        raise OrderNotFound(order_id, sorted(ORDERS))
    return ORDERS[key]


def list_order_ids() -> list[str]:
    return sorted(ORDERS)


def get_tracking(order_id: str) -> dict[str, Any]:
    """The flaky dependency, on purpose.

    A-1422's carrier is down. The agent must cope — and, critically, must not
    invent a delivery date to fill the gap.
    """
    order = get_order(order_id)
    if order.order_id == "A-1422":
        raise CarrierUnavailable(order.order_id, "carrier returned 503 for 3 consecutive polls")
    return {
        "order_id": order.order_id,
        "status": "delivered" if order.delivered_on else "in_transit",
        "delivered_on": order.delivered_on.isoformat() if order.delivered_on else None,
        "promised_on": order.promised_on.isoformat(),
        "late_by_days": order.late_by_days,
    }


def get_ticket(ticket_id: str) -> Ticket:
    for t in TICKETS:
        if t.ticket_id == ticket_id:
            return t
    raise KeyError(f"No ticket '{ticket_id}'.")
