"""Layer 3 — the graph: build it, validate it, then run it.

The argument for a graph over "an orchestrator agent that decides what to do
next" is not elegance. It is that a graph is **inspectable before it executes**:

* every dependency is declared, so a missing input is a build-time error rather
  than a confident answer built on a hole;
* cycles are detected rather than discovered on the invoice;
* the shape can be rendered, reviewed, and put in a change record;
* each node checkpoints, so a crash resumes instead of restarting;
* the same run replays deterministically, which is what makes an audit possible.

An LLM deciding the next step every turn gives up all five, in exchange for
flexibility most production workflows do not want. Where you *do* want it, put
an `AgentNode` inside a node — the flexibility is scoped to where it earns its
keep.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.errors import GraphError
from .nodes import Node


@dataclass
class Graph:
    name: str
    nodes: dict[str, Node] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    # Keys present in the initial state, so `requires` can be satisfied by
    # inputs as well as by upstream nodes.
    seeds: tuple[str, ...] = ()

    def add(self, node: Node) -> Graph:
        if node.id in self.nodes:
            raise GraphError(f"Duplicate node id '{node.id}'.")
        self.nodes[node.id] = node
        self.order.append(node.id)
        return self

    def extend(self, *nodes: Node) -> Graph:
        for n in nodes:
            self.add(n)
        return self

    # --- validation -------------------------------------------------------

    def validate(self) -> Graph:
        """Every failure mode a graph can have, caught before it costs anything."""
        self._check_dependencies()
        self._check_cycles()
        self._check_dataflow()
        self._check_side_effects()
        return self

    def _check_dependencies(self) -> None:
        for node in self.nodes.values():
            unknown = [d for d in node.depends_on if d not in self.nodes]
            if unknown:
                raise GraphError(
                    f"Node '{node.id}' depends on unknown node(s): {', '.join(unknown)}."
                )
            if node.id in node.depends_on:
                raise GraphError(f"Node '{node.id}' depends on itself.")

    def _check_cycles(self) -> None:
        colour: dict[str, int] = {}

        def visit(nid: str, path: list[str]) -> None:
            state = colour.get(nid, 0)
            if state == 1:
                cycle = " -> ".join([*path[path.index(nid):], nid])
                raise GraphError(f"Cycle detected: {cycle}")
            if state == 2:
                return
            colour[nid] = 1
            for dep in self.nodes[nid].depends_on:
                visit(dep, [*path, nid])
            colour[nid] = 2

        for nid in self.order:
            visit(nid, [])

    def _check_dataflow(self) -> None:
        """A node may only require what something upstream provides."""
        for nid in self.topological():
            node = self.nodes[nid]
            available = set(self.seeds)
            for dep in self._ancestors(nid):
                available.update(self.nodes[dep].provides)
            missing = [k for k in node.requires if k not in available]
            if missing:
                raise GraphError(
                    f"Node '{nid}' requires {missing} which nothing upstream provides. "
                    f"Available at that point: {sorted(available) or 'nothing'}. "
                    f"Add it to a predecessor's `provides`, or to the graph's `seeds`."
                )

    def _check_side_effects(self) -> None:
        """A node that can undo an effect must be able to make it exactly once.

        We cannot detect an *undeclared* side effect from the graph structure —
        `idempotency` being present is precisely what declares one (see
        `nodes.py`), so a node without it has, by contract, nothing to protect.
        Guessing from `provides` would flag every pure computation that retries.

        What we can catch is the incoherent pair: a node that declares
        `compensate` — an undo for an effect that landed — while declaring no
        key by which that effect is made once. On resume the graph has no way to
        know whether the effect happened, so it cannot know whether to undo it.
        That is the duplicate custodian instruction, visible at build time.
        """
        for nid, node in self.nodes.items():
            if node.compensate is not None and node.idempotency is None:
                raise GraphError(
                    f"Node '{nid}' declares `compensate` but no `idempotency`. "
                    f"Compensation undoes an effect that landed; without a key the "
                    f"graph cannot tell whether it landed, so it can neither make it "
                    f"once nor safely undo it. Add `idempotency=` deriving a key from "
                    f"the facts of the action."
                )

    def _ancestors(self, nid: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self.nodes[nid].depends_on)
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.nodes[cur].depends_on)
        return seen

    # --- shape ------------------------------------------------------------

    def topological(self) -> list[str]:
        """Deterministic topological order: insertion order breaks ties.

        Determinism matters — a replayed run must visit nodes in the same order
        or the "identical replay" property is a coincidence.
        """
        indeg = {nid: len(self.nodes[nid].depends_on) for nid in self.order}
        ready = [nid for nid in self.order if indeg[nid] == 0]
        out: list[str] = []
        while ready:
            nid = ready.pop(0)
            out.append(nid)
            for other in self.order:
                if nid in self.nodes[other].depends_on:
                    indeg[other] -= 1
                    if indeg[other] == 0:
                        ready.append(other)
        if len(out) != len(self.order):
            raise GraphError("Graph is not a DAG.")
        return out

    def layers(self) -> list[list[str]]:
        """Nodes grouped by depth. Everything in one layer could run in parallel."""
        depth: dict[str, int] = {}
        for nid in self.topological():
            deps = self.nodes[nid].depends_on
            depth[nid] = 1 + max((depth[d] for d in deps), default=-1)
        out: list[list[str]] = []
        for nid, d in depth.items():
            while len(out) <= d:
                out.append([])
            out[d].append(nid)
        return out

    def render(self) -> str:
        """ASCII, because a graph you cannot see in a terminal is a graph
        nobody reviews."""
        lines = [f"graph: {self.name}"]
        if self.seeds:
            lines.append(f"  seeds: {', '.join(self.seeds)}")
        for i, layer in enumerate(self.layers()):
            lines.append(f"  ── layer {i} " + "─" * 44)
            for nid in layer:
                n = self.nodes[nid]
                deps = f" after {','.join(n.depends_on)}" if n.depends_on else ""
                marks = []
                if n.idempotency:
                    marks.append("idempotent")
                if n.retry.max_attempts > 1:
                    marks.append(f"retry×{n.retry.max_attempts}")
                if n.when:
                    marks.append("conditional")
                tag = f"  ({', '.join(marks)})" if marks else ""
                lines.append(f"     {nid:<20} {n.kind:<8}{deps}{tag}")
                if n.provides:
                    lines.append(f"     {'':<20} provides: {', '.join(n.provides)}")
        return "\n".join(lines)

    def to_mermaid(self) -> str:
        """For a design doc. Node shape encodes kind."""
        shapes = {
            "step": ("[", "]"), "agent": ("([", "])"), "gate": ("{{", "}}"),
            "human": (">", "]"), "map": ("[/", "/]"), "reduce": ("[\\", "\\]"),
        }
        out = ["graph TD"]
        for nid in self.topological():
            n = self.nodes[nid]
            lo, hi = shapes.get(n.kind, ("[", "]"))
            out.append(f'    {nid}{lo}"{nid}<br/><i>{n.kind}</i>"{hi}')
        for nid in self.topological():
            for dep in self.nodes[nid].depends_on:
                arrow = "-.->" if self.nodes[nid].when else "-->"
                out.append(f"    {dep} {arrow} {nid}")
        return "\n".join(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "seeds": list(self.seeds),
            "layers": self.layers(),
            "nodes": [
                {
                    "id": n.id, "kind": n.kind, "depends_on": list(n.depends_on),
                    "requires": list(n.requires), "provides": list(n.provides),
                    "idempotent": n.idempotency is not None,
                    "retries": n.retry.max_attempts, "conditional": n.when is not None,
                    "description": n.description,
                }
                for n in (self.nodes[i] for i in self.topological())
            ],
        }
