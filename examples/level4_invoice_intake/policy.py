"""Scripted behaviour for the Level 4 example.

Every `evidence_quote` below is a genuine substring of
`shopdesk.data.SUPPLIER_INVOICE_TEXT`. That is load-bearing: two of the three
variants differ from the good one by exactly one thing, and the gates catch each.

    good_policy          everything correct
    fabricating_policy   the verifier invents its citation
    transposed_policy    one unit price is mis-read: 82.50 -> 85.20
"""

from __future__ import annotations

import json

from pyligent_agents.testing import ScriptedTurn, router, turn


def _q(v, quote):
    return {"value": v, "evidence_quote": quote}


HEADER = {"fields": {
    "invoice_number": _q("NW-2026-04417", "Invoice number:   NW-2026-04417"),
    "invoice_date": _q("14 August 2026", "Invoice date:     14 August 2026"),
    "due_date": _q("13 September 2026", "Payment due:      13 September 2026 (30 days net)"),
    "supplier": _q("Northwind Supply Co.", "NORTHWIND SUPPLY CO."),
    "purchase_order": _q("PO-88231", "Purchase order:   PO-88231"),
    "net_total": _q(824.99, "Net total            824.99"),
    "tax_rate_pct": _q(20, "VAT at 20%             165.00"),
    "tax_amount": _q(165.00, "VAT at 20%             165.00"),
    "gross_total": _q(989.99, "TOTAL DUE            GBP 989.99"),
    "currency": _q("GBP", "TOTAL DUE            GBP 989.99"),
}}

LINES = {"line_items": [
    {"description": "Aeropress Go, boxed", "quantity": 6, "unit_price": 82.50,
     "line_total": 495.00,
     "evidence_quote": "Aeropress Go, boxed                    6          82.50            495.00"},
    {"description": "Burr grinder, model BG-2", "quantity": 2, "unit_price": 145.00,
     "line_total": 290.00,
     "evidence_quote": "Burr grinder, model BG-2               2         145.00            290.00"},
    {"description": "Filter papers, pack of 350", "quantity": 1, "unit_price": 39.99,
     "line_total": 39.99,
     "evidence_quote": "Filter papers, pack of 350             1          39.99             39.99"},
]}

# One digit transposed: 82.50 -> 85.20. Every field is present, every type is
# right, the quote is real, and the invoice no longer adds up.
TRANSPOSED = {"line_items": [
    {**LINES["line_items"][0], "unit_price": 85.20, "line_total": 511.20},
    LINES["line_items"][1],
    LINES["line_items"][2],
]}

APPROVAL = {"approved": True, "citations": [
    {"claim": "invoice_number = NW-2026-04417", "paragraph": "header",
     "verbatim_quote": "Invoice number:   NW-2026-04417"},
    {"claim": "gross_total = 989.99", "paragraph": "totals",
     "verbatim_quote": "TOTAL DUE            GBP 989.99"},
    {"claim": "payment terms are net 30", "paragraph": "terms",
     "verbatim_quote": "Payment terms: Net 30 days from the invoice date."},
], "reasons": ["All extracted values are supported by the document."]}

# The same approval, with a citation that is NOT in the document.
FABRICATED = {"approved": True, "citations": [
    {"claim": "gross_total = 989.99", "paragraph": "totals",
     "verbatim_quote": ("The total amount payable under this invoice is GBP 989.99, "
                        "inclusive of all applicable taxes and charges.")},
], "reasons": ["Looks right to me."]}


def _extractors(lines: dict) -> dict:
    return {
        "You extract header fields from a supplier invoice":
            lambda c: ScriptedTurn(text=json.dumps(HEADER), input_tokens=1_900, output_tokens=460),
        "You extract the line items from a supplier invoice":
            lambda c: ScriptedTurn(text=json.dumps(lines), input_tokens=1_900, output_tokens=380),
        "checking work that has just been completed":
            lambda c: turn("VERIFIED", input_tokens=500, output_tokens=10),
        "compacting an agent transcript":
            lambda c: turn("Earlier: read the invoice and transcribed its lines."),
    }


good_policy = router({
    **_extractors(LINES),
    "You are an independent reviewer":
        lambda c: ScriptedTurn(text=json.dumps(APPROVAL), input_tokens=2_400, output_tokens=300),
})

fabricating_policy = router({
    **_extractors(LINES),
    "You are an independent reviewer":
        lambda c: ScriptedTurn(text=json.dumps(FABRICATED), input_tokens=2_400, output_tokens=160),
})

transposed_policy = router({
    **_extractors(TRANSPOSED),
    "You are an independent reviewer":
        lambda c: ScriptedTurn(text=json.dumps(APPROVAL), input_tokens=2_400, output_tokens=300),
})
