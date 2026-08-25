"""The graph runner: execute, checkpoint, resume, replay.

The execution contract, in five lines:

1. A node that already finished is **replayed from the checkpoint**, not re-run.
2. A node with an idempotency key whose effect is already on the ledger is
   **replayed from the ledger**, even if its checkpoint was lost.
3. A node writes "started" before it works and its result after, in that order.
4. A failed node blocks its dependents; it does not let the graph invent a
   result to carry on with.
5. A human gate **pauses** the run. Paused is not failed.

Rules 1 and 2 are not redundant. Rule 1 covers an ordinary restart. Rule 2
covers the nasty window where the side effect landed externally and the state
write did not — the exact window in which a naive resume double-sends.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import HumanApprovalRequired, classify
from ..core.ids import run_id as new_run_id
from ..harness.harness import Harness
from .graph import Graph
from .nodes import Node, NodeContext, NodeStatus
from .state import GraphState
from .store import GraphStore


@dataclass
class RunResult:
    run_id: str
    graph: str
    status: str                      # completed | paused | failed
    state: GraphState
    node_status: dict[str, str] = field(default_factory=dict)
    paused_on: str = ""
    pause_prompt: str = ""
    failed_on: str = ""
    error: str = ""
    replayed: list[str] = field(default_factory=list)
    resumed: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "graph": self.graph, "status": self.status,
            "resumed": self.resumed, "replayed": self.replayed,
            "node_status": self.node_status, "paused_on": self.paused_on,
            "pause_prompt": self.pause_prompt, "failed_on": self.failed_on,
            "error": self.error, "outputs": self.state.outputs,
        }

    def render(self) -> str:
        icons = {"done": "✓", "failed": "✗", "skipped": "—", "paused": "⏸",
                 "blocked": "▪", "pending": "·"}
        lines = [f"run {self.run_id}  graph={self.graph}  status={self.status}"]
        for nid, st in self.node_status.items():
            mark = " (replayed)" if nid in self.replayed else ""
            lines.append(f"  {icons.get(st, '?')} {nid:<22} {st}{mark}")
        return "\n".join(lines)


class GraphRunner:
    def __init__(self, graph: Graph, store: GraphStore, harness: Harness):
        self.graph = graph.validate()   # never run an unvalidated graph
        self.store = store
        self.h = harness

    # --- entry points -----------------------------------------------------

    def start(self, goal: str, seed: dict[str, Any] | None = None,
              *, run_id: str | None = None) -> RunResult:
        rid = run_id or new_run_id("gr")
        state = GraphState(run_id=rid, goal=goal, data=dict(seed or {}))
        self.store.save_run(rid, self.graph.name, "running", state.to_dict())
        return self._execute(state, resumed=False)

    def resume(self, run_id: str, *, decisions: dict[str, Any] | None = None) -> RunResult:
        """Pick a run back up. Optionally supply pending human decisions."""
        record = self.store.load_run(run_id)
        if record is None:
            raise KeyError(f"No run '{run_id}'.")
        state = GraphState.from_dict(record["state"])
        for node_id, decision in (decisions or {}).items():
            state.set(f"decision:{node_id}", decision)
        return self._execute(state, resumed=True)

    # --- the loop ---------------------------------------------------------

    def _execute(self, state: GraphState, *, resumed: bool) -> RunResult:
        g = self.graph
        checkpoints = self.store.node_runs(state.run_id)
        status: dict[str, str] = {
            nid: checkpoints.get(nid, {}).get("status", NodeStatus.PENDING.value)
            for nid in g.topological()
        }
        replayed: list[str] = []
        result = RunResult(state.run_id, g.name, "running", state, status, resumed=resumed)

        for nid in g.topological():
            node = g.nodes[nid]

            # --- rule 1: already finished? replay from the checkpoint. ---
            if status[nid] == NodeStatus.DONE.value:
                cached = checkpoints.get(nid, {}).get("output")
                state.record_output(nid, cached)
                self._reinstate(node, state, cached)
                replayed.append(nid)
                self.store.span(state.run_id, nid, "replay", {"source": "checkpoint"})
                continue

            # --- blocked by an upstream failure? ---
            blocked = [d for d in node.depends_on
                       if status[d] in {NodeStatus.FAILED.value, NodeStatus.BLOCKED.value}]
            if blocked:
                status[nid] = NodeStatus.BLOCKED.value
                self.store.finish_node(state.run_id, nid, "blocked",
                                       error=f"blocked by {', '.join(blocked)}")
                continue

            # --- conditional routing ---
            if node.when is not None and not node.when(state):
                status[nid] = NodeStatus.SKIPPED.value
                self.store.finish_node(state.run_id, nid, "skipped")
                self.store.span(state.run_id, nid, "skip", {"reason": "guard returned false"})
                continue

            outcome = self._run_node(node, state, status, replayed)
            if outcome is not None:
                result.status = outcome[0]
                if outcome[0] == "paused":
                    result.paused_on, result.pause_prompt = nid, outcome[1]
                else:
                    result.failed_on, result.error = nid, outcome[1]
                    # Rule 4, made visible: everything downstream is blocked, and
                    # says so. A dependent left "pending" reads like work that
                    # might still happen.
                    self._block_dependents(nid, status, state.run_id)
                result.replayed = replayed
                self.store.save_run(state.run_id, g.name, result.status, state.to_dict())
                return result

        terminal = "completed" if all(
            s in {NodeStatus.DONE.value, NodeStatus.SKIPPED.value} for s in status.values()
        ) else "failed"
        result.status = terminal
        result.replayed = replayed
        self.store.save_run(state.run_id, g.name, terminal, state.to_dict())
        self.h.ledger.record("graph_finished", graph=g.name, status=terminal,
                             replayed=len(replayed))
        return result

    def _run_node(
        self, node: Node, state: GraphState, status: dict[str, str], replayed: list[str]
    ) -> tuple[str, str] | None:
        """Execute one node. Returns None on success, else (status, message)."""
        rid = state.run_id

        # --- rule 2: has this side effect already fired? ---
        key = node.idempotency(state) if node.idempotency else None
        if key is not None:
            prior = self.store.get_effect(rid, key)
            if prior is not None:
                state.record_output(node.id, prior["result"])
                self._reinstate(node, state, prior["result"])
                status[node.id] = NodeStatus.DONE.value
                replayed.append(node.id)
                self.store.finish_node(rid, node.id, "done", prior["result"])
                self.store.span(rid, node.id, "replay",
                                {"source": "effect_ledger", "key": key})
                return None

        last_error = ""
        for attempt in range(1, node.retry.max_attempts + 1):
            # --- rule 3: write "started" BEFORE the work. ---
            self.store.start_node(rid, node.id, attempt, state.fingerprint(node.requires))
            started = time.perf_counter()
            try:
                output = node.execute(state, NodeContext(rid, self.h, attempt))
            except HumanApprovalRequired as pause:
                # --- rule 5: paused is not failed. ---
                status[node.id] = NodeStatus.PAUSED.value
                self.store.finish_node(rid, node.id, "paused", error=pause.prompt)
                self.store.span(rid, node.id, "pause", {"prompt": pause.prompt,
                                                        "payload": pause.payload})
                self.h.ledger.record("human_gate", node=node.id, prompt=pause.prompt)
                return ("paused", pause.prompt)
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                self.store.span(rid, node.id, "error",
                                {"attempt": attempt, "class": classify(exc).value,
                                 "message": last_error})
                if attempt < node.retry.max_attempts:
                    time.sleep(min(node.retry.backoff_s * (2 ** (attempt - 1)), 2.0))
                    continue
                # --- rule 4: fail the node; do not invent a result. ---
                status[node.id] = NodeStatus.FAILED.value
                self.store.finish_node(rid, node.id, "failed", error=last_error)
                self.h.ledger.error(message=f"node {node.id} failed", error=last_error)
                self._compensate(state, status)
                return ("failed", last_error)

            # Record the effect in the same moment we learn it happened.
            if key is not None:
                self.store.record_effect(rid, key, node.id, output)

            state.record_output(node.id, output)
            self._reinstate(node, state, output)
            status[node.id] = NodeStatus.DONE.value
            self.store.finish_node(rid, node.id, "done", output)
            self.store.save_run(rid, self.graph.name, "running", state.to_dict())
            self.store.span(rid, node.id, "done",
                            {"attempt": attempt, "kind": node.kind,
                             "ms": round((time.perf_counter() - started) * 1000, 2)})
            return None
        return ("failed", last_error)  # pragma: no cover

    def _block_dependents(self, failed: str, status: dict[str, str], run_id: str) -> None:
        """Mark every transitive dependent blocked, so nothing looks pending."""
        blocked = {failed}
        for nid in self.graph.topological():
            if nid in blocked:
                continue
            if any(d in blocked for d in self.graph.nodes[nid].depends_on):
                blocked.add(nid)
                if status.get(nid) in {None, NodeStatus.PENDING.value}:
                    status[nid] = NodeStatus.BLOCKED.value
                    self.store.finish_node(run_id, nid, "blocked",
                                           error=f"blocked by failed node '{failed}'")

    @staticmethod
    def _reinstate(node: Node, state: GraphState, output: Any) -> None:
        """Publish a node's declared `provides` into shared state.

        Run on the replay path too, which is what makes a resumed run see
        exactly the state the original one did.
        """
        if not node.provides:
            return
        if len(node.provides) == 1:
            key = node.provides[0]
            if isinstance(output, dict) and key in output:
                state.set(key, output[key])
            else:
                state.set(key, output)
            return
        if isinstance(output, dict):
            for key in node.provides:
                if key in output:
                    state.set(key, output[key])

    def _compensate(self, state: GraphState, status: dict[str, str]) -> None:
        """Unwind completed side effects, newest first.

        Best-effort by design: a compensation that itself fails is logged, not
        raised, because the original failure is the one a human needs to see.
        """
        for nid in reversed(self.graph.topological()):
            node = self.graph.nodes[nid]
            if node.compensate is None or status.get(nid) != NodeStatus.DONE.value:
                continue
            try:
                node.compensate(state, state.outputs.get(nid))
                self.store.span(state.run_id, nid, "compensated", {})
            except Exception as exc:  # noqa: BLE001
                self.store.span(state.run_id, nid, "compensation_failed",
                                {"error": str(exc)})
