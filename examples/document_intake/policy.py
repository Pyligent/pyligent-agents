"""Scripted extractions for the three documents, plus their failure variants.

Every `evidence_quote` below is a genuine substring of its source document —
verified by a test, because a fixture that quietly stops matching turns the whole
demo into theatre.

Three failure variants, deliberately of three different kinds:

    csa      --flaw   the extractor transposes Threshold and MTA.
                      Both quotes are REAL. Evidence checking passes.
    invoice  --flaw   the extractor misreads one unit price, 82.50 -> 85.20.
                      The quote is REAL. Evidence checking passes.
    kyc      --flaw   the DOCUMENT is internally inconsistent: the passport name
                      differs from the application by one letter. The extraction
                      is PERFECT and every quote is verbatim.

In all three, evidence-gating and the independent verifier are satisfied. Only a
cross-field gate notices. That is the point of the example.
"""

from __future__ import annotations

import json

from pyligent_agents.testing import ScriptedTurn, router, turn


def _q(value, quote):
    return {"value": value, "evidence_quote": quote}


# --- 1. Credit Support Annex ----------------------------------------------

CSA_GOOD = {
    "fields": {
        "base_currency": _q("USD", '"Base Currency" means United States Dollars (USD).'),
        "eligible_currency": _q("USD", '"Base Currency" means United States Dollars (USD).'),
        "threshold": _q(5_000_000,
                        '"Threshold" means with respect to each party: USD 5,000,000.'),
        "mta": _q(500_000,
                  '"Minimum Transfer Amount" means with respect to each party:\n          USD 500,000.'),
        # Rounding is four fields, not one. "100,000" alone cannot say that the
        # Delivery Amount rounds UP while the Return Amount rounds DOWN, and
        # that direction is what decides who is over-collateralised.
        "rounding_delivery_amount": _q(100_000,
            "The Delivery Amount will be rounded up and the Return\n"
            "          Amount rounded down to the nearest integral multiple of USD 100,000."),
        "rounding_delivery_direction": _q("UP",
            "The Delivery Amount will be rounded up"),
        "rounding_return_amount": _q(100_000,
            "the nearest integral multiple of USD 100,000."),
        "rounding_return_direction": _q("DOWN",
            "the Return\n          Amount rounded down"),
        "independent_amount": _q(0,
                                 '"Independent Amount" means with respect to each party: zero.'),
        "governing_law": _q("English law", "This Annex is governed by English law."),
    },
    "eligible_collateral": [
        {"description": "Cash in the Base Currency", "valuation_pct": 100},
        {"description": "US Treasury obligations, residual maturity up to 5 years",
         "valuation_pct": 98},
        {"description": "US Treasury obligations, residual maturity 5 to 10 years",
         "valuation_pct": 96},
    ],
}

# Threshold and MTA read into each other's fields. Note that BOTH quotes are
# still genuine substrings of the document — the extractor read the right lines
# and assigned them to the wrong keys, which is exactly how this happens.
CSA_FLAWED = {
    **CSA_GOOD,
    "fields": {
        **CSA_GOOD["fields"],
        "threshold": _q(500_000,
                        '"Minimum Transfer Amount" means with respect to each party:\n          USD 500,000.'),
        "mta": _q(5_000_000,
                  '"Threshold" means with respect to each party: USD 5,000,000.'),
    },
}


# --- 2. Supplier invoice ---------------------------------------------------

INVOICE_LINES = [
    {"description": "Aeropress Go, boxed", "quantity": 6, "unit_price": 82.50,
     "line_total": 495.00},
    {"description": "Burr grinder, model BG-2", "quantity": 2, "unit_price": 145.00,
     "line_total": 290.00},
    {"description": "Filter papers, pack of 350", "quantity": 1, "unit_price": 39.99,
     "line_total": 39.99},
]

INVOICE_GOOD = {
    "fields": {
        "invoice_number": _q("NW-2026-04417", "Invoice number:   NW-2026-04417"),
        "invoice_date": _q("14 August 2026", "Invoice date:     14 August 2026"),
        "due_date": _q("13 September 2026", "Payment due:      13 September 2026"),
        "supplier": _q("Northwind Supply Co.", "NORTHWIND SUPPLY CO."),
        "purchase_order": _q("PO-88231", "Purchase order:   PO-88231"),
        "net_total": _q(824.99, "Net total            824.99"),
        "tax_rate_pct": _q(20, "VAT at 20%            165.00"),
        "gross_total": _q(989.99, "TOTAL DUE            GBP 989.99"),
        "currency": _q("GBP", "TOTAL DUE            GBP 989.99"),
    },
    "line_items": INVOICE_LINES,
}

# One digit transposed: 82.50 -> 85.20. The evidence quote is untouched and real.
INVOICE_FLAWED = {
    **INVOICE_GOOD,
    "line_items": [{**INVOICE_LINES[0], "unit_price": 85.20, "line_total": 511.20},
                   INVOICE_LINES[1], INVOICE_LINES[2]],
}


# --- 3. KYC onboarding pack -------------------------------------------------
# There is no flawed extraction here. The flaw is in the document, and this
# extraction reproduces it faithfully — which is the correct behaviour.

def _kyc(name_on_document: str, **overrides) -> dict:
    fields = {
        "application_reference": _q("APP-2026-11842",
                                    "Application reference:  APP-2026-11842"),
        "application_date": _q("21 August 2026", "Application date:       21 August 2026"),
        "applicant_name": _q("Jonathan Alexander Whitfield",
                             "Full legal name:        Jonathan Alexander Whitfield"),
        "date_of_birth": _q("03 February 2005", "Date of birth:          03 February 2005"),
        "place_of_birth": _q("Leeds, United Kingdom",
                             "Place of birth:         Leeds, United Kingdom"),
        "country_of_citizenship": _q("United Kingdom",
                                     "Country of citizenship: United Kingdom"),
        "country_of_residence": _q("United Kingdom",
                                   "Country of residence:   United Kingdom"),
        "document_type": _q("Passport", "Document type:          Passport"),
        "document_number": _q("548912337", "Document number:        548912337"),
        "name_on_document": _q(name_on_document,
                               f"Name as shown on document:  {name_on_document}"),
        "document_expiry": _q("12 June 2031", "Date of expiry:         12 June 2031"),
        "address_proof_type": _q("Utility bill", "Document type:          Utility bill"),
        "address_proof_provider": _q("Northern Grid Energy",
                                     "Provider:               Northern Grid Energy"),
        "address_proof_addressed_to": _q("Jonathan Alexander Whitfield",
                                         "Addressed to:           Jonathan Alexander Whitfield"),
        "address_proof_date": _q("02 August 2026", "Statement date:         02 August 2026"),
        "address_proof_format": _q("Original PDF from provider",
                                   "Submitted as:           Original PDF from provider"),
        "pep_status": _q("No", "Politically exposed person (PEP):        No"),
        "sanctions_result": _q("Clear", "Sanctions screening result:              Clear"),
        "source_of_funds": _q("Employment income",
                              "Source of funds:                         Employment income"),
    }
    fields.update(overrides)
    return {"fields": fields}


KYC_GOOD = _kyc("Jonathan Alexander Whitfield")
KYC_FROM_FLAWED_DOCUMENT = _kyc("Jonathon Alexander Whitfield")


# --- the verifier ----------------------------------------------------------

APPROVALS = {
    "csa": {"approved": True, "citations": [
        {"claim": "threshold", "paragraph": "11(b)(i)",
         "verbatim_quote": '"Threshold" means with respect to each party: USD 5,000,000.'},
        {"claim": "governing law", "paragraph": "11(e)",
         "verbatim_quote": "This Annex is governed by English law."},
    ], "reasons": ["Every extracted election is supported by the document."]},
    "invoice": {"approved": True, "citations": [
        {"claim": "invoice number", "paragraph": "header",
         "verbatim_quote": "Invoice number:   NW-2026-04417"},
        {"claim": "total due", "paragraph": "totals",
         "verbatim_quote": "TOTAL DUE            GBP 989.99"},
    ], "reasons": ["Every extracted value is supported by the document."]},
    "kyc": {"approved": True, "citations": [
        {"claim": "applicant name", "paragraph": "Section 1",
         "verbatim_quote": "Full legal name:        Jonathan Alexander Whitfield"},
        {"claim": "PEP status", "paragraph": "Section 4",
         "verbatim_quote": "Politically exposed person (PEP):        No"},
    ], "reasons": ["Every extracted value is supported by the document."]},
}

# A verifier that approves while citing text that is not in the document. Its
# citation is substring-checked, so the approval does not survive.
FABRICATED = {"approved": True, "citations": [
    {"claim": "the document is complete and consistent", "paragraph": "throughout",
     "verbatim_quote": ("All values in this document have been checked and are certified "
                        "complete and internally consistent by the issuing party.")},
], "reasons": ["Looks right to me."]}


# --- policies --------------------------------------------------------------

_EXTRACTORS = {
    "csa": "You extract the Paragraph 11",
    "invoice": "You extract a supplier invoice",
    "kyc": "You extract an individual customer onboarding pack",
}

_PAYLOADS = {
    ("csa", False): CSA_GOOD, ("csa", True): CSA_FLAWED,
    ("invoice", False): INVOICE_GOOD, ("invoice", True): INVOICE_FLAWED,
    # The KYC extraction is identical either way. Only the source differs.
    ("kyc", False): KYC_GOOD, ("kyc", True): KYC_FROM_FLAWED_DOCUMENT,
}


def build_policy(kind: str, *, flawed: bool = False, fabricate: bool = False):
    """One policy per run: the right extractor, and the right verifier."""
    payload = _PAYLOADS[(kind, flawed)]
    approval = FABRICATED if fabricate else APPROVALS[kind]

    return router({
        _EXTRACTORS[kind]:
            lambda c: ScriptedTurn(text=json.dumps(payload),
                                   input_tokens=1_800, output_tokens=420),
        "You are an independent reviewer":
            lambda c: ScriptedTurn(text=json.dumps(approval),
                                   input_tokens=2_200, output_tokens=260),
        "checking work that has just been completed":
            lambda c: turn("VERIFIED", input_tokens=400, output_tokens=8),
        "compacting an agent transcript":
            lambda c: turn("Earlier: read the document and transcribed its fields."),
    })
