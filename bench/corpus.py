"""The corpus: real documents, and whatever several extractors made of them.

A benchmark is only worth publishing if someone can disagree with it, so the
layout is deliberately plain — files on disk, one directory per document, one
JSON per extractor:

    bench/corpus/<document>/
        source.html          the document, as retrieved
        meta.json            where it came from, licence, when
        extractions/
            claude-sonnet-5.json
            gpt-5.json
            gemini-3-pro.json

No database, no download step, no credentials to reproduce the numbers. Anyone
can add a document, re-run, and see whether the finding survives their corpus.

**Nothing here needs gold labels.** Evidence integrity is reference-free: you
do not need to know the right answer to know that a quote is not in the
document. That is what makes this benchmark cheap enough to run on thousands of
pages, and it is the property that makes the metric interesting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Extraction:
    extractor: str
    fields: dict[str, dict[str, Any]]
    path: Path | None = None


@dataclass(frozen=True)
class Entry:
    name: str
    source_path: Path
    extractions: tuple[Extraction, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def provenance(self) -> str:
        """Where this document came from. A benchmark on documents nobody can
        find is a benchmark nobody can check."""
        url = self.meta.get("source_url", "")
        licence = self.meta.get("licence") or self.meta.get("license", "")
        return f"{url}{f' ({licence})' if licence else ''}" or "unstated"


def _read_extraction(path: Path) -> Extraction:
    from evidencecheck.cli import normalise_extraction

    payload = json.loads(path.read_text())
    return Extraction(extractor=path.stem,
                      fields=normalise_extraction(payload), path=path)


def load_corpus(root: str | Path) -> list[Entry]:
    """Every document under `root` that has at least one extraction."""
    base = Path(root)
    if not base.exists():
        raise FileNotFoundError(f"no corpus at {base}")

    entries: list[Entry] = []
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        sources = sorted(
            p for p in d.iterdir()
            if p.is_file() and p.suffix.lower() in (".html", ".htm", ".txt", ".pdf")
        )
        if not sources:
            continue
        ex_dir = d / "extractions"
        extractions = tuple(
            _read_extraction(p) for p in sorted(ex_dir.glob("*.json"))
        ) if ex_dir.is_dir() else ()
        if not extractions:
            continue
        meta_path = d / "meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        entries.append(Entry(name=d.name, source_path=sources[0],
                             extractions=extractions, meta=meta))
    return entries


def extractors_in(entries: list[Entry]) -> list[str]:
    return sorted({e.extractor for entry in entries for e in entry.extractions})
