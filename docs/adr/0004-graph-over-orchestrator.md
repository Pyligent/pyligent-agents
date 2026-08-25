# ADR 0004 — A declared graph instead of an orchestrator agent

**Status:** accepted · **Date:** 2026-08-25

## Context

The common pattern for multi-agent work is an orchestrator agent that plans,
delegates to specialists, and never executes. An earlier version of this work
followed it, and enforced the never-executes rule structurally: the orchestrator
held an empty tool list, asserted by a test.

That worked. But enforcing "the orchestrator must not execute" is working around
the fact that it *could*, and it left four things unavailable:

- the plan could not be validated before it ran
- there was nothing to render into a design doc or change record
- a crash restarted the plan
- the run could not be replayed identically

## Decision

Declare the plan as a graph. Nodes carry `depends_on`, `requires`, `provides`,
`when`, `retry`, `idempotency` and `compensate`. `validate()` runs before any
execution and rejects unknown dependencies, cycles, and reads of keys nothing
upstream provides.

Where a model genuinely should choose the next step, an `AgentNode` contains a
full agent loop — so the flexibility is scoped to one node rather than being the
architecture.

## Consequences

**Good.** Four classes of runtime surprise become build-time errors.
`pyligent-agents graph show` and `to_mermaid()` make the plan reviewable by someone who
will never read the code. Resume, replay, conditional routing and per-node
tracing come from the runner rather than being rewritten per workflow. And the
orchestrator-never-executes rule becomes vacuous: there is no orchestrator to
constrain.

**Bad.** Less adaptive. A graph cannot invent a step the author did not
anticipate. For genuinely open-ended work — exploratory research, incident triage
where the shape is unknown — an `AgentNode` with a broad tool surface is the
better fit, and the graph around it is then a thin wrapper. We accept that: most
production workflows are known-shaped, and the ones that are not are exactly
where a human belongs in the loop anyway.

**Also.** The node kinds are a closed set of six. Keeping it closed is what makes
graphs analysable. Adding a seventh should be hard, and should require showing
that no combination of the existing six expresses the shape.
