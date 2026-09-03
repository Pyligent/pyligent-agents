# Documentation

Start with [GETTING-STARTED.md](GETTING-STARTED.md). Everything there runs without an
API key.

## By task

| I want to… | Read |
|---|---|
| Get something running in five minutes | [GETTING-STARTED.md](GETTING-STARTED.md) |
| Know what this can and cannot do | [CAPABILITIES.md](CAPABILITIES.md) |
| Check whether an extraction's citations hold up | [SPEC-evidence-checks.md](SPEC-evidence-checks.md) |
| Compare agreements against a system of record | [RECONCILE.md](RECONCILE.md) |
| Configure an API key, or avoid needing one | [CREDENTIALS.md](CREDENTIALS.md) |
| Test an agent I am building | [TESTING.md](TESTING.md) |
| Build an agent step by step | [STEPS.md](STEPS.md) |

## By layer

| Layer | Owns | Document |
|---|---|---|
| harness | context and permission | [HARNESS.md](HARNESS.md) |
| loop | when to stop, and what to do on failure | [LOOP.md](LOOP.md) |
| graph | order, and what survives a crash | [GRAPH.md](GRAPH.md) |

[LADDER.md](LADDER.md) explains which of the three you actually need — most tasks need
fewer than all of them. [PATTERNS.md](PATTERNS.md) works the same ideas through an
ordinary support desk, away from the financial domain.

## Design decisions

[adr/](adr/) records why things are the way they are, including the ones that were
wrong first:

| ADR | Decision |
|---|---|
| [0001](adr/0001-three-layers.md) | Three layers rather than one framework |
| [0002](adr/0002-ships-no-tools.md) | The library ships no tools |
| [0003](adr/0003-idempotency-ledger.md) | An idempotency ledger, not retries |
| [0004](adr/0004-graph-over-orchestrator.md) | A declarative graph, not an orchestrator agent |
| [0005](adr/0005-two-backends.md) | Two backends, one of them deterministic |
| [0006](adr/0006-gates-cite-published-guidance.md) | Gates cite published guidance, and a gate that cannot tell abstains |
