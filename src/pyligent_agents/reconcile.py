"""Compare a signed agreement against what a system of record currently holds.

Shadow mode's product. The bank already has both halves — the executed agreements
and the terms keyed into the collateral system — and the expensive question is where
they disagree. Drift between them is what margin disputes are made of.

Nothing here writes anywhere. It reads two inputs and produces a list of
disagreements, each carrying the clause that settles it.

**The rule that makes this safe to act on.** A discrepancy is only raised when the
extraction's own citation survives checking. If a field's quote does not appear in
the document, the honest output is "we could not verify this", never "your system is
wrong" — telling an operations team their record contradicts an agreement, on the
strength of a sentence a model invented, is worse than saying nothing. It burns the
one thing a trial is trying to establish.

So every field lands in exactly one of three states:

    AGREES        the system matches the agreement
    DISCREPANCY   they differ, and the clause behind our value checks out
    UNVERIFIED    the citation failed its check; a human must read the document

Only the middle one is an exception an analyst should work.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from evidencecheck.checks import check
from evidencecheck.normalize import parse_number

# Fields where a mismatch changes the size or timing of a margin call, and fields
# where it does not. Severity decides what a human reads first, so it is stated
# rather than inferred.
MATERIAL: dict[str, str] = {
    "threshold": "changes when a call is made and by how much",
    "mta": "changes whether a transfer happens at all",
    "minimum_transfer_amount": "changes whether a transfer happens at all",
    "independent_amount": "changes the amount posted regardless of exposure",
    "base_currency": "changes the unit every figure is denominated in",
    "eligible_currency": "changes what may be delivered",
    "rounding": "changes the size of every transfer",
    "rounding_delivery_amount": "changes the size of every delivery",
    "rounding_return_amount": "changes the size of every return",
    "valuation_percentage": "changes how much collateral a given asset is worth",
}

# Columns an export carries to identify a row, not to state a term. Comparing
# these produces noise that reads like a finding.
METADATA_COLUMNS = frozenset({
    "counterparty", "counterparty_name", "name", "id", "document", "document_id",
    "as_of", "as_of_date", "book", "desk", "portfolio", "legal_entity",
})

AGREES = "agrees"
DISCREPANCY = "discrepancy"
UNVERIFIED = "unverified"


# ISO codes and symbols we will treat as a stated unit. Restricted to a known list
# rather than "any three uppercase letters", which would read CSA, MTA and VM as
# currencies.
_CURRENCY_CODES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK",
    "HKD", "SGD", "CNY", "CNH", "MXN", "ZAR", "PLN", "CZK", "HUF", "KRW", "INR",
    "BRL", "TRY", "ILS", "RUB", "THB", "TWD",
}
_SYMBOL_TO_CODE = {"$": "$", "£": "GBP", "€": "EUR", "¥": "JPY"}
_CURRENCY_RE = re.compile(r"\b([A-Z]{3})\b|([$£€¥])")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _currency_of(value: Any) -> str | None:
    """The currency a value states, or None when it states none."""
    if not isinstance(value, str):
        return None
    text = value.upper().replace("US$", "USD ").replace("A$", "AUD ").replace("C$", "CAD ")
    for code, symbol in _CURRENCY_RE.findall(text):
        if code in _CURRENCY_CODES:
            return code
        if symbol:
            return _SYMBOL_TO_CODE.get(symbol, symbol)
    return None


def _numeric(value: Any) -> float | None:
    """The number a value states, ignoring units and separators."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    parsed = parse_number(value)
    if parsed is not None:
        return float(parsed)
    # Pull the number out rather than stripping units off, because units arrive in
    # forms nobody enumerates: "US$250,000", "EUR250000", "250,000 USD". Stripping
    # missed "US$" and made a value uncomparable, which reported a real match as a
    # mismatch.
    match = _NUMBER_RE.search(str(value).replace(",", ""))
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def values_agree(ours: Any, theirs: Any) -> bool:
    """Whether two stated values are the same term, formatted differently.

    Tolerant about formatting, strict about units. `500000`, `'500,000'` and
    `'USD 500,000'` are one term. **`'USD 500,000'` and `'EUR 500,000'` are not** —
    an earlier version stripped currency before comparing and reported them as
    agreeing, which silently disappeared one of the most material discrepancies a
    collateral book can have. A tool that hides a currency change has done the exact
    thing it exists to detect.

    A unit stated on only one side is treated as compatible: exports routinely carry
    a bare number in a column whose currency lives in the schema, and manufacturing
    a discrepancy from that is noise, which ends trials just as fast.
    """
    if isinstance(ours, bool) or isinstance(theirs, bool):
        return ours == theirs

    left, right = _currency_of(ours), _currency_of(theirs)
    if left and right and left != right:
        return False                      # both stated a unit, and they differ

    a, b = _numeric(ours), _numeric(theirs)
    if a is not None and b is not None:
        return a == b
    if a is None and b is None:
        return str(ours).strip().casefold() == str(theirs).strip().casefold()
    return False                          # one is a number, the other is not


@dataclass(frozen=True)
class FieldResult:
    field: str
    state: str
    ours: Any = None
    theirs: Any = None
    clause: str = ""
    material: bool = False
    impact: str = ""
    reason: str = ""          # why it could not be verified, when state is UNVERIFIED

    def to_dict(self) -> dict[str, Any]:
        out = {
            "field": self.field,
            "state": self.state,
            "agreement_says": self.ours,
            "system_says": self.theirs,
            "material": self.material,
        }
        if self.clause:
            out["clause"] = self.clause
        if self.impact:
            out["impact"] = self.impact
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True)
class Reconciliation:
    document: str
    counterparty: str
    results: tuple[FieldResult, ...] = ()
    notes: tuple[str, ...] = dc_field(default_factory=tuple)

    def _of(self, state: str) -> tuple[FieldResult, ...]:
        return tuple(r for r in self.results if r.state == state)

    @property
    def discrepancies(self) -> tuple[FieldResult, ...]:
        return self._of(DISCREPANCY)

    @property
    def unverified(self) -> tuple[FieldResult, ...]:
        return self._of(UNVERIFIED)

    @property
    def material(self) -> tuple[FieldResult, ...]:
        return tuple(r for r in self.discrepancies if r.material)

    @property
    def compared(self) -> int:
        return len(self.results)

    @property
    def agrees(self) -> bool:
        """True only when something was actually compared and all of it matched.

        A run that compared nothing agrees with nothing. Reporting agreement on an
        empty comparison is the kind of vacuously true claim this whole project
        exists to catch, and it would be worst here: an operations team reading
        "matches on every field" about a document nobody checked.
        """
        return bool(self.results) and not self.discrepancies and not self.unverified

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document,
            "counterparty": self.counterparty,
            "fields_compared": len(self.results),
            "agrees": self.agrees,
            "discrepancies": len(self.discrepancies),
            "material": len(self.material),
            "unverified": len(self.unverified),
            "fields": [r.to_dict() for r in self.results],
            "notes": list(self.notes),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def reconcile(
    source_text: str,
    fields: dict[str, dict[str, Any]],
    system_of_record: dict[str, Any],
    *,
    document: str = "",
    counterparty: str = "",
    material: dict[str, str] | None = None,
) -> Reconciliation:
    """Compare an extraction against stored terms, refusing to trust bad evidence.

    Only fields the system actually holds are compared. A field the system does not
    carry is a scope question, not a disagreement, and inventing disagreements is
    the fastest way to end a trial.
    """
    material_map = MATERIAL if material is None else material
    system_of_record = {k: v for k, v in system_of_record.items()
                        if k.strip().casefold() not in METADATA_COLUMNS}

    # One evidence pass over the whole extraction. A field whose citation does not
    # hold cannot support an exception against anyone's system.
    report = check(source_text, fields)
    unsupported = {
        f.field: f.message
        for f in report.findings
        if f.code in {"FABRICATED_EVIDENCE", "SILENT_REPAIR", "MISSING_EVIDENCE",
                      "PLACEHOLDER_VALUE", "EMPTY_VALUE"}
    }

    results: list[FieldResult] = []
    for name, theirs in system_of_record.items():
        entry = fields.get(name)
        if not isinstance(entry, dict):
            continue                      # not extracted: out of scope, not a finding

        is_material = name in material_map
        impact = material_map.get(name, "no effect on call size; record-keeping only")
        ours = entry.get("value")
        clause = entry.get("quote") or entry.get("evidence_quote") or ""

        if name in unsupported:
            results.append(FieldResult(
                name, UNVERIFIED, ours, theirs, clause, is_material, impact,
                reason=unsupported[name],
            ))
        elif values_agree(ours, theirs):
            results.append(FieldResult(name, AGREES, ours, theirs, clause, is_material, impact))
        else:
            results.append(FieldResult(
                name, DISCREPANCY, ours, theirs, clause, is_material, impact))

    notes: list[str] = []
    missing = [k for k in system_of_record if k not in fields]
    if missing:
        notes.append(
            f"{len(missing)} field(s) the system holds were not extracted, so nothing "
            f"was compared for them: {', '.join(sorted(missing)[:6])}"
        )
    return Reconciliation(document, counterparty, tuple(results), tuple(notes))


def render(rec: Reconciliation) -> str:
    """The page an analyst reads. Exceptions first, then what could not be checked."""
    rule = "─" * 68
    out = [rule, f"SHADOW RECONCILIATION  {rec.document}", rule]
    if rec.counterparty:
        out.append(f"  counterparty     {rec.counterparty}")
    out.append(f"  fields compared  {len(rec.results)}")
    out.append(f"  discrepancies    {len(rec.discrepancies)} "
               f"({len(rec.material)} material)")
    if rec.unverified:
        out.append(f"  unverified       {len(rec.unverified)}  "
                   f"— citation did not check out; read the document")
    out.append("")

    for r in sorted(rec.discrepancies, key=lambda r: not r.material):
        tag = "MATERIAL" if r.material else "minor"
        out.append(f"── {tag} · {r.field} " + "─" * max(3, 46 - len(r.field)))
        out.append(f"  agreement says : {r.ours}")
        out.append(f"  system says    : {r.theirs}")
        out.append(f"  impact         : {r.impact}")
        if r.clause:
            out.append(f"  clause         : {r.clause[:160]}")
        out.append("")

    for r in rec.unverified:
        out.append(f"── UNVERIFIED · {r.field} " + "─" * max(3, 42 - len(r.field)))
        out.append(f"  {r.reason}")
        out.append("  No exception raised: a citation that does not check out cannot")
        out.append("  support a claim that the system is wrong.")
        out.append("")

    for note in rec.notes:
        out.append(f"  note: {note}")
    if not rec.results:
        out.append("  NOTHING COMPARED — no field the system holds was extracted from")
        out.append("  this document. This is not agreement; it is absence of evidence.")
    elif rec.agrees:
        out.append(f"  The system matches the agreement on all {len(rec.results)} "
                   "field(s) compared.")
    out.append("")
    out.append("  Nothing was written. This process reads only.")
    return "\n".join(out)
