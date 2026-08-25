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
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

# A check returns (passed, message). The message is read by a human at 3am, so
# it should say what is wrong, not merely that something is.
Check = Callable[[dict[str, Any]], "tuple[bool, str]"]


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
        haystack = " ".join(str(source).split()).lower()

        entries = _dig(artifact, under) or {}
        if isinstance(entries, list):
            entries = {str(i): e for i, e in enumerate(entries)}
        fabricated = [
            k for k, v in entries.items()
            if isinstance(v, dict)
            and " ".join(str(v.get(quote_field, "")).split()).lower() not in haystack
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


def no_placeholder_values(*, under: str, markers: Sequence[str] = ("TODO", "TBD", "N/A", "unknown", "null")) -> Check:
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
