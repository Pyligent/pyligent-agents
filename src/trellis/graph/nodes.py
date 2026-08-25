"""Node kinds. A small, closed set — on purpose.

Every workflow anyone has asked for here decomposes into six shapes. Keeping the
set closed is what makes a graph *analysable*: you can validate it, render it,
resume it and reason about its cost without executing it.

    Step        deterministic code. No model, no ambiguity.
    AgentNode   a Level-2 loop with a contract.
    GateNode    machine-checkable predicates. Routes on pass/fail.
    HumanGate   pauses the run and waits for a recorded decision.
    MapNode     fan out over items; one child result per item.
    ReduceNode  fan in; combine children into one output.

Each node declares `requires` and `provides` so the graph can be validated
before it runs, and each may declare an `idempotency` function so its side
effect fires exactly once across any number of resumes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from ..core.errors import HumanApprovalRequired
from .state import GraphState

if TYPE_CHECKING:  # pragma: no cover
    from ..harness.harness import Harness


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    PAUSED = "paused"


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_s: float = 0.5

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")


@dataclass
class NodeContext:
    """What the runner hands a node. Deliberately narrow."""

    run_id: str
    harness: Harness
    attempt: int = 1
    children: list[Any] = field(default_factory=list)  # MapNode results, for ReduceNode


@dataclass
class Node:
    """Base node. Subclasses implement `execute`."""

    id: str
    kind: str = "node"
    depends_on: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    # Facts -> key. Present means "this node has an external side effect".
    idempotency: Callable[[GraphState], str] | None = None
    # Undo, if this node's effect landed and a later node failed.
    compensate: Callable[[GraphState, Any], None] | None = None
    # Conditional routing. False means SKIPPED, not failed — dependents still
    # run. This is how a gate failure reaches a remediation branch.
    when: Callable[[GraphState], bool] | None = None
    description: str = ""

    def execute(self, state: GraphState, ctx: NodeContext) -> Any:  # pragma: no cover
        raise NotImplementedError

    def label(self) -> str:
        return f"{self.id} [{self.kind}]"


@dataclass
class Step(Node):
    """Deterministic code. The cheapest, most testable node there is.

    Push everything you can into these. A workflow whose model calls are the
    exception rather than the loop is faster, cheaper and auditable — that is
    why the dispute graph costs less than a single tool-loop question.
    """

    fn: Callable[[GraphState], Any] = None  # type: ignore[assignment]
    kind: str = "step"

    def execute(self, state: GraphState, ctx: NodeContext) -> Any:
        return self.fn(state)


@dataclass
class AgentNode(Node):
    """One agent loop, with its own contract and its own tool surface."""

    build: Callable[[Harness, GraphState], Any] = None  # type: ignore[assignment]
    task: Callable[[GraphState], str] = None            # type: ignore[assignment]
    # A narrower registry, not merely an unapproved one: a tool absent from a
    # subagent's registry cannot be reached by anything it reads.
    tools: tuple[str, ...] | None = None
    kind: str = "agent"

    def execute(self, state: GraphState, ctx: NodeContext) -> Any:
        harness = ctx.harness
        if self.tools is not None:
            harness = harness.child(registry=harness.registry.clone(*self.tools))
        agent = self.build(harness, state)
        result = agent.run(self.task(state))
        # Return the SUMMARY, never the transcript. An orchestrating graph whose
        # state fills with subagent reasoning has lost the point of delegating.
        return {
            "ok": result.ok,
            "answer": result.answer,
            "artifact": result.artifact,
            "turns": result.turns,
            "tools_used": [o.tool_name for o in result.outcomes],
            "degraded": result.degraded,
        }


@dataclass
class GateNode(Node):
    """Machine-checkable stop condition, in the middle of a workflow.

    Returns a report and raises nothing. The graph routes on `passed`, which is
    what lets a failed gate reopen the node that owns it instead of killing the
    run.
    """

    gates: Any = None
    subject: str = "artifact"
    kind: str = "gate"

    def execute(self, state: GraphState, ctx: NodeContext) -> Any:
        report = self.gates.evaluate(state.require(self.subject))
        ctx.harness.ledger.record(
            "gate_report", node=self.id, passed=report.passed,
            failed=[f.name for f in report.failures],
        )
        return {
            "passed": report.passed,
            "failed": [f.name for f in report.failures],
            "results": [r.to_dict() for r in report.results],
        }


@dataclass
class HumanGate(Node):
    """Pause for a decision a machine must not make.

    Raises `HumanApprovalRequired`, which the runner treats as *paused*, not
    *failed*: state is checkpointed and the run resumes once the decision is
    recorded. The agent is never left spinning while it waits.
    """

    prompt: Callable[[GraphState], str] = None   # type: ignore[assignment]
    payload: Callable[[GraphState], dict[str, Any]] = lambda s: {}
    decision_key: str = "approval"
    kind: str = "human"

    def execute(self, state: GraphState, ctx: NodeContext) -> Any:
        decision = state.get(f"decision:{self.id}")
        if decision is None:
            raise HumanApprovalRequired(self.id, self.prompt(state), self.payload(state))
        return {"decision": decision, "key": self.decision_key}


@dataclass
class MapNode(Node):
    """Fan out over a list. One child result per item, in input order.

    `parallel > 1` trades deterministic ordering for latency. The default is
    sequential because replaying a run byte-for-byte is worth more than
    concurrency in most audited workflows — but the knob is here when it is not.
    """

    over: Callable[[GraphState], list[Any]] = None            # type: ignore[assignment]
    child: Callable[[Any, GraphState, NodeContext], Any] = None  # type: ignore[assignment]
    parallel: int = 1
    kind: str = "map"

    def execute(self, state: GraphState, ctx: NodeContext) -> Any:
        items = list(self.over(state))
        if self.parallel <= 1:
            return [self.child(item, state, ctx) for item in items]

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=self.parallel) as pool:
            # `map` preserves input order even when completion order does not.
            return list(pool.map(lambda it: self.child(it, state, ctx), items))


@dataclass
class ReduceNode(Node):
    """Fan in. Combines the children of a MapNode into one output."""

    source: str = ""
    combine: Callable[[list[Any], GraphState], Any] = None  # type: ignore[assignment]
    kind: str = "reduce"

    def execute(self, state: GraphState, ctx: NodeContext) -> Any:
        children = state.outputs.get(self.source) or []
        return self.combine(list(children), state)
