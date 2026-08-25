"""Document intake — one graph, three document types.

Six nodes. Read it top to bottom and you know what happens, what it touches, and
where it stops.

    load ──▶ extract ──▶ verify ──▶ gates ──┬──▶ accept   (gates passed)
                                            └──▶ refer    (gates failed)

A Credit Support Annex, a supplier invoice and a KYC pack share nothing as
documents. As *work* they are identical: pull values out, prove each came from
the page, and refuse the result if the values do not hang together.

What differs per document is one thing — the domain gates. Five generic gates
apply to all three; each type then adds the checks a JSON schema could not make.
That is the whole argument of this example.

    python examples/run.py intake csa
    python examples/run.py intake invoice --flaw
    python examples/run.py intake kyc --flaw
"""

from __future__ import annotations

import json
from typing import Any

from pyligent_agents import idempotency_key
from pyligent_agents.graph import AgentNode, GateNode, Graph, Step
from pyligent_agents.graph.state import GraphState
from pyligent_agents.harness import Harness
from pyligent_agents.loop import (
    Agent,
    AgentContract,
    Budget,
    LoopState,
    ModelSaysDone,
    Produced,
    no_verification,
)
from pyligent_agents.verify import DocumentVerifier

from document_intake.documents import DOCUMENTS, DocumentSpec


def _json_extractor(state: LoopState) -> dict[str, Any]:
    body = (state.answer or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.split("\n", 1)[1] if "\n" in body else body
        body = body.rsplit("```", 1)[0]
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _build_extractor(spec: DocumentSpec):
    def build(harness: Harness, state: GraphState) -> Agent:
        return Agent(
            harness,
            AgentContract(
                goal=f"Extract {spec.title} fields with verbatim evidence for each.",
                # Two conditions: the model is finished AND it actually produced
                # the fields. "It stopped calling tools" is not enough.
                stop=ModelSaysDone() & Produced("fields"),
                verifier=no_verification(
                    "The artifact is checked downstream by an independent verifier "
                    "and a gate set, both of which see the artifact and not this agent."
                ),
                budget=Budget(max_turns=3, max_usd=0.30, max_seconds=60),
            ),
            model=harness.settings.worker_model,
            system=spec.system,
            # No tools at all. A document written outside your firm is untrusted
            # input; an agent that reads one should have nothing worth reaching.
            tools=[],
            extractor=_json_extractor,
            name=f"extract_{spec.key}",
        )

    return build


# --- node bodies ----------------------------------------------------------


def _load(spec: DocumentSpec, *, flawed: bool):
    def load(_state: GraphState) -> dict[str, Any]:
        return {"source_text": spec.source(flawed=flawed),
                "document": {"document_id": spec.document_id, "title": spec.title,
                             "kind": spec.key}}
    return load


def _assemble(state: GraphState) -> dict[str, Any]:
    extracted = state.require("extracted")["artifact"]
    doc = state.require("document")
    return {"artifact": {
        **doc,
        **extracted,
        # Prefixed keys are ours. The verifier never sees them — it judges the
        # claims, not our notes about how we got them.
        "_source_text": state.require("source_text"),
    }}


def _verify(harness: Harness):
    def verify(state: GraphState) -> dict[str, Any]:
        artifact = state.require("artifact")
        verdict = DocumentVerifier(harness).verify(artifact, {"goal": "intake a document"})
        artifact["_verification"] = verdict.to_dict()
        return {"artifact": artifact, "verification": verdict.to_dict()}
    return verify


def _accept(state: GraphState) -> dict[str, Any]:
    artifact = state.require("artifact")
    return {"accepted": {
        "document_id": artifact["document_id"],
        "kind": artifact["kind"],
        "fields": len(artifact.get("fields", {})),
        "status": "accepted_into_system_of_record",
    }}


def _refer(state: GraphState) -> dict[str, Any]:
    report = state.get("gate_report") or {}
    failures = [r for r in report.get("results", []) if not r["passed"]]
    return {"referral": {
        "reason": "intake gates failed",
        "failed_gates": report.get("failed", []),
        "findings": [r["message"] for r in failures],
        "action": "route to a human for manual review; do not load",
    }}


def _gates_passed(state: GraphState) -> bool:
    return bool((state.get("gate_report") or {}).get("passed"))


# --- the graph ------------------------------------------------------------


def build_graph(harness: Harness, kind: str = "invoice", *, flawed: bool = False) -> Graph:
    """One graph shape, parameterised by which document it is reading."""
    spec = DOCUMENTS[kind]

    return Graph(name=f"intake_{spec.key}", seeds=()).extend(
        Step(id="load", fn=_load(spec, flawed=flawed),
             provides=("source_text", "document"),
             description=f"Fetch the {spec.title}. Untrusted content from here on."),

        AgentNode(id="extract", build=_build_extractor(spec),
                  task=lambda s: (f"Extract the fields.\n\nSOURCE DOCUMENT\n"
                                  f"{s.require('source_text')}"),
                  depends_on=("load",), requires=("source_text",),
                  provides=("extracted",), tools=(),
                  description="One specialist, no tools, evidence required per field."),

        Step(id="assemble", fn=_assemble,
             depends_on=("extract",), requires=("extracted", "document", "source_text"),
             provides=("artifact",),
             description="Attach the source so evidence can be checked against it."),

        Step(id="verify", fn=_verify(harness),
             depends_on=("assemble",), requires=("artifact",),
             provides=("artifact", "verification"),
             description="Independent verifier. Its own citations are checked too."),

        GateNode(id="gates", gates=spec.gates(), subject="artifact",
                 depends_on=("verify",), requires=("artifact",), provides=("gate_report",),
                 description="Five generic gates plus this document's own. THE stop condition."),

        Step(id="accept", fn=_accept,
             depends_on=("gates",), requires=("artifact",), provides=("accepted",),
             when=_gates_passed,
             idempotency=lambda s: idempotency_key(
                 "accept_document", doc=s.require("artifact")["document_id"]),
             description="Loads into the system of record. Gated, and idempotent."),

        Step(id="refer", fn=_refer,
             depends_on=("gates",), requires=("gate_report",), provides=("referral",),
             when=lambda s: not _gates_passed(s),
             description="The other branch. A failed gate reaches a human, not the ledger."),
    )
