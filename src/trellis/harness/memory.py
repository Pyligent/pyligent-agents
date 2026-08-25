"""Cross-run memory: one fact per file, with an index.

Distinct from context (this turn), from the workspace (this run), and from the
graph store (this workflow). Memory is what survives all of them.

The discipline matters more than the storage. Notes are small, one fact each,
and say *why* — a note that records an outcome without its reason is useless
the next time conditions differ. Duplicates are updated, not appended, or the
store becomes a transcript nobody reads.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")[:60] or "note"


@dataclass
class MemoryStore:
    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.root / f"{slug(name)}.json"

    def write(self, name: str, body: str, *, kind: str = "observation",
              tags: list[str] | None = None) -> Path:
        """Create or update. Never append a near-duplicate."""
        path = self._path(name)
        prior = self.read(name)
        record = {
            "name": slug(name),
            "kind": kind,
            "body": body,
            "tags": tags or [],
            "created_at": (prior or {}).get("created_at", time.time()),
            "updated_at": time.time(),
            "revisions": (prior or {}).get("revisions", 0) + 1,
        }
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return path

    def read(self, name: str) -> dict[str, Any] | None:
        path = self._path(name)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def forget(self, name: str) -> bool:
        """Wrong notes are worse than missing ones. Deleting is maintenance."""
        path = self._path(name)
        if path.exists():
            path.unlink()
            return True
        return False

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        terms = [t for t in query.lower().split() if len(t) > 2]
        hits = []
        for path in sorted(self.root.glob("*.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            hay = f"{rec['name']} {rec['body']} {' '.join(rec.get('tags', []))}".lower()
            score = sum(hay.count(t) for t in terms)
            if score:
                hits.append((score, rec))
        hits.sort(key=lambda p: (-p[0], p[1]["name"]))
        return [r for _, r in hits[:limit]]

    def index(self) -> list[dict[str, Any]]:
        """One line per note. This is what gets injected, not the bodies."""
        out = []
        for path in sorted(self.root.glob("*.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            out.append(
                {
                    "name": rec["name"],
                    "kind": rec["kind"],
                    "summary": rec["body"].split("\n", 1)[0][:120],
                }
            )
        return out
