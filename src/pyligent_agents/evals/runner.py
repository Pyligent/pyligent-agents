"""Run a system over a dataset and score it.

The runner knows nothing about your pipeline. You supply `run_one`, a function
from an `EvalCase` to a `CaseOutcome`; the runner handles iteration, timing,
error containment and scoring.

That boundary is deliberate. An eval harness that assumes the shape of your
pipeline is one you fight the first time your pipeline changes.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

from .case import Dataset, EvalCase
from .metrics import CaseOutcome, Report, score

RunOne = Callable[[EvalCase], CaseOutcome]


def run_eval(
    dataset: Dataset,
    run_one: RunOne,
    *,
    system: str,
    on_case: Callable[[EvalCase, CaseOutcome], None] | None = None,
    only: Iterable[str] | None = None,
) -> Report:
    """Run every case, score the lot, return a report.

    A case that raises becomes an errored outcome rather than stopping the run —
    an eval that dies on case 3 of 40 tells you nothing about cases 4 to 40, and
    the crash is itself a finding worth recording.
    """
    dataset.validate()
    wanted = set(only) if only else None
    outcomes: list[CaseOutcome] = []

    for case in dataset:
        if wanted and case.case_id not in wanted:
            continue
        started = time.perf_counter()
        try:
            outcome = run_one(case)
        except Exception as exc:  # noqa: BLE001 - a crash is a result
            outcome = CaseOutcome(
                case_id=case.case_id, kind=case.kind, decision="refer",
                error=f"{type(exc).__name__}: {exc}",
            )
        if not outcome.duration_s:
            outcome.duration_s = time.perf_counter() - started
        outcomes.append(outcome)
        if on_case:
            on_case(case, outcome)

    return score(dataset, outcomes, system=system)


def run_by_kind(dataset: Dataset, run_one: RunOne, *, system: str) -> dict[str, Report]:
    """One report per document/task type, plus `_all`.

    Aggregate numbers hide the case where one document type is fine and another
    is quietly broken — which is the usual shape of a real regression.
    """
    reports: dict[str, Report] = {}
    for kind in dataset.kinds():
        subset = dataset.filter(kind=kind)
        # Per-kind subsets are often single-class; score them without the
        # both-classes-present check that a full dataset must satisfy.
        outcomes = [run_one(c) for c in subset]
        reports[kind] = score(subset, outcomes, system=f"{system}/{kind}")
    reports["_all"] = run_eval(dataset, run_one, system=system)
    return reports


def compare(baseline: dict[str, Any], current: Report, *,
            tolerances: dict[str, float] | None = None) -> list[str]:
    """Regression check against a saved baseline.

    Defaults are asymmetric on purpose. A single new false accept is a
    regression, full stop — there is no tolerance band for letting a bad
    document through. Everything else gets a small one, because a metric that
    trips on noise gets muted, and a muted check is not a check.
    """
    tol = {"field_accuracy": 0.02, "evidence_validity": 0.0,
           "false_accept_rate": 0.0, "false_refer_rate": 0.05,
           "attribution_accuracy": 0.05, **(tolerances or {})}
    base = baseline.get("metrics", {})
    now = current.to_dict()["metrics"]
    regressions: list[str] = []

    # Higher is better.
    for name in ("field_accuracy", "evidence_validity", "attribution_accuracy"):
        if name not in base:
            continue
        drop = base[name] - now[name]
        if drop > tol[name]:
            regressions.append(
                f"{name}: {base[name]:.1%} -> {now[name]:.1%} (down {drop:.1%}, "
                f"tolerance {tol[name]:.1%})")

    # Lower is better.
    for name in ("false_accept_rate", "false_refer_rate"):
        if name not in base:
            continue
        rise = now[name] - base[name]
        if rise > tol[name]:
            regressions.append(
                f"{name}: {base[name]:.1%} -> {now[name]:.1%} (up {rise:.1%}, "
                f"tolerance {tol[name]:.1%})")

    base_fa = baseline.get("counts", {}).get("false_accept")
    if base_fa is not None and current.false_accept > base_fa:
        regressions.append(
            f"false accepts: {base_fa} -> {current.false_accept}. Any increase is a "
            f"regression; there is no acceptable number of flawed documents let "
            f"through.")
    return regressions
