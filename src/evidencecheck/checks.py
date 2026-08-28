"""The three checks. SPEC.md §3.

They are mutually exclusive by construction: evaluated in order, first match
wins, at most one finding per field. That is not a tidiness preference — a
field reported three ways is a field nobody triages, and overlapping checks
make the counts in a benchmark meaningless.

    UNSUPPORTED_FIELD    nothing was offered to check
    FABRICATED_EVIDENCE  the citation is not in the document
    SILENT_REPAIR        the citation is real and names a different value
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .normalize import (
    contains,
    dates_in,
    looks_like_a_name,
    near_miss,
    numbers_in,
    parse_dates,
    parse_number,
    proper_nouns,
    squash,
    strip_references,
    trimmed_quote,
)
from .report import Finding, Report, sha256

PLACEHOLDERS = ("todo", "tbd", "n/a", "na", "unknown", "none", "null", "-", "--", "?")


def _placeholder(value: Any) -> bool:
    return isinstance(value, str) and squash(value).casefold() in PLACEHOLDERS


def _empty(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and not squash(value)


def _competing_numbers(value: Any, quote: str) -> list[str]:
    """Numbers the quote states that are not the extracted one.

    Clause pointers are excluded. A CSA that says an amount is "specified as
    such for that party in Paragraph 13" is not stating thirteen of anything,
    and reporting it as a competing figure is the tool inventing a discrepancy
    in a correct extraction.
    """
    target = parse_number(value)
    if target is None:
        return []
    found = numbers_in(strip_references(quote))
    if not found:
        return []                       # §3.3 no number cited: inference, not repair
    if any(abs(n - target) < 1e-9 for n in found):
        return []
    return [f"{n:g}" for n in found]


def _competing_dates(value: Any, quote: str) -> list[str]:
    readings = parse_dates(value)
    if not readings:
        return []
    cited = dates_in(quote)
    if not cited:
        return []
    if any(readings & group for group in cited):
        return []
    return [sorted(g)[0].isoformat() for g in cited]


def _competing_names(value: Any, quote: str) -> list[str]:
    """A cited name that is a near-miss for the extracted one.

    Token by token, because a repair usually touches one part of a name and
    leaves the rest: `Jonathan Whitfield` against a document that reads
    `Jonathon Whitfield` matches on the surname and differs on the given name,
    and the surname match must not excuse the difference.
    """
    if not looks_like_a_name(value):
        return []                       # "USD" is a code; there is no name to compare
    cited = proper_nouns(quote)
    if not cited:
        return []
    competing = []
    for token in proper_nouns(str(value)):
        if token in cited:
            continue                    # this part of the name is confirmed
        competing += [c for c in sorted(cited) if near_miss(token, c)]
    return sorted(set(competing))


def check_field(name: str, value: Any, quote: str, source: str) -> Finding | None:
    """One field, at most one finding. SPEC.md §3, in order."""
    # §3.1 — nothing usable was offered.
    if _empty(value):
        return Finding("EMPTY_VALUE", name, "No value was extracted.", value, quote)
    if _placeholder(value):
        return Finding("PLACEHOLDER_VALUE", name,
                       f"{value!r} is a placeholder, not a value. It will pass a "
                       f"schema and fail a person.", value, quote)
    if not squash(quote):
        return Finding("MISSING_EVIDENCE", name,
                       "No evidence was cited for this value.", value, quote)

    # §3.2 — the citation is not in the document.
    if not contains(source, trimmed_quote(quote, source)):
        return Finding("FABRICATED_EVIDENCE", name,
                       "The cited text does not appear in the document. Whatever "
                       "the value is, nothing here supports it.", value, quote)

    # §3.3 — the citation is real; does it name a different value?
    if contains(quote, str(value)):
        return None
    for competing in (_competing_numbers(value, quote),
                      _competing_dates(value, quote),
                      _competing_names(value, quote)):
        if competing:
            shown = ", ".join(competing[:3])
            return Finding("SILENT_REPAIR", name,
                           f"The cited text states {shown}, not {value!r}.",
                           value, quote, tuple(competing))
    # No competing value of the same type. §3.3 legitimate-inference rule: a
    # correct extraction may derive a value the cited text does not state.
    return None


def check(source: str, fields: Mapping[str, Mapping[str, Any]], *,
          tool: str = "evidence-check 0.1.0") -> Report:
    """Check an extraction against its source. No model, no network."""
    findings: list[Finding] = []
    notes: list[str] = []

    quoted = [n for n, f in fields.items() if squash((f or {}).get("quote", ""))]
    if fields and not quoted:
        # §3.1 report-level exception: say it once, not once per field.
        notes.append(
            "No field carried a citation, so evidence could not be checked. "
            "This tool reports whether values are supported; a pipeline that "
            "emits no quotes cannot be assessed by it."
        )

    for name in sorted(fields):
        entry = fields[name] or {}
        value, quote = entry.get("value"), str(entry.get("quote") or "")
        if notes and not squash(quote):
            # Suppressed per the report-level exception, but still check the
            # value itself — a placeholder is a finding with or without a quote.
            if _empty(value) or _placeholder(value):
                f = check_field(name, value, quote, source)
                if f and f.code in ("EMPTY_VALUE", "PLACEHOLDER_VALUE"):
                    findings.append(f)
            continue
        found = check_field(name, value, quote, source)
        if found is not None:
            findings.append(found)

    return Report(findings=tuple(findings), fields_checked=len(fields),
                  source_sha256=sha256(source), source_chars=len(source),
                  notes=tuple(notes), tool=tool)
