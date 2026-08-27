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

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from document_intake.cdm import DIRECTIONS, CdmError, classify_rounding, to_cdm
from pyligent_agents.verify import (
    GateSet,
    cross_field,
    evidence_gated_extraction,
    iso_currency,
    no_cross_reference_values,
    values_are_numeric,
)

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


def _csa_amount(a: dict[str, Any], base: str) -> float:
    """Read `<base>`, or the per-party pair, as one comparable number.

    Most CSAs elect "with respect to each party"; some elect different figures
    per party. Where they differ, the conservative reading for a sanity check
    is the larger — that is the one that would move the most collateral.
    """
    values = []
    for name in (base, f"{base}_party_a", f"{base}_party_b"):
        raw = _field(a, name)
        if raw is not None:
            values.append(_num(raw))
    if not values:
        raise ValueError(f"no value for {base}")
    return max(values)


def _csa_gates() -> GateSet:
    def mta_not_transposed_with_threshold(a: dict[str, Any]) -> bool:
        """Catch Threshold and MTA read into each other's fields.

        The naive form of this check — `mta <= threshold` — is WRONG, and
        wrong in the most expensive direction. A 2016 VM CSA elects a
        Threshold of **zero**: variation margin is fully collateralised, so
        there is no uncollateralised band, while the MTA remains a normal
        operational figure. ISDA's own worked example (Benchmarking GenAI for
        CSA Clause Extraction, Appendix Table A) is exactly this shape —
        Threshold zero, MTA 5,000,000 — and an ordering test refers it to a
        human every single time.

        So the ordering only carries information when the Threshold is
        non-zero. At zero it says nothing, and a gate that says nothing must
        not vote.
        """
        try:
            threshold = _csa_amount(a, "threshold")
            mta = _csa_amount(a, "mta")
        except (TypeError, ValueError):
            return False
        if threshold == 0:
            return True  # standard VM election; the comparison is uninformative
        return mta <= threshold

    def rounding_no_coarser_than_mta(a: dict[str, Any]) -> bool:
        """A rounding multiple bigger than the MTA is incoherent.

        Rounding exists to make a transfer operationally clean; if the multiple
        exceeds the smallest transfer you would make, every transfer gets
        rounded past its own minimum. Both numbers are money, both are
        plausible alone, and a schema sees nothing.
        """
        try:
            mta = _csa_amount(a, "mta")
            delivery = _num(_field(a, "rounding_delivery_amount"))
        except (TypeError, ValueError):
            return False
        return delivery <= mta

    def rounding_is_standard_or_documented(a: dict[str, Any]) -> bool:
        """Non-standard rounding must carry its full text, per ISDA Variant 2.

        Standard is Delivery UP, Return DOWN, equal fixed multiples. Anything
        else — different multiples, both directions the same, conditional
        rounding — is Variant 2, and Variant 2 without the complete provision
        text has lost the condition that made it non-standard.
        """
        fields = a.get("fields") or {}
        if classify_rounding(fields) == "VARIANT_1":
            return True
        return bool(_field(a, "rounding_full_text"))

    def valuation_percentages_are_percentages(a: dict[str, Any]) -> bool:
        # A CSA states *valuation percentages*; systems store *haircuts*. A 98%
        # valuation percentage is a 2% haircut. Reading "98" straight into a
        # haircut field prices a $1m bond at $20,000.
        rows = a.get("eligible_collateral") or []
        if not rows:
            return False
        values = [_num(r.get("valuation_pct")) for r in rows]
        return all(50 <= v <= 100 for v in values) and max(values) >= 95

    def representable_in_cdm(a: dict[str, Any]) -> bool:
        """The terms map into CDM without inventing anything.

        The deliverable is not a dictionary, it is CDM JSON a collateral system
        can load. If the mapping has to guess, the extraction is not finished.
        """
        try:
            to_cdm(a)
            return True
        except CdmError:
            return False

    return (
        GateSet()
        # --- ISDA validation protocol -------------------------------------
        .add("currencies_are_iso_codes",
             "Base and eligible currency are valid ISO 4217 codes",
             iso_currency("base_currency", "eligible_currency", "rounding_currency",
                          under="fields"))
        .add("amounts_are_numeric",
             "Monetary elections are numbers, not formatted strings",
             values_are_numeric("threshold", "mta", "rounding_delivery_amount",
                                "rounding_return_amount", under="fields"))
        .add("no_clause_references_in_amounts",
             "No paragraph reference was transcribed into a value field",
             no_cross_reference_values("threshold", "mta", "rounding_delivery_amount",
                                       "rounding_return_amount", under="fields"))
        .add("rounding_directions_valid",
             "Rounding directions are UP or DOWN",
             _rounding_directions_valid())
        # --- domain cross-field checks -------------------------------------
        .add("mta_not_transposed_with_threshold",
             "Minimum Transfer Amount and Threshold are not swapped",
             cross_field("MTA is consistent with a non-zero Threshold",
                         mta_not_transposed_with_threshold,
                         message=("Minimum Transfer Amount exceeds a non-zero Threshold — "
                                  "these two are almost certainly transposed. Do not load.")))
        .add("rounding_no_coarser_than_mta",
             "The rounding multiple is no larger than the Minimum Transfer Amount",
             cross_field("rounding is finer than the MTA", rounding_no_coarser_than_mta,
                         message=("the rounding multiple is larger than the Minimum Transfer "
                                  "Amount, so every transfer rounds past its own minimum")))
        .add("rounding_standard_or_documented",
             "Non-standard rounding carries its complete provision text",
             cross_field("rounding is Variant 1, or Variant 2 with full text",
                         rounding_is_standard_or_documented,
                         message=("this rounding provision is non-standard (ISDA Variant 2) "
                                  "and the full clause text was not captured. The condition "
                                  "you drop is the one that mattered.")))
        .add("valuation_percentages_sane",
             "Eligible collateral carries valuation percentages, not haircuts",
             cross_field("valuation percentages look like percentages",
                         valuation_percentages_are_percentages,
                         message=("These read as haircuts, not valuation percentages. A 98% "
                                  "valuation percentage is a 2% haircut; storing them the "
                                  "wrong way round misprices the whole collateral book.")))
        .add("representable_in_cdm",
             "The elections map into ISDA CDM without invention",
             cross_field("CDM representable", representable_in_cdm,
                         message=("the elections cannot be expressed in CDM without guessing "
                                  "a value. See the CdmError for which one.")))
    )


def _rounding_directions_valid():
    """UP or DOWN, and only when rounding was elected at all."""
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        checked = 0
        for name in ("rounding_delivery_direction", "rounding_return_direction"):
            value = _field(artifact, name)
            if value is None:
                continue
            checked += 1
            if value not in DIRECTIONS:
                return False, f"{name} = {value!r}; CDM allows only UP or DOWN"
        if checked == 0:
            return True, "no rounding elected; nothing to validate"
        return True, f"{checked} rounding direction(s) are UP or DOWN"
    return check


CSA_SYSTEM = """\
You extract the Paragraph 11 (or Paragraph 13) elections from an ISDA Credit
Support Annex and prepare them for Common Domain Model representation.

VOCABULARY. These are different things and are routinely conflated:
  · "Threshold" is the unsecured exposure a party tolerates before collateral
    is called. A 2016 VM CSA usually elects ZERO. Zero is a real value, not a
    missing one.
  · "Minimum Transfer Amount" (MTA) is the smallest transfer that will be made.
    It is unrelated to the Threshold and may exceed it.
  · "Independent Amount" is neither of the above. Do not merge it into either.
  · A "Threshold Amount" in the Master Agreement's cross-default provision is
    NOT the CSA Threshold. Ignore it.

REFERENCES ARE NOT AMOUNTS. Paragraph pointers such as 13(c)(ii), 11(b) or
"Paragraph 5" appear next to money throughout. Never transcribe one into a
value field.

FIELDS.
  base_currency, eligible_currency        ISO 4217 codes, e.g. "USD"
  threshold, mta                          numbers
  rounding_delivery_amount                number
  rounding_delivery_direction             "UP" or "DOWN"
  rounding_return_amount                  number
  rounding_return_direction               "UP" or "DOWN"
  governing_law
  independent_amount                      number, if elected

Where the parties elect DIFFERENT figures, use threshold_party_a /
threshold_party_b (and the same for mta) instead of the shared field. Where the
document says "with respect to each party", use the shared field.

AMOUNTS ARE NUMBERS. Write 500000, never "500,000" or "USD 500,000".

ROUNDING. If the Annex does not mention rounding, omit all four rounding fields
entirely. Do not assume a default — an invented rounding direction is a term
the parties never agreed. If the rounding is non-standard in any way (different
multiples per leg, both legs the same direction, or conditional on anything),
also return rounding_full_text containing the COMPLETE provision, untruncated.

Return the eligible collateral schedule as `eligible_collateral`, each entry with
`description` and `valuation_pct`. The document states VALUATION PERCENTAGES —
transcribe them as printed. Do not convert them to haircuts.

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
Place of birth:         Leeds, United Kingdom
Country of citizenship: United Kingdom
Residential address:    44 Cranmer Road, Leeds LS6 3TX
Country of residence:   United Kingdom

SECTION 2 — IDENTITY DOCUMENT
Document type:          Passport
Document number:        548912337
Name as shown on document:  Jonathan Alexander Whitfield
Date of issue:          12 June 2021
Date of expiry:         12 June 2031
Issuing authority:      HM Passport Office
Image quality:          Colour, both sides supplied, legible

SECTION 3 — PROOF OF ADDRESS
Document type:          Utility bill
Provider:               Northern Grid Energy
Provider logo present:  Yes
Statement date:         02 August 2026
Addressed to:           Jonathan Alexander Whitfield
Address as shown:       44 Cranmer Road, Leeds LS6 3TX
Submitted as:           Original PDF from provider

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

# AWS Marketplace KYC Documents Guide: proof of address, letters of authority,
# statute documents and registration extracts must all be "dated within 180
# days". The library's previous 90 was a guess; this is the published number.
MAX_ADDRESS_PROOF_AGE_DAYS = 180

# "Acceptable Identity Document (ID)". Driving licence and residence permit are
# starred in the guide — accepted only where the document itself shows every
# required data point — so they are accepted here and then made to prove it by
# `identity_document_shows_required_data_points`.
ACCEPTED_ID_TYPES = (
    "passport",
    "national identity card",
    "us passport card",
    "driving license",
    "driving licence",
    "residence permit",
)

# "Acceptable Proof of Address".
ACCEPTED_ADDRESS_PROOF_TYPES = (
    "utility bill",
    "bank statement",
    "credit union statement",
    "building society statement",
    "credit card statement",
    "credit card bill",
    "mortgage statement",
    "rent receipt",
)

# The guide excludes these explicitly: "Documents issued by a financial services
# provider, other than a bank, e.g. third-party providers or online digital
# banks, are not acceptable as a proof of address." A statement from one is a
# perfectly well-formed bank statement that does not count.
NON_BANK_ADDRESS_PROOF_MARKERS = (
    "digital bank", "online-only", "e-money", "emi", "payment institution",
    "third-party provider", "neobank",
)

# The identity document "must contain full name, date of birth, place of birth,
# and country of citizenship".
ID_REQUIRED_DATA_POINTS = (
    "applicant_name", "date_of_birth", "place_of_birth", "country_of_citizenship",
)


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
        """"The document must not be expired." Checked at the application date."""
        try:
            return _date(_field(a, "document_expiry")) > _date(_field(a, "application_date"))
        except (TypeError, ValueError):
            return False

    def identity_document_type_is_accepted(a: dict[str, Any]) -> bool:
        value = str(_field(a, "document_type") or "").lower()
        return any(t in value for t in ACCEPTED_ID_TYPES)

    def identity_document_shows_required_data_points(a: dict[str, Any]) -> bool:
        """Full name, date of birth, place of birth, country of citizenship.

        The guide is explicit that a single document need not carry all four —
        "if a standalone ID document does not contain all the data points,
        please provide two ID documents in combination". What is *not*
        acceptable is proceeding without them, which is what happens when an
        extractor omits a field nobody listed as required.
        """
        return all(_field(a, name) not in (None, "") for name in ID_REQUIRED_DATA_POINTS)

    def name_on_document_matches_application(a: dict[str, Any]) -> bool:
        # The classic KYC finding, and the clearest example of a check a schema
        # cannot make: both fields are present, both are strings, both are
        # plausible names. They just are not the *same* name.
        try:
            return (_normalise_name(_field(a, "applicant_name"))
                    == _normalise_name(_field(a, "name_on_document")))
        except (TypeError, ValueError):
            return False

    def address_proof_type_is_accepted(a: dict[str, Any]) -> bool:
        value = str(_field(a, "address_proof_type") or "").lower()
        if not any(t in value for t in ACCEPTED_ADDRESS_PROOF_TYPES):
            return False
        # A statement from a non-bank financial provider looks exactly like a
        # bank statement and is explicitly not acceptable.
        provider = str(_field(a, "address_proof_provider") or "").lower()
        haystack = f"{value} {provider}"
        return not any(m in haystack for m in NON_BANK_ADDRESS_PROOF_MARKERS)

    def address_proof_names_the_applicant(a: dict[str, Any]) -> bool:
        """"Must be addressed to the corresponding person ... names should match."

        A utility bill in a landlord's, partner's or previous occupant's name
        proves an address exists. It does not tie this applicant to it, which
        is the only thing it was collected to do.
        """
        addressed = _field(a, "address_proof_addressed_to")
        if addressed in (None, ""):
            return False
        try:
            return _normalise_name(addressed) == _normalise_name(_field(a, "applicant_name"))
        except (TypeError, ValueError):
            return False

    def address_proof_is_not_a_screenshot(a: dict[str, Any]) -> bool:
        """"The document must not be a screenshot." A rule worth having in code.

        A screenshot of an online billing page is trivially fabricated and
        carries none of the provenance a issued document does.
        """
        submitted = str(_field(a, "address_proof_format") or "").lower()
        return "screenshot" not in submitted and "screen shot" not in submitted

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
        .add("identity_document_type_accepted",
             "The identity document is an accepted type",
             cross_field("identity document type is accepted",
                         identity_document_type_is_accepted,
                         message=("this is not an accepted identity document. Accepted: "
                                  "passport, national identity card, US passport card, "
                                  "driving licence, residence permit.")))
        .add("identity_data_points_present",
             "Name, date of birth, place of birth and citizenship are all present",
             cross_field("required identity data points present",
                         identity_document_shows_required_data_points,
                         message=("the identity evidence does not establish full name, date "
                                  "of birth, place of birth and country of citizenship. "
                                  "A second document in combination may be required.")))
        .add("name_matches_document",
             "The name on the identity document matches the application",
             cross_field("names match", name_on_document_matches_application,
                         message=("the name on the identity document does not match the name "
                                  "on the application. Refer to compliance; do not proceed.")))
        .add("address_proof_type_accepted",
             "The proof of address is an accepted type from an acceptable issuer",
             cross_field("address proof type is accepted", address_proof_type_is_accepted,
                         message=("this proof of address is not an accepted type, or is "
                                  "issued by a non-bank financial provider — which the "
                                  "policy excludes even though it looks like a statement.")))
        .add("address_proof_names_applicant",
             "The proof of address is addressed to the applicant",
             cross_field("address proof names the applicant",
                         address_proof_names_the_applicant,
                         message=("the proof of address is not addressed to the applicant. "
                                  "It evidences an address, not this applicant's link to it.")))
        .add("address_proof_not_a_screenshot",
             "The proof of address is an issued document, not a screenshot",
             cross_field("address proof is not a screenshot",
                         address_proof_is_not_a_screenshot,
                         message="the proof of address was submitted as a screenshot"))
        .add("address_proof_recent",
             f"Proof of address is no more than {MAX_ADDRESS_PROOF_AGE_DAYS} days old",
             cross_field("address proof is recent", address_proof_is_recent,
                         message=(f"proof of address is older than "
                                  f"{MAX_ADDRESS_PROOF_AGE_DAYS} days or post-dates the "
                                  f"application")))
    )


KYC_SYSTEM = """\
You extract an individual customer onboarding pack for compliance review.

Return these fields:
  application_reference, application_date
  applicant_name, date_of_birth, place_of_birth, country_of_citizenship
  document_type, name_on_document, document_expiry
  address_proof_type, address_proof_provider, address_proof_addressed_to,
  address_proof_date, address_proof_format
  pep_status

TRANSCRIBE, DO NOT RECONCILE. If the name on the identity document differs from
the name on the application — even by one letter — record BOTH exactly as
printed. That difference is the finding. Correcting it silently destroys the
only signal compliance has.

The same applies to every other field: an expired document, an under-age date of
birth, a proof of address in someone else's name. Report what is there.

address_proof_format is how the document was supplied (for example "Original PDF
from provider" or "Screenshot"). Record it verbatim.

For every field give the exact words from the document that establish it. Copy
each quote character for character; quotes are checked against the source and an
invented one fails the whole extraction. If a field is genuinely absent, omit it
rather than writing a placeholder.

Respond with JSON only:
{"fields": {"<name>": {"value": <value>, "evidence_quote": "<exact words>"}}}"""


DOCUMENTS: dict[str, DocumentSpec] = {
    "csa": DocumentSpec(
        key="csa",
        title="ISDA Credit Support Annex",
        document_id="DOC-CSA-ATLAS-2019",
        text=CSA_TEXT,
        # ISDA's five benchmarked clauses: base currency, eligible currency,
        # MTA, threshold, rounding. Rounding is four fields, not one — a single
        # "rounding: 100000" cannot say which way each leg goes.
        required=("base_currency", "eligible_currency", "threshold", "mta",
                  "rounding_delivery_amount", "rounding_delivery_direction",
                  "rounding_return_amount", "rounding_return_direction",
                  "governing_law"),
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
                  "date_of_birth", "place_of_birth", "country_of_citizenship",
                  "document_type", "name_on_document", "document_expiry",
                  "address_proof_type", "address_proof_addressed_to",
                  "address_proof_date", "address_proof_format", "pep_status"),
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
