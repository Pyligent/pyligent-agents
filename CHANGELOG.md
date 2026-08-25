# Changelog

All notable changes to this project are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] — 2026-08-25

First public release.

### Added

**Layer 1 — harness** (`pyligent_agents.harness`)
- `Harness`: one code path for model calls, one for tool calls
- `ContextManager`: tool-result offloading to a content-addressed `Workspace`,
  then compaction that preserves the goal turn and never orphans a `tool_use`
- `HookBus`: four interception points, with `defang_untrusted_content`,
  `redact_secrets`, `phase_guard` and `deny_restricted_without_approval` shipped
- `ToolRegistry`: permission tiers, deferred loading via `search_tools`,
  dispatch that never raises
- `Governor`: turns, tokens, USD and wall-clock, checked before the call
- `MemoryStore`: cross-run notes

**Layer 2 — loop** (`pyligent_agents.loop`)
- `AgentContract`: the four questions as constructor arguments
- Composable `StopCondition`s (`ModelSaysDone`, `GatesPass`, `Produced`,
  `Predicate`, `&`, `|`)
- `RecoveryPolicy`: one branch per error class, with runaway limits
- `Agent`: gather → act → **verify** → repeat, with push-back that names the gap

**Layer 3 — graph** (`pyligent_agents.graph`)
- Six node kinds: `Step`, `AgentNode`, `GateNode`, `HumanGate`, `MapNode`,
  `ReduceNode`
- Build-time validation: unknown dependencies, cycles, unsatisfiable `requires`
- `GraphRunner`: checkpoint, resume, deterministic replay, conditional routing,
  human pauses, compensation
- `GraphStore`: SQLite checkpoints, spans, and an idempotency ledger with a
  database-level uniqueness constraint

**Verification** (`pyligent_agents.verify`)
- A composable gate library, plus `evidence_gated_extraction()`
- `DocumentVerifier`: an independent verifier whose citations are
  substring-checked against the source
- `GateVerifier`: verification with no model and no cost

**Tooling**
- `pyligent_agents.testing`: policy builders, `build_test_stack`, `capture_prompts`,
  `assert_capped`, `assert_effects_fire_once`
- `pyligent-agents` CLI: `steps`, `doctor`, `new`, `graph`, `runs`, `trace`
- Four worked examples, one per rung of the ladder

### Notes
- Repository `pyligent/pyligent-agents`, distribution **`pyligent-agents`**,
  import name **`pyligent_agents`**, CLI **`pyligent-agents`**.
- The core library has no required third-party dependencies.
- Pyligent Agents ships no tools and no domain, on purpose.
