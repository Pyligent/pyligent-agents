"""A small labelled dataset: 12 documents across three domains.

**Documents and gold labels are generated together from the same inputs.** That
is the important design choice here. A dataset where the text is written by hand
and the labels are written by hand drifts the first time somebody fixes a typo,
and a drifted label is worse than no label — it fails a correct system and
teaches you to ignore the eval.

Every case is a *document*, not a model behaviour. Model behaviours live in
`personas.py`. Keeping them apart is what lets you run several systems over the
same dataset and compare them.

Balance: 6 clean, 6 flawed. Both classes are required — a set with no clean
cases cannot detect a system that refers everything, which scores perfectly on
safety and is useless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pyligent_agents.evals import ACCEPT, REFER, Dataset, EvalCase

AS_OF = date(2026, 8, 25)


@dataclass
class Built:
    """A generated document plus everything a perfect extraction would return."""

    text: str
    fields: dict[str, Any]
    quotes: dict[str, str]
    extras: dict[str, Any] = field(default_factory=dict)


# ==========================================================================
# CSA
# ==========================================================================


def build_csa(*, party: str, threshold: int, mta: int, rounding: int = 100_000,
              governing_law: str | None = "English law") -> Built:
    law_clause = (f"(e) Governing Law. This Annex is governed by {governing_law}.\n"
                  if governing_law else "")
    text = f"""\
CREDIT SUPPORT ANNEX
to the Schedule to the ISDA Master Agreement dated as of 14 March 2019
between {party} ("Party A") and NORTHWIND BANK PLC ("Party B")

Paragraph 11. Elections and Variables

(a) Base Currency.
    "Base Currency" means United States Dollars (USD).
    "Eligible Currency" means the Base Currency.

(b) Credit Support Obligations.
    (i)   "Threshold" means with respect to each party: USD {threshold:,}.
    (ii)  "Minimum Transfer Amount" means with respect to each party: USD {mta:,}.
    (iii) "Rounding". The Delivery Amount will be rounded up and the Return Amount
          rounded down to the nearest integral multiple of USD {rounding:,}.
    (iv)  "Independent Amount" means with respect to each party: zero.

(c) Eligible Credit Support, with the Valuation Percentage specified:
      (A) Cash in the Base Currency ................................. 100%
      (B) US Treasury obligations, residual maturity up to 5 years ... 98%
      (C) US Treasury obligations, residual maturity 5 to 10 years ... 96%

{law_clause}"""
    rounding_quote = (
        f'"Rounding". The Delivery Amount will be rounded up and the Return Amount\n'
        f'          rounded down to the nearest integral multiple of USD {rounding:,}.')
    fields = {
        "base_currency": "USD",
        "eligible_currency": "USD",
        "threshold": threshold,
        "mta": mta,
        "rounding_delivery_amount": rounding,
        "rounding_delivery_direction": "UP",
        "rounding_return_amount": rounding,
        "rounding_return_direction": "DOWN",
    }
    quotes = {
        "base_currency": '"Base Currency" means United States Dollars (USD).',
        "eligible_currency": '"Eligible Currency" means the Base Currency.',
        "threshold": f'"Threshold" means with respect to each party: USD {threshold:,}.',
        "mta": f'"Minimum Transfer Amount" means with respect to each party: USD {mta:,}.',
        "rounding_delivery_amount": rounding_quote,
        "rounding_delivery_direction": "The Delivery Amount will be rounded up",
        "rounding_return_amount": f"the nearest integral multiple of USD {rounding:,}.",
        "rounding_return_direction": "the Return Amount\n          rounded down",
    }
    if governing_law:
        fields["governing_law"] = governing_law
        quotes["governing_law"] = f"This Annex is governed by {governing_law}."
    return Built(text, fields, quotes, {"eligible_collateral": [
        {"description": "Cash in the Base Currency", "valuation_pct": 100},
        {"description": "US Treasury obligations, residual maturity up to 5 years",
         "valuation_pct": 98},
        {"description": "US Treasury obligations, residual maturity 5 to 10 years",
         "valuation_pct": 96},
    ]})


# ==========================================================================
# Invoice
# ==========================================================================


def build_invoice(*, number: str, invoice_date: date, due_date: date,
                  lines: list[tuple[str, int, float]], tax_rate: float = 20.0,
                  stated_net: float | None = None) -> Built:
    rows, computed_net = [], 0.0
    for desc, qty, unit in lines:
        total = round(qty * unit, 2)
        computed_net += total
        rows.append(f"{desc:<36}{qty:>4}{unit:>15,.2f}{total:>18,.2f}")
    computed_net = round(computed_net, 2)
    # `stated_net` lets a case print a total that does not match its lines —
    # a real supplier error, and the thing the arithmetic gate exists for.
    net = computed_net if stated_net is None else stated_net
    tax = round(net * tax_rate / 100, 2)
    gross = round(net + tax, 2)

    text = f"""\
NORTHWIND SUPPLY CO.
Unit 4, Kestrel Park, Bristol BS11 9QD

INVOICE

Invoice number:   {number}
Invoice date:     {invoice_date.strftime('%d %B %Y')}
Payment due:      {due_date.strftime('%d %B %Y')}
Purchase order:   PO-88231

------------------------------------------------------------------------
Description                          Qty     Unit price        Line total
------------------------------------------------------------------------
""" + "\n".join(rows) + f"""
------------------------------------------------------------------------
                                              Net total        {net:>10,.2f}
                                             VAT at {tax_rate:.0f}%        {tax:>10,.2f}
                                          TOTAL DUE        GBP {gross:>10,.2f}
------------------------------------------------------------------------
"""
    fields = {"invoice_number": number,
              "invoice_date": invoice_date.strftime("%d %B %Y"),
              "due_date": due_date.strftime("%d %B %Y"),
              "net_total": net, "tax_rate_pct": tax_rate,
              "gross_total": gross, "currency": "GBP"}
    quotes = {
        "invoice_number": f"Invoice number:   {number}",
        "invoice_date": f"Invoice date:     {invoice_date.strftime('%d %B %Y')}",
        "due_date": f"Payment due:      {due_date.strftime('%d %B %Y')}",
        "net_total": f"Net total        {net:>10,.2f}",
        "tax_rate_pct": f"VAT at {tax_rate:.0f}%        {tax:>10,.2f}",
        "gross_total": f"TOTAL DUE        GBP {gross:>10,.2f}",
        "currency": f"TOTAL DUE        GBP {gross:>10,.2f}",
    }
    return Built(text, fields, quotes, {"line_items": [
        {"description": d, "quantity": q, "unit_price": u, "line_total": round(q * u, 2)}
        for d, q, u in lines]})


# ==========================================================================
# KYC
# ==========================================================================


def build_kyc(*, ref: str, applicant: str, name_on_document: str | None = None,
              dob: date, expiry: date, address_proof: date,
              application_date: date = AS_OF, pep: str = "No",
              id_type: str = "Passport",
              address_proof_type: str = "Utility bill",
              address_proof_provider: str = "Northern Grid Energy",
              addressed_to: str | None = None,
              address_proof_format: str = "Original PDF from provider") -> Built:
    on_doc = name_on_document or applicant
    billed_to = addressed_to or applicant
    text = f"""\
CUSTOMER ONBOARDING PACK
Pyligent Financial Services — Individual Account Application

Application reference:  {ref}
Application date:       {application_date.strftime('%d %B %Y')}

SECTION 1 — APPLICANT
Full legal name:        {applicant}
Date of birth:          {dob.strftime('%d %B %Y')}
Place of birth:         Leeds, United Kingdom
Country of citizenship: United Kingdom
Country of residence:   United Kingdom

SECTION 2 — IDENTITY DOCUMENT
Document type:          {id_type}
Document number:        548912337
Name as shown on document:  {on_doc}
Date of expiry:         {expiry.strftime('%d %B %Y')}

SECTION 3 — PROOF OF ADDRESS
Document type:          {address_proof_type}
Provider:               {address_proof_provider}
Addressed to:           {billed_to}
Statement date:         {address_proof.strftime('%d %B %Y')}
Submitted as:           {address_proof_format}

SECTION 4 — DECLARATIONS
Politically exposed person (PEP):        {pep}
Sanctions screening result:              Clear
Source of funds:                         Employment income
"""
    fields = {
        "application_reference": ref,
        "application_date": application_date.strftime("%d %B %Y"),
        "applicant_name": applicant,
        "date_of_birth": dob.strftime("%d %B %Y"),
        "place_of_birth": "Leeds, United Kingdom",
        "country_of_citizenship": "United Kingdom",
        "document_type": id_type,
        "name_on_document": on_doc,
        "document_expiry": expiry.strftime("%d %B %Y"),
        "address_proof_type": address_proof_type,
        "address_proof_provider": address_proof_provider,
        "address_proof_addressed_to": billed_to,
        "address_proof_date": address_proof.strftime("%d %B %Y"),
        "address_proof_format": address_proof_format,
        "pep_status": pep,
    }
    quotes = {
        "application_reference": f"Application reference:  {ref}",
        "application_date": f"Application date:       {application_date.strftime('%d %B %Y')}",
        "applicant_name": f"Full legal name:        {applicant}",
        "date_of_birth": f"Date of birth:          {dob.strftime('%d %B %Y')}",
        "place_of_birth": "Place of birth:         Leeds, United Kingdom",
        "country_of_citizenship": "Country of citizenship: United Kingdom",
        "document_type": f"Document type:          {id_type}",
        "name_on_document": f"Name as shown on document:  {on_doc}",
        "document_expiry": f"Date of expiry:         {expiry.strftime('%d %B %Y')}",
        "address_proof_type": f"Document type:          {address_proof_type}",
        "address_proof_provider": f"Provider:               {address_proof_provider}",
        "address_proof_addressed_to": f"Addressed to:           {billed_to}",
        "address_proof_date": f"Statement date:         {address_proof.strftime('%d %B %Y')}",
        "address_proof_format": f"Submitted as:           {address_proof_format}",
        "pep_status": f"Politically exposed person (PEP):        {pep}",
    }
    return Built(text, fields, quotes, {})


# ==========================================================================
# The dataset
# ==========================================================================

BUILDS: dict[str, Built] = {}


def _case(case_id, kind, built, decision, gates=(), note="") -> EvalCase:
    BUILDS[case_id] = built
    return EvalCase(case_id=case_id, kind=kind, source_text=built.text,
                    gold_fields=built.fields, gold_decision=decision,
                    gold_failing_gates=tuple(gates), note=note)


def build_dataset() -> Dataset:
    ds = Dataset("document-intake-v1")

    # --- CSA -------------------------------------------------------------
    ds.add(_case("csa/clean", "csa",
                 build_csa(party="ATLAS GLOBAL MARKETS LTD", threshold=5_000_000, mta=500_000),
                 ACCEPT, note="A well-drafted Annex. Nothing should fire."))
    ds.add(_case("csa/clean-large", "csa",
                 build_csa(party="BOREAL CAPITAL LLP", threshold=25_000_000, mta=1_000_000,
                           rounding=250_000),
                 ACCEPT, note="Different magnitudes; checks the gates are not tuned to one set."))
    ds.add(_case("csa/mta-exceeds-threshold", "csa",
                 build_csa(party="CYGNUS SF SA", threshold=250_000, mta=5_000_000),
                 REFER, ("mta_not_transposed_with_threshold",),
                 note="Genuine drafting error: the MTA is larger than the Threshold. "
                      "Both values are money, both present, both individually plausible."))
    # The regression guard. A 2016 VM CSA elects a Threshold of ZERO — variation
    # margin is fully collateralised — while the MTA stays at the regulatory
    # 500,000. So MTA > Threshold, legitimately, in the single most common CSA
    # shape in the market. ISDA's own worked example is this shape.
    #
    # The gate here used to be `mta <= threshold`, which referred every one of
    # them. This case is why it no longer does, and it must never be deleted
    # to make a gate pass.
    ds.add(_case("csa/vm-zero-threshold", "csa",
                 build_csa(party="EIGER MARKETS AG", threshold=0, mta=500_000),
                 ACCEPT,
                 note="2016 VM CSA: Threshold zero, MTA 500,000. MTA legitimately exceeds "
                      "the Threshold. An ordering gate refers this and is wrong to."))
    ds.add(_case("csa/no-governing-law", "csa",
                 build_csa(party="DELPHI RATES BV", threshold=5_000_000, mta=500_000,
                           governing_law=None),
                 REFER, ("required_fields",),
                 note="The clause is absent. A model that invents 'English law' here is "
                      "the failure this case exists to catch."))

    # --- Invoice ---------------------------------------------------------
    std = [("Aeropress Go, boxed", 6, 82.50), ("Burr grinder, model BG-2", 2, 145.00),
           ("Filter papers, pack of 350", 1, 39.99)]
    ds.add(_case("invoice/clean", "invoice",
                 build_invoice(number="NW-2026-04417", invoice_date=date(2026, 8, 14),
                               due_date=date(2026, 9, 13), lines=std),
                 ACCEPT, note="Lines sum to the stated total."))
    ds.add(_case("invoice/clean-single-line", "invoice",
                 build_invoice(number="NW-2026-04502", invoice_date=date(2026, 8, 18),
                               due_date=date(2026, 9, 17),
                               lines=[("Espresso machine, model EM-9", 1, 1_249.00)]),
                 ACCEPT, note="One line. Checks the arithmetic gate on a trivial invoice."))
    ds.add(_case("invoice/lines-do-not-sum", "invoice",
                 build_invoice(number="NW-2026-04611", invoice_date=date(2026, 8, 12),
                               due_date=date(2026, 9, 11), lines=std, stated_net=851.19),
                 REFER, ("lines_sum_to_total",),
                 note="The supplier's printed total is £26.20 above its own lines. Every "
                      "field is present and correctly typed; only arithmetic sees it."))
    ds.add(_case("invoice/due-before-issue", "invoice",
                 build_invoice(number="NW-2026-04702", invoice_date=date(2026, 8, 20),
                               due_date=date(2026, 7, 20), lines=std),
                 REFER, ("due_after_invoice_date",),
                 note="Due date precedes the invoice date — a keying error."))

    # --- KYC -------------------------------------------------------------
    ds.add(_case("kyc/clean", "kyc",
                 build_kyc(ref="APP-2026-11842", applicant="Jonathan Alexander Whitfield",
                           dob=date(2005, 2, 3), expiry=date(2031, 6, 12),
                           address_proof=date(2026, 8, 2)),
                 ACCEPT, note="A complete, consistent pack."))
    ds.add(_case("kyc/clean-older-applicant", "kyc",
                 build_kyc(ref="APP-2026-11903", applicant="Margaret Rose Okonjo",
                           dob=date(1974, 11, 19), expiry=date(2029, 3, 4),
                           address_proof=date(2026, 7, 28)),
                 ACCEPT, note="Different name shape and dates; guards against overfitting."))
    ds.add(_case("kyc/name-mismatch", "kyc",
                 build_kyc(ref="APP-2026-12004", applicant="Jonathan Alexander Whitfield",
                           name_on_document="Jonathon Alexander Whitfield",
                           dob=date(2005, 2, 3), expiry=date(2031, 6, 12),
                           address_proof=date(2026, 8, 2)),
                 REFER, ("name_matches_document",),
                 note="One letter apart. The extraction can be PERFECT and every quote "
                      "verbatim; the pack is still not acceptable. The classic finding."))
    ds.add(_case("kyc/underage", "kyc",
                 build_kyc(ref="APP-2026-12115", applicant="Daniel Peter Hargreaves",
                           dob=date(2010, 5, 30), expiry=date(2032, 1, 9),
                           address_proof=date(2026, 8, 11)),
                 REFER, ("applicant_is_of_age",),
                 note="Sixteen at application. Requires arithmetic across two date fields."))
    # AWS Marketplace KYC Documents Guide: the proof of address "must be
    # addressed to the corresponding person ... names should match the ID/legal
    # document provided". A bill in a partner's or landlord's name proves an
    # address exists; it does not tie this applicant to it.
    ds.add(_case("kyc/address-proof-in-another-name", "kyc",
                 build_kyc(ref="APP-2026-12220", applicant="Priya Anand Raghavan",
                           dob=date(1994, 7, 12), expiry=date(2030, 3, 4),
                           address_proof=date(2026, 8, 2),
                           addressed_to="Michael J Raghavan"),
                 REFER, ("address_proof_names_applicant",),
                 note="Every field present, every quote real, document in date. The bill is "
                      "simply in someone else's name — a different person entirely."))
    # Same guide: "The document must not be a screenshot." A screenshot of an
    # online billing page is trivially fabricated and carries no provenance.
    ds.add(_case("kyc/address-proof-screenshot", "kyc",
                 build_kyc(ref="APP-2026-12318", applicant="Tomas Eduard Lindqvist",
                           dob=date(1988, 11, 2), expiry=date(2029, 6, 19),
                           address_proof=date(2026, 8, 14),
                           address_proof_format="Screenshot of online account page"),
                 REFER, ("address_proof_not_a_screenshot",),
                 note="Recent, correctly addressed, right document type — and unverifiable."))

    return ds.validate()


DATASET = build_dataset()
