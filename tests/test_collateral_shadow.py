"""The collateral chain: constraints, certification, and the shadow guarantee.

The safety property a bank buys in a shadow trial is "it cannot touch anything".
That is worth exactly as much as the test that proves it, so the first test here
is the one that would fail if shadow mode ever became able to write.
"""

from __future__ import annotations

from datetime import date

import pytest
from collateral.app import SYSTEM_OF_RECORD, run, shadow_hooks, vm_policy
from collateral.constraints import build_pack, certify, find_unsupported
from collateral.shadow import reconcile
from document_intake.policy import build_policy
from shopdesk.tools import build_registry

from pyligent_agents import Phase, ToolUse
from pyligent_agents.testing import build_test_stack

# --- the guarantee --------------------------------------------------------


@pytest.mark.parametrize("tool,args", [
    ("issue_refund", {"order_id": "A-1207", "amount": 1.0}),
    ("email_customer", {"order_id": "A-1207", "body": "hello"}),
])
def test_shadow_mode_cannot_reach_a_tool_with_an_external_effect(tool, args):
    """The whole basis of a shadow trial, as an assertion.

    Note there IS an approver attached. `deny_restricted_without_approval` would
    let this through; the shadow hook denies the tier outright. The guarantee
    must not depend on how the stack happened to be configured.
    """
    stack = build_test_stack(build_policy("csa"), tools=build_registry(),
                             hooks=shadow_hooks())
    outcome = stack.harness.run_tool(
        ToolUse(id="t1", name=tool, input=args),
        phase=Phase.ACT,
        approver=lambda ctx: True,          # a human saying yes...
    )
    assert outcome.denied, f"{tool} was reachable in shadow mode"
    assert "shadow mode" in outcome.content.lower()


def test_read_only_tools_still_work_in_shadow_mode():
    """A mode that blocks everything is not a mode, it is an outage."""
    stack = build_test_stack(build_policy("csa"), tools=build_registry(),
                             hooks=shadow_hooks())
    outcome = stack.harness.run_tool(
        ToolUse(id="t1", name="get_order", input={"order_id": "A-1207"}),
        phase=Phase.ACT,
    )
    assert not outcome.denied and not outcome.is_error


def test_a_shadow_run_completes_and_reconciles():
    stack = build_test_stack(build_policy("csa"), hooks=shadow_hooks())
    bundle, pack, report = run(stack)
    assert bundle["artifact"] is not None
    assert pack.constraints
    assert report.checked == len(SYSTEM_OF_RECORD)


# --- the drift finding ----------------------------------------------------


def test_the_drifted_margin_system_is_caught_with_the_clause_that_settles_it():
    """The finding a trial exists to produce.

    The parties amended to a VM CSA and the Threshold went to zero. The margin
    system still holds 5,000,000. Nothing in the margin system looks wrong —
    every field is populated and plausible — and no reconciliation of the system
    against itself would ever find it.
    """
    stack = build_test_stack(vm_policy(), hooks=shadow_hooks())
    _, _, report = run(stack, drifted=True)

    by_field = {d.field: d for d in report.material}
    assert "threshold" in by_field, "the stale Threshold was not caught"

    threshold = by_field["threshold"]
    assert threshold.ours == 0 and threshold.theirs == 5_000_000
    # And the finding is checkable by a counterparty, not merely asserted.
    assert "USD 0" in threshold.clause


def test_a_matching_system_produces_no_discrepancies():
    """No invented disagreements. The fastest way to lose a trial."""
    stack = build_test_stack(build_policy("csa"), hooks=shadow_hooks())
    _, _, report = run(stack)
    assert report.agrees and not report.material


def test_a_field_the_system_does_not_hold_is_not_a_discrepancy():
    artifact = {"document_id": "D", "_source_text": "x",
                "fields": {"threshold": {"value": 0, "evidence_quote": "x"}}}
    report = reconcile(artifact, {"mta": 500_000})
    assert report.checked == 0 and report.agrees


def test_formatted_and_numeric_values_compare_equal():
    artifact = {"document_id": "D", "_source_text": "x",
                "fields": {"mta": {"value": 500_000, "evidence_quote": "x"}}}
    assert reconcile(artifact, {"mta": "USD 500,000"}).agrees


# --- constraints and certification ---------------------------------------


def _artifact(**fields):
    base = {"base_currency": "USD", "threshold": 0, "mta": 500_000}
    base.update(fields)
    src = "source text"
    return {
        "document_id": "DOC-1", "_source_text": src,
        "fields": {k: {"value": v, "evidence_quote": src} for k, v in base.items()},
        "eligible_collateral": [{"description": "Cash", "valuation_pct": 100}],
    }


def test_every_constraint_carries_the_clause_behind_it():
    """A recommendation is defensible only if each constraint traces to language."""
    pack = build_pack(_artifact(), as_of=date(2026, 8, 26))
    assert pack.constraints
    for c in pack.constraints:
        assert c.provenance, f"{c.id} has no clause"
        assert all(p.verified for p in c.provenance), f"{c.id} cites an unverified quote"


def test_an_unverifiable_clause_blocks_certification():
    art = _artifact()
    art["fields"]["threshold"]["evidence_quote"] = "words that are not in the source"
    record = certify(build_pack(art))
    assert not record.certified and not record.provenance_complete
    assert any("could not be confirmed" in r for r in record.reasons)


def test_a_pack_missing_a_required_constraint_kind_is_not_certified():
    art = _artifact()
    del art["fields"]["mta"]                      # no transfer constraint
    record = certify(build_pack(art))
    assert not record.certified
    assert any("transfer" in r for r in record.reasons)


def test_terms_that_cannot_be_expressed_are_declared_and_block_certification():
    """The honest failure. A pack that silently drops a term looks complete."""
    art = _artifact()
    art["_source_text"] = ("... the Valuation Agent shall determine ... "
                           "a ratings trigger applies ...")
    for entry in art["fields"].values():
        entry["evidence_quote"] = art["_source_text"]

    unsupported = find_unsupported(art)
    assert len(unsupported) >= 2
    record = certify(build_pack(art))
    assert not record.certified
    assert any("not expressible" in r for r in record.reasons)

    # ...and a human may accept that risk explicitly, which is different from
    # never being told.
    assert certify(build_pack(art), allow_unsupported=True).certified


def test_valuation_percentages_stay_percentages_in_the_pack():
    """98% valuation is a 2% haircut. Storing it the wrong way misprices the book."""
    pack = build_pack(_artifact())
    valuation = pack.of_kind("valuation")[0]
    assert valuation.expression["op"] == "valuation_pct"
    assert valuation.expression["pct"] == 100


def test_a_shared_election_fans_out_to_both_parties():
    pack = build_pack(_artifact())
    parties = {c.party for c in pack.of_kind("threshold")}
    assert parties == {"PARTY_1", "PARTY_2"}


def test_the_pack_is_json_serialisable():
    """It is a hand-off artifact. If it will not serialise, it is not one."""
    import json
    pack = build_pack(_artifact())
    assert json.loads(pack.to_json())["document_id"] == "DOC-1"
