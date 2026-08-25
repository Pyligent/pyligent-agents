"""The blackboard: the only thing nodes share.

Nodes do not call each other and do not hold references to each other. They
read from state and write to state, which is what makes the graph replayable:
re-running a node with the same inputs produces the same writes, and a run can
be reconstructed from its checkpoints alone.

Two namespaces, kept apart on purpose:

    outputs[node_id]  what each node returned — the audit trail
    data[key]         the shared working set — what nodes actually read

Mixing them is how you end up with a node that depends on another node's
internal shape and breaks when it changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.ids import content_hash


@dataclass
class GraphState:
    run_id: str
    goal: str
    data: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    version: int = 0

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.version += 1

    def update(self, values: dict[str, Any]) -> None:
        self.data.update(values)
        self.version += 1

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def require(self, key: str) -> Any:
        """Read a value a node declared it needs.

        Fails loudly with the keys that *are* present. A node reading a missing
        key and silently getting `None` is how a graph produces a confident
        answer built on a hole.
        """
        if key not in self.data:
            raise KeyError(
                f"Graph state has no '{key}'. Available: {sorted(self.data)}. "
                f"Declare it in the producing node's `provides`."
            )
        return self.data[key]

    def record_output(self, node_id: str, value: Any) -> None:
        self.outputs[node_id] = value
        self.version += 1

    def fingerprint(self, keys: tuple[str, ...] = ()) -> str:
        """Stable hash of the inputs a node will read.

        Used for idempotency and for proving a replayed run saw identical
        inputs. Sorted keys make it order-independent.
        """
        subset = {k: self.data.get(k) for k in keys} if keys else self.data
        return content_hash(subset)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "version": self.version,
            "data": self.data,
            "outputs": self.outputs,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GraphState:
        return cls(
            run_id=payload["run_id"],
            goal=payload.get("goal", ""),
            data=payload.get("data", {}),
            outputs=payload.get("outputs", {}),
            version=payload.get("version", 0),
        )
