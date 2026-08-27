"""The admissible artifact: the unit of control.

A chat response is not a governable object. It has no schema, no provenance, no
status, and no way to say "this value came from that sentence on that page,
extracted by that prompt at that version". You cannot review it, version it, or
replay it — you can only read it and hope.

This module makes the artifact the unit instead:

    Record
      ├─ document_id, doc_type, schema_version
      ├─ fields:  name -> Field
      │             ├─ value
      │             ├─ evidence:   Evidence(quote, locator, extractor)
      │             └─ provenance: Provenance(extractor, prompt, gate_set, ...)
      ├─ status:  PROPOSED | CERTIFIED | ADMITTED | REFERRED | ABSTAINED
      ├─ review:  ReviewItem(...)  — what a human must still decide
      └─ source:  SourceRef(uri, sha256, media_type, ingested_by)

Three properties make it governable rather than merely structured.

**Status is a lifecycle, not a boolean.** An artifact that failed is not simply
"invalid" — it is REFERRED to somebody, or ABSTAINED on because the system
could not tell. Those are different outcomes with different owners, and a
system that collapses them into `False` cannot route work.

**Provenance is per field, not per document.** "This document was processed by
v2.1" is useless at review time. "This threshold came from prompt csa/v3 under
gate set csa/v7, and the quote is on page 4" is what an auditor asks for, and
the answer has to survive the document being re-processed by a later version.

**Transitions are explicit and one-way.** `certify()` and `refer()` return a new
Record. Nothing mutates a status in place, so the chain from source to decision
can be replayed rather than reconstructed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class Status(str, Enum):
    """Where an artifact sits between extraction and use.

    ABSTAINED is the one usually missing, and the one that keeps a control
    honest: the system could not tell, and said so, rather than guessing in
    whichever direction its threshold happened to fall.
    """

    PROPOSED = "proposed"      # an agent produced it; nothing has checked it
    CERTIFIED = "certified"    # every gate passed; admissible downstream
    ADMITTED = "admitted"      # accepted into a system of record
    REFERRED = "referred"      # a gate failed; a named human decides
    ABSTAINED = "abstained"    # insufficient basis to decide either way


@dataclass(frozen=True)
class Locator:
    """Where in the source a value physically came from.

    Optional, because a plain-text source has no pages. Populated when the
    ingestion adapter knows — which is why adapters that already emit page
    spans and table cells are worth more than a better parser.
    """

    page: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    dom_path: str = ""          # SEC HTML exhibits: a CSS-ish path to the node
    table: str = ""             # table id or caption
    cell: str = ""              # "r4c2" — the cell a haircut actually sits in

    def is_empty(self) -> bool:
        return not any((self.page, self.char_start, self.dom_path, self.table, self.cell))

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if v not in (None, "")}
        return out

    def describe(self) -> str:
        if self.cell and self.table:
            return f"table {self.table} cell {self.cell}"
        if self.dom_path:
            return self.dom_path
        if self.page is not None:
            return f"page {self.page}"
        if self.char_start is not None:
            return f"chars {self.char_start}–{self.char_end}"
        return "source"


@dataclass(frozen=True)
class Evidence:
    """A claim about the source, in a form that can be checked against it."""

    quote: str
    locator: Locator = field(default_factory=Locator)
    verified: bool = False       # set by the gates, never by the extractor

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"quote": self.quote, "verified": self.verified}
        if not self.locator.is_empty():
            out["locator"] = self.locator.to_dict()
        return out


@dataclass(frozen=True)
class Provenance:
    """Which logic produced a value. Per field, because review is per field."""

    extractor: str = ""          # "csa/paragraph-11"
    prompt_version: str = ""     # "csa/v3"
    model: str = ""              # the model id, or "scripted"
    gate_set: str = ""           # "csa/v7"
    repaired: bool = False       # a semantic-repair pass touched this value

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in ("", False)}


@dataclass(frozen=True)
class Field:
    name: str
    value: Any
    evidence: tuple[Evidence, ...] = ()
    provenance: Provenance = field(default_factory=Provenance)

    @property
    def quote(self) -> str:
        return self.evidence[0].quote if self.evidence else ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"value": self.value}
        # The legacy single-quote shape every shipped gate reads. Keeping it
        # means locators and provenance are additive rather than a migration.
        if self.evidence:
            out["evidence_quote"] = self.evidence[0].quote
            out["evidence"] = [e.to_dict() for e in self.evidence]
        prov = self.provenance.to_dict()
        if prov:
            out["provenance"] = prov
        return out


@dataclass(frozen=True)
class ReviewItem:
    """Something a human must decide before this artifact is used.

    Distinct from a failed gate. A gate failure says the artifact is wrong; a
    review item says the artifact is silent on something that matters — a term
    nobody modelled, a discretion the contract leaves to a person.
    """

    topic: str
    detail: str
    owner: str = "operations"        # who this routes to
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"topic": self.topic, "detail": self.detail,
                "owner": self.owner, "blocking": self.blocking}


@dataclass(frozen=True)
class SourceRef:
    """The document this came from, identified by content rather than by name."""

    uri: str = ""
    sha256: str = ""
    media_type: str = "text/plain"
    ingested_by: str = ""            # "html/sec-exhibit", "adapter/azure-di"
    pages: int | None = None

    @staticmethod
    def of(text: str, *, uri: str = "", media_type: str = "text/plain",
           ingested_by: str = "") -> SourceRef:
        return SourceRef(uri=uri, sha256=hashlib.sha256(text.encode()).hexdigest(),
                         media_type=media_type, ingested_by=ingested_by)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in ("", None)}


@dataclass(frozen=True)
class Record:
    """A typed, versioned, evidence-backed artifact with a status."""

    document_id: str
    doc_type: str
    fields: dict[str, Field] = field(default_factory=dict)
    source: SourceRef = field(default_factory=SourceRef)
    source_text: str = ""
    schema_version: str = ""
    status: Status = Status.PROPOSED
    review: tuple[ReviewItem, ...] = ()
    gate_report: dict[str, Any] | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    # --- reading ----------------------------------------------------------

    def value(self, name: str, default: Any = None) -> Any:
        f = self.fields.get(name)
        return f.value if f else default

    def quote(self, name: str) -> str:
        f = self.fields.get(name)
        return f.quote if f else ""

    def missing(self, *required: str) -> list[str]:
        return [n for n in required if n not in self.fields]

    @property
    def blocking_review(self) -> tuple[ReviewItem, ...]:
        return tuple(r for r in self.review if r.blocking)

    # --- transitions ------------------------------------------------------
    # Each returns a new Record. Status is never mutated in place, so the whole
    # chain from source to decision replays instead of being reconstructed.

    def certified(self, gate_report: dict[str, Any] | None = None) -> Record:
        return replace(self, status=Status.CERTIFIED,
                       gate_report=gate_report or self.gate_report)

    def admitted(self) -> Record:
        return replace(self, status=Status.ADMITTED)

    def referred(self, *items: ReviewItem,
                 gate_report: dict[str, Any] | None = None) -> Record:
        return replace(self, status=Status.REFERRED,
                       review=self.review + items,
                       gate_report=gate_report or self.gate_report)

    def abstained(self, *items: ReviewItem) -> Record:
        return replace(self, status=Status.ABSTAINED, review=self.review + items)

    def needing_review(self, *items: ReviewItem) -> Record:
        return replace(self, review=self.review + items)

    # --- interop ----------------------------------------------------------

    def to_artifact(self) -> dict[str, Any]:
        """The dict shape the gate library reads.

        Deliberately the same shape the examples used before this type existed,
        so locators, provenance and status are additive. A gate written last
        month keeps working; a gate written next month can take the Record.
        """
        out: dict[str, Any] = {
            "document_id": self.document_id,
            "kind": self.doc_type,
            "fields": {n: f.to_dict() for n, f in self.fields.items()},
            "_source_text": self.source_text,
            "_status": self.status.value,
        }
        out.update(self.extras)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "doc_type": self.doc_type,
            "schema_version": self.schema_version,
            "status": self.status.value,
            "source": self.source.to_dict(),
            "fields": {n: f.to_dict() for n, f in self.fields.items()},
            "review": [r.to_dict() for r in self.review],
            "gate_report": self.gate_report,
            "extras": self.extras,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @staticmethod
    def from_artifact(artifact: dict[str, Any], *, doc_type: str = "",
                      provenance: Provenance | None = None) -> Record:
        """Lift an untyped extraction into a Record.

        The migration path: an extractor that still returns a plain dict gets a
        typed record without being rewritten, and gains provenance the moment
        the caller knows what produced it.
        """
        prov = provenance or Provenance()
        fields: dict[str, Field] = {}
        for name, entry in (artifact.get("fields") or {}).items():
            if isinstance(entry, dict):
                quote = entry.get("evidence_quote", "")
                ev = (Evidence(quote=quote),) if quote else ()
                fields[name] = Field(name=name, value=entry.get("value"),
                                     evidence=ev, provenance=prov)
            else:
                fields[name] = Field(name=name, value=entry, provenance=prov)

        text = artifact.get("_source_text", "")
        reserved = {"fields", "_source_text", "document_id", "kind", "_status"}
        return Record(
            document_id=artifact.get("document_id", "unknown"),
            doc_type=doc_type or artifact.get("kind", ""),
            fields=fields,
            source=SourceRef.of(text) if text else SourceRef(),
            source_text=text,
            extras={k: v for k, v in artifact.items() if k not in reserved},
        )
