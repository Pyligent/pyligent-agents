"""The shadow run: intake → constraints → certification → reconciliation.

    python examples/run.py shadow
    python examples/run.py shadow --drift      # a margin system that has drifted

One command, and the boundary is explicit: this stops at a certified constraint
set. Allocation and optimisation are downstream and deliberately out of scope
here.
"""

from __future__ import annotations

from typing import Any

from document_intake import app as intake
from document_intake.documents import AS_OF

from collateral.constraints import ConstraintPack, build_pack, certify
from collateral.shadow import ReconciliationReport, reconcile, render
from pyligent_agents import PermissionTier
from pyligent_agents.harness import HookBus, HookPoint, ToolCallContext, default_hooks


def shadow_hooks() -> HookBus:
    """Default protections, plus one that makes a shadow run unable to write.

    `deny_restricted_without_approval` already stops a RESTRICTED tool when no
    approver is attached — but "no approver was attached" is a configuration
    accident away from being false. This denies the tier outright, so the
    guarantee does not depend on how the stack happened to be built.
    """
    def no_external_effects(ctx: ToolCallContext) -> None:
        if ctx.tier is not PermissionTier.READ_ONLY:
            ctx.deny(
                "shadow mode: this run may read the agreement and compute, and "
                "may not touch anything outside the process"
            )

    return default_hooks().on(HookPoint.PRE_TOOL, no_external_effects)


# What the counterparty's margin system currently holds. In a real trial this is
# an extract from the collateral system; here it is a fixture.
SYSTEM_OF_RECORD: dict[str, Any] = {
    "base_currency": "USD",
    "threshold": 5_000_000,
    "mta": 500_000,
    "rounding_delivery_amount": 100_000,
    "governing_law": "English law",
}

# The drift case, and the reason trials get renewed.
#
# The parties amended to a 2016 VM CSA: the Threshold went to zero, because
# variation margin is fully collateralised, and the rounding multiple was cut.
# The amendment was executed. Nobody re-keyed it. The margin system still sizes
# every call against a 5,000,000 unsecured band that no longer exists, and has
# been under-calling this counterparty ever since.
#
# Nothing in the margin system looks wrong. Every field is populated, every
# value is plausible, and no reconciliation that compares the system to itself
# will ever find it. Only reading the agreement finds it.
VM_AMENDED_CSA = """\
CREDIT SUPPORT ANNEX (VM)
to the Schedule to the ISDA Master Agreement dated as of 14 March 2019
between ATLAS GLOBAL MARKETS LTD ("Party A") and NORTHWIND BANK PLC ("Party B")
as amended by the Variation Margin Protocol Adherence dated 1 March 2017

Paragraph 11. Elections and Variables

(a) Base Currency.
    "Base Currency" means United States Dollars (USD).
    "Eligible Currency" means the Base Currency.

(b) Credit Support Obligations.
    (i)   "Threshold" means with respect to each party: USD 0.
    (ii)  "Minimum Transfer Amount" means with respect to each party: USD 500,000.
    (iii) "Rounding". The Delivery Amount will be rounded up and the Return
          Amount rounded down to the nearest integral multiple of USD 10,000.

(c) Eligible Credit Support, with the Valuation Percentage specified:
      (A) Cash in the Base Currency ................................. 100%

(e) Governing Law. This Annex is governed by English law.
"""

_VM_EXTRACTION = {
    "fields": {
        "base_currency": {"value": "USD",
            "evidence_quote": '"Base Currency" means United States Dollars (USD).'},
        "eligible_currency": {"value": "USD",
            "evidence_quote": '"Eligible Currency" means the Base Currency.'},
        "threshold": {"value": 0,
            "evidence_quote": '"Threshold" means with respect to each party: USD 0.'},
        "mta": {"value": 500_000,
            "evidence_quote": '"Minimum Transfer Amount" means with respect to each party: USD 500,000.'},
        "rounding_delivery_amount": {"value": 10_000,
            "evidence_quote": "the nearest integral multiple of USD 10,000."},
        "rounding_delivery_direction": {"value": "UP",
            "evidence_quote": "The Delivery Amount will be rounded up"},
        "rounding_return_amount": {"value": 10_000,
            "evidence_quote": "the nearest integral multiple of USD 10,000."},
        "rounding_return_direction": {"value": "DOWN",
            "evidence_quote": "the Return\n          Amount rounded down"},
        "governing_law": {"value": "English law",
            "evidence_quote": "This Annex is governed by English law."},
    },
    "eligible_collateral": [
        {"description": "Cash in the Base Currency", "valuation_pct": 100},
    ],
}


def vm_policy():
    """A scripted extraction of the amended agreement, with real quotes."""
    import json

    from document_intake.policy import APPROVALS

    from pyligent_agents.testing import ScriptedTurn, router, turn

    return router({
        "You extract the Paragraph 11":
            lambda c: ScriptedTurn(text=json.dumps(_VM_EXTRACTION)),
        "You are an independent verifier":
            lambda c: ScriptedTurn(text=json.dumps(APPROVALS["csa"])),
    }, default=turn("done"))


def run(stack, *, drifted: bool = False) -> tuple[dict, ConstraintPack, ReconciliationReport]:
    """Read the agreement, derive the constraints, compare. Write nothing."""
    graph = intake.build_graph(stack.harness, "csa",
                               source=VM_AMENDED_CSA if drifted else None)
    result = stack.runner(graph).start("intake_csa", {})
    artifact = result.state.get("artifact")
    if artifact is None:
        raise RuntimeError("intake did not produce an artifact; nothing to reconcile")

    pack = build_pack(artifact, as_of=AS_OF)
    record = certify(pack)
    # The system record is the same either way. That is the point: the stored
    # terms did not change, the agreement did.
    system = SYSTEM_OF_RECORD
    report = reconcile(artifact, system, counterparty=artifact.get("title", ""))
    return {"artifact": artifact, "certification": record, "gate_report":
            result.state.get("gate_report")}, pack, report


def render_run(bundle: dict, pack: ConstraintPack, report: ReconciliationReport) -> str:
    """Everything a trial reviewer needs on one page."""
    record = bundle["certification"]
    out = [render(report, pack), ""]
    out.append("── CONSTRAINT PACK " + "─" * 50)
    out.append(f"  constraints derived : {record.constraint_count}")
    out.append(f"  every one traceable : {'yes' if record.provenance_complete else 'NO'}")
    out.append(f"  certified for use   : {'yes' if record.certified else 'NO'}")
    for reason in record.reasons:
        out.append(f"    · {reason}")
    out.append("")
    out.append("  Allocation and optimisation are downstream of this pack and")
    out.append("  out of scope for this repository. This is the hand-off.")
    return "\n".join(out)
