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
from evidencecheck.sources import load as load_source  # noqa: E402

CODES = ("FABRICATED_EVIDENCE", "SILENT_REPAIR", "PLACEHOLDER_VALUE",
         "MISSING_EVIDENCE", "EMPTY_VALUE")


@dataclass
class Score:
    extractor: str
    documents: int = 0
    fields: int = 0
    findings: int = 0
    by_code: dict[str, int] = None            # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.by_code is None:
            self.by_code = dict.fromkeys(CODES, 0)

    @property
    def evidence_integrity(self) -> float:
        """Share of fields with no finding against them. The headline."""
        return 1.0 if not self.fields else 1 - (self.findings / self.fields)

    def rate(self, code: str) -> float:
        return 0.0 if not self.fields else self.by_code.get(code, 0) / self.fields

    def to_dict(self) -> dict:
        return {
            "extractor": self.extractor, "documents": self.documents,
            "fields": self.fields, "findings": self.findings,
            "evidence_integrity": round(self.evidence_integrity, 4),
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
    out.append(f"  {'extractor':<22}{'integrity':>10}{'fields':>8}"
               f"{'fabricated':>12}{'repair':>8}{'placeholder':>13}")
    out.append("  " + "-" * 74)
    for s in sorted(scores.values(), key=lambda x: -x.evidence_integrity):
        out.append(
            f"  {s.extractor:<22}{s.evidence_integrity:>9.1%}{s.fields:>8}"
            f"{s.by_code.get('FABRICATED_EVIDENCE', 0):>12}"
            f"{s.by_code.get('SILENT_REPAIR', 0):>8}"
            f"{s.by_code.get('PLACEHOLDER_VALUE', 0):>13}")

    out.append("")
    out.append("  Integrity is the share of fields whose value is supported by a")
    out.append("  citation that appears in the document. It is not accuracy: a")
    out.append("  quote can be genuine, contain the value, and be the wrong clause.")

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
            indent=2))
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
