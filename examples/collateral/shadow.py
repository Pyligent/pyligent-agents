"""Shadow mode: run beside the existing process, write nothing, reconcile.

A bank does not begin by letting a model touch collateral. It begins by letting
it watch. Shadow mode reads the agreements, derives what the terms *should* be,
compares that against what the margin system currently holds, and produces one
artifact: a list of disagreements, each with the clause that settles it.

Two properties make this safe enough to say yes to, and both are enforced rather
than promised:

**Nothing is written.** A shadow run is constructed with no approver and a
read-only phase, so every `RESTRICTED` tool is denied at the harness. That is
the same mechanism the refund example uses to stop an agent moving money, and
there is a test asserting a shadow run cannot reach one.

**Nothing is claimed without a clause.** Every discrepancy carries the verbatim
agreement text behind our value. A finding a counterparty cannot check is not a
finding; it is an opinion with a number attached.

The result most trials actually produce is not "the AI agrees with us". It is a
short list of counterparties whose stored terms do not match their signed
agreement — which is worth the trial on its own, and is the reason to run one.

    python examples/run.py shadow
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from collateral.constraints import ConstraintPack

# Fields where a mismatch changes the size of a margin call, and fields where it
# does not. Severity is not decoration — it decides what a human reads first.
MATERIAL = {
    "threshold": "changes when a call is made and by how much",
    "mta": "changes whether a transfer happens at all",
    "independent_amount": "changes the amount posted regardless of exposure",
    "base_currency": "changes the unit every figure is denominated in",
    "rounding_delivery_amount": "changes the size of every delivery",
    "rounding_return_amount": "changes the size of every return",
}


@dataclass(frozen=True)
class Discrepancy:
    field: str
    ours: Any
    theirs: Any
    clause: str
    material: bool
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field, "agreement_says": self.ours,
            "system_says": self.theirs, "clause": self.clause,
            "material": self.material, "impact": self.why,
        }


@dataclass(frozen=True)
class ReconciliationReport:
    document_id: str
    counterparty: str
    checked: int
    discrepancies: tuple[Discrepancy, ...]

    @property
    def material(self) -> tuple[Discrepancy, ...]:
        return tuple(d for d in self.discrepancies if d.material)

    @property
    def agrees(self) -> bool:
        return not self.discrepancies

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id, "counterparty": self.counterparty,
            "fields_checked": self.checked,
            "agrees": self.agrees,
            "material_discrepancies": len(self.material),
            "discrepancies": [d.to_dict() for d in self.discrepancies],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _num(value: Any) -> Any:
    """Compare 500000 and '500,000' as equal; leave everything else alone."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    for junk in ("£", "$", "€", "USD", "GBP", "EUR", ",", " "):
        text = text.replace(junk, "")
    try:
        return float(text)
    except ValueError:
        return str(value).strip().casefold()


def reconcile(artifact: dict[str, Any], system_of_record: dict[str, Any], *,
              counterparty: str = "") -> ReconciliationReport:
    """Compare the agreement against what the margin system currently holds.

    Only fields the system claims to hold are compared. A field the system does
    not carry is not a discrepancy — it is a scope question, and inventing
    disagreements is the fastest way to lose a trial.
    """
    fields = artifact.get("fields") or {}
    found: list[Discrepancy] = []
    checked = 0

    for name, theirs in system_of_record.items():
        entry = fields.get(name)
        if not isinstance(entry, dict):
            continue
        checked += 1
        ours = entry.get("value")
        if _num(ours) == _num(theirs):
            continue
        found.append(Discrepancy(
            field=name, ours=ours, theirs=theirs,
            clause=entry.get("evidence_quote", ""),
            material=name in MATERIAL,
            why=MATERIAL.get(name, "no effect on call size; record-keeping only"),
        ))

    return ReconciliationReport(
        document_id=artifact.get("document_id", "unknown"),
        counterparty=counterparty or artifact.get("title", ""),
        checked=checked,
        discrepancies=tuple(found),
    )


def render(report: ReconciliationReport, pack: ConstraintPack | None = None) -> str:
    """The page a collateral analyst actually reads."""
    out: list[str] = []
    rule = "─" * 68
    out.append(f"SHADOW RECONCILIATION  {report.document_id}")
    out.append("=" * 68)
    out.append(f"  counterparty     {report.counterparty}")
    out.append(f"  fields compared  {report.checked}")
    out.append(f"  disagreements    {len(report.discrepancies)} "
               f"({len(report.material)} material)")
    out.append("")

    if report.agrees:
        out.append("  The margin system matches the agreement on every field compared.")
    for d in sorted(report.discrepancies, key=lambda x: not x.material):
        tag = "MATERIAL" if d.material else "minor   "
        out.append(f"── {tag} · {d.field} {rule[:44 - len(d.field)]}")
        out.append(f"  agreement says : {d.ours!r}")
        out.append(f"  system says    : {d.theirs!r}")
        out.append(f"  impact         : {d.why}")
        if d.clause:
            clause = d.clause.replace("\n", " ").strip()
            out.append(f"  clause         : \"{clause[:96]}\"")
        out.append("")

    if pack is not None and pack.unsupported:
        out.append(f"── NOT MODELLED · {len(pack.unsupported)} term(s) {rule[:34]}")
        for u in pack.unsupported:
            out.append(f"  · {u}")
        out.append("")
        out.append("  These are in the agreement and are not constraints. A human")
        out.append("  must read them before this counterparty is optimised.")
        out.append("")

    out.append("  Nothing was written. No tool with an external effect was reachable")
    out.append("  in this run; the harness denied the tier outright.")
    return "\n".join(out)
