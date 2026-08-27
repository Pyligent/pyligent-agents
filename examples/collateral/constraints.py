"""From a verified CSA extraction to a constraint set an optimiser can consume.

This is the hand-off boundary. Everything upstream — extraction, evidence
checking, gates, CDM — establishes *what the agreement says*. Everything
downstream — allocation, optimisation, settlement — decides *what to move*.
This module is the seam, and the seam is where collateral projects usually lose
their audit trail.

The rule here: **every constraint carries the clause that established it.** Not a
field name, not a page number — the verbatim text, already proved to be a
substring of the source document by the gates upstream. A recommendation is
defensible only if every constraint behind it can be traced to language a
counterparty signed.

And the part most models skip:

> **A constraint set must declare what it could not express.**

A CSA contains terms no constraint model represents — a Valuation Agent's
discretion, a ratings trigger, a substitution right, bespoke dispute mechanics.
Dropping those silently produces a pack that looks complete and is not. The
optimiser then solves the wrong problem with total confidence, and nobody can
see why. `ConstraintPack.unsupported` is the list of things a human must still
read, and a pack is not certified while anything material sits in it.

    from collateral.constraints import build_pack, certify

    pack = build_pack(artifact)
    record = certify(pack)
    if not record.certified:
        ...   # route to a human; do not optimise
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

# Terms that commonly appear in a CSA and that a linear constraint set cannot
# represent. Detected on the source text, not the extraction, because the point
# is to catch what the extractor did not model at all.
UNSUPPORTED_MARKERS: tuple[tuple[str, str], ...] = (
    ("valuation agent", "A Valuation Agent's discretion is not a constraint; a human decides."),
    ("ratings trigger", "A ratings trigger changes the terms conditionally over time."),
    ("downgrade", "A downgrade provision changes the terms conditionally over time."),
    ("substitution", "Substitution rights alter eligibility after the fact."),
    ("dispute resolution", "Dispute mechanics govern what happens when the numbers disagree."),
    ("interest amount", "Interest on posted collateral is a cashflow, not an eligibility rule."),
    ("distributions", "Distributions on posted collateral are a cashflow rule."),
    ("cross-default", "Cross-default affects the agreement's life, not a single call."),
)


@dataclass(frozen=True)
class Provenance:
    """The clause behind a constraint, and whether it was machine-checked."""

    field: str
    quote: str
    verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "quote": self.quote, "verified": self.verified}


@dataclass(frozen=True)
class Constraint:
    id: str
    kind: str                       # eligibility | valuation | threshold | transfer | rounding | currency
    party: str                      # PARTY_1 | PARTY_2 | BOTH
    description: str
    expression: dict[str, Any]      # machine-readable; the optimiser reads this
    provenance: tuple[Provenance, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "party": self.party,
            "description": self.description, "expression": self.expression,
            "provenance": [p.to_dict() for p in self.provenance],
        }


@dataclass(frozen=True)
class ConstraintPack:
    document_id: str
    base_currency: str
    as_of: date
    constraints: tuple[Constraint, ...] = ()
    unsupported: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def of_kind(self, kind: str) -> list[Constraint]:
        return [c for c in self.constraints if c.kind == kind]

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "base_currency": self.base_currency,
            "as_of": self.as_of.isoformat(),
            "constraints": [c.to_dict() for c in self.constraints],
            "unsupported": list(self.unsupported),
            "notes": list(self.notes),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


@dataclass(frozen=True)
class CertificationRecord:
    """Whether this pack may be optimised against, and why.

    Deliberately not a boolean. "Not certified" needs to say which constraint is
    missing or which term nobody modelled, because that is what a human acts on.
    """

    document_id: str
    certified: bool
    reasons: tuple[str, ...] = ()
    constraint_count: int = 0
    unsupported_count: int = 0
    provenance_complete: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id, "certified": self.certified,
            "reasons": list(self.reasons), "constraint_count": self.constraint_count,
            "unsupported_count": self.unsupported_count,
            "provenance_complete": self.provenance_complete,
        }


# --- building -------------------------------------------------------------


def _entry(artifact: dict[str, Any], name: str) -> dict[str, Any] | None:
    entry = (artifact.get("fields") or {}).get(name)
    return entry if isinstance(entry, dict) else None


def _prov(artifact: dict[str, Any], *names: str) -> tuple[Provenance, ...]:
    out = []
    for name in names:
        entry = _entry(artifact, name)
        if entry is None:
            continue
        quote = entry.get("evidence_quote") or ""
        # The gates upstream already proved every quote is a verbatim substring
        # of the source. Re-checking here keeps the pack honest if it is ever
        # built from an artifact that skipped them.
        verified = bool(quote) and quote in (artifact.get("_source_text") or "")
        out.append(Provenance(field=name, quote=quote, verified=verified))
    return tuple(out)


def _value(artifact: dict[str, Any], name: str) -> Any:
    entry = _entry(artifact, name)
    return entry.get("value") if entry else None


def _per_party(artifact: dict[str, Any], base: str) -> list[tuple[str, Any, tuple[str, ...]]]:
    """[(party, value, source_field_names)] — shared election fans out to both."""
    a, b = _value(artifact, f"{base}_party_a"), _value(artifact, f"{base}_party_b")
    if a is None and b is None:
        shared = _value(artifact, base)
        if shared is None:
            return []
        return [("PARTY_1", shared, (base,)), ("PARTY_2", shared, (base,))]
    out = []
    if a is not None:
        out.append(("PARTY_1", a, (f"{base}_party_a",)))
    if b is not None:
        out.append(("PARTY_2", b, (f"{base}_party_b",)))
    return out


def find_unsupported(artifact: dict[str, Any]) -> tuple[str, ...]:
    """Terms present in the document that this constraint model cannot express.

    Scanned on the SOURCE, not the extraction. A term the extractor never
    modelled will not appear in the fields, which is precisely why looking at
    the fields would miss it.
    """
    source = (artifact.get("_source_text") or "").lower()
    return tuple(
        f"{marker}: {why}" for marker, why in UNSUPPORTED_MARKERS if marker in source
    )


def build_pack(artifact: dict[str, Any], *, as_of: date | None = None) -> ConstraintPack:
    """Turn a verified CSA artifact into constraints, with the clauses attached."""
    base = _value(artifact, "base_currency")
    if not base:
        raise ValueError("cannot build a constraint pack without a base currency")

    constraints: list[Constraint] = []
    add = constraints.append

    # --- currency ---------------------------------------------------------
    eligible = _value(artifact, "eligible_currency") or base
    if isinstance(eligible, str):
        eligible = [c.strip() for c in eligible.split(",") if c.strip()]
    add(Constraint(
        id="currency.eligible", kind="currency", party="BOTH",
        description="Collateral may be denominated only in these currencies",
        expression={"op": "in", "field": "currency", "values": list(eligible)},
        provenance=_prov(artifact, "eligible_currency", "base_currency"),
    ))

    # --- eligibility and valuation ---------------------------------------
    for i, row in enumerate(artifact.get("eligible_collateral") or []):
        desc = row.get("description", f"asset class {i}")
        add(Constraint(
            id=f"eligibility.{i}", kind="eligibility", party="BOTH",
            description=f"Eligible credit support: {desc}",
            expression={"op": "allow", "asset_class": desc},
            provenance=_prov(artifact, "base_currency"),
        ))
        pct = row.get("valuation_pct")
        if pct is not None:
            add(Constraint(
                id=f"valuation.{i}", kind="valuation", party="BOTH",
                description=f"Valuation percentage for {desc} is {pct}%",
                # Stated as a valuation percentage, NOT a haircut. The optimiser
                # multiplies market value by this; storing 98 as a haircut would
                # price a $1m bond at $20,000.
                expression={"op": "valuation_pct", "asset_class": desc, "pct": pct},
                provenance=_prov(artifact, "base_currency"),
            ))

    # --- threshold, MTA, independent amount ------------------------------
    for party, value, fields in _per_party(artifact, "threshold"):
        add(Constraint(
            id=f"threshold.{party.lower()}", kind="threshold", party=party,
            description=f"Unsecured exposure tolerated before a call ({party})",
            expression={"op": "threshold", "amount": value, "currency": base},
            provenance=_prov(artifact, *fields),
        ))
    for party, value, fields in _per_party(artifact, "mta"):
        add(Constraint(
            id=f"mta.{party.lower()}", kind="transfer", party=party,
            description=f"No transfer smaller than this will be made ({party})",
            expression={"op": "min_transfer", "amount": value, "currency": base},
            provenance=_prov(artifact, *fields),
        ))
    for party, value, fields in _per_party(artifact, "independent_amount"):
        add(Constraint(
            id=f"independent_amount.{party.lower()}", kind="threshold", party=party,
            description=f"Independent Amount posted regardless of exposure ({party})",
            expression={"op": "independent_amount", "amount": value, "currency": base},
            provenance=_prov(artifact, *fields),
        ))

    # --- rounding ---------------------------------------------------------
    d_amt = _value(artifact, "rounding_delivery_amount")
    r_amt = _value(artifact, "rounding_return_amount")
    if d_amt is not None or r_amt is not None:
        add(Constraint(
            id="rounding", kind="rounding", party="BOTH",
            description="Delivery and Return Amounts round to these multiples",
            expression={
                "op": "round",
                "delivery": {"amount": d_amt,
                             "direction": _value(artifact, "rounding_delivery_direction")},
                "return": {"amount": r_amt,
                           "direction": _value(artifact, "rounding_return_direction")},
                "currency": _value(artifact, "rounding_currency") or base,
            },
            provenance=_prov(artifact, "rounding_delivery_amount",
                             "rounding_delivery_direction", "rounding_return_amount",
                             "rounding_return_direction"),
        ))

    return ConstraintPack(
        document_id=artifact.get("document_id", "unknown"),
        base_currency=base,
        as_of=as_of or date.today(),
        constraints=tuple(constraints),
        unsupported=find_unsupported(artifact),
        notes=("Constraints only. Allocation, optimisation and settlement are "
               "downstream of this pack and out of scope for this repository.",),
    )


# --- certification --------------------------------------------------------

# A pack without these cannot describe a margin call at all.
REQUIRED_KINDS = ("currency", "eligibility", "threshold", "transfer")


def certify(pack: ConstraintPack, *, allow_unsupported: bool = False) -> CertificationRecord:
    """May this pack be optimised against?

    Certification is not "did it parse". It is: are the constraints an optimiser
    needs all present, is every one of them traceable to signed language, and
    does anything remain in the agreement that nobody modelled?
    """
    reasons: list[str] = []

    missing = [k for k in REQUIRED_KINDS if not pack.of_kind(k)]
    if missing:
        reasons.append(
            f"no {', '.join(missing)} constraint(s) were derived. An optimiser "
            f"cannot size a call without them."
        )

    unverified = [
        f"{c.id}:{p.field}"
        for c in pack.constraints for p in c.provenance if not p.verified
    ]
    if unverified:
        reasons.append(
            f"{len(unverified)} constraint(s) cite a clause that could not be "
            f"confirmed in the source: {', '.join(unverified[:4])}"
            + (" …" if len(unverified) > 4 else "")
        )

    bare = [c.id for c in pack.constraints if not c.provenance]
    if bare:
        reasons.append(
            f"{len(bare)} constraint(s) carry no clause at all: {', '.join(bare[:4])}"
            + (" …" if len(bare) > 4 else "")
        )

    if pack.unsupported and not allow_unsupported:
        reasons.append(
            f"{len(pack.unsupported)} term(s) in the agreement are not expressible "
            f"as constraints and must be read by a human before this counterparty "
            f"is optimised."
        )

    return CertificationRecord(
        document_id=pack.document_id,
        certified=not reasons,
        reasons=tuple(reasons),
        constraint_count=len(pack.constraints),
        unsupported_count=len(pack.unsupported),
        provenance_complete=not unverified and not bare,
    )
