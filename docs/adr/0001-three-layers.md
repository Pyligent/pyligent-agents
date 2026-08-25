# ADR 0001 — Three layers: harness, loop, graph

**Status:** accepted · **Date:** 2026-08-25

## Context

The four-level agent ladder — stateless call, tool loop, durable state,
orchestration — is a good framework for deciding *how much* agent a task needs.
It is not a good factoring for the code that builds them.

An earlier internal version implemented it literally: one module per level, each
with its own loop, its own checkpointing, its own tool dispatch. It worked, and
it duplicated. The Level 4 workers re-implemented the Level 2 loop. The Level 3
session re-implemented tool dispatch. Adding context management would have meant
adding it four times — which in practice means adding it once and forgetting the
other three.

## Decision

Three layers, each owning exactly one concern:

| Layer | Owns | The question it answers |
|---|---|---|
| harness | context | what does the model see, and what may it touch? |
| loop | control | when do we stop, and what happens when something breaks? |
| graph | coordination | what runs in what order, and what survives a crash? |

The four levels become *configurations* of these layers, not separate
implementations:

- **Level 1** — harness only, no loop (a loop with `max_turns=1` is a loop
  pretending not to be one)
- **Level 2** — harness + loop
- **Level 3** — harness + graph, mostly `Step` nodes
- **Level 4** — harness + graph with `MapNode`/`GateNode`, and a loop inside
  `AgentNode`

## Consequences

**Good.** Context management, hooks, permissions and metering are written once
and every level gets them. An agent built at Level 2 becomes a Level 4 node with
no changes — the composition claim the ladder makes is now enforced by
construction rather than by discipline. Each layer is separately testable, and
the test files map one-to-one onto the layers.

**Bad.** More indirection for a reader who only wants Level 1. Mitigated by
`examples/level1_triage/app.py`, which is ninety lines and talks to the harness
directly. There is also a real risk of the layers leaking into each other; the
boundary is one sentence each, and a PR that blurs it should be pushed back.

**Also.** The layering makes visible how much of "agent engineering" is not about
the model at all. Two of the three layers contain no prompting.
