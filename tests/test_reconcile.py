"""Reconciliation: agreement against stored terms, and what must not be claimed.

The load-bearing test here is `test_a_fabricated_citation_never_raises...`. Telling
a collateral team their system contradicts a signed agreement, on the strength of a
sentence a model invented, is the single worst output this software could produce —
worse than silence, because it spends the credibility a shadow trial exists to build.
"""

from __future__ import annotations

from pyligent_agents.reconcile import (
    AGREES,
    DISCREPANCY,
    UNVERIFIED,
    reconcile,
    render,
)

DOC = (
    'Paragraph 13. "Threshold" means with respect to each party: USD 0. '
    "The Minimum Transfer Amount is USD 500,000. "
    "The Base Currency is United States Dollars."
)


def _fields(**over):
    base = {
        "threshold": {"value": "0",
                      "quote": '"Threshold" means with respect to each party: USD 0.'},
        "mta": {"value": "USD 500,000",
                "quote": "The Minimum Transfer Amount is USD 500,000."},
    }
    base.update(over)
    return base


def test_a_matching_system_produces_no_exceptions():
    rec = reconcile(DOC, _fields(), {"threshold": 0, "mta": 500_000})
    assert rec.agrees and not rec.discrepancies
    assert all(r.state == AGREES for r in rec.results)


def test_formatting_differences_are_not_discrepancies():
    """`500000`, `'500,000'` and `'USD 500,000'` are the same stored term."""
    rec = reconcile(DOC, _fields(), {"mta": "USD 500,000"})
    assert rec.agrees, [r.to_dict() for r in rec.results]


def test_a_real_drift_is_raised_and_marked_material():
    rec = reconcile(DOC, _fields(), {"threshold": 5_000_000})
    assert len(rec.discrepancies) == 1
    found = rec.discrepancies[0]
    assert found.field == "threshold" and found.material
    assert found.ours == "0" and found.theirs == 5_000_000
    assert "Threshold" in found.clause          # the clause that settles it


def test_a_fabricated_citation_never_raises_an_exception_against_the_system():
    """The rule that makes this safe to run beside a real book."""
    invented = {"threshold": {"value": "0",
                              "quote": "The Threshold shall at all times be nil."}}
    rec = reconcile(DOC, invented, {"threshold": 5_000_000})
    assert not rec.discrepancies, "raised an exception on evidence that does not exist"
    assert len(rec.unverified) == 1
    assert rec.unverified[0].state == UNVERIFIED
    assert not rec.agrees
    assert "does not appear" in rec.unverified[0].reason


def test_a_silently_repaired_value_is_also_unverified():
    """The citation is genuine and states a different number. Not a system finding."""
    repaired = {"mta": {"value": "USD 250,000",
                        "quote": "The Minimum Transfer Amount is USD 500,000."}}
    rec = reconcile(DOC, repaired, {"mta": 250_000})
    assert not rec.discrepancies
    assert len(rec.unverified) == 1


def test_fields_the_system_does_not_hold_are_not_findings():
    """Inventing disagreements is the fastest way to end a trial."""
    rec = reconcile(DOC, _fields(), {"threshold": 0})
    assert len(rec.results) == 1


def test_metadata_columns_are_not_compared():
    """An export carries identifiers; comparing them reads like a finding."""
    rec = reconcile(DOC, _fields(), {"counterparty": "Atlas Bank", "document": "X",
                                     "threshold": 0})
    assert [r.field for r in rec.results] == ["threshold"]
    assert not rec.notes, rec.notes


def test_comparing_nothing_is_not_agreement():
    """A vacuous truth is the one claim this project must never print."""
    rec = reconcile(DOC, {}, {"threshold": 0})
    assert not rec.results
    assert not rec.agrees, "claimed agreement having compared nothing"
    assert "NOTHING COMPARED" in render(rec)


def test_the_report_states_that_nothing_was_written():
    """A shadow run's core promise, in the artifact a reviewer reads."""
    assert "Nothing was written" in render(reconcile(DOC, _fields(), {"threshold": 0}))


def test_discrepancies_carry_impact_and_are_ordered_material_first():
    fields = _fields(governing_law={"value": "New York", "quote": "Paragraph 13."})
    rec = reconcile(DOC, fields, {"governing_law": "English", "threshold": 5_000_000})
    states = {r.field: r.state for r in rec.results}
    assert states["threshold"] == DISCREPANCY
    assert states["governing_law"] == DISCREPANCY
    # `discrepancies` keeps the export's order; `render` is what an analyst reads,
    # and that puts the material one first regardless of where it appeared.
    assert {r.field for r in rec.material} == {"threshold"}
    assert rec.material[0].impact
    body = render(rec)
    assert body.index("MATERIAL \u00b7 threshold") < body.index("minor \u00b7 governing_law")
