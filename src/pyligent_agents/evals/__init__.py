"""Evaluation — measuring whether the answers are right, not just whether the
control flow held.

`pyligent_agents.testing` proves your guardrails work: turn caps fire, tool
errors are recovered from, side effects happen once. It runs offline against a
scripted model and it will **never** tell you a prompt got worse.

This module is the other half. You supply a labelled dataset and a function that
runs your pipeline over one case; it reports the numbers that matter for a system
that gates a consequential action — and it reports the two decision errors
separately, because a false accept and a false refer are not the same thing and
averaging them hides the one you care about.

    from pyligent_agents.evals import Dataset, EvalCase, CaseOutcome, run_eval

    report = run_eval(my_dataset, run_one=my_pipeline, system="v3-prompt")
    print(report.render())
"""

from .baseline import load_baseline, render_comparison, save_baseline
from .case import ACCEPT, REFER, Dataset, EvalCase
from .metrics import CaseOutcome, FieldScore, Report, score
from .runner import compare, run_by_kind, run_eval

__all__ = [
    "ACCEPT", "CaseOutcome", "Dataset", "EvalCase", "FieldScore", "REFER",
    "Report", "compare", "load_baseline", "render_comparison", "run_by_kind",
    "run_eval", "save_baseline", "score",
]
