"""SPEC.md §4 — comparison rules.

This is where the tool is right or wrong. Every case here is one that either
broke an earlier implementation or would have.
"""

from __future__ import annotations

from datetime import date

import pytest

from evidencecheck.normalize import (
    contains,
    dates_in,
    looks_like_a_name,
    near_miss,
    numbers_in,
    parse_dates,
    parse_number,
    proper_nouns,
    squash,
)

# --- §4.1 text ------------------------------------------------------------


def test_a_quote_spanning_a_line_break_is_still_the_quote():
    """Documents wrap wherever the layout felt like it."""
    source = "The Delivery Amount will be rounded up and the Return\n   Amount rounded down."
    assert contains(source, "rounded up and the Return Amount rounded down")


def test_comparison_ignores_case_but_never_wording():
    assert contains("The THRESHOLD is zero", "the threshold is zero")
    # A paraphrase is not a citation. No stemming, no synonyms, no fuzz.
    assert not contains("The Threshold is zero", "The Threshold equals nil")


def test_squash_is_idempotent():
    assert squash(squash("  a   b \n c ")) == "a b c"


# --- §4.2 numbers ---------------------------------------------------------


@pytest.mark.parametrize("token,expected", [
    ("5,000,000.", 5_000_000.0),   # trailing sentence stop — broke two earlier attempts
    ("5000000", 5_000_000.0),
    ("82.50", 82.5),
    ("USD 500,000", 500_000.0),
    ("£989.99", 989.99),
    ("€1 234,56", 1234.56),        # European, space-separated thousands
    ("1.234,56", 1234.56),         # European decimal comma
    ("1.234.567", 1_234_567.0),    # European thousands, repeated dot
    ("1,50", 1.5),                 # decimal comma, two trailing digits
    ("1,500", 1500.0),             # thousands comma, three trailing digits
    ("(1,234)", -1234.0),          # accounting negative
    ("-500", -500.0),
    ("20%", 20.0),
    ("0", 0.0),
])
def test_numbers_people_actually_write(token, expected):
    assert parse_number(token) == pytest.approx(expected)


@pytest.mark.parametrize("token", [
    "13(c)(ii)",        # a clause reference, not a quantity
    "Paragraph 11",
    "not a number",
    "",
    "USD",
    None,
    True,               # a bool is not a number here
])
def test_things_that_are_not_numbers(token):
    assert parse_number(token) is None


def test_zero_is_a_number_not_an_absence():
    """A VM CSA elects a Threshold of zero. Treating that as missing is a bug."""
    assert parse_number(0) == 0.0
    assert parse_number("USD 0.") == 0.0
    assert 0.0 in numbers_in('"Threshold" means with respect to each party: USD 0.')


def test_every_number_in_a_sentence_is_found_in_order():
    assert numbers_in("Net total 824.99 of USD 5,000,000.") == [824.99, 5_000_000.0]


def test_no_tolerance_window():
    """A tolerance is a way to miss the transposed digit this tool exists for."""
    assert parse_number("82.50") != parse_number("85.20")


# --- §4.3 dates -----------------------------------------------------------


@pytest.mark.parametrize("token,expected", [
    ("2026-08-14", date(2026, 8, 14)),
    ("14 August 2026", date(2026, 8, 14)),
    ("August 14, 2026", date(2026, 8, 14)),
    ("14 Aug 2026", date(2026, 8, 14)),
])
def test_unambiguous_dates_parse_to_one_reading(token, expected):
    assert parse_dates(token) == {expected}


def test_slash_dates_keep_both_readings():
    """DD/MM against MM/DD is genuinely ambiguous.

    Reporting a discrepancy on separator convention would make the tool
    untrustworthy in exactly the documents where dates matter.
    """
    assert parse_dates("03/04/2026") == {date(2026, 3, 4), date(2026, 4, 3)}


def test_a_trailing_stop_does_not_break_a_date():
    assert parse_dates("14 August 2026.") == {date(2026, 8, 14)}


def test_dates_are_found_inside_prose():
    found = dates_in("Invoice date: 14 August 2026 · Payment due: 13 September 2026")
    assert {date(2026, 8, 14)} in found and {date(2026, 9, 13)} in found


# --- §4.4 proper nouns ----------------------------------------------------


def test_an_all_caps_code_is_not_a_name():
    """`USD` is a currency code. Comparing it against names invents findings."""
    assert not looks_like_a_name("USD")
    assert not looks_like_a_name("GBP")
    # "Eligible" and "Currency" are capitalised defined terms; "USD" is neither.
    assert proper_nouns("Eligible Currency means USD") == {"eligible", "currency"}


def test_proper_nouns_require_a_lowercase_body():
    assert proper_nouns("Jonathon Whitfield paid USD 5") == {"jonathon", "whitfield"}


@pytest.mark.parametrize("a,b,expected", [
    ("Jonathan", "Jonathon", True),     # the classic KYC finding
    ("Whitfield", "Whitfield", False),  # identical is not a near miss
    ("Whitfield", "Pledgor", False),    # unrelated: a different subject
    ("Atlas", "Party", False),
    ("Smith", "Smyth", True),
])
def test_near_miss_separates_a_repair_from_a_different_subject(a, b, expected):
    assert near_miss(a, b) is expected


# --- §4.4, the bound that keeps prose off the name path ------------------


@pytest.mark.parametrize("value,is_name", [
    ("Jonathan Alexander Whitfield", True),
    ("ATLAS GLOBAL MARKETS LTD", False),        # all caps: a code-ish entity, not a name token
    ("Northwind Bank Plc", True),
    ("The Valuation Percentage equals the percentage specified under the "
     "applicable Rating Agency's name in the table", False),
])
def test_only_short_values_take_the_name_comparison_path(value, is_name):
    """A paragraph is not a name.

    Found on real SEC filings: a prose summary of a valuation-percentage rule
    was reported as a silent repair because "Ratings" in the summary is a
    near-miss for "Rating" in the clause it summarised. A long value shares most
    of its words with its own source, so one plural is enough to make a correct
    extraction look wrong.
    """
    assert looks_like_a_name(value) is is_name


def test_large_amounts_are_not_reported_in_scientific_notation():
    """`f"{n:g}"` reported a USD 10,000,000 threshold as `1e+07`.

    This text is the product's headline sentence — "the cited text states X, not
    Y" — and CSA thresholds are routinely millions, so the defect was in the most
    visible string the tool produces.
    """
    from evidencecheck.normalize import format_number

    assert format_number(10_000_000.0) == "10,000,000"
    assert format_number(1_000_000.0) == "1,000,000"
    assert format_number(50_000.0) == "50,000"
    assert "e+" not in format_number(9.99e14)


def test_formatting_does_not_lose_precision():
    """`:g` keeps six significant figures, so 1,234,567.89 became 1.23457e+06.

    That is a different number, off by more than ten thousand. A tool that reports
    the document states one figure when it states another has inverted its own job.
    """
    from evidencecheck.normalize import format_number, parse_number

    for original in ("1,234,567.89", "10,000,000", "999,999.5", "0.5"):
        parsed = parse_number(original)
        assert parsed is not None
        assert parse_number(format_number(parsed)) == parsed, original


def test_the_tool_reports_one_version():
    """Two version strings from one distribution is a support conversation."""
    import evidencecheck

    # Assert the invariant, not a literal: one distribution reports one version.
    # Pinning the number here means every release fails a test that was never
    # about the number.
    import pyligent_agents
    from evidencecheck.report import Report

    assert evidencecheck.__version__ == pyligent_agents.__version__
    assert evidencecheck.__version__ in Report.__dataclass_fields__["tool"].default
