"""`trellis` — a small, domain-free CLI for the library itself.

Trellis ships no tools and no domain, so this CLI does not run agents. It does
the four things that are useful about *any* Trellis project:

    trellis steps                    the ten build steps
    trellis doctor                   check config, credentials, pricing
    trellis new my_agent             scaffold a project that already has guardrails
    trellis graph mypkg.flows:build  inspect a graph without running it
    trellis runs / trellis trace ID  read a state directory

Your application's own commands belong in your application. See
`examples/run.py` for what that looks like.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

from . import __version__, get_settings

STEPS = [
    ("1", "Write the domain first, without a model",
     "Every figure your agent quotes must come from tested deterministic code. "
     "Build this and the rest of the system has something true to stand on."),
    ("2", "Pick a level, and write down what forced it",
     "Level 1 (one call) unless the task demonstrably breaks it. 'It felt "
     "limiting' is not a breakage. Bring the failing run."),
    ("3", "Define the tools, with tiers",
     "READ_ONLY vs RESTRICTED is the difference between quoting a refund and "
     "issuing one. The tier is part of the tool's identity."),
    ("4", "Stand up the harness",
     "build_stack(registry=your_tools). One code path for model calls and one "
     "for tool calls, so there is one place to enforce anything."),
    ("5", "Answer the four questions as a contract",
     "AgentContract(goal, stop, verifier, budget). You cannot construct an "
     "Agent without all four, and no_verification() makes you justify 'nobody'."),
    ("6", "Write the stop condition before the prompt",
     "If you cannot express 'done' as a predicate, you do not yet understand "
     "the task well enough to automate it."),
    ("7", "Run the loop: gather, act, verify, repeat",
     "The model saying it is finished is a candidate, not a conclusion. "
     "Verify, check the stop condition, then push back with the specific gap."),
    ("8", "Promote to a graph when the work outlives one loop",
     "Declared dependencies, per-node checkpoints, conditional routing, human "
     "gates — validated before it costs anything."),
    ("9", "Put an idempotency key on every external effect",
     "Derived from the facts of the action, never a clock. Checkpointing "
     "narrows the duplicate window; only the ledger closes it."),
    ("10", "Prove each guardrail with a test that fails without it",
     "A rule that is not a failing test is a rule that will be broken within "
     "two quarters. trellis.testing has the helpers."),
]

QUESTIONS = ("What is the stop condition?", "Who verifies before it ships?",
             "What is the spend cap?", "What happens when a subagent fails?")

TEMPLATE_APP = '''"""{name} — an agent built on Trellis.

Fill in the domain, the tools and the stop condition. The guardrails are already
wired: a contract you cannot leave incomplete, tiered tool permissions, four
governors, and a run ledger.
"""

from __future__ import annotations

from typing import Any

from trellis import PermissionTier, ToolSpec, build_stack
from trellis.harness import Harness, ToolRegistry
from trellis.loop import (
    Agent, AgentContract, Budget, LoopState, ModelSaysDone, OnFailure,
    Predicate, no_verification,
)

SYSTEM = """\\
You are ... .

1. Never state a figure you did not get from a tool.
2. If a tool errors, read it and adapt. Say what failed and what you did instead.
3. When you have enough, answer."""


# --- 1. the domain: deterministic, tested, no model -----------------------

def compute_something(x: float) -> dict[str, Any]:
    """Replace me. Whatever your agent quotes to a human comes from here."""
    return {{"result": round(x * 2, 2)}}


# --- 2. the tools, with tiers --------------------------------------------

def build_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(
        ToolSpec(
            name="compute_something",
            description="Compute the thing. Always use this rather than doing "
                        "the arithmetic yourself.",
            input_schema={{"type": "object",
                          "properties": {{"x": {{"type": "number"}}}},
                          "required": ["x"]}},
            tier=PermissionTier.READ_ONLY,
        ),
        compute_something,
    )
    # Anything that leaves the building is RESTRICTED and denied by default:
    # r.register(ToolSpec(..., tier=PermissionTier.RESTRICTED), send_it)
    return r


# --- 3. the stop condition, written before the prompt --------------------

def _grounded(state: LoopState) -> bool:
    """Every figure in the answer traceable to a tool that SUCCEEDED."""
    if not any(ch.isdigit() for ch in state.answer or ""):
        return True
    return any(o.tool_name == "compute_something" and not o.is_error
               for o in state.outcomes)


# --- 4. the contract: the four questions ---------------------------------

def build(harness: Harness, goal: str = "Answer the user's question.") -> Agent:
    return Agent(
        harness,
        AgentContract(
            goal=goal,
            stop=ModelSaysDone() & Predicate(_grounded, "figures traceable to tools"),
            verifier=no_verification(
                "Every figure comes from deterministic code covered by tests."
            ),
            budget=Budget(max_turns=6, max_usd=0.25, max_seconds=60),
            on_failure=OnFailure.ESCALATE,
        ),
        model=harness.settings.worker_model,
        system=SYSTEM,
        name="{name}",
    )


def main() -> None:
    stack = build_stack(registry=build_registry())
    result = build(stack.harness).run("Ask me something.")
    print(result.answer)
    print(stack.cost())


if __name__ == "__main__":
    main()
'''

TEMPLATE_TEST = '''"""Guardrail tests for {name}.

Each of these fails if you remove the guardrail it covers. That is the
definition of a guardrail; everything else is a comment.
"""

from __future__ import annotations

from trellis.testing import (
    assert_capped, build_test_stack, calls, looping, tools_used, turn,
)

from {name} import build, build_registry


def _stack(policy):
    return build_test_stack(policy, tools=build_registry())


def test_the_agent_reaches_an_answer_using_tools():
    def policy(call):
        if not call.called("compute_something"):
            return calls("compute_something", x=21)
        return turn("The result is 42.")

    stack = _stack(policy)
    result = build(stack.harness).run("What is 21 doubled?")
    assert result.ok
    assert "compute_something" in tools_used(stack)


def test_a_looping_model_is_stopped():
    stack = _stack(looping("compute_something", x=1))
    assert_capped(lambda: build(stack.harness).run("go"))


def test_an_ungrounded_figure_never_ships():
    """Fluent, confident, invented. Nothing in the text signals it.

    Under a plain `ModelSaysDone()` stop condition this answer would be
    returned to the caller. The grounding predicate rejects it, pushes back,
    and — because this model never corrects itself — the run ends in a loud,
    diagnosable failure instead of a wrong answer.
    """
    import pytest
    from trellis.core.errors import StopConditionNotMet

    stack = _stack(lambda call: turn("The result is 99."))
    with pytest.raises(StopConditionNotMet):
        build(stack.harness).run("What is 21 doubled?")
'''


def _rule(title: str, ch: str = "-") -> None:
    print(f"\n{title}\n{ch * max(len(title), 66)}")


def _wrap(text: str, width: int = 68) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def cmd_steps(_a) -> int:
    _rule("Building a production agent: ten steps", "=")
    print("Steps 1-3 have no model in them at all. That is the point — most "
          "agent\nfailures are domain and permission failures wearing an AI costume.")
    for n, title, why in STEPS:
        print(f"\n  {n:>2}. {title}")
        for line in _wrap(why):
            print(f"      {line}")
    _rule("Then, before any unattended run")
    for q in QUESTIONS:
        print(f"  · {q}")
    print("\n  Cannot answer all four? You have an expensive experiment, not an agent.")
    return 0


def cmd_doctor(_a) -> int:
    s = get_settings()
    from .config import CONTEXT_WINDOW, PRICES

    has_key = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
    _rule("trellis doctor", "=")
    print(f"  version            {__version__}")
    print(f"  python             {sys.version.split()[0]}")
    print(f"  backend            {s.backend}"
          f"{'  (credential found)' if has_key else '  (no credential — scripted)'}")
    try:
        import anthropic  # noqa: F401  (presence probe, not a usage)
        print("  anthropic sdk      installed")
    except ImportError:
        print("  anthropic sdk      not installed  "
              "(pip install 'pyligent-agents[anthropic]')")
    print(f"\n  models             orchestrator={s.orchestrator_model}")
    print(f"                     worker={s.worker_model}")
    print(f"                     cheap={s.cheap_model}")
    for m in (s.orchestrator_model, s.worker_model, s.cheap_model):
        known = m in PRICES
        print(f"    {m:<24} {'priced' if known else 'UNPRICED — will bill at the dearest tier'}"
              f"{'' if not known else f'  ${PRICES[m][0]}/${PRICES[m][1]} per MTok'}"
              f"{'' if m not in CONTEXT_WINDOW else f', window {CONTEXT_WINDOW[m]:,}'}")
    print(f"\n  governors          turns={s.max_turns}  usd={s.run_budget_usd}  "
          f"seconds={s.run_budget_seconds}")
    print(f"  context            compact_at={s.compact_at}  "
          f"offload_over={s.offload_over_chars} chars")
    print(f"  state dir          {s.state_dir.resolve()}"
          f"{'  (exists)' if s.state_dir.exists() else '  (will be created)'}")
    unpriced = [m for m in (s.orchestrator_model, s.worker_model, s.cheap_model)
                if m not in PRICES]
    if unpriced:
        _rule("Action")
        print(f"  {len(unpriced)} model(s) have no price. They will not look free — Trellis")
        print("  charges an unknown model at the dearest tier it knows — but a real")
        print("  number beats a safe guess:\n")
        print("    from trellis import register_model")
        for m in unpriced:
            print(f'    register_model("{m}", price_in=..., price_out=..., context_window=...)')
    return 0


def cmd_new(a) -> int:
    target = Path(a.path)
    name = a.name or target.name.replace("-", "_")
    if target.exists() and any(target.iterdir()):
        print(f"{target} exists and is not empty.", file=sys.stderr)
        return 2
    (target / "tests").mkdir(parents=True, exist_ok=True)
    (target / f"{name}.py").write_text(TEMPLATE_APP.format(name=name), encoding="utf-8")
    (target / "tests" / f"test_{name}.py").write_text(
        TEMPLATE_TEST.format(name=name), encoding="utf-8")
    (target / "README.md").write_text(
        f"# {name}\n\nBuilt on [Trellis](https://github.com/pyligent/pyligent-agents).\n\n"
        f"```bash\npip install pyligent-agents pytest\npython {name}.py\npytest\n```\n\n"
        f"## The four questions\n\n"
        f"| | |\n|---|---|\n"
        f"| Stop condition | `ModelSaysDone() & Predicate(_grounded, ...)` |\n"
        f"| Who verifies | _fill this in — `no_verification()` needs a real reason_ |\n"
        f"| Spend cap | `Budget(max_usd=0.25)` |\n"
        f"| On failure | `OnFailure.ESCALATE` |\n", encoding="utf-8")
    print(f"Created {target}/")
    print(f"  {name}.py               agent, tools and stop condition")
    print(f"  tests/test_{name}.py    three guardrail tests: convergence, turn cap, grounding")
    print(f"\n  cd {target} && pytest")
    print("\n  Then: fill in the domain, and answer the four questions in README.md.")
    return 0


def cmd_graph(a) -> int:
    """Inspect any graph by import path, without running it."""
    module_name, _, factory = a.target.partition(":")
    if not factory:
        print("Use module:factory, e.g. myapp.flows:build_graph", file=sys.stderr)
        return 2
    sys.path.insert(0, str(Path.cwd()))
    fn = getattr(importlib.import_module(module_name), factory)
    try:
        graph = fn()
    except TypeError:
        from .runtime import build_stack

        graph = fn(build_stack().harness)
    graph.validate()
    print({"show": graph.render, "mermaid": graph.to_mermaid,
           "json": lambda: json.dumps(graph.to_dict(), indent=2)}[a.action]())
    return 0


def cmd_runs(a) -> int:
    from .graph.store import GraphStore

    store = GraphStore(Path(a.state_dir or get_settings().state_dir) / "graph.sqlite")
    rows = store.list_runs(limit=a.limit)
    if not rows:
        print("No runs recorded in this state directory.")
        return 0
    print(f"{'RUN':<20} {'GRAPH':<22} {'STATUS':<12}")
    print("-" * 56)
    for r in rows:
        print(f"{r['run_id']:<20} {r['graph']:<22} {r['status']:<12}")
    return 0


def cmd_trace(a) -> int:
    from .graph.store import GraphStore

    store = GraphStore(Path(a.state_dir or get_settings().state_dir) / "graph.sqlite")
    spans = store.spans(a.run_id)
    if not spans:
        print(f"No spans for '{a.run_id}'.", file=sys.stderr)
        return 2
    for s in spans:
        detail = ", ".join(f"{k}={v}" for k, v in s["detail"].items() if k != "payload")
        print(f"  {s['node_id']:<22} {s['kind']:<18} {detail}")
    effects = store.effects(a.run_id)
    if effects:
        _rule("EFFECTS (external side effects, once each)")
        for e in effects:
            print(f"  {e['node_id']:<22} {e['key']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="trellis",
        description="Harness, loop and graph engineering for production agents.")
    p.add_argument("--version", action="version", version=f"trellis {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("steps", help="the ten build steps").set_defaults(fn=cmd_steps)
    sub.add_parser("doctor", help="check config, credentials and pricing").set_defaults(fn=cmd_doctor)

    n = sub.add_parser("new", help="scaffold a project with guardrails already wired")
    n.add_argument("path")
    n.add_argument("--name", default="")
    n.set_defaults(fn=cmd_new)

    g = sub.add_parser("graph", help="inspect a graph without running it")
    g.add_argument("action", choices=["show", "mermaid", "json"])
    g.add_argument("target", help="module:factory, e.g. myapp.flows:build_graph")
    g.set_defaults(fn=cmd_graph)

    r = sub.add_parser("runs", help="list graph runs in a state directory")
    r.add_argument("--state-dir", default="")
    r.add_argument("--limit", type=int, default=20)
    r.set_defaults(fn=cmd_runs)

    t = sub.add_parser("trace", help="per-node spans for a run")
    t.add_argument("run_id")
    t.add_argument("--state-dir", default="")
    t.set_defaults(fn=cmd_trace)

    args = p.parse_args(argv)
    return args.fn(args)
