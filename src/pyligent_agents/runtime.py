"""One-call setup. The "easy to use" front door.

Everything in this repo can be assembled by hand — that is the point of the
layering. But nobody should have to, to get started:

    from pyligent_agents import build_stack
    stack = build_stack()                       # offline, deterministic
    stack = build_stack(policy=my_policy)       # scripted behaviour you control
    stack = build_stack(backend="anthropic")    # the real API

`Stack` bundles the four things every run needs — harness, tools, durable store
and settings — and nothing else.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .core.ids import run_id as new_run_id
from .graph.graph import Graph
from .graph.runner import GraphRunner
from .graph.store import GraphStore
from .harness.client import ScriptedLLM, build_backend
from .harness.governor import Governor
from .harness.harness import Harness
from .harness.hooks import HookBus, default_hooks
from .harness.registry import ToolRegistry
from .observability.trace import RunLedger


@dataclass
class Stack:
    settings: Settings
    harness: Harness
    store: GraphStore

    @property
    def registry(self) -> ToolRegistry:
        return self.harness.registry

    @property
    def ledger(self) -> RunLedger:
        return self.harness.ledger

    @property
    def governor(self) -> Governor:
        return self.harness.governor

    def runner(self, graph: Graph) -> GraphRunner:
        return GraphRunner(graph, self.store, self.harness)

    def report(self) -> dict[str, Any]:
        return self.harness.report()

    def cost(self) -> dict[str, Any]:
        return self.harness.governor.report()


def build_stack(
    *,
    policy: Callable | None = None,
    backend: str | None = None,
    settings: Settings | None = None,
    registry: ToolRegistry | None = None,
    hooks: HookBus | None = None,
    run_id: str | None = None,
    budget_usd: float | None = None,
    state_dir: str | Path | None = None,
) -> Stack:
    """Assemble a ready-to-use stack.

    `policy` wins over `backend`: passing a scripted policy is how tests and
    demos get deterministic behaviour through the exact code path production
    uses. There is no separate test harness, because a separate test harness is
    a harness you are not testing.

    `registry` defaults to empty. Pyligent Agents ships no tools — your tools are your
    domain, and a library that guesses at them is a library you fight.
    """
    s = settings or get_settings()
    if backend or state_dir or budget_usd is not None:
        from dataclasses import replace

        s = replace(
            s,
            backend=backend or s.backend,
            state_dir=Path(state_dir) if state_dir else s.state_dir,
            run_budget_usd=s.run_budget_usd if budget_usd is None else budget_usd,
        )

    client = ScriptedLLM(policy=policy) if policy is not None else build_backend(s.backend)
    rid = run_id or new_run_id()

    harness = Harness(
        settings=s,
        client=client,
        registry=registry or ToolRegistry(),
        hooks=hooks or default_hooks(),
        run=rid,
    )
    store = GraphStore(Path(s.state_dir) / "graph.sqlite")
    return Stack(settings=s, harness=harness, store=store)
