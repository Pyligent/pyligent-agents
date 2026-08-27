"""Four model behaviours, so the eval harness can be shown to work.

An eval you have only run against one system tells you nothing about whether the
*metrics* are any good. These personas are known-good and known-bad extractors;
if the report does not separate them, the report is wrong.

They are also a compact catalogue of how document extraction actually fails:

    faithful      transcribes the document, including its inconsistencies
    paraphraser   right values, invented quotes — the hallucinated citation
    helpful       silently CORRECTS inconsistencies it was meant to report
    sloppy        placeholders and dropped fields

`helpful` is the one to study. It is the most human failure — a model doing what
it was implicitly rewarded for, tidying up a document so the answer looks clean —
and it costs almost nothing in field accuracy while producing false accepts. Any
eval whose headline is a single accuracy number will rate it the *best* system in
this file.
"""

from __future__ import annotations

import json
from typing import Any

from dataset import BUILDS

from pyligent_agents.testing import ScriptedTurn, router, turn

EXTRACTOR_PROMPTS = {
    "csa": "You extract the Paragraph 11",
    "invoice": "You extract a supplier invoice",
    "kyc": "You extract an individual customer onboarding pack",
}


def _payload(fields: dict[str, Any], quotes: dict[str, str],
             extras: dict[str, Any]) -> dict[str, Any]:
    return {"fields": {k: {"value": v, "evidence_quote": quotes.get(k, "")}
                       for k, v in fields.items()},
            **extras}


# --- the four personas ----------------------------------------------------


def faithful(case) -> dict[str, Any]:
    b = BUILDS[case.case_id]
    return _payload(dict(b.fields), dict(b.quotes), dict(b.extras))


def paraphraser(case) -> dict[str, Any]:
    """Correct values, but the quotes are its own words rather than the page's.

    The failure that requiring citations does not catch and *checking* them
    does. Note the values are right — a model can be entirely correct and still
    unable to prove it.
    """
    b = BUILDS[case.case_id]
    quotes = {k: f"The document states that {k.replace('_', ' ')} is {v}."
              for k, v in b.fields.items()}
    return _payload(dict(b.fields), quotes, dict(b.extras))


def helpful(case) -> dict[str, Any]:
    """Tidies up. Reports the document as it 'should' read.

    Every change below is a plausible act of helpfulness, and every one converts
    a document that should have been referred into one that sails through.
    """
    b = BUILDS[case.case_id]
    fields, quotes, extras = dict(b.fields), dict(b.quotes), dict(b.extras)

    # KYC: the passport name and the application name differ by a letter, so it
    # "fixes the typo" — and the mismatch is never seen by a human.
    if fields.get("name_on_document") and fields.get("applicant_name"):
        fields["name_on_document"] = fields["applicant_name"]
        quotes["name_on_document"] = quotes.get("applicant_name", "")

    # CSA: the MTA is larger than the Threshold, which "must be a mistake", so
    # it puts them back the way they usually appear.
    if (isinstance(fields.get("mta"), int)
            and isinstance(fields.get("threshold"), int)
            and fields["mta"] > fields["threshold"]):
        fields["mta"], fields["threshold"] = fields["threshold"], fields["mta"]
        quotes["mta"], quotes["threshold"] = quotes["threshold"], quotes["mta"]

    # CSA: no governing law clause, so it supplies the one that is usually there.
    if case.kind == "csa" and "governing_law" not in fields:
        fields["governing_law"] = "English law"
        quotes["governing_law"] = '"Base Currency" means United States Dollars (USD).'

    # Invoice: the printed total does not match the lines, so it reports the
    # total it computed instead of the total on the page.
    if extras.get("line_items") and "net_total" in fields:
        computed = round(sum(x["quantity"] * x["unit_price"] for x in extras["line_items"]), 2)
        if abs(computed - float(fields["net_total"])) > 0.01:
            rate = float(fields.get("tax_rate_pct", 20))
            fields["net_total"] = computed
            fields["gross_total"] = round(computed * (1 + rate / 100), 2)

    return _payload(fields, quotes, extras)


def sloppy(case) -> dict[str, Any]:
    """Drops the last field and writes a placeholder into another.

    The cheapest failure to detect, and the one a schema-only check waves
    through: `"value": "TBD"` is a perfectly valid string.
    """
    b = BUILDS[case.case_id]
    fields, quotes = dict(b.fields), dict(b.quotes)
    names = list(fields)
    if len(names) >= 2:
        fields.pop(names[-1])
        fields[names[-2]] = "TBD"
    return _payload(fields, quotes, dict(b.extras))


PERSONAS = {"faithful": faithful, "paraphraser": paraphraser,
            "helpful": helpful, "sloppy": sloppy}

VERIFIER_APPROVES = {"approved": True, "citations": [], "reasons": ["Reviewed."]}


def build_policy(case, persona_name: str):
    """A scripted client for one case under one persona.

    The verifier is deliberately generous — it approves with a citation drawn
    from the extraction itself. That keeps the eval measuring the *extractor*
    and the *gates* rather than a second model's mood.
    """
    payload = PERSONAS[persona_name](case)
    first = next(iter(payload["fields"].values()), {})
    approval = {**VERIFIER_APPROVES, "citations": [
        {"claim": "spot check", "paragraph": "source",
         "verbatim_quote": first.get("evidence_quote", "")}]}

    return router({
        EXTRACTOR_PROMPTS[case.kind]:
            lambda c: ScriptedTurn(text=json.dumps(payload),
                                   input_tokens=1_800, output_tokens=400),
        "You are an independent reviewer":
            lambda c: ScriptedTurn(text=json.dumps(approval),
                                   input_tokens=2_000, output_tokens=120),
        "checking work that has just been completed":
            lambda c: turn("VERIFIED", input_tokens=350, output_tokens=8),
        "compacting an agent transcript":
            lambda c: turn("Earlier: read the document."),
    })
