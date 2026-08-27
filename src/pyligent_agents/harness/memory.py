"""Cross-run memory, bound to the evidence it came from.

Distinct from context (this turn), from the workspace (this run), and from the
graph store (this workflow). Memory is what survives all of them — which is
precisely why it is the most dangerous thing in the system.

Everything else here carries evidence, provenance and a status. Memory, in most
agent frameworks, is a bag of notes a model writes and later believes. For a
system that reads documents, that is not a gap; it is a defect with a clock on
it:

    run 1   reads a CSA, notes "ATLAS Threshold is USD 5,000,000"
    ...     the parties adhere to the VM protocol; Threshold becomes zero
    run 9   recalls the note and sizes a call against a band that no longer
            exists — confidently, with no citation anyone can check

That is the same drift the shadow-mode reconciliation exists to find, happening
inside the agent where nothing looks at it. A stale note is worse than no note:
the absence of a fact prompts a lookup, and a wrong fact prevents one.

So a note records **what it was derived from**, by content hash, and recall
checks that hash against the source as it is now.

    memory.write("atlas-threshold", "Threshold is USD 5,000,000.",
                 why="Sizing calls without re-reading Paragraph 11 each time.",
                 derived_from=[Binding("DOC-CSA-ATLAS", sha256_of(text))])

    memory.recall("atlas threshold", sources={"DOC-CSA-ATLAS": current_sha})
    # -> [] once the document changes, and the note is marked STALE

Four states, and the third is the one usually missing:

    FRESH       bound to a source, and the source still hashes the same
    STALE       bound, and the source has changed since
    UNVERIFIED  bound, but no current hash was supplied — we cannot tell
    UNBOUND     no provenance: general knowledge, or written before this existed

`UNVERIFIED` abstains rather than guessing, for the same reason a gate does: a
control that answers when it cannot tell is a control that answers wrongly in
whichever direction its default happens to fall.

Retrieval is lexical and explainable on purpose. An embedding would recall more
and justify less, and a memory whose retrieval you cannot explain is a memory
you cannot audit.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

_SLUG = re.compile(r"[^a-z0-9]+")

# Memory injected into a prompt is context nobody budgeted for. The harness
# governs turns, tokens, spend and time; this is the same discipline applied to
# the one input that grows without anyone deciding to grow it.
DEFAULT_INJECT_BUDGET_CHARS = 1_200


def slug(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")[:60] or "note"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNVERIFIED = "unverified"
    UNBOUND = "unbound"


@dataclass(frozen=True)
class Binding:
    """The document a note was derived from, identified by content.

    By content and not by name: two copies of an agreement under different
    filenames are one source, and a renamed file is not a new fact.
    """

    ref: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"ref": self.ref, "sha256": self.sha256}

    @staticmethod
    def of(ref: str, text: str) -> Binding:
        return Binding(ref=ref, sha256=content_hash(text))


@dataclass(frozen=True)
class Note:
    name: str
    body: str
    why: str = ""
    kind: str = "observation"
    tags: tuple[str, ...] = ()
    derived_from: tuple[Binding, ...] = ()
    run_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    revisions: int = 0

    def freshness(self, sources: Mapping[str, str] | None) -> Freshness:
        if not self.derived_from:
            return Freshness.UNBOUND
        if not sources:
            return Freshness.UNVERIFIED
        seen_any = False
        for b in self.derived_from:
            current = sources.get(b.ref)
            if current is None:
                continue
            seen_any = True
            if current != b.sha256:
                return Freshness.STALE
        return Freshness.FRESH if seen_any else Freshness.UNVERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "body": self.body,
            "why": self.why, "tags": list(self.tags),
            "derived_from": [b.to_dict() for b in self.derived_from],
            "run_id": self.run_id, "created_at": self.created_at,
            "updated_at": self.updated_at, "revisions": self.revisions,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> Note:
        return Note(
            name=d.get("name", ""), body=d.get("body", ""), why=d.get("why", ""),
            kind=d.get("kind", "observation"), tags=tuple(d.get("tags") or ()),
            derived_from=tuple(
                Binding(b["ref"], b["sha256"]) for b in (d.get("derived_from") or [])
            ),
            run_id=d.get("run_id", ""),
            created_at=d.get("created_at", 0.0), updated_at=d.get("updated_at", 0.0),
            revisions=d.get("revisions", 0),
        )


@dataclass(frozen=True)
class Recalled:
    note: Note
    freshness: Freshness
    score: int

    @property
    def usable(self) -> bool:
        return self.freshness in (Freshness.FRESH, Freshness.UNBOUND)


@dataclass
class MemoryStore:
    root: Path
    inject_budget_chars: int = DEFAULT_INJECT_BUDGET_CHARS
    # Every note this store handed out, in order. The audit trail has to be able
    # to say which remembered facts a decision leaned on.
    used: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    # --- writing ----------------------------------------------------------

    def _path(self, name: str) -> Path:
        return self.root / f"{slug(name)}.json"

    def write(self, name: str, body: str, *, why: str = "",
              kind: str = "observation", tags: Iterable[str] | None = None,
              derived_from: Iterable[Binding] | None = None,
              run_id: str = "") -> Note:
        """Create or update. Never append a near-duplicate.

        `why` is not decoration. A note recording an outcome without its reason
        is useless the next time conditions differ, which is the only time
        anyone reads it.
        """
        prior = self.read(name)
        note = Note(
            name=slug(name), body=body, why=why, kind=kind,
            tags=tuple(tags or ()), derived_from=tuple(derived_from or ()),
            run_id=run_id,
            created_at=prior.created_at if prior else time.time(),
            updated_at=time.time(),
            revisions=(prior.revisions if prior else 0) + 1,
        )
        self._path(name).write_text(json.dumps(note.to_dict(), indent=2),
                                    encoding="utf-8")
        return note

    def read(self, name: str) -> Note | None:
        path = self._path(name)
        if not path.exists():
            return None
        return Note.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def forget(self, name: str) -> bool:
        """Wrong notes are worse than missing ones. Deleting is maintenance."""
        path = self._path(name)
        if path.exists():
            path.unlink()
            return True
        return False

    # --- reading ----------------------------------------------------------

    def _all(self) -> list[Note]:
        out = []
        for path in sorted(self.root.glob("*.json")):
            try:
                out.append(Note.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, KeyError):
                continue        # a corrupt note is not a reason to lose the rest
        return out

    def recall(self, query: str, *, sources: Mapping[str, str] | None = None,
               limit: int = 5, include_unusable: bool = False,
               strict: bool = False) -> list[Recalled]:
        """Notes matching the query, with staleness resolved against `sources`.

        STALE and UNVERIFIED notes are withheld by default. `strict=True` also
        withholds UNBOUND ones — for a run where nothing without provenance may
        influence a decision.
        """
        terms = [t for t in query.lower().split() if len(t) > 2]
        hits: list[Recalled] = []
        for note in self._all():
            hay = f"{note.name} {note.body} {note.why} {' '.join(note.tags)}".lower()
            score = sum(hay.count(t) for t in terms)
            if not score:
                continue
            fresh = note.freshness(sources)
            usable = fresh is Freshness.FRESH or (
                fresh is Freshness.UNBOUND and not strict)
            if usable or include_unusable:
                hits.append(Recalled(note, fresh, score))

        hits.sort(key=lambda r: (-r.score, r.note.name))
        chosen = hits[:limit]
        self.used.extend(r.note.name for r in chosen if r.usable)
        return chosen

    def stale(self, sources: Mapping[str, str]) -> list[Recalled]:
        """Every note the current sources have overtaken. Maintenance surface."""
        return [
            Recalled(n, Freshness.STALE, 0) for n in self._all()
            if n.freshness(sources) is Freshness.STALE
        ]

    def index(self, sources: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
        """One line per note. This is what gets injected, not the bodies."""
        return [
            {"name": n.name, "kind": n.kind,
             "summary": n.body.split("\n", 1)[0][:120],
             "freshness": n.freshness(sources).value}
            for n in self._all()
        ]

    # --- injection --------------------------------------------------------

    def inject(self, query: str = "", *, sources: Mapping[str, str] | None = None,
               budget_chars: int | None = None, strict: bool = False) -> str:
        """The text to place in a prompt, capped.

        Memory that grows without a budget is a context leak with a good
        reputation. This is the same discipline the governor applies to turns
        and spend, applied to the one input nobody decided to grow.

        Withheld notes are counted, not hidden: a prompt that silently drops
        half of what it recalled is worse than one that says so.
        """
        budget = self.inject_budget_chars if budget_chars is None else budget_chars
        recalled = self.recall(query or "", sources=sources, limit=50,
                               include_unusable=True, strict=strict)
        usable = [r for r in recalled if r.usable]
        withheld = [r for r in recalled if not r.usable]

        lines: list[str] = []
        used = 0
        for r in usable:
            line = f"- {r.note.name}: {r.note.body.splitlines()[0][:160]}"
            if r.note.why:
                line += f"  (why: {r.note.why[:100]})"
            if used + len(line) + 1 > budget:
                break
            lines.append(line)
            used += len(line) + 1

        if not lines and not withheld:
            return ""

        head = ["Remembered from previous runs. Each is a claim you may check, "
                "not a fact you must accept."]
        if withheld:
            stale = sum(1 for r in withheld if r.freshness is Freshness.STALE)
            unver = sum(1 for r in withheld if r.freshness is Freshness.UNVERIFIED)
            parts = []
            if stale:
                parts.append(f"{stale} withheld: the source has changed since")
            if unver:
                parts.append(f"{unver} withheld: could not verify the source")
            head.append("(" + "; ".join(parts) + ")")
        return "\n".join(head + lines)
