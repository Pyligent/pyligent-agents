#!/usr/bin/env python3
"""Run the document-intake eval.

    python evals/run_evals.py                      # every persona, compared
    python evals/run_evals.py --system faithful    # one system, in detail
    python evals/run_evals.py --by-kind            # per document type
    python evals/run_evals.py --baseline           # record the current numbers
    python evals/run_evals.py --check              # fail if worse than baseline

Offline by default: the "systems" are scripted personas, which is how the
harness itself stays honest — if the report cannot separate a known-good
extractor from a known-bad one, the report is wrong.

With ANTHROPIC_API_KEY set and --live, the same dataset runs against a real
model through the same pipeline.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "src"), str(HERE.parent / "examples")]
os.environ.setdefault("PYLIGENT_AGENTS_BACKEND", "scripted")

from dataset import DATASET  # noqa: E402
from document_intake import app  # noqa: E402
from personas import PERSONAS, build_policy  # noqa: E402

from pyligent_agents.evals import (  # noqa: E402
    CaseOutcome,
    compare,
    load_baseline,
    render_comparison,
    run_eval,
    save_baseline,
)
from pyligent_agents.testing import build_test_stack  # noqa: E402
from pyligent_agents.verify import quote_is_in  # noqa: E402

BASELINE_DIR = HERE / "baselines"


def make_runner(persona: str, *, live: bool = False):
    """Run ONE case through the real intake graph and report what happened.

    Note what this does not do: it does not re-implement the pipeline. It calls
    the same `build_graph` the application uses, with the case's document
    substituted. Evaluating a re-implementation evaluates the wrong thing.
    """
    import tempfile

    def run_one(case) -> CaseOutcome:
        policy = None if live else build_policy(case, persona)
        stack = build_test_stack(policy, tools=None,
                                 state_dir=tempfile.mkdtemp(prefix="eval-"))
        graph = app.build_graph(stack.harness, case.kind, source=case.source_text,
                                document_id=case.case_id)
        result = stack.runner(graph).start(f"eval {case.case_id}", {})

        artifact = result.state.get("artifact") or {}
        fields = artifact.get("fields") or {}
        report = result.state.get("gate_report") or {}

        return CaseOutcome(
            case_id=case.case_id, kind=case.kind,
            decision="accept" if report.get("passed") else "refer",
            failing_gates=tuple(report.get("failed", [])),
            extracted={k: v.get("value") for k, v in fields.items()},
            # Check every citation ourselves rather than trusting the gate's
            # verdict — the eval must be able to measure a control, not just
            # inherit its opinion.
            evidence_checked={
                k: quote_is_in(case.source_text, v.get("evidence_quote", ""))
                for k, v in fields.items()},
            cost_usd=stack.cost()["spent_usd"],
            model_calls=stack.cost()["calls"],
        )

    return run_one


def _rule(t: str, c: str = "=") -> None:
    print(f"\n{t}\n{c * max(len(t), 74)}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--system", default="", choices=[*PERSONAS, ""],
                   help="one persona, reported in detail")
    p.add_argument("--by-kind", action="store_true", help="split by document type")
    p.add_argument("--baseline", action="store_true", help="record the current numbers")
    p.add_argument("--check", action="store_true", help="fail if worse than baseline")
    p.add_argument("--live", action="store_true", help="run against a real model")
    p.add_argument("--note", default="", help="note to store with a baseline")
    a = p.parse_args(argv)

    if a.live and not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        print("--live needs ANTHROPIC_API_KEY.", file=sys.stderr)
        return 2

    system = a.system or ("live" if a.live else "")
    _rule(f"DATASET  {DATASET.name}")
    b = DATASET.balance()
    print(f"  {b['total']} cases: {b['accept']} clean, {b['refer']} flawed"
          f"   kinds: {', '.join(DATASET.kinds())}")

    # --- one system, in detail -------------------------------------------
    if system:
        report = run_eval(DATASET, make_runner(system, live=a.live), system=system)
        _rule(f"SYSTEM  {system}")
        print(report.render())

        if a.by_kind:
            _rule("BY DOCUMENT TYPE", "-")
            for kind in DATASET.kinds():
                sub = DATASET.filter(kind=kind)
                outs = [o for o in report.outcomes if o.kind == kind]
                wrong = sum(1 for o in outs
                            if (o.decision == "accept") !=
                            (next(c for c in sub if c.case_id == o.case_id).is_clean))
                print(f"  {kind:<10} {len(outs)} cases, {wrong} wrong decision(s)")

        path = BASELINE_DIR / f"{system}.json"
        if a.baseline:
            save_baseline(report, path, note=a.note)
            print(f"\n  baseline written: {path.relative_to(HERE.parent)}")
            return 0

        base = load_baseline(path)
        if base:
            _rule("VERSUS BASELINE", "-")
            print(render_comparison(base, report))
            regressions = compare(base, report)
            if regressions:
                print("\n  REGRESSIONS")
                for r in regressions:
                    print(f"    · {r}")
                if a.check:
                    return 1
            elif a.check:
                print("\n  no regressions")
        elif a.check:
            print(f"\n  no baseline at {path}; run with --baseline first", file=sys.stderr)
            return 2
        return 0

    # --- every persona, compared -----------------------------------------
    _rule("SYSTEMS COMPARED")
    reports = {name: run_eval(DATASET, make_runner(name), system=name)
               for name in PERSONAS}

    print(f"\n  {'SYSTEM':<14}{'FALSE ACC':>10}{'FALSE REF':>10}"
          f"{'FIELD ACC':>11}{'EVIDENCE':>10}{'REASON':>9}")
    print("  " + "-" * 64)
    for name, r in reports.items():
        print(f"  {name:<14}{r.false_accept:>7}    {r.false_refer:>7}   "
              f"{r.field_accuracy:>10.1%}{r.evidence_validity:>10.1%}"
              f"{r.attribution_accuracy:>9.1%}")

    _rule("READ THIS TABLE CAREFULLY", "-")

    by_fields = sorted(reports.values(), key=lambda r: -r.field_accuracy)
    unsafe = [r for r in reports.values() if r.false_accept]

    print("  Ranked by field accuracy — the number most eval harnesses headline:")
    for i, r in enumerate(by_fields, 1):
        flag = f"   <-- {r.false_accept} FALSE ACCEPT(S)" if r.false_accept else ""
        print(f"    {i}. {r.system:<14}{r.field_accuracy:>7.1%}{flag}")

    if unsafe:
        worst = max(unsafe, key=lambda r: r.false_accept)
        beaten = [r.system for r in by_fields
                  if r.field_accuracy < worst.field_accuracy and not r.false_accept]
        print(
            f"\n  '{worst.system}' ranks above {len(beaten)} safe system(s) on field\n"
            f"  accuracy — {', '.join(beaten) or 'none'} — and it is the only one that let a\n"
            f"  flawed document through. It lost {1 - worst.field_accuracy:.1%} of fields and\n"
            f"  gained {worst.false_accept} false accepts, because it quietly corrected the\n"
            f"  inconsistencies it was supposed to report.\n\n"
            f"  A single accuracy figure ranks it second. The business ranks it last.\n"
            f"  That is why this report separates the two decision errors and refuses to\n"
            f"  lead with their average.")

    blind = [r for r in reports.values() if r.evidence_validity < 0.5]
    for r in blind:
        print(
            f"\n  '{r.system}' is the other trap: {r.field_accuracy:.0%} field accuracy and\n"
            f"  {r.evidence_validity:.0%} evidence validity. Every answer is right and not one\n"
            f"  of them can be proved from the page. Requiring citations would not have\n"
            f"  caught it; checking them did — all {r.false_refer} clean documents were\n"
            f"  correctly held back rather than accepted on unverifiable evidence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
