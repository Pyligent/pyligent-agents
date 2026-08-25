"""Level 4 — supplier invoice intake: fan-out, gates, and an independent verifier.

Four different jobs: read the document, extract the header fields, extract the
line items, and prove the result is actually supported by the page. Give one
agent all four and its context fills with the document, the table, its own
reasoning and the verifier's — and you cannot tell which of the four the quality
fell off during.

The shape: extraction fans out (`MapNode`), assembly fans in (`ReduceNode`), then
a verifier and a gate set decide whether it posts to the ledger.

**The gate worth studying is `line_items_sum_to_total`.** No JSON schema catches
it. Every field is present, every type is right, every value is plausible — and
the invoice is wrong. That gate is a single line of arithmetic and it is the
whole reason this pipeline can run unattended.

    python examples/run.py invoice
    python examples/run.py invoice --fabricate
"""

from __future__ import annotations

import json
from typing import Any

from trellis import idempotency_key
from trellis.graph import GateNode, Graph, MapNode, NodeContext, ReduceNode, Step
from trellis.graph.state import GraphState
from trellis.harness import Harness
from trellis.loop import (
    Agent,
    AgentContract,
    Budget,
    LoopState,
    ModelSaysDone,
    Produced,
    no_verification,
)
from trellis.verify import (
    DocumentVerifier,
    GateSet,
    cross_field,
    evidence_gated_extraction,
    non_empty,
    numeric_range,
)

from shopdesk import data, money

HEADER_SYSTEM = """\
You extract header fields from a supplier invoice.

Return: invoice_number, invoice_date, due_date, supplier, purchase_order,
net_total, tax_rate_pct, tax_amount, gross_total, currency.

For each, give the exact words from the document that establish it. Copy every
quote character for character — quotes are checked against the source and an
invented one fails the whole extraction. If a field is genuinely absent, omit it
rather than guessing or writing a placeholder.

Respond with JSON only:
{"fields": {"<name>": {"value": <value>, "evidence_quote": "<exact words>"}}}"""

LINES_SYSTEM = """\
You extract the line items from a supplier invoice.

For each line give description, quantity, unit_price and line_total, exactly as
printed. Do not recompute anything — transcribe. Include the exact words from
the document that establish the line.

Respond with JSON only:
{"line_items": [{"description": "...", "quantity": <n>, "unit_price": <n>,
                 "line_total": <n>, "evidence_quote": "<exact words>"}]}"""

EXTRACTIONS = (
    {"id": "header", "system": HEADER_SYSTEM, "tier": "worker", "produces": "fields",
     "task": "Extract the header fields with evidence for each."},
    {"id": "lines", "system": LINES_SYSTEM, "tier": "cheap", "produces": "line_items",
     "task": "Transcribe the line items with evidence for each."},
)


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


def _extraction_child(spec: dict[str, Any], state: GraphState, ctx: NodeContext) -> dict[str, Any]:
    """One extraction specialist: its own context, its own model, no tools.

    A document-reading agent holds no tools at all, so instruction-shaped text
    inside a supplier's PDF has nothing to reach. That is a capability boundary,
    and it is stronger than any prompt-level defence.
    """
    h = ctx.harness
    model = h.settings.cheap_model if spec["tier"] == "cheap" else h.settings.worker_model
    agent = Agent(
        h,
        AgentContract(
            goal=f"Extract {spec['produces']} from the invoice with verbatim evidence.",
            stop=ModelSaysDone() & Produced(spec["produces"]),
            verifier=no_verification(
                "Extractions are checked downstream by the independent verifier "
                "and the gate set, both of which see this artifact and not this agent."
            ),
            budget=Budget(max_turns=3, max_usd=0.30, max_seconds=60),
        ),
        model=model, system=spec["system"], tools=[],
        extractor=_json_extractor, name=f"extract_{spec['id']}",
    )
    result = agent.run(f"{spec['task']}\n\nSOURCE DOCUMENT\n{state.require('source_text')}")
    return {"id": spec["id"], "ok": result.ok, "payload": result.artifact,
            "model": model, "turns": result.turns}


def _load_document(state: GraphState) -> dict[str, Any]:
    doc = data.INVOICE_DOC
    return {"source_text": doc["text"],
            "document": {k: v for k, v in doc.items() if k != "text"}}


def _assemble(children: list[dict[str, Any]], state: GraphState) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "document_id": state.require("document")["document_id"],
        "supplier": state.require("document")["supplier"],
        "fields": {}, "line_items": [],
        # Prefixed keys are ours. The verifier never sees them.
        "_source_text": state.require("source_text"),
    }
    for child in children:
        payload = child.get("payload") or {}
        artifact["fields"].update(payload.get("fields") or {})
        if payload.get("line_items"):
            artifact["line_items"] = payload["line_items"]
    return {"artifact": artifact}


def _verify(state: GraphState, harness: Harness) -> dict[str, Any]:
    artifact = state.require("artifact")
    verdict = DocumentVerifier(harness).verify(artifact, {"goal": "intake a supplier invoice"})
    artifact["_verification"] = verdict.to_dict()
    return {"artifact": artifact, "verification": verdict.to_dict()}


def _post_to_ledger(state: GraphState) -> dict[str, Any]:
    fields = state.require("artifact")["fields"]
    return {"posted": {
        "invoice_number": fields["invoice_number"]["value"],
        "gross_total": fields["gross_total"]["value"],
        "status": "posted_to_accounts_payable",
    }}


def _ledger_key(state: GraphState) -> str:
    """Posting the same invoice twice is how a supplier gets paid twice."""
    fields = state.require("artifact")["fields"]
    return idempotency_key("post_invoice",
                           number=fields["invoice_number"]["value"],
                           gross=fields["gross_total"]["value"])


def _gates_passed(state: GraphState) -> bool:
    return bool((state.get("gate_report") or {}).get("passed"))


def _escalate(state: GraphState) -> dict[str, Any]:
    report = state.get("gate_report") or {}
    return {"escalation": {
        "reason": "invoice intake gates failed",
        "failed_gates": report.get("failed", []),
        "action": "route to accounts payable for manual keying",
    }}


# --- the gate set ---------------------------------------------------------


def _num(value: Any) -> float:
    return float(str(value).replace(",", "").replace("£", "").replace("GBP", "").strip())


def _lines_sum_to_total(artifact: dict[str, Any]) -> bool:
    """THE gate. Do the line items actually add up to the stated total?

    Every field can be present, correctly typed and individually plausible while
    the invoice as a whole is wrong — a transposed digit, a missed line, a
    mis-read quantity. A schema sees nothing. One line of arithmetic sees it all.

    Tolerance is one penny, for rounding, and no more. "Close enough" on an
    invoice is how you overpay a supplier.
    """
    lines = artifact.get("line_items") or []
    fields = artifact.get("fields") or {}
    if not lines or "net_total" not in fields:
        return False
    try:
        computed = money.invoice_total(
            [{"quantity": _num(x["quantity"]), "unit_price": _num(x["unit_price"])}
             for x in lines],
            tax_rate_pct=_num(fields["tax_rate_pct"]["value"]),
        )
        return (abs(computed["net"] - _num(fields["net_total"]["value"])) <= 0.01
                and abs(computed["gross"] - _num(fields["gross_total"]["value"])) <= 0.01)
    except (KeyError, TypeError, ValueError):
        return False


def _due_after_invoice_date(artifact: dict[str, Any]) -> bool:
    """A due date before the invoice date is a mis-read, every time."""
    fields = artifact.get("fields") or {}
    try:
        from datetime import datetime

        fmt = "%d %B %Y"
        invoice = datetime.strptime(str(fields["invoice_date"]["value"]), fmt)
        due = datetime.strptime(str(fields["due_date"]["value"]).split(" (")[0], fmt)
        return due > invoice
    except (KeyError, TypeError, ValueError):
        return False


def invoice_gates() -> GateSet:
    """Five generic gates from Trellis, plus three this domain had to write."""
    return (
        evidence_gated_extraction(
            "invoice_number", "invoice_date", "due_date", "net_total",
            "tax_rate_pct", "gross_total", "currency",
            under="fields",
        )
        .add("line_items_present", "The invoice has at least one line",
             non_empty("line_items"))
        .add("tax_rate_sane", "The tax rate is a percentage, not a multiplier",
             numeric_range("fields.tax_rate_pct.value", 0, 100))
        .add("lines_sum_to_total",
             "Line items reconcile to the stated net and gross totals",
             cross_field("line items reconcile to the totals", _lines_sum_to_total,
                         message=("line items do not reconcile to the stated totals — "
                                  "a transposed digit or a missed line. Do not post.")))
        .add("due_after_invoice",
             "The due date falls after the invoice date",
             cross_field("due date is after the invoice date", _due_after_invoice_date,
                         message="due date is not after the invoice date; likely mis-read"))
    )


# --- the graph ------------------------------------------------------------


def build_graph(harness: Harness) -> Graph:
    return Graph(name="invoice_intake", seeds=()).extend(
        Step(id="load_document", fn=_load_document,
             provides=("source_text", "document"),
             description="Fetch the invoice. Untrusted content from here on."),

        MapNode(id="extract", over=lambda s: list(EXTRACTIONS), child=_extraction_child,
                depends_on=("load_document",), requires=("source_text",),
                # parallel=1 keeps replay byte-identical. Raise it to trade
                # determinism for latency; see docs/GRAPH.md.
                parallel=1,
                description="Fan out: header fields and line items, separately."),

        ReduceNode(id="assemble", source="extract", combine=_assemble,
                   depends_on=("extract",), requires=("document", "source_text"),
                   provides=("artifact",),
                   description="Fan in: one artifact from both specialists."),

        Step(id="verify", fn=lambda s: _verify(s, harness),
             depends_on=("assemble",), requires=("artifact",),
             provides=("artifact", "verification"),
             description="Independent verifier: sees the artifact and the source, "
                         "never how the artifact was produced."),

        GateNode(id="gates", gates=invoice_gates(), subject="artifact",
                 depends_on=("verify",), requires=("artifact",), provides=("gate_report",),
                 description="Nine predicates. THE stop condition."),

        Step(id="post_to_ledger", fn=_post_to_ledger,
             depends_on=("gates",), requires=("artifact",), provides=("posted",),
             when=_gates_passed, idempotency=_ledger_key,
             description="Posts to accounts payable. Gated, and idempotent."),

        Step(id="escalate", fn=_escalate,
             depends_on=("gates",), requires=("gate_report",), provides=("escalation",),
             when=lambda s: not _gates_passed(s),
             description="The other branch. A failed gate goes to a human."),
    )
