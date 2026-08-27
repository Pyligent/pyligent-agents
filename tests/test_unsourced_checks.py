"""SPEC.md §3 — the three checks.

The corpus here is the executable form of the contract. Each test names the
section it holds the implementation to.
"""

from __future__ import annotations

import pytest

from unsourced import check, check_field

SOURCE = """\
INVOICE NW-2026-04417
Invoice date:     14 August 2026
Net total            824.99
TOTAL DUE            GBP 989.99

CUSTOMER FILE
Full legal name:            Jonathan Alexander Whitfield
Name as shown on document:  Jonathon Alexander Whitfield

CREDIT SUPPORT ANNEX
"Base Currency" means United States Dollars (USD).
"Eligible Currency" means the Base Currency.
"Threshold" means with respect to each party: USD 0.
The Secured Party shall deliver to the Pledgor on demand.
"""


def one(value, quote, source=SOURCE):
    return check_field("f", value, quote, source)


# --- §3.1 nothing was offered --------------------------------------------


@pytest.mark.parametrize("value", ["TBD", "N/A", "unknown", "  none  ", "-", "TODO"])
def test_a_placeholder_is_critical_not_a_gap(value):
    """It passes a schema and fails a person. That is worse than a blank."""
    f = one(value, "Net total            824.99")
    assert f.code == "PLACEHOLDER_VALUE" and f.severity == "critical"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_empty_value_is_a_warning(value):
    assert one(value, "Net total            824.99").code == "EMPTY_VALUE"


def test_a_value_with_no_citation_is_reported_once_the_pipeline_cites_anything():
    assert one(824.99, "").code == "MISSING_EVIDENCE"


def test_zero_is_a_value_not_an_absence():
    """A VM CSA elects a Threshold of zero. Treating it as empty is the bug
    that referred every standard agreement in an earlier system."""
    assert one(0, '"Threshold" means with respect to each party: USD 0.') is None


def test_a_pipeline_that_cites_nothing_is_told_once_not_per_field():
    """§3.1 report-level exception. Otherwise the report is unreadable noise."""
    r = check(SOURCE, {f"field_{i}": {"value": i, "quote": ""} for i in range(6)})
    assert not [f for f in r.findings if f.code == "MISSING_EVIDENCE"]
    assert len(r.notes) == 1 and "no quotes" in r.notes[0].lower().replace("emits ", "")


def test_a_placeholder_is_still_reported_when_no_quotes_exist_at_all():
    r = check(SOURCE, {"a": {"value": 1, "quote": ""}, "b": {"value": "TBD", "quote": ""}})
    assert [f.code for f in r.findings] == ["PLACEHOLDER_VALUE"]


# --- §3.2 fabricated evidence --------------------------------------------


def test_an_invented_quote_is_caught_however_correct_the_value():
    f = one(824.99, "The net total for this invoice is eight hundred and twenty four pounds")
    assert f.code == "FABRICATED_EVIDENCE" and f.severity == "critical"


def test_a_genuine_quote_passes_even_when_wrapped_differently():
    assert one("Jonathon Alexander Whitfield",
               "Name as shown on document:   Jonathon Alexander Whitfield") is None


# --- §3.3 silent repair ---------------------------------------------------


def test_a_recomputed_total_is_caught():
    """The invoice failure: the model quotes the right line and writes the
    figure that makes the arithmetic work."""
    f = one(989.99, "Net total            824.99")
    assert f.code == "SILENT_REPAIR"
    assert f.competing == ("824.99",)
    assert "824.99" in f.message


def test_a_normalised_name_is_caught():
    """The KYC failure, and the hardest one: the surname matches, the given
    name does not, and a whole-string comparison would excuse it."""
    f = one("Jonathan Alexander Whitfield",
            "Name as shown on document:  Jonathon Alexander Whitfield")
    assert f.code == "SILENT_REPAIR" and "jonathon" in f.competing


def test_a_changed_date_is_caught():
    f = one("15 August 2026", "Invoice date:     14 August 2026")
    assert f.code == "SILENT_REPAIR"


def test_formatting_is_not_a_repair():
    """989.99 cited as 'GBP 989.99' is the same number written differently."""
    assert one(989.99, "TOTAL DUE            GBP 989.99") is None
    assert one("989.99", "TOTAL DUE            GBP 989.99") is None


# --- §3.3 the legitimate-inference rule ----------------------------------


def test_a_derived_value_the_quote_does_not_state_is_not_a_finding():
    """Measured, not hypothetical: without this rule the check fires on a
    perfect extractor. The quote names no currency, so nothing is contradicted."""
    assert one("USD", '"Eligible Currency" means the Base Currency.') is None


def test_capitalised_legal_terms_are_not_competing_names():
    """Contracts are full of defined terms. `Secured Party` does not contradict
    a counterparty name; it is a different subject, not a repair."""
    assert one("Atlas Global Markets",
               "The Secured Party shall deliver to the Pledgor on demand.") is None


def test_a_quote_with_no_number_cannot_contradict_a_number():
    assert one(500_000, '"Eligible Currency" means the Base Currency.') is None


# --- exclusivity ----------------------------------------------------------


def test_a_field_yields_at_most_one_finding():
    """§3 — mutually exclusive by construction. A field reported three ways is
    a field nobody triages, and overlapping checks make counts meaningless."""
    r = check(SOURCE, {
        "fabricated_and_different": {"value": 1234, "quote": "a sentence not in the document"},
    })
    assert len(r.findings) == 1 and r.findings[0].code == "FABRICATED_EVIDENCE"


def test_the_four_failure_modes_are_separated():
    r = check(SOURCE, {
        "repaired":    {"value": 989.99, "quote": "Net total            824.99"},
        "fabricated":  {"value": 1.0, "quote": "words that are not in the document"},
        "placeholder": {"value": "TBD", "quote": "Net total            824.99"},
        "clean":       {"value": 824.99, "quote": "Net total            824.99"},
    })
    assert {f.field: f.code for f in r.findings} == {
        "repaired": "SILENT_REPAIR",
        "fabricated": "FABRICATED_EVIDENCE",
        "placeholder": "PLACEHOLDER_VALUE",
    }
    assert r.fields_checked == 4 and len(r.critical) == 3 and not r.ok


def test_a_clean_extraction_produces_nothing():
    """The most important test in the file. A tool that cries wolf on correct
    work is switched off within a week."""
    q_date = "Invoice date:     14 August 2026"
    q_name = "Name as shown on document:  Jonathon Alexander Whitfield"
    q_ccy = '"Eligible Currency" means the Base Currency.'
    q_thr = '"Threshold" means with respect to each party: USD 0.'
    r = check(SOURCE, {
        "net_total": {"value": 824.99, "quote": "Net total            824.99"},
        "gross_total": {"value": 989.99, "quote": "TOTAL DUE            GBP 989.99"},
        "invoice_date": {"value": "14 August 2026", "quote": q_date},
        "name_on_document": {"value": "Jonathon Alexander Whitfield", "quote": q_name},
        "eligible_currency": {"value": "USD", "quote": q_ccy},
        "threshold": {"value": 0, "quote": q_thr},
    })
    assert r.findings == () and r.ok
