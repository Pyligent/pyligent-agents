"""`pyligent-agents` — a small, domain-free CLI for the library itself.

Pyligent Agents ships no tools and no domain, so this CLI does not run agents. It does
the four things that are useful about *any* Pyligent Agents project:

    pyligent-agents steps                    the ten build steps
    pyligent-agents doctor                   check config, credentials, pricing
    pyligent-agents new my_agent             scaffold a project that already has guardrails
    pyligent-agents graph mypkg.flows:build  inspect a graph without running it
    pyligent-agents runs / pyligent-agents trace ID  read a state directory

Your application's own commands belong in your application. See
`examples/run.py` for what that looks like.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from evidencecheck.console import use_utf8_stdout

from . import PermissionTier, __version__, get_settings
from .credentials import detect, gitignore_protects, guidance

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
     "two quarters. pyligent_agents.testing has the helpers."),
]

QUESTIONS = ("What is the stop condition?", "Who verifies before it ships?",
             "What is the spend cap?", "What happens when a subagent fails?")

TEMPLATE_APP = '''"""{name} — an agent built on Pyligent Agents.

Fill in the domain, the tools and the stop condition. The guardrails are already
wired: a contract you cannot leave incomplete, tiered tool permissions, four
governors, and a run ledger.
"""

from __future__ import annotations

from typing import Any

from pyligent_agents import PermissionTier, ToolSpec, build_stack
from pyligent_agents.harness import Harness, ToolRegistry
from pyligent_agents.loop import (
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

from pyligent_agents.testing import (
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
    from pyligent_agents.core.errors import StopConditionNotMet

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
    print("Steps 1-3 involve no model. Most agent failures originate in domain\n"
          "rules and permission boundaries rather than in the model itself.")
    for n, title, why in STEPS:
        print(f"\n  {n:>2}. {title}")
        for line in _wrap(why):
            print(f"      {line}")
    _rule("Then, before any unattended run")
    for q in QUESTIONS:
        print(f"  · {q}")
    print("\n  Cannot answer all four? You have an expensive experiment, not an agent.")
    return 0


def cmd_validation_pack(a) -> int:
    """Assemble the evidence a model-risk review asks for.

    Second-line validation functions ask the same questions of every AI system:
    what does it refuse to do, how do you know, what did you measure it against,
    and can you reproduce a decision from six months ago. This command answers
    them from the repository itself rather than from a slide, so the answers
    cannot drift away from the code.

    It reports what is there. It does not certify anything, and it will say so.
    """
    import json as _json
    from pathlib import Path as _Path

    root = _Path(a.root).resolve()
    out: dict[str, object] = {
        "generated_by": f"pyligent-agents {__version__}",
        "repository": str(root),
        "disclaimer": (
            "Evidence inventory only. This is not a validation, an attestation, "
            "or a claim of regulatory compliance. It reports what controls exist "
            "in this codebase and what evidence supports them."
        ),
    }

    # 1. Determinism — the basis of every reproducibility claim.
    s = get_settings()
    out["reproducibility"] = {
        "deterministic_backend": "ScriptedLLM is a second implementation of the "
                                 "LLMClient contract, not a mock; the full suite "
                                 "runs against it with no credential and no spend",
        "replay": "graph runs checkpoint before each node and replay from disk at "
                  "zero token cost",
        "effect_ledger": "external effects carry a key derived from the facts of "
                         "the action and a UNIQUE(run_id, key) constraint",
        "state_dir": str(s.state_dir.resolve()),
    }

    # 2. Controls that are enforced rather than documented.
    out["enforced_controls"] = {
        "permission_tiers": [t.name for t in PermissionTier],
        "governors": ["turns", "tokens", "usd", "wall_clock"],
        "governors_checked": "before each model call, not after",
        "contract_required_fields": ["goal", "stop", "verifier", "budget"],
        "verification_waiver": "no_verification() requires a written justification "
                               "and rejects a placeholder",
        "citations": "every evidence quote is checked as a verbatim substring of "
                     "the source; one fabricated quote rejects the artifact "
                     "regardless of the verifier's verdict",
    }

    # 3. Tests — the only evidence a control is real.
    tests = sorted((root / "tests").glob("test_*.py"))
    out["test_evidence"] = {
        "files": [t.name for t in tests],
        "principle": "each guardrail has a test that fails when the guardrail is "
                     "removed; a rule that is not a failing test is not a rule",
    }

    # 4. Measurement — baselines, tolerances, and the asymmetry.
    baselines = sorted((root / "evals" / "baselines").glob("*.json"))
    systems = {}
    for b in baselines:
        try:
            data = _json.loads(b.read_text(encoding="utf-8"))
            systems[b.stem] = data.get("metrics", {})
        except (OSError, ValueError):
            systems[b.stem] = {"error": "unreadable"}
    out["measurement"] = {
        "committed_baselines": systems,
        "false_accept_tolerance": 0.0,
        "false_accept_rationale": "a false accept is a flawed document approved; "
                                  "there is no acceptable number, so any increase "
                                  "fails the build",
        "false_refer_tolerance": 0.05,
        "headline_metric": "none. The two decision errors are reported separately "
                           "and never averaged.",
    }

    # 5. What this system does NOT do. The section reviewers actually read.
    out["limitations"] = [
        "Tools are not sandboxed; they run in-process with the host's privileges.",
        "The citation check catches fabricated evidence, not irrelevant evidence: "
        "a genuine sentence that does not support the claim will pass.",
        "Domain gates are worked examples over synthetic documents. They are not "
        "calibrated to any institution's policy.",
        "No retrieval layer; everything must fit in context.",
        "estimate_tokens is a heuristic used only to trigger context management; "
        "billing uses the figures the API returns.",
    ]

    text = _json.dumps(out, indent=2)
    if a.out:
        _Path(a.out).write_text(text, encoding="utf-8")
        print(f"validation pack written to {a.out}")
    else:
        print(text)
    return 0


def cmd_setup(_a) -> int:
    """Tell someone what the library can see, and what to do about it.

    Deliberately read-only. It never asks for a key, never accepts one, and never
    writes one anywhere — the person at the keyboard sets it in their own shell,
    where it belongs. A helper that takes a pasted secret and puts it in a file is
    how secrets reach repositories.
    """
    use_utf8_stdout()
    cred = detect()
    s = get_settings()

    _rule("pyligent-agents setup", "=")
    print(f"  credential         {cred.summary}")
    print(f"  backend requested  {s.backend}")

    if cred.present:
        print("  workspace id       "
              + ("set" if cred.workspace else "not set (only needed for identity-linked keys)"))

    protected = gitignore_protects()
    if protected is None:
        print("  .env in git        not a git repository")
    elif protected:
        print("  .env in git        ignored")
    else:
        print("  .env in git        NOT IGNORED — add `.env` to .gitignore before")
        print("                     putting anything in it")

    _rule("What will happen")
    if cred.present:
        print("  Model calls will be made and billed to that key.")
        print("  Every run is capped: "
              f"turns={s.max_turns}  usd={s.run_budget_usd}  "
              f"seconds={s.run_budget_seconds}")
        print()
        print("  Verify it end to end — one small call, a few cents at most:")
        print()
        print("      PYLIGENT_LIVE_MODEL=1 pytest -m live -s")
    elif s.backend == "anthropic":
        print("  No model calls can be made: the backend is set to `anthropic`")
        print("  but no credential is configured, so the first call will fail.")
        print(guidance())
    else:
        print("  The deterministic backend runs in-process. All tests, demos,")
        print("  evaluations and benchmarks run without a credential and without")
        print("  incurring cost.")
        print()
        print("  To use a real model instead:")
        print(guidance())
    return 0


def _load_system(path: Path) -> dict[str, dict[str, object]]:
    """Stored terms, keyed by document. CSV from an export, or JSON.

    CSV is the format collateral systems actually export, so it is the one that
    matters: a key column (`document`, `document_id`, `counterparty` or the first
    column) and one column per stored term.
    """
    import csv as _csv

    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".json" or raw.lstrip().startswith("{"):
        data = json.loads(raw)
        return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}

    rows = list(_csv.DictReader(raw.splitlines()))
    if not rows:
        return {}
    headers = list(rows[0].keys())
    key = next((h for h in ("document", "document_id", "counterparty", "id")
                if h in headers), headers[0])
    out: dict[str, dict[str, object]] = {}
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        ident = (row.get(key) or "").strip()
        if not ident:
            continue
        if ident in seen:
            duplicates.add(ident)
        seen.add(ident)
        out[ident] = {k: v for k, v in row.items()
                      if k != key and v not in (None, "")}

    # A repeated key means the export is ambiguous about that counterparty, and the
    # last row silently winning produces a finding that depends on row order. Which
    # row is authoritative is not ours to guess: name them and leave them out, so
    # the rest of the portfolio still reconciles.
    for ident in sorted(duplicates):
        out.pop(ident, None)
        print(f"  {ident}: appears more than once in the export; skipped. Deduplicate "
              f"it upstream — this tool will not choose which row is authoritative.",
              file=sys.stderr)
    return out


def _pair_up(documents: Path, extractions: Path | None) -> list[tuple[Path, Path]]:
    """Match each document to its extraction by filename stem."""
    docs = ([documents] if documents.is_file()
            else sorted(f for f in documents.rglob("*")
                        if f.suffix.lower() in {".htm", ".html", ".txt", ".pdf"}))
    if extractions is None:
        return [(d, d.with_suffix(".json")) for d in docs]
    if extractions.is_file():
        return [(d, extractions) for d in docs]
    index = {f.stem: f for f in extractions.rglob("*.json")}
    return [(d, index[d.stem]) for d in docs if d.stem in index]


def cmd_reconcile(a) -> int:
    """Compare signed agreements against stored terms. Writes nothing anywhere."""
    use_utf8_stdout()
    from evidencecheck.cli import normalise_extraction
    from evidencecheck.sources import load as load_source

    from .reconcile import reconcile, render

    # Exit codes are a contract for whoever schedules this: 0 clean, 1 findings a
    # human must work, 2 the run could not happen. Collapsing the last two into the
    # first would page someone about discrepancies when the export simply moved.
    system_path = Path(a.system)
    if not system_path.exists():
        print(f"stored terms not found: {a.system}", file=sys.stderr)
        return 2
    try:
        system = _load_system(system_path)
    except Exception as exc:  # noqa: BLE001 — a malformed export is a usage error
        print(f"could not read {a.system}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if not system:
        print(f"no stored terms found in {a.system}", file=sys.stderr)
        return 2

    documents_path = Path(a.documents)
    if not documents_path.exists():
        print(f"documents not found: {a.documents}", file=sys.stderr)
        return 2

    pairs = _pair_up(documents_path, Path(a.extractions) if a.extractions else None)
    if not pairs:
        print("no document/extraction pairs found. Extractions are matched to "
              "documents by filename stem.", file=sys.stderr)
        return 2

    reports, material_total = [], 0
    for doc_path, extraction_path in pairs:
        if not extraction_path.exists():
            continue
        key = doc_path.stem
        stored = system.get(key) or system.get(doc_path.name)
        if stored is None:
            continue                      # no stored terms for this one: not a finding

        try:
            source = load_source(doc_path)
            fields = normalise_extraction(json.loads(
                extraction_path.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001 — one bad file must not stop the run
            print(f"  {key}: could not read ({type(exc).__name__}: {exc})",
                  file=sys.stderr)
            continue

        rec = reconcile(source.text, fields, stored,
                        document=key, counterparty=str(stored.get("counterparty", "")))
        reports.append(rec)
        material_total += len(rec.material)
        if not a.quiet:
            print(render(rec))

    if not reports:
        print("nothing to reconcile: no document matched a row in the export.",
              file=sys.stderr)
        return 2

    _rule("PORTFOLIO")
    print(f"  documents reconciled   {len(reports)}")
    print(f"  agreeing               {sum(1 for r in reports if r.agrees)}")
    print(f"  with discrepancies     {sum(1 for r in reports if r.discrepancies)}")
    print(f"  material discrepancies {material_total}")
    unver = sum(len(r.unverified) for r in reports)
    if unver:
        print(f"  unverified fields      {unver}  (citation did not check out)")
    print("\n  Nothing was written. No tool with an external effect was used.")

    if a.out:
        _write_exceptions(Path(a.out), reports)
        print(f"\n  exceptions written to {a.out}")

    # Non-zero when a human should look, so this can gate a nightly job.
    return 1 if material_total else 0


def _write_exceptions(path: Path, reports: list) -> None:
    """One row per field needing attention. The file an analyst works from."""
    import csv as _csv

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = _csv.writer(handle)
        writer.writerow(["document", "counterparty", "field", "state", "material",
                         "agreement_says", "system_says", "impact", "clause"])
        for rec in reports:
            for r in (*rec.material,
                      *(x for x in rec.discrepancies if not x.material),
                      *rec.unverified):
                writer.writerow([rec.document, rec.counterparty, r.field, r.state,
                                 "yes" if r.material else "no", r.ours, r.theirs,
                                 r.impact, (r.clause or r.reason)[:300]])


def cmd_doctor(_a) -> int:
    s = get_settings()
    from .config import CONTEXT_WINDOW, PRICES

    cred = detect()
    has_key = cred.present
    _rule("pyligent-agents doctor", "=")
    print(f"  version            {__version__}")
    print(f"  python             {sys.version.split()[0]}")
    # `auto` falls back to the scripted backend without a credential; an explicit
    # `anthropic` does not — it builds the real client and fails at the first call.
    # Reporting "scripted" for both told people they were safe when they were not.
    if has_key:
        note = f"  (credential in {cred.variable})"
    elif s.backend == "anthropic":
        note = "  (NO credential — calls will fail; run `pyligent-agents setup`)"
    else:
        note = "  (no credential — falls back to scripted)"
    print(f"  backend            {s.backend}{note}")
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
        print(f"    {m:<24} {'priced' if known else 'UNPRICED — billed at the highest known rate'}"
              f"{'' if not known else f'  ${PRICES[m][0]}/${PRICES[m][1]} per MTok'}"
              f"{'' if m not in CONTEXT_WINDOW else f', window {CONTEXT_WINDOW[m]:,}'}")
    print(f"\n  governors          turns={s.max_turns}  usd={s.run_budget_usd}  "
          f"seconds={s.run_budget_seconds}")
    print(f"  context            compact_at={s.compact_at}  "
          f"offload_over={s.offload_over_chars} chars")
    print(f"  state dir          {s.state_dir.resolve()}"
          f"{'  (exists)' if s.state_dir.exists() else '  (will be created)'}")

    # Asked in the first thirty minutes of every regulated-industry review, and
    # answered badly by most tools: where does the document text actually go?
    _rule("Data residency")
    if s.backend == "scripted" or not has_key:
        print("  model calls        NONE — the deterministic backend runs in-process")
        print("  document text      never leaves this process")
    else:
        print(f"  model calls        sent to the {s.backend} API over the network")
        print("  document text      LEAVES this process in the request body")
        print("                     Review against your data policy before pointing")
        print("                     this at real agreements.")
    print(f"  state written to   {s.state_dir.resolve()}")
    print("  telemetry          none. This library makes no call you did not ask for.")
    print("  tool sandboxing    NONE — tools run in this process with its privileges")
    unpriced = [m for m in (s.orchestrator_model, s.worker_model, s.cheap_model)
                if m not in PRICES]
    if unpriced:
        _rule("Action")
        print(f"  {len(unpriced)} model(s) have no registered price. Usage is")
        print("  estimated at the highest known rate, so cost is never understated.")
        print("  Register the actual rates for accurate figures:\n")
        print("    from pyligent_agents import register_model")
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
        f"# {name}\n\nBuilt on [Pyligent Agents](https://github.com/pyligent/pyligent-agents).\n\n"
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
    use_utf8_stdout()
    p = argparse.ArgumentParser(
        prog="pyligent-agents",
        description="Harness, loop and graph engineering for production agents.")
    p.add_argument("--version", action="version", version=f"pyligent-agents {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("steps", help="the ten build steps").set_defaults(fn=cmd_steps)
    rc = sub.add_parser("reconcile",
                        help="compare signed agreements against stored terms; writes nothing")
    rc.add_argument("--documents", required=True, help="a document, or a directory of them")
    rc.add_argument("--extractions", help="extraction JSON, or a directory matched by filename stem")
    rc.add_argument("--system", required=True, help="stored terms: CSV export or JSON")
    rc.add_argument("--out", help="write the exception report here, as CSV")
    rc.add_argument("--quiet", action="store_true", help="portfolio summary only")
    rc.set_defaults(fn=cmd_reconcile)

    sub.add_parser("setup", help="what credential the library can see, and how to set one"
                   ).set_defaults(fn=cmd_setup)
    sub.add_parser("doctor", help="check config, credentials and pricing").set_defaults(fn=cmd_doctor)

    vp = sub.add_parser("validation-pack",
                        help="assemble the evidence a model-risk review asks for")
    vp.add_argument("--root", default=".", help="repository root to inventory")
    vp.add_argument("--out", help="write JSON here instead of stdout")
    vp.set_defaults(fn=cmd_validation_pack)

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
