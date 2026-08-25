"""Three documents, three domains, one pattern.

A Credit Support Annex, a supplier invoice, and a KYC onboarding pack. They have
nothing in common as *documents* — different vocabulary, different structure,
different regulator. They have everything in common as *work*: pull values out,
prove each one came from the page, and refuse to accept the result if the values
do not hang together.

Each `DocumentSpec` carries four things:

    text            the source, which is the only ground truth
    required        the fields that must be present
    system          the extraction prompt
    domain_gates    the checks a JSON schema could not express

That last field is where the value is. The five generic gates below are the same
for all three; the domain gates are different every time, and they are what
catch a plausible, well-formed, wrong extraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from pyligent_agents.verify import GateSet, cross_field, evidence_gated_extraction

# The date these documents are being processed. Fixed so the demo is
# reproducible — several gates are date-relative.
AS_OF = date(2026, 8, 25)


@dataclass(frozen=True)
class DocumentSpec:
    key: str
    title: str
    document_id: str
    text: str
    required: tuple[str, ...]
    system: str
    domain_gates: Callable[[], GateSet]
    what_the_domain_gate_catches: str
    # Some failures live in the DOCUMENT, not the extraction. A KYC pack whose
    # passport name differs from its application form is internally
    # inconsistent; a perfect extraction reproduces the inconsistency faithfully
    # and every evidence quote checks out. Only a cross-field gate notices.
    flawed_text: str | None = None
    flaw_origin: str = "extraction"

    def source(self, *, flawed: bool = False) -> str:
        return self.flawed_text if (flawed and self.flawed_text) else self.text

    def gates(self) -> GateSet:
        """Five generic gates, plus this document's own."""
        return evidence_gated_extraction(*self.required, under="fields").extend(
            self.domain_gates()
        )


# --- helpers used by the domain gates -------------------------------------


def _num(value: Any) -> float:
    """Parse a number out of whatever the extractor produced."""
    s = str(value)
    for junk in ("£", "$", "GBP", "USD", "EUR", ",", "%"):
        s = s.replace(junk, "")
    return float(s.strip())


def _field(artifact: dict[str, Any], name: str) -> Any:
    return (artifact.get("fields") or {}).get(name, {}).get("value")


def _date(value: Any) -> date:
    """Accept '14 August 2026' or '2026-08-14'."""
    text = str(value).split(" (")[0].strip()
    for fmt in ("%d %B %Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            from datetime import datetime

            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {value!r}")


def _normalise_name(value: Any) -> str:
    """Casefold, strip punctuation and collapse spaces — but do not fuzzy-match.

    'Mr. Jonathan A. Whitfield' and 'Jonathan A Whitfield' are the same person.
    'Jonathan' and 'Jonathon' are not, and this deliberately will not pretend
    otherwise: on a KYC file a one-letter difference is the finding.
    """
    import re

    return re.sub(r"[^a-z ]", "", str(value).lower()).replace("mr ", "").replace("ms ", "")


# ==========================================================================
# 1. Credit Support Annex — derivatives collateral
# ==========================================================================

CSA_TEXT = """\
CREDIT SUPPORT ANNEX
to the Schedule to the ISDA Master Agreement dated as of 14 March 2019
between ATLAS GLOBAL MARKETS LTD ("Party A") and NORTHWIND BANK PLC ("Party B")

Paragraph 11. Elections and Variables

(a) Base Currency.
    "Base Currency" means United States Dollars (USD).

(b) Credit Support Obligations.
    (i)   "Threshold" means with respect to each party: USD 5,000,000.
    (ii)  "Minimum Transfer Amount" means with respect to each party:
          USD 500,000.
    (iii) "Rounding". The Delivery Amount will be rounded up and the Return
          Amount rounded down to the nearest integral multiple of USD 100,000.
    (iv)  "Independent Amount" means with respect to each party: zero.

(c) Eligible Credit Support. The following will qualify as Eligible Credit
    Support, with the Valuation Percentage specified:

      (A) Cash in the Base Currency ................................. 100%
      (B) US Treasury obligations, residual maturity up to 5 years ... 98%
      (C) US Treasury obligations, residual maturity 5 to 10 years ... 96%

    For the avoidance of doubt, equity securities shall not constitute
    Eligible Credit Support.

(d) Dispute Resolution.
    "Resolution Time" means 1:00 p.m. London time on the Local Business Day
    following the date on which notice of a dispute is given.

(e) Governing Law. This Annex is governed by English law.
"""


def _csa_gates() -> GateSet:
    def mta_within_threshold(a: dict[str, Any]) -> bool:
        # An MTA larger than the Threshold is not impossible, but it is almost
        # always the two numbers read into the wrong fields. Both are money,
        # both are present, both are individually plausible — a schema sees
        # nothing wrong at all.
        try:
            return _num(_field(a, "mta")) <= _num(_field(a, "threshold"))
        except (TypeError, ValueError):
            return False

    def valuation_percentages_are_percentages(a: dict[str, Any]) -> bool:
        # A CSA states *valuation percentages*; systems store *haircuts*. A 98%
        # valuation percentage is a 2% haircut. Reading "98" straight into a
        # haircut field prices a $1m bond at $20,000.
        rows = a.get("eligible_collateral") or []
        if not rows:
            return False
        values = [_num(r.get("valuation_pct")) for r in rows]
        return all(50 <= v <= 100 for v in values) and max(values) >= 95

    return (
        GateSet()
        .add("mta_within_threshold",
             "Minimum Transfer Amount does not exceed the Threshold",
             cross_field("MTA is within the Threshold", mta_within_threshold,
                         message=("Minimum Transfer Amount exceeds the Threshold — these "
                                  "two are almost certainly transposed. Do not load.")))
        .add("valuation_percentages_sane",
             "Eligible collateral carries valuation percentages, not haircuts",
             cross_field("valuation percentages look like percentages",
                         valuation_percentages_are_percentages,
                         message=("These read as haircuts, not valuation percentages. A 98% "
                                  "valuation percentage is a 2% haircut; storing them the "
                                  "wrong way round misprices the whole collateral book.")))
    )


CSA_SYSTEM = """\
You extract the Paragraph 11 elections from an ISDA Credit Support Annex.

Return these fields: base_currency, threshold, mta, rounding, independent_amount,
governing_law.

Also return the eligible collateral schedule as `eligible_collateral`, each entry
with `description` and `valuation_pct`. The document states VALUATION
PERCENTAGES — transcribe them as printed. Do not convert them to haircuts.

For every field give the exact words from the document that establish it. Copy
each quote character for character; quotes are checked against the source and an
invented one fails the whole extraction. If a field is genuinely absent, omit it
rather than writing a placeholder.

Respond with JSON only:
{"fields": {"<name>": {"value": <value>, "evidence_quote": "<exact words>"}},
 "eligible_collateral": [{"description": "...", "valuation_pct": <n>}]}"""


# ==========================================================================
# 2. Supplier invoice — accounts payable
# ==========================================================================

INVOICE_TEXT = """\
NORTHWIND SUPPLY CO.
Unit 4, Kestrel Park, Bristol BS11 9QD
VAT Registration No. GB 418 2290 55

INVOICE

Invoice number:   NW-2026-04417
Invoice date:     14 August 2026
Payment due:      13 September 2026
Bill to:          Pyligent Retail Ltd, 12 Halyard Street, Bristol BS1 4RN
Purchase order:   PO-88231

------------------------------------------------------------------------
Description                          Qty     Unit price        Line total
------------------------------------------------------------------------
Aeropress Go, boxed                    6          82.50            495.00
Burr grinder, model BG-2               2         145.00            290.00
Filter papers, pack of 350             1          39.99             39.99
------------------------------------------------------------------------
                                              Net total            824.99
                                             VAT at 20%            165.00
                                          TOTAL DUE            GBP 989.99
------------------------------------------------------------------------

Payment terms: Net 30 days from the invoice date.
"""


def _invoice_gates() -> GateSet:
    def lines_sum_to_total(a: dict[str, Any]) -> bool:
        # The gate that earns its keep. Every field present, every type right,
        # every value plausible — and one unit price misread, so the invoice
        # does not add up. One line of arithmetic; no schema catches it.
        lines = a.get("line_items") or []
        if not lines:
            return False
        try:
            net = round(sum(_num(x["quantity"]) * _num(x["unit_price"]) for x in lines), 2)
            rate = _num(_field(a, "tax_rate_pct"))
            gross = round(net * (1 + rate / 100), 2)
            return (abs(net - _num(_field(a, "net_total"))) <= 0.01
                    and abs(gross - _num(_field(a, "gross_total"))) <= 0.01)
        except (KeyError, TypeError, ValueError):
            return False

    def due_after_invoice_date(a: dict[str, Any]) -> bool:
        try:
            return _date(_field(a, "due_date")) > _date(_field(a, "invoice_date"))
        except (TypeError, ValueError):
            return False

    return (
        GateSet()
        .add("lines_sum_to_total",
             "Line items reconcile to the stated net and gross totals",
             cross_field("line items reconcile", lines_sum_to_total,
                         message=("line items do not reconcile to the stated totals — a "
                                  "transposed digit or a missed line. Do not pay.")))
        .add("due_after_invoice_date",
             "The due date falls after the invoice date",
             cross_field("due date is after the invoice date", due_after_invoice_date,
                         message="due date is not after the invoice date; likely misread"))
    )


INVOICE_SYSTEM = """\
You extract a supplier invoice for accounts payable.

Return these fields: invoice_number, invoice_date, due_date, supplier,
purchase_order, net_total, tax_rate_pct, gross_total, currency.

Also return `line_items`, each with description, quantity, unit_price and
line_total, transcribed exactly as printed. Do not recompute anything.

For every field give the exact words from the document that establish it. Copy
each quote character for character; quotes are checked against the source.

Respond with JSON only:
{"fields": {"<name>": {"value": <value>, "evidence_quote": "<exact words>"}},
 "line_items": [{"description": "...", "quantity": <n>, "unit_price": <n>,
                 "line_total": <n>}]}"""


# ==========================================================================
# 3. KYC onboarding pack — customer due diligence
# ==========================================================================

KYC_TEXT = """\
CUSTOMER ONBOARDING PACK
Pyligent Financial Services — Individual Account Application

Application reference:  APP-2026-11842
Application date:       21 August 2026
Account type:           Individual investment account

SECTION 1 — APPLICANT
Full legal name:        Jonathan Alexander Whitfield
Date of birth:          03 February 2005
Nationality:            British
Residential address:    44 Cranmer Road, Leeds LS6 3TX
Country of residence:   United Kingdom

SECTION 2 — IDENTITY DOCUMENT
Document type:          United Kingdom passport
Document number:        548912337
Name as shown on document:  Jonathan Alexander Whitfield
Date of issue:          12 June 2021
Date of expiry:         12 June 2031
Issuing authority:      HM Passport Office

SECTION 3 — PROOF OF ADDRESS
Document type:          Utility bill (electricity)
Provider:               Northern Grid Energy
Statement date:         02 August 2026
Address as shown:       44 Cranmer Road, Leeds LS6 3TX

SECTION 4 — DECLARATIONS
Politically exposed person (PEP):        No
Sanctions screening result:              Clear
Source of funds:                         Employment income
Tax residency:                           United Kingdom only

Signed by the applicant on 21 August 2026.
"""

# The same pack, with one letter different in Section 2. This is what a real
# KYC finding looks like: the extraction is perfect, every quote is verbatim,
# and the file is still not acceptable.
KYC_TEXT_MISMATCH = KYC_TEXT.replace(
    "Name as shown on document:  Jonathan Alexander Whitfield",
    "Name as shown on document:  Jonathon Alexander Whitfield")

MINIMUM_AGE = 18
MAX_ADDRESS_PROOF_AGE_DAYS = 90


def _kyc_gates() -> GateSet:
    def applicant_is_of_age(a: dict[str, Any]) -> bool:
        try:
            dob = _date(_field(a, "date_of_birth"))
            applied = _date(_field(a, "application_date"))
        except (TypeError, ValueError):
            return False
        years = applied.year - dob.year - ((applied.month, applied.day) < (dob.month, dob.day))
        return years >= MINIMUM_AGE

    def identity_document_is_valid(a: dict[str, Any]) -> bool:
        try:
            return _date(_field(a, "document_expiry")) > _date(_field(a, "application_date"))
        except (TypeError, ValueError):
            return False

    def name_on_document_matches_application(a: dict[str, Any]) -> bool:
        # The classic KYC finding, and the clearest example of a check a schema
        # cannot make: both fields are present, both are strings, both are
        # plausible names. They just are not the *same* name.
        try:
            return (_normalise_name(_field(a, "applicant_name"))
                    == _normalise_name(_field(a, "name_on_document")))
        except (TypeError, ValueError):
            return False

    def address_proof_is_recent(a: dict[str, Any]) -> bool:
        try:
            age = (_date(_field(a, "application_date"))
                   - _date(_field(a, "address_proof_date"))).days
        except (TypeError, ValueError):
            return False
        return 0 <= age <= MAX_ADDRESS_PROOF_AGE_DAYS

    return (
        GateSet()
        .add("applicant_is_of_age",
             f"The applicant is at least {MINIMUM_AGE} at the application date",
             cross_field("applicant is of age", applicant_is_of_age,
                         message=(f"the applicant is under {MINIMUM_AGE} at the application "
                                  f"date. Do not open the account.")))
        .add("identity_document_valid",
             "The identity document has not expired",
             cross_field("identity document is in date", identity_document_is_valid,
                         message="the identity document is expired at the application date"))
        .add("name_matches_document",
             "The name on the identity document matches the application",
             cross_field("names match", name_on_document_matches_application,
                         message=("the name on the identity document does not match the name "
                                  "on the application. Refer to compliance; do not proceed.")))
        .add("address_proof_recent",
             f"Proof of address is no more than {MAX_ADDRESS_PROOF_AGE_DAYS} days old",
             cross_field("address proof is recent", address_proof_is_recent,
                         message=(f"proof of address is older than "
                                  f"{MAX_ADDRESS_PROOF_AGE_DAYS} days or post-dates the "
                                  f"application")))
    )


KYC_SYSTEM = """\
You extract a KYC onboarding pack for an individual account application.

Return these fields: application_reference, application_date, applicant_name,
date_of_birth, nationality, country_of_residence, document_type,
document_number, name_on_document, document_expiry, address_proof_date,
pep_status, sanctions_result, source_of_funds.

`applicant_name` comes from Section 1. `name_on_document` comes from Section 2 —
transcribe each exactly as written, even if they look the same. Do not normalise
or correct spelling.

For every field give the exact words from the document that establish it. Copy
each quote character for character; quotes are checked against the source. If a
field is genuinely absent, omit it rather than writing a placeholder.

Respond with JSON only:
{"fields": {"<name>": {"value": <value>, "evidence_quote": "<exact words>"}}}"""


# --- the registry ---------------------------------------------------------

DOCUMENTS: dict[str, DocumentSpec] = {
    "csa": DocumentSpec(
        key="csa",
        title="ISDA Credit Support Annex",
        document_id="DOC-CSA-ATLAS-2019",
        text=CSA_TEXT,
        required=("base_currency", "threshold", "mta", "rounding", "governing_law"),
        system=CSA_SYSTEM,
        domain_gates=_csa_gates,
        what_the_domain_gate_catches=(
            "Threshold and Minimum Transfer Amount read into each other's fields, "
            "and valuation percentages stored as haircuts."),
    ),
    "invoice": DocumentSpec(
        key="invoice",
        title="Supplier invoice",
        document_id="DOC-NW-2026-04417",
        text=INVOICE_TEXT,
        required=("invoice_number", "invoice_date", "due_date", "net_total",
                  "tax_rate_pct", "gross_total", "currency"),
        system=INVOICE_SYSTEM,
        domain_gates=_invoice_gates,
        what_the_domain_gate_catches=(
            "A transposed digit in a unit price, so the lines no longer sum to "
            "the stated total."),
    ),
    "kyc": DocumentSpec(
        key="kyc",
        title="KYC onboarding pack",
        document_id="DOC-KYC-APP-2026-11842",
        text=KYC_TEXT,
        required=("application_reference", "application_date", "applicant_name",
                  "date_of_birth", "document_type", "name_on_document",
                  "document_expiry", "address_proof_date", "pep_status"),
        system=KYC_SYSTEM,
        domain_gates=_kyc_gates,
        what_the_domain_gate_catches=(
            "A name on the identity document that does not match the name on the "
            "application, an under-age applicant, an expired document, or stale "
            "proof of address."),
        flawed_text=KYC_TEXT_MISMATCH,
        flaw_origin="document",
    ),
}
