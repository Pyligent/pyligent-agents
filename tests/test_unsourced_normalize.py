"""SPEC.md §4 — comparison rules.

This is where the tool is right or wrong. Every case here is one that either
broke an earlier implementation or would have.
"""

from __future__ import annotations

from datetime import date

import pytest

from unsourced.normalize import (
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
