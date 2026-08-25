"""The independent verifier, and the check that makes its approval falsifiable.

Self-grading is a bias, not a safeguard: an agent that extracts a document and
then reviews its own extraction reads the same paragraph the same way twice. So
the verifier is a separate agent, given the artifact and the source and nothing
about how the artifact was produced.

That is necessary and **not sufficient**. A verifier that must cite evidence can
still invent a quote — and it is *more* likely on a structured document (a
contract, an invoice, a policy) than on arbitrary text, because the register is
so predictable. A model asked to quote "clause 4.2" can produce something
perfectly plausible without having read it.

So every citation is checked against the source with a whitespace-normalised
substring match, and one fabricated quote rejects the artifact regardless of the
verdict. The model can be wrong; the substring check cannot.

Known limit, stated rather than hidden: this catches *fabricated* evidence, not
*irrelevant* evidence. A genuine sentence that does not support the claim passes.
Closing that needs a positional check or a gold set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..core.types import Phase
from ..harness.harness import Harness
from ..loop.contract import VerifierVerdict

VERIFIER_SYSTEM = """\
You are an independent reviewer of values extracted from a document. You did not
perform the extraction and you have no access to how it was done.

You are given the source document and a candidate extraction. Check each value
against the document. For every value you confirm, cite the paragraph and quote
the exact words that support it. Copy the quote character for character — a
quote that is not literally in the document will be detected and will reject the
whole artifact.

Approve only if every value is supported. If any is wrong or unsupported,
reject and say which.

Respond with JSON only:
{"approved": true|false,
 "citations": [{"claim": "...", "paragraph": "...", "verbatim_quote": "..."}],
 "reasons": ["..."]}"""


def _normalise(text: str) -> str:
    return " ".join(str(text).split()).lower()


def quote_is_in(source: str, quote: str) -> bool:
    """Whitespace-normalised, case-insensitive containment.

    Whitespace is normalised because PDF text wraps at arbitrary points and a
    quote spanning a line break is still a real quote. Wording is NOT
    normalised — a paraphrase is not a citation.
    """
    return bool(quote.strip()) and _normalise(quote) in _normalise(source)


@dataclass
class DocumentVerifier:
    """Verifies an artifact against a source document."""

    harness: Harness
    source_key: str = "_source_text"
    model: str | None = None

    def verify(self, artifact: dict[str, Any], context: dict[str, Any]) -> VerifierVerdict:
        source = artifact.get(self.source_key) or context.get("source_text", "")
        if not source:
            return VerifierVerdict(False, ("no source document supplied; cannot verify",))

        # Strip our own bookkeeping. The verifier sees claims and the document,
        # never a prior verdict or the producing agent's reasoning.
        candidate = {k: v for k, v in artifact.items() if not k.startswith("_")}

        ctx = self.harness.new_context(
            model=self.model or self.harness.settings.worker_model,
            system=VERIFIER_SYSTEM,
        )
        ctx.append_user(
            f"SOURCE DOCUMENT\n===============\n{source}\n\n"
            f"CANDIDATE EXTRACTION\n====================\n"
            f"{json.dumps(candidate, indent=2, default=str)}"
        )
        response = self.harness.call_model(
            phase=Phase.VERIFY,
            model=self.model or self.harness.settings.worker_model,
            context=ctx, max_tokens=1_500,
        )

        parsed = _parse_json(response.text)
        if parsed is None:
            # Failing OPEN on a control is worse than not having the control.
            return VerifierVerdict(False, ("verifier output was not valid JSON; treated as a rejection",))

        confirmed, fabricated = [], []
        for raw in parsed.get("citations") or []:
            if not isinstance(raw, dict):
                continue
            quote = str(raw.get("verbatim_quote", "")).strip()
            record = {
                "claim": str(raw.get("claim", "")).strip(),
                "paragraph": str(raw.get("paragraph", "")).strip(),
                "verbatim_quote": quote,
                "found_in_source": quote_is_in(source, quote),
            }
            (confirmed if record["found_in_source"] else fabricated).append(record)

        approved = bool(parsed.get("approved"))
        reasons = [str(r) for r in (parsed.get("reasons") or [])]

        if fabricated:
            approved = False
            reasons.append(
                f"{len(fabricated)} citation(s) quote text absent from the source: "
                + "; ".join(repr(c["claim"]) for c in fabricated)
            )
        if approved and not confirmed:
            approved = False
            reasons.append("approval carried no citations; evidence is required")

        return VerifierVerdict(
            approved=approved,
            reasons=tuple(reasons),
            evidence=tuple(confirmed + fabricated),
        )


@dataclass
class GateVerifier:
    """A verifier that is just a gate set. No model, no ambiguity, no cost.

    Use it wherever "correct" is fully expressible as predicates. It is the
    cheapest verification there is, and the only kind that cannot itself be
    wrong.
    """

    gates: Any

    def verify(self, artifact: dict[str, Any], context: dict[str, Any]) -> VerifierVerdict:
        report = self.gates.evaluate(artifact)
        if report.passed:
            return VerifierVerdict(True, (f"{len(report.results)} gate(s) passed",))
        return VerifierVerdict(
            False,
            tuple(f"{f.name}: {f.message}" for f in report.failures),
            tuple(r.to_dict() for r in report.results),
        )


def _parse_json(text: str) -> dict[str, Any] | None:
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        if "\n" in body:
            body = body.split("\n", 1)[1]
        body = body.rsplit("```", 1)[0]
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None
