"""The eval harness, and the meta-question: can it tell good from bad?

An eval you have only run against one system tells you nothing about whether
the *metrics* work. These tests run known-good and known-bad extractors through
it and assert the report separates them — and, specifically, that it separates
them in the direction that matters.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dataset import BUILDS, DATASET
from personas import PERSONAS
from run_evals import make_runner

from pyligent_agents.evals import (
    ACCEPT,
    REFER,
    CaseOutcome,
    Dataset,
    EvalCase,
    compare,
    run_eval,
    score,
)
from pyligent_agents.verify import quote_is_in

EVALS = Path(__file__).resolve().parents[1] / "evals"


# --- the dataset must be trustworthy before anything it measures is -------


def test_the_dataset_is_balanced_and_valid():
    """Both classes present, or the numbers cannot mean what you think."""
    DATASET.validate()
    b = DATASET.balance()
    assert b["total"] == 15 and b[ACCEPT] == 7 and b[REFER] == 8
    assert DATASET.kinds() == ["csa", "invoice", "kyc"]


def test_every_gold_quote_is_verbatim_in_its_document():
    """Labels and text are generated together; this proves they stayed together."""
    for case in DATASET:
        for name, quote in BUILDS[case.case_id].quotes.items():
            assert quote_is_in(case.source_text, quote), f"{case.case_id}.{name}"


def test_a_refer_case_must_name_the_gate_it_expects():
    """Referring for the wrong reason is not a pass."""
    with pytest.raises(ValueError, match="must name the gate"):
        EvalCase("x", "k", "doc", {}, REFER)


def test_an_accept_case_cannot_expect_failing_gates():
    with pytest.raises(ValueError, match="cannot expect failing gates"):
        EvalCase("x", "k", "doc", {}, ACCEPT, ("g",))


def test_a_single_class_dataset_is_rejected():
    # All clean: you never find out whether the system can spot a problem.
    only_clean = Dataset("clean-only").add(EvalCase("a", "k", "d", {}, ACCEPT))
    with pytest.raises(ValueError, match="measuring extraction, not judgement"):
        only_clean.validate()

    # All flawed: a system that refuses everything scores 100%.
    only_flawed = Dataset("flawed-only").add(EvalCase("b", "k", "d", {}, REFER, ("g",)))
    with pytest.raises(ValueError, match="refers everything"):
        only_flawed.validate()


# --- the metrics ---------------------------------------------------------


def _outcome(case_id, kind, decision, gates=(), extracted=None, evidence=None):
    return CaseOutcome(case_id, kind, decision, tuple(gates), extracted or {},
                       evidence or {})


def test_false_accepts_and_false_refers_are_counted_separately():
    ds = (Dataset("t")
          .add(EvalCase("clean", "k", "d", {}, ACCEPT))
          .add(EvalCase("flawed", "k", "d", {}, REFER, ("g1",))))
    r = score(ds, [_outcome("clean", "k", REFER), _outcome("flawed", "k", ACCEPT)],
              system="bad")

    assert r.false_refer == 1 and r.false_accept == 1
    assert r.false_accept_rate == 1.0 and r.false_refer_rate == 1.0
    # The average hides both, which is exactly why it is not the headline.
    assert r.decision_accuracy == 0.0


def test_referring_for_the_wrong_reason_is_recorded():
    ds = Dataset("t").add(EvalCase("a", "k", "d", {}, ACCEPT)).add(
        EvalCase("b", "k", "d", {}, REFER, ("expected_gate",)))
    r = score(ds, [_outcome("a", "k", ACCEPT),
                   _outcome("b", "k", REFER, ("some_other_gate",))], system="s")
    assert r.true_refer == 1 and r.wrong_reason == 1
    assert r.attribution_accuracy == 0.0


def test_numbers_compare_numerically_but_names_do_not_fuzzy_match():
    """A one-letter name difference is the finding, not noise to normalise away."""
    ds = (Dataset("t")
          .add(EvalCase("n", "k", "d", {"amount": 500_000}, ACCEPT))
          .add(EvalCase("s", "k", "d", {"name": "Jonathan"}, REFER, ("g",))))
    r = score(ds, [_outcome("n", "k", ACCEPT, extracted={"amount": "500,000"}),
                   _outcome("s", "k", REFER, ("g",), extracted={"name": "Jonathon"})],
              system="s")
    assert r.fields.correct == 1 and r.fields.wrong == 1


def test_an_errored_case_never_looks_safe():
    """A crashing system must not score as cautious."""
    ds = (Dataset("t").add(EvalCase("a", "k", "d", {}, ACCEPT))
                      .add(EvalCase("b", "k", "d", {}, REFER, ("g",))))
    r = score(ds, [_outcome("a", "k", ACCEPT),
                   CaseOutcome("b", "k", REFER, error="boom")], system="s")
    assert r.errored == 1
    assert r.wrong_reason == 1, "an errored flawed case is not a correctly-reasoned refer"


def test_the_runner_turns_a_crash_into_a_result_not_a_stopped_run():
    def explode(_case):
        raise RuntimeError("boom")

    r = run_eval(DATASET, explode, system="crashy")
    assert r.n == len(DATASET) and r.errored == len(DATASET)


# --- the meta-test: does the harness separate good from bad? -------------


@pytest.fixture(scope="module")
def reports():
    return {name: run_eval(DATASET, make_runner(name), system=name) for name in PERSONAS}


def test_a_faithful_extractor_scores_perfectly(reports):
    r = reports["faithful"]
    assert r.false_accept == 0 and r.false_refer == 0
    assert r.field_accuracy == 1.0 and r.evidence_validity == 1.0


def test_invented_citations_are_caught_and_cost_nothing_in_safety(reports):
    """Right answers, unprovable. Requiring citations misses this; checking finds it."""
    r = reports["paraphraser"]
    assert r.field_accuracy == 1.0, "the values are correct"
    assert r.evidence_validity == 0.0, "and not one of them is provable from the page"
    assert r.false_accept == 0, "nothing unprovable was accepted"
    assert r.false_refer == 7, "every clean document was held back instead"


def test_a_helpful_extractor_is_the_dangerous_one(reports):
    """The finding this whole harness exists to make visible.

    A model that silently corrects the inconsistencies it was meant to report
    loses almost nothing on field accuracy and lets flawed documents through.
    """
    helpful, sloppy = reports["helpful"], reports["sloppy"]

    assert helpful.false_accept >= 4, "it corrected documents that should have been referred"
    assert sloppy.false_accept == 0

    # ...and a naive ranking by field accuracy puts the dangerous one ABOVE the
    # safe one. That inversion is the argument for the report's shape.
    assert helpful.field_accuracy > sloppy.field_accuracy
    assert helpful.field_accuracy > 0.9, "the damage barely shows in field accuracy"

    # And it corrupts a CLEAN document too: "fixing" the zero-Threshold VM CSA
    # by swapping the two values defeats the transposition gate — and is then
    # caught by a different one, because a rounding multiple of 100,000 against
    # an MTA of 0 is incoherent. Defence in depth, demonstrated.
    corrupted = next(o for o in helpful.outcomes if o.case_id == "csa/vm-zero-threshold")
    assert corrupted.failing_gates == ("rounding_no_coarser_than_mta",)


def test_placeholders_and_dropped_fields_are_caught(reports):
    r = reports["sloppy"]
    assert r.false_accept == 0
    assert r.field_accuracy < 0.9
    assert r.false_refer == 7, "every clean document was refused for missing data"


def test_no_persona_is_scored_identically_to_another(reports):
    """If the metrics cannot separate four known-different systems, they are wrong."""
    signatures = {
        (round(r.field_accuracy, 3), round(r.evidence_validity, 3),
         r.false_accept, r.false_refer)
        for r in reports.values()
    }
    assert len(signatures) == len(reports)


# --- regression detection ------------------------------------------------


def test_any_new_false_accept_is_a_regression(reports):
    baseline = reports["faithful"].to_dict()
    regressions = compare(baseline, reports["helpful"])
    assert any("false accepts: 0 -> " in r for r in regressions)
    assert any("false_accept_rate" in r for r in regressions)


def test_an_unchanged_system_reports_no_regression(reports):
    assert compare(reports["faithful"].to_dict(), reports["faithful"]) == []


def test_small_field_accuracy_noise_is_tolerated_but_safety_is_not():
    ds = Dataset("t").add(EvalCase("a", "k", "d", {}, ACCEPT)).add(
        EvalCase("b", "k", "d", {}, REFER, ("g",)))
    base = score(ds, [_outcome("a", "k", ACCEPT), _outcome("b", "k", REFER, ("g",))],
                 system="base").to_dict()
    base["metrics"]["field_accuracy"] = 1.0

    slightly_worse = score(ds, [_outcome("a", "k", ACCEPT), _outcome("b", "k", REFER, ("g",))],
                           system="now")
    slightly_worse.fields.correct, slightly_worse.fields.wrong = 99, 1  # 99%
    assert compare(base, slightly_worse) == [], "1% drift must not trip the check"


# --- the shipped baselines stay honest ------------------------------------


@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_the_committed_baseline_still_matches_the_persona(persona, reports):
    """A baseline nobody re-checks is a baseline that has quietly rotted."""
    from pyligent_agents.evals import load_baseline

    base = load_baseline(EVALS / "baselines" / f"{persona}.json")
    assert base is not None, f"no baseline committed for {persona}"
    assert compare(base, reports[persona]) == []
