"""Scoring, and the one number people get wrong.

Most eval harnesses report a single accuracy figure. For anything that gates a
consequential action, that figure hides the distinction you care about most:

    FALSE ACCEPT   a flawed document was let through          expensive
    FALSE REFER    a clean document went to a human           annoying

They are not symmetric and averaging them is dishonest. A system that refers
everything scores 50% on a balanced set and is perfectly safe and completely
useless; a system that accepts everything scores the same and is a liability.
This module always reports the two separately, and `decision_accuracy` is
deliberately not the headline.

The second thing it separates: **field accuracy and decision safety can
diverge.** A model that silently "corrects" an inconsistency it was supposed to
report scores *better* on field accuracy and worse on everything that matters.
That pattern is common enough to be worth designing the report around.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .case import ACCEPT, REFER, Dataset, EvalCase


@dataclass
class CaseOutcome:
    """What the system under evaluation actually did with one case."""

    case_id: str
    kind: str
    decision: str                              # ACCEPT | REFER
    failing_gates: tuple[str, ...] = ()
    extracted: dict[str, Any] = field(default_factory=dict)
    evidence_checked: dict[str, bool] = field(default_factory=dict)
    cost_usd: float = 0.0
    duration_s: float = 0.0
    model_calls: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _same(gold: Any, got: Any) -> bool:
    """Compare a gold value with an extracted one, tolerantly but not loosely.

    Numbers compare numerically so 500000 and "500,000" match. Strings compare
    case-insensitively with whitespace collapsed, so "Jonathan  Whitfield" and
    "jonathan whitfield" match — but "Jonathon" and "Jonathan" do NOT, and that
    is deliberate. On a KYC file a one-letter difference is the finding, and an
    eval that fuzzy-matches it away cannot see the thing you built it for.
    """
    if gold is None or got is None:
        return gold is got
    if isinstance(gold, bool) or isinstance(got, bool):
        return gold is got
    try:
        gn = float(str(gold).replace(",", "").replace("£", "").replace("$", "").replace("%", ""))
        cn = float(str(got).replace(",", "").replace("£", "").replace("$", "").replace("%", ""))
        return abs(gn - cn) < 1e-6
    except (TypeError, ValueError):
        pass
    return " ".join(str(gold).split()).lower() == " ".join(str(got).split()).lower()


@dataclass
class FieldScore:
    correct: int = 0
    wrong: int = 0
    missing: int = 0
    wrong_examples: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.correct + self.wrong + self.missing

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass
class Report:
    dataset: str
    system: str
    outcomes: list[CaseOutcome] = field(default_factory=list)
    fields: FieldScore = field(default_factory=FieldScore)
    evidence_valid: int = 0
    evidence_total: int = 0

    # decisions
    true_accept: int = 0
    true_refer: int = 0
    false_accept: int = 0        # a flawed document let through
    false_refer: int = 0         # a clean document sent to a human
    right_reason: int = 0        # referred, and named the gate we expected
    wrong_reason: int = 0        # referred, for a different reason
    errored: int = 0

    cost_usd: float = 0.0
    duration_s: float = 0.0
    model_calls: int = 0

    # --- headline rates ---------------------------------------------------

    @property
    def n(self) -> int:
        return len(self.outcomes)

    @property
    def field_accuracy(self) -> float:
        return self.fields.accuracy

    @property
    def evidence_validity(self) -> float:
        """Share of citations genuinely present in the source. 1 - hallucination."""
        return self.evidence_valid / self.evidence_total if self.evidence_total else 1.0

    @property
    def false_accept_rate(self) -> float:
        """Of the flawed documents, how many were let through. The number."""
        flawed = self.false_accept + self.true_refer
        return self.false_accept / flawed if flawed else 0.0

    @property
    def false_refer_rate(self) -> float:
        """Of the clean documents, how many were needlessly sent to a human."""
        clean = self.false_refer + self.true_accept
        return self.false_refer / clean if clean else 0.0

    @property
    def decision_accuracy(self) -> float:
        """Reported, but never the headline — it averages the two asymmetric errors."""
        right = self.true_accept + self.true_refer
        return right / self.n if self.n else 0.0

    @property
    def attribution_accuracy(self) -> float:
        """When it referred, did it name the gate we expected?

        A system that refers for the wrong reason sends a human to look in the
        wrong place, which is worse than it sounds.
        """
        referred = self.right_reason + self.wrong_reason
        return self.right_reason / referred if referred else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "system": self.system,
            "cases": self.n,
            "metrics": {
                "field_accuracy": round(self.field_accuracy, 4),
                "evidence_validity": round(self.evidence_validity, 4),
                "false_accept_rate": round(self.false_accept_rate, 4),
                "false_refer_rate": round(self.false_refer_rate, 4),
                "decision_accuracy": round(self.decision_accuracy, 4),
                "attribution_accuracy": round(self.attribution_accuracy, 4),
            },
            "counts": {
                "true_accept": self.true_accept, "true_refer": self.true_refer,
                "false_accept": self.false_accept, "false_refer": self.false_refer,
                "right_reason": self.right_reason, "wrong_reason": self.wrong_reason,
                "errored": self.errored,
                "fields_correct": self.fields.correct, "fields_wrong": self.fields.wrong,
                "fields_missing": self.fields.missing,
            },
            "cost": {
                "usd": round(self.cost_usd, 6),
                "usd_per_case": round(self.cost_usd / self.n, 6) if self.n else 0.0,
                "model_calls": self.model_calls,
                "seconds": round(self.duration_s, 2),
            },
        }

    def render(self) -> str:
        d = self.to_dict()
        m, c = d["metrics"], d["counts"]
        lines = [
            f"eval: {self.dataset}   system: {self.system}   cases: {self.n}",
            "",
            "  SAFETY  (the two errors are not symmetric; never average them)",
            f"    false accepts   {c['false_accept']:>3}   "
            f"{m['false_accept_rate']:>7.1%}   a flawed document was let through",
            f"    false refers    {c['false_refer']:>3}   "
            f"{m['false_refer_rate']:>7.1%}   a clean document went to a human",
            "",
            "  QUALITY",
            f"    field accuracy       {m['field_accuracy']:>7.1%}   "
            f"({c['fields_correct']} right, {c['fields_wrong']} wrong, "
            f"{c['fields_missing']} missing)",
            f"    evidence validity    {m['evidence_validity']:>7.1%}   "
            f"share of citations genuinely in the source",
            f"    right reason         {m['attribution_accuracy']:>7.1%}   "
            f"when it referred, it named the gate we expected",
            f"    decision accuracy    {m['decision_accuracy']:>7.1%}   "
            f"(averages the two above — do not lead with this)",
            "",
            "  COST",
            f"    ${d['cost']['usd']:.5f} total, ${d['cost']['usd_per_case']:.5f}/case, "
            f"{d['cost']['model_calls']} model calls",
        ]
        if self.errored:
            lines.append(f"\n  {self.errored} case(s) errored outright")
        if self.fields.wrong_examples:
            lines.append("\n  WRONG FIELDS")
            for x in self.fields.wrong_examples[:8]:
                lines.append(f"    {x}")
        return "\n".join(lines)


def score(dataset: Dataset, outcomes: list[CaseOutcome], *, system: str) -> Report:
    """Turn raw outcomes into a report. Pure; no model, no I/O."""
    by_id = {c.case_id: c for c in dataset}
    report = Report(dataset=dataset.name, system=system, outcomes=outcomes)

    for out in outcomes:
        case = by_id.get(out.case_id)
        if case is None:
            continue
        report.cost_usd += out.cost_usd
        report.duration_s += out.duration_s
        report.model_calls += out.model_calls

        if out.error:
            report.errored += 1
            # An errored case is not a pass. Count it on the unsafe side if the
            # case was flawed, so a crashing system cannot look safe.
            if case.gold_decision == REFER:
                report.true_refer += 1
                report.wrong_reason += 1
            else:
                report.false_refer += 1
            continue

        _score_fields(case, out, report)
        _score_evidence(out, report)
        _score_decision(case, out, report)

    return report


def _score_fields(case: EvalCase, out: CaseOutcome, report: Report) -> None:
    for name, gold in case.gold_fields.items():
        if name not in out.extracted:
            report.fields.missing += 1
            continue
        got = out.extracted[name]
        if _same(gold, got):
            report.fields.correct += 1
        else:
            report.fields.wrong += 1
            report.fields.wrong_examples.append(
                f"{case.case_id}.{name}: expected {gold!r}, got {got!r}")


def _score_evidence(out: CaseOutcome, report: Report) -> None:
    for ok in out.evidence_checked.values():
        report.evidence_total += 1
        report.evidence_valid += int(ok)


def _score_decision(case: EvalCase, out: CaseOutcome, report: Report) -> None:
    if case.gold_decision == ACCEPT:
        if out.decision == ACCEPT:
            report.true_accept += 1
        else:
            report.false_refer += 1
        return

    if out.decision == ACCEPT:
        report.false_accept += 1
        return

    report.true_refer += 1
    if set(case.gold_failing_gates) & set(out.failing_gates):
        report.right_reason += 1
    else:
        report.wrong_reason += 1
