# The patterns, away from the domain

The collateral work in this repository is an application of three layers, not a
bespoke pipeline. This page works those layers through on an ordinary support
desk for an online retailer — no ISDA vocabulary, nothing to know in advance —
because a pattern you can only see in one domain is not yet a pattern.

If you are evaluating the collateral capability, you do not need this page. If
you are adopting the library, start here.

| Layer | Owns | The question it answers |
|---|---|---|
| `harness` | **Context** | What the model sees, and what it may touch |
| `loop` | **Control** | When to stop, and what to do when something breaks |
| `graph` | **Coordination** | What runs in what order, and what survives a crash |

---

## The ladder

Four levels, on one domain, one per rung. Start at Level 1 and move up only when
the current level *demonstrably breaks* under the real shape of the task. "It
felt limiting" is not a breakage — bring the failing run.

```bash
python examples/run.py triage        # 1 — one call, no tools, no loop
python examples/run.py order-agent   # 2 — a tool loop with a hard cap
python examples/run.py refund        # 3 — durable, resumable, idempotent
python examples/run.py invoice       # 4 — a graph of specialists
```

| | What it is | Move up only when |
|---|---|---|
| **1** | One call. No memory, no tools, no loop. | The answer depends on something not already in the request |
| **2** | Tool loop with a hard turn cap. | Work spans sessions, or a crash must not redo it |
| **3** | Checkpointed, resumable, idempotent. | One task is genuinely several different jobs |
| **4** | Graph of specialists with gates. | — |

The two mistakes that show up most in review:

- **Mistaking volume for variety.** The same job forty times is a batch loop
  around a Level 2 agent, made durable. It is not a fan-out. Level 4 would add
  an orchestrator coordinating forty identical specialists — cost and failure
  surface for nothing.
- **Mistaking "it's just search" for "no external data."** If the corpus is not
  in the prompt, the model needs a tool, and you are at Level 2. Retrieval
  quality then dominates every other design decision.

---

## The three demos worth running

```bash
python examples/run.py demo harness   # offloading vs compaction, tiers, hooks
python examples/run.py demo loop      # a confident invented number, caught
python examples/run.py demo graph     # three executions, one refund
```

**The loop demo** is the one that changes minds. An agent states a refund of
£310.00 — fluent, confident, invented, and nothing in the text signals it. A
stop condition requiring every monetary figure to trace to a tool that *ran and
succeeded* rejects the answer, pushes back naming the gap, and the corrected
figure is £257.99 from the calculator.

Under "the model stopped calling tools", the £310 ships.

**The graph demo** runs the same workflow three times, killing it mid-run, and
asserts the customer was refunded **once**. The key is derived from the facts of
the refund — order, amount, fault — so every attempt produces the same key. A
timestamp would have paid the customer three times.

---

## Where the detail lives

| | |
|---|---|
| The ten build steps | `pyligent-agents steps` |
| Layer 1 | [`HARNESS.md`](HARNESS.md) |
| Layer 2 | [`LOOP.md`](LOOP.md) |
| Layer 3 | [`GRAPH.md`](GRAPH.md) |
| Choosing a level | [`LADDER.md`](LADDER.md) |
| Testing agent behaviour | [`TESTING.md`](TESTING.md) |
| Decisions and their reasoning | [`adr/`](adr/) |

---

## Why the support desk

Because the awkward cases are in the seed data on purpose: an order outside the
return window (the domain **refuses**), one whose carrier API is **down** (the
tool *raises*), and one already refunded in full (a naive refund pays twice).

A demo domain where everything works teaches nothing. The interesting
engineering is entirely in what happens when it does not.
