"""Decide whether a document *is* a Credit Support Annex, not whether it mentions one.

SEC full-text search answers the wrong question. `q="Credit Support Annex"` returns
every 10-K whose notes say the parties entered into one — thousands of documents that
contain the phrase exactly once, in prose, and contain no annex at all. A benchmark
built from that search measures nothing, because most of its corpus has no terms to
extract.

The discriminator is the annex's own vocabulary. "Credit Support Annex" is a name and
travels freely into prose. "Delivery Amount", "Return Amount" and "Valuation
Percentage" are operative defined terms: they appear where the mechanism is actually
set out, and essentially nowhere else. A filing that describes a CSA says so once; a
filing that *is* one cannot avoid saying "Delivery Amount".

Scoring is deliberately transparent rather than learned. Every verdict carries the
markers that produced it, so a disputed classification is inspected, not re-trained.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from evidencecheck.console import use_utf8_stdout
from evidencecheck.sources import load  # noqa: E402

# Operative terms. These are defined in the annex and used by its mechanics; prose
# describing a CSA from the outside has no reason to reach for them.
OPERATIVE = (
    "delivery amount",
    "return amount",
    "credit support amount",
    "valuation percentage",
    "valuation agent",
    "valuation date",
    "minimum transfer amount",
    "independent amount",
    "eligible collateral",
    "posted collateral",
    "substitute credit support",
    "interest amount",
)

# Paragraph headings from the ISDA forms (1994 NY security-interest, 1995 English
# transfer). Their presence means the document carries the annex's structure.
STRUCTURE = (
    "interpretation",
    "security interest",
    "credit support obligations",
    "conditions precedent",
    "transfer timing",
    "dispute resolution",
    "holding and using posted collateral",
    "distributions and interest amount",
    "additional representations",
    "demands and notices",
    "elections and variables",
)

# Paragraph 11 (NY) / Paragraph 13 (English) carry the negotiated elections. A
# document containing these is not describing an annex; it is one.
ELECTIONS = re.compile(r"paragraph\s*1[13]\b", re.I)
PARA_NUMBERING = re.compile(r"paragraph\s*\(?\s*([1-9]|1[0-3])\s*\)?[.\s]", re.I)


@dataclass
class Verdict:
    path: str
    is_csa: bool
    score: int
    chars: int
    operative: list[str] = field(default_factory=list)
    structure: list[str] = field(default_factory=list)
    elections: bool = False
    distinct_paragraphs: int = 0
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "is_csa": self.is_csa,
            "score": self.score,
            "chars": self.chars,
            "operative_terms": self.operative,
            "structural_headings": self.structure,
            "has_elections_paragraph": self.elections,
            "distinct_paragraph_numbers": self.distinct_paragraphs,
            "reason": self.reason,
        }


def classify(path: Path) -> Verdict:
    """Score one document. The verdict carries its own evidence."""
    try:
        text = load(path).text
    except Exception as exc:  # a document we cannot read is not a document we can score
        return Verdict(str(path), False, 0, 0, reason=f"unreadable: {type(exc).__name__}: {exc}")

    low = " ".join(text.lower().split())

    operative = [t for t in OPERATIVE if t in low]
    structure = [h for h in STRUCTURE if h in low]
    elections = bool(ELECTIONS.search(low))
    paragraphs = len({m.group(1) for m in PARA_NUMBERING.finditer(low)})

    # Weighting reflects how hard each signal is to produce by accident. Operative
    # terms are the backbone; structure and elections corroborate.
    score = len(operative) * 2 + len(structure) + (3 if elections else 0) + min(paragraphs, 6)

    # The gate: an annex states its own mechanics. Requiring both sides of the
    # transfer obligation ("Delivery Amount" and "Return Amount") plus corroboration
    # rejects prose mentions without rejecting genuine annexes that use one form's
    # vocabulary and not the other's.
    has_both_legs = "delivery amount" in low and "return amount" in low
    corroborated = elections or len(structure) >= 3 or paragraphs >= 5

    if not has_both_legs:
        reason = "no Delivery/Return Amount pair — mentions a CSA rather than being one"
        return Verdict(str(path), False, score, len(text), operative, structure, elections, paragraphs, reason)
    if not corroborated:
        reason = "operative terms present but no annex structure — likely a filing that quotes one"
        return Verdict(str(path), False, score, len(text), operative, structure, elections, paragraphs, reason)
    if len(text) < 4000:
        reason = "too short to carry an annex's elections"
        return Verdict(str(path), False, score, len(text), operative, structure, elections, paragraphs, reason)

    reason = f"{len(operative)} operative terms, {len(structure)} headings, {paragraphs} paragraph numbers"
    return Verdict(str(path), True, score, len(text), operative, structure, elections, paragraphs, reason)


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("paths", nargs="+", help="files or directories to classify")
    p.add_argument("--json", type=Path, help="write full verdicts here")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args(argv)

    files: list[Path] = []
    for raw in a.paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(f for f in path.rglob("*") if f.suffix.lower() in {".htm", ".html", ".txt", ".pdf"}))
        elif path.is_file():
            files.append(path)

    verdicts = [classify(f) for f in files]
    accepted = [v for v in verdicts if v.is_csa]

    if not a.quiet:
        for v in sorted(verdicts, key=lambda v: -v.score):
            mark = "CSA " if v.is_csa else "  - "
            print(f"  {mark} {v.score:3d}  {Path(v.path).name[:52]:<52}  {v.reason}")

    print(f"\n  {len(accepted)}/{len(verdicts)} are CSAs")

    if a.json:
        a.json.write_text(json.dumps([v.as_dict() for v in verdicts], indent=2), encoding="utf-8")
        print(f"  verdicts → {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
