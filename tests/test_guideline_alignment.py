"""Conformance with the two published guidelines the intake example targets.

  · ISDA, *Benchmarking Generative AI for CSA Clause Extraction and CDM
    Representation*, May 2025 — five benchmarked clauses, CDM JSON output,
    and an explicit validation protocol.
  · AWS Marketplace, *Know Your Customer (KYC) Documentation Upload Best
    Practices* — accepted document types, required data points, and the
    180-day recency rule.

These are conformance tests, not unit tests: each one names the rule it holds
the code to, so that when a guideline is revised the failing test says which
paragraph moved.
"""

from __future__ import annotations

import pytest
from document_intake.cdm import CdmError, classify_rounding, to_cdm
from document_intake.documents import (
    ACCEPTED_ADDRESS_PROOF_TYPES,
    ACCEPTED_ID_TYPES,
    MAX_ADDRESS_PROOF_AGE_DAYS,
    _csa_gates,
    _kyc_gates,
)

from pyligent_agents.verify import iso_currency, no_cross_reference_values, values_are_numeric


def _csa(**fields):
    base = {
        "base_currency": {"value": "USD"},
        "eligible_currency": {"value": "USD"},
        "rounding_delivery_amount": {"value": 100_000},
        "rounding_delivery_direction": {"value": "UP"},
        "rounding_return_amount": {"value": 100_000},
        "rounding_return_direction": {"value": "DOWN"},
    }
    base.update({k: {"value": v} for k, v in fields.items()})
    return {"fields": base, "eligible_collateral": [{"valuation_pct": 100}]}


def _failing(artifact, gates):
    return [r.name for r in gates.evaluate(artifact).results if not r.passed]


# --- the regression this whole file exists to guard ----------------------


def test_a_zero_threshold_vm_csa_is_accepted():
    """ISDA Appendix Table A: Threshold *zero*, MTA 5,000,000.

    A 2016 VM CSA collateralises variation margin in full, so the Threshold is
    zero while the MTA stays at a normal operational figure. MTA therefore
    exceeds Threshold in the most common CSA shape in the market.

    The gate here was once `mta <= threshold`. That referred ISDA's own
    published example — and every standard VM CSA — to a human. If this test
    fails, that bug is back.
    """
    assert _failing(_csa(threshold=0, mta=5_000_000), _csa_gates()) == []


@pytest.mark.parametrize("threshold,mta", [(0, 500_000), (0, 5_000_000),
                                           (50_000_000, 500_000), (5_000_000, 500_000)])
def test_legitimate_threshold_and_mta_combinations_pass(threshold, mta):
    assert _failing(_csa(threshold=threshold, mta=mta), _csa_gates()) == []


def test_a_transposition_is_still_caught_when_the_threshold_is_non_zero():
    """The ordering only carries information above zero — but there it still does."""
    failed = _failing(_csa(threshold=500_000, mta=5_000_000), _csa_gates())
    assert "mta_not_transposed_with_threshold" in failed


# --- ISDA validation protocol (section 2.2, appendix) --------------------


def test_currency_codes_must_be_iso_4217():
    """"Ensure currency codes are valid ISO codes." """
    ok, _ = iso_currency("base_currency", under="fields")(_csa(threshold=0, mta=1))
    assert ok
    bad, msg = iso_currency("base_currency", under="fields")(
        {"fields": {"base_currency": {"value": "US Dollars"}}})
    assert not bad and "ISO 4217" in msg


def test_amounts_must_be_numbers_not_strings():
    """"Ensure rounding amounts are represented as numbers, not strings." """
    ok, _ = values_are_numeric("mta", under="fields")({"fields": {"mta": {"value": 500_000}}})
    assert ok
    bad, _ = values_are_numeric("mta", under="fields")({"fields": {"mta": {"value": "500,000"}}})
    assert not bad


@pytest.mark.parametrize("reference", ["13(c)(ii)", "Paragraph 11(b)", "11(b)", "Section 4.2"])
def test_a_clause_reference_is_not_a_quantity(reference):
    """The paper's transcription risk: a CSA is dense with both, side by side."""
    bad, msg = no_cross_reference_values("threshold", under="fields")(
        {"fields": {"threshold": {"value": reference}}})
    assert not bad and "cross-reference" in msg


@pytest.mark.parametrize("direction", ["NEAREST", "up", "", "BOTH"])
def test_rounding_direction_must_be_up_or_down(direction):
    failed = _failing(
        _csa(threshold=0, mta=500_000, rounding_delivery_direction=direction), _csa_gates())
    assert "rounding_directions_valid" in failed


# --- CDM representation (appendix Table A) -------------------------------


def test_cdm_matches_the_published_structure():
    cdm = to_cdm(_csa(threshold=0, mta=5_000_000))
    elections = cdm["agreementTerms"]["agreement"]["creditSupportAgreementElections"]

    assert elections["baseAndEligibleCurrency"] == {
        "baseCurrency": "USD",
        "eligibleCurrency": ["USD"],
        "eligibleCurrencyInclBaseCurrency": True,
    }
    mta = elections["minimumTransferAmount"]
    assert [m["mtaType"]["fixedAmount"]["party"] for m in mta] == ["PARTY_1", "PARTY_2"]
    assert mta[0]["mtaType"]["fixedAmount"] == {
        "amount": 5_000_000.0, "currency": "USD", "party": "PARTY_1"}
    assert elections["threshold"][0]["thresholdType"]["fixedAmount"]["amount"] == 0.0
    assert elections["creditSupportObligations"]["rounding"] == {
        "currency": "USD", "deliveryAmount": 100_000.0, "deliveryDirection": "UP",
        "returnAmount": 100_000.0, "returnDirection": "DOWN"}


def test_asymmetric_per_party_elections_are_represented_separately():
    """Different figures per party is a normal commercial outcome, not an error."""
    cdm = to_cdm({"fields": {
        "base_currency": {"value": "USD"},
        "threshold_party_a": {"value": 0},
        "threshold_party_b": {"value": 10_000_000},
    }})
    thresholds = cdm["agreementTerms"]["agreement"][
        "creditSupportAgreementElections"]["threshold"]
    assert [t["thresholdType"]["fixedAmount"]["amount"] for t in thresholds] == [0.0, 10_000_000.0]


def test_an_unmentioned_rounding_clause_produces_no_rounding_object():
    """"If rounding is not mentioned at all, do not assume any default rounding
    and do not generate JSON output."

    A defaulted `deliveryDirection: UP` is a contractual term the parties never
    agreed, and it is invisible downstream because it is perfectly well-formed.
    """
    cdm = to_cdm({"fields": {"base_currency": {"value": "USD"}}})
    elections = cdm["agreementTerms"]["agreement"]["creditSupportAgreementElections"]
    assert "creditSupportObligations" not in elections


def test_partial_rounding_refuses_rather_than_guessing():
    with pytest.raises(CdmError, match="deliveryDirection"):
        to_cdm({"fields": {"base_currency": {"value": "USD"},
                           "rounding_delivery_amount": {"value": 100_000},
                           "rounding_return_amount": {"value": 100_000},
                           "rounding_return_direction": {"value": "DOWN"}}})


@pytest.mark.parametrize("overrides,variant", [
    ({}, "VARIANT_1"),
    ({"rounding_return_direction": "UP"}, "VARIANT_2"),          # both legs up
    ({"rounding_return_amount": 50_000}, "VARIANT_2"),           # different multiples
    ({"rounding_conditions": "unless Exposure is zero"}, "VARIANT_2"),
])
def test_rounding_variant_classification(overrides, variant):
    """"Only use Variant 1 when rounding is completely standard and unconditional." """
    fields = _csa(threshold=0, mta=500_000, **overrides)["fields"]
    assert classify_rounding(fields) == variant


def test_variant_2_rounding_must_carry_its_full_text():
    """"For Variant 2, include the complete rounding provision text ... Do not
    truncate or summarize." The condition you drop is the one that mattered."""
    failed = _failing(
        _csa(threshold=0, mta=500_000, rounding_conditions="unless Exposure is zero"),
        _csa_gates())
    assert "rounding_standard_or_documented" in failed


# --- AWS Marketplace KYC guide -------------------------------------------


def _kyc(**overrides):
    base = {
        "application_date": "21 August 2026", "applicant_name": "Jonathan Whitfield",
        "date_of_birth": "03 February 1990", "place_of_birth": "Leeds, United Kingdom",
        "country_of_citizenship": "United Kingdom", "document_type": "Passport",
        "name_on_document": "Jonathan Whitfield", "document_expiry": "12 June 2031",
        "address_proof_type": "Utility bill", "address_proof_provider": "Northern Grid Energy",
        "address_proof_addressed_to": "Jonathan Whitfield",
        "address_proof_date": "02 August 2026",
        "address_proof_format": "Original PDF from provider",
    }
    base.update(overrides)
    return {"fields": {k: {"value": v} for k, v in base.items()}}


def test_the_recency_window_is_the_published_180_days():
    """The guide says "dated within 180 days" for every dated document class."""
    assert MAX_ADDRESS_PROOF_AGE_DAYS == 180
    assert _failing(_kyc(address_proof_date="01 April 2026"), _kyc_gates()) == []   # 142 days
    assert "address_proof_recent" in _failing(
        _kyc(address_proof_date="01 January 2026"), _kyc_gates())                   # 232 days


@pytest.mark.parametrize("id_type", ACCEPTED_ID_TYPES)
def test_every_listed_identity_document_is_accepted(id_type):
    assert "identity_document_type_accepted" not in _failing(_kyc(document_type=id_type),
                                                             _kyc_gates())


@pytest.mark.parametrize("id_type", ["Student card", "Library card", "Birth certificate"])
def test_an_unlisted_identity_document_is_refused(id_type):
    assert "identity_document_type_accepted" in _failing(_kyc(document_type=id_type),
                                                         _kyc_gates())


@pytest.mark.parametrize("proof_type", ACCEPTED_ADDRESS_PROOF_TYPES)
def test_every_listed_address_proof_is_accepted(proof_type):
    assert "address_proof_type_accepted" not in _failing(_kyc(address_proof_type=proof_type),
                                                         _kyc_gates())


def test_a_statement_from_a_non_bank_provider_is_refused():
    """"Documents issued by a financial services provider, other than a bank,
    e.g. third-party providers or online digital banks, are not acceptable."

    It is a perfectly well-formed bank statement. It just does not count, and
    nothing about its shape says so.
    """
    assert "address_proof_type_accepted" in _failing(
        _kyc(address_proof_type="Bank statement",
             address_proof_provider="Kestrel, a digital bank"), _kyc_gates())


def test_address_proof_must_be_addressed_to_the_applicant():
    """A bill in a partner's name evidences an address, not this applicant."""
    assert "address_proof_names_applicant" in _failing(
        _kyc(address_proof_addressed_to="Michael J Raghavan"), _kyc_gates())


def test_a_screenshot_is_not_a_document():
    assert "address_proof_not_a_screenshot" in _failing(
        _kyc(address_proof_format="Screenshot of online account page"), _kyc_gates())


@pytest.mark.parametrize("missing", ["place_of_birth", "country_of_citizenship"])
def test_the_guides_required_identity_data_points_are_enforced(missing):
    """"The document must contain full name, date of birth, place of birth, and
    country of citizenship." Two of those were not previously collected."""
    assert "identity_data_points_present" in _failing(_kyc(**{missing: ""}), _kyc_gates())
