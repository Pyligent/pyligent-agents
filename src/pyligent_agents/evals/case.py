"""Labelled cases: the thing an eval is *against*.

A test asserts that control flow held. An eval asks a different question — *did
the system get the right answer, on data where we know the right answer?* — and
answers it as a number you can compare across runs.

The distinction that matters in this library's domain: a case carries **two**
kinds of label.

    gold_fields      what a perfect extraction returns
    gold_decision    what the pipeline should DO about it

They are not the same, and the gap between them is where the interesting
findings live. A model can extract every field correctly and still reach the
wrong decision — and, more dangerously, a model can reach a decision that looks
right while quietly "correcting" an inconsistency it was supposed to report.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

ACCEPT = "accept"
REFER = "refer"


@dataclass(frozen=True)
class EvalCase:
    """One labelled document."""

    case_id: str
    kind: str                       # your document/task type
    source_text: str                # the document exactly as the system sees it
    gold_fields: dict[str, Any]     # field name -> expected value
    gold_decision: str              # ACCEPT or REFER
    gold_failing_gates: tuple[str, ...] = ()
    note: str = ""                  # why this case is in the set

    def __post_init__(self) -> None:
        if self.gold_decision not in (ACCEPT, REFER):
            raise ValueError(f"{self.case_id}: gold_decision must be {ACCEPT!r} or {REFER!r}")
        if self.gold_decision == REFER and not self.gold_failing_gates:
            # A case labelled REFER without naming the gate is untestable: you
            # can pass it by referring for the wrong reason, which is a failure
            # mode you would then never see.
            raise ValueError(
                f"{self.case_id}: a REFER case must name the gate(s) that should fire. "
                f"Referring for the wrong reason is not a pass."
            )
        if self.gold_decision == ACCEPT and self.gold_failing_gates:
            raise ValueError(f"{self.case_id}: an ACCEPT case cannot expect failing gates")

    @property
    def is_clean(self) -> bool:
        return self.gold_decision == ACCEPT


@dataclass
class Dataset:
    """A named set of cases, with the invariants an eval set needs."""

    name: str
    cases: list[EvalCase] = field(default_factory=list)

    def add(self, case: EvalCase) -> Dataset:
        if any(c.case_id == case.case_id for c in self.cases):
            raise ValueError(f"duplicate case_id: {case.case_id}")
        self.cases.append(case)
        return self

    def __iter__(self) -> Iterator[EvalCase]:
        return iter(self.cases)

    def __len__(self) -> int:
        return len(self.cases)

    def filter(self, *, kind: str | None = None, decision: str | None = None) -> Dataset:
        out = Dataset(name=f"{self.name}[{kind or 'all'}/{decision or 'all'}]")
        for c in self.cases:
            if kind and c.kind != kind:
                continue
            if decision and c.gold_decision != decision:
                continue
            out.add(c)
        return out

    def kinds(self) -> list[str]:
        return sorted({c.kind for c in self.cases})

    def balance(self) -> dict[str, int]:
        """How many clean vs flawed. Print it; an unbalanced set flatters."""
        return {
            "total": len(self.cases),
            ACCEPT: sum(1 for c in self.cases if c.gold_decision == ACCEPT),
            REFER: sum(1 for c in self.cases if c.gold_decision == REFER),
        }

    def validate(self) -> Dataset:
        """Checks that catch a dataset which cannot measure what you think.

        Both classes must be represented. A set of only clean cases measures
        nothing about safety; a set of only flawed cases cannot detect a system
        that refers everything, which is trivially "safe" and useless.
        """
        b = self.balance()
        if b[ACCEPT] == 0:
            raise ValueError(
                f"{self.name}: no ACCEPT cases. Without them you cannot detect a "
                f"system that refers everything — which passes every safety metric "
                f"and is worthless."
            )
        if b[REFER] == 0:
            raise ValueError(
                f"{self.name}: no REFER cases. Without them you are measuring "
                f"extraction, not judgement."
            )
        for c in self.cases:
            missing = [f for f in c.gold_fields if not str(f).strip()]
            if missing:
                raise ValueError(f"{c.case_id}: empty gold field name")
        return self
