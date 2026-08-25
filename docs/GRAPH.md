# Layer 3 — Graph engineering

> The graph owns **coordination**: what runs in what order, and what survives a
> crash.

## Why a graph rather than an orchestrator agent

v1 of this work used an orchestrator agent that planned and delegated. That is a
reasonable pattern and it is what the reference architecture describes. A graph
beats it on five specific things, and they all come from the same property:

**a graph is inspectable before it executes.**

| | Orchestrator agent | Declared graph |
|---|---|---|
| Missing input | discovered mid-run, often as a confident wrong answer | build-time error |
| Cycle | discovered on the invoice | build-time error |
| Review | read the prompt and hope | `graph show`, or a Mermaid diagram in the change record |
| Crash | restart the plan | resume from the last checkpoint |
| Audit | a transcript | per-node spans + deterministic replay |

You give up flexibility. Where you actually want it, put an `AgentNode` inside a
node — then the flexibility is scoped to where it earns its keep, and the
workflow around it is still declared.

```bash
pyligent-agents graph show level3_refund_workflow.app:build_graph
python examples/run.py demo graph
```

---

## 1. Node kinds — a small, closed set

Every workflow anyone has asked for here decomposes into six shapes. Keeping the
set closed is what makes a graph analysable.

| Kind | What it is |
|---|---|
| `Step` | deterministic code. No model, no ambiguity. |
| `AgentNode` | a full agent loop with its own contract and tool surface |
| `GateNode` | machine-checkable predicates; routes on pass/fail |
| `HumanGate` | pauses the run and waits for a recorded decision |
| `MapNode` | fan out over items; one child result per item |
| `ReduceNode` | fan in; combine children into one output |

**Push work into `Step`s.** The dispute graph costs less than a single Level 2
question because five of its six nodes are deterministic and only one calls a
model. A workflow whose model calls are the exception rather than the loop is
faster, cheaper and auditable. Copy that shape.

Every node declares:

```python
Step(id="issue_instruction",
     depends_on=("approve_instruction",),      # ordering
     requires=("allocation", "figures"),        # inputs, validated at build time
     provides=("instruction",),                 # outputs, published to state
     when=_delivery_due,                        # conditional routing
     retry=RetryPolicy(max_attempts=2),
     idempotency=_instruction_key,              # external side effect
     compensate=_unwind)                        # undo, if a later node fails
```

---

## 2. Validation — every failure mode, before it costs anything

```python
graph.validate()   # the runner refuses to run an unvalidated graph
```

| Check | Catches |
|---|---|
| `_check_dependencies` | unknown or self-referential `depends_on` |
| `_check_cycles` | a cycle, reported as the actual path |
| `_check_dataflow` | a node reading a key nothing upstream provides |

The third is the valuable one:

```
Node 'assemble' requires ['artifact'] which nothing upstream provides.
Available at that point: ['document', 'source_text'].
Add it to a predecessor's `provides`, or to the graph's `seeds`.
```

A node reading a missing key and silently getting `None` is how a graph produces
a confident answer built on a hole.

`state.require(key)` enforces the same thing at run time, and names what *is*
available.

---

## 3. State — the only thing nodes share

Nodes never call each other. They read from state and write to state, which is
what makes replay meaningful.

```python
state.outputs[node_id]   # what each node returned — the audit trail
state.data[key]          # the shared working set — what nodes actually read
```

Kept apart on purpose: mixing them produces a node that depends on another
node's internal shape and breaks when it changes.

`state.fingerprint(keys)` is a stable content hash of the inputs a node will
read — used for idempotency and for proving a replayed run saw identical inputs.

---

## 4. The execution contract

Five rules, in [`runner.py`](../src/pyligent_agents/graph/runner.py):

1. A node that already finished is **replayed from the checkpoint**, not re-run.
2. A node whose idempotency key is already on the ledger is **replayed from the
   ledger**, even if its checkpoint was lost.
3. A node writes "started" before it works and its result after, in that order.
4. A failed node **blocks** its dependents; it does not let the graph invent a
   result to carry on with.
5. A human gate **pauses**. Paused is not failed.

**Rules 1 and 2 are not redundant.** Rule 1 covers an ordinary restart. Rule 2
covers the window where the side effect landed externally and the state write
did not — the exact window in which a naive resume double-sends.

```
  t0   node "issue_instruction" starts, checkpoint written
  t1   custodian ACCEPTS the instruction        ← it happened, externally
  t2   process dies before the completion write
  t3   resume: the checkpoint says it never finished
  t4   ...rule 2 finds the key on the ledger and replays. No duplicate.
```

`test_a_wiped_checkpoint_still_cannot_double_instruct` deletes the node
checkpoint and proves the instruction still fires once.

---

## 5. Idempotency keys

```python
idempotency_key("instruct", cpty="A-1207", asset="CASH-USD-01",
                amount=1200000.0, settle="2026-08-22")
# 'instruct:amount=1200000.0|asset=CASH-USD-01|cpty=A-1207|settle=2026-08-22'
```

Human-readable on purpose: when Operations asks why an instruction did not
re-send, you can read the answer off the ledger.

**Derived from the facts of the action.** Never:

| Never | Why |
|---|---|
| `datetime.now()` | different on the retry → guaranteed duplicate |
| `uuid4()` | same, and unsearchable afterwards |
| a retry counter | every attempt becomes a "new" action |

`idempotency_key()` with no facts raises: *"a key with no facts is a uuid wearing
a costume, and will fire twice."*

The uniqueness constraint lives in the **database** (`PRIMARY KEY (run_id,
key)`). Application-level "did we already do this?" loses the race between two
workers; the constraint does not.

> **A judgement call, made explicitly.** A fact-derived key suppresses a
> *legitimate* repeat of an identical action. In refunds the second identical
> refund on the same order is almost always an error, so that default is right
> here. Where genuine repeats happen, add a
> business-meaningful discriminator — a call reference, not a clock. See
> [ADR 0002](adr/0003-idempotency-ledger.md).

---

## 6. Conditional routing

```python
Step(id="publish",  when=_gates_passed,        ...)
Step(id="escalate", when=lambda s: not _gates_passed(s), ...)
```

A guard returning false marks the node **skipped**, not failed — dependents
still run. That is how a gate failure reaches a remediation branch:

```
✓ gates                  done
— publish                skipped
✓ escalate               done
```

---

## 7. Human gates

```python
HumanGate(id="approve_instruction",
          prompt=lambda s: f"Approve delivery of {...}?",
          payload=lambda s: {"allocation": ..., "figures": ...})
```

Raises `HumanApprovalRequired`, which the runner treats as **paused**. State is
checkpointed; nothing is spinning; the run resumes once the decision is
recorded:

```bash
python examples/run.py refund A-1207
python examples/run.py resume gr_55b7585fd5c3 --approve
```

---

## 8. Verification

Two independent mechanisms, and you want both.

**`GateNode`** — pure predicates over the artifact. No model, no network, no
ambiguity. Nine of them for invoice intake: required fields present, no
placeholder values, every field evidenced, **every quote verbatim in the
source**, independently verified, line items present, tax rate in [0, 100],
**line items sum to the stated total**, due date after invoice date.

`lines_sum_to_total` is worth calling out: **no JSON schema catches it.** Every
field is present, every type is right, every value is individually plausible —
and the invoice is wrong, because one unit price was mis-read. It takes one line
of arithmetic and a little domain knowledge. **Every gate set should contain at
least one check a schema cannot express.** If yours does not, you have written a
validator, not a gate set.

**`DocumentVerifier`** — a separate agent given the artifact and the source and
*nothing about how the artifact was produced*. It may approve only with cited
evidence, and then **every citation is substring-checked against the source**.
One fabricated quote rejects the artifact regardless of the verdict:

```
✓ gates                  done
— publish                skipped
✓ escalate               done

failing gates : ['independently_verified']
verifier said : approved=False
  - 1 citation(s) quote text absent from the source: 'threshold_usd = 5,000,000'
```

The model can be wrong. The substring check cannot. Whitespace is normalised
(PDF text wraps); wording is not (a paraphrase is not a citation).

Known limit, stated rather than hidden: this catches *fabricated* evidence, not
*irrelevant* evidence. A genuine sentence that does not support the claim passes.

---

## 9. Parallelism, and why the default is off

`MapNode(parallel=N)` uses a thread pool and preserves input ordering. The
default is `parallel=1`.

**Deterministic replay is worth more than concurrency in an audited workflow.**
A run you can reproduce byte-for-byte is a run you can explain to a control
function. Turn parallelism up when latency matters more than that — and know
which one you traded.

---

## 10. Compensation

```python
Step(id="reserve", fn=..., compensate=lambda state, output: release(output))
```

When a node fails, completed nodes with a `compensate` are unwound newest-first.
Best-effort by design: a compensation that itself fails is logged, not raised,
because the original failure is the one a human needs to see.

---

## Reading a run afterwards

```bash
python -m pyligent-agents runs
python -m pyligent-agents trace gr_55b7585fd5c3
```

```
  capture_intake         done               attempt=1, kind=step, ms=0.31
  recompute_call         done               attempt=1, kind=step, ms=0.44
  draft_response         done               attempt=1, kind=agent, ms=1.02
  approve_instruction    pause              prompt=Approve delivery of ...
  issue_instruction      replay             source=effect_ledger, key=instruct:...
```

Per-node spans, plus the effect ledger. "The agent decided that" is not an
answer a control function accepts. This is.

---

## What to copy into your own system

- Declared `requires` / `provides`, validated at build time.
- Checkpoint before the work, result after.
- An effect ledger separate from checkpoints, with a DB uniqueness constraint.
- Fact-derived idempotency keys.
- Guards that mark nodes skipped, so failure has somewhere to route.
- Human gates that pause rather than block.
- A gate set containing at least one check a schema cannot express.
