"""Comparison rules. This module is where the tool is right or wrong.

Every function here implements a numbered rule in SPEC.md §4. The rules are
fussy because the failures are: a trailing full stop after `5,000,000.` will
report a discrepancy against `5000000` on a *perfect* extraction, and a tool
that cries wolf on correct work gets switched off within a week.

Nothing here normalises wording. Whitespace and number formatting are
presentation; a paraphrase is a different claim.
"""

from __future__ import annotations

import re
from datetime import date, datetime

# --- §4.1 text ------------------------------------------------------------

_WS = re.compile(r"\s+")


def squash(text: object) -> str:
    """Collapse whitespace. Documents wrap wherever the layout felt like it."""
    return _WS.sub(" ", str(text)).strip()


def contains(haystack: str, needle: str) -> bool:
    """Case-insensitive, whitespace-normalised substring. Never fuzzy."""
    if not needle:
        return False
    return squash(needle).casefold() in squash(haystack).casefold()


# --- §4.2 numbers ---------------------------------------------------------

_CURRENCY = re.compile(r"[£$€¥₹]|\b(?:USD|GBP|EUR|JPY|CHF|CAD|AUD|CNY|HKD|SGD|INR)\b",
                       re.IGNORECASE)
_ACCOUNTING = re.compile(r"^\((.*)\)$")
# A numeric run: digits, with , . ' or spaces between digit groups.
_NUM_TOKEN = re.compile(r"-?\d(?:[\d,. ' ]*\d)?%?")


def parse_number(token: object) -> float | None:
    """Parse a human-written number, or return None.

    Implements SPEC.md §4.2 step by step. The separator resolution is the part
    that matters: `1,234` is one thousand two hundred and thirty-four, `1,23`
    is not a number anyone writes, and `1.234,56` is European.
    """
    if isinstance(token, bool):
        return None
    if isinstance(token, (int, float)):
        return float(token)

    t = squash(token)
    if not t:
        return None

    negative = False
    m = _ACCOUNTING.match(t)          # (1,234) is minus one thousand two hundred…
    if m:
        negative, t = True, m.group(1)

    t = _CURRENCY.sub("", t).strip()
    t = t.replace(" ", "").replace("'", "").replace(" ", "")

    if t.startswith("-"):
        negative, t = True, t[1:]
    if t.endswith("%"):
        t = t[:-1]

    # Sentence punctuation only — a separator is decimal when digits follow it.
    while t and t[-1] in ".,;:":
        t = t[:-1]
    if not t or not any(c.isdigit() for c in t):
        return None
    if not re.fullmatch(r"[\d.,]+", t):
        return None

    has_comma, has_dot = "," in t, "." in t
    if has_comma and has_dot:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        thou = "." if dec == "," else ","
        t = t.replace(thou, "").replace(dec, ".")
    elif has_comma:
        parts = t.split(",")
        # 1,50 is a decimal comma; 1,234,567 is thousands. Two trailing digits
        # is the only reading under which a comma is a decimal mark.
        decimal_comma = len(parts) == 2 and len(parts[1]) == 2
        t = t.replace(",", "." if decimal_comma else "")
    elif has_dot:
        parts = t.split(".")
        if len(parts) > 2:
            t = t.replace(".", "")         # 1.234.567 — European thousands
    try:
        value = float(t)
    except ValueError:
        return None
    return -value if negative else value


def numbers_in(text: str) -> list[float]:
    """Every number the text states, in order."""
    out = []
    for token in _NUM_TOKEN.findall(squash(text)):
        n = parse_number(token)
        if n is not None:
            out.append(n)
    return out


# --- §4.3 dates -----------------------------------------------------------

_UNAMBIGUOUS = ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y",
                "%d %B, %Y", "%Y/%m/%d")
_AMBIGUOUS = ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y")
_DATE_TOKEN = re.compile(
    r"\b(?:\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{1,2}\s+[A-Za-z]{3,9},?\s+\d{4}"
    r"|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})\b")


def parse_dates(token: object) -> set[date]:
    """All readings of a date token.

    `03/04/2026` is two dates depending on which side of the Atlantic wrote it,
    and both are returned. Reporting a discrepancy on separator order would
    make the tool untrustworthy exactly where dates matter.
    """
    t = squash(token).rstrip(".,;:")
    if not t:
        return set()
    found = set()
    for fmt in _UNAMBIGUOUS:
        try:
            return {datetime.strptime(t, fmt).date()}
        except ValueError:
            continue
    for fmt in _AMBIGUOUS:
        try:
            found.add(datetime.strptime(t, fmt).date())
        except ValueError:
            continue
    return found


def dates_in(text: str) -> list[set[date]]:
    return [d for tok in _DATE_TOKEN.findall(squash(text)) if (d := parse_dates(tok))]


# --- §4.4 proper nouns ----------------------------------------------------

_PROPER = re.compile(r"\b[A-Z][a-z][a-zA-Z'’-]*\b")


def proper_nouns(text: str) -> set[str]:
    """Capitalised words with lowercase bodies. `USD` is a code, not a name."""
    return {t.casefold() for t in _PROPER.findall(squash(text))}


def near_miss(a: str, b: str, *, threshold: float = 0.72) -> bool:
    """Similar but not identical — the shape a silent repair leaves behind.

    `Jonathon` against `Jonathan` is a repair. `Whitfield` against `Party` is a
    different subject, and reporting it would be noise. Unrelated capitalised
    words are everywhere in legal text ("the Base Currency", "the Secured
    Party"), so proximity is what separates a finding from a defined term.
    """
    from difflib import SequenceMatcher

    a, b = a.casefold(), b.casefold()
    if a == b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= threshold


# A name is a few tokens. Beyond that it is prose, and prose must not go down
# the name-comparison path: a long summary shares most of its words with the
# text it summarises, so a single plural — "Ratings" against "Rating" — reads as
# a competing value and a correct extraction is reported as a repair. Measured
# on real SEC filings, where it was the only finding and it was wrong.
MAX_NAME_TOKENS = 6


def looks_like_a_name(value: object) -> bool:
    t = squash(value)
    if not t or not _PROPER.match(t):
        return False
    return len(t.split()) <= MAX_NAME_TOKENS
