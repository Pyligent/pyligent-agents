"""The run ledger.

An agent you cannot reconstruct after the fact is an agent you cannot operate.
When a counterparty asks why the desk called for $2.3m, "the agent decided that"
is not an answer a control function accepts.

Every level writes to a ledger. It records what the model was asked, which tools
ran with which arguments, which failed, what the gates said, and what it cost.
It is deliberately plain: a list of timestamped events that serialises to JSON.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TraceEvent:
    seq: int
    ts: float
    kind: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": round(self.ts, 6),
            "kind": self.kind,
            "detail": self.detail,
        }


@dataclass
class RunLedger:
    """An append-only record of one agent run."""

    run_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex[:12]}")
    label: str = ""
    events: list[TraceEvent] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def record(self, kind: str, **detail: Any) -> TraceEvent:
        event = TraceEvent(
            seq=len(self.events), ts=time.time() - self.started_at, kind=kind, detail=detail
        )
        self.events.append(event)
        return event

    # Convenience wrappers, so call sites read like the thing they describe.

    def model_call(self, *, model: str, turn: int, stop_reason: str, **extra: Any) -> None:
        self.record("model_call", model=model, turn=turn, stop_reason=stop_reason, **extra)

    def tool_call(
        self, *, name: str, arguments: dict[str, Any], is_error: bool, denied: bool = False
    ) -> None:
        self.record(
            "tool_call",
            name=name,
            arguments=arguments,
            is_error=is_error,
            denied=denied,
        )

    def gate(self, *, name: str, passed: bool, message: str) -> None:
        self.record("gate", name=name, passed=passed, message=message)

    def error(self, *, message: str, **extra: Any) -> None:
        self.record("error", message=message, **extra)

    # --- reporting --------------------------------------------------------

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for event in self.events:
            out[event.kind] = out.get(event.kind, 0) + 1
        return out

    def tool_failures(self) -> list[TraceEvent]:
        return [
            e for e in self.events if e.kind == "tool_call" and e.detail.get("is_error")
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "label": self.label,
            "duration_s": round(time.time() - self.started_at, 3),
            "counts": self.counts(),
            "events": [e.to_dict() for e in self.events],
        }

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return target

    def render(self, *, limit: int | None = None) -> str:
        """A human-readable trace for terminals and demos."""
        lines = [f"── trace {self.run_id} {self.label}".ljust(72, "─")]
        events = self.events if limit is None else self.events[-limit:]
        for event in events:
            detail = event.detail
            if event.kind == "model_call":
                body = (
                    f"turn {detail.get('turn')} model={detail.get('model')} "
                    f"stop={detail.get('stop_reason')}"
                )
            elif event.kind == "tool_call":
                mark = "DENIED" if detail.get("denied") else ("ERR" if detail.get("is_error") else "ok")
                body = f"{detail.get('name')} [{mark}] {json.dumps(detail.get('arguments', {}))}"
            elif event.kind == "gate":
                body = f"{'PASS' if detail.get('passed') else 'FAIL'} {detail.get('name')}: {detail.get('message')}"
            else:
                body = json.dumps(detail, default=str)
            lines.append(f"  {event.ts:6.2f}s {event.kind:<12} {body}")
        return "\n".join(lines)
