"""Build a benchmark corpus of documents that *are* Credit Support Annexes.

Two rules govern what may enter, and both are enforced here rather than trusted to
whoever runs the script.

The first is provenance. Benchmark documents are sent to third-party model APIs. Only
material that is already public may go: SEC EDGAR exhibits (US federal government
works) and ISDA's published forms. Executed bilateral agreements between named
counterparties are excluded by pattern, unconditionally — a corpus builder is exactly
the place where such a file slips in unnoticed, because it looks like every other CSA.

The second is that the document must actually be an annex. See classify.py: the phrase
"Credit Support Annex" travels into prose, so a corpus built from a phrase search is
mostly filings that reference one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from classify import classify  # noqa: E402

from evidencecheck.console import use_utf8_stdout
from evidencecheck.sources import load  # noqa: E402

# Executed bilateral agreements. Excluded on filename, before anything reads them.
# Deliberately broad: a false exclusion costs one public document, a false inclusion
# sends a counterparty's negotiated terms to an API vendor.
CONFIDENTIAL = re.compile(
    r"executed|td[_\- ]?bank|holmes|project[_\- ]?snow|bmo|finsbury|barclays[_\- ]?fin",
    re.I,
)


# An EDGAR-filed document carries its filing furniture: the exhibit type, the
# SGML header, or the <PAGE> markers of a text filing. This is checked rather than
# assumed, because the licence line below is a claim about someone else's rights.
SEC_MARKER = re.compile(
    r"<TYPE>|<SEQUENCE>|SEC-HEADER|<PAGE>|\bEX-\d|\bEX-99|\bExhibit\s+\d", re.I
)

PUBLIC_DOMAIN = "US federal government work, public domain"


def _is_public(path: Path, text: str) -> tuple[bool, str, str]:
    """Whether this document may be redistributed, and on what evidence.

    The first version of this function asserted `US federal government work, public
    domain` for everything it admitted. That was a claim about copyright with nothing
    behind it, in the tool whose entire subject is unsupported claims. It was wrong for
    three files: two ISDA-published forms, which are ISDA's copyright and not a federal
    work at all, and one named securitisation's transaction document carrying its own
    legal notice.

    So provenance is now evidence, like everything else here. A document is admitted
    only if it carries EDGAR filing furniture. Anything else is refused rather than
    relabelled: this corpus is redistributed and sent to third-party APIs, and
    "probably fine" is not a licence.
    """
    if CONFIDENTIAL.search(path.name):
        return False, "", "excluded: filename indicates an executed bilateral agreement"
    m = SEC_MARKER.search(text[:4000])
    if not m:
        return False, "", ("excluded: no EDGAR filing marker, so its provenance and "
                           "licence cannot be established from the document itself")
    return True, PUBLIC_DOMAIN, f"EDGAR filing marker {m.group(0)!r} present in the document"


def build(sources: list[Path], out: Path, *, limit: int) -> int:
    out.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}
    written = skipped_private = skipped_notcsa = skipped_dupe = 0

    for path in sources:
        try:
            text = load(path).text
        except Exception:
            skipped_notcsa += 1
            continue

        public, licence, note = _is_public(path, text)
        if not public:
            skipped_private += 1
            print(f"  refused  {path.name[:52]:<52} {note}")
            continue

        verdict = classify(path)
        if not verdict.is_csa:
            skipped_notcsa += 1
            continue

        digest = hashlib.sha256(" ".join(text.split()).encode()).hexdigest()
        if digest[:16] in seen:
            skipped_dupe += 1
            continue
        seen[digest[:16]] = path.name

        name = re.sub(r"[^A-Za-z0-9_.-]", "_", path.stem)[:60]
        record = out / name
        record.mkdir(exist_ok=True)
        (record / f"source{path.suffix.lower()}").write_bytes(path.read_bytes())
        (record / "meta.json").write_text(
            json.dumps(
                {
                    "source_url": "SEC EDGAR exhibit (public filing)",
                    "licence": licence,
                    "provenance": note,
                    "original_filename": path.name,
                    "sha256": digest,
                    "chars": len(text),
                    "classifier_score": verdict.score,
                    "operative_terms": verdict.operative,
                    "structural_headings": verdict.structure,
                    "distinct_paragraph_numbers": verdict.distinct_paragraphs,
                    "admitted_because": verdict.reason,
                },
                indent=2,
            )
        )
        written += 1
        if written >= limit:
            break

    print(f"  written:              {written}")
    print(f"  skipped, not a CSA:   {skipped_notcsa}")
    print(f"  skipped, duplicate:   {skipped_dupe}")
    print(f"  refused on provenance:{skipped_private}")
    print(f"  corpus → {out}")
    return written


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("roots", nargs="+", type=Path)
    p.add_argument("--out", type=Path, default=Path(__file__).parent / "corpus-sec")
    p.add_argument("--limit", type=int, default=100)
    a = p.parse_args(argv)

    files: list[Path] = []
    for root in a.roots:
        if root.is_dir():
            files.extend(sorted(f for f in root.rglob("*") if f.suffix.lower() in {".htm", ".html", ".txt", ".pdf"}))
        elif root.is_file():
            files.append(root)
    return 0 if build(files, a.out, limit=a.limit) else 1


if __name__ == "__main__":
    raise SystemExit(main())
