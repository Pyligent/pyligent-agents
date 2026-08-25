"""The run workspace: where big things live instead of in the context window.

The single highest-leverage move in context engineering is refusing to put
something in the transcript just because a tool produced it. A 40,000-character
inventory dump is read once, reasoned over once, and then sits in every
subsequent request for the rest of the run, at full price, crowding out the
things that actually matter.

So: large tool results are written here, and the model gets a **preview plus a
handle**. If it needs the rest, it calls `read_artifact` with an offset. That is
progressive disclosure, and it is the difference between a run that degrades
after twenty turns and one that does not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.ids import content_hash


@dataclass
class Artifact:
    handle: str
    path: Path
    kind: str
    total_chars: int
    preview: str
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "kind": self.kind,
            "total_chars": self.total_chars,
            "source": self.source,
        }


@dataclass
class Workspace:
    """A directory plus an index. Scoped to one run."""

    root: Path
    artifacts: dict[str, Artifact] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, body: str, *, kind: str = "tool_result", source: str = "",
            preview_chars: int = 600) -> Artifact:
        """Store a blob and return a handle.

        The handle is content-addressed, so storing the same tool result twice
        — which happens constantly on a retry — reuses one file and one handle
        instead of growing the index.
        """
        handle = f"art_{content_hash(body)}"
        if handle in self.artifacts:
            return self.artifacts[handle]

        path = self.root / f"{handle}.txt"
        path.write_text(body, encoding="utf-8")
        art = Artifact(
            handle=handle,
            path=path,
            kind=kind,
            total_chars=len(body),
            preview=body[:preview_chars],
            source=source,
        )
        self.artifacts[handle] = art
        return art

    def read(self, handle: str, *, offset: int = 0, limit: int = 4_000) -> str:
        art = self.artifacts.get(handle)
        if art is None:
            known = ", ".join(sorted(self.artifacts)) or "none"
            raise KeyError(f"No artifact '{handle}'. Known handles: {known}.")
        body = art.path.read_text(encoding="utf-8")
        chunk = body[offset : offset + limit]
        end = offset + len(chunk)
        tail = (
            f"\n\n[... {art.total_chars - end:,} more characters. "
            f"Call read_artifact with offset={end} to continue.]"
            if end < art.total_chars
            else ""
        )
        return chunk + tail

    def index(self) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self.artifacts.values()]

    def write_json(self, name: str, payload: Any) -> Path:
        """For run outputs a human will open afterwards, not for the model."""
        path = self.root / name
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path
