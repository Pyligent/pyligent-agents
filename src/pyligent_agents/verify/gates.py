"""Gates: machine-checkable stop conditions.

A model must never get to unilaterally declare a task done. "The extraction
looks complete" is a judgement call. "Every required field is present, every
value carries a quote, and every quote appears verbatim in the source" is a
condition a computer checks, and it either holds or it does not.

Gates are **pure functions over the artifact**. No model, no network, no
ambiguity. That is what makes them the engineering equivalent of *all tests pass
and lint reports zero errors*.

This module ships the gates that recur in every domain. Your domain gates are
composed from them, plus at least one you write yourself:

> **Every gate set should contain at least one check a JSON schema could not
> express.** If yours does not, you have written a validator, not a gate set.
> The examples show what that looks like: `refund <= order total`,
> `rounding finer than the minimum transfer amount`, `these haircuts are
> actually valuation percentages`.

And the counterpart rule, learned the expensive way:

> **A gate that cannot tell must pass, not fail.** When a check's precondition
> does not hold, abstain. A gate that fires on "I cannot tell" turns every
> unusual-but-valid document into a referral, and a queue full of correct
> documents is how a control gets switched off. See ADR 0006 for the CSA gate
> that asserted `MTA <= Threshold` and referred every standard VM CSA, where
> the Threshold is legitimately zero.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

# One definition of each check, shared with the `unsourced` CLI. Two copies
# of a comparison rule drift, and a drifted rule invalidates every number
# measured with it long after anyone would notice.
from unsourced.checks import PLACEHOLDERS, check_field
from unsourced.normalize import contains

# A check returns (passed, message). The message is read by a human at 3am, so
# it should say what is wrong, not merely that something is.
Check = Callable[[dict[str, Any]], "tuple[bool, str]"]

# ISO 4217 alphabetic codes. The active list, not the historical one — a
# withdrawn code in a live agreement is a finding, not a convenience.
ISO_4217 = frozenset([
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN", "BAM", "BBD",
    "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BRL", "BSD", "BTN", "BWP", "BYN",
    "BZD", "CAD", "CDF", "CHF", "CLP", "CNY", "COP", "CRC", "CUP", "CVE", "CZK", "DJF",
    "DKK", "DOP", "DZD", "EGP", "ERN", "ETB", "EUR", "FJD", "FKP", "GBP", "GEL", "GHS",
    "GIP", "GMD", "GNF", "GTQ", "GYD", "HKD", "HNL", "HRK", "HTG", "HUF", "IDR", "ILS",
    "INR", "IQD", "IRR", "ISK", "JMD", "JOD", "JPY", "KES", "KGS", "KHR", "KMF", "KPW",
    "KRW", "KWD", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD", "LSL", "LYD", "MAD", "MDL",
    "MGA", "MKD", "MMK", "MNT", "MOP", "MRU", "MUR", "MVR", "MWK", "MXN", "MYR", "MZN",
    "NAD", "NGN", "NIO", "NOK", "NPR", "NZD", "OMR", "PAB", "PEN", "PGK", "PHP", "PKR",
    "PLN", "PYG", "QAR", "RON", "RSD", "RUB", "RWF", "SAR", "SBD", "SCR", "SDG", "SEK",
    "SGD", "SHP", "SLE", "SOS", "SRD", "SSP", "STN", "SVC", "SYP", "SZL", "THB", "TJS",
    "TMT", "TND", "TOP", "TRY", "TTD", "TWD", "TZS", "UAH", "UGX", "USD", "UYU", "UZS",
    "VED", "VES", "VND", "VUV", "WST", "XAF", "XCD", "XOF", "XPF", "YER", "ZAR", "ZMW",
    "ZWG"
])


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "message": self.message}


@dataclass(frozen=True)
class Gate:
    name: str
    description: str
    check: Check

    def run(self, artifact: dict[str, Any]) -> GateResult:
        try:
            passed, message = self.check(artifact)
        except Exception as exc:  # noqa: BLE001
            # A gate that errors has FAILED. Fail closed: an exception in a
            # control is not a reason to let the artifact through.
            return GateResult(self.name, False, f"gate raised {type(exc).__name__}: {exc}")
        return GateResult(self.name, passed, message)


@dataclass(frozen=True)
class GateReport:
    results: tuple[GateResult, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> tuple[GateResult, ...]:
        return tuple(r for r in self.results if not r.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "results": [r.to_dict() for r in self.results],
            "failed": [r.name for r in self.failures],
        }

    def render(self) -> str:
        lines = [f"  [{'PASS' if r.passed else 'FAIL'}] {r.name}: {r.message}"
                 for r in self.results]
        lines.append(f"  => {'ALL GATES PASSED' if self.passed else 'GATES FAILED'}")
        return "\n".join(lines)


@dataclass
class GateSet:
    gates: list[Gate] = field(default_factory=list)

    def add(self, name: str, description: str, check: Check) -> GateSet:
        self.gates.append(Gate(name, description, check))
        return self

    def extend(self, other: GateSet) -> GateSet:
        self.gates.extend(other.gates)
        return self

    def evaluate(self, artifact: dict[str, Any]) -> GateReport:
        return GateReport(tuple(g.run(artifact) for g in self.gates))

    def __len__(self) -> int:
        return len(self.gates)


# ---------------------------------------------------------------------------
# The reusable library. Each returns a `Check` you hand to `GateSet.add`.
# ---------------------------------------------------------------------------


def _dig(artifact: dict[str, Any], path: str) -> Any:
    """Read a dotted path. Returns None if any hop is missing."""
    cur: Any = artifact
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def required_keys(*keys: str, under: str = "") -> Check:
    """Every named key is present (dotted paths allowed)."""
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        base = _dig(artifact, under) if under else artifact
        if not isinstance(base, dict):
            return False, f"'{under}' is missing or not an object"
        missing = [k for k in keys if k not in base]
        if missing:
            return False, f"missing required key(s): {', '.join(sorted(missing))}"
        return True, f"all {len(keys)} required key(s) present"
    return check


def non_empty(path: str) -> Check:
    """A list, string or dict at `path` exists and is not empty."""
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        value = _dig(artifact, path)
        if not value:
            return False, f"'{path}' is missing or empty"
        return True, f"'{path}' has {len(value)} item(s)" if hasattr(value, "__len__") \
            else f"'{path}' is set"
    return check


def every_value_has(field_name: str, *, under: str) -> Check:
    """Every entry under `under` carries a non-empty `field_name`.

    The canonical use is evidence: an extracted value with no citation is an
    assertion, not an extraction.
    """
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        entries = _dig(artifact, under) or {}
        if isinstance(entries, list):
            entries = {str(i): e for i, e in enumerate(entries)}
        if not isinstance(entries, dict):
            return False, f"'{under}' is not a mapping or list"
        bad = [k for k, v in entries.items()
               if not isinstance(v, dict) or not str(v.get(field_name, "")).strip()]
        if bad:
            return False, f"entries without '{field_name}': {', '.join(sorted(bad))}"
        return True, f"all {len(entries)} entries carry '{field_name}'"
    return check


def quotes_appear_in_source(
    *, under: str, quote_field: str = "evidence_quote", source_key: str = "_source_text"
) -> Check:
    """Every quote appears **verbatim** in the source document.

    This is the gate that makes an extraction falsifiable. Whitespace is
    normalised — PDF and HTML text wraps at arbitrary points, and a quote
    spanning a line break is still a real quote. **Wording is not normalised:**
    a paraphrase is not a citation.
    """
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        source = artifact.get(source_key)
        if not source:
            return False, f"no '{source_key}' supplied; evidence cannot be checked"

        entries = _dig(artifact, under) or {}
        if isinstance(entries, list):
            entries = {str(i): e for i, e in enumerate(entries)}
        fabricated = [
            k for k, v in entries.items()
            if isinstance(v, dict) and not contains(str(source), str(v.get(quote_field, "")))
        ]
        if fabricated:
            return False, f"quote not found in source for: {', '.join(sorted(fabricated))}"
        return True, f"all {len(entries)} quote(s) appear verbatim in the source"
    return check


def numeric_range(
    path: str, low: float, high: float, *, over: str = "", key: str = ""
) -> Check:
    """A number, or every number in a list of objects, sits inside [low, high]."""
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        if over:
            rows = _dig(artifact, over) or []
            bad = [
                r.get(path, "?") for r in rows
                if not isinstance(r.get(key or path), (int, float))
                or not (low <= float(r[key or path]) <= high)
            ]
            if bad:
                return False, f"outside [{low}, {high}]: {', '.join(map(str, bad))}"
            return True, f"{len(rows)} value(s) within [{low}, {high}]"

        value = _dig(artifact, path)
        if not isinstance(value, (int, float)):
            return False, f"'{path}' is missing or not numeric"
        if not (low <= float(value) <= high):
            return False, f"'{path}' = {value} is outside [{low}, {high}]"
        return True, f"'{path}' = {value} is within [{low}, {high}]"
    return check


def one_of(path: str, allowed: Iterable[Any]) -> Check:
    """The value at `path` is in a closed vocabulary.

    Valid JSON naming a category you have no queue for is still a failure — a
    label nothing consumes is worse than no label, because it looks like it
    worked.
    """
    options = list(allowed)
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        value = _dig(artifact, path)
        if value not in options:
            return False, f"'{path}' = {value!r} is not one of {options}"
        return True, f"'{path}' = {value!r} is in the allowed set"
    return check


def cross_field(
    label: str, fn: Callable[[dict[str, Any]], bool], *, message: str = ""
) -> Check:
    """The escape hatch, and the most valuable gate in most sets.

    Use it for the relationship no schema can express — the one that needs
    domain knowledge to spot. Give it a message that says what is probably
    wrong, not just that something is.
    """
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        return (True, label) if fn(artifact) else (False, message or f"{label} does not hold")
    return check


def verified_independently(key: str = "_verification") -> Check:
    """A separate verifier approved this artifact, with citations."""
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        verdict = artifact.get(key) or {}
        if not verdict:
            return False, "no independent verification attached"
        if not verdict.get("approved"):
            reasons = "; ".join(verdict.get("reasons", [])) or "no reason given"
            return False, f"independent verifier rejected: {reasons}"
        n = len(verdict.get("evidence", verdict.get("citations", [])))
        return True, f"independent verifier approved with {n} citation(s)"
    return check


def no_placeholder_values(*, under: str, markers: Sequence[str] = PLACEHOLDERS) -> Check:
    """Catch an extraction that filled the shape but not the content.

    A model that cannot find a value will often produce a plausible-looking
    placeholder rather than omitting the field. The shape passes a schema; the
    artifact is useless.
    """
    lowered = [m.lower() for m in markers]
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        entries = _dig(artifact, under) or {}
        if isinstance(entries, list):
            entries = {str(i): e for i, e in enumerate(entries)}
        bad = []
        for k, v in entries.items():
            value = v.get("value") if isinstance(v, dict) else v
            if isinstance(value, str) and value.strip().lower() in lowered:
                bad.append(k)
        if bad:
            return False, f"placeholder value(s) in: {', '.join(sorted(bad))}"
        return True, f"no placeholder values in {len(entries)} entries"
    return check


def iso_currency(*paths: str, under: str = "") -> Check:
    """Every named value is a valid ISO 4217 alphabetic currency code.

    ISDA's CSA benchmarking protocol names this explicitly as a validation
    check ("ensure currency codes are valid ISO codes"). It matters more than
    it looks: `USD` and `US Dollars` are the same thing to a reader and two
    different things to a collateral system, and a downstream mapping that
    silently drops an unrecognised code loses the currency rather than failing.
    """
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        bad, seen = [], 0
        for path in paths:
            value = _dig(artifact, f"{under}.{path}" if under else path)
            if isinstance(value, dict):
                value = value.get("value")
            if value is None:
                continue
            seen += 1
            if not (isinstance(value, str) and value.strip().upper() in ISO_4217):
                bad.append(f"{path}={value!r}")
        if bad:
            return False, f"not a valid ISO 4217 code: {', '.join(bad)}"
        return True, f"{seen} currency code(s) are valid ISO 4217"
    return check


def values_are_numeric(*paths: str, under: str = "") -> Check:
    """Every named value is a number, not a string that looks like one.

    Also an explicit ISDA validation check ("ensure rounding amounts are
    represented as numbers, not strings"). `"5,000,000"` and `5000000` both
    read correctly to a human; only one of them adds up.
    """
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        bad, seen = [], 0
        for path in paths:
            value = _dig(artifact, f"{under}.{path}" if under else path)
            if isinstance(value, dict):
                value = value.get("value")
            if value is None:
                continue
            seen += 1
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                bad.append(f"{path}={value!r}")
        if bad:
            return False, f"should be a number, not a string: {', '.join(bad)}"
        return True, f"{seen} value(s) are numeric"
    return check


# A clause pointer such as "13(c)(ii)", "Paragraph 11(b)" or "Section 4.2" —
# digits and punctuation, so it survives a naive "does it contain a number"
# test and lands in a numeric field looking like money.
_REFERENCE = re.compile(
    r"^\s*(?:paragraph|para|clause|section|annex|appendix|part)?\s*"
    r"\d+\s*(?:[().]\s*[a-z0-9ivx]+\s*\)?)+\s*$",
    re.IGNORECASE,
)


def no_cross_reference_values(*paths: str, under: str = "") -> Check:
    """A field that should hold a quantity is not holding a clause pointer.

    ISDA's benchmarking paper singles this out: a domain-aware extractor
    "is more likely to distinguish references like 'paragraph 13(c)(ii)' from
    monetary amounts and avoid transcription errors". A CSA is dense with both,
    they sit in adjacent sentences, and `13(c)(ii)` in a threshold field is a
    number a schema will happily accept.
    """
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        bad, seen = [], 0
        for path in paths:
            value = _dig(artifact, f"{under}.{path}" if under else path)
            if isinstance(value, dict):
                value = value.get("value")
            if value is None:
                continue
            seen += 1
            if isinstance(value, str) and _REFERENCE.match(value):
                bad.append(f"{path}={value!r}")
        if bad:
            return False, (
                f"a clause reference was read into a value field: {', '.join(bad)}. "
                f"This is a cross-reference, not a quantity."
            )
        return True, f"{seen} value(s) are quantities, not clause references"
    return check


def no_silent_repair(*, under: str = "fields", quote_field: str = "evidence_quote",
                     source_key: str = "_source_text") -> Check:
    """No field's cited text names a *different* value than the one extracted.

    The failure the other evidence gates cannot see. A model asked to extract a
    name that reads `Jonathon` on the passport and `Jonathan` on the form will
    often write down whichever makes the file consistent — and quote the
    passport line honestly. `evidence_present` passes. `evidence_verbatim`
    passes. The discrepancy the extraction was hired to surface is the thing it
    removed.

    Delegates to `unsourced`, which is the single definition of this check and
    carries the normalisation rules that keep it from firing on a correct
    extraction: a value the quote does not mention at all is inference, not
    repair, and reporting it would make the gate unusable on legal text.
    """
    def check(artifact: dict[str, Any]) -> tuple[bool, str]:
        source = str(artifact.get(source_key) or "")
        if not source:
            return False, f"no '{source_key}' supplied; evidence cannot be checked"
        entries = _dig(artifact, under) or {}
        if isinstance(entries, list):
            entries = {str(i): e for i, e in enumerate(entries)}

        repaired = []
        for name, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            finding = check_field(str(name), entry.get("value"),
                                  str(entry.get(quote_field) or ""), source)
            if finding is not None and finding.code == "SILENT_REPAIR":
                repaired.append(f"{name} (cited text states {', '.join(finding.competing[:2])})")
        if repaired:
            return False, (
                f"the cited text names a different value for: {'; '.join(sorted(repaired))}. "
                f"A discrepancy was removed rather than reported."
            )
        return True, f"no cited text contradicts its value across {len(entries)} entries"
    return check


def evidence_gated_extraction(
    *required: str,
    under: str = "fields",
    source_key: str = "_source_text",
    quote_field: str = "evidence_quote",
) -> GateSet:
    """The five gates every document-extraction task needs.

    Compose your domain checks on top:

        gates = evidence_gated_extraction("total", "currency", under="fields")
        gates.add("total_matches_lines", "...", cross_field(...))
    """
    return (
        GateSet()
        .add("required_fields", "Every mandatory field is present",
             required_keys(*required, under=under))
        .add("no_placeholders", "No field was filled with a placeholder",
             no_placeholder_values(under=under))
        .add("evidence_present", "Every field carries a quote",
             every_value_has(quote_field, under=under))
        .add("evidence_verbatim", "Every quote appears verbatim in the source",
             quotes_appear_in_source(under=under, quote_field=quote_field,
                                     source_key=source_key))
        .add("independently_verified", "A separate verifier approved the artifact",
             verified_independently())
    )
