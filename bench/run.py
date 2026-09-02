"""Run the checks over a corpus and print the table.

    python bench/run.py                      # the shipped corpus
    python bench/run.py --corpus path/ --json results.json

The headline number is **evidence integrity**: the share of extracted fields
whose value is supported by a citation that actually appears in the document.
It needs no ground truth, so it can be computed on any corpus by anyone.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import Entry, extractors_in, load_corpus  # noqa: E402

from evidencecheck import check  # noqa: E402
from evidencecheck.console import use_utf8_stdout
from evidencecheck.sources import load as load_source  # noqa: E402

CODES = ("FABRICATED_EVIDENCE", "SILENT_REPAIR", "PLACEHOLDER_VALUE",
         "MISSING_EVIDENCE", "EMPTY_VALUE")


# The schema a run is scored against. Integrity alone is a share of what a model
# CHOSE to emit, so a model that answers only the easy fields scores better than one
# that attempts the hard ones. Coverage is the denominator that closes that hole, and
# the two must always be read together.
SCHEMA_FIELDS = ("base_currency", "eligible_currency", "threshold",
                 "minimum_transfer_amount", "rounding", "governing_law",
                 "party_a", "party_b", "valuation_percentage")


@dataclass
class Score:
    extractor: str
    documents: int = 0
    fields: int = 0                           # fields the extractor emitted
    findings: int = 0
    expected: int = 0                         # schema fields × documents attempted
    cited: int = 0                            # emitted fields carrying a quote
    by_code: dict[str, int] = None            # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.by_code is None:
            self.by_code = dict.fromkeys(CODES, 0)

    @property
    def coverage(self) -> float:
        """Share of schema fields the extractor actually attempted.

        Without this, integrity is trivially gamed: omit the field you would fail
        and the score goes up. Measured on a three-field document, dropping the one
        bad field moves integrity from 66.7% to 100%.
        """
        return 0.0 if not self.expected else self.fields / self.expected

    @property
    def citation_coverage(self) -> float:
        """Share of emitted fields that cited anything at all."""
        return 0.0 if not self.fields else self.cited / self.fields

    @property
    def evidence_integrity(self) -> float:
        """Share of EMITTED fields with no finding against them.

        Conditional on emission, and never to be reported alone. See `coverage`.
        """
        return 1.0 if not self.fields else 1 - (self.findings / self.fields)

    @property
    def effective_integrity(self) -> float:
        """Coverage x integrity: the share of the SCHEMA both answered and supported.

        One number that omission cannot inflate — which is the whole reason it
        exists, since integrity alone rewards a model for skipping the fields it
        would fail.

        **It deliberately does not distinguish a field that was never attempted from
        one that was answered with an invented citation.** Both leave the schema
        unsupported, and this figure counts supported schema:

            4 emitted, 0 fabricated  ->  (4-0)/9 = 44.4%
            6 emitted, 2 fabricated  ->  (6-2)/9 = 44.4%

        That tie is real and is left in place on purpose. Penalising fabrication
        harder would mean choosing a coefficient — twice? three times? — and a
        weighting nobody can derive is a worse property in a control than a tie
        anyone can see. The strength of this figure is that it is one line of
        arithmetic a sceptic can recompute.

        The distinction is not lost, only moved: `by_code['FABRICATED_EVIDENCE']` is
        reported beside it in every table. A reader who cares whether the gap is
        silence or invention — and for an institution that difference matters, since
        the project's own lifecycle treats abstention as honest and guessing as
        not — reads that column. Do not report effective integrity without it.
        """
        return 0.0 if not self.expected else (self.fields - self.findings) / self.expected

    def rate(self, code: str) -> float:
        return 0.0 if not self.fields else self.by_code.get(code, 0) / self.fields

    def to_dict(self) -> dict:
        return {
            "extractor": self.extractor, "documents": self.documents,
            "schema_fields_expected": self.expected,
            "fields_emitted": self.fields,
            "fields_cited": self.cited,
            "findings": self.findings,
            "coverage": round(self.coverage, 4),
            "citation_coverage": round(self.citation_coverage, 4),
            "evidence_integrity": round(self.evidence_integrity, 4),
            "effective_integrity": round(self.effective_integrity, 4),
            "by_code": self.by_code,
        }


def score_corpus(entries: list[Entry]) -> dict[str, Score]:
    scores = {name: Score(name) for name in extractors_in(entries)}
    for entry in entries:
        source = load_source(entry.source_path)
        for extraction in entry.extractions:
            s = scores[extraction.extractor]
            report = check(source.text, extraction.fields)
            s.documents += 1
            s.fields += report.fields_checked
            s.expected += len(SCHEMA_FIELDS)
            s.cited += sum(1 for v in extraction.fields.values()
                           if isinstance(v, dict) and str(v.get("quote") or "").strip())
            s.findings += len(report.findings)
            for code, n in report.by_code().items():
                s.by_code[code] = s.by_code.get(code, 0) + n
    return scores


def render(entries: list[Entry], scores: dict[str, Score]) -> str:
    out: list[str] = []
    out.append("EVIDENCE INTEGRITY")
    out.append("=" * 78)
    out.append(f"  {len(entries)} document(s), {len(scores)} extractor(s). "
               f"No ground truth required.")
    out.append("")
    out.append(f"  {'extractor':<20}{'coverage':>10}{'cited':>8}{'integrity':>11}"
               f"{'effective':>11}{'fabricated':>12}{'repair':>8}")
    out.append("  " + "-" * 76)
    # Ordered by effective integrity: the one figure omission cannot inflate.
    for s in sorted(scores.values(), key=lambda x: -x.effective_integrity):
        out.append(
            f"  {s.extractor:<20}{s.coverage:>9.1%}{s.citation_coverage:>8.0%}"
            f"{s.evidence_integrity:>10.1%}{s.effective_integrity:>11.1%}"
            f"{s.by_code.get('FABRICATED_EVIDENCE', 0):>12}"
            f"{s.by_code.get('SILENT_REPAIR', 0):>8}")

    out.append("")
    out.append("  coverage   share of the schema the extractor attempted at all")
    out.append("  integrity  of what it DID emit, the share a citation supports")
    out.append("  effective  coverage x integrity — the share of the schema both")
    out.append("             answered and supported")
    out.append("")
    out.append("  Read integrity WITH coverage, never alone: omitting a field you")
    out.append("  would have failed raises integrity and lowers coverage. Effective")
    out.append("  integrity is the figure that omission cannot inflate.")
    out.append("")
    out.append("  None of these is accuracy. A quote can be genuine, contain the")
    out.append("  value, and still be the wrong clause.")

    repairs = {n: s for n, s in scores.items() if s.by_code.get("SILENT_REPAIR")}
    if repairs:
        out.append("")
        out.append("SILENT REPAIR")
        out.append("-" * 78)
        for name, s in sorted(repairs.items(), key=lambda kv: -kv[1].by_code["SILENT_REPAIR"]):
            out.append(f"  {name:<22}{s.by_code['SILENT_REPAIR']:>4} "
                       f"field(s) where the cited text names a different value "
                       f"({s.rate('SILENT_REPAIR'):.1%})")
        out.append("")
        out.append("  These are the expensive ones. The citation is real, so every")
        out.append("  check that asks 'did it cite something' passes. The discrepancy")
        out.append("  the extraction was meant to surface is the thing it removed.")

    out.append("")
    out.append("CORPUS")
    out.append("-" * 78)
    for e in entries:
        out.append(f"  {e.name:<34}{e.provenance}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    p = argparse.ArgumentParser(description="Evidence integrity across extractors.")
    p.add_argument("--corpus", default=str(Path(__file__).resolve().parent / "corpus"))
    p.add_argument("--json", help="write the scores here as JSON")
    a = p.parse_args(argv)

    entries = load_corpus(a.corpus)
    if not entries:
        print(f"no documents with extractions under {a.corpus}", file=sys.stderr)
        return 2

    scores = score_corpus(entries)
    print(render(entries, scores))

    if a.json:
        Path(a.json).write_text(json.dumps(
            {"documents": len(entries),
             "corpus": [{"name": e.name, "provenance": e.provenance} for e in entries],
             "scores": [s.to_dict() for s in scores.values()]},
            indent=2), encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
